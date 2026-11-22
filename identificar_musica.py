"""Identifica o artista e o título de cada vídeo.

É simples de propósito:

1. Se o YouTube/YouTube Music já diz quem é o artista (faixa oficial, clipe oficial,
   descrição "Provided to YouTube by" ou créditos "Música neste vídeo"), vale isso.
2. Senão, junta os nomes possíveis: artistas do banco que aparecem no título, os dois
   lados de "Artista - Música" e o canal.
3. Cada nome possível é procurado no banco de artistas (sql/artistas.sqlite3) e,
   se não estiver lá, na web (página de artista no YouTube Music).
4. Achou: CONFIRMADO. Não achou: NAO_CONFIRMADO, com o palpite mais provável
   ("Artista - Música" → Artista; sem isso, o canal). A música é baixada do mesmo jeito,
   na pasta do palpite; você só confere no SQL as NAO_CONFIRMADO.

A biblioteca ytmusicapi faz as consultas ao YouTube Music (sem login). Sem ela,
só o banco de artistas confirma.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Sequence

import textos as tx
from verbose import v, vv

CONFIRMADO = "CONFIRMADO"
NAO_CONFIRMADO = "NAO_CONFIRMADO"

# De onde veio o artista (gravado em provenance).
FONTE_MUSICA = "youtube_music"                # o vídeo é a faixa oficial no YouTube Music
FONTE_DESCRICAO = "youtube_music_descricao"   # descrição "Provided to YouTube by"
FONTE_CLIPE = "youtube_music_clipe"           # clipe oficial no YouTube Music
FONTE_CREDITOS = "youtube_creditos"           # "Música neste vídeo" do YouTube
FONTE_BANCO = "banco_artistas"                # nome possível achado no banco de artistas
FONTE_WEB = "youtube_music_artista"           # nome possível achado como artista no YouTube Music
FONTE_LOCAL = "local"                         # tags do MP3 / identificação anterior
FONTE_TITULO = "titulo"                       # palpite: "Artista - Música"
FONTE_CANAL = "canal"                         # palpite: nome do canal

FONTES_OFICIAIS = {FONTE_MUSICA, FONTE_DESCRICAO, FONTE_CLIPE, FONTE_CREDITOS}
FONTES_CONFIAVEIS = FONTES_OFICIAIS | {FONTE_BANCO, FONTE_WEB}
DESCRICAO_FONTE = {
    FONTE_MUSICA: "faixa oficial no YouTube Music", FONTE_DESCRICAO: "descrição do YouTube Music",
    FONTE_CLIPE: "clipe oficial no YouTube Music", FONTE_CREDITOS: "créditos de música do YouTube",
    FONTE_BANCO: "banco de artistas", FONTE_WEB: "artista no YouTube Music",
    FONTE_LOCAL: "tags do MP3", FONTE_TITULO: "título do vídeo", FONTE_CANAL: "nome do canal",
}

TAMANHO_CAPA = "=w544-h544-l90-rj"

_TIPOS_YTM = {
    "MUSIC_VIDEO_TYPE_ATV": "musica",                 # faixa de áudio da gravadora
    "MUSIC_VIDEO_TYPE_OMV": "clipe",                  # clipe oficial
    "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC": "clipe",
    "MUSIC_VIDEO_TYPE_UGC": "video",                  # vídeo de qualquer usuário
}


# --------------------------------------------------------------- YouTube Music

class ClienteYTMusic:
    """Consultas ao YouTube Music (via ytmusicapi), com cache e uma pausa curta entre elas.
    """

    def __init__(self, api: Any = None, pausa: float = 0.3, avisar: Callable[[str], None] = print):
        self._api = api
        self._erro = ""
        self.pausa = pausa
        self._ultima = 0.0
        self._cache: dict[Any, Any] = {}
        self._avisar = avisar

    def _carregar(self) -> Any:
        if self._api is None and not self._erro:
            try:
                from ytmusicapi import YTMusic  # type: ignore

                self._api = YTMusic()
            except ImportError:
                self._erro = ("ytmusicapi não está instalado: sem ele não dá para consultar o YouTube Music. "
                              "Instale com: pip install -r requirements.txt")
            except Exception as exc:  # noqa: BLE001 - qualquer falha: segue sem YouTube Music
                self._erro = f"YouTube Music indisponível: {exc}"
            if self._erro:
                self._avisar(self._erro)
        return self._api

    @property
    def disponivel(self) -> bool:
        return self._carregar() is not None

    def _chamar(self, chave: Any, funcao: Callable[[Any], Any]) -> Any:
        if chave in self._cache:
            return self._cache[chave]
        api = self._carregar()
        if api is None:
            return None
        espera = self.pausa - (time.monotonic() - self._ultima)
        if espera > 0:
            time.sleep(espera)
        try:
            resposta = funcao(api)
        except Exception as exc:  # noqa: BLE001 - vídeo indisponível, rede, mudança no site
            v("YouTube Music", f"consulta falhou ({chave[0]}): {exc}")
            resposta = None
        self._ultima = time.monotonic()
        self._cache[chave] = resposta
        return resposta

    def faixa(self, video_id: str) -> dict[str, Any] | None:
        """O vídeo como o YouTube Music o vê: tipo (musica/clipe/video), título, artistas, álbum."""
        if not video_id:
            return None
        dados = self._chamar(("vídeo", video_id), lambda api: api.get_watch_playlist(videoId=video_id, limit=1))
        faixas = (dados or {}).get("tracks") or [] if isinstance(dados, Mapping) else []
        bruto = next((f for f in faixas if isinstance(f, Mapping) and f.get("videoId") == video_id), None)
        return ler_faixa(bruto) if bruto else None

    def buscar(self, consulta: str, limite: int = 10) -> list[dict[str, Any]]:
        consulta = re.sub(r"\s{2,}", " ", consulta).strip()
        if not consulta:
            return []
        dados = self._chamar(("busca", consulta), lambda api: api.search(consulta, filter="songs", limit=limite))
        return [f for f in (ler_faixa(d) for d in dados or [] if isinstance(d, Mapping)) if f]

    def artista(self, nome: str) -> str | None:
        """O nome como está no YouTube Music, se existe um artista com esse nome; senão None."""
        nome = re.sub(r"\s{2,}", " ", str(nome or "")).strip()
        if not nome:
            return None
        dados = self._chamar(("artista", nome), lambda api: api.search(nome, filter="artists", limit=5))
        for item in dados or []:
            achado = tx._text(item.get("artist") or item.get("title")) if isinstance(item, Mapping) else ""
            if achado and tx._sem_espaco(achado) == tx._sem_espaco(nome):
                return achado
        return None

    def album(self, browse_id: str) -> dict[str, Any] | None:
        if not browse_id:
            return None
        dados = self._chamar(("álbum", browse_id), lambda api: api.get_album(browse_id))
        return dict(dados) if isinstance(dados, Mapping) else None


def _segundos(valor: Any) -> int:
    if isinstance(valor, (int, float)):
        return int(valor)
    partes = re.findall(r"\d+", str(valor or ""))
    if not partes or not re.fullmatch(r"\s*\d+(?::\d{1,2}){0,2}\s*", str(valor or "")):
        return 0
    total = 0
    for parte in partes:
        total = total * 60 + int(parte)
    return total


def _maior_imagem(imagens: Any) -> str:
    if not isinstance(imagens, list):
        return ""
    validas = [i for i in imagens if isinstance(i, Mapping) and i.get("url")]
    if not validas:
        return ""
    return str(max(validas, key=lambda i: int(i.get("width") or 0) * int(i.get("height") or 0))["url"])


def url_capa(url: str) -> str:
    """Capa quadrada do YouTube Music em 544x544 JPEG; thumbnails de vídeo (16:9) não servem."""
    if "googleusercontent.com" not in url and "ggpht.com" not in url:
        return ""
    return re.sub(r"=w\d+-h\d+.*$", "", url) + TAMANHO_CAPA


def ler_faixa(dados: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Converte um item da ytmusicapi (watch playlist, busca ou álbum) para o formato daqui."""
    if not isinstance(dados, Mapping):
        return None
    titulo = tx._text(dados.get("title"))
    if not titulo:
        return None
    artistas: list[str] = []
    for item in dados.get("artists") or []:
        nome = tx._text(item.get("name") if isinstance(item, Mapping) else item)
        if not nome:
            continue
        # artista com página (id) é um nome só; texto solto pode juntar vários ("A & B")
        tem_pagina = isinstance(item, Mapping) and item.get("id")
        artistas += [nome] if tem_pagina else tx.dividir_artistas(nome)
    album = dados.get("album")
    if isinstance(album, str):
        album = {"name": album}
    album = album if isinstance(album, Mapping) else {}
    faixa = {
        "video_id": tx._text(dados.get("videoId")),
        "title": titulo,
        "artists": list(dict.fromkeys(artistas)),
        "album": tx._text(album.get("name")),
        "album_id": tx._text(album.get("id")),
        "year": tx._text(dados.get("year")) if re.fullmatch(r"\d{4}", tx._text(dados.get("year"))) else "",
        "duration": int(dados.get("duration_seconds") or 0) or _segundos(dados.get("length") or dados.get("duration")),
        "tipo": _TIPOS_YTM.get(tx._text(dados.get("videoType")), "musica" if dados.get("resultType") == "song" else "video"),
        "capa": url_capa(_maior_imagem(dados.get("thumbnail") or dados.get("thumbnails"))),
    }
    contraparte = ler_faixa(dados.get("counterpart")) if isinstance(dados.get("counterpart"), Mapping) else None
    if contraparte:
        faixa["contraparte"] = contraparte
    return faixa


