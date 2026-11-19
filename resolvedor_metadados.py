"""Resolvedor híbrido de metadados para integração futura com pltube.

O módulo é independente do downloader: não baixa áudio, não escreve tags, não
edita SQLite e não cria arquivos. Recebe metadados do yt-dlp, correções locais
e, opcionalmente, consulta MusicBrainz/Cover Art Archive. Retorna uma
ResolutionResult com campos, proveniência, candidatos e estado de revisão.

Uso mínimo:
    resultado = resolver_metadados(
        video_id="abc123",
        dados_ytdlp={"title": "Faixa", "artists": ["Artista"], "isrc": "USAAA9900001"},
        correcoes_locais={"abc123": {"status_revisao": "CORRIGIDO", "album_corrigido": "Álbum"}},
        musicbrainz_client=MusicBrainzClient(),  # reutilize a mesma instância
    )
    if resultado.status == "PENDENTE":
        print(resultado.motivos_pendencia)

MusicBrainz exige um User-Agent identificável e no máximo uma chamada por
segundo por aplicação/IP. O cliente aplica essa pausa às consultas MusicBrainz.
A chamada ao Cover Art Archive é separada. Consulte os links no fim deste
arquivo para a documentação oficial.
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
        try:
            body = self._transport(url, headers, self.timeout)
            payload = json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MusicBrainzError(
                f"HTTP {exc.code} consultando metadados externos.", exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise MusicBrainzError(f"Falha de conexão: {exc.reason}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MusicBrainzError("A API retornou JSON inválido.") from exc
        except TimeoutError as exc:
            raise MusicBrainzError("Tempo limite ao consultar a API.") from exc
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

    def get_front_cover(self, release_id: str) -> str | None:
        """Retorna URL de thumbnail frontal, sem baixar a imagem."""
        if not _valid_mbid(release_id):
            return None
        url = f"{COVER_ART_API}/release/{release_id}"
        try:
            payload = self._get_json(url, rate_limited=False)
        except MusicBrainzError as exc:
            if exc.status_code == 404:
                return None
            raise
        images = payload.get("images") or []
        fronts = [
            image
            for image in images
            if image.get("front") is True or "front" in {
                str(kind).casefold() for kind in image.get("types", [])
            }
        ]
        if not fronts:
            return None
        selected = fronts[0]
        thumbnails = selected.get("thumbnails") or {}
        return (
            thumbnails.get("500")
            or thumbnails.get("250")
            or selected.get("image")
            or None
        )


def resolver_metadados(
    video_id: str,
    dados_ytdlp: Mapping[str, Any] | None,
    correcoes_locais: Mapping[str, Any] | None = None,
    cache: MutableMapping[str, Any] | None = None,
    *,
    musicbrainz_client: MusicBrainzClient | Any | None = None,
    consultar_musicbrainz: bool = True,
    campos_obrigatorios: Sequence[str] = ("title", "artists", "album"),
    limite_candidatos: int = 10,
    limiar_correspondencia: float = 0.82,
    margem_correspondencia: float = 0.06,
    exigir_isrc: bool = False,
    exigir_artista_musicbrainz: bool = False,
    dados_locais: Mapping[str, Any] | None = None,
) -> ResolutionResult:
    """Combina correções confirmadas, yt-dlp e correspondências do MusicBrainz.

    Ordem prática:
      1. campos manuais confirmados (status CORRIGIDO/APLICADO/CONFIRMADO);
      2. correspondência única e forte do MusicBrainz, preferindo ISRC exato;
      3. dados explícitos do vídeo como valores de origem/sugestão.

    ``uploader``/``channel`` são preservados separadamente e nunca viram
    artista automaticamente. Uma busca ambígua não escolhe silenciosamente.
    ``cache`` é um mapping mutável opcional para reutilizar respostas entre
    chamadas; por padrão, ele é apenas em memória, não persistente.

    ``campos_obrigatorios`` aceita ``title``, ``artists``, ``album`` e
    ``cover`` (URL ou arquivo local). Artista/álbum oriundos apenas do YouTube
    permanecem provisórios e deixam a faixa pendente por padrão. Uma linha
    manual pode listar campos intencionalmente vazios em
    ``campos_confirmados_vazios``; isso não é confundido com falta de revisão.
    A thumbnail do YouTube é separada da capa do lançamento.
    """
    if not isinstance(dados_ytdlp, Mapping):
        dados_ytdlp = {}
    cache = cache if cache is not None else {}
    manual = _confirmed_manual_record(correcoes_locais, video_id)
    yt = _extract_youtube_metadata(dados_ytdlp)
    local = _extract_local_metadata(dados_locais)

    metadata: dict[str, Any] = {
        "id": _text(video_id) or _text(dados_ytdlp.get("id")),
        "title": yt["title"] or local["title"],
        "artists": list(yt["artists"] or local["artists"]),
        "artist": (yt["artists"] or local["artists"] or [""])[0],
        "album": yt["album"] or local["album"],
        "isrc": yt["isrc"] or local["isrc"],
        "duration": yt["duration"],
        "uploader": yt["uploader"],
        "thumbnail_url": yt["thumbnail_url"],
        "thumbnails": yt["thumbnails"],
        "cover_url": "",
        "cover_file": "",
        "musicbrainz_recording_id": "",
        "musicbrainz_release_id": "",
    }
    provenance: dict[str, str] = {}
    confidence: dict[str, str] = {}
    for field_name in ("title", "artists", "album", "isrc", "duration", "thumbnail_url"):
        if _has_value(yt.get(field_name)):
            provenance[field_name] = "youtube"
            confidence[field_name] = "provisoria"
        elif field_name in local and _has_value(local.get(field_name)):
            provenance[field_name] = "local"
            confidence[field_name] = "tag_local_provisoria"
    if metadata["uploader"]:
        provenance["uploader"] = "youtube"
        confidence["uploader"] = "origem_nao_artista"
    if metadata["thumbnails"]:
        provenance["thumbnails"] = "youtube"

    candidates: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    diagnostics_by_source: dict[str, list[str]] = {
        "youtube": [],
        "local": [],
        "musicbrainz": [],
    }
    if not yt["isrc"]:
        diagnostics_by_source["youtube"].append("yt-dlp/YouTube não forneceu um ISRC válido")
    if not yt["artists"]:
        diagnostics_by_source["youtube"].append("yt-dlp/YouTube não forneceu crédito de artista")
    if local["isrc"] and not yt["isrc"]:
        diagnostics_by_source["local"].append("ISRC recuperado das tags locais do MP3")
    if local["title"] or local["artists"] or local["album"]:
        diagnostics_by_source["local"].append("Metadados locais disponíveis como fallback")
    ambiguity_reasons: list[str] = []
    accepted_match = False
    search_attempted = False
    saw_musicbrainz_candidates = False
    release_state = "not_checked"
    client = musicbrainz_client
    if consultar_musicbrainz and client is None:
        client = MusicBrainzClient()

    # Se manualmente já vieram os campos essenciais, não há motivo para rede.
    cover_required = any(
        field_name in {"cover", "cover_url", "cover_file"}
        for field_name in campos_obrigatorios
    )
    missing_or_unconfirmed = [
        field_name
        for field_name in campos_obrigatorios
        if field_name not in {"cover", "cover_url", "cover_file"}
        and not _manual_has_field(manual, field_name)
    ]
    should_search = consultar_musicbrainz and (
        bool(missing_or_unconfirmed)
        or (cover_required and not _manual_has_field(manual, "cover"))
    )

    if should_search and client is not None:
        raw_candidates: list[dict[str, Any]] = []
        normalized_isrc = normalize_isrc(metadata["isrc"])
        try:
            if normalized_isrc:
                search_attempted = True
                raw_candidates = _cached_search(
                    cache,
                    f"isrc:{normalized_isrc}",
                    lambda: client.search_by_isrc(normalized_isrc, limit=limite_candidatos),
                )
                saw_musicbrainz_candidates = saw_musicbrainz_candidates or bool(raw_candidates)
                exact = [
                    item
                    for item in raw_candidates
                    if normalized_isrc in {
                        normalize_isrc(value) for value in item.get("isrcs", [])
                    }
                ]
                if exact:
                    chosen, ambiguous = _choose_exact_isrc(exact)
                    if ambiguous:
                        ambiguity_reasons.append(
                            "mais de uma gravação corresponde ao mesmo ISRC"
                        )
                    elif chosen:
                        accepted_match = True
                        _merge_musicbrainz_recording(
                            metadata,
                            provenance,
                            confidence,
                            chosen,
                            source_confidence="isrc_exato",
                            override_youtube=True,
                        )
                        release_state = _resolve_release(
                            metadata,
                            provenance,
                            confidence,
                            chosen,
                            client,
                            diagnostics,
                            manual,
                        )
                        candidates = [_public_candidate(chosen, score=1.0)]
                # Sem correspondência ISRC exata, tenta o par título/artista abaixo.
            if not accepted_match and not ambiguity_reasons:
                title = metadata["title"]
                artists = metadata["artists"]
                if title:
                    if artists:
                        search_key = f"title_artist:{_normalize_text(title)}|{_normalize_text(artists[0])}"
                        search_operation = lambda: client.search_by_title_artist(
                            title, artists[0], limit=limite_candidatos
                        )
                    else:
                        search_key = f"title_only:{_normalize_text(title)}"
                        search_operation = lambda: client.search_by_title(
                            title, limit=limite_candidatos
                        )
                    search_attempted = True
                    raw_candidates = _cached_search(cache, search_key, search_operation)
                    saw_musicbrainz_candidates = saw_musicbrainz_candidates or bool(raw_candidates)
                    ranked = _rank_candidates(
                        raw_candidates,
                        title=title,
                        artists=artists,
                        duration=metadata["duration"],
                    )
                    candidates = [_public_candidate(item[0], score=item[1]) for item in ranked]
                    chosen, ambiguous = _choose_ranked(
                        ranked,
                        threshold=(
                            max(
                                limiar_correspondencia,
                                0.88 if metadata["duration"] > 0 else 0.92,
                            )
                            if not artists
                            else limiar_correspondencia
                        ),
                        margin=margem_correspondencia,
                    )
                    if ambiguous:
                        ambiguity_reasons.append(
                            "várias correspondências plausíveis no MusicBrainz"
                        )
                    elif chosen:
                        accepted_match = True
                        _merge_musicbrainz_recording(
                            metadata,
                            provenance,
                            confidence,
                            chosen,
                            source_confidence="correspondencia_forte",
                            override_youtube=True,
                        )
                        release_state = _resolve_release(
                            metadata,
                            provenance,
                            confidence,
                            chosen,
                            client,
                            diagnostics,
                            manual,
                        )
                        _mark_youtube_fields_validated(
                            metadata, provenance, confidence, chosen
                        )
        except (MusicBrainzError, OSError, ValueError, TimeoutError) as exc:
            diagnostics.append(f"MusicBrainz indisponível: {exc}")
            diagnostics_by_source["musicbrainz"].append(
                f"MusicBrainz indisponível: {exc}"
            )
        except Exception as exc:
            # Um cliente injetado também pode propagar erros de transporte próprios.
            diagnostics.append(f"Falha na consulta de metadados: {exc}")
            diagnostics_by_source["musicbrainz"].append(
                f"Falha na consulta de metadados: {exc}"
            )

    if search_attempted and not accepted_match and not ambiguity_reasons:
        mensagem = (
            "MusicBrainz retornou candidatos, mas nenhum atingiu o limiar de correspondência"
            if saw_musicbrainz_candidates
            else "MusicBrainz não encontrou uma correspondência para ISRC/título/artista"
        )
        diagnostics_by_source["musicbrainz"].append(mensagem)
    if accepted_match and provenance.get("artists") == "youtube":
        diagnostics_by_source["musicbrainz"].append(
            "MusicBrainz aceitou a gravação, mas não retornou artistas para confirmar o crédito do vídeo"
        )
    if should_search and not search_attempted and client is not None and not metadata["title"]:
        diagnostics_by_source["musicbrainz"].append(
            "MusicBrainz não foi consultado porque o vídeo não forneceu título"
        )
    if ambiguity_reasons:
        diagnostics_by_source["musicbrainz"].append(
            "MusicBrainz retornou mais de uma correspondência plausível"
        )

    # Correções locais confirmadas vencem todos os resultados automáticos.
    manual_values = _manual_values(manual)
    for field_name, value in manual_values.items():
        if not _has_value(value):
            continue
        metadata[field_name] = value
        provenance[field_name] = "manual"
        confidence[field_name] = "confirmada"
    for field_name in _manual_empty_fields(manual):
        if field_name in manual_values:
            continue
        targets = ("cover_url", "cover_file") if field_name == "cover" else (field_name,)
        for target in targets:
            if target in {"artists", "cover_file"}:
                metadata[target] = [] if target == "artists" else ""
            elif target in metadata:
                metadata[target] = ""
            provenance[target] = "manual"
            confidence[target] = "confirmada_vazia"
    if metadata["artists"]:
        metadata["artist"] = metadata["artists"][0]

    reasons = list(ambiguity_reasons)
    if release_state == "ambiguous":
        if "album" in campos_obrigatorios and not _manual_has_field(manual, "album"):
            reasons.append("álbum associado a mais de uma edição possível")
        if cover_required and not _manual_has_field(manual, "cover"):
            reasons.append("capa de lançamento não pôde ser escolhida sem ambiguidade")
    elif release_state == "missing" and "album" in campos_obrigatorios:
        if not _manual_has_field(manual, "album"):
            reasons.append("nenhum lançamento foi localizado para confirmar o álbum")

    required = set(campos_obrigatorios)
    for field_name in campos_obrigatorios:
        if _manual_field_confirmed_empty(manual, field_name) and not (
            field_name == "isrc" and exigir_isrc
        ):
            continue
        if field_name == "artists":
            value = metadata.get("artists")
        elif field_name == "cover":
            value = metadata.get("cover_url") or metadata.get("cover_file")
        elif field_name in {"cover_url", "cover_file"}:
            value = metadata.get("cover_url") or metadata.get("cover_file")
        else:
            value = metadata.get(field_name)
        if not _has_value(value):
            reasons.append(f"campo obrigatório ausente: {field_name}")

    if "artists" in required and metadata.get("artists") and provenance.get("artists") == "youtube":
        if not _manual_has_field(manual, "artists"):
            reasons.append("artista informado somente pelo vídeo, sem confirmação independente")
    if "title" in required and metadata.get("title") and provenance.get("title") == "youtube":
        if not _manual_has_field(manual, "title"):
            reasons.append("título informado somente pelo vídeo, sem confirmação independente")
    if "album" in required and metadata.get("album") and provenance.get("album") == "youtube":
        if not accepted_match and not _manual_has_field(manual, "album"):
            reasons.append("álbum informado somente pelo vídeo, sem lançamento confirmado")
    for field_name, rotulo in (
        ("title", "título"), ("artists", "artista"), ("album", "álbum")
    ):
        if (
            field_name in required
            and _has_value(metadata.get(field_name))
            and provenance.get(field_name) == "local"
            and not _manual_has_field(manual, field_name)
        ):
            reasons.append(f"{rotulo} veio de tags/estrutura local, sem confirmação independente")
    if exigir_isrc and metadata.get("isrc") and provenance.get("isrc") not in {
        "musicbrainz", "manual"
    }:
        reasons.append("ISRC disponível, mas não confirmado pelo MusicBrainz ou por revisão manual")
    if exigir_artista_musicbrainz:
        fontes_confiaveis_artista = {
            "musicbrainz", "youtube_validado_musicbrainz", "manual"
        }
        if (
            not metadata.get("artists")
            or provenance.get("artists") not in fontes_confiaveis_artista
        ):
            reasons.append("artista não confirmado pelo MusicBrainz ou por revisão manual")
            diagnostics_by_source["musicbrainz"].append(
                "Nenhum artista foi confirmado pelo MusicBrainz"
            )
    if cover_required and metadata.get("cover_url") and provenance.get("cover_url") == "youtube_thumbnail_fallback":
        reasons.append("há apenas thumbnail do vídeo, não capa de lançamento confirmada")
    if cover_required and not (metadata.get("cover_url") or metadata.get("cover_file")):
        reasons.append("nenhuma capa de lançamento confirmada")

    # Remover mensagens repetidas preservando a ordem.
    reasons = list(dict.fromkeys(reasons))
    status = "PENDENTE" if reasons else "RESOLVIDO"
    return ResolutionResult(
        video_id=metadata["id"],
        status=status,
        metadata=metadata,
        provenance=provenance,
        confidence=confidence,
        pending_reasons=reasons,
        candidates=candidates,
        diagnostics=list(dict.fromkeys(
            diagnostics
            + diagnostics_by_source["youtube"]
            + diagnostics_by_source["musicbrainz"]
        )),
        diagnostics_by_source={
            fonte: list(dict.fromkeys(mensagens))
            for fonte, mensagens in diagnostics_by_source.items()
        },
        source_youtube=yt,
        source_local=local,
    )


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


def _manual_has_field(record: Mapping[str, Any], field_name: str) -> bool:
    values = _manual_values(record)
    empty_fields = _manual_empty_fields(record)
    if field_name == "cover":
        return bool(
            "cover_url" in values
            or "cover_file" in values
            or "cover" in empty_fields
            or "cover_url" in empty_fields
            or "cover_file" in empty_fields
        )
    if field_name == "cover_url" and "cover_file" in values:
        return True
    return field_name in values or field_name in empty_fields


def _manual_empty_fields(record: Mapping[str, Any]) -> set[str]:
    raw = record.get("campos_confirmados_vazios", [])
    if isinstance(raw, str):
        fields = [part.strip() for part in raw.split("|")]
    elif isinstance(raw, (list, tuple, set)):
        fields = [str(part).strip() for part in raw]
    else:
        fields = []
    aliases = {
        "titulo": "title",
        "titulo_corrigido": "title",
        "artista": "artists",
        "artistas": "artists",
        "artistas_corrigidos": "artists",
        "album_corrigido": "album",
        "isrc_corrigido": "isrc",
        "capa": "cover",
        "capa_url": "cover_url",
        "capa_arquivo": "cover_file",
    }
    supported = {"title", "artists", "album", "isrc", "cover", "cover_url", "cover_file"}
    result = set()
    for field_name in fields:
        normalized = aliases.get(field_name.casefold(), field_name.casefold())
        if normalized in supported:
            result.add(normalized)
    return result


def _manual_field_confirmed_empty(record: Mapping[str, Any], field_name: str) -> bool:
    empty_fields = _manual_empty_fields(record)
    if field_name == "cover":
        return bool(empty_fields.intersection({"cover", "cover_url", "cover_file"}))
    if field_name in {"cover_url", "cover_file"}:
        return bool(empty_fields.intersection({"cover", "cover_url", "cover_file"}))
    return field_name in empty_fields


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


def _merge_musicbrainz_recording(
    metadata: dict[str, Any],
    provenance: dict[str, str],
    confidence: dict[str, str],
    candidate: Mapping[str, Any],
    *,
    source_confidence: str,
    override_youtube: bool,
) -> None:
    if candidate.get("recording_id"):
        metadata["musicbrainz_recording_id"] = candidate["recording_id"]
        provenance["musicbrainz_recording_id"] = "musicbrainz"
        confidence["musicbrainz_recording_id"] = source_confidence
    if candidate.get("title") and (override_youtube or not metadata.get("title")):
        metadata["title"] = candidate["title"]
        provenance["title"] = "musicbrainz"
        confidence["title"] = source_confidence
    candidate_artists = _artists(candidate.get("artists"))
    if candidate_artists and (override_youtube or not metadata.get("artists")):
        metadata["artists"] = candidate_artists
        metadata["artist"] = candidate_artists[0]
        provenance["artists"] = "musicbrainz"
        confidence["artists"] = source_confidence
    candidate_isrcs = [normalize_isrc(value) for value in candidate.get("isrcs", [])]
    candidate_isrcs = [value for value in candidate_isrcs if value]
    if candidate_isrcs and (override_youtube or not metadata.get("isrc")):
        metadata["isrc"] = candidate_isrcs[0]
        provenance["isrc"] = "musicbrainz"
        confidence["isrc"] = source_confidence


def _mark_youtube_fields_validated(
    metadata: Mapping[str, Any],
    provenance: dict[str, str],
    confidence: dict[str, str],
    candidate: Mapping[str, Any],
) -> None:
    """Marca título/créditos do vídeo como corroborados por match forte."""
    for field_name in ("title", "artists"):
        if field_name == "artists" and not _artists(candidate.get("artists")):
            continue
        if field_name == "title" and not _text(candidate.get("title")):
            continue
        if _has_value(metadata.get(field_name)) and provenance.get(field_name) == "youtube":
            provenance[field_name] = "youtube_validado_musicbrainz"
            confidence[field_name] = "correspondencia_forte"


def _resolve_release(
    metadata: dict[str, Any],
    provenance: dict[str, str],
    confidence: dict[str, str],
    candidate: Mapping[str, Any],
    client: Any,
    diagnostics: list[str],
    manual: Mapping[str, Any],
) -> str:
    """Resolve release/cover e informa se a edição é única, ambígua ou ausente."""
    releases = [
        release for release in candidate.get("releases", [])
        if isinstance(release, Mapping) and _text(release.get("id"))
    ]
    by_id: dict[str, Mapping[str, Any]] = {}
    for release in releases:
        by_id.setdefault(_text(release.get("id")), release)
    releases = list(by_id.values())
    if len(releases) == 1:
        release = releases[0]
        release_title = _text(release.get("title"))
        if release_title and not _manual_has_field(manual, "album"):
            metadata["album"] = release_title
            provenance["album"] = "musicbrainz"
            confidence["album"] = "release_unico"
        metadata["musicbrainz_release_id"] = _text(release.get("id"))
        provenance["musicbrainz_release_id"] = "musicbrainz"
        cover_url = None
        if not _manual_has_field(manual, "cover_url"):
            try:
                cover_url = client.get_front_cover(_text(release.get("id")))
            except Exception as exc:
                diagnostics.append(f"Cover Art Archive indisponível: {exc}")
        if cover_url:
            metadata["cover_url"] = cover_url
            provenance["cover_url"] = "cover_art_archive"
            confidence["cover_url"] = "frontal_do_release"
        elif metadata.get("thumbnail_url"):
            # É apenas uma alternativa visual; não a identifica como capa de álbum.
            metadata["cover_url"] = metadata["thumbnail_url"]
            provenance["cover_url"] = "youtube_thumbnail_fallback"
            confidence["cover_url"] = "provisoria"
        return "unique" if release_title else "missing"

    album_titles = list(dict.fromkeys(
        _text(release.get("title")) for release in releases if _text(release.get("title"))
    ))
    if len(album_titles) == 1:
        if not _manual_has_field(manual, "album"):
            metadata["album"] = album_titles[0]
            provenance["album"] = "musicbrainz"
            confidence["album"] = "titulo_de_album_compartilhado_por_edicoes"
        return "ambiguous"
    if len(album_titles) > 1:
        return "ambiguous"
    return "missing"


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


def _rank_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    title: str,
    artists: Sequence[str],
    duration: float,
) -> list[tuple[Mapping[str, Any], float]]:
    unique: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        recording_id = _text(candidate.get("recording_id"))
        key = recording_id or json.dumps(candidate, sort_keys=True, ensure_ascii=False)
        unique.setdefault(key, candidate)
    ranked = [
        (candidate, _match_score(candidate, title, artists, duration))
        for candidate in unique.values()
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _match_score(
    candidate: Mapping[str, Any], title: str, artists: Sequence[str], duration: float
) -> float:
    title_score = _similarity(title, _text(candidate.get("title")))
    candidate_artists = _artists(candidate.get("artists"))
    artist_score = max(
        (_similarity(expected, actual) for expected in artists for actual in candidate_artists),
        default=0.0,
    )
    parts = (
        [(title_score, 0.62), (artist_score, 0.33)]
        if artists else [(title_score, 0.82)]
    )
    candidate_duration = candidate.get("duration")
    try:
        candidate_duration = float(candidate_duration or 0)
    except (TypeError, ValueError):
        candidate_duration = 0.0
    if duration > 0 and candidate_duration > 0:
        delta = abs(duration - candidate_duration)
        duration_score = max(0.0, 1.0 - delta / 20.0)
        parts.append((duration_score, 0.05 if artists else 0.18))
    weight_total = sum(weight for _, weight in parts)
    return round(sum(score * weight for score, weight in parts) / weight_total, 4)


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
    "resolver_metadados",
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


def limpar_titulo_youtube(title: str) -> tuple[str, list[str]]:
    """Remove ruídos ("(Official Video)", "[HD]", "| NCS ...") e separa "feat. X"."""
    analise = analisar_titulo(title)
    return analise["base"], analise["feat"]


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
        if any(_mesmo_nome(nome, p) or any(_mesmo_nome(nome, a) for a in dividir_artistas(p)) for p in pedacos):
            sinal(3, "canal aparece no título como artista")
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
            artistas = dividir_artistas(lado_artista)
            if any(conhecido(a) for a in artistas) or conhecido(lado_artista):
                add(lado_titulo, artistas, "artista_conhecido", 0.9)
            if canal["nome"] and canal["tipo"] != "curador" and (
                _mesmo_nome(canal["nome"], lado_artista)
                or any(_mesmo_nome(canal["nome"], a) for a in artistas)
            ):
                add(lado_titulo, artistas, "youtube_titulo_canal",
                    0.9 if canal["tipo"] == "artista" else 0.85)
        curador = canal["tipo"] == "curador"
        add(lados[1], dividir_artistas(lados[0]), "youtube_titulo_artista_primeiro", 0.72 if curador else 0.62)
        add(lados[0], dividir_artistas(lados[1]), "youtube_titulo_artista_depois", 0.4 if curador else 0.45)
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
