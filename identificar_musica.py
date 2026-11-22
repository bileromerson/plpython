"""Identifica o artista e o título de cada vídeo.

Ordem (para no primeiro que responder):

1. O código do vídeo: se o YouTube/YouTube Music já diz quem é o artista (descrição
   "Provided to YouTube by", créditos "Música neste vídeo", faixa oficial ou clipe
   oficial no YouTube Music), vale isso.
2. Senão, junta os nomes possíveis (pedaços do título e o canal) e compara com as
   pastas de artista que já existem em songs/ e com o banco de artistas. Os dois são
   lidos uma vez no começo e ficam na memória: nenhuma consulta ao servidor nem ao SQL.
   Uma pasta com nome de música (songs/Believer/Believer [id].mp3) ou só com faixas
   não confirmadas não conta.
3. Senão, busca no YouTube Music com o título: vale o resultado em que o nome da
   música E o artista batem com um dos palpites (um nome de música nunca vira artista).
4. Senão, compara a mesma busca com o vídeo: mesmo título e mesma duração (até 2 s).
5. Nada disso: NAO_CONFIRMADO, com o palpite "Artista - Música" (sem isso, o canal).
   A música é baixada do mesmo jeito, na pasta do palpite.

A biblioteca ytmusicapi faz as consultas ao YouTube Music (sem login). Sem ela,
valem os passos 1 (só os dados do vídeo), 2 e 5.
"""

from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable, Mapping, Sequence

import textos as tx
from verbose import v, vv

CONFIRMADO = "CONFIRMADO"
NAO_CONFIRMADO = "NAO_CONFIRMADO"

# De onde veio o artista (gravado em provenance).
FONTE_MUSICA = "youtube_music"                # o vídeo é a faixa oficial no YouTube Music
FONTE_DESCRICAO = "youtube_music_descricao"   # descrição "Provided to YouTube by"
FONTE_CLIPE = "youtube_music_clipe"           # clipe oficial no YouTube Music
FONTE_CREDITOS = "youtube_creditos"           # "Música neste vídeo" do YouTube
FONTE_PASTA = "pasta_artista"                 # nome possível que já tem pasta em songs/
FONTE_BANCO = "banco_artistas"                # nome possível achado no banco de artistas
FONTE_BUSCA = "youtube_music_busca"           # busca: música e artista batem com o palpite
FONTE_COMPARACAO = "youtube_music_duracao"    # busca: mesmo título e mesma duração do vídeo
FONTE_WEB = "youtube_music_artista"           # (antigo) nome achado como artista no YouTube Music
FONTE_MANUAL = "manual"                       # correção sua
FONTE_LOCAL = "local"                         # tags do MP3 / identificação anterior
FONTE_TITULO = "titulo"                       # palpite: "Artista - Música"
FONTE_TITULO_INVERTIDO = "titulo_invertido"   # palpite: "Música - Artista"
FONTE_CANAL = "canal"                         # palpite: nome do canal