def baixar_imagem(url: str, max_bytes: int = 8 * 1024 * 1024, timeout: float = 30) -> bytes | None:
    """Baixa uma capa (JPEG/PNG); None se não for imagem."""
    if not str(url).lower().startswith("https://"):
        return None
    pedido = urllib.request.Request(url, headers={"User-Agent": "plpython (capa do álbum)"})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:  # noqa: S310 - só https
            dados = resposta.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        v("Capa", f"não foi possível baixar a capa: {exc}")
        return None
    if len(dados) > max_bytes:
        return None
    if dados[:3] == b"\xff\xd8\xff" or dados[:8] == b"\x89PNG\r\n\x1a\n":
        return dados
    return None


# --------------------------------------------------------------- títulos

# Marcas de versão: o MP3 de um "slowed" não pode ficar com o nome da música original.
_VERSOES = (
    ("slowed", r"slowed"), ("sped up", r"sped\s*up|speed\s*up"), ("nightcore", r"nightcore"),
    ("daycore", r"daycore"), ("reverb", r"reverb"), ("8d", r"\b8d\b"), ("bass boosted", r"bass\s*boost"),
    ("remix", r"\bremix\b|\bbootleg\b|\bflip\b"), ("ao vivo", r"\blive\b|ao\s+vivo|en\s+vivo"),
    ("acústico", r"acoustic|ac[uú]stic[oa]"), ("instrumental", r"instrumental"),
    ("karaokê", r"karaok[eê]"), ("cover", r"\bcover\b"), ("extended", r"extended"),
)
# "#shorts #music" no fim ou "(#a #b)": fora; "#FLUTEGANG" sozinho é o nome da música
_HASHTAG = re.compile(r"(?:\s+#[^\W\d]\w*)+\s*$|\s*[(\[](?:\s*#[^\W\d]\w*)+\s*[)\]]")
_TAG_INICIAL = re.compile(r"^\s*[\[【][^\]】]*[\]】]\s*(?:[-–—|]\s*)?")
_NUMERO_FAIXA = re.compile(r"^\s*\d{1,3}[.)]\s+")


