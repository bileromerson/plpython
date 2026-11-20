"""Identifica a música de cada vídeo usando o YouTube Music.

A ideia é confiar em quem publica a música e só desconfiar quando houver um sinal
forte de erro (em vez de tentar confirmar cada artista em vários lugares).

Ordem das fontes (para na primeira que responder):

1. o próprio vídeo no YouTube Music: se ele é a faixa de áudio oficial (canal
   "- Topic") ou um clipe oficial, título, artistas, álbum e ano vêm de lá;
2. a descrição "Provided to YouTube by ..." do vídeo (é a mesma faixa oficial);
3. os créditos do YouTube ("Música neste vídeo", que o yt-dlp lê como track/artist);
4. uma busca de músicas no YouTube Music com o título do vídeo: vale o resultado
   cujo título está no título do vídeo, cuja versão bate (slowed, remix, ao vivo...)
   e cujo artista aparece no título/canal ou cuja duração é a mesma;
5. sem nada disso, o título e o canal do vídeo ("Artista - Música", o canal como
   artista), como nas primeiras versões do plpython.

Depois, `sinais_de_erro` procura só o que tem grande chance de estar errado
(artista com cara de título, artista que é canal de re-upload, sobras no título,
título e artista trocados, vídeo longo demais). Só isso manda a faixa para a
conferência. Álbum ou capa que faltam não são erro.

A biblioteca ytmusicapi faz as consultas ao YouTube Music (sem login). Sem ela,
ficam as fontes 2, 3 e 5.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Sequence

import textos as tx
from verbose import v, vv

# De onde veio o artista/título (gravado em provenance).
FONTE_MUSICA = "youtube_music"                # o vídeo é a faixa oficial no YouTube Music
FONTE_DESCRICAO = "youtube_music_descricao"   # descrição "Provided to YouTube by"
FONTE_CLIPE = "youtube_music_clipe"           # clipe oficial no YouTube Music
FONTE_CREDITOS = "youtube_creditos"           # "Música neste vídeo" do YouTube
FONTE_BUSCA = "youtube_music_busca"           # achado no catálogo do YouTube Music
FONTE_LOCAL = "local"                         # tags do MP3 (sem dados do YouTube)
FONTE_TITULO = "titulo"                       # deduzido do título do vídeo
FONTE_CANAL = "canal"                         # deduzido do nome do canal

FONTES_CONFIAVEIS = {FONTE_MUSICA, FONTE_DESCRICAO, FONTE_CLIPE, FONTE_CREDITOS, FONTE_BUSCA}
CATALOGO = {FONTE_MUSICA, FONTE_DESCRICAO, FONTE_BUSCA}  # dados de gravadora/distribuidora
CONFIANCA = {
    FONTE_MUSICA: 0.98, FONTE_DESCRICAO: 0.97, FONTE_CLIPE: 0.95, FONTE_CREDITOS: 0.95,
    FONTE_BUSCA: 0.9, FONTE_LOCAL: 0.7, FONTE_TITULO: 0.65, FONTE_CANAL: 0.6,
}
DESCRICAO_FONTE = {
    FONTE_MUSICA: "faixa oficial no YouTube Music", FONTE_DESCRICAO: "descrição do YouTube Music",
    FONTE_CLIPE: "clipe oficial no YouTube Music", FONTE_CREDITOS: "créditos de música do YouTube",
    FONTE_BUSCA: "catálogo do YouTube Music", FONTE_LOCAL: "tags do MP3",
    FONTE_TITULO: "título do vídeo", FONTE_CANAL: "nome do canal",
}
REVISOES = ("errors", "all", "none")  # o que vai para conferência

VIDEO_LONGO = 15 * 60  # segundos: mais que isso costuma ser compilação, álbum inteiro ou loop
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

    `api` pode ser injetada nos testes (um objeto com get_watch_playlist, search e get_album).
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
_SOBRAS = re.compile(
    r"\b(?:lyrics?|letra|legendad[oa]|tradu[cç][aã]o|official\s+(?:music\s+)?(?:video|audio)|"
    r"v[ií]deo\s+oficial|clipe\s+oficial|[aá]udio\s+oficial|official\s+visuali[sz]er|full\s+album|"
    r"[aá]lbum\s+completo|1\s*hour|1\s*hora|hd|4k|hq)\b|(?<=\s)#[^\W\d]|\bprovided to youtube\b"
    r"|[\[(][^\])]*\brelease\b[^\])]*[\])]",
    re.I,
)


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


def _contem(texto: str, nome: str) -> bool:
    """O nome aparece inteiro no texto (palavra por palavra, ou colado: "ImagineDragons")."""
    a, b = tx._normalize_text(texto), tx._normalize_text(nome)
    if not a or not b:
        return False
    if f" {b} " in f" {a} ":
        return True
    junto = b.replace(" ", "")
    return len(junto) >= 5 and junto in a.replace(" ", "")


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


# --------------------------------------------------------------- busca no catálogo

def _titulo_bate(candidato: str, video_limpo: str, partes: Sequence[str]) -> float:
    """0 a 1: quanto o título da música do catálogo bate com o título do vídeo."""
    nome = tx._sem_parenteses(candidato) or candidato
    if _contem(video_limpo, nome):
        return 1.0
    return max([tx._similarity(nome, tx._sem_parenteses(p) or p) for p in partes] or [0.0])


def _resto_do_titulo(video_limpo: str, titulo: str) -> str:
    """O que sobra no título do vídeo tirando o nome da música e as marcas de versão."""
    resto = f" {tx._normalize_text(video_limpo)} "
    resto = resto.replace(f" {tx._normalize_text(tx._sem_parenteses(titulo) or titulo)} ", " ")
    for _, padrao in _VERSOES:
        resto = re.sub(padrao, " ", resto, flags=re.I)
    return " ".join(resto.split())


def _sobra_explicada(resto: str, candidato: Mapping[str, Any]) -> bool:
    if not resto:
        return True
    catalogo = set(tx._normalize_text(f"{candidato['title']} {candidato.get('album') or ''}").split())
    return set(resto.split()) <= catalogo


def escolher_no_catalogo(candidatos: Sequence[Mapping[str, Any]], titulo_video: str, canal: str,
                         duracao: float) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """A música do catálogo que corresponde ao vídeo, e os quase (para sugerir no review)."""
    limpo = limpar_titulo(titulo_video)
    partes = tx.partes_do_titulo(limpo) or [limpo]
    versao_video = versoes(titulo_video)
    aceitos: list[tuple[float, dict[str, Any]]] = []
    quase: list[dict[str, Any]] = []
    for posicao, c in enumerate(candidatos[:10]):
        nota_titulo = _titulo_bate(c["title"], limpo, partes)
        if nota_titulo < 0.85:
            continue
        if versoes(c["title"]) != versao_video:  # "Heat Waves" não é "Heat Waves (Slowed)"
            continue
        artista_ok = any(_contem(limpo, a) or (canal and tx._mesmo_nome(a, canal)) for a in c["artists"])
        diferenca = abs(duracao - c["duration"]) if duracao and c["duration"] else None
        if artista_ok and (diferenca is None or diferenca <= 15):
            nota = 2 + nota_titulo + (1 - min(diferenca or 0, 15) / 15)
        elif (not artista_ok and diferenca is not None and diferenca <= max(3, duracao * 0.02)
              and posicao < 3 and _sobra_explicada(_resto_do_titulo(limpo, c["title"]), c)):
            # o vídeo só tem o nome da música ("Thunderstruck") ou o resto está no próprio
            # catálogo ("Bella Ciao - La Casa de Papel" e o álbum da série): vale a mesma duração
            nota = 1 + nota_titulo
        else:
            quase.append(c)
            continue
        aceitos.append((nota - posicao * 0.01, dict(c)))
    if not aceitos:
        return None, quase[:3]
    aceitos.sort(key=lambda item: item[0], reverse=True)
    return aceitos[0][1], []


def buscar_no_catalogo(cliente: ClienteYTMusic, titulo_video: str, canal: str, canal_tipo: str,
                       duracao: float) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    limpo = limpar_titulo(titulo_video)
    partes = tx.partes_do_titulo(limpo) or [limpo]
    consultas = []
    if len(partes) == 1 and canal and canal_tipo != "curador" and not _contem(limpo, canal):
        consultas.append(f"{canal} {limpo}")  # "Thunderstruck" no canal AC/DC
    consultas.append(" ".join(partes))
    quase: list[dict[str, Any]] = []
    for consulta in dict.fromkeys(consultas):
        candidatos = cliente.buscar(consulta)
        vv("YouTube Music", f"busca '{consulta}': " + "; ".join(
            f"{' & '.join(c['artists'])} — {c['title']} ({c['duration']}s)" for c in candidatos[:5]) or "nada")
        achado, perto = escolher_no_catalogo(candidatos, titulo_video, canal, duracao)
        if achado:
            return achado, []
        quase += perto
    return None, quase[:3]


# --------------------------------------------------------------- sem fonte confiável

def _canal_tipo(yt: Mapping[str, Any], canal_conhecido: Mapping[str, Any] | None) -> str:
    bruto = tx._text(yt.get("uploader"))
    if canal_conhecido and canal_conhecido.get("kind") in ("artista", "curador"):
        return str(canal_conhecido["kind"])
    if re.search(r"(?:-\s*topic|vevo)\s*$", bruto, re.I):
        return "artista"
    if tx._CANAL_CURADOR.search(bruto) or yt.get("descricao_reupload"):
        return "curador"
    return "?"


_FEAT_NO_MEIO = re.compile(r"\b(?:feat(?:uring)?\.?|ft\.?)\s+((?:(?!\s*//)[^-–—|()\[\]])+)", re.I)
_FEAT_ENTRE_PARENTESES = re.compile(r"\s*[(\[]\s*(?:feat(?:uring)?\.?|ft\.?)\s+[^)\]]+[)\]]", re.I)
_SEGMENTOS = re.compile(r"\s+[|｜]\s+|\s*//\s*")
_ENTRE_ASPAS = re.compile(r"[\"“]([^\"“”]+)[\"”](?:\s*(?:remix\s+)?(?:by|por|de)\s+([^|()\[\]]+))?", re.I)


def _nomes(texto: str, conhecidos: Sequence[str]) -> list[str]:
    """Nomes de uma lista de participações: "A, B and C" -> A, B, C; "@A" -> A."""
    texto = re.sub(r"\s*,?\s+(?:and|e)\s+", ", ", texto)
    return [n.lstrip("@").strip() for n in tx.dividir_artistas(texto, conhecidos) if n.lstrip("@").strip()]


def deduzir(titulo_video: str, canal: str, canal_tipo: str, conhecidos: Sequence[str],
            canal_conhecido: Mapping[str, Any] | None) -> dict[str, Any]:
    """Como nas primeiras versões: "Artista - Música", "Música" by Artista ou o canal.

    O título é lido em blocos separados por "|" ou "//" (o primeiro bloco que não é
    trilha/obra, nem o próprio canal, é o principal) e, dentro do bloco, pelos "-"."""
    limpo = limpar_titulo(titulo_video)
    participacoes = [n for m in _FEAT_NO_MEIO.finditer(limpo) for n in _nomes(m.group(1), conhecidos)]
    grafias = {tx._sem_espaco(c): c for c in conhecidos if c}
    grafia = lambda nome: grafias.get(tx._sem_espaco(nome), nome)  # noqa: E731 - "Ivycomb" -> "ivycomb" do banco
    candidatos = list((canal_conhecido or {}).get("artists") or [])
    if canal and canal_tipo != "curador":
        candidatos.append(canal)
    candidatos += list(grafias.values())

    def resultado(titulo: str, artistas: list[str], detalhe: str, album: str = "", incerto: bool = False) -> dict:
        titulo = _FEAT_ENTRE_PARENTESES.sub("", titulo).strip()  # "(ft. X)" no meio: X já está nos artistas
        titulo, feat = _feat_do_titulo(titulo)
        artistas = tx._limpar_nomes(grafia(a.lstrip("@")) for a in artistas + feat + participacoes)
        return {"title": titulo, "artists": artistas, "album": album, "incerto": incerto,
                "fonte": FONTE_CANAL if detalhe == "canal como artista" else FONTE_TITULO, "detalhe": detalhe}

    def achar(texto: str) -> dict[str, Any] | None:
        achou = tx.artista_nas_partes(texto, candidatos)
        if not achou:
            return None
        nome, pedaco, musica = achou
        if tx._nome_inteiro(nome, pedaco):  # grafia do banco; senão a do título ("ImagineDragons" do canal perde)
            artistas = [grafias.get(tx._sem_espaco(nome)) or tx._sem_parenteses(pedaco) or nome]
        else:
            artistas = tx.dividir_artistas(pedaco, conhecidos)
        return resultado(musica, artistas, f"'{nome}' aparece no título")

    # '"Música" by Artista' ou só '"Música"' (o resto é o jogo/série: "Cult of the Lamb Song")
    if (aspas := _ENTRE_ASPAS.search(limpo)):
        if aspas.group(2):
            return resultado(aspas.group(1), _nomes(aspas.group(2), conhecidos), "música entre aspas")
        if (r := achar(limpo)):  # 'Earth Wind & Fire "September"'
            return {**r, "title": _feat_do_titulo(aspas.group(1))[0]}
        if canal:
            return resultado(aspas.group(1), [canal], "música entre aspas")
    # "Meme qualquer (Artista - Música)": o que está entre parênteses é a música
    for dentro in re.findall(r"[(\[]([^()\[\]]+?\s[-–—]\s[^()\[\]]+?)[)\]]", limpo):
        if (r := achar(dentro)):
            return r

    segmentos = [b.strip() for b in _SEGMENTOS.split(limpo) if b.strip()] or [limpo]
    eh_artista = lambda b: any(tx._nome_inteiro(c, b) for c in candidatos)  # noqa: E731
    principais = [b for b in segmentos if not tx.parece_obra(b) and not eh_artista(b)] or segmentos
    principal = principais[0]
    if (r := achar(principal)):
        return r
    partes = tx.partes_do_titulo(principal)
    # "Álbum - 02. Música": lista de faixas de um álbum; o artista é o canal
    for i, parte in enumerate(partes):
        if i and _NUMERO_FAIXA.match(parte) and canal:
            return resultado(_NUMERO_FAIXA.sub("", parte), [canal], "faixa de álbum no canal", album=partes[0])
    # "Música-Artista" sem espaços, quando um lado é o canal ou um artista conhecido
    if len(partes) == 1 and re.search(r"\w-\w", principal):
        if (r := achar(re.sub(r"(?<=\w)-(?=\w)", " - ", principal, count=1))):
            return r
    if len(partes) >= 2:
        # incerto: mais de duas partes, ou o canal num bloco à parte (selo que publica "Música - Artista")
        incerto = len(partes) > 2 or any(b is not principal and eh_artista(b) for b in segmentos)
        return resultado(partes[1], tx.dividir_artistas(partes[0], conhecidos), "formato \"Artista - Música\"",
                         incerto=incerto)
    principal = partes[0] if partes else principal  # "Overwhelmed - (Ryan Mack Remix)" vira uma parte só
    # Um bloco só com o nome da música: o artista está em outro bloco ou é o canal
    for bloco in segmentos:
        if bloco is not principal and (achou := tx.artista_nas_partes(bloco, candidatos)):
            return resultado(principal, [grafia(achou[0])], f"'{achou[0]}' em outro bloco do título")
    if canal:
        return resultado(principal, [canal], "canal como artista")
    return resultado(principal, [], "sem artista")


# --------------------------------------------------------------- sinais de erro

_ARTISTA_COM_CARA_DE_TITULO = re.compile(
    r"[()\[\]{}#]|\s[-–—|]\s|https?://|^\d{1,3}[.)]\s|(?:^|\s)@\w|\b(?:lyrics?|letra|official|oficial|v[ií]deo|"
    r"audio|[aá]udio|remix|soundtrack|ost|trilha|full\s+album|1\s*hour)\b",
    re.I,
)


def _contagem(conhecidos: Sequence[str] | Mapping[str, int]) -> dict[str, int]:
    """Nome → em quantas faixas aparece (uma lista conta cada repetição)."""
    contagem: dict[str, int] = {}
    itens = conhecidos.items() if isinstance(conhecidos, Mapping) else ((n, 1) for n in conhecidos)
    for nome, vezes in itens:
        chave = tx._sem_espaco(nome)
        if chave:
            contagem[chave] = contagem.get(chave, 0) + int(vezes)
    return contagem


def sinais_de_erro(metadata: Mapping[str, Any], fonte: str, *, titulo_video: str = "", canal: str = "",
                   canal_tipo: str = "?", duracao: float = 0, conhecidos: Sequence[str] | Mapping[str, int] = (),
                   canal_conhecido: Mapping[str, Any] | None = None) -> list[str]:
    """Só o que tem grande chance de estar errado. Dados do catálogo do YouTube Music
    (gravadora/distribuidora) não são questionados; os deduzidos do título/canal são.
    Cada frase começa pelo campo ("artista ...", "título ...") para o review saber o que perguntar."""
    sinais: list[str] = []
    titulo = tx._text(metadata.get("title"))
    artistas = [tx._text(a) for a in metadata.get("artists") or [] if tx._text(a)]
    catalogo = fonte in CATALOGO
    confiavel = fonte in FONTES_CONFIAVEIS or fonte in ("manual", "banco_isrc")

    for nome in artistas:  # no máximo um aviso de artista por faixa
        do_canal = canal and tx._mesmo_nome(nome, canal)
        if not confiavel and (
                (do_canal and (canal_tipo == "curador" or (canal_conhecido or {}).get("kind") == "curador"))
                or tx._CANAL_CURADOR.search(nome) or tx.parece_obra(nome)
                or re.search(r"(?:-\s*topic|vevo)\s*$", nome, re.I)):
            sinais.append(f"artista parece canal de re-upload, selo ou trilha: '{nome}'")
            break
        if not catalogo and (_ARTISTA_COM_CARA_DE_TITULO.search(nome) or len(nome) > 40 or len(nome.split()) > 6):
            sinais.append(f"artista parece pedaço do título do vídeo: '{nome}'")
            break
    if not catalogo and titulo and (_SOBRAS.search(titulo) or (
            not confiavel and re.search(r"[\"“”]|\sby\s|^\d{1,3}[.)]\s|\s[-–—]\s", titulo, re.I))):
        sinais.append(f"título com sobras do vídeo: '{titulo}'")
    elif not confiavel and titulo and any(
            len(tx._sem_espaco(a)) >= 4 and _contem(tx._sem_parenteses(titulo), a)
            and tx._sem_espaco(a) != tx._sem_espaco(titulo)
            for a in artistas):
        sinais.append(f"título contém o nome do artista: '{titulo}'")
    if not confiavel and titulo and artistas:
        # o título é um artista que você tem (em mais faixas que o "artista" desta)
        contagem = _contagem(conhecidos)
        vezes = lambda nome: contagem.get(tx._sem_espaco(nome), 0)  # noqa: E731
        if vezes(titulo) > max(vezes(a) for a in artistas):
            sinais.append(f"título e artista podem estar invertidos: '{titulo}' é um artista conhecido")
    if duracao > VIDEO_LONGO and fonte not in CATALOGO:
        sinais.append(f"vídeo longo ({int(duracao // 60)} min): pode ser compilação, álbum inteiro ou loop")
    return sinais


# --------------------------------------------------------------- identificação

def _de_faixa(faixa: Mapping[str, Any], fonte: str) -> dict[str, Any]:
    return {"title": faixa["title"], "artists": list(faixa["artists"]), "album": faixa.get("album") or "",
            "album_id": faixa.get("album_id") or "", "year": faixa.get("year") or "",
            "capa": faixa.get("capa") or "", "video_id": faixa.get("video_id") or "", "fonte": fonte,
            "duration": faixa.get("duration") or 0}


def _candidato(faixa: Mapping[str, Any], nota: float = 0.0) -> dict[str, Any]:
    """Formato que o review mostra como sugestão (":db")."""
    return {"title": faixa["title"], "artists": list(faixa["artists"]), "score": round(nota, 3),
            "releases": [{"title": faixa["album"]}] if faixa.get("album") else [], "isrcs": [],
            "fonte": "youtube_music", "video_id": faixa.get("video_id") or ""}


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


def identificar(
    meta: Mapping[str, Any],
    *,
    cliente: ClienteYTMusic | None = None,
    conhecidos: Sequence[str] = (),
    canal_conhecido: Mapping[str, Any] | None = None,
    dados_locais: Mapping[str, Any] | None = None,
    sem_dados_youtube: bool = False,
    revisar: str = "errors",
    forcar: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resultado no formato do banco: status, metadata, provenance, confidence, observations...

    `forcar` = {"title": ..., "artists": [...]}: uma correção sua (review --apply) que vale
    no lugar das fontes; o YouTube Music só completa álbum e capa."""
    yt = tx.dados_youtube({} if sem_dados_youtube else meta)
    video_id = tx._text(meta.get("id"))
    local = dict(dados_locais or {})
    titulo_video = yt["video_title"] or tx._text(local.get("title")) or tx._text(meta.get("title"))
    canal = tx._nome_canal_limpo(yt["uploader"])
    canal_tipo = _canal_tipo(yt, canal_conhecido)
    diagnostico: list[str] = []
    candidatos: list[dict[str, Any]] = []

    achado: dict[str, Any] | None = None
    if forcar and forcar.get("artists"):
        achado = {"title": tx._text(forcar.get("title")) or limpar_titulo(titulo_video),
                  "artists": list(forcar["artists"]), "album": yt["album"], "fonte": FONTE_CREDITOS}
    faixa_ytm = cliente.faixa(video_id) if cliente and video_id and not sem_dados_youtube and not achado else None
    duracao = float(yt["duration"] or (faixa_ytm or {}).get("duration") or 0)

    if faixa_ytm:
        diagnostico.append(f"YouTube Music: este vídeo é {'uma faixa oficial' if faixa_ytm['tipo'] == 'musica' else 'um clipe oficial' if faixa_ytm['tipo'] == 'clipe' else 'um vídeo comum'}")
        if faixa_ytm["tipo"] == "musica" and faixa_ytm["artists"]:
            achado = _de_faixa(faixa_ytm, FONTE_MUSICA)
        elif faixa_ytm["tipo"] == "clipe" and faixa_ytm["artists"]:
            par = faixa_ytm.get("contraparte")
            if par and par["tipo"] == "musica" and par["artists"] and (
                    not par["duration"] or not faixa_ytm["duration"] or abs(par["duration"] - faixa_ytm["duration"]) <= 20):
                achado = _de_faixa(par, FONTE_CLIPE)  # a faixa de áudio do clipe: título e álbum oficiais
            else:
                achado = _de_faixa(faixa_ytm, FONTE_CLIPE)
                achado["title"] = limpar_titulo(achado["title"])
                no_titulo = tx.artista_nas_partes(achado["title"], achado["artists"])
                if no_titulo:  # "Artista - Música (Official Video)" como título do clipe
                    achado["title"] = no_titulo[2]
    if achado is None and yt["youtube_music"].get("title") and yt["youtube_music"].get("artists"):
        d = yt["youtube_music"]
        achado = {"title": d["title"], "artists": list(d["artists"]), "album": d.get("album") or "",
                  "year": (d.get("date") or "")[:4], "fonte": FONTE_DESCRICAO}
    if achado is None and yt["artists"] and yt["fonte_artistas"] == "youtube":
        titulo = yt["track"]
        if not titulo:  # crédito sem o nome da faixa: a música é o resto do título do vídeo
            no_titulo = tx.artista_nas_partes(limpar_titulo(titulo_video), yt["artists"])
            titulo = no_titulo[2] if no_titulo else limpar_titulo(titulo_video)
        achado = {"title": titulo, "artists": list(yt["artists"]), "album": yt["album"],
                  "year": (yt["release_date"] or "")[:4], "fonte": FONTE_CREDITOS}
    if achado is None and cliente and titulo_video and not sem_dados_youtube:
        encontrado, quase = buscar_no_catalogo(cliente, titulo_video, canal, canal_tipo, duracao)
        if encontrado:
            achado = _de_faixa(encontrado, FONTE_BUSCA)
            candidatos.append(_candidato(encontrado, 0.9))
        else:
            candidatos += [_candidato(c) for c in quase]
    if achado is None and yt["fonte_artistas"] == "cache" and yt["artists"]:
        # identificação anterior, sem dados novos do vídeo: continua valendo
        achado = {"title": tx._text(meta.get("title")), "artists": list(yt["artists"]), "album": yt["album"],
                  "fonte": FONTE_LOCAL}
    if achado is None and sem_dados_youtube and local.get("title") and local.get("artists"):
        achado = {"title": tx._text(local["title"]), "artists": tx._artists(local["artists"]),
                  "album": tx._text(local.get("album")), "fonte": FONTE_LOCAL}
    if achado is None:
        achado = deduzir(titulo_video, canal, canal_tipo, conhecidos, canal_conhecido)
        diagnostico.append(f"sem fonte confiável; {achado.pop('detalhe')}")

    # Créditos/descrição sem álbum: procura a mesma música no catálogo só para o álbum e a capa.
    if achado["fonte"] in (FONTE_CREDITOS, FONTE_DESCRICAO) and not achado.get("album_id") and cliente:
        consulta = f"{' '.join(achado['artists'][:2])} {achado['title']}"
        for c in cliente.buscar(consulta, limite=5):
            if tx._similarity(c["title"], achado["title"]) >= 0.9 and versoes(c["title"]) == versoes(achado["title"]) and any(
                    tx._mesmo_nome(a, b) for a in c["artists"] for b in achado["artists"]):
                achado.update({k: c[k] for k in ("album_id", "capa", "video_id") if c.get(k)})
                achado["album"] = achado.get("album") or c["album"]
                achado["year"] = achado.get("year") or c["year"]
                break

    fonte = achado["fonte"]
    titulo, feat = _feat_do_titulo(tx._text(achado["title"])) if fonte not in CATALOGO else (achado["title"], [])
    artistas = tx._limpar_nomes(list(achado["artists"]) + feat)
    if fonte not in CATALOGO:
        titulo = _com_versao(titulo, titulo_video)
    album = _detalhes_do_album(cliente, achado) if achado.get("album_id") else {}
    capa = album.get("capa") or achado.get("capa") or ""
    thumbs = yt["thumbnails"]

    metadata = {
        "id": video_id, "title": titulo, "artists": artistas, "artist": (artistas or [""])[0],
        "album": album.get("album") or tx._text(achado.get("album")),
        "album_artists": album.get("album_artists") or [],
        "date": album.get("year") or tx._text(achado.get("year")) or yt["release_date"],
        "track_number": album.get("track_number") or 0, "track_total": album.get("track_total") or 0,
        "disc_number": 0, "disc_total": 0, "label": tx._text(yt["youtube_music"].get("label")),
        "isrc": yt["isrc"] or tx.normalize_isrc(local.get("isrc")),
        "duration": duracao, "uploader": yt["uploader"], "channel_id": yt["channel_id"],
        "thumbnail_url": yt["thumbnail_url"], "thumbnails": thumbs,
        "cover_url": capa, "cover_file": "",
        "cover_source": "youtube_music" if capa else ("youtube_thumbnail" if yt["thumbnail_url"] else ""),
        "musicbrainz_recording_id": "", "musicbrainz_release_id": "", "musicbrainz_release_group_id": "",
        "musicbrainz_artist_ids": [], "ytmusic_album_id": achado.get("album_id") or "",
        "variant": " + ".join(sorted(versoes(titulo_video))),
    }
    confiavel = fonte in FONTES_CONFIAVEIS
    provenance = {"title": fonte, "artists": fonte, "uploader": "youtube"}
    confidence = {campo: "confirmada" if confiavel else "provisoria" for campo in ("title", "artists")}
    if metadata["album"]:
        provenance["album"] = fonte
        confidence["album"] = "confirmada" if confiavel else "provisoria"
    if metadata["isrc"]:
        provenance["isrc"] = "youtube" if yt["isrc"] else "local"
    if capa:
        provenance["cover_url"] = "youtube_music"
    nota = CONFIANCA.get(fonte, 0.6)
    scores = {"title": nota, "artists": nota, "cover": 0.95 if capa else 0.3}

    sinais = sinais_de_erro(metadata, fonte, titulo_video=titulo_video, canal=canal, canal_tipo=canal_tipo,
                            duracao=duracao, conhecidos=conhecidos, canal_conhecido=canal_conhecido)
    motivos = [] if artistas else ["nenhum artista identificado"]
    if not titulo:
        motivos.append("campo obrigatório ausente: title")
    observacoes = list(sinais)
    if achado.get("incerto") and fonte == FONTE_TITULO:
        observacoes.append(f"artista incerto: o título do vídeo tem várias partes ('{limpar_titulo(titulo_video)}')")
    if not confiavel and fonte != FONTE_LOCAL and revisar == "all":
        observacoes.append(f"não achado no YouTube Music; título/artista deduzidos do {DESCRICAO_FONTE[fonte]}")
    if revisar == "none":
        observacoes = []
    status = "PENDENTE" if motivos else ("OBSERVAR" if observacoes else "RESOLVIDO")

    v("Identificação", f"{status}: {' & '.join(artistas) or '?'} — {titulo or '?'}"
                       f"{' | álbum ' + metadata['album'] if metadata['album'] else ''} ({DESCRICAO_FONTE.get(fonte, fonte)})")
    for sinal in observacoes:
        v("Identificação", f"  conferir: {sinal}")
    return {
        "video_id": video_id,
        "status": status,
        "metadata": metadata,
        "provenance": provenance,
        "confidence": confidence,
        "pending_reasons": motivos,
        "candidates": candidatos,
        "diagnostics": diagnostico + observacoes,
        "diagnostics_by_source": {"youtube_music": diagnostico},
        "source_youtube": yt,
        "source_local": {k: local.get(k) for k in ("title", "artists", "album", "isrc") if local.get(k)},
        "observations": observacoes,
        "policy": {"modelo": "youtube_music", "fonte": fonte, "revisar": revisar,
                   "canal": {"nome": canal, "tipo": canal_tipo}},
        "scores": scores,
    }
