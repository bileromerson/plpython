"""Identificação de música: YouTube x MusicBrainz/Cover Art Archive.

Usado pelo downloader (pltube.py) através de `resolver_com_prioridade`. Não baixa
áudio nem escreve arquivos: recebe os metadados do yt-dlp e as correções
manuais, consulta o MusicBrainz e devolve um ResolutionResult com os campos,
a origem e a confiança de cada um, o status (RESOLVIDO/OBSERVAR/PENDENTE) e
as observações para conferência.

O MusicBrainz exige um User-Agent identificável e no máximo uma consulta por
segundo; o MusicBrainzClient aplica essa pausa. O Cover Art Archive é
consultado à parte.
"""

from __future__ import annotations

import copy
import difflib
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from verbose import curto, v, vv
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, MutableMapping, Sequence

MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
COVER_ART_API = "https://coverartarchive.org"
DEFAULT_USER_AGENT = (
    "pltube-metadata-resolver/0.1 "
    "(https://github.com/bileromerson/plpython)"
)
CONFIRMED_REVIEW_STATES = {"CORRIGIDO", "APLICADO", "CONFIRMADO"}
ISRC_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")
_FEATURE_SPLIT = re.compile(r"\s+(?:feat(?:uring)?\.?|ft\.?)\s+", re.IGNORECASE)
_UNKNOWN_VALUES = {
    "",
    "unknown",
    "unknown artist",
    "artista desconhecido",
    "desconhecido",
    "n/a",
    "na",
    "none",
    "null",
    "-",
}


class MusicBrainzError(RuntimeError):
    """Erro de transporte ou resposta inesperada nas APIs de metadados."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ResolutionResult:
    """Resultado serializável do resolvedor, sem efeitos colaterais."""

    video_id: str
    status: str
    metadata: dict[str, Any]
    provenance: dict[str, str]
    confidence: dict[str, str]
    pending_reasons: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    diagnostics_by_source: dict[str, list[str]] = field(default_factory=dict)
    source_youtube: dict[str, Any] = field(default_factory=dict)
    source_local: dict[str, Any] = field(default_factory=dict)
    observations: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Retorna uma cópia simples apropriada para JSON/SQLite."""
        return asdict(self)


ESPERAS_MUSICBRAINZ = (2.0, 5.0, 15.0, 30.0)  # novas tentativas em 503/falha de rede


class MusicBrainzClient:
    """Cliente pequeno para buscas de gravações e capas.

    ``transport`` pode ser injetado em testes; sua assinatura é
    ``transport(url, headers, timeout) -> bytes``. A mesma instância deve ser
    reutilizada entre várias faixas para respeitar o intervalo de chamadas e
    aproveitar o cache em memória.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        min_interval: float = 1.0,
        transport: Callable[[str, Mapping[str, str], float], bytes] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if not user_agent.strip():
            raise ValueError("MusicBrainz requer um User-Agent identificável.")
        if min_interval < 0:
            raise ValueError("min_interval não pode ser negativo.")
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self._transport = transport or self._default_transport
        self._clock = clock
        self._sleep = sleeper
        self._last_musicbrainz_request: float | None = None
        self._rate_lock = threading.Lock()
        self._json_cache: dict[str, Any] = {}

    @staticmethod
    def _default_transport(
        url: str, headers: Mapping[str, str], timeout: float
    ) -> bytes:
        request = urllib.request.Request(url, headers=dict(headers))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _wait_for_musicbrainz_slot(self) -> None:
        with self._rate_lock:
            now = self._clock()
            if self._last_musicbrainz_request is not None:
                delay = self.min_interval - (now - self._last_musicbrainz_request)
                if delay > 0:
                    self._sleep(delay)
            self._last_musicbrainz_request = self._clock()

    def _get_json(self, url: str, *, rate_limited: bool) -> dict[str, Any]:
        if url in self._json_cache:
            vv("MusicBrainz", f"resposta já em memória: {curto(url, 110)}")
            return copy.deepcopy(self._json_cache[url])
        if rate_limited:
            self._wait_for_musicbrainz_slot()
        vv("MusicBrainz", f"GET {curto(url, 140)}")
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        # O MusicBrainz responde 503 quando está sobrecarregado ou limitando o
        # ritmo; a documentação pede para esperar e tentar de novo.
        esperas = (*ESPERAS_MUSICBRAINZ, None)
        for espera in esperas:
            try:
                body = self._transport(url, headers, self.timeout)
                payload = json.loads(body.decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (502, 503, 504) and espera is not None:
                    v("MusicBrainz", f"HTTP {exc.code} (serviço ocupado); tentando de novo em {espera:.0f}s")
                    self._sleep(espera)
                    continue
                raise MusicBrainzError(
                    f"HTTP {exc.code} consultando metadados externos.", exc.code
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if espera is not None:
                    v("MusicBrainz", f"falha de conexão; tentando de novo em {espera:.0f}s")
                    self._sleep(espera)
                    continue
                motivo = getattr(exc, "reason", None) or "tempo limite"
                raise MusicBrainzError(f"Falha de conexão: {motivo}") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MusicBrainzError("A API retornou JSON inválido.") from exc
        if not isinstance(payload, dict):
            raise MusicBrainzError("A resposta JSON não é um objeto.")
        self._json_cache[url] = copy.deepcopy(payload)
        return payload

    def _search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        params = urllib.parse.urlencode(
            {"query": query, "fmt": "json", "limit": max(1, min(limit, 25))}
        )
        payload = self._get_json(
            f"{MUSICBRAINZ_API}/recording/?{params}", rate_limited=True
        )
        recordings = payload.get("recordings") or []
        return [
            candidate
            for recording in recordings
            if (candidate := _parse_musicbrainz_recording(recording)) is not None
        ]

    def search_by_isrc(self, isrc: str, limit: int = 10) -> list[dict[str, Any]]:
        normalized = normalize_isrc(isrc)
        if not normalized:
            return []
        return self._search(f"isrc:{normalized}", limit=limit)

    def search_by_title_artist(
        self, title: str, artist: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        title, artist = _text(title), _text(artist)
        if not title or not artist:
            return []
        query = (
            f"recording:{_lucene_fuzzy_group(title)} "
            f"AND artist:{_lucene_fuzzy_group(artist)}"
        )
        return self._search(query, limit=limit)

    def search_by_title(self, title: str, limit: int = 10) -> list[dict[str, Any]]:
        """Busca por título quando o YouTube não fornece créditos de artista."""
        title = _text(title)
        if not title:
            return []
        return self._search(f"recording:{_lucene_fuzzy_group(title)}", limit=limit)

    def get_album_cover(self, release_group_id: str = "", release_id: str = "") -> str | None:
        """URL da capa frontal: primeiro a do release-group (capa "oficial" do
        álbum, igual entre edições), depois a do release escolhido."""
        for kind, mbid in (("release-group", release_group_id), ("release", release_id)):
            if not _valid_mbid(mbid or ""):
                continue
            try:
                payload = self._get_json(f"{COVER_ART_API}/{kind}/{mbid}", rate_limited=False)
            except MusicBrainzError as exc:
                if exc.status_code == 404:
                    continue
                raise
            url = _front_image_url(payload, preferred=("1200", "500", "large", "250", "small"))
            if url:
                return url
        return None

    def download_image(self, url: str, max_bytes: int = 8 * 1024 * 1024) -> bytes | None:
        """Baixa uma imagem (JPEG/PNG) da capa. Retorna None se não for imagem válida."""
        url = _text(url)
        if not url.lower().startswith(("https://", "http://")):
            return None
        try:
            body = self._transport(url, {"User-Agent": self.user_agent}, self.timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MusicBrainzError(f"Falha baixando a capa: {exc}") from exc
        if not body or len(body) > max_bytes:
            return None
        if body[:3] == b"\xff\xd8\xff" or body[:8] == b"\x89PNG\r\n\x1a\n":
            return body
        return None


def normalize_isrc(value: Any) -> str:
    """Normaliza ISRC e retorna vazio se não corresponder ao formato de 12 chars."""
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if normalize_isrc(item)), "")
    normalized = re.sub(r"[\s-]", "", _text(value)).upper()
    return normalized if ISRC_PATTERN.fullmatch(normalized) else ""


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    result = str(value).strip()
    if result.casefold() in _UNKNOWN_VALUES:
        return ""
    return result


def _artists(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        values: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                item = item.get("name") or item.get("artist") or ""
            name = _text(item)
            if name:
                values.append(name)
        return list(dict.fromkeys(values))
    text = _text(value)
    if not text:
        return []
    # | é o separador de correções manuais; feat./ft. é crédito de participação.
    pieces = [text]
    if "|" in text:
        pieces = text.split("|")
    else:
        pieces = _FEATURE_SPLIT.split(text)
    return list(dict.fromkeys(piece.strip() for piece in pieces if _text(piece)))


def _extract_youtube_metadata(data: Mapping[str, Any]) -> dict[str, Any]:
    title = _text(data.get("track")) or _text(data.get("title")) or _text(data.get("fulltitle"))
    artists = _artists(data.get("artists")) or _artists(data.get("artist"))
    album = _text(data.get("album")) or _text(data.get("release"))
    isrc = normalize_isrc(data.get("isrc") or data.get("isrcs"))
    uploader = _text(data.get("uploader")) or _text(data.get("channel"))
    try:
        duration = max(0.0, float(data.get("duration") or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    thumbnails: list[dict[str, Any]] = []
    raw_thumbnails = data.get("thumbnails")
    if isinstance(raw_thumbnails, list):
        for item in raw_thumbnails:
            if not isinstance(item, Mapping):
                continue
            url = _text(item.get("url"))
            if not url:
                continue
            try:
                width = max(0, int(item.get("width") or 0))
                height = max(0, int(item.get("height") or 0))
            except (TypeError, ValueError):
                width, height = 0, 0
            thumbnails.append({"url": url, "width": width, "height": height})
    single_thumbnail = _text(data.get("thumbnail"))
    if single_thumbnail and all(item["url"] != single_thumbnail for item in thumbnails):
        thumbnails.append({"url": single_thumbnail, "width": 0, "height": 0})
    thumbnails.sort(key=lambda item: (item["width"] * item["height"], item["width"]), reverse=True)
    return {
        "title": title,
        "artists": artists,
        "album": album,
        "isrc": isrc,
        "uploader": uploader,
        "duration": duration,
        "thumbnails": thumbnails,
        "thumbnail_url": thumbnails[0]["url"] if thumbnails else "",
    }


def _extract_local_metadata(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normaliza tags já gravadas no MP3 sem classificá-las como dados do YouTube."""
    if not isinstance(data, Mapping):
        data = {}
    return {
        "title": _text(data.get("title")),
        "artists": _artists(data.get("artists") or data.get("artist")),
        "album": _text(data.get("album")),
        "isrc": normalize_isrc(data.get("isrc") or data.get("tsrc")),
    }


