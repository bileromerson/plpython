"""Compara o spotify.csv com as músicas baixadas e acerta as M3Us.

Para cada playlist do Spotify:
  * as músicas já baixadas entram na M3U, na ordem do Spotify;
  * o que está na M3U mas saiu da playlist do Spotify sai da M3U
    (--keep-extra mantém). Nenhum MP3 é apagado;
  * o que não foi baixado vai para nao_baixadas.csv, com um link de busca no
    YouTube Music;
  * se uma música do Spotify não casa com certeza, mas parece ser uma que já
    está na M3U, ela fica e aparece como "conferir" (também no CSV).

Como uma música do Spotify é reconhecida na biblioteca, nesta ordem:
  1. ISRC igual;
  2. título igual, ignorando o que não muda a música ("- Remastered 2011",
     "(feat. X)", "(Radio Edit)", "- From 'Filme'"...), com artista compatível.
     Remix, ao vivo, acústico, "Taylor's Version" etc. contam como outra música;
  3. título quase igual, do mesmo artista, com duração parecida.

A biblioteca vem do banco do download (só leitura) e, para MP3 que não estão
no banco, das tags ou do nome da pasta e do arquivo. Simula por padrão; com -y
grava as M3Us, guardando antes as antigas em sql/m3u_backup/.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import resolvedor_metadados as rm
from spotify_conta import caminho_curto, chave_nome, extrair_playlist_id, ler_csv, numero, qtd
from verbose import colorir, v, vv

ARQUIVO_FALTANDO = "nao_baixadas.csv"
COLUNAS_FALTANDO = ("status", "playlists", "artists", "title", "album", "isrc",
                    "spotify_url", "youtube_music", "obs")
MOSTRAR = 10                 # itens listados por playlist (com -v: todos)
DIFERENCA_DURACAO = 45       # segundos; acima disso o casamento aproximado é descartado

# --- Como comparar títulos --------------------------------------------------

_PARENTESES = re.compile(r"\s*[\[(]([^\])]*)[\])]")
_SEPARADOR = re.compile(r"\s+[-–—]\s+")
_FEAT_SOLTO = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.I)
# Palavras que indicam outra gravação: com elas, o trecho faz parte do nome.
_OUTRA_GRAVACAO = re.compile(
    r"\b(?:re)?mix|\brmx\b|\blive\b|ao vivo|ac[uú]stic|acoustic|instrumental|\bvers(?:ion|ão|ao)\b"
    r"|\bedit\b|\bcover\b|karaok|\bdemo\b|sped\s*up|slowed|reverb|nightcore|rework|bootleg"
    r"|\bvip\b|\bflip\b|\bdub\b|extended|\bsessions?\b|unplugged|orchestr|piano|a\s*cappella"
    r"|acapella|\brecorded\b",
    re.I,
)
# Exceções: versões que são a mesma música.
_MESMA_GRAVACAO = re.compile(
    r"^(?:original|radio|single|album|mono|stereo|main|clean|explicit|lp)\s+(?:mix|edit|version)$", re.I
)


def _neutro(trecho: str) -> bool:
    """True se o trecho ("Remastered 2011", "feat. X", "Radio Edit") não muda a música."""
    trecho = trecho.strip()
    return bool(_MESMA_GRAVACAO.match(trecho)) or not _OUTRA_GRAVACAO.search(trecho)


def chave_titulo(titulo: Any) -> str:
    texto = _PARENTESES.sub(lambda m: "" if _neutro(m.group(1)) else m.group(0), rm._text(titulo))
    partes = _SEPARADOR.split(texto)
    while len(partes) > 1 and _neutro(partes[-1]):
        partes.pop()
    return rm._normalize_text(_FEAT_SOLTO.sub("", " - ".join(partes))) or rm._normalize_text(titulo)


def chave_nucleo(titulo: Any) -> str:
    """Só o nome, sem nenhuma indicação de versão: acha "outra versão" da música."""
    texto = _SEPARADOR.split(_PARENTESES.sub("", rm._text(titulo)))[0]
    return rm._normalize_text(_FEAT_SOLTO.sub("", texto))


def chave_artista(nome: Any) -> str:
    texto = re.sub(r"^the\s+", "", rm._normalize_text(nome))
    return texto.replace(" ", "")


def artistas_batem(a: Iterable[str], b: Iterable[str]) -> bool:
    b = list(b)
    return any(rm._mesmo_nome(x, y) for x in a for y in b)


def _mesmos_artistas(a: Iterable[str], b: Iterable[str]) -> bool:
    return bool(({chave_artista(x) for x in a} & {chave_artista(y) for y in b}) - {""})


def quase_igual(a: str, b: str) -> float:
    """Semelhança de duas chaves de título que só aceita erro de digitação: números
    diferentes ("No. 5" x "No. 9") ou uma palavra a mais ("Song" x "Song Two") zeram."""
    if not a or not b or re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return 0.0
    nota = rm._similarity(a, b)
    return nota if len(a.split()) == len(b.split()) or nota >= 0.95 else 0.0


def _longe(a: float, b: float) -> bool:
    return a > 0 and b > 0 and abs(a - b) > DIFERENCA_DURACAO


def _id(caminho: str) -> str:
    return os.path.normcase(os.path.realpath(caminho))


# --- Dados ------------------------------------------------------------------

@dataclass(eq=False)
class FaixaSpotify:
    playlist: str
    titulo: str
    artistas: list[str]
    track_id: str = ""
    isrc: str = ""
    album: str = ""
    duracao: float = 0.0
    url: str = ""

    @property
    def chave_unica(self) -> str:
        return self.track_id or f"{chave_titulo(self.titulo)}|{'|'.join(map(chave_artista, self.artistas))}"

    @property
    def rotulo(self) -> str:
        return f"{', '.join(self.artistas)} — {self.titulo}" if self.artistas else self.titulo


@dataclass(eq=False)
class Musica:
    caminho: str
    titulo: str
    artistas: list[str]
    isrc: str = ""
    album: str = ""
    duracao: float = 0.0
    chaves: list[str] = field(default_factory=list)
    nucleo: str = ""
    artistas_titulo: list[str] = field(default_factory=list)  # de "Artista - Música"

    def __post_init__(self) -> None:
        self.chaves = [chave_titulo(self.titulo)]
        partes = _SEPARADOR.split(rm._text(self.titulo), maxsplit=1)
        if len(partes) == 2:  # título no estilo do YouTube: "Artista - Música (Clipe)"
            self.chaves.append(chave_titulo(partes[1]))
            self.artistas_titulo = rm.dividir_artistas(partes[0])
        self.chaves = [c for c in dict.fromkeys(self.chaves) if c]
        self.nucleo = chave_nucleo(self.titulo)

    @property
    def todos_artistas(self) -> list[str]:
        return self.artistas + self.artistas_titulo

    @property
    def rotulo(self) -> str:
        return f"{', '.join(self.artistas)} — {self.titulo}" if self.artistas else self.titulo

    def extinf(self) -> str:
        nomes = ", ".join(self.artistas)
        duracao = f"{self.duracao:g}" if self.duracao > 0 else "-1"
        return f"#EXTINF:{duracao},{nomes + ' - ' if nomes else ''}{self.titulo}"


@dataclass(eq=False)
class EntradaM3U:
    linha: str      # o caminho como está escrito na M3U
    extinf: str
    caminho: str    # absoluto

    @property
    def rotulo(self) -> str:
        _, _, texto = self.extinf.partition(",")
        return texto.strip() or os.path.splitext(os.path.basename(self.caminho))[0]


@dataclass(eq=False)
class Item:
    linha: str
    extinf: str
    caminho: str
    rotulo: str


@dataclass
class Casamento:
    musica: Musica | None
    como: str = ""   # isrc, nome ou aproximada
    obs: str = ""


@dataclass(eq=False)
class Plano:
    nome: str
    arquivo: str
    existe: bool
    total: int
    baixadas: int = 0
    itens: list[Item] = field(default_factory=list)
    adicionadas: list[Item] = field(default_factory=list)
    removidas: list[tuple[Item, str]] = field(default_factory=list)
    conferir: list[tuple[FaixaSpotify, Item]] = field(default_factory=list)
    faltando: list[tuple[FaixaSpotify, str]] = field(default_factory=list)
    aproximadas: list[tuple[FaixaSpotify, Musica]] = field(default_factory=list)
    pares: list[tuple[FaixaSpotify, EntradaM3U]] = field(default_factory=list)
    ordem_mudou: bool = False
    repetidas: bool = False

    @property
    def mudou(self) -> bool:
        if not self.existe:
            return bool(self.itens)
        return bool(self.adicionadas or self.removidas or self.ordem_mudou or self.repetidas)


# --- Biblioteca ---------------------------------------------------------------

def _uri_leitura(caminho: str) -> str:
    return "file:" + urllib.request.pathname2url(os.path.abspath(caminho)) + "?mode=ro"


def _lista(valor: Any) -> list[str]:
    try:
        dados = json.loads(valor or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x).strip() for x in dados if str(x).strip()] if isinstance(dados, list) else []


def ler_banco(downloads_db: str, metadata_db: str) -> list[sqlite3.Row]:
    """Faixas do banco do download, sem gravar nada nele."""
    if not os.path.isfile(downloads_db):
        return []
    tem_metadados = os.path.isfile(metadata_db)
    if tem_metadados:
        consulta = """
            SELECT c.path AS caminho1, r.audio_path AS caminho2,
                   COALESCE(NULLIF(t.title, ''), c.title) AS titulo,
                   CASE WHEN COALESCE(t.artists_json, '[]') NOT IN ('', '[]')
                        THEN t.artists_json ELSE c.artists_json END AS artistas,
                   COALESCE(t.album, '') AS album, COALESCE(t.isrc, '') AS isrc,
                   COALESCE(NULLIF(t.duration, 0), c.duration) AS duracao
            FROM video_cache c
            LEFT JOIN music.music_tracks t ON t.video_key = c.video_key
            LEFT JOIN music.metadata_resolution r ON r.video_key = c.video_key
            UNION ALL
            SELECT '', r.audio_path, t.title, t.artists_json, t.album, t.isrc, t.duration
            FROM music.music_tracks t
            LEFT JOIN music.metadata_resolution r ON r.video_key = t.video_key
            WHERE t.video_key NOT IN (SELECT video_key FROM video_cache)"""
    else:
        consulta = ("SELECT path AS caminho1, '' AS caminho2, title AS titulo, artists_json AS artistas, "
                    "'' AS album, '' AS isrc, duration AS duracao FROM video_cache")
    conexao = sqlite3.connect(_uri_leitura(downloads_db), uri=True)
    conexao.row_factory = sqlite3.Row
    try:
        if tem_metadados:
            conexao.execute("ATTACH DATABASE ? AS music", (_uri_leitura(metadata_db),))
        return conexao.execute(consulta).fetchall()
    except sqlite3.DatabaseError as exc:
        v("Biblioteca", f"não consegui ler o banco {downloads_db}: {exc}")
        return []
    finally:
        conexao.close()


def _tags(caminho: str) -> dict | None:
    try:
        from mutagen.mp3 import MP3
    except ImportError:
        return None
    try:
        audio = MP3(caminho)
    except Exception:  # noqa: BLE001 - arquivo estranho: usa o nome
        return None
    tags = audio.tags

    def texto(chave: str) -> list[str]:
        quadro = tags.get(chave) if tags is not None else None
        return [str(t).strip() for t in getattr(quadro, "text", []) if str(t).strip()]

    titulo = (texto("TIT2") or [""])[0]
    if not titulo:
        return None
    return {"titulo": titulo, "artistas": texto("TXXX:ARTISTS") or texto("TPE1"),
            "isrc": (texto("TSRC") or [""])[0], "album": (texto("TALB") or [""])[0],
            "duracao": float(getattr(audio.info, "length", 0) or 0)}


def _pelo_caminho(caminho: str, raiz: str) -> dict:
    pasta = os.path.dirname(caminho)
    titulo = os.path.splitext(os.path.basename(caminho))[0]
    artista = os.path.basename(pasta)
    artistas = [artista] if os.path.normcase(pasta) != os.path.normcase(raiz) and not artista.startswith("_") else []
    return {"titulo": titulo, "artistas": artistas, "isrc": "", "album": "", "duracao": 0.0}


def carregar_biblioteca(pasta_musicas: str, downloads_db: str, metadata_db: str) -> list[Musica]:
    musicas: list[Musica] = []
    vistos: set[Any] = set()

    def guardar(caminho: str, dados: dict) -> None:
        if os.path.islink(caminho):  # atalho de artista secundário: vale o arquivo real
            caminho = os.path.normpath(os.path.join(os.path.dirname(caminho), os.readlink(caminho)))
        if not caminho.lower().endswith(".mp3") or not os.path.isfile(caminho):
            return
        info = os.stat(caminho)
        chave = (info.st_dev, info.st_ino) if info.st_ino else _id(caminho)
        if chave in vistos:  # mesmo arquivo (hard link) ou já visto
            return
        vistos.add(chave)
        titulo = str(dados.get("titulo") or "").strip() or os.path.splitext(os.path.basename(caminho))[0]
        musicas.append(Musica(os.path.abspath(caminho), titulo, list(dados.get("artistas") or []),
                              rm.normalize_isrc(dados.get("isrc")), str(dados.get("album") or ""),
                              float(dados.get("duracao") or 0)))

    base = os.path.dirname(os.path.abspath(downloads_db))
    for linha in ler_banco(downloads_db, metadata_db):
        for bruto in (linha["caminho2"], linha["caminho1"]):
            caminho = os.path.join(base, bruto) if bruto and not os.path.isabs(bruto) else bruto
            if caminho and os.path.isfile(caminho):
                guardar(caminho, {"titulo": linha["titulo"], "artistas": _lista(linha["artistas"]),
                                  "isrc": linha["isrc"], "album": linha["album"], "duracao": linha["duracao"]})
                break
    do_banco = len(musicas)

    if os.path.isdir(pasta_musicas):
        for raiz, pastas, arquivos in os.walk(pasta_musicas):
            pastas[:] = sorted(p for p in pastas if not p.startswith("."))
            for nome in sorted(arquivos):
                caminho = os.path.join(raiz, nome)
                if nome.startswith(".") or not nome.lower().endswith(".mp3") or os.path.islink(caminho):
                    continue
                try:
                    info = os.stat(caminho)
                except OSError:
                    continue
                if (info.st_dev, info.st_ino) in vistos or _id(caminho) in vistos:
                    continue
                guardar(caminho, _tags(caminho) or _pelo_caminho(caminho, pasta_musicas))
    v("Biblioteca", f"{do_banco} músicas do banco e {len(musicas) - do_banco} só na pasta {caminho_curto(pasta_musicas)}")
    return musicas


class Indice:
    def __init__(self, musicas: list[Musica]):
        self.por_id: dict[str, Musica] = {}
        self.por_isrc: dict[str, list[Musica]] = defaultdict(list)
        self.por_chave: dict[str, list[Musica]] = defaultdict(list)
        self.por_nucleo: dict[str, list[Musica]] = defaultdict(list)
        self.por_artista: dict[str, list[Musica]] = defaultdict(list)
        for musica in musicas:
            self.por_id[_id(musica.caminho)] = musica
            if musica.isrc:
                self.por_isrc[musica.isrc].append(musica)
            for chave in musica.chaves:
                self.por_chave[chave].append(musica)
            if musica.nucleo:
                self.por_nucleo[musica.nucleo].append(musica)
            for artista in dict.fromkeys(map(chave_artista, musica.todos_artistas)):
                if artista:
                    self.por_artista[artista].append(musica)

    @staticmethod
    def _melhor(candidatas: list[Musica], faixa: FaixaSpotify, chave: str) -> Musica:
        return min(candidatas, key=lambda m: (
            chave not in m.chaves,
            not _mesmos_artistas(faixa.artistas, m.artistas),
            abs(m.duracao - faixa.duracao) if m.duracao and faixa.duracao else 999,
        ))

    def casar(self, faixa: FaixaSpotify) -> Casamento:
        chave = chave_titulo(faixa.titulo)
        if faixa.isrc and self.por_isrc.get(faixa.isrc):
            return Casamento(self._melhor(self.por_isrc[faixa.isrc], faixa, chave), "isrc")
        candidatas = [m for m in self.por_chave.get(chave, []) if artistas_batem(faixa.artistas, m.todos_artistas)]
        if candidatas:
            return Casamento(self._melhor(candidatas, faixa, chave), "nome")
        melhor, nota, vistas = None, 0.0, set()
        for artista in faixa.artistas:
            for musica in self.por_artista.get(chave_artista(artista), []):
                if id(musica) in vistas or _longe(faixa.duracao, musica.duracao):
                    continue
                vistas.add(id(musica))
                semelhanca = max((quase_igual(chave, c) for c in musica.chaves), default=0.0)
                if semelhanca > nota:
                    melhor, nota = musica, semelhanca
        if melhor is not None and nota >= 0.9:
            return Casamento(melhor, "aproximada")
        return Casamento(None, obs=self._dica(faixa, chave))

    def _dica(self, faixa: FaixaSpotify, chave: str) -> str:
        outras = [m for m in self.por_nucleo.get(chave_nucleo(faixa.titulo), [])
                  if artistas_batem(faixa.artistas, m.todos_artistas)]
        if outras:
            return f"você tem outra versão: {outras[0].rotulo}"
        homonimas = self.por_chave.get(chave, [])
        if homonimas:
            return f"mesmo título com outro artista no seu arquivo: {homonimas[0].rotulo} (confira com review)"
        return ""


# --- M3U -----------------------------------------------------------------------

def ler_m3u(arquivo: str) -> list[EntradaM3U]:
    pasta = os.path.dirname(os.path.abspath(arquivo))
    entradas: list[EntradaM3U] = []
    extinf = ""
    with open(arquivo, encoding="utf-8-sig", errors="replace") as handle:
        for bruta in handle:
            linha = bruta.strip()
            if not linha:
                continue
            if linha.startswith("#"):
                if linha.upper().startswith("#EXTINF"):
                    extinf = linha
                continue
            alvo = linha.replace("\\", "/") if os.sep == "/" else linha  # M3U feita no Windows
            caminho = alvo if os.path.isabs(alvo) else os.path.normpath(os.path.join(pasta, alvo))
            entradas.append(EntradaM3U(linha, extinf, caminho))
            extinf = ""
    return entradas


def gravar_m3u(arquivo: str, itens: list[Item]) -> None:
    pasta = os.path.dirname(os.path.abspath(arquivo))
    os.makedirs(pasta, exist_ok=True)
    linhas = ["#EXTM3U"]
    for item in itens:
        if item.extinf:
            linhas.append(item.extinf)
        linhas.append(item.linha)
    fd, temporario = tempfile.mkstemp(prefix=".m3u-", suffix=".tmp", dir=pasta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(linhas) + "\n")
        os.replace(temporario, arquivo)
    except BaseException:
        try:
            os.unlink(temporario)
        except OSError:
            pass
        raise


def _item_da_musica(musica: Musica, pasta_playlists: str) -> Item:
    try:
        linha = os.path.relpath(musica.caminho, start=pasta_playlists)
    except ValueError:  # outro disco no Windows
        linha = musica.caminho
    return Item(linha, musica.extinf(), musica.caminho, musica.rotulo)


def _item_da_entrada(entrada: EntradaM3U, indice: Indice) -> Item:
    musica = indice.por_id.get(_id(entrada.caminho))
    return Item(entrada.linha, entrada.extinf, entrada.caminho, musica.rotulo if musica else entrada.rotulo)


def _do_extinf(entrada: EntradaM3U) -> tuple[str, list[str]]:
    texto = entrada.rotulo
    artistas, separador, titulo = texto.partition(" - ")
    return (titulo, rm.dividir_artistas(artistas)) if separador else (texto, [])


def _par_na_m3u(faixa: FaixaSpotify, extras: list[EntradaM3U], indice: Indice) -> EntradaM3U | None:
    """Uma entrada que já estava na M3U e parece ser esta música do Spotify."""
    chave = chave_titulo(faixa.titulo)
    melhor, nota = None, 0.0
    for entrada in extras:
        if not os.path.isfile(entrada.caminho):
            continue
        musica = indice.por_id.get(_id(entrada.caminho))
        if musica:
            chaves, artistas = musica.chaves, musica.todos_artistas
        else:
            titulo, artistas = _do_extinf(entrada)
            chaves = [chave_titulo(titulo)]
        semelhanca = max((rm._similarity(chave, c) for c in chaves
                          if re.findall(r"\d+", chave) == re.findall(r"\d+", c)), default=0.0)
        if artistas_batem(faixa.artistas, artistas):
            semelhanca += 0.15
        elif semelhanca < 0.92:  # sem artista em comum, só com título praticamente igual
            continue
        if semelhanca > nota:
            melhor, nota = entrada, semelhanca
    return melhor if nota >= 0.85 else None


def planejar(nome: str, arquivo: str, faixas: list[FaixaSpotify], indice: Indice, pasta_playlists: str,
             manter_extras: bool, pares: dict[str, EntradaM3U] | None = None) -> Plano:
    existe = os.path.isfile(arquivo)
    antigas = ler_m3u(arquivo) if existe else []
    plano = Plano(nome, arquivo, existe, len(faixas))
    vagas: list[Item | FaixaSpotify] = []  # ordem do Spotify; FaixaSpotify = ainda sem arquivo
    dicas: dict[int, str] = {}
    usados: set[str] = set()
    for faixa in faixas:
        casamento = indice.casar(faixa)
        if casamento.musica is None:
            vagas.append(faixa)
            dicas[id(faixa)] = casamento.obs
            continue
        plano.baixadas += 1
        chave = _id(casamento.musica.caminho)
        if chave in usados:  # a mesma música duas vezes na playlist
            continue
        usados.add(chave)
        vv("Sync", f"{faixa.rotulo} → {casamento.como}: {caminho_curto(casamento.musica.caminho)}")
        if casamento.como == "aproximada":
            plano.aproximadas.append((faixa, casamento.musica))
        vagas.append(_item_da_musica(casamento.musica, pasta_playlists))

    extras = [e for e in antigas if _id(e.caminho) not in usados]
    for posicao, vaga in enumerate(vagas):
        if not isinstance(vaga, FaixaSpotify):
            continue
        conhecida = (pares or {}).get(vaga.chave_unica)
        entrada = None
        if conhecida is not None and os.path.isfile(conhecida.caminho) and _id(conhecida.caminho) not in usados:
            entrada = next((e for e in extras if _id(e.caminho) == _id(conhecida.caminho)), conhecida)
        else:
            entrada = _par_na_m3u(vaga, extras, indice)
            if entrada is not None:
                plano.pares.append((vaga, entrada))
        if entrada is None:
            plano.faltando.append((vaga, dicas.get(id(vaga), "")))
            continue
        extras = [e for e in extras if _id(e.caminho) != _id(entrada.caminho)]
        usados.add(_id(entrada.caminho))
        item = _item_da_entrada(entrada, indice)
        plano.conferir.append((vaga, item))
        plano.baixadas += 1
        vagas[posicao] = item

    plano.itens = [vaga for vaga in vagas if isinstance(vaga, Item)]
    sobrando: dict[str, EntradaM3U] = {}
    for entrada in extras:
        sobrando.setdefault(_id(entrada.caminho), entrada)
    for entrada in sobrando.values():
        if not os.path.isfile(entrada.caminho):
            plano.removidas.append((_item_da_entrada(entrada, indice), "o arquivo não existe"))
        elif manter_extras:
            plano.itens.append(_item_da_entrada(entrada, indice))
        else:
            plano.removidas.append((_item_da_entrada(entrada, indice), "saiu da playlist do Spotify"))

    antes = [_id(e.caminho) for e in antigas]
    depois = [_id(i.caminho) for i in plano.itens]
    conjunto_antes, conjunto_depois = set(antes), set(depois)
    plano.adicionadas = [i for i in plano.itens if _id(i.caminho) not in conjunto_antes]
    comuns_antes = list(dict.fromkeys(k for k in antes if k in conjunto_depois))
    plano.ordem_mudou = comuns_antes != [k for k in depois if k in conjunto_antes]
    plano.repetidas = len(antes) != len(conjunto_antes)
    return plano


def mapa_m3u(pasta: str) -> dict[str, str]:
    """M3Us que já existem, pela chave do nome ("Rock 🎸.m3u" → "rock")."""
    mapa: dict[str, str] = {}
    if os.path.isdir(pasta):
        for nome in sorted(os.listdir(pasta)):
            if nome.lower().endswith(".m3u") and not nome.startswith("."):
                mapa.setdefault(chave_nome(os.path.splitext(nome)[0]), os.path.join(pasta, nome))
    return mapa


def arquivo_da_playlist(nome: str, pasta: str, mapa: dict[str, str]) -> str:
    import pltube

    return mapa.get(chave_nome(nome)) or os.path.join(pasta, pltube.nome_seguro(nome, "playlist") + ".m3u")


def montar_planos(playlists: list[tuple[str, list[FaixaSpotify]]], indice: Indice, pasta_playlists: str,
                  manter_extras: bool) -> tuple[list[Plano], list[str]]:
    """Duas passadas: a 1ª descobre pares "conferir" nas M3Us; a 2ª usa esses pares
    também nas playlists em que a música aparece sem estar na M3U."""
    mapa = mapa_m3u(pasta_playlists)
    pares: dict[str, EntradaM3U] = {}
    planos: list[Plano] = []
    avisos: list[str] = []
    for _passada in range(2):
        planos, avisos, donos = [], [], {}
        for nome, faixas in playlists:
            arquivo = arquivo_da_playlist(nome, pasta_playlists, mapa)
            if _id(arquivo) in donos:
                avisos.append(f"{nome}: mesmo arquivo de '{donos[_id(arquivo)]}' "
                              f"({os.path.basename(arquivo)}); renomeie uma delas no Spotify")
                continue
            donos[_id(arquivo)] = nome
            planos.append(planejar(nome, arquivo, faixas, indice, pasta_playlists, manter_extras, pares))
        novos = {faixa.chave_unica: entrada for plano in planos for faixa, entrada in plano.pares}
        if novos.keys() <= pares.keys():
            break
        pares.update(novos)
    return planos, avisos


# --- Saída -----------------------------------------------------------------------

def _busca_youtube(faixa: FaixaSpotify) -> str:
    return "https://music.youtube.com/search?q=" + urllib.parse.quote_plus(
        f"{', '.join(faixa.artistas)} {faixa.titulo}".strip())


def gravar_faltando(caminho: str, planos: list[Plano]) -> tuple[int, int]:
    """nao_baixadas.csv: uma linha por música (com as playlists em que aparece)."""
    linhas: dict[str, dict] = {}

    def anotar(plano: Plano, faixa: FaixaSpotify, status: str, obs: str) -> None:
        linha = linhas.get(faixa.chave_unica)
        if linha is None or (status == "conferir" and linha["status"] != "conferir"):
            anteriores = linha["playlists"] if linha else []
            linha = linhas[faixa.chave_unica] = {
                "status": status, "playlists": anteriores, "artists": ", ".join(faixa.artistas),
                "title": faixa.titulo, "album": faixa.album, "isrc": faixa.isrc, "spotify_url": faixa.url,
                "youtube_music": _busca_youtube(faixa), "obs": obs,
            }
        if plano.nome not in linha["playlists"]:
            linha["playlists"].append(plano.nome)

    for plano in planos:
        for faixa, item in plano.conferir:
            anotar(plano, faixa, "conferir", f"talvez seja {caminho_curto(item.caminho)}")
    for plano in planos:
        for faixa, obs in plano.faltando:
            anotar(plano, faixa, "não baixada", obs)

    pasta = os.path.dirname(os.path.abspath(caminho))
    os.makedirs(pasta, exist_ok=True)
    with open(caminho, "w", encoding="utf-8-sig", newline="") as handle:
        escritor = csv.DictWriter(handle, fieldnames=COLUNAS_FALTANDO)
        escritor.writeheader()
        for linha in sorted(linhas.values(), key=lambda l: (l["status"] != "não baixada", l["artists"].casefold(),
                                                             l["title"].casefold())):
            escritor.writerow(dict(linha, playlists="; ".join(linha["playlists"])))
    faltando = sum(1 for l in linhas.values() if l["status"] == "não baixada")
    return faltando, len(linhas) - faltando


def _corte(lista: list, detalhado: bool) -> list:
    return lista if detalhado else lista[:MOSTRAR]


def _mais(lista: list, detalhado: bool) -> None:
    if not detalhado and len(lista) > MOSTRAR:
        print(f"    … e mais {len(lista) - MOSTRAR} (use -v para ver todas)")


def imprimir(plano: Plano, detalhado: bool) -> None:
    baixadas = f"{plano.baixadas} de {plano.total} baixadas"
    destino = caminho_curto(plano.arquivo)
    if not plano.existe and not plano.itens:
        quantas = "a música não está baixada" if plano.total == 1 else f"nenhuma das {plano.total} músicas está baixada"
        print(f"{plano.nome}: {quantas} (M3U não criada)")
    elif not plano.mudou:
        print(f"{plano.nome}: sem mudanças ({baixadas})")
    elif not plano.existe:
        print(f"{plano.nome} → {destino} (nova): {baixadas}")
        if detalhado:
            for item in plano.itens:
                print(f"    {colorir('+', '32')} {item.rotulo}")
    else:
        partes = []
        if plano.adicionadas:
            partes.append(colorir(f"+{len(plano.adicionadas)}", "32"))
        if plano.removidas:
            partes.append(colorir(f"−{len(plano.removidas)}", "31"))
        if plano.ordem_mudou:
            partes.append("ordem do Spotify")
        if not partes:
            partes.append("linhas repetidas")
        print(f"{plano.nome} → {destino}: {', '.join(partes)} ({baixadas})")
        for item in _corte(plano.adicionadas, detalhado):
            print(f"    {colorir('+', '32')} {item.rotulo}")
        _mais(plano.adicionadas, detalhado)
        for item, motivo in _corte(plano.removidas, detalhado):
            print(f"    {colorir('−', '31')} {item.rotulo}  ({motivo})")
        _mais(plano.removidas, detalhado)
    for faixa, item in plano.conferir:
        print(f"    {colorir('?', '33')} {faixa.rotulo}  ↔  {caminho_curto(item.caminho)}")
    if detalhado:
        for faixa, musica in plano.aproximadas:
            print(f"    ~ {faixa.rotulo}  ↔  {musica.rotulo}")
    if plano.faltando:
        if detalhado:
            print(f"    não baixadas ({len(plano.faltando)}):")
            for faixa, obs in plano.faltando:
                print(f"      ✗ {faixa.rotulo}" + (f"  ({obs})" if obs else ""))
        else:
            nomes = [faixa.rotulo for faixa, _ in plano.faltando]
            print(f"    {colorir('não baixadas', '2')}: {len(nomes)} — {', '.join(nomes[:3])}"
                  + (", …" if len(nomes) > 3 else ""))


def ler_playlists(caminho: str) -> list[tuple[str, str, list[FaixaSpotify]]]:
    """spotify.csv → [(id, nome, faixas)] na ordem do arquivo."""
    resultado = []
    for pid, playlist in ler_csv(caminho).items():
        faixas = []
        for linha in playlist.faixas:
            try:
                duracao = int(linha.get("duration_ms") or 0) / 1000
            except ValueError:
                duracao = 0.0
            faixas.append(FaixaSpotify(
                playlist.nome, str(linha.get("title") or "").strip(),
                [a.strip() for a in str(linha.get("artists") or "").split(";") if a.strip()],
                str(linha.get("track_id") or ""), rm.normalize_isrc(linha.get("isrc")),
                str(linha.get("album") or ""), duracao, str(linha.get("url") or ""),
            ))
        resultado.append((pid, playlist.nome, [f for f in faixas if f.titulo]))
    return resultado


def _filtrar(playlists: list[tuple[str, str, list[FaixaSpotify]]], pedidos: list[str]):
    if not pedidos:
        return playlists, []
    escolhidas, faltando = [], []
    for pedido in pedidos:
        try:
            pid = extrair_playlist_id(pedido)
        except ValueError:
            pid = pedido.strip()
        achadas = [p for p in playlists if chave_nome(p[1]) == chave_nome(pedido) or p[0] == pid]
        if not achadas:
            faltando.append(pedido)
        escolhidas += [p for p in achadas if p not in escolhidas]
    return escolhidas, faltando


def executar(args: Any) -> int:
    detalhado = (getattr(args, "verbose", 0) or 0) >= 1
    arquivo_spotify = os.path.abspath(args.csvfile)
    if not os.path.isfile(arquivo_spotify):
        print(f"Não achei {caminho_curto(arquivo_spotify)}. Rode antes: python plpython.py spotify",
              file=sys.stderr)
        return 1
    playlists, faltando = _filtrar(ler_playlists(arquivo_spotify), list(args.playlists or []))
    for pedido in faltando:
        print(f"Playlist não está em {os.path.basename(arquivo_spotify)}: {pedido}", file=sys.stderr)
    if not playlists:
        print("Nenhuma playlist para comparar.", file=sys.stderr)
        return 1

    pasta_musicas = os.path.abspath(args.songs_dir or "songs")
    pasta_playlists = os.path.abspath(args.playlist_dir or "playlists")
    downloads_db = os.path.abspath(args.downloads_db)
    biblioteca = carregar_biblioteca(pasta_musicas, downloads_db, os.path.abspath(args.metadata_db))
    lido_em = time.strftime("%d/%m/%Y %H:%M", time.localtime(os.path.getmtime(arquivo_spotify)))
    total = sum(len(faixas) for _, _, faixas in playlists)
    print(f"Spotify: {qtd(len(playlists), 'playlist', 'playlists')}, {qtd(total, 'música', 'músicas')} "
          f"({caminho_curto(arquivo_spotify)}, lido em {lido_em})")
    print(f"Biblioteca: {qtd(len(biblioteca), 'música', 'músicas')} em {caminho_curto(pasta_musicas)}")
    if not biblioteca:
        print("  Nenhum MP3 encontrado: rode na pasta onde fez o download, ou use -s/--songs-dir.")

    planos, avisos = montar_planos([(nome, faixas) for _, nome, faixas in playlists],
                                   Indice(biblioteca), pasta_playlists, args.manter_extras)
    print(f"\n{colorir('+', '32')} entra na M3U · {colorir('−', '31')} sai da M3U · "
          f"{colorir('?', '33')} conferir (parece ser a mesma música) · o MP3 nunca é apagado\n")
    for plano in planos:
        imprimir(plano, detalhado)
    for aviso in avisos:
        print(f"Aviso: {aviso}")

    arquivo_faltando = os.path.abspath(args.faltando)
    nao_baixadas, conferir = gravar_faltando(arquivo_faltando, planos)
    mudam = [p for p in planos if p.mudou]
    novas = sum(1 for p in mudam if not p.existe)
    iguais = len(planos) - len(mudam)
    print(f"\nResumo: {qtd(len(mudam), 'M3U muda', 'M3Us mudam')}"
          + (f" ({qtd(novas, 'nova', 'novas')})" if novas else "")
          + f", {iguais} sem mudanças; {qtd(nao_baixadas, 'música não baixada', 'músicas não baixadas')}"
          + (f" e {conferir} para conferir" if conferir else "")
          + f" → {caminho_curto(arquivo_faltando)}")

    if not mudam:
        return 0
    if not args.apply:
        print("Simulação: nenhuma M3U foi gravada. Para gravar: python plpython.py sync -y")
        return 0
    copia = None
    for plano in mudam:
        if plano.existe:
            if copia is None:
                copia = os.path.join(os.path.dirname(downloads_db), "m3u_backup", time.strftime("%Y%m%d-%H%M%S"))
                os.makedirs(copia, exist_ok=True)
            shutil.copy2(plano.arquivo, os.path.join(copia, os.path.basename(plano.arquivo)))
        gravar_m3u(plano.arquivo, plano.itens)
    print(f"{qtd(len(mudam), 'M3U gravada', 'M3Us gravadas')} em {caminho_curto(pasta_playlists)}."
          + (f" As versões antigas estão em {caminho_curto(copia)}." if copia else ""))
    return 0