def versoes(texto: Any) -> set[str]:
    texto = str(texto or "")
    return {nome for nome, padrao in _VERSOES if re.search(padrao, texto, re.I)}


def limpar_titulo(titulo: Any) -> str:
    """Tira do título do vídeo o que não é nome de música: emojis, #hashtags, "[HD]",
    "(Official Video)", "| Lyrics", número de faixa. As marcas de versão ficam."""
    texto = tx._remover_simbolos(tx._text(titulo))
    texto = _HASHTAG.sub(" ", texto)
    texto = _TAG_INICIAL.sub("", texto) or texto
    texto = _NUMERO_FAIXA.sub("", texto) or texto
    anterior = None
    while anterior != texto:
        anterior = texto
        texto = tx._RUIDO_PARENTESES.sub("", texto).strip()
        texto = tx._RUIDO_FINAL.sub("", texto).strip()
    partes = tx.partes_do_titulo(texto)
    if len(partes) > 1:  # pedaços que são só ruído: "Lyrics", "com cifras", "Vídeo Clip..."
        sobram = [p for p in partes if not tx._PARTE_RUIDO.match(tx._sem_parenteses(p))]
        if sobram and len(sobram) < len(partes):
            texto = " - ".join(sobram)
    return re.sub(r"\s{2,}", " ", texto).strip(" -–—|:;\"'")


