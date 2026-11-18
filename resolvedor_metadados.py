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
            return copy.deepcopy(self._json_cache[url])
        if rate_limited:
            self._wait_for_musicbrainz_slot()
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
        cache[key] = copy.deepcopy(search())
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
    artists = []
    for credit in recording.get("artist-credit", []) or []:
        if not isinstance(credit, Mapping):
            continue
        artist = credit.get("artist")
        if isinstance(artist, Mapping):
            name = _text(artist.get("name"))
        else:
            name = _text(credit.get("name"))
        if name:
            artists.append(name)
    releases = []
    for release in recording.get("releases", []) or []:
        if not isinstance(release, Mapping):
            continue
        release_id = _text(release.get("id"))
        if release_id:
            releases.append({
                "id": release_id,
                "title": _text(release.get("title")),
                "date": _text(release.get("date")),
            })
    try:
        duration_ms = int(recording.get("length") or 0)
        duration = duration_ms / 1000.0 if duration_ms > 0 else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "recording_id": recording_id,
        "title": _text(recording.get("title")),
        "artists": list(dict.fromkeys(artists)),
        "isrcs": [value for value in (normalize_isrc(item) for item in recording.get("isrcs", []) or []) if value],
        "duration": duration,
        "releases": releases,
    }


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