FONTES_OFICIAIS = {FONTE_MUSICA, FONTE_DESCRICAO, FONTE_CLIPE, FONTE_CREDITOS}
FONTES_CATALOGO = FONTES_OFICIAIS | {FONTE_BUSCA, FONTE_COMPARACAO}  # título/artistas oficiais
FONTES_CONFIAVEIS = FONTES_CATALOGO | {FONTE_PASTA, FONTE_BANCO, FONTE_WEB}
DESCRICAO_FONTE = {
    FONTE_MUSICA: "faixa oficial no YouTube Music", FONTE_DESCRICAO: "descrição do YouTube Music",
    FONTE_CLIPE: "clipe oficial no YouTube Music", FONTE_CREDITOS: "créditos de música do YouTube",
    FONTE_PASTA: "pasta de artista em songs/", FONTE_BANCO: "banco de artistas",
    FONTE_BUSCA: "música e artista no YouTube Music", FONTE_COMPARACAO: "YouTube Music × YouTube (título e duração)",
    FONTE_WEB: "artista no YouTube Music", FONTE_MANUAL: "correção manual",
    FONTE_LOCAL: "tags do MP3", FONTE_TITULO: "título do vídeo", FONTE_TITULO_INVERTIDO: "título do vídeo (invertido)",
    FONTE_CANAL: "nome do canal",
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


# --------------------------------------------------------------- artistas que já existem


def _chave(nome: Any) -> str:
    """Nome comparável: sem acento, caixa, pontuação nem espaço ("AC/DC" = "AC_DC" = "acdc")."""
    return tx._sem_espaco(str(nome or ""))


def _titulo_do_arquivo(arquivo: str) -> str:
    """"Believer [7wtfhZwyrcc].mp3" -> "Believer"."""
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", os.path.splitext(arquivo)[0]).strip()


class ArtistasConhecidos:
    """Artistas que já existem na sua biblioteca, lidos uma vez e guardados na memória:

    - as pastas de `songs/` (uma pasta por artista);
    - o banco de artistas (nome certo e variações), lido de uma vez só no começo.

    Assim cada música é conferida sem consultar o SQL nem o servidor.

    Uma pasta NÃO conta como artista quando:
    - tem o nome de uma música que está dentro dela (`songs/Believer/Believer [id].mp3`):
      é a confusão antiga de título no lugar do artista;
    - só tem faixas NAO_CONFIRMADO (`duvidosos`): foi criada por um palpite que ninguém conferiu.
    """

    PASTAS_DO_SISTEMA = {"_pendentes", "_observar", "_library", "artista desconhecido"}

    def __init__(self, pasta_musicas: str = "", banco: Mapping[str, str] | None = None,
                 duvidosos: Iterable[str] = ()):
        self.banco: dict[str, str] = {}
        for k, nome in (banco or {}).items():
            if _chave(k) and nome:
                self.banco[_chave(k)] = nome
        self.pastas: dict[str, str] = {}
        self.ignoradas: dict[str, str] = {}
        if pasta_musicas:
            self._ler_pastas(pasta_musicas, {_chave(d) for d in duvidosos if _chave(d)})

    def _ler_pastas(self, pasta_musicas: str, duvidosos: set[str]) -> None:
        try:
            entradas = [e for e in os.scandir(pasta_musicas) if e.is_dir()]
        except OSError:
            return
        for entrada in entradas:
            nome = entrada.name
            k = _chave(nome)
            if not k or nome.casefold() in self.PASTAS_DO_SISTEMA or nome.startswith("_"):
                continue
            if k in self.banco:  # o banco já diz que é artista
                self.pastas[k] = nome
                continue
            try:
                titulos = {_chave(tx._sem_parenteses(_titulo_do_arquivo(a.name)))
                           for a in os.scandir(entrada.path) if a.name.lower().endswith(".mp3")}
            except OSError:
                titulos = set()
            if k in titulos:
                self.ignoradas[nome] = "nome de música"
            elif k in duvidosos:
                self.ignoradas[nome] = "só faixas não confirmadas"
            else:
                self.pastas[k] = nome
        if self.ignoradas:
            v("Identificação", f"{len(self.ignoradas)} pasta(s) não contam como artista: " + ", ".join(
                f"'{n}' ({m})" for n, m in list(self.ignoradas.items())[:8]))
        v("Identificação", f"{len(self.pastas)} pasta(s) de artista em {pasta_musicas}")

    def achar(self, nome: Any) -> tuple[str, str] | None:
        """(nome certo, fonte) se o artista já existe no banco ou numa pasta; senão None."""
        k = _chave(nome)
        if not k:
            return None
        if k in self.banco:
            return self.banco[k], FONTE_BANCO
        if k in self.pastas:
            pasta = self.pastas[k]
            # a pasta troca / : ? etc. por "_": nesse caso o nome como veio é a grafia certa
            return (str(nome).strip() if "_" in pasta else pasta), FONTE_PASTA
        return None

    def canonico(self, nome: Any) -> str | None:
        achado = self.achar(nome)
        return achado[0] if achado else None

    def nomes(self) -> list[str]:
        """Para procurar o artista dentro do título do vídeo."""
        return list(dict.fromkeys(list(self.banco.values()) + [
            p for p in self.pastas.values() if "_" not in p]))

    def aprender(self, nome: Any) -> None:
        """Um artista confirmado agora vale para as próximas músicas desta execução."""
        k = _chave(nome)
        if k and k not in self.banco:
            self.banco[k] = str(nome).strip()


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
    """Passo 1, o código do vídeo: o que o próprio YouTube/YouTube Music diz da música.

    Primeiro o que já veio com o vídeo (descrição "Provided to YouTube by", créditos
    "Música neste vídeo"), que não custa consulta; depois o vídeo no YouTube Music."""
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
    return None


def nomes_possiveis(titulo_video: str, canal: str, conhecidos: Sequence[str]) -> list[dict[str, Any]]:
    """Palpites de (artistas, título), do mais provável para o menos provável.

    - um artista conhecido (banco/pasta) que aparece no título;
    - "Artista - Música" (o lado esquerdo) e, se for o contrário, "Música - Artista";
    - o canal, com o título inteiro como nome da música.
    Palpite em que o "artista" e a "música" são o mesmo texto é descartado."""
    limpo = limpar_titulo(titulo_video)
    partes = tx.partes_do_titulo(limpo) or [limpo]
    palpites: list[dict[str, Any]] = []
    if conhecidos and (achou := tx.artista_nas_partes(limpo, conhecidos)):
        palpites.append({"artists": [achou[0]], "title": achou[2], "fonte": FONTE_TITULO})
    if len(partes) >= 2:
        palpites.append({"artists": tx.dividir_artistas(partes[0], conhecidos), "title": partes[1],
                         "fonte": FONTE_TITULO})
        palpites.append({"artists": tx.dividir_artistas(partes[-1], conhecidos), "title": partes[0],
                         "fonte": FONTE_TITULO_INVERTIDO})
    if canal:
        palpites.append({"artists": [canal], "title": limpo, "fonte": FONTE_CANAL})
    vistos, unicos = set(), []
    for p in palpites:
        p["artists"] = [a.lstrip("@").strip() for a in p["artists"] if a.lstrip("@").strip()]
        if not p["artists"] or not _chave(p["title"]) or _chave(p["artists"][0]) == _chave(p["title"]):
            continue
        chave = (tuple(_chave(a) for a in p["artists"]), _chave(p["title"]))
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(p)
    return unicos


def _titulo_base(titulo: Any) -> str:
    """Título sem "(feat. ...)" e sem parênteses, para comparar."""
    return _chave(tx._sem_parenteses(_feat_do_titulo(tx._text(titulo))[0]))


def _titulo_bate(catalogo: str, esperado: str) -> bool:
    a, b = _titulo_base(catalogo), _titulo_base(esperado)
    return bool(a and b) and (a == b or tx._similarity(a, b) >= 0.9)


def _busca_artista_titulo(resultados: Sequence[Mapping[str, Any]], palpites: Sequence[Mapping[str, Any]],
                          titulo_video: str) -> dict[str, Any] | None:
    """Passo 3: um resultado do YouTube Music cujo título é a música do palpite E cujo artista
    é o artista do palpite. Os dois têm que bater: um nome de música nunca vira artista."""
    versao = versoes(titulo_video)
    for palpite in palpites:
        principal = palpite["artists"][0]
        for c in resultados:
            if not c.get("artists") or versoes(c["title"]) != versao:
                continue
            if _titulo_bate(c["title"], palpite["title"]) and any(tx._mesmo_nome(a, principal) for a in c["artists"]):
                v("Identificação", f"YouTube Music: '{' & '.join(c['artists'])} — {c['title']}' "
                                   f"bate com o palpite '{principal} — {palpite['title']}'")
                return dict(c)
    return None


def _comparar_com_youtube(resultados: Sequence[Mapping[str, Any]], titulo_video: str,
                          duracao: float) -> dict[str, Any] | None:
    """Passo 4: sem artista que bata, compara os dados do YouTube Music com os do vídeo:
    o nome da música tem que ser o título do vídeo (ou um pedaço dele), a versão igual
    e a duração a mesma (até 2 s de diferença)."""
    if not duracao:
        return None
    limpo = limpar_titulo(titulo_video)
    pedacos = [limpo] + (tx.partes_do_titulo(limpo) or [])
    versao = versoes(titulo_video)
    for c in resultados:
        if not c.get("artists") or not c.get("duration") or abs(c["duration"] - duracao) > 2:
            continue
        if versoes(c["title"]) == versao and any(_titulo_bate(c["title"], p) for p in pedacos):
            v("Identificação", f"YouTube Music × YouTube: '{' & '.join(c['artists'])} — {c['title']}' "
                               f"tem o mesmo título e a mesma duração ({c['duration']}s)")
            return dict(c)
    return None


def identificar(
    meta: Mapping[str, Any],
    *,
    cliente: ClienteYTMusic | None = None,
    artistas: ArtistasConhecidos | None = None,
    no_banco: Callable[[str], str | None] | None = None,
    conhecidos: Sequence[str] = (),
    dados_locais: Mapping[str, Any] | None = None,
    sem_dados_youtube: bool = False,
    forcar: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resultado no formato do banco: status, metadata, provenance, observations...

    Ordem (para no primeiro que responder):
    1. o código do vídeo: o YouTube/YouTube Music diz quem é (faixa oficial, clipe,
       descrição, créditos);
    2. os nomes possíveis do título/canal comparados com as pastas de artista que já
       existem e com o banco de artistas (na memória: sem servidor e sem SQL);
    3. busca no YouTube Music com o título: vale o resultado em que a música E o artista
       batem com um palpite;
    4. a mesma busca comparada com o vídeo: mesmo título e mesma duração;
    5. nada disso: NAO_CONFIRMADO, com o palpite "Artista - Música" (ou o canal).

    `artistas`: pastas + banco de artistas na memória. Sem ele, `no_banco(nome)` e
    `conhecidos` fazem o papel do banco (como antes).
    `forcar` = {"title": ..., "artists": [...]}: uma correção sua, que vale no lugar das fontes."""
    if artistas is None:
        artistas = ArtistasConhecidos()
        for nome in conhecidos:
            artistas.banco.setdefault(_chave(nome), (no_banco(nome) if no_banco else None) or nome)
    if no_banco is not None:
        _achar = artistas.achar

        def achar(nome: Any) -> tuple[str, str] | None:
            return _achar(nome) or ((certo, FONTE_BANCO) if (certo := no_banco(str(nome))) else None)
    else:
        achar = artistas.achar

    yt = tx.dados_youtube({} if sem_dados_youtube else meta)
    video_id = tx._text(meta.get("id"))
    local = dict(dados_locais or {})
    titulo_video = yt["video_title"] or tx._text(local.get("title")) or tx._text(meta.get("title"))
    canal = tx._nome_canal_limpo(yt["uploader"])
    curador = bool(tx._CANAL_CURADOR.search(yt["uploader"] or "") or yt.get("descricao_reupload"))
    consultar_web = bool(cliente) and not sem_dados_youtube
    observacoes: list[str] = []
    nomes_conhecidos = artistas.nomes() or list(conhecidos)

    # 1. código do vídeo / correção sua
    achado: dict[str, Any] | None = None
    if forcar and forcar.get("artists"):
        achado = {"title": tx._text(forcar.get("title")) or limpar_titulo(titulo_video),
                  "artists": list(forcar["artists"]), "album": yt["album"], "fonte": FONTE_MANUAL}
    if achado is None and not sem_dados_youtube:
        achado = _fonte_oficial(cliente, video_id, yt, titulo_video)
    confirmado = achado is not None

    if achado is None:
        palpites: list[dict[str, Any]] = []
        if yt["fonte_artistas"] == "cache" and yt["artists"]:
            palpites.append({"title": limpar_titulo(meta.get("title")) or limpar_titulo(titulo_video),
                             "artists": list(yt["artists"]), "album": yt["album"], "fonte": FONTE_LOCAL})
        if sem_dados_youtube and local.get("title") and local.get("artists"):
            palpites.append({"title": tx._text(local["title"]), "artists": tx._artists(local["artists"]),
                             "album": tx._text(local.get("album")), "fonte": FONTE_LOCAL})
        if not sem_dados_youtube or not palpites:
            palpites += nomes_possiveis(titulo_video, canal, nomes_conhecidos)
        vv("Identificação", "nomes possíveis: " + ("; ".join(
            f"{' & '.join(p['artists'])} — {p['title']}" for p in palpites) or "nenhum"))

        # 2. pastas e banco de artistas (sem servidor)
        for palpite in palpites:
            principal = palpite["artists"][0]
            ok = achar(principal)
            if not ok:
                continue
            if palpite["fonte"] == FONTE_TITULO_INVERTIDO and achar(palpite["title"]):
                continue  # "A - B" com A e B conhecidos: fica com o lado esquerdo, não inverte
            v("Identificação", f"'{principal}' já existe ({DESCRICAO_FONTE[ok[1]]}" +
              (f" como '{ok[0]}'" if ok[0] != principal else "") + ")")
            achado = dict(palpite)
            achado["artists"] = [ok[0]] + list(palpite["artists"][1:])
            achado["fonte"], confirmado = ok[1], True
            break

        # 3 e 4. YouTube Music: artista + título; senão título + duração
        if achado is None and consultar_web and palpites:
            validos = [p for p in palpites if not (p["fonte"] == FONTE_CANAL and curador)]
            limpo = limpar_titulo(titulo_video)
            consultas = [limpo]
            for p in validos:  # artista que não está no título (o canal, por exemplo) vai junto na busca
                if _chave(p["artists"][0]) not in _chave(limpo):
                    consultas.append(f"{p['artists'][0]} {p['title']}")
            resultados: list[dict[str, Any]] = []
            for consulta in list(dict.fromkeys(consultas))[:2]:
                resultados += cliente.buscar(consulta, limite=10)
                if (c := _busca_artista_titulo(resultados, validos, titulo_video)):
                    achado, confirmado = _de_faixa(c, FONTE_BUSCA), True
                    break
            if achado is None and (c := _comparar_com_youtube(resultados, titulo_video, yt["duration"])):
                achado, confirmado = _de_faixa(c, FONTE_COMPARACAO), True

        if achado is None:
            sem_curador = [p for p in palpites if not (p["fonte"] == FONTE_CANAL and curador)]
            achado = dict((sem_curador or palpites or [{"title": limpar_titulo(titulo_video), "artists": [],
                                                        "fonte": FONTE_TITULO}])[0])
            if achado["fonte"] == FONTE_TITULO_INVERTIDO:
                achado["fonte"] = FONTE_TITULO
            nomes = ", ".join(f"'{p['artists'][0]}'" for p in palpites) or "nenhum"
            observacoes.append("artista não achado nas pastas, no banco nem no YouTube Music "
                               f"(nomes possíveis: {nomes})")

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
    catalogo = fonte in FONTES_CATALOGO
    titulo, feat = (achado["title"], []) if catalogo else _feat_do_titulo(tx._text(achado["title"]))
    nomes_finais = [artistas.canonico(a) or (no_banco(a) if no_banco else None) or a
                     for a in list(achado["artists"]) + feat]
    lista_artistas = tx._limpar_nomes(nomes_finais)
    if not catalogo:
        titulo = _com_versao(titulo, titulo_video)
    album = _detalhes_do_album(cliente, achado) if achado.get("album_id") else {}
    capa = album.get("capa") or achado.get("capa") or ""

    metadata = {
        "id": video_id, "title": titulo, "artists": lista_artistas, "artist": (lista_artistas or [""])[0],
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
    if not lista_artistas:
        observacoes.append("nenhum artista identificado")
    status = CONFIRMADO if confirmado and lista_artistas else NAO_CONFIRMADO
    provenance = {"title": fonte, "artists": fonte, "uploader": "youtube"}
    if metadata["album"]:
        provenance["album"] = fonte
    if capa:
        provenance["cover_url"] = "youtube_music"
    certeza = "confirmada" if status == CONFIRMADO else "provisoria"

    v("Identificação", f"{status}: {' & '.join(lista_artistas) or '?'} — {titulo or '?'}"
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