_COM = re.compile(r"\s*[(\[]\s*with\s+([^)\]]+)[)\]]\s*$", re.I)  # "Música (with A & B)"


def _feat_do_titulo(titulo: str) -> tuple[str, list[str]]:
    """"Música (feat. B & C)" -> ("Música", ["B", "C"]); "(with B)" também."""
    achou = tx._TITLE_FEAT.search(titulo) or _COM.search(titulo)
    if not achou or achou.start() == 0:
        return titulo, []
    nomes = re.sub(r"\s+(?:and|e)\s+", " & ", achou.group(1))
    return titulo[: achou.start()].strip(" -–—"), tx.dividir_artistas(nomes)


def _com_versao(titulo: str, video: str) -> str:
    """Se o vídeo é uma versão (slowed, nightcore...) e o título não diz, acrescenta."""
    faltam = versoes(video) - versoes(titulo)
    if not faltam:
        return titulo
    for dentro in re.findall(r"[(\[【]([^()\[\]【】]+)[)\]】]", str(video)):
        if versoes(dentro) & faltam:  # "(Sir Meow Remix)", "(slowed + reverb)"
            dentro = re.sub(r"\b(?:official|oficial)\s+", "", dentro, flags=re.I).strip()
            return f"{titulo} ({dentro})"
    return f"{titulo} ({' + '.join(sorted(faltam))})"


# --------------------------------------------------------------- identificação


def _de_faixa(faixa: Mapping[str, Any], fonte: str) -> dict[str, Any]:
    return {"title": faixa["title"], "artists": list(faixa["artists"]), "album": faixa.get("album") or "",
            "album_id": faixa.get("album_id") or "", "year": faixa.get("year") or "",
            "capa": faixa.get("capa") or "", "video_id": faixa.get("video_id") or "", "fonte": fonte}


def _detalhes_do_album(cliente: ClienteYTMusic | None, achado: dict[str, Any]) -> dict[str, Any]:
    info = cliente.album(achado.get("album_id") or "") if cliente else None
    if not info:
        return {}
    faixas = [f for f in info.get("tracks") or [] if isinstance(f, Mapping)]
    numero = 0
    for indice, faixa in enumerate(faixas, 1):
        if (achado.get("video_id") and faixa.get("videoId") == achado["video_id"]) or \
                tx._similarity(tx._text(faixa.get("title")), achado["title"]) >= 0.97:
            numero = int(faixa.get("trackNumber") or indice)
            break
    artistas = [tx._text(a.get("name") if isinstance(a, Mapping) else a) for a in info.get("artists") or []]
    return {
        "album": tx._text(info.get("title")),
        "year": tx._text(info.get("year")) if re.fullmatch(r"\d{4}", tx._text(info.get("year"))) else "",
        "track_number": numero,
        "track_total": int(info.get("trackCount") or len(faixas) or 0) if numero else 0,
        "album_artists": [a for a in artistas if a],
        "capa": url_capa(_maior_imagem(info.get("thumbnails"))),
    }