def _confirmed_manual_record(
    corrections: Mapping[str, Any] | None, video_id: str
) -> Mapping[str, Any]:
    if not isinstance(corrections, Mapping):
        return {}
    record: Any = corrections.get(video_id)
    if not isinstance(record, Mapping):
        record = corrections.get(f"youtube:{video_id}")
    if not isinstance(record, Mapping):
        # Também aceita uma única linha já selecionada pelo chamador.
        if any(key in corrections for key in (
            "status_revisao", "title", "titulo_corrigido", "artists_corrigidos"
        )):
            record = corrections
    if not isinstance(record, Mapping):
        return {}
    status = _text(record.get("status_revisao")).upper()
    if status not in CONFIRMED_REVIEW_STATES:
        return {}
    return record


def _manual_values(record: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    aliases = {
        "title": ("titulo_corrigido", "title_corrected", "title", "titulo"),
        "artists": ("artistas_corrigidos", "artists_corrected", "artists", "artist"),
        "album": ("album_corrigido", "album_corrected", "album"),
        "isrc": ("isrc_corrigido", "isrc_corrected", "isrc"),
        "cover_url": ("cover_url_corrigida", "cover_url", "capa_url"),
        "cover_file": ("capa_arquivo", "cover_file", "capa_arquivo_corrigida"),
    }
    for field_name, keys in aliases.items():
        for key in keys:
            if key in record and _has_value(record[key]):
                value = record[key]
                if field_name == "artists":
                    value = _artists(value)
                elif field_name == "isrc":
                    value = normalize_isrc(value)
                else:
                    value = _text(value)
                if _has_value(value):
                    values[field_name] = value
                break
    return values


def _cached_search(
    cache: MutableMapping[str, Any], key: str, search: Callable[[], list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    if key not in cache:
        v("MusicBrainz", f"buscando {curto(key.replace('|', ' / '), 100)}")
        cache[key] = copy.deepcopy(search())
        v("MusicBrainz", f"  {len(cache[key] or [])} gravação(ões) encontrada(s)")
    else:
        v("MusicBrainz", f"busca repetida, usando o resultado anterior: {curto(key, 80)}")
    value = cache[key]
    return copy.deepcopy(value) if isinstance(value, list) else []


def _choose_exact_isrc(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, bool]:
    unique: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        recording_id = _text(candidate.get("recording_id"))
        key = recording_id or json.dumps(candidate, sort_keys=True, ensure_ascii=False)
        unique.setdefault(key, candidate)
    if len(unique) == 1:
        return next(iter(unique.values())), False
    return None, len(unique) > 1


def _choose_ranked(
    ranked: Sequence[tuple[Mapping[str, Any], float]],
    *,
    threshold: float,
    margin: float,
) -> tuple[Mapping[str, Any] | None, bool]:
    if not ranked:
        return None, False
    top_candidate, top_score = ranked[0]
    next_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_score < threshold:
        return None, bool(len(ranked) > 1 and ranked[1][1] >= threshold - margin)
    if len(ranked) > 1 and top_score - next_score < margin:
        return None, True
    return top_candidate, False


def _public_candidate(candidate: Mapping[str, Any], score: float) -> dict[str, Any]:
    recording_id = _text(candidate.get("recording_id"))
    releases = []
    for release in candidate.get("releases", []):
        if not isinstance(release, Mapping):
            continue
        release_id = _text(release.get("id"))
        releases.append({
            "release_id": release_id,
            "title": _text(release.get("title")),
            "date": _text(release.get("date")),
            "url": f"https://musicbrainz.org/release/{release_id}" if release_id else "",
        })
    return {
        "source": "musicbrainz",
        "recording_id": recording_id,
        "title": _text(candidate.get("title")),
        "artists": _artists(candidate.get("artists")),
        "isrcs": [
            normalized for value in candidate.get("isrcs", [])
            if (normalized := normalize_isrc(value))
        ],
        "duration": candidate.get("duration"),
        "score": score,
        "recording_url": (
            f"https://musicbrainz.org/recording/{recording_id}" if recording_id else ""
        ),
        "releases": releases,
    }


def _parse_musicbrainz_recording(recording: Any) -> dict[str, Any] | None:
    if not isinstance(recording, Mapping):
        return None
    recording_id = _text(recording.get("id"))
    if not recording_id:
        return None
    artists, artist_ids = _artist_credit(recording.get("artist-credit"))
    releases = []
    for release in recording.get("releases", []) or []:
        parsed = _parse_musicbrainz_release(release)
        if parsed:
            releases.append(parsed)
    try:
        duration_ms = int(recording.get("length") or 0)
        duration = duration_ms / 1000.0 if duration_ms > 0 else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "recording_id": recording_id,
        "title": _text(recording.get("title")),
        "artists": list(dict.fromkeys(artists)),
        "artist_ids": artist_ids,
        "first_release_date": _text(recording.get("first-release-date")),
        "isrcs": [value for value in (normalize_isrc(item) for item in recording.get("isrcs", []) or []) if value],
        "duration": duration,
        "releases": releases,
    }


def _artist_credit(credits: Any) -> tuple[list[str], list[str]]:
    """Nomes canônicos e IDs MusicBrainz de um artist-credit, na ordem creditada."""
    names: list[str] = []
    ids: list[str] = []
    for credit in credits or []:
        if not isinstance(credit, Mapping):
            continue
        artist = credit.get("artist")
        if isinstance(artist, Mapping):
            name = _text(artist.get("name")) or _text(credit.get("name"))
            artist_id = _text(artist.get("id"))
        else:
            name, artist_id = _text(credit.get("name")), ""
        if name and name not in names:
            names.append(name)
            ids.append(artist_id)
    return names, ids


def _primeiro_inteiro(value: Any) -> int:
    match = re.search(r"\d+", _text(value))
    return int(match.group()) if match else 0


def _parse_musicbrainz_release(release: Any) -> dict[str, Any] | None:
    """Extrai do resultado de busca os dados de álbum usados nas tags."""
    if not isinstance(release, Mapping) or not _text(release.get("id")):
        return None
    group = release.get("release-group") if isinstance(release.get("release-group"), Mapping) else {}
    track_number = track_total = disc_number = 0
    for medium in release.get("media") or []:
        if not isinstance(medium, Mapping):
            continue
        tracks = medium.get("track") or medium.get("tracks") or []
        if tracks and isinstance(tracks[0], Mapping):
            track_number = _primeiro_inteiro(tracks[0].get("number") or tracks[0].get("position"))
        track_total = _primeiro_inteiro(medium.get("track-count"))
        disc_number = _primeiro_inteiro(medium.get("position"))
        break
    artists, artist_ids = _artist_credit(release.get("artist-credit"))
    return {
        "id": _text(release.get("id")),
        "title": _text(release.get("title")),
        "date": _text(release.get("date")),
        "status": _text(release.get("status")),
        "country": _text(release.get("country")),
        "release_group_id": _text(group.get("id")),
        "primary_type": _text(group.get("primary-type")),
        "secondary_types": [_text(t) for t in group.get("secondary-types") or [] if _text(t)],
        "track_number": track_number,
        "track_total": track_total,
        "disc_number": disc_number,
        "disc_total": _primeiro_inteiro(release.get("medium-count")),
        "artists": artists,
        "artist_ids": artist_ids,
    }


def _front_image_url(payload: Mapping[str, Any], preferred: Sequence[str]) -> str | None:
    images = payload.get("images") or [] if isinstance(payload, Mapping) else []
    fronts = [
        image for image in images
        if isinstance(image, Mapping) and (
            image.get("front") is True
            or "front" in {str(kind).casefold() for kind in image.get("types", [])}
        )
    ]
    if not fronts:
        return None
    thumbnails = fronts[0].get("thumbnails") or {}
    for size in preferred:
        if thumbnails.get(size):
            return str(thumbnails[size])
    return _text(fronts[0].get("image")) or None


def _escape_lucene(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _lucene_fuzzy_group(value: str) -> str:
    """Forma uma consulta por termos; palavras com 5+ letras toleram 1 erro."""
    tokens = re.findall(r"[^\W_]+", _text(value), flags=re.UNICODE)
    if not tokens:
        return f'"{_escape_lucene(value)}"'
    termos = [f"{token}~1" if len(token) >= 5 else token for token in tokens]
    return "(" + " AND ".join(termos) + ")"


def _normalize_text(value: Any) -> str:
    text = _text(value).casefold()
    text = text.replace("’", "").replace("'", "")
    # Variações comuns de apresentação no YouTube não são parte do título.
    ruido = r"(?:official(?:\s+(?:music\s+)?(?:audio|video))?|lyrics?(?:\s+video)?|visuali[sz]er|hd|4k)"
    text = re.sub(rf"\s*[\[(]\s*{ruido}\s*[\])]\s*$", " ", text, flags=re.I)
    text = re.sub(rf"\s*[-–—]\s*{ruido}\s*$", " ", text, flags=re.I)
    text = _FEATURE_SPLIT.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _similarity(left: str, right: str) -> float:
    normalized_left = _normalize_text(left)
    normalized_right = _normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    direct = difflib.SequenceMatcher(None, normalized_left, normalized_right).ratio()
    sorted_left = " ".join(sorted(normalized_left.split()))
    sorted_right = " ".join(sorted(normalized_right.split()))
    reordered = difflib.SequenceMatcher(None, sorted_left, sorted_right).ratio()
    return max(direct, reordered)


def _has_value(value: Any) -> bool:
    if isinstance(value, (list, tuple, set)):
        return any(_has_value(item) for item in value)
    if isinstance(value, Mapping):
        return bool(value)
    return bool(_text(value))


def _valid_mbid(value: str) -> bool:
    return bool(re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value,
    ))


__all__ = [
    "analisar_titulo",
    "avaliar_canal",
    "escolher_release",
    "hipoteses_youtube",
    "ler_descricao_youtube_music",
    "resolver_com_prioridade",
    "MusicBrainzClient",
    "MusicBrainzError",
    "ResolutionResult",
    "normalize_isrc",
]

# Documentação oficial:
# https://musicbrainz.org/doc/MusicBrainz_API
# https://musicbrainz.org/doc/MusicBrainz_API/Search/RecordingSearch
# https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
# https://musicbrainz.org/doc/Cover_Art_Archive/API


# ---------------------------------------------------------------------------
# Modelo de identificação: YouTube x MusicBrainz, com confiança por campo
# ---------------------------------------------------------------------------
#
# Ideia geral (não depende de acertar tudo de primeira):
#   1. Do vídeo saem várias hipóteses de (título, artistas), cada uma com uma
#      confiança inicial ("prior") conforme a fonte: descrição do YouTube Music,
#      crédito oficial do YouTube, canal Topic/VEVO, título "Artista - Música",
#      canal já aprendido, artista já conhecido na biblioteca...
#   2. O canal é avaliado: artista oficial, curador/re-upload ou desconhecido.
#      Nome de canal de re-upload nunca vira artista.
#   3. As melhores hipóteses são procuradas no MusicBrainz; a que bater melhor
#      (correspondência + confiança inicial) é escolhida.
#   4. Cada campo recebe uma confiança. Só vai para conferência o que ficou
#      abaixo do limiar, divergiu entre as fontes ou não foi encontrado.

PREFERENCIAS = ("youtube", "musicbrainz")
REVISOES = ("all", "diff", "none")
LIMIAR_CONFIANCA = 0.85
FONTES_EXPLICITAS = {"youtube_music", "youtube_artista", "youtube_canal_topic", "canal_aprendido"}

_SEPARADOR_TITULO = re.compile(r"\s+[-–—]\s+")
_SEPARADOR_SEGMENTO = re.compile(r"\s+[|｜]\s+")
_POR = re.compile(r"^[\"“'‘](.+?)[\"”'’]\s+(?:by|de|por)\s+(.+)$", re.I)
_VARIANTE = re.compile(
    r"\s*[\[(【]([^\])】]*\b(?:slowed|sped\s*up|speed\s*up|nightcore|daycore|reverb|8d|"
    r"bass\s*boost(?:ed)?|pitched|acelerad[oa]|lento)\b[^\])】]*)[\])】]",
    re.I,
)
_RUIDO_PARENTESES = re.compile(
    r"\s*[\[(【](?:[^\])】]*?\b(?:official|oficial|lyrics?|letras?|legendad[oa]|"
    r"tradu[cç][aã]o|visuali[sz]er|audio|áudio|video|vídeo|clipe|mv|hd|4k|hq|"
    r"explicit|copyright|original\s+song|from\s+the|from\s+[\"“]|ost)\b[^\])】]*)[\])】]",
    re.I,
)
_RUIDO_FINAL = re.compile(
    r"(?:\s*[-–—]\s*(?:official|oficial|lyrics?|letra|audio|áudio|video|vídeo|visuali[sz]er)\b.*$"
    r"|\s+No\.\s*\d+\s*$)",
    re.I,
)
_TITLE_FEAT = re.compile(
    r"\s*[\[(]?\s*\b(?:feat(?:uring)?\.?|ft\.?|part\.?|participa[cç][aã]o\s+(?:de\s+)?)\s+([^\])]+?)\s*[\])]?\s*$",
    re.I,
)
_CHANNEL_SUFFIX = re.compile(
    r"\s*(?:-\s*topic|vevo|official|oficial|music|m[uú]sica|tv|channel|canal)\s*$", re.I
)
_CANAL_CURADOR = re.compile(
    r"\b(?:lyrics?|letras?|legendad[oa]s?|tradu[cç][oõ]es|nightcore|slowed|reverb|sped\s*up|"
    r"8d|bass\s*boost(?:ed)?|vibes|lo-?fi|chill(?:hop)?|mix(?:es)?|playlists?|hits|r[aá]dio|"
    r"fans?|fanpage|nation|collective|network|records|recordings|copyright|nocopyright|"
    r"sounds|tunes|beats|trap|edm|remix(?:es)?|1\s*hour|hora|compila[cç][aã]o|uploads?)\b",
    re.I,
)
_DESCRICAO_REUPLOAD = re.compile(
    r"(?:no copyright infringement|i do not own|i don'?t own|all rights (?:go|belong|reserved) to|"
    r"copyright disclaimer|fair use|n[aã]o possuo|n[aã]o tenho os direitos|todos os direitos|"
    r"submit your (?:music|track)|promo(?:tion)? submissions?|follow the artist)",
    re.I,
)


def _remover_simbolos(texto: str) -> str:
    """Tira emojis e símbolos decorativos, mantendo letras de qualquer idioma."""
    texto = "".join(
        c for c in texto
        if not unicodedata.category(c).startswith(("So", "Sk", "Cs", "Co")) and c not in "\ufe0f\u200d"
    )
    return re.sub(r"\s{2,}", " ", texto).strip()


def _nome_canal_limpo(nome: str) -> str:
    anterior = None
    nome = _remover_simbolos(_text(nome))
    while anterior != nome:
        anterior = nome
        nome = _CHANNEL_SUFFIX.sub("", nome).strip(" -_|")
    return nome


def _mesmo_nome(a: str, b: str) -> bool:
    """Compara nomes tolerando grafia e nomes compostos ("Imagine Dragons" em
    "Imagine Dragons x J.I.D")."""
    na, nb = _normalize_text(a), _normalize_text(b)
    if not na or not nb:
        return False
    if na == nb or na.replace(" ", "") == nb.replace(" ", ""):
        return True
    if _similarity(na, nb) >= 0.88:
        return True
    ta, tb = set(na.split()), set(nb.split())
    menor, maior = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len("".join(menor)) >= 4 and menor <= maior


_PARENTESES = re.compile(r"\s*[\[(【][^\])】]*[\])】]")
_REMIXER = re.compile(r"[\[(]\s*(.+?)\s+(?:remix|edit|flip|bootleg|vip|rework|mix|cover)\s*[\])]", re.I)


def _sem_parenteses(texto: str) -> str:
    """'Urgently Jammin' (Sir Meow Remix)' -> 'Urgently Jammin''."""
    return _PARENTESES.sub("", _text(texto)).strip()


def _remixers(texto: str) -> list[str]:
    """Quem fez o remix/edit citado entre parênteses: '(Sir Meow Remix)' -> ['Sir Meow']."""
    return [m.group(1) for m in _REMIXER.finditer(_text(texto))]


def dividir_artistas(texto: str) -> list[str]:
    """Separa colaborações ("A, B", "A x B", "A & B & C") sem quebrar nomes de
    banda com um único "&" ("Mumford & Sons"); o MusicBrainz corrige o resto."""
    texto = _remover_simbolos(_text(texto))
    if not texto:
        return []
    partes = re.split(r"\s*,\s*|\s+(?:x|X|×|vs\.?|feat(?:uring)?\.?|ft\.?)\s+", texto)
    if len(partes) > 1 or texto.count(" & ") >= 2:
        partes = [p for parte in partes for p in re.split(r"\s+&\s+", parte)]
    return list(dict.fromkeys(p.strip(" .") for p in partes if _text(p.strip(" ."))))


def analisar_titulo(title: str) -> dict[str, Any]:
    """Decompõe o título do vídeo em partes úteis para identificar a música."""
    texto = _remover_simbolos(_text(title))
    segmentos = [s.strip() for s in _SEPARADOR_SEGMENTO.split(texto) if s.strip()]
    principal = segmentos[0] if segmentos else ""
    extras = segmentos[1:]

    variante = ""
    achou = _VARIANTE.search(principal)
    if achou:
        variante = achou.group(1).strip()
        principal = (principal[: achou.start()] + principal[achou.end():]).strip()

    anterior = None
    while anterior != principal:
        anterior = principal
        principal = _RUIDO_PARENTESES.sub("", principal).strip()
        principal = _RUIDO_FINAL.sub("", principal).strip()

    feat: list[str] = []
    achou = _TITLE_FEAT.search(principal)
    if achou and achou.start() > 0:
        feat = dividir_artistas(achou.group(1).replace(" & ", ", ").replace(" e ", ", ").replace(" and ", ", "))
        principal = (principal[: achou.start()] + principal[achou.end():]).strip()
    # "feat." também pode estar no meio: "Artista ft. Outro - Música"
    meio = re.search(r"\s+(?:feat(?:uring)?\.?|ft\.?)\s+(.+?)(?=\s+[-–—]\s+)", principal, re.I)
    if meio:
        feat += dividir_artistas(meio.group(1).replace(" & ", ", "))
        principal = (principal[: meio.start()] + principal[meio.end():]).strip()

    principal = principal.strip(" -–—|\"'")
    lados = None
    por = None
    partes = _SEPARADOR_TITULO.split(principal, maxsplit=1)
    if len(partes) == 2 and all(p.strip() for p in partes):
        lados = (partes[0].strip(" \"'"), partes[1].strip(" \"'"))
    elif (m := _POR.match(principal)):
        por = (m.group(1).strip(), m.group(2).strip())
    return {
        "base": principal,
        "lados": lados,
        "por": por,
        "segmentos": [s for s in (_RUIDO_PARENTESES.sub("", e).strip() for e in extras) if s],
        "feat": list(dict.fromkeys(feat)),
        "variante": variante,
    }


def ler_descricao_youtube_music(descricao: Any) -> dict[str, Any]:
    """Lê a descrição padrão das faixas "Provided to YouTube by ..." (YouTube Music).

    Formato:  Provided to YouTube by <Distribuidora>
              <Título> · <Artista> · <Artista 2>
              <Álbum>
              ℗ 2021 <Dono>
              Released on: 2021-11-01
    Esses dados vêm da gravadora/distribuidora e são bem confiáveis.
    """
    texto = _text(descricao)
    if "provided to youtube by" not in texto.casefold():
        return {}
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    inicio = next(
        (i for i, linha in enumerate(linhas) if linha.casefold().startswith("provided to youtube by")), None
    )
    if inicio is None or inicio + 1 >= len(linhas):
        return {}
    resultado: dict[str, Any] = {"label": linhas[inicio][len("provided to youtube by"):].strip()}
    partes = [p.strip() for p in re.split(r"\s+·\s+", linhas[inicio + 1]) if p.strip()]
    if len(partes) < 2:
        return {}
    resultado["title"] = partes[0]
    resultado["artists"] = list(dict.fromkeys(partes[1:]))
    ignorar = re.compile(r"^(?:℗|©|released on|auto-generated|composer|lyricist|producer|"
                         r"artist|associated|music publisher|\d{4}\b)", re.I)
    if inicio + 2 < len(linhas) and not ignorar.match(linhas[inicio + 2]):
        resultado["album"] = linhas[inicio + 2]
    for linha in linhas[inicio + 2:]:
        if (m := re.match(r"released on:\s*(\d{4}-\d{2}-\d{2})", linha, re.I)):
            resultado["date"] = m.group(1)
        elif (m := re.match(r"℗\s*(\d{4})", linha)) and "date" not in resultado:
            resultado["year"] = m.group(1)
    if "date" not in resultado and resultado.get("year"):
        resultado["date"] = resultado["year"]
    return resultado


def _data_youtube(data: Mapping[str, Any]) -> str:
    bruto = _text(data.get("release_date"))
    if re.fullmatch(r"\d{8}", bruto):
        return f"{bruto[:4]}-{bruto[4:6]}-{bruto[6:]}"
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", bruto):
        return bruto
    ano = _text(data.get("release_year"))
    return ano if re.fullmatch(r"\d{4}", ano) else ""


def dados_youtube_completos(data: Mapping[str, Any]) -> dict[str, Any]:
    """Campos do yt-dlp usados pelo modelo, já normalizados."""
    yt = _extract_youtube_metadata(data)
    ytm = data.get("youtube_music")
    if not isinstance(ytm, Mapping):
        ytm = ler_descricao_youtube_music(data.get("description"))
    reupload = data.get("descricao_reupload")
    if reupload is None:
        reupload = bool(_DESCRICAO_REUPLOAD.search(_text(data.get("description"))))
    yt.update({
        "video_title": _text(data.get("title")) or _text(data.get("fulltitle")),
        "track": _text(data.get("track")),
        "channel_id": _text(data.get("channel_id")),
        "channel_verified": bool(data.get("channel_is_verified")),
        "release_date": _data_youtube(data),
        "youtube_music": dict(ytm or {}),
        "descricao_reupload": bool(reupload),
        "fonte_artistas": _text(data.get("fonte_artistas")) or "youtube",
    })
    # Artista igual ao nome do canal sem campo de faixa costuma ser o fallback
    # antigo "uploader como artista"; não é crédito explícito.
    if yt["artists"] and not yt["track"] and len(yt["artists"]) == 1 and _mesmo_nome(
        yt["artists"][0], _nome_canal_limpo(yt["uploader"])
    ) and not yt["youtube_music"]:
        yt["fonte_artistas"] = "canal"
    return yt


def avaliar_canal(
    yt: Mapping[str, Any],
    canal_conhecido: Mapping[str, Any] | None = None,
    artistas_conhecidos: Sequence[str] = (),
) -> dict[str, Any]:
    """Classifica o canal: "artista" (oficial), "curador" (re-upload, gravadora,
    lyrics...) ou "desconhecido", com os motivos."""
    bruto = _text(yt.get("uploader"))
    nome = _nome_canal_limpo(bruto)
    pontos, motivos = 0, []

    def sinal(valor: int, motivo: str) -> None:
        nonlocal pontos
        pontos += valor
        motivos.append(f"{motivo} ({valor:+d})")

    if re.search(r"-\s*topic\s*$", bruto, re.I):
        sinal(5, "canal Topic do YouTube Music")
    if re.search(r"vevo\s*$", bruto, re.I):
        sinal(3, "canal VEVO")
    if yt.get("channel_verified"):
        sinal(1, "canal verificado")
    if yt.get("youtube_music"):
        sinal(2, "descrição do YouTube Music")
    explicitos = list(yt.get("artists") or []) if yt.get("fonte_artistas", "youtube") == "youtube" else []
    if nome and any(_mesmo_nome(nome, a) for a in explicitos):
        sinal(3, "canal igual ao artista creditado pelo YouTube")
    analise = analisar_titulo(yt.get("video_title") or yt.get("title") or "")
    pedacos = list(analise["lados"] or ()) + list(analise["segmentos"]) + list(analise["por"] or ())
    if nome and pedacos:
        if any(_mesmo_nome(nome, _sem_parenteses(p)) or any(_mesmo_nome(nome, a) for a in dividir_artistas(_sem_parenteses(p)))
               for p in pedacos):
            sinal(3, "canal aparece no título como artista")
        elif any(_mesmo_nome(nome, r) for p in pedacos for r in _remixers(p)):
            sinal(3, "canal é quem fez o remix citado no título")
        elif analise["lados"]:
            sinal(-2, "título traz outro artista que não é o canal")
    if nome and any(_mesmo_nome(nome, a) for a in artistas_conhecidos):
        sinal(2, "canal é um artista já confirmado na biblioteca")
    if _CANAL_CURADOR.search(bruto):
        sinal(-2, "nome típico de canal de re-upload/curadoria")
    if yt.get("descricao_reupload"):
        sinal(-2, "descrição de re-upload")
    if canal_conhecido:
        tipo = canal_conhecido.get("kind")
        if tipo == "artista":
            sinal(4, "canal já confirmado como do artista")
        elif tipo == "curador":
            sinal(-4, "canal já confirmado como re-upload")
    tipo = "artista" if pontos >= 3 else "curador" if pontos <= -2 else "desconhecido"
    return {"nome": nome, "bruto": bruto, "tipo": tipo, "pontos": pontos, "motivos": motivos}


def hipoteses_youtube(
    yt: Mapping[str, Any],
    canal_conhecido: Mapping[str, Any] | None = None,
    artistas_conhecidos: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Interpretações de (título, artistas) com confiança inicial, da maior para a menor."""
    if "video_title" not in yt:
        yt = dados_youtube_completos(yt)
    canal = avaliar_canal(yt, canal_conhecido, artistas_conhecidos)
    analise = analisar_titulo(yt.get("video_title") or yt.get("title") or "")
    feat = analise["feat"]
    conhecidos = [a for a in artistas_conhecidos if _text(a)]
    hipoteses: dict[tuple, dict[str, Any]] = {}

    def add(titulo: str, artistas: Sequence[str], origem: str, prior: float) -> None:
        titulo = _text(titulo).strip(" -–—\"'")
        artistas = [a for a in dict.fromkeys(_text(a) for a in list(artistas) + feat) if a]
        if not titulo:
            return
        chave = (_normalize_text(titulo), tuple(sorted(_normalize_text(a) for a in artistas)))
        atual = hipoteses.get(chave)
        if atual is None or prior > atual["prior"]:
            hipoteses[chave] = {"title": titulo, "artists": artistas, "origem": origem, "prior": prior}

    def conhecido(nome: str) -> bool:
        return any(_mesmo_nome(nome, a) for a in conhecidos)

    ytm = yt.get("youtube_music") or {}
    if ytm.get("title") and ytm.get("artists"):
        add(ytm["title"], ytm["artists"], "youtube_music", 0.97)

    base = analise["base"]
    lados = analise["lados"]
    explicitos = list(yt.get("artists") or [])
    fonte = yt.get("fonte_artistas") or "youtube"
    if explicitos and fonte == "youtube":
        titulo = _text(yt.get("track"))
        if titulo:
            titulo = analisar_titulo(titulo)["base"] or titulo
        elif lados and any(_mesmo_nome(lados[0], a) for a in explicitos):
            titulo = lados[1]
        elif lados and any(_mesmo_nome(lados[1], a) for a in explicitos):
            titulo = lados[0]
        else:
            titulo = base
        add(titulo, explicitos, "youtube_artista", 0.9)
    elif explicitos and fonte == "cache":
        add(base if not lados else lados[1], explicitos, "cache_local", 0.55)

    if lados:
        for lado_artista, lado_titulo in (lados, lados[::-1]):
            # parênteses não identificam o artista: "(Sir Meow Remix)" é o remixer
            artistas = dividir_artistas(_sem_parenteses(lado_artista))
            if not artistas:
                continue
            if any(conhecido(a) for a in artistas) or conhecido(_sem_parenteses(lado_artista)):
                add(lado_titulo, artistas, "artista_conhecido", 0.9)
            if canal["nome"] and canal["tipo"] != "curador" and (
                _mesmo_nome(canal["nome"], _sem_parenteses(lado_artista))
                or any(_mesmo_nome(canal["nome"], a) for a in artistas)
            ):
                add(lado_titulo, artistas, "youtube_titulo_canal",
                    0.9 if canal["tipo"] == "artista" else 0.85)
            if canal["nome"] and any(_mesmo_nome(canal["nome"], r) for r in _remixers(lado_titulo)):
                add(lado_titulo, artistas, "youtube_titulo_remix_do_canal", 0.85)
        curador = canal["tipo"] == "curador"
        add(lados[1], dividir_artistas(_sem_parenteses(lados[0])) or dividir_artistas(lados[0]),
            "youtube_titulo_artista_primeiro", 0.72 if curador else 0.62)
        add(lados[0], dividir_artistas(_sem_parenteses(lados[1])) or dividir_artistas(lados[1]),
            "youtube_titulo_artista_depois", 0.4 if curador else 0.45)
    if analise["por"]:
        titulo, artista = analise["por"]
        add(titulo, dividir_artistas(artista), "youtube_titulo_by", 0.75)
    for segmento in analise["segmentos"]:
        if (canal["nome"] and _mesmo_nome(canal["nome"], segmento)) or conhecido(segmento):
            add(base, dividir_artistas(segmento), "youtube_titulo_segmento", 0.82)

    nome = canal["nome"]
    titulo_canal = base if not lados else lados[1]
    if nome:
        if re.search(r"-\s*topic\s*$", canal["bruto"], re.I):
            add(base, [nome], "youtube_canal_topic", 0.95)
        elif canal_conhecido and canal_conhecido.get("kind") == "artista":
            add(titulo_canal if lados and _mesmo_nome(nome, lados[0]) else base,
                canal_conhecido.get("artists") or [nome], "canal_aprendido", 0.92)
        elif canal["tipo"] == "artista" and not lados:
            add(base, [nome], "youtube_canal_oficial", 0.86 if canal["pontos"] >= 5 else 0.78)
        elif canal["tipo"] == "desconhecido" and not lados:
            add(base, [nome], "youtube_canal", 0.4)
    add(base, [], "youtube_somente_titulo", 0.1)
    return sorted(hipoteses.values(), key=lambda h: h["prior"], reverse=True)


def _fundir_duplicatas(ranked: Sequence[tuple[Mapping[str, Any], float]], margem: float) -> dict[str, Any] | None:
    """O MusicBrainz costuma ter várias gravações da MESMA música (single, álbum,
    remaster...). Se as empatadas têm o mesmo título e artistas, não é ambiguidade:
    junta os lançamentos de todas para escolher o álbum."""
    topo, nota = ranked[0]
    grupo = [c for c, sc in ranked if nota - sc <= margem]
    if len(grupo) < 2 or not all(
        _mesmo_texto(_text(c.get("title")), _text(topo.get("title")), 0.95)
        and sorted(map(_normalize_text, _artists(c.get("artists"))))
        == sorted(map(_normalize_text, _artists(topo.get("artists"))))
        for c in grupo
    ):
        return None
    unido = dict(topo)
    vistos: set[str] = set()
    unido["releases"] = [r for c in grupo for r in c.get("releases") or []
                         if isinstance(r, Mapping) and r.get("id") not in vistos and not vistos.add(r.get("id"))]
    unido["isrcs"] = list(dict.fromkeys(i for c in grupo for i in c.get("isrcs") or []))
    return unido


def _mesmo_texto(a: str, b: str, limiar: float = 0.9) -> bool:
    return _similarity(a, b) >= limiar


def _mesmos_artistas(a: Sequence[str], b: Sequence[str]) -> bool:
    if not a or not b:
        return False
    juntos = lambda v: " ".join(sorted(" ".join(map(_normalize_text, v)).split()))  # noqa: E731
    return _mesmo_texto(juntos(a), juntos(b), 0.85) or _mesmo_nome(a[0], b[0])


def _pontuar(candidate: Mapping[str, Any], title: str, artists: Sequence[str], duration: float,
             usar_duracao: bool = True) -> float:
    title_score = _similarity(title, _text(candidate.get("title")))
    cand = _artists(candidate.get("artists"))
    if artists:
        artist_score = max(
            [_similarity(" ".join(artists), " ".join(cand))]
            + [_similarity(e, c) for e in artists for c in cand],
            default=0.0,
        )
        parts = [(title_score, 0.6), (artist_score, 0.35)]
    else:
        parts = [(title_score, 0.8)]
    fator = 1.0
    try:
        cand_duration = float(candidate.get("duration") or 0)
    except (TypeError, ValueError):
        cand_duration = 0.0
    if usar_duracao and duration > 0 and cand_duration > 0:
        delta = abs(duration - cand_duration)
        parts.append((max(0.0, 1.0 - delta / 15.0), 0.05 if artists else 0.2))
        if delta > 30:
            fator = 0.85  # provavelmente outra versão (ao vivo, extended...)
    total = sum(w for _, w in parts)
    return round(fator * sum(s * w for s, w in parts) / total, 4)


def escolher_release(
    candidate: Mapping[str, Any], album_hint: str = "", artistas: Sequence[str] = ()
) -> tuple[dict[str, Any] | None, float]:
    """Escolhe o lançamento mais provável (como o Picard): oficial, álbum/EP/single
    antes de coletânea/ao vivo, o mais antigo; o álbum do YouTube decide se houver."""
    releases = [r for r in candidate.get("releases") or [] if isinstance(r, Mapping) and r.get("id")]
    if not releases:
        return None, 0.0
    hint = _text(album_hint)
    pesos_tipo = {"album": 1.5, "ep": 1.2, "single": 1.0}
    pesos_secundarios = {"compilation": -2.5, "live": -1.5, "remix": -1.0, "dj-mix": -3.0,
                         "soundtrack": -0.5, "mixtape/street": -0.5, "demo": -1.0}

    def pontos(release: Mapping[str, Any]) -> float:
        p = 0.0
        if hint and _similarity(hint, release.get("title", "")) >= 0.9:
            p += 4
        status = _text(release.get("status")).casefold()
        p += {"official": 2.0, "bootleg": -3.0, "pseudo-release": -2.0}.get(status, 0.0)
        p += pesos_tipo.get(_text(release.get("primary_type")).casefold(), 0.0)
        p += sum(pesos_secundarios.get(t.casefold(), 0.0) for t in release.get("secondary_types") or [])
        artistas_release = release.get("artists") or []
        if artistas_release and _normalize_text(artistas_release[0]) == "various artists":
            p -= 2
        elif artistas_release and artistas and not _mesmos_artistas(artistas_release, artistas):
            p -= 1
        return p

    ordenados = sorted(releases, key=lambda r: (-pontos(r), _text(r.get("date")) or "9999"))
    melhor = dict(ordenados[0])
    confianca = 0.95 if hint and _similarity(hint, melhor.get("title", "")) >= 0.9 else 0.9
    if len({_normalize_text(r.get("title")) for r in ordenados[:3]}) > 1 and not hint:
        confianca = 0.86
    return melhor, confianca


def resolver_com_prioridade(
    video_id: str,
    dados_ytdlp: Mapping[str, Any] | None,
    correcoes_locais: Mapping[str, Any] | None = None,
    cache: MutableMapping[str, Any] | None = None,
    *,
    musicbrainz_client: MusicBrainzClient | Any | None = None,
    consultar_musicbrainz: bool = True,
    preferir: str = "youtube",
    revisar: str = "all",
    exigir_isrc: bool = False,
    exigir_artista_musicbrainz: bool = False,
    dados_locais: Mapping[str, Any] | None = None,
    canal_conhecido: Mapping[str, Any] | None = None,
    artistas_conhecidos: Sequence[str] = (),
    buscar_capa: bool = True,
    limite_candidatos: int = 10,
    limiar_correspondencia: float = 0.82,
    margem_correspondencia: float = 0.06,
) -> ResolutionResult:
    """Identifica título, artistas, álbum e capa com confiança por campo.

    * YouTube e MusicBrainz iguais: grafia oficial do MusicBrainz (confirmado).
    * Diferentes: vence ``preferir`` ("youtube" ou "musicbrainz") e a faixa é anotada.
    * Sem MusicBrainz: usa a melhor hipótese do YouTube; só anota se a confiança
      ficar abaixo de LIMIAR_CONFIANCA (ex.: artista deduzido do nome do canal).
    * Álbum ou capa não encontrados: anotados como faltando.

    ``revisar``: "all" marca OBSERVAR para divergências, baixa confiança e
    faltas; "diff" só divergências; "none" nada (as notas ficam guardadas).
    PENDENTE fica para quando falta título/artista ou um requisito --require-*.
    """
    if preferir not in PREFERENCIAS:
        raise ValueError(f"preferir deve ser um de {PREFERENCIAS}")
    if revisar not in REVISOES:
        raise ValueError(f"revisar deve ser um de {REVISOES}")
    dados_ytdlp = dados_ytdlp if isinstance(dados_ytdlp, Mapping) else {}
    cache = cache if cache is not None else {}
    manual = _confirmed_manual_record(correcoes_locais, video_id)
    yt = dados_youtube_completos(dados_ytdlp)
    local = _extract_local_metadata(dados_locais)
    canal = avaliar_canal(yt, canal_conhecido, artistas_conhecidos)
    analise = analisar_titulo(yt["video_title"] or yt["title"])
    ytm = yt["youtube_music"]

    hipoteses = hipoteses_youtube(yt, canal_conhecido, artistas_conhecidos) if (yt["video_title"] or yt["title"]) else []
    if local["title"]:
        hipoteses.append({"title": local["title"], "artists": local["artists"], "origem": "local", "prior": 0.5})
        hipoteses.sort(key=lambda h: h["prior"], reverse=True)
    vazia = {"title": "", "artists": [], "origem": "", "prior": 0.0}
    base = hipoteses[0] if hipoteses else vazia
    v("Modelo", f"canal '{canal['bruto']}' → {canal['tipo']} ({canal['pontos']:+d} pontos)")
    for motivo in canal["motivos"]:
        vv("Modelo", f"  canal: {motivo}")
    if analise["variante"]:
        vv("Modelo", f"versão alterada detectada no título: {analise['variante']}")
    for h in hipoteses[:8]:
        vv("Modelo", f"  hipótese {h['prior']:.0%}  {' & '.join(h['artists']) or '(sem artista)'} — "
                     f"{h['title']}  [{h['origem']}]")
    if base["title"]:
        v("Modelo", f"melhor palpite pelo YouTube: {' & '.join(base['artists']) or '?'} — {base['title']} "
                    f"({base['prior']:.0%}, {base['origem']})")
    explicita = next((h for h in hipoteses if h["origem"] in FONTES_EXPLICITAS), None)

    metadata: dict[str, Any] = {
        "id": _text(video_id) or _text(dados_ytdlp.get("id")),
        "title": base["title"], "artists": list(base["artists"]),
        "artist": (base["artists"] or [""])[0],
        "album": _text(ytm.get("album")) or yt["album"] or local["album"],
        "album_artists": [], "date": _text(ytm.get("date")) or yt["release_date"],
        "track_number": 0, "track_total": 0, "disc_number": 0, "disc_total": 0,
        "label": _text(ytm.get("label")),
        "isrc": yt["isrc"] or local["isrc"],
        "duration": yt["duration"], "uploader": yt["uploader"],
        "channel_id": yt["channel_id"],
        "thumbnail_url": yt["thumbnail_url"], "thumbnails": yt["thumbnails"],
        "cover_url": "", "cover_file": "", "cover_source": "",
        "musicbrainz_recording_id": "", "musicbrainz_release_id": "",
        "musicbrainz_release_group_id": "", "musicbrainz_artist_ids": [],
        "variant": analise["variante"],
    }
    provenance: dict[str, str] = {}
    confidence: dict[str, str] = {}
    scores: dict[str, float] = {}
    for campo in ("title", "artists"):
        if metadata[campo]:
            provenance[campo] = base["origem"]
            confidence[campo] = "provisoria"
            scores[campo] = base["prior"]
    if metadata["album"]:
        provenance["album"] = "youtube_music" if ytm.get("album") else ("youtube" if yt["album"] else "local")
        scores["album"] = 0.95 if ytm.get("album") else 0.7
    if metadata["isrc"]:
        provenance["isrc"] = "youtube" if yt["isrc"] else "local"
    if metadata["uploader"]:
        provenance["uploader"] = "youtube"
        confidence["uploader"] = f"canal_{canal['tipo']}"

    diag: dict[str, list[str]] = {"youtube": [], "local": [], "musicbrainz": []}
    diag["youtube"].append(f"canal '{canal['bruto']}' avaliado como {canal['tipo']}: " + "; ".join(canal["motivos"]))
    divergencias: list[str] = []
    baixa_confianca: list[str] = []
    faltando: list[str] = []
    candidatos: list[dict[str, Any]] = []
    escolhido: Mapping[str, Any] | None = None
    por_isrc = False
    hipotese_escolhida = base
    client = musicbrainz_client
    if consultar_musicbrainz and client is None:
        client = MusicBrainzClient()
    valores_manuais = _manual_values(manual)
    tem_manual = bool(valores_manuais.get("title") and valores_manuais.get("artists"))
    usar_duracao = not analise["variante"]

    if consultar_musicbrainz and client is not None and not tem_manual and hipoteses:
        try:
            isrc = normalize_isrc(metadata["isrc"])
            if isrc:
                brutos = _cached_search(cache, f"isrc:{isrc}", lambda: client.search_by_isrc(isrc, limit=limite_candidatos))
                exatos = [c for c in brutos if isrc in {normalize_isrc(v) for v in c.get("isrcs", [])}]
                chosen, ambiguous = _choose_exact_isrc(exatos) if exatos else (None, False)
                if chosen and not ambiguous:
                    escolhido, candidatos = chosen, [_public_candidate(chosen, 1.0)]
                    por_isrc = True
                    v("MusicBrainz", f"ISRC {isrc} identifica a gravação exata")
            if escolhido is None:
                melhor: tuple | None = None
                tentativas = 0
                for h in hipoteses:
                    if tentativas >= 3 or (h["prior"] < 0.3 and tentativas):
                        break
                    if h["artists"]:
                        chave = f"title_artist:{_normalize_text(h['title'])}|{_normalize_text(' '.join(h['artists']))}"
                        consulta = " ".join(h["artists"]) if len(h["artists"]) > 1 and len(" ".join(h["artists"])) < 60 else h["artists"][0]
                        op = (lambda h=h, a=consulta: client.search_by_title_artist(h["title"], a, limit=limite_candidatos))
                    else:
                        chave = f"title_only:{_normalize_text(h['title'])}"
                        op = (lambda h=h: client.search_by_title(h["title"], limit=limite_candidatos))
                    tentativas += 1
                    v("Modelo", f"conferindo no MusicBrainz: {' & '.join(h['artists']) or '(sem artista)'} — "
                                f"{h['title']} ({h['origem']})")
                    brutos = _cached_search(cache, chave, op)
                    if not brutos and len(h["artists"]) > 1:
                        brutos = _cached_search(cache, chave + "|primeiro", lambda h=h: client.search_by_title_artist(
                            h["title"], h["artists"][0], limit=limite_candidatos))
                    ranked = sorted(
                        ((c, _pontuar(c, h["title"], h["artists"], metadata["duration"], usar_duracao)) for c in
                         {(_text(c.get("recording_id")) or json.dumps(c, sort_keys=True, default=str)): c for c in brutos}.values()),
                        key=lambda item: item[1], reverse=True,
                    )
                    if not ranked:
                        continue
                    for cand, nota in ranked[:3]:
                        vv("MusicBrainz", f"  candidato {nota:.2f}  {' & '.join(_artists(cand.get('artists')))} — "
                                          f"{_text(cand.get('title'))}  ({len(cand.get('releases') or [])} lançamentos)")
                    limiar = limiar_correspondencia if h["artists"] else max(limiar_correspondencia, 0.9)
                    chosen, ambiguo = _choose_ranked(ranked, threshold=limiar, margin=margem_correspondencia)
                    if chosen is None and ambiguo and ranked[0][1] >= limiar:
                        chosen = _fundir_duplicatas(ranked, margem_correspondencia)
                        if chosen is not None:
                            v("MusicBrainz", "  empate entre entradas da mesma música; juntando os lançamentos")
                    if chosen is None:
                        v("MusicBrainz", f"  nenhum candidato confiável (melhor nota {ranked[0][1]:.2f}, mínimo "
                                         f"{limiar:.2f}{', empate entre candidatos' if ambiguo else ''})")
                    if chosen is not None:
                        total = ranked[0][1] + 0.15 * h["prior"]
                        if melhor is None or total > melhor[0]:
                            melhor = (total, chosen, h, ranked)
                        if ranked[0][1] >= 0.95 and h["prior"] >= 0.6:
                            break
                    elif ambiguo and not candidatos:
                        candidatos = [_public_candidate(c, sc) for c, sc in ranked[:5]]
                        diag["musicbrainz"].append(f"várias gravações parecidas para '{h['title']}'")
                if melhor:
                    _, escolhido, hipotese_escolhida, ranked = melhor
                    candidatos = [_public_candidate(c, sc) for c, sc in ranked[:5]]
        except Exception as exc:  # rede, limite, cliente injetado: o download segue sem MusicBrainz
            diag["musicbrainz"].append(f"MusicBrainz indisponível: {exc}")
            v("Erro", f"MusicBrainz indisponível; seguindo só com o YouTube: {exc}")

    if escolhido is not None:
        # Compara o MusicBrainz com o dado explícito do YouTube, se houver; senão
        # com a hipótese que bateu (que veio do título/canal do vídeo).
        referencia = explicita or hipotese_escolhida
        v("MusicBrainz", f"correspondência: {' & '.join(_artists(escolhido.get('artists')))} — "
                         f"{_text(escolhido.get('title'))} (via {hipotese_escolhida['origem']})")
        metadata["title"] = referencia["title"]
        metadata["artists"] = list(referencia["artists"])
        origem_yt = referencia["origem"]
        if escolhido.get("recording_id"):
            metadata["musicbrainz_recording_id"] = escolhido["recording_id"]
            provenance["musicbrainz_recording_id"] = "musicbrainz"
        mb = {"title": _text(escolhido.get("title")), "artists": _artists(escolhido.get("artists"))}
        for campo, rotulo in (("title", "título"), ("artists", "artista")):
            yt_val, mb_val = metadata[campo], mb[campo]
            if not mb_val:
                continue
            iguais = _mesmos_artistas(yt_val, mb_val) if campo == "artists" else _mesmo_texto(yt_val, mb_val)
            if por_isrc and not explicita:
                # ISRC exato prova que é a mesma gravação; o título do vídeo é só um palpite.
                iguais = True
            mostrar = (lambda x: " & ".join(x)) if campo == "artists" else (lambda x: x)
            if not yt_val or iguais:
                metadata[campo] = mb_val
                provenance[campo] = "youtube_validado_musicbrainz" if yt_val else "musicbrainz"
                confidence[campo] = "confirmada"
                scores[campo] = 0.98
                v("Comparação", f"{rotulo}: YouTube '{mostrar(yt_val)}' = banco '{mostrar(mb_val)}' → "
                                f"bate, usando a grafia do banco" if yt_val else
                                f"{rotulo}: só o banco tem → '{mostrar(mb_val)}'")
                continue
            divergencias.append(f"{rotulo} diverge: YouTube='{mostrar(yt_val)}' / MusicBrainz='{mostrar(mb_val)}'")
            escolha = mb_val if preferir == "musicbrainz" else yt_val
            v("Comparação", f"{rotulo}: YouTube '{mostrar(yt_val)}' ≠ banco '{mostrar(mb_val)}' → não bate; "
                            f"preferência {preferir}: usando '{mostrar(escolha)}' (vai para conferência)")
            if preferir == "musicbrainz":
                metadata[campo] = mb_val
                provenance[campo] = "musicbrainz"
                confidence[campo] = "preferencia_musicbrainz"
                scores[campo] = 0.8
            else:
                provenance[campo] = origem_yt
                confidence[campo] = "preferencia_youtube"
                scores[campo] = 0.7
        if provenance.get("artists") in {"musicbrainz", "youtube_validado_musicbrainz"}:
            ids = list(escolhido.get("artist_ids") or [])
            metadata["musicbrainz_artist_ids"] = [i for i in ids if i]
        isrcs = [normalize_isrc(v) for v in escolhido.get("isrcs", []) if normalize_isrc(v)]
        if isrcs:
            if metadata["isrc"] and metadata["isrc"] not in isrcs:
                divergencias.append(f"ISRC diverge: YouTube='{metadata['isrc']}' / MusicBrainz='{isrcs[0]}'")
                if preferir == "musicbrainz":
                    metadata["isrc"], provenance["isrc"] = isrcs[0], "musicbrainz"
            else:
                metadata["isrc"] = metadata["isrc"] or isrcs[0]
                provenance["isrc"] = "musicbrainz"
                confidence["isrc"] = "confirmada"

        release, conf_release = escolher_release(escolhido, metadata["album"], metadata["artists"])
        if release:
            tipo_release = "/".join([release.get("primary_type") or "?"] + list(release.get("secondary_types") or []))
            v("Álbum", f"lançamento escolhido: '{release['title']}' ({tipo_release}, {release.get('status') or '?'}, "
                       f"{release.get('date') or 'sem data'}) entre {len(escolhido.get('releases') or [])}; "
                       f"faixa {release.get('track_number') or '?'}/{release.get('track_total') or '?'}")
            album_yt = metadata["album"]
            if album_yt and not _mesmo_texto(album_yt, release["title"]) and provenance.get("album") in {"youtube_music", "youtube"}:
                divergencias.append(f"álbum diverge: YouTube='{album_yt}' / MusicBrainz='{release['title']}'")
                usar_mb_album = preferir == "musicbrainz"
            else:
                usar_mb_album = True
            if usar_mb_album:
                metadata["album"] = release["title"]
                provenance["album"] = "musicbrainz"
                scores["album"] = conf_release
            metadata.update({
                "album_artists": release.get("artists") or [],
                "date": release.get("date") or _text(escolhido.get("first_release_date")) or metadata["date"],
                "track_number": release.get("track_number") or 0,
                "track_total": release.get("track_total") or 0,
                "disc_number": release.get("disc_number") or 0,
                "disc_total": release.get("disc_total") or 0,
                "musicbrainz_release_id": release.get("id", ""),
                "musicbrainz_release_group_id": release.get("release_group_id", ""),
            })
            provenance["musicbrainz_release_id"] = "musicbrainz"
            if buscar_capa and hasattr(client, "get_album_cover"):
                try:
                    url = client.get_album_cover(release.get("release_group_id", ""), release.get("id", ""))
                except Exception as exc:
                    url = None
                    diag["musicbrainz"].append(f"Cover Art Archive indisponível: {exc}")
                v("Capa", f"Cover Art Archive: {url}" if url else "Cover Art Archive: este álbum não tem capa cadastrada")
                if url:
                    metadata["cover_url"], metadata["cover_source"] = url, "cover_art_archive"
                    provenance["cover_url"] = "cover_art_archive"
                    scores["cover"] = 0.97
    elif hipoteses and not tem_manual:
        if consultar_musicbrainz:
            diag["musicbrainz"].append("MusicBrainz não encontrou correspondência confiável")
            v("Comparação", "nada no banco para comparar; ficam os dados do YouTube "
                            f"(confiança {base['prior']:.0%})")
        if base["prior"] < LIMIAR_CONFIANCA:
            de_onde = {
                "youtube_titulo_segmento": "do título do vídeo (parte depois de \"|\")",
                "youtube_titulo_remix_do_canal": "do título do vídeo (remix feito pelo canal)",
                "youtube_titulo_artista_primeiro": "do título do vídeo (\"Artista - Música\")",
                "youtube_titulo_artista_depois": "do título do vídeo (\"Música - Artista\")",
                "youtube_canal": "do nome do canal, que pode não ser o artista",
                "youtube_canal_oficial": "do nome do canal",
                "youtube_titulo_by": "do título do vídeo",
                "youtube_somente_titulo": "do título (sem artista)",
                "cache_local": "de um download antigo",
                "local": "das tags do MP3",
            }.get(base["origem"], base["origem"])
            baixa_confianca.append(
                f"não confirmado no MusicBrainz; título/artista deduzidos {de_onde} "
                f"(confiança {base['prior']:.0%})"
            )

    if not metadata["cover_url"]:
        if ytm or re.search(r"-\s*topic\s*$", yt["uploader"], re.I):
            metadata["cover_source"] = "youtube_music_thumbnail"  # no YouTube Music a thumbnail é a capa
            scores["cover"] = 0.9
        elif metadata["thumbnail_url"]:
            metadata["cover_source"] = "youtube_thumbnail"
            scores["cover"] = 0.3
            faltando.append("capa do álbum não encontrada; usando a thumbnail do vídeo recortada")
    if not metadata["album"]:
        faltando.append("álbum não encontrado")

    for campo, valor in valores_manuais.items():
        metadata[campo] = valor
        provenance[campo] = "manual"
        confidence[campo] = "confirmada"
        scores["cover" if campo.startswith("cover") else campo] = 1.0
    if analise["variante"] and metadata["title"] and analise["variante"].casefold() not in metadata["title"].casefold():
        metadata["title"] = f"{metadata['title']} ({analise['variante']})"
    metadata["artist"] = (metadata["artists"] or [""])[0]

    motivos: list[str] = []
    if not metadata["title"]:
        motivos.append("campo obrigatório ausente: title")
    if not metadata["artists"]:
        motivos.append("nenhum artista identificado")
    if exigir_isrc and provenance.get("isrc") not in {"musicbrainz", "manual"}:
        motivos.append("ISRC não confirmado pelo MusicBrainz ou por revisão manual")
    if exigir_artista_musicbrainz and provenance.get("artists") not in {
        "musicbrainz", "youtube_validado_musicbrainz", "manual"
    }:
        motivos.append("artista não confirmado pelo MusicBrainz ou por revisão manual")

    if tem_manual:
        divergencias, baixa_confianca = [], []
    marcar = {"all": divergencias + baixa_confianca + faltando, "diff": divergencias, "none": []}[revisar]
    status = "PENDENTE" if motivos else ("OBSERVAR" if marcar else "RESOLVIDO")
    if tem_manual:
        v("SQL", "correção manual salva encontrada; ela vence YouTube e banco")
    v("Resultado", f"{status}: {' & '.join(metadata['artists']) or '?'} — {metadata['title'] or '?'}"
                   f"{' | álbum ' + metadata['album'] if metadata['album'] else ''}"
                   f"{' | capa: ' + metadata['cover_source'] if metadata['cover_source'] else ''}")
    for motivo in motivos + (marcar if status == "OBSERVAR" else []):
        v("Resultado", f"  motivo: {motivo}")
    for campo, nota in sorted(scores.items()):
        fonte = provenance.get(campo) or (metadata.get("cover_source") if campo == "cover" else "") or "?"
        vv("Resultado", f"  confiança {campo}: {nota:.0%} (fonte: {fonte})")
    observacoes = divergencias + baixa_confianca + faltando
    return ResolutionResult(
        video_id=metadata["id"],
        status=status,
        metadata=metadata,
        provenance=provenance,
        confidence=confidence,
        pending_reasons=motivos,
        candidates=candidatos,
        diagnostics=list(dict.fromkeys(observacoes + diag["youtube"] + diag["musicbrainz"])),
        diagnostics_by_source={k: list(dict.fromkeys(v)) for k, v in diag.items()},
        source_youtube=yt,
        source_local=local,
        observations=observacoes,
        policy={
            "preferir": preferir, "revisar": revisar,
            "hipotese": hipotese_escolhida.get("origem", ""),
            "canal": {k: canal[k] for k in ("nome", "tipo", "pontos")},
            "hipoteses": [{k: h[k] for k in ("title", "artists", "origem", "prior")} for h in hipoteses[:6]],
        },
        scores={k: round(v, 3) for k, v in scores.items()},
    )