def _fonte_oficial(cliente: ClienteYTMusic | None, video_id: str, yt: Mapping[str, Any],
                   titulo_video: str) -> dict[str, Any] | None:
    """O que o próprio YouTube/YouTube Music diz da música; None se não diz nada."""
    faixa = cliente.faixa(video_id) if cliente and video_id else None
    if faixa and faixa["artists"] and faixa["tipo"] == "musica":
        return _de_faixa(faixa, FONTE_MUSICA)
    if faixa and faixa["artists"] and faixa["tipo"] == "clipe":
        par = faixa.get("contraparte")
        if par and par["tipo"] == "musica" and par["artists"]:
            return _de_faixa(par, FONTE_CLIPE)  # a faixa de áudio do clipe: título e álbum oficiais
        achado = _de_faixa(faixa, FONTE_CLIPE)
        achado["title"] = limpar_titulo(achado["title"])
        no_titulo = tx.artista_nas_partes(achado["title"], achado["artists"])
        if no_titulo:  # "Artista - Música (Official Video)" como título do clipe
            achado["title"] = no_titulo[2]
        return achado
    d = yt.get("youtube_music") or {}
    if d.get("title") and d.get("artists"):
        return {"title": d["title"], "artists": list(d["artists"]), "album": d.get("album") or "",
                "year": (d.get("date") or "")[:4], "fonte": FONTE_DESCRICAO}
    if yt.get("artists") and yt.get("fonte_artistas") == "youtube":
        titulo = yt.get("track") or ""
        if not titulo:  # crédito sem o nome da faixa: a música é o resto do título do vídeo
            no_titulo = tx.artista_nas_partes(limpar_titulo(titulo_video), yt["artists"])
            titulo = no_titulo[2] if no_titulo else limpar_titulo(titulo_video)
        return {"title": titulo, "artists": list(yt["artists"]), "album": yt.get("album") or "",
                "year": (yt.get("release_date") or "")[:4], "fonte": FONTE_CREDITOS}
    return None


def nomes_possiveis(titulo_video: str, canal: str, conhecidos: Sequence[str]) -> list[dict[str, Any]]:
    """Palpites de (artistas, título), do mais provável para o menos provável.

    - um artista do banco que aparece no título;
    - "Artista - Música" (o lado esquerdo) e, se for o contrário, "Música - Artista";
    - o canal, com o título inteiro como nome da música."""
    limpo = limpar_titulo(titulo_video)
    partes = tx.partes_do_titulo(limpo) or [limpo]
    palpites: list[dict[str, Any]] = []
    if conhecidos and (achou := tx.artista_nas_partes(limpo, conhecidos)):
        palpites.append({"artists": [achou[0]], "title": achou[2], "fonte": FONTE_TITULO})
    if len(partes) >= 2:
        palpites.append({"artists": tx.dividir_artistas(partes[0], conhecidos), "title": partes[1],
                         "fonte": FONTE_TITULO})
        palpites.append({"artists": tx.dividir_artistas(partes[-1], conhecidos), "title": partes[0],
                         "fonte": FONTE_TITULO})
    if canal:
        palpites.append({"artists": [canal], "title": limpo, "fonte": FONTE_CANAL})
    vistos, unicos = set(), []
    for p in palpites:
        p["artists"] = [a.lstrip("@").strip() for a in p["artists"] if a.lstrip("@").strip()]
        chave = tuple(tx._sem_espaco(a) for a in p["artists"])
        if p["artists"] and chave not in vistos:
            vistos.add(chave)
            unicos.append(p)
    return unicos


def identificar(
    meta: Mapping[str, Any],
    *,
    cliente: ClienteYTMusic | None = None,
    no_banco: Callable[[str], str | None] = lambda nome: None,
    conhecidos: Sequence[str] = (),
    dados_locais: Mapping[str, Any] | None = None,
    sem_dados_youtube: bool = False,
    forcar: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resultado no formato do banco: status, metadata, provenance, observations...

    `no_banco(nome)` devolve o nome certo do artista se ele está no banco de artistas.
    `forcar` = {"title": ..., "artists": [...]}: uma correção sua, que vale no lugar das fontes."""
    yt = tx.dados_youtube({} if sem_dados_youtube else meta)
    video_id = tx._text(meta.get("id"))
    local = dict(dados_locais or {})
    titulo_video = yt["video_title"] or tx._text(local.get("title")) or tx._text(meta.get("title"))
    canal = tx._nome_canal_limpo(yt["uploader"])
    curador = bool(tx._CANAL_CURADOR.search(yt["uploader"] or "") or yt.get("descricao_reupload"))
    consultar_web = bool(cliente) and not sem_dados_youtube
    observacoes: list[str] = []

    def confirmar(nome: str) -> tuple[str, str] | None:
        """(nome certo, fonte) se o nome está no banco ou é um artista no YouTube Music."""
        if (certo := no_banco(nome)):
            v("Identificação", f"'{nome}' está no banco de artistas" + (f" como '{certo}'" if certo != nome else ""))
            return certo, FONTE_BANCO
        if consultar_web and (certo := cliente.artista(nome)):
            v("Identificação", f"'{nome}' é um artista no YouTube Music")
            return certo, FONTE_WEB
        return None

    achado: dict[str, Any] | None = None
    if forcar and forcar.get("artists"):
        achado = {"title": tx._text(forcar.get("title")) or limpar_titulo(titulo_video),
                  "artists": list(forcar["artists"]), "album": yt["album"], "fonte": "manual"}
    if achado is None and not sem_dados_youtube:
        achado = _fonte_oficial(cliente, video_id, yt, titulo_video)
    if achado is None and yt["fonte_artistas"] == "cache" and yt["artists"]:
        achado = {"title": tx._text(meta.get("title")), "artists": list(yt["artists"]),
                  "album": yt["album"], "fonte": FONTE_LOCAL}
    if achado is None and sem_dados_youtube and local.get("title") and local.get("artists"):
        achado = {"title": tx._text(local["title"]), "artists": tx._artists(local["artists"]),
                  "album": tx._text(local.get("album")), "fonte": FONTE_LOCAL}

    if achado is not None:
        confirmado = achado["fonte"] in FONTES_OFICIAIS or achado["fonte"] == "manual"
        if not confirmado and achado["artists"] and (ok := confirmar(achado["artists"][0])):
            achado["artists"][0], achado["fonte"] = ok
            confirmado = True
    else:
        palpites = nomes_possiveis(titulo_video, canal, conhecidos)
        vv("Identificação", "nomes possíveis: " + ("; ".join(
            f"{' & '.join(p['artists'])} — {p['title']}" for p in palpites) or "nenhum"))
        confirmado = False
        for palpite in palpites:
            principal = palpite["artists"][0]
            if palpite["fonte"] == FONTE_CANAL and curador:
                if (certo := no_banco(principal)):  # canal de re-upload só vale se já está no banco
                    palpite["artists"][0], palpite["fonte"] = certo, FONTE_BANCO
                    achado, confirmado = palpite, True
                    break
                continue
            if (ok := confirmar(principal)):
                palpite["artists"][0], palpite["fonte"] = ok
                achado, confirmado = palpite, True
                break
        if achado is None:
            achado = palpites[0] if palpites else {"title": limpar_titulo(titulo_video), "artists": [],
                                                   "fonte": FONTE_TITULO}
            nomes = ", ".join(f"'{p['artists'][0]}'" for p in palpites) or "nenhum"
            observacoes.append(f"artista não achado no banco nem no YouTube Music (nomes possíveis: {nomes})")

    # Créditos/descrição sem álbum: procura a mesma música no catálogo só para o álbum e a capa.
    if achado["fonte"] in (FONTE_CREDITOS, FONTE_DESCRICAO) and not achado.get("album_id") and consultar_web:
        consulta = f"{' '.join(achado['artists'][:2])} {achado['title']}"
        for c in cliente.buscar(consulta, limite=5):
            if tx._similarity(c["title"], achado["title"]) >= 0.9 and versoes(c["title"]) == versoes(achado["title"]) \
                    and any(tx._mesmo_nome(a, b) for a in c["artists"] for b in achado["artists"]):
                achado.update({k: c[k] for k in ("album_id", "capa", "video_id") if c.get(k)})
                achado["album"] = achado.get("album") or c["album"]
                achado["year"] = achado.get("year") or c["year"]
                break

    fonte = achado["fonte"]
    oficial = fonte in FONTES_OFICIAIS
    titulo, feat = (achado["title"], []) if oficial else _feat_do_titulo(tx._text(achado["title"]))
    artistas = tx._limpar_nomes([no_banco(a) or a for a in list(achado["artists"]) + feat])
    if not oficial:
        titulo = _com_versao(titulo, titulo_video)
    album = _detalhes_do_album(cliente, achado) if achado.get("album_id") else {}
    capa = album.get("capa") or achado.get("capa") or ""

    metadata = {
        "id": video_id, "title": titulo, "artists": artistas, "artist": (artistas or [""])[0],
        "album": album.get("album") or tx._text(achado.get("album")),
        "album_artists": album.get("album_artists") or [],
        "date": album.get("year") or tx._text(achado.get("year")) or yt["release_date"],
        "track_number": album.get("track_number") or 0, "track_total": album.get("track_total") or 0,
        "disc_number": 0, "disc_total": 0, "label": tx._text(yt["youtube_music"].get("label")),
        "isrc": yt["isrc"] or tx.normalize_isrc(local.get("isrc")),
        "duration": float(yt["duration"] or 0), "uploader": yt["uploader"], "channel_id": yt["channel_id"],
        "thumbnail_url": yt["thumbnail_url"], "thumbnails": yt["thumbnails"],
        "cover_url": capa, "cover_file": "",
        "cover_source": "youtube_music" if capa else ("youtube_thumbnail" if yt["thumbnail_url"] else ""),
        "ytmusic_album_id": achado.get("album_id") or "",
        "variant": " + ".join(sorted(versoes(titulo_video))),
    }
    if not artistas:
        observacoes.append("nenhum artista identificado")
    status = CONFIRMADO if confirmado and artistas else NAO_CONFIRMADO
    provenance = {"title": fonte, "artists": fonte, "uploader": "youtube"}
    if metadata["album"]:
        provenance["album"] = fonte
    if capa:
        provenance["cover_url"] = "youtube_music"
    certeza = "confirmada" if status == CONFIRMADO else "provisoria"

    v("Identificação", f"{status}: {' & '.join(artistas) or '?'} — {titulo or '?'}"
                       f"{' | álbum ' + metadata['album'] if metadata['album'] else ''} ({DESCRICAO_FONTE.get(fonte, fonte)})")
    return {
        "video_id": video_id,
        "status": status,
        "metadata": metadata,
        "provenance": provenance,
        "confidence": {"title": certeza, "artists": certeza},
        "pending_reasons": [],
        "candidates": [],
        "diagnostics": [f"artista: {DESCRICAO_FONTE.get(fonte, fonte)}"] + observacoes,
        "diagnostics_by_source": {},
        "source_youtube": yt,
        "source_local": {k: local.get(k) for k in ("title", "artists", "album", "isrc") if local.get(k)},
        "observations": observacoes,
        "policy": {"modelo": "simples", "fonte": fonte, "canal": {"nome": canal, "curador": curador}},
    }
