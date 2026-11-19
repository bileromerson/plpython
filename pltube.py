"""Motor do download (usado pelo comando `plpython.py download`/`update`).

Lê os CSVs, consulta o YouTube, identifica a música (resolvedor_metadados),
baixa o áudio, grava tags e capa, organiza as pastas e gera as M3Us. Não é
executado diretamente: use `python plpython.py download ...`.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import progresso
import verbose
from verbose import curto, v, vv, vvv
from resolvedor_metadados import (
    LIMIAR_CONFIANCA, MusicBrainzClient, _DESCRICAO_REUPLOAD, _mesmo_nome, _nome_canal_limpo,
    _normalize_text as _normalizar_texto,
    dividir_artistas, ler_descricao_youtube_music, normalize_isrc, resolver_com_prioridade,
)

try:
    import yt_dlp
except ImportError:  # Mantém helpers e testes importáveis sem a dependência instalada.
    yt_dlp = None


BASE_DIR = os.getcwd()  # usada SOMENTE para ler os CSVs
WORK_DIR = os.getcwd()  # pasta de execução: songs/, playlists/, sql/, Logs.txt e planilha
SONGS_DIR = os.path.join(WORK_DIR, "songs")
PLAYLISTS_DIR = os.path.join(WORK_DIR, "playlists")
LOG_FILE = os.path.join(WORK_DIR, "Logs.txt")
ROW = 2
ARTIST_LINKS = False
ESPERAR_BLOQUEIO = False
MODELO_HIBRIDO = False
EXIGIR_ISRC = False
EXIGIR_ARTISTA_MUSICBRAINZ = False
PREFERIR = "youtube"  # em divergência: youtube | musicbrainz
REVISAR = "all"  # all | diff | none
STATUS_REVISAO = ("PENDENTE", "OBSERVAR")
MUSICBRAINZ_CLIENT: MusicBrainzClient | None = None
RESOLVER_CACHE: dict[str, Any] = {}
FAIXAS_PARA_PERGUNTAR: list[str] = []  # download --ask: faixas desta execução sem dados confirmados
ARTISTAS_DB = ""  # --artists-db; vazio = artistas.sqlite3 ao lado do banco de músicas
ARTISTAS_CONHECIDOS: list[str] = []  # artistas já confirmados na biblioteca (aprendidos)
CAPAS_BAIXADAS: dict[str, bytes | None] = {}  # cache das capas por URL nesta execução
FONTES_ARTISTA_CONFIRMADO = {"musicbrainz", "youtube_validado_musicbrainz", "manual", "youtube_music", "banco_isrc"}
# Falhas de rede/servidor (502/503, timeout): somem em segundos ou minutos.
MAX_TENTATIVAS_BLOQUEIO = 6
ESPERA_INICIAL_SEGUNDOS = 30
ESPERA_MAXIMA_SEGUNDOS = 600
# Limite da sessão ("try again later", 429, "not a bot"): segundo o próprio
# yt-dlp, o YouTube limita a sessão "por até uma hora". Insistir antes disso só
# prolonga o bloqueio, então as esperas são longas e passam de 1 hora no total.
ESPERAS_LIMITE_SESSAO = (5 * 60, 15 * 60, 30 * 60, 60 * 60)
# Pausa entre vídeos, como recomenda a wiki do yt-dlp (5-10 s). O tempo gasto com
# MusicBrainz/download conta, então só espera o que faltar.
PAUSA_YOUTUBE = (5.0, 10.0)
PAUSA_ENTRE_REQUISICOES = 0.75  # dentro de uma mesma consulta (preset "-t sleep" do yt-dlp)
_ULTIMA_CONSULTA_YOUTUBE: float | None = None
# Metadados já lidos nesta execução: o download reaproveita em vez de consultar de novo.
INFO_YOUTUBE: dict[str, dict[str, Any]] = {}
# Conta do Google (cookies) para o yt-dlp: {"cookiesfrombrowser": (...)} ou {"cookiefile": ...}
COOKIES_YTDLP: dict[str, Any] = {}
_ULTIMO_STATUS_GRAVADO: dict[str, str] = {}  # só para o -vv não repetir a mesma linha
_DICA_LOGIN_MOSTRADA = False
MAX_INFO_YOUTUBE = 50


def verificar_diretorios(args: argparse.Namespace) -> None:
    """--csvfile aponta para os CSVs; diretórios de saída ficam na pasta de execução."""
    global BASE_DIR, WORK_DIR, SONGS_DIR, PLAYLISTS_DIR, LOG_FILE, ROW, ARTIST_LINKS
    global ESPERAR_BLOQUEIO, MODELO_HIBRIDO, EXIGIR_ISRC, PAUSA_YOUTUBE, COOKIES_YTDLP
    global EXIGIR_ARTISTA_MUSICBRAINZ, PREFERIR, REVISAR, ARTISTAS_DB

    caminho_csv = os.path.abspath(args.base_dir or os.getcwd())
    if os.path.isfile(caminho_csv):
        # --csvfile apontando para um arquivo: processa só ele.
        if not getattr(args, "lista", None):
            args.lista = caminho_csv
        caminho_csv = os.path.dirname(caminho_csv)
    BASE_DIR = caminho_csv
    # --csvfile só indica onde estão os CSVs; songs/, playlists/, sql/, log e
    # planilha ficam na pasta de onde o programa foi iniciado.
    WORK_DIR = os.path.abspath(getattr(args, "work_dir", None) or os.getcwd())
    padrao_musicas = os.path.join(WORK_DIR, "songs")
    SONGS_DIR = os.path.abspath(args.songs_dir) if args.songs_dir else padrao_musicas
    PLAYLISTS_DIR = (
        os.path.abspath(args.playlist_dir)
        if args.playlist_dir
        else os.path.join(WORK_DIR, "playlists")
    )
    LOG_FILE = os.path.abspath(args.log_file) if args.log_file else os.path.join(WORK_DIR, "Logs.txt")
    ROW = args.row
    ARTIST_LINKS = args.artist_links
    ESPERAR_BLOQUEIO = args.esperar_bloqueio
    MODELO_HIBRIDO = args.modelo_hibrido
    EXIGIR_ISRC = args.exigir_isrc
    EXIGIR_ARTISTA_MUSICBRAINZ = args.exigir_artista_musicbrainz
    PREFERIR = getattr(args, "preferir", None) or "youtube"
    COOKIES_YTDLP = dict(getattr(args, "cookies_ytdlp", None) or {})
    pausa = getattr(args, "pausa", None)
    if pausa is not None:
        PAUSA_YOUTUBE = tuple(pausa)
    REVISAR = getattr(args, "revisar", None) or "all"
    ARTISTAS_DB = os.path.abspath(args.artists_db) if getattr(args, "artists_db", None) else ""


def registrar_erro(url: str, motivo: str, playlist: str = "") -> None:
    """Registra falhas sem interromper o processamento do restante da playlist."""
    v("Erro", f"{curto(motivo, 160)}  ({url})")
    try:
        pasta = os.path.dirname(LOG_FILE)
        if pasta:
            os.makedirs(pasta, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as arquivo:
            arquivo.write(f"Link: {url} | Motivo: {motivo} | CSV: {playlist}\n")
    except OSError as exc:
        print(f"Não foi possível gravar o log {LOG_FILE}: {exc}")


class MeuLogger:
    """Adapta mensagens do yt-dlp ao log do programa."""

    def __init__(self, url: str, csv_file: str = "") -> None:
        self.url = url
        self.playlist = csv_file
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        vvv("yt-dlp", msg)

    def warning(self, msg: str) -> None:
        vv("yt-dlp", f"aviso: {msg}")

    def error(self, msg: str) -> None:
        self.errors.append(str(msg))
        print(f"Erro capturado: {msg}")
        registrar_erro(self.url, msg, self.playlist)


class BloqueioTemporarioYouTube(RuntimeError):
    """Sinaliza que o YouTube está recusando temporariamente novas requisições."""


_SINAIS_LIMITE_SESSAO = (
    "try again later", "rate-limited", "rate limited", "rate limit", "ratelimit",
    "http error 429", "429: too many requests", "too many requests",
    "not a bot", "captcha", "temporarily blocked", "unusual traffic", "automated queries",
)
_SINAIS_REDE = (
    "http error 502", "http error 503", "http error 504", "service unavailable",
    "bad gateway", "connection reset", "timed out", "timeout",
)


def tipo_bloqueio(mensagem: str) -> str:
    """"sessao" (o YouTube limitou a sessão), "rede" (falha passageira) ou ""."""
    texto = mensagem.casefold().replace("’", "'")
    if any(sinal in texto for sinal in _SINAIS_LIMITE_SESSAO):
        return "sessao"
    if any(sinal in texto for sinal in _SINAIS_REDE):
        return "rede"
    return ""


def _minutos(segundos: float) -> str:
    return f"{segundos / 60:.0f} min" if segundos >= 60 else f"{segundos:.0f} s"


_SINAIS_LOGIN = (
    "sign in to confirm your age", "age-restricted", "private video", "members-only",
    "join this channel", "login required", "requires authentication", "cookies",
)


def dica_login(mensagem: str) -> None:
    """Uma vez por execução, avisa que o vídeo exige uma conta logada."""
    global _DICA_LOGIN_MOSTRADA
    texto = mensagem.casefold().replace("’", "'")
    if _DICA_LOGIN_MOSTRADA or not any(sinal in texto for sinal in _SINAIS_LOGIN):
        return
    _DICA_LOGIN_MOSTRADA = True
    if COOKIES_YTDLP:
        print("Este vídeo exige login e a conta usada não tem acesso (ou os cookies expiraram; "
              "exporte de novo).")
    else:
        print("Este vídeo exige uma conta logada (privado, +18 ou só para membros). "
              "Rode com --cookies-from-browser firefox para usar a sua conta do Google.")


def aguardar_vez_youtube() -> None:
    """Espaça as consultas ao YouTube (5-10 s por padrão), descontando o tempo
    que já passou desde a última, para não chegar ao limite da sessão."""
    global _ULTIMA_CONSULTA_YOUTUBE
    minimo, maximo = PAUSA_YOUTUBE
    if _ULTIMA_CONSULTA_YOUTUBE is not None and maximo > 0:
        alvo = random.uniform(minimo, maximo)
        falta = alvo - (time.monotonic() - _ULTIMA_CONSULTA_YOUTUBE)
        if falta > 0:
            v("Pausa", f"aguardando {falta:.1f}s antes de consultar o YouTube (pausa sorteada {alvo:.1f}s)")
            progresso.etapa(f"pausa {falta:.0f}s")
            time.sleep(falta)
        else:
            vv("Pausa", f"pausa de {alvo:.1f}s já cumprida pelo trabalho anterior; sem espera")
    _ULTIMA_CONSULTA_YOUTUBE = time.monotonic()


def executar_com_retentativas(url: str, csv_file: str, operacao):
    """Repete só falhas passageiras. Limite da sessão: espera longa (até ~1 h50
    no total) com --wait-on-limit; sem ela, para e avisa quanto esperar."""
    tentativas = 0
    while True:
        try:
            return operacao()
        except Exception as exc:
            mensagem = str(exc)
            tipo = tipo_bloqueio(mensagem)
            registrar_erro(url, mensagem, csv_file)
            if not tipo:
                dica_login(mensagem)
                return None

            v("YouTube", f"erro reconhecido como {'limite da sessão' if tipo == 'sessao' else 'falha de rede'}")
            if tipo == "sessao":
                esperas = ESPERAS_LIMITE_SESSAO
                aviso = ("O YouTube limitou esta sessão (segundo o yt-dlp, por até 1 hora). "
                         "Continuar insistindo só prolonga o bloqueio.")
            else:
                esperas = [min(ESPERA_INICIAL_SEGUNDOS * 2 ** n, ESPERA_MAXIMA_SEGUNDOS)
                           for n in range(MAX_TENTATIVAS_BLOQUEIO)]
                aviso = "Falha passageira de rede/servidor do YouTube."

            if not ESPERAR_BLOQUEIO:
                print(aviso)
                if tipo == "sessao":
                    print("Espere cerca de 1 hora e rode o mesmo comando (ou `plpython.py update`); "
                          "o progresso foi salvo. Para esperar sozinho, use --wait-on-limit.")
                else:
                    print("Rode de novo em alguns minutos, ou use --wait-on-limit para tentar sozinho.")
                raise BloqueioTemporarioYouTube(mensagem) from exc

            if tentativas >= len(esperas):
                print("O acesso continua bloqueado após as tentativas. "
                      "O processamento será interrompido; rode de novo mais tarde.")
                raise BloqueioTemporarioYouTube(mensagem) from exc

            espera = esperas[tentativas]
            tentativas += 1
            print(f"{aviso} Nova tentativa {tentativas}/{len(esperas)} em {_minutos(espera)}. "
                  "Pressione Ctrl+C para cancelar (o progresso está salvo).")
            progresso.etapa("bloqueado; tentando de novo às "
                            + time.strftime("%H:%M", time.localtime(time.time() + espera)))
            time.sleep(espera)


def criar_diretorios() -> None:
    os.makedirs(SONGS_DIR, exist_ok=True)
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)


def obter_csvs() -> list[str]:
    """Lista os CSVs diretamente na pasta base, em ordem determinística."""
    if not os.path.isdir(BASE_DIR):
        return []
    return sorted(
        nome for nome in os.listdir(BASE_DIR)
        if nome.lower().endswith(".csv")
        and os.path.isfile(os.path.join(BASE_DIR, nome))
    )


def obter_chave_video(url: str) -> tuple[str, str]:
    """Extrai o ID do vídeo sem rede ou cria uma chave estável para a URL."""
    partes = urlsplit(url.strip())
    host = (partes.hostname or "").lower()
    video_id = parse_qs(partes.query).get("v", [""])[0]
    caminho = [segmento for segmento in partes.path.split("/") if segmento]
    if not video_id and host in {"youtu.be", "www.youtu.be"} and caminho:
        video_id = caminho[0]
    if not video_id and host.endswith("youtube.com"):
        for marcador in ("shorts", "embed", "live", "v"):
            if marcador in caminho:
                indice = caminho.index(marcador)
                if indice + 1 < len(caminho):
                    video_id = caminho[indice + 1]
                    break
    if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id or ""):
        return f"youtube:{video_id}", video_id
    url_normalizada = url.strip().split("#", 1)[0]
    return "url:" + hashlib.sha256(url_normalizada.encode("utf-8")).hexdigest(), ""


def assinatura_csv(csv_path: str) -> str:
    """Assinatura barata por stat: CSVs já concluídos não precisam ser lidos de novo."""
    stat = os.stat(csv_path)
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def indexar_musicas_locais() -> dict[str, str]:
    """Reconhece downloads anteriores cujo nome termina em [ID-do-YouTube].mp3."""
    encontrados: dict[str, str] = {}
    if not os.path.isdir(SONGS_DIR):
        return encontrados
    padrao = re.compile(r" \[([A-Za-z0-9_-]{6,20})\]\.mp3$", re.IGNORECASE)
    arquivos = []
    for pasta, subpastas, nomes in os.walk(SONGS_DIR, followlinks=False):
        subpastas[:] = [nome for nome in subpastas if not os.path.islink(os.path.join(pasta, nome))]
        for nome in nomes:
            caminho = os.path.join(pasta, nome)
            resultado = padrao.search(nome)
            if resultado and os.path.isfile(caminho) and not os.path.islink(caminho):
                arquivos.append((caminho, resultado.group(1)))
    # Compatibilidade com a estrutura antiga; na nova, o arquivo físico fica
    # na pasta do artista principal e as entradas de colaboradores são links.
    arquivos.sort(key=lambda item: (os.path.basename(os.path.dirname(item[0])) != "_library", item[0]))
    for caminho, video_id in arquivos:
        encontrados.setdefault(video_id, caminho)
    return encontrados


def metadados_de_arquivo_local(path: str, video_id: str, title_hint: str = "") -> dict[str, Any]:
    """Obtém tags locais se disponíveis; não consulta YouTube."""
    title = title_hint.strip() if title_hint else ""
    artistas: list[str] = []
    album = ""
    isrc = ""
    duration = 0.0
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path, easy=True)
        if audio:
            tags = audio.get("title", [])
            if tags:
                title = str(tags[0] if isinstance(tags, list) else tags)
            artistas = normalizar_artistas({"artists": audio.get("artist", [])})
            albums = audio.get("album", [])
            if albums:
                album = str(albums[0] if isinstance(albums, list) else albums).strip()
            duration = float(getattr(getattr(audio, "info", None), "length", 0) or 0)
            for key in ("isrc", "tsrc"):
                values = audio.get(key, [])
                if not isinstance(values, (list, tuple)):
                    values = [values]
                isrc = next((normalized for value in values
                             if (normalized := normalize_isrc(str(value)))), "")
                if isrc:
                    break
    except Exception:
        pass
    if not isrc:
        # TSRC é o frame ID3 padrão para International Standard Recording Code.
        try:
            from mutagen.id3 import ID3

            tags_id3 = ID3(path)
            for frame in tags_id3.getall("TSRC"):
                values = getattr(frame, "text", [])
                if not isinstance(values, (list, tuple)):
                    values = [values]
                isrc = next((normalized for value in values
                             if (normalized := normalize_isrc(str(value)))), "")
                if isrc:
                    break
        except Exception:
            pass
    if not title:
        stem = os.path.splitext(os.path.basename(path))[0]
        title = re.sub(rf" \[{re.escape(video_id)}\]$", "", stem)
    if not artistas:
        relativo = os.path.relpath(os.path.dirname(path), SONGS_DIR)
        nome_pasta = relativo.split(os.sep, 1)[0]
        if nome_pasta and nome_pasta != "_library":
            artistas = [nome_pasta]
        else:
            artistas = ["Artista Desconhecido"]
    local_metadata = {
        "title": title,
        "artists": list(artistas),
        "album": album,
        "isrc": isrc,
    }
    return {
        "title": title,
        "artists": artistas,
        "artist": artistas[0],
        "album": album,
        "isrc": isrc,
        "duration": duration,
        "id": video_id,
        "local_metadata": local_metadata,
    }


def _diretorio_banco(conexao: sqlite3.Connection, schema: str = "main") -> str:
    """Retorna a pasta do arquivo SQLite; em memória, usa a pasta dos CSVs."""
    databases = {row[1]: row[2] for row in conexao.execute("PRAGMA database_list")}
    arquivo = databases.get(schema, "")
    return os.path.dirname(os.path.abspath(arquivo)) if arquivo else WORK_DIR


def _caminho_relativo_banco(path: str, conexao: sqlite3.Connection) -> str:
    if not path:
        return ""
    return os.path.relpath(os.path.abspath(path), start=_diretorio_banco(conexao))


def _resolver_caminho_banco(path: str, conexao: sqlite3.Connection) -> str:
    if not path:
        return ""
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.abspath(os.path.join(_diretorio_banco(conexao), path))


def _chave_csv(csv_path: str) -> str:
    """Guarda CSVs relativamente à pasta base para manter o estado portável."""
    absoluto = csv_path if os.path.isabs(csv_path) else os.path.join(BASE_DIR, csv_path)
    return os.path.normpath(os.path.relpath(os.path.abspath(absoluto), start=BASE_DIR))


def abrir_banco_estado(
    path: str, metadata_path: str | None = None
) -> sqlite3.Connection:
    """Abre o banco de downloads e anexa um segundo arquivo só para metadados."""
    downloads_path = os.path.abspath(path)
    pasta = os.path.dirname(downloads_path)
    os.makedirs(pasta, exist_ok=True)
    metadata_path = os.path.abspath(
        metadata_path or os.path.join(pasta, "musicas.sqlite3")
    )
    if os.path.normcase(os.path.realpath(downloads_path)) == os.path.normcase(
        os.path.realpath(metadata_path)
    ):
        raise ValueError("downloads.sqlite3 e musicas.sqlite3 precisam ser arquivos diferentes")
    os.makedirs(os.path.dirname(metadata_path), exist_ok=True)
    conexao = sqlite3.connect(downloads_path, timeout=30)
    conexao.row_factory = sqlite3.Row
    conexao.execute("ATTACH DATABASE ? AS music", (metadata_path,))
    import banco_artistas

    banco_artistas.anexar(conexao, ARTISTAS_DB or os.path.join(os.path.dirname(metadata_path), "artistas.sqlite3"))
    conexao.executescript(
        """
        CREATE TABLE IF NOT EXISTS video_cache (
            video_key TEXT PRIMARY KEY,
            video_id TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            artists_json TEXT NOT NULL DEFAULT '[]',
            duration REAL NOT NULL DEFAULT 0,
            path TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS download_jobs (
            video_key TEXT PRIMARY KEY,
            video_id TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDENTE',
            attempts INTEGER NOT NULL DEFAULT 0,
            audio_path TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS csv_progress (
            csv_path TEXT PRIMARY KEY,
            signature TEXT NOT NULL,
            next_row INTEGER NOT NULL DEFAULT 0,
            complete INTEGER NOT NULL DEFAULT 0,
            total_rows INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS playlist_items (
            csv_path TEXT NOT NULL,
            row_index INTEGER NOT NULL,
            video_key TEXT NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            duration REAL NOT NULL,
            path TEXT NOT NULL,
            PRIMARY KEY (csv_path, row_index)
        );
        CREATE TABLE IF NOT EXISTS migration_log (
            source_path TEXT PRIMARY KEY,
            migrated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS music.metadata_resolution (
            video_key TEXT PRIMARY KEY,
            video_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            result_json TEXT NOT NULL,
            audio_path TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS music.music_tracks (
            video_key TEXT PRIMARY KEY,
            video_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            artists_json TEXT NOT NULL DEFAULT '[]',
            album TEXT NOT NULL DEFAULT '',
            isrc TEXT NOT NULL DEFAULT '',
            duration REAL NOT NULL DEFAULT 0,
            thumbnail_url TEXT NOT NULL DEFAULT '',
            cover_url TEXT NOT NULL DEFAULT '',
            metadata_status TEXT NOT NULL DEFAULT 'PROVISORIO',
            source_youtube_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            confidence_json TEXT NOT NULL DEFAULT '{}',
            diagnostics_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Correções de artista feitas no review --answer, para valer nas outras faixas (review --apply).
    conexao.execute(
        """CREATE TABLE IF NOT EXISTS music.correcoes_artistas (
            chave TEXT NOT NULL,
            de_json TEXT NOT NULL,
            para_json TEXT NOT NULL,
            video_key TEXT NOT NULL,
            canal TEXT NOT NULL DEFAULT '',
            criada_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chave, video_key)
        )"""
    )
    conexao.execute(
        """CREATE TABLE IF NOT EXISTS music.channel_votes (
            channel_key TEXT NOT NULL,
            video_key TEXT NOT NULL,
            channel_name TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            artist TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (channel_key, video_key)
        )"""
    )
    # Conserta faixas que ficaram NAO_VALIDADO embora a identificação esteja salva.
    conexao.execute(
        """UPDATE music.music_tracks SET metadata_status = (
               SELECT r.status FROM music.metadata_resolution r WHERE r.video_key = music_tracks.video_key)
           WHERE metadata_status = 'NAO_VALIDADO'
             AND EXISTS (SELECT 1 FROM music.metadata_resolution r WHERE r.video_key = music_tracks.video_key)"""
    )
    colunas_tracks = {
        linha[1] for linha in conexao.execute("PRAGMA music.table_info(music_tracks)").fetchall()
    }
    if "review_note" not in colunas_tracks:
        conexao.execute(
            "ALTER TABLE music.music_tracks ADD COLUMN review_note TEXT NOT NULL DEFAULT ''"
        )
    # Compatibilidade de leitura com chamadas antigas e ferramentas locais.
    conexao.execute(
        "CREATE TEMP VIEW IF NOT EXISTS metadata_resolution AS "
        "SELECT * FROM music.metadata_resolution"
    )
    conexao.commit()
    return conexao


def migrar_banco_legado(
    conexao: sqlite3.Connection, legacy_path: str
) -> dict[str, int]:
    """Copia o banco antigo único para os dois bancos novos, sem remover o original."""
    legacy_path = os.path.abspath(legacy_path)
    if not os.path.isfile(legacy_path):
        return {"downloads": 0, "playlists": 0, "metadados": 0}
    bancos_abertos = {
        row[2] for row in conexao.execute("PRAGMA database_list") if row[2]
    }
    if legacy_path in bancos_abertos:
        return {"downloads": 0, "playlists": 0, "metadados": 0}
    chave_migracao = _chave_csv(legacy_path)
    if conexao.execute(
        "SELECT 1 FROM migration_log WHERE source_path = ?", (chave_migracao,)
    ).fetchone():
        return {"downloads": 0, "playlists": 0, "metadados": 0}

    antigo = sqlite3.connect(f"file:{legacy_path}?mode=ro", uri=True, timeout=30)
    antigo.row_factory = sqlite3.Row
    try:
        tabelas = {
            row[0] for row in antigo.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not (tabelas & {"video_cache", "csv_progress", "playlist_items", "metadata_resolution"}):
            return {"downloads": 0, "playlists": 0, "metadados": 0}

        migrated = {"downloads": 0, "playlists": 0, "metadados": 0}
        if "video_cache" in tabelas:
            for row in antigo.execute("SELECT * FROM video_cache"):
                data = dict(row)
                try:
                    artists = json.loads(data.get("artists_json") or "[]")
                except json.JSONDecodeError:
                    artists = []
                meta = {
                    "id": data.get("video_id") or "",
                    "title": data.get("title") or "",
                    "artists": artists if isinstance(artists, list) else [],
                    "duration": data.get("duration") or 0,
                }
                salvar_video_cache(
                    conexao, data["video_key"], data.get("source_url") or "",
                    data.get("status") or "metadata", meta,
                    _resolver_caminho_banco(data.get("path") or "", antigo)
                    if data.get("path") else "",
                    data.get("error") or "",
                )
                _salvar_metadados_musica(conexao, data["video_key"], meta)
                status_map = {
                    "complete": "CONCLUIDO", "failed": "ERRO", "metadata": "METADADOS"
                }
                registrar_job_download(
                    conexao, data["video_key"], data.get("source_url") or "",
                    status_map.get(data.get("status"), "PENDENTE"),
                    video_id=data.get("video_id") or "",
                    path=_resolver_caminho_banco(data.get("path") or "", antigo)
                    if data.get("path") else "",
                    error=data.get("error") or "",
                )
                migrated["downloads"] += 1

        if "csv_progress" in tabelas:
            for row in antigo.execute("SELECT * FROM csv_progress"):
                data = dict(row)
                conexao.execute(
                    """INSERT OR IGNORE INTO csv_progress
                    (csv_path, signature, next_row, complete, total_rows)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        _chave_csv(data["csv_path"]), data.get("signature") or "",
                        int(data.get("next_row") or 0), int(data.get("complete") or 0),
                        int(data.get("total_rows") or 0),
                    ),
                )

        if "playlist_items" in tabelas:
            for row in antigo.execute("SELECT * FROM playlist_items"):
                data = dict(row)
                conexao.execute(
                    """INSERT OR IGNORE INTO playlist_items
                    (csv_path, row_index, video_key, title, artist, duration, path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _chave_csv(data["csv_path"]), int(data["row_index"]),
                        data["video_key"], data.get("title") or "",
                        data.get("artist") or "", float(data.get("duration") or 0),
                        _caminho_relativo_banco(
                            _resolver_caminho_banco(data.get("path") or "", antigo), conexao
                        ) if data.get("path") else "",
                    ),
                )
                migrated["playlists"] += 1

        if "metadata_resolution" in tabelas:
            for row in antigo.execute("SELECT * FROM metadata_resolution"):
                data = dict(row)
                try:
                    resultado = json.loads(data.get("result_json") or "{}")
                except json.JSONDecodeError:
                    resultado = {}
                caminho_antigo = data.get("audio_path") or ""
                audio_path = _resolver_caminho_banco(caminho_antigo, antigo) if caminho_antigo else ""
                salvar_resolucao(
                    conexao, data["video_key"], resultado, audio_path
                )
                migrated["metadados"] += 1

        conexao.execute(
            "INSERT INTO migration_log (source_path) VALUES (?)", (chave_migracao,)
        )
        conexao.commit()
        return migrated
    finally:
        antigo.close()


def ler_video_cache(conexao: sqlite3.Connection, video_key: str) -> dict[str, Any] | None:
    linha = conexao.execute("SELECT * FROM video_cache WHERE video_key = ?", (video_key,)).fetchone()
    if not linha:
        v("SQL", f"{video_key}: ainda não está no banco de downloads")
        return None
    registro = dict(linha)
    v("SQL", f"{video_key}: encontrado no banco (status {registro.get('status')})")
    registro["artists"] = json.loads(registro.pop("artists_json") or "[]")
    registro["path"] = _resolver_caminho_banco(registro.get("path") or "", conexao)
    return registro


def registrar_job_download(
    conexao: sqlite3.Connection,
    video_key: str,
    url: str,
    status: str,
    *,
    video_id: str = "",
    path: str = "",
    error: str = "",
) -> None:
    """Atualiza o acompanhamento da fila sem guardar caminhos absolutos."""
    conexao.execute(
        """INSERT INTO download_jobs
        (video_key, video_id, source_url, status, attempts, audio_path, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_key) DO UPDATE SET
        video_id=CASE WHEN excluded.video_id != '' THEN excluded.video_id ELSE download_jobs.video_id END,
        source_url=excluded.source_url, status=excluded.status,
        attempts=download_jobs.attempts + CASE WHEN excluded.status = 'BAIXANDO' THEN 1 ELSE 0 END,
        audio_path=CASE WHEN excluded.audio_path != '' THEN excluded.audio_path ELSE download_jobs.audio_path END,
        error=excluded.error, updated_at=CURRENT_TIMESTAMP""",
        (
            video_key, video_id, url, status, 1 if status == "BAIXANDO" else 0,
            _caminho_relativo_banco(path, conexao), error,
        ),
    )


def salvar_video_cache(
    conexao: sqlite3.Connection,
    video_key: str,
    url: str,
    status: str,
    meta: dict[str, Any] | None = None,
    path: str = "",
    error: str = "",
) -> None:
    meta = meta or {}
    conexao.execute(
        """INSERT INTO video_cache
        (video_key, video_id, source_url, status, title, artists_json, duration, path, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_key) DO UPDATE SET
        video_id=excluded.video_id, source_url=excluded.source_url, status=excluded.status,
        title=excluded.title, artists_json=excluded.artists_json, duration=excluded.duration,
        path=excluded.path, error=excluded.error""",
        (
            video_key, str(meta.get("id") or ""), url, status,
            str(meta.get("title") or ""), json.dumps(meta.get("artists") or [], ensure_ascii=False),
            float(meta.get("duration") or 0), _caminho_relativo_banco(path, conexao), error,
        ),
    )


def salvar_item_playlist(
    conexao: sqlite3.Connection,
    csv_path: str,
    row_index: int,
    video_key: str,
    meta: dict[str, Any],
    path: str,
) -> None:
    conexao.execute(
        """INSERT OR REPLACE INTO playlist_items
        (csv_path, row_index, video_key, title, artist, duration, path)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            _chave_csv(csv_path), row_index, video_key, meta["title"], ", ".join(meta["artists"]),
            float(meta.get("duration") or 0), _caminho_relativo_banco(path, conexao),
        ),
    )


def listar_itens_playlist(conexao: sqlite3.Connection, csv_path: str):
    rows = conexao.execute(
        "SELECT title, artist, duration, path FROM playlist_items WHERE csv_path = ? ORDER BY row_index",
        (csv_path,),
    ).fetchall()
    return [
        (row["title"], row["artist"], row["duration"], _resolver_caminho_banco(row["path"], conexao))
        for row in rows
    ]


def carregar_resolucao(
    conexao: sqlite3.Connection, video_key: str
) -> dict[str, Any] | None:
    linha = conexao.execute(
        "SELECT result_json, audio_path FROM music.metadata_resolution WHERE video_key = ?",
        (video_key,),
    ).fetchone()
    if not linha:
        vv("SQL", f"{video_key}: sem metadados salvos")
        return None
    try:
        resultado = json.loads(linha["result_json"])
        v("SQL", f"{video_key}: metadados salvos encontrados (status {resultado.get('status')})")
        return {
            "result": resultado,
            "audio_path": _resolver_caminho_banco(linha["audio_path"], conexao),
        }
    except (TypeError, json.JSONDecodeError):
        return None


def _salvar_metadados_musica(
    conexao: sqlite3.Connection, video_key: str, dados: dict[str, Any]
) -> None:
    status = dados.get("status")
    if status and _ULTIMO_STATUS_GRAVADO.get(video_key) != status:
        _ULTIMO_STATUS_GRAVADO[video_key] = status
        vv("SQL", f"{video_key}: metadados gravados no banco (status {status})")
    """Mantém uma linha estruturada por faixa no banco dedicado de músicas."""
    resultado_hibrido = isinstance(dados.get("metadata"), dict)
    metadata = dados.get("metadata") if resultado_hibrido else dados
    metadata = metadata or {}
    origem = dados.get("source_youtube") or (dados if not resultado_hibrido else {})
    provenance = dados.get("provenance") or {
        campo: "youtube" for campo in ("title", "artists", "album", "isrc", "thumbnail_url")
        if metadata.get(campo)
    }
    confidence = dados.get("confidence") or {
        campo: "provisoria" for campo in provenance
    }
    diagnostics_by_source = dados.get("diagnostics_by_source") or {}
    artists = metadata.get("artists") or []
    if isinstance(artists, str):
        artists = [artists]
    artists = [str(item).strip() for item in artists if str(item).strip()]
    track_status = str(dados.get("status") or metadata.get("hybrid_status") or "")
    if not track_status:
        # Dados crus do YouTube (antes da identificação) não apagam um status já decidido.
        existente = conexao.execute(
            "SELECT status FROM music.metadata_resolution WHERE video_key = ?", (video_key,)
        ).fetchone()
        track_status = existente[0] if existente else "NAO_VALIDADO"
    values = (
        video_key,
        str(metadata.get("id") or dados.get("video_id") or ""),
        str(metadata.get("title") or ""),
        json.dumps(artists, ensure_ascii=False),
        str(metadata.get("album") or ""),
        str(metadata.get("isrc") or ""),
        float(metadata.get("duration") or 0),
        str(metadata.get("thumbnail_url") or metadata.get("thumbnail") or ""),
        str(metadata.get("cover_url") or ""),
        track_status,
        json.dumps(origem, ensure_ascii=False),
        json.dumps(provenance, ensure_ascii=False),
        json.dumps(confidence, ensure_ascii=False),
        json.dumps(diagnostics_by_source, ensure_ascii=False),
        json.dumps(dados, ensure_ascii=False),
        " | ".join(
            (dados.get("pending_reasons") or []) + (dados.get("observations") or [])
        ) if track_status in STATUS_REVISAO else "",
    )
    conexao.execute(
        """INSERT INTO music.music_tracks
        (video_key, video_id, title, artists_json, album, isrc, duration,
         thumbnail_url, cover_url, metadata_status, source_youtube_json,
         provenance_json, confidence_json, diagnostics_json, metadata_json, review_note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_key) DO UPDATE SET
        video_id=excluded.video_id, title=excluded.title,
        artists_json=excluded.artists_json, album=excluded.album,
        isrc=excluded.isrc, duration=excluded.duration,
        thumbnail_url=excluded.thumbnail_url, cover_url=excluded.cover_url,
        metadata_status=excluded.metadata_status,
        source_youtube_json=excluded.source_youtube_json,
        provenance_json=excluded.provenance_json,
        confidence_json=excluded.confidence_json,
        diagnostics_json=excluded.diagnostics_json,
        metadata_json=excluded.metadata_json, review_note=excluded.review_note,
        updated_at=CURRENT_TIMESTAMP""",
        values,
    )


def salvar_resolucao(
    conexao: sqlite3.Connection,
    video_key: str,
    resultado: dict[str, Any],
    audio_path: str = "",
) -> None:
    conexao.execute(
        """INSERT INTO music.metadata_resolution
        (video_key, video_id, status, result_json, audio_path, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(video_key) DO UPDATE SET
        video_id=excluded.video_id, status=excluded.status,
        result_json=excluded.result_json,
        audio_path=CASE WHEN excluded.audio_path != '' THEN excluded.audio_path
                        ELSE music.metadata_resolution.audio_path END,
        updated_at=CURRENT_TIMESTAMP""",
        (
            video_key,
            str(resultado.get("video_id") or ""),
            str(resultado.get("status") or "PENDENTE"),
            json.dumps(resultado, ensure_ascii=False),
            _caminho_relativo_banco(audio_path, conexao),
        ),
    )
    _salvar_metadados_musica(conexao, video_key, resultado)
    conexao.commit()


def meta_do_resultado_hibrido(resultado: dict[str, Any]) -> dict[str, Any]:
    """Converte o resultado do resolver para os campos usados pelo downloader."""
    campos = resultado.get("metadata") or {}
    origem = resultado.get("source_youtube") or {}
    artistas = campos.get("artists") or origem.get("artists") or []
    if isinstance(artistas, str):
        artistas = [artistas]
    artistas = [str(item).strip() for item in artistas if str(item).strip()]
    titulo = (
        campos.get("title") or origem.get("title") or campos.get("id")
        or "faixa-sem-titulo"
    )
    return {
        "title": str(titulo),
        "artists": artistas,
        "artist": artistas[0] if artistas else "",
        "duration": campos.get("duration") or origem.get("duration") or 0,
        "id": str(campos.get("id") or resultado.get("video_id") or ""),
        "album": str(campos.get("album") or ""),
        "isrc": str(campos.get("isrc") or ""),
        "uploader": str(campos.get("uploader") or origem.get("uploader") or ""),
        "thumbnail": str(campos.get("thumbnail_url") or origem.get("thumbnail_url") or ""),
        "hybrid_status": str(resultado.get("status") or "PENDENTE"),
    }


def chave_canal(dados: dict[str, Any]) -> str:
    """Identificador estável do canal: o ID do YouTube ou, sem ele, o nome limpo."""
    if dados.get("channel_id"):
        return "youtube:" + str(dados["channel_id"])
    nome = _nome_canal_limpo(str(dados.get("uploader") or ""))
    return ("nome:" + nome.casefold()) if nome else ""


def perfil_canal(conexao: sqlite3.Connection | None, chave: str) -> dict[str, Any] | None:
    """O que já se sabe do canal: maioria dos votos entre "artista" e "curador"."""
    if conexao is None or not chave:
        return None
    linhas = conexao.execute(
        "SELECT kind, artist, COUNT(*) AS n FROM music.channel_votes WHERE channel_key = ? "
        "GROUP BY kind, artist ORDER BY n DESC",
        (chave,),
    ).fetchall()
    if not linhas:
        return None
    votos = {"artista": 0, "curador": 0}
    artistas: dict[str, int] = {}
    for linha in linhas:
        votos[linha["kind"]] = votos.get(linha["kind"], 0) + linha["n"]
        if linha["kind"] == "artista" and linha["artist"]:
            artistas[linha["artist"]] = artistas.get(linha["artist"], 0) + linha["n"]
    vv("SQL", f"canal {chave}: votos {votos}")
    if votos["artista"] == votos["curador"]:
        return None
    tipo = "artista" if votos["artista"] > votos["curador"] else "curador"
    principal = max(artistas, key=artistas.get) if artistas else ""
    v("SQL", f"canal já conhecido: {'do artista ' + principal if tipo == 'artista' else 're-upload/curadoria'} "
             f"({votos['artista']} x {votos['curador']} votos)")
    return {"kind": tipo, "artists": [principal] if principal and tipo == "artista" else [], "votos": votos}


def aprender_canal(conexao: sqlite3.Connection | None, video_key: str, resultado: dict[str, Any]) -> None:
    """Quando o artista está confirmado, registra se o canal é do artista ou um re-upload.

    Um voto por vídeo; a decisão é pela maioria, então um erro isolado não estraga
    o que foi aprendido.
    """
    if conexao is None:
        return
    metadata = resultado.get("metadata") or {}
    origem = resultado.get("source_youtube") or {}
    if (resultado.get("provenance") or {}).get("artists") not in FONTES_ARTISTA_CONFIRMADO:
        return
    uploader = str(metadata.get("uploader") or origem.get("uploader") or "")
    if not uploader or re.search(r"-\s*topic\s*$", uploader, re.I):
        return
    nome = _nome_canal_limpo(uploader)
    chave = chave_canal({"channel_id": metadata.get("channel_id") or origem.get("channel_id"), "uploader": uploader})
    artistas = [str(a) for a in metadata.get("artists") or []]
    if not nome or not chave or not artistas:
        return
    artista = next((a for a in artistas if _mesmo_nome(nome, a)), "")
    v("SQL", f"aprendido: canal '{nome}' é " + (f"do artista {artista}" if artista else "re-upload/curadoria"))
    conexao.execute(
        """INSERT INTO music.channel_votes (channel_key, video_key, channel_name, kind, artist)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(channel_key, video_key) DO UPDATE SET
           kind=excluded.kind, artist=excluded.artist, channel_name=excluded.channel_name,
           updated_at=CURRENT_TIMESTAMP""",
        (chave, video_key, nome, "artista" if artista else "curador", artista),
    )


def carregar_artistas_conhecidos(conexao: sqlite3.Connection | None) -> list[str]:
    """Artistas de faixas já resolvidas: ajudam a saber qual lado de "A - B" é o artista."""
    if conexao is None:
        return []
    nomes: dict[str, None] = {}
    for (artistas_json,) in conexao.execute(
        "SELECT artists_json FROM music.music_tracks WHERE metadata_status = 'RESOLVIDO'"
    ).fetchall():
        try:
            for nome in json.loads(artistas_json or "[]"):
                if str(nome).strip():
                    nomes[str(nome).strip()] = None
        except (TypeError, ValueError):
            continue
    v("SQL", f"{len(nomes)} artistas já confirmados carregados da biblioteca")
    import banco_artistas

    if banco_artistas.anexado(conexao):
        try:
            if banco_artistas.vazio(conexao):
                novos = banco_artistas.importar_biblioteca(conexao)
                if novos:
                    v("SQL", f"banco de artistas criado com {novos} artista(s) da biblioteca")
            extras = [n for n in banco_artistas.nomes_conhecidos(conexao) if n not in nomes]
        except sqlite3.Error as exc:
            v("SQL", f"banco de artistas indisponível: {exc}")
            extras = []
        if extras:
            v("SQL", f"{len(extras)} nome(s)/variação(ões) a mais vindos do banco de artistas")
        return list(nomes) + extras
    return list(nomes)


def aprender_artistas(conexao: sqlite3.Connection | None, resultado: dict[str, Any], origem: str) -> None:
    """Artistas de uma faixa confirmada vão para o banco de artistas."""
    import banco_artistas

    if not banco_artistas.anexado(conexao) or (resultado or {}).get("status") != "RESOLVIDO":
        return
    try:
        for nome in (resultado.get("metadata") or {}).get("artists") or []:
            banco_artistas.adicionar(conexao, nome, origem)
        conexao.commit()
    except sqlite3.Error as exc:
        v("SQL", f"não foi possível guardar no banco de artistas: {exc}")


def artista_confiavel(resultado: dict[str, Any] | None) -> bool:
    if not resultado or resultado.get("status") == "PENDENTE":
        return False
    if (resultado.get("provenance") or {}).get("artists") in FONTES_ARTISTA_CONFIRMADO:
        return True
    return float((resultado.get("scores") or {}).get("artists") or 0) >= LIMIAR_CONFIANCA


CAMPOS_DO_ISRC = ("title", "artists", "artist", "album", "album_artists", "date", "label", "track_number",
                  "track_total", "disc_number", "disc_total", "musicbrainz_recording_id",
                  "musicbrainz_release_id", "musicbrainz_release_group_id", "musicbrainz_artist_ids",
                  "cover_url", "cover_source")


def faixa_pelo_isrc(estado: sqlite3.Connection | None, isrc: str, video_id: str) -> dict[str, Any] | None:
    """Faixa já confirmada (RESOLVIDO) com o mesmo ISRC, de outro vídeo."""
    if estado is None or not isrc:
        return None
    try:
        linhas = estado.execute(
            "SELECT video_key FROM music.music_tracks WHERE isrc = ? AND metadata_status = 'RESOLVIDO' "
            "AND video_id != ? ORDER BY updated_at DESC", (isrc, video_id),
        ).fetchall()
    except sqlite3.Error:
        return None
    for linha in linhas:
        salvo = carregar_resolucao(estado, linha[0])
        if salvo and (salvo["result"].get("metadata") or {}).get("artists"):
            v("SQL", f"ISRC {isrc} já está no banco: {linha[0]}")
            return salvo["result"]
    return None


def usar_faixa_do_isrc(resultado: dict[str, Any], conhecida: dict[str, Any], isrc: str) -> None:
    """Mesmo ISRC = mesma gravação: os dados confirmados do banco valem para este vídeo."""
    metadata = resultado.setdefault("metadata", {})
    provenance = resultado.setdefault("provenance", {})
    confidence = resultado.setdefault("confidence", {})
    origem = conhecida.get("metadata") or {}
    for campo in CAMPOS_DO_ISRC:
        if origem.get(campo) not in (None, "", []):
            metadata[campo] = copy.deepcopy(origem[campo])
            provenance[campo] = "banco_isrc"
            confidence[campo] = "confirmada"
    metadata["isrc"], provenance["isrc"], confidence["isrc"] = isrc, "banco_isrc", "confirmada"
    resultado["status"] = "RESOLVIDO"
    resultado["pending_reasons"] = []
    resultado.setdefault("diagnostics", []).append(f"ISRC {isrc} igual ao de uma faixa já confirmada no banco")


def resolver_meta_hibrido(
    meta: dict[str, Any],
    *,
    dados_locais: dict[str, Any] | None = None,
    sem_dados_youtube: bool = False,
    estado: sqlite3.Connection | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    progresso.etapa("identificando artista, álbum e capa")
    # 1º o código da música (ISRC): se outra faixa já confirmada tem o mesmo, é a mesma gravação
    isrc = normalize_isrc(meta.get("isrc") or (dados_locais or {}).get("isrc"))
    conhecida = faixa_pelo_isrc(estado, isrc, str(meta.get("id") or "")) if isrc else None
    resultado = resolver_com_prioridade(
        str(meta.get("id") or ""),
        {} if sem_dados_youtube else meta,
        cache=RESOLVER_CACHE,
        musicbrainz_client=MUSICBRAINZ_CLIENT,
        preferir=PREFERIR,
        revisar=REVISAR,
        canal_conhecido=perfil_canal(estado, chave_canal(meta)),
        artistas_conhecidos=ARTISTAS_CONHECIDOS,
        exigir_isrc=EXIGIR_ISRC,
        exigir_artista_musicbrainz=EXIGIR_ARTISTA_MUSICBRAINZ,
        dados_locais=dados_locais,
        consultar_musicbrainz=conhecida is None,  # já confirmada pelo ISRC: não precisa consultar
    ).to_dict()
    if conhecida:
        usar_faixa_do_isrc(resultado, conhecida, isrc)
    import banco_artistas

    for de, para in banco_artistas.corrigir_resultado(estado, resultado):
        v("SQL", f"banco de artistas: {de} → {para}")
    return meta_do_resultado_hibrido(resultado), resultado


def aplicar_tags_hibridas(audio_path: str, resultado: dict[str, Any]) -> bool:
    """Grava no MP3 as tags completas (faixa, álbum, IDs MusicBrainz), a capa do
    álbum e um marcador de status. Funciona como o Picard: TPE1 com vários
    artistas, TPE2 artista do álbum, TRCK "n/total", UFID da gravação."""
    try:
        from mutagen.id3 import (
            APIC, ID3, ID3NoHeaderError, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TPUB, TRCK, TSRC, TXXX, UFID,
        )
    except ImportError as exc:
        registrar_erro(audio_path, f"mutagen não instalado; tags não gravadas: {exc}")
        return False
    try:
        try:
            tags = ID3(audio_path)
        except ID3NoHeaderError:
            tags = ID3()
        metadata = resultado.get("metadata") or {}
        artistas = metadata.get("artists") or []
        if isinstance(artistas, str):
            artistas = [artistas]
        artistas = [str(a) for a in artistas if str(a).strip()]
        album_artistas = [str(a) for a in metadata.get("album_artists") or [] if str(a).strip()]

        def texto(frame_cls, frame_id: str, valor: Any) -> None:
            tags.delall(frame_id)
            valores = valor if isinstance(valor, list) else [valor]
            valores = [str(v) for v in valores if str(v or "").strip()]
            if valores:
                tags.add(frame_cls(encoding=3, text=valores))

        def extra(desc: str, valor: Any) -> None:
            tags.delall(f"TXXX:{desc}")
            valores = valor if isinstance(valor, list) else [valor]
            valores = [str(v) for v in valores if str(v or "").strip()]
            if valores:
                tags.add(TXXX(encoding=3, desc=desc, text=valores))

        v("Tags", f"gravando no MP3: {os.path.basename(audio_path)}")
        progresso.etapa("gravando tags e capa")
        if verbose.ativo(2):
            for rotulo, valor in (
                ("título", metadata.get("title")), ("artistas", artistas), ("álbum", metadata.get("album")),
                ("artista do álbum", album_artistas), ("data", metadata.get("date")),
                ("faixa", f"{metadata.get('track_number') or ''}/{metadata.get('track_total') or ''}"),
                ("disco", metadata.get("disc_number")), ("gravadora", metadata.get("label")),
                ("ISRC", metadata.get("isrc")), ("MusicBrainz gravação", metadata.get("musicbrainz_recording_id")),
                ("MusicBrainz álbum", metadata.get("musicbrainz_release_id")),
            ):
                if valor and valor != "/":
                    vv("Tags", f"  {rotulo}: {' & '.join(valor) if isinstance(valor, list) else valor}")
        texto(TIT2, "TIT2", metadata.get("title"))
        texto(TPE1, "TPE1", artistas)
        texto(TALB, "TALB", metadata.get("album"))
        texto(TPE2, "TPE2", " & ".join(album_artistas) if album_artistas else (artistas[0] if artistas and metadata.get("album") else ""))
        texto(TDRC, "TDRC", metadata.get("date"))
        numero, total = int(metadata.get("track_number") or 0), int(metadata.get("track_total") or 0)
        texto(TRCK, "TRCK", (f"{numero}/{total}" if total else str(numero)) if numero else "")
        disco, discos = int(metadata.get("disc_number") or 0), int(metadata.get("disc_total") or 0)
        texto(TPOS, "TPOS", (f"{disco}/{discos}" if discos else str(disco)) if disco else "")
        texto(TPUB, "TPUB", metadata.get("label"))
        texto(TSRC, "TSRC", metadata.get("isrc"))
        extra("ARTISTS", artistas if len(artistas) > 1 else "")
        extra("MusicBrainz Album Id", metadata.get("musicbrainz_release_id"))
        extra("MusicBrainz Release Group Id", metadata.get("musicbrainz_release_group_id"))
        extra("MusicBrainz Artist Id", list(metadata.get("musicbrainz_artist_ids") or []))
        tags.delall("UFID:http://musicbrainz.org")
        if metadata.get("musicbrainz_recording_id"):
            tags.add(UFID(owner="http://musicbrainz.org", data=str(metadata["musicbrainz_recording_id"]).encode()))

        # Capa: a do álbum (Cover Art Archive) substitui a thumbnail do vídeo;
        # sem ela, a thumbnail é recortada para ficar quadrada. O marcador evita
        # baixar/recortar de novo a cada execução.
        marcador = str(tags.get("TXXX:PLTUBE_COVER") or "")
        fonte = metadata.get("cover_source") or ""
        url = str(metadata.get("cover_url") or "")
        if marcador and (marcador == url or (marcador == "thumbnail-recortada" and fonte != "cover_art_archive")):
            vv("Capa", "capa já aplicada em execução anterior; nada a fazer")
        if fonte == "cover_art_archive" and url and marcador != url:
            imagem = _baixar_capa(url)
            if imagem:
                tags.delall("APIC")
                mime = "image/png" if imagem[:4] == b"\x89PNG" else "image/jpeg"
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=imagem))
                extra("PLTUBE_COVER", url)
                v("Capa", "capa do álbum aplicada no MP3 (substituiu a thumbnail)")
        elif fonte in {"youtube_thumbnail", "youtube_music_thumbnail"} and not marcador:
            atuais = tags.getall("APIC")
            if atuais:
                quadrada = recortar_quadrado(atuais[0].data)
                if quadrada:
                    tags.delall("APIC")
                    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=quadrada))
                    extra("PLTUBE_COVER", "thumbnail-recortada")
                    v("Capa", "sem capa do álbum: thumbnail do vídeo recortada para quadrada")

        status = str(resultado.get("status") or "PENDENTE")
        extra("PLTUBE_METADATA_STATUS", status)
        extra("PLTUBE_METADATA_NOTE", {
            "PENDENTE": "Metadados provisórios; podem estar incorretos. Consulte: plpython.py review",
            "OBSERVAR": "Confira título/artista/álbum: plpython.py review",
        }.get(status, "Metadados conferidos pelo plpython."))
        extra("PLTUBE_METADATA_REVIEW", " | ".join(
            (resultado.get("pending_reasons") or []) + (resultado.get("observations") or [])
        ))
        for antigo in ("PLTUBE_METADATA_PROVENANCE", "PLTUBE_METADATA_CONFIDENCE"):
            tags.delall(f"TXXX:{antigo}")
        tags.save(audio_path)
        return True
    except Exception as exc:
        registrar_erro(audio_path, f"Não foi possível gravar tags do modelo híbrido: {exc}")
        return False


def exportar_metadados_pendentes(
    conexao: sqlite3.Connection, destino: str | None = None
) -> str:
    """Exporta um CSV legível com uma linha por vídeo ainda sem comprovação clara."""
    destino = destino or os.path.join(WORK_DIR, "metadados_pendentes.csv")
    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    colunas_editaveis = [
        "aplicar", "usar_titulo", "titulo_final", "usar_artistas", "artistas_finais",
        "usar_album", "album_final", "usar_isrc", "isrc_final", "confirmar_manual",
    ]
    colunas = [
        "video_key", "video_id", "url_youtube", "arquivo_audio", "playlists", "status",
        "titulo_youtube", "artistas_youtube", "canal_youtube", "album_youtube", "titulo_usado",
        "artistas_usados", "album_sugerido", "isrc_youtube", "isrc_sugerido",
        "thumbnail_youtube", "capa_sugerida", "motivos_pendencia", "diagnosticos",
        "diagnosticos_youtube", "diagnosticos_local", "diagnosticos_musicbrainz",
        "candidatos_musicbrainz", "proveniencia", "confianca", "observacoes", *colunas_editaveis,
    ]
    edicoes_anteriores: dict[str, dict[str, str]] = {}
    colunas_anteriores: set[str] = set()
    if os.path.isfile(destino):
        try:
            with open(destino, "r", encoding="utf-8-sig", newline="") as arquivo_existente:
                reader = csv.DictReader(arquivo_existente)
                colunas_anteriores = set(reader.fieldnames or [])
                for registro_antigo in reader:
                    chave = (registro_antigo.get("video_key") or "").strip()
                    if not chave and registro_antigo.get("video_id"):
                        chave = "youtube:" + registro_antigo["video_id"].strip()
                    if chave:
                        edicoes_anteriores[chave] = {
                            coluna: registro_antigo.get(coluna, "") or ""
                            for coluna in colunas_editaveis
                            if coluna in colunas_anteriores
                        }
        except (OSError, csv.Error, UnicodeError) as exc:
            print(f"Aviso: não consegui preservar edições anteriores de {destino}: {exc}")
    linhas = conexao.execute(
        """SELECT r.video_key, r.video_id, r.result_json, r.audio_path,
                  v.source_url
           FROM music.metadata_resolution r
           LEFT JOIN video_cache v ON v.video_key = r.video_key
           WHERE r.status IN ('PENDENTE', 'OBSERVAR')
           ORDER BY r.video_id, r.video_key"""
    ).fetchall()
    fd, temporario = tempfile.mkstemp(
        prefix=".metadados-pendentes-",
        suffix=".csv",
        dir=os.path.dirname(os.path.abspath(destino)),
    )
    os.close(fd)
    try:
        with open(temporario, "w", encoding="utf-8-sig", newline="") as arquivo:
            writer = csv.DictWriter(arquivo, fieldnames=colunas)
            writer.writeheader()
            for linha in linhas:
                try:
                    resultado = json.loads(linha["result_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    resultado = {}
                metadata = resultado.get("metadata") or {}
                origem = resultado.get("source_youtube") or {}
                video_key = linha["video_key"]
                playlists = [
                    os.path.splitext(os.path.basename(row[0]))[0]
                    for row in conexao.execute(
                        "SELECT DISTINCT csv_path FROM playlist_items WHERE video_key = ?",
                        (video_key,),
                    ).fetchall()
                ]
                dados = {
                    "video_key": video_key,
                    "video_id": linha["video_id"],
                    "url_youtube": linha["source_url"] or "",
                    "arquivo_audio": _resolver_caminho_banco(linha["audio_path"] or "", conexao),
                    "playlists": " | ".join(playlists),
                    "status": resultado.get("status") or "PENDENTE",
                    "titulo_youtube": origem.get("title") or "",
                    "artistas_youtube": " | ".join(origem.get("artists") or []),
                    "canal_youtube": origem.get("uploader") or "",
                    "album_youtube": origem.get("album") or "",
                    "titulo_usado": metadata.get("title") or "",
                    "artistas_usados": " | ".join(metadata.get("artists") or []),
                    "album_sugerido": metadata.get("album") or "",
                    "isrc_youtube": origem.get("isrc") or "",
                    "isrc_sugerido": metadata.get("isrc") or "",
                    "thumbnail_youtube": origem.get("thumbnail_url") or "",
                    "capa_sugerida": metadata.get("cover_url") or "",
                    "motivos_pendencia": " | ".join(resultado.get("pending_reasons") or []),
                    "diagnosticos": " | ".join(resultado.get("diagnostics") or []),
                    "diagnosticos_youtube": " | ".join(
                        (resultado.get("diagnostics_by_source") or {}).get("youtube") or []
                    ),
                    "diagnosticos_local": " | ".join(
                        (resultado.get("diagnostics_by_source") or {}).get("local") or []
                    ),
                    "diagnosticos_musicbrainz": " | ".join(
                        (resultado.get("diagnostics_by_source") or {}).get("musicbrainz") or []
                    ),
                    "candidatos_musicbrainz": json.dumps(
                        resultado.get("candidates") or [], ensure_ascii=False
                    ),
                    "proveniencia": json.dumps(
                        resultado.get("provenance") or {}, ensure_ascii=False
                    ),
                    "confianca": json.dumps(
                        resultado.get("confidence") or {}, ensure_ascii=False
                    ),
                    "observacoes": " | ".join(resultado.get("observations") or []),
                }
                padroes = {
                    "aplicar": "nao",
                    "usar_titulo": "nao",
                    "titulo_final": metadata.get("title") or "",
                    "usar_artistas": "nao",
                    "artistas_finais": " | ".join(metadata.get("artists") or []),
                    "usar_album": "nao",
                    "album_final": metadata.get("album") or "",
                    "usar_isrc": "nao",
                    "isrc_final": metadata.get("isrc") or "",
                    "confirmar_manual": "nao",
                }
                antigas = edicoes_anteriores.get(video_key, {})
                for coluna in colunas_editaveis:
                    dados[coluna] = antigas.get(coluna, padroes[coluna])
                writer.writerow(dados)
        os.replace(temporario, destino)
    finally:
        if os.path.exists(temporario):
            os.unlink(temporario)
    return destino


def normalizar_artistas(
    info: dict[str, Any], permitir_uploader: bool = True
) -> list[str]:
    """Obtém os artistas dos metadados do YouTube, preservando nomes de bandas.

    Lista (YouTube Music): cada item é um artista; só "feat." separa. Texto solto
    ("A, B & C"): passa por dividir_artistas, que conhece "Earth, Wind & Fire" e as
    duplas ("Jorge & Mateus") e os artistas já confirmados na biblioteca."""
    candidatos = info.get("artists")
    if isinstance(candidatos, str):
        candidatos = dividir_artistas(candidatos, ARTISTAS_CONHECIDOS)
    if not candidatos:
        artista = info.get("artist")
        candidatos = artista if isinstance(artista, list) else dividir_artistas(artista, ARTISTAS_CONHECIDOS)

    artistas: list[str] = []
    for candidato in candidatos:
        if isinstance(candidato, dict):
            candidato = candidato.get("name") or candidato.get("artist") or ""
        if not isinstance(candidato, str):
            continue
        # Só separa créditos explícitos de participação; não divide nomes de bandas
        # como "Earth, Wind & Fire" nem nomes próprios que contenham '&'.
        partes = re.split(r"\s+(?:feat(?:uring)?\.?|ft\.?)\s+", candidato, flags=re.I)
        for parte in partes:
            nome = parte.strip(" \t,;-")
            if nome and nome.casefold() not in {item.casefold() for item in artistas}:
                artistas.append(nome)

    if not artistas and permitir_uploader:
        for chave in ("uploader", "channel", "creator"):
            nome = info.get(chave)
            if isinstance(nome, str) and nome.strip():
                artistas.append(nome.strip())
                break
    return artistas


def extrair_metadados(url: str, csv_file: str = "") -> dict[str, Any] | None:
    if yt_dlp is None:
        registrar_erro(url, "Dependência yt-dlp não instalada", csv_file)
        print("Instale as dependências com: pip install -r requirements.txt")
        return None

    def obter_info() -> dict[str, Any]:
        logger = MeuLogger(url, csv_file)
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "logger": logger,
            "ignoreerrors": False,
            "noplaylist": True,
            "sleep_interval_requests": PAUSA_ENTRE_REQUISICOES,
            **COOKIES_YTDLP,
        }
        if ESPERAR_BLOQUEIO:
            # Deixa o backoff do programa controlar as novas tentativas em vez
            # de repetir imediatamente dentro do yt-dlp.
            ydl_opts.update({"retries": 0, "extractor_retries": 0, "fragment_retries": 0})
        ydl_opts["verbose"] = verbose.ativo(3)
        ydl_opts["no_warnings"] = not verbose.ativo(2)  # avisos do yt-dlp a partir do -vv
        aguardar_vez_youtube()
        vv("YouTube", f"yt-dlp: extract_info (sem baixar) {url}")
        progresso.etapa("lendo metadados no YouTube")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise RuntimeError("yt-dlp não retornou metadados. " + " ".join(logger.errors))
        guardar_info_youtube(url, info)
        return info

    def montar_metadados() -> dict[str, Any]:
        info = obter_info()
        artistas = normalizar_artistas(
            info, permitir_uploader=not MODELO_HIBRIDO
        )
        if not artistas and not MODELO_HIBRIDO:
            raise RuntimeError("O YouTube não informou o artista deste vídeo.")
        descricao = str(info.get("description") or "")
        if verbose.ativo(2):
            vv("YouTube", f"  título do vídeo: {info.get('title')!r}")
            vv("YouTube", f"  canal: {info.get('uploader') or info.get('channel')!r} (id {info.get('channel_id')}, "
                          f"verificado: {bool(info.get('channel_is_verified'))})")
            vv("YouTube", f"  crédito do YouTube: artistas {artistas or '-'} | faixa {info.get('track') or '-'} | "
                          f"álbum {info.get('album') or '-'} | lançamento {info.get('release_date') or info.get('release_year') or '-'}")
            vv("YouTube", f"  duração {info.get('duration') or 0}s | {len(info.get('thumbnails') or [])} thumbnails | "
                          f"descrição {len(descricao)} caracteres")
            ytm = ler_descricao_youtube_music(descricao)
            if ytm:
                vv("YouTube", f"  descrição do YouTube Music: {ytm}")
            if _DESCRICAO_REUPLOAD.search(descricao):
                vv("YouTube", "  descrição tem aviso típico de re-upload")
        return {
            "title": info.get("title") or info.get("id") or "musica-sem-titulo",
            "artists": artistas,
            "artist": artistas[0] if artistas else "",
            "fonte_artistas": "youtube",
            "track": info.get("track") or "",
            "duration": info.get("duration") or 0,
            "id": str(info.get("id") or hashlib.sha1(url.encode("utf-8")).hexdigest()[:11]),
            "album": info.get("album") or info.get("release") or "",
            "isrc": info.get("isrc") or info.get("isrcs") or "",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "channel_id": info.get("channel_id") or "",
            "channel_is_verified": bool(info.get("channel_is_verified")),
            "release_date": info.get("release_date") or "",
            "release_year": info.get("release_year") or "",
            # Só o que importa da descrição; o texto inteiro não vai para o banco.
            "youtube_music": ler_descricao_youtube_music(descricao),
            "descricao_reupload": bool(_DESCRICAO_REUPLOAD.search(descricao)),
            "thumbnail": info.get("thumbnail") or "",
            "thumbnails": info.get("thumbnails") or [],
        }

    try:
        return executar_com_retentativas(url, csv_file, montar_metadados)
    except BloqueioTemporarioYouTube:
        raise


def guardar_info_youtube(url: str, info: dict[str, Any]) -> None:
    """Guarda (só na memória) o que o yt-dlp leu, para o download não consultar de novo."""
    if yt_dlp is None:
        return
    try:
        INFO_YOUTUBE[url] = yt_dlp.YoutubeDL.sanitize_info(dict(info), remove_private_keys=True)
    except Exception:
        return
    while len(INFO_YOUTUBE) > MAX_INFO_YOUTUBE:
        INFO_YOUTUBE.pop(next(iter(INFO_YOUTUBE)))


def _tamanho(n: float | None) -> str:
    if not n:
        return "?"
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.0f} {unidade}" if unidade in ("B", "KB") else f"{n:.1f} {unidade}"
        n /= 1024
    return "?"


def _gancho_download(d: dict[str, Any]) -> None:
    """Progresso do download de cada música, vindo do yt-dlp (para a barra -P)."""
    if d.get("status") == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        baixado = d.get("downloaded_bytes") or 0
        if total:
            progresso.etapa(f"baixando {100 * baixado / total:.0f}% de {_tamanho(total)}")
        else:
            progresso.etapa(f"baixando {_tamanho(baixado)}")
    elif d.get("status") == "finished":
        progresso.etapa("download concluído")


_ETAPAS_POS = {
    "ExtractAudio": "convertendo para MP3", "EmbedThumbnail": "embutindo a thumbnail",
    "Metadata": "gravando metadados", "MoveFiles": "movendo arquivo",
}


def _gancho_pos_processamento(d: dict[str, Any]) -> None:
    if d.get("status") == "started":
        nome = str(d.get("postprocessor") or "")
        progresso.etapa(next((t for chave, t in _ETAPAS_POS.items() if chave in nome), "finalizando"))


def baixar_reaproveitando(ydl: Any, url: str) -> None:
    """Baixa usando os metadados já lidos, como o `yt-dlp --load-info-json`.

    Se os links guardados tiverem expirado, o próprio yt-dlp volta a consultar
    pela URL. Sem metadados guardados, faz o download normal.
    """
    info = INFO_YOUTUBE.pop(url, None)
    if not info:
        aguardar_vez_youtube()
        v("YouTube", "baixando pela URL (metadados não estavam guardados)")
        ydl.download([url])
        return
    v("YouTube", "baixando com os metadados já lidos (sem nova consulta ao YouTube)")
    fd, caminho = tempfile.mkstemp(prefix=".pltube-info-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            json.dump(info, arquivo, ensure_ascii=False)
        ydl.download_with_info_file(caminho)
    finally:
        try:
            os.unlink(caminho)
        except OSError:
            pass


def nome_seguro(valor: str, fallback: str = "sem-nome") -> str:
    """Remove caracteres que poderiam criar caminhos inválidos ou escapar da pasta."""
    valor = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(valor))
    valor = valor.strip().strip(".")
    if valor in {"", ".", ".."}:
        return fallback
    return valor[:180]


def pasta_do_artista(artista: str) -> str:
    """Nome da pasta do artista. Se já existe uma pasta para o mesmo nome com outra
    grafia ("WHSPRS" e "Whsprs", "kennyoung" e "Kennyoung"), usa a que já existe,
    para não espalhar o mesmo artista em várias pastas."""
    nome = nome_seguro(artista, "Artista Desconhecido")
    chave = "".join(_normalizar_texto(nome).split()) if "_normalizar_texto" in globals() else ""
    if not chave or not os.path.isdir(SONGS_DIR):
        return nome
    if os.path.isdir(os.path.join(SONGS_DIR, nome)):
        return nome
    for existente in sorted(os.listdir(SONGS_DIR)):
        if existente.startswith(("_", ".")) or not os.path.isdir(os.path.join(SONGS_DIR, existente)):
            continue
        if "".join(_normalizar_texto(existente).split()) == chave:
            return existente
    return nome


def criar_link_artista(arquivo_original: str, artista: str) -> bool:
    """Cria um atalho ao MP3 físico, sem copiar os dados da música."""
    pasta_artista = os.path.join(SONGS_DIR, pasta_do_artista(artista))
    destino = os.path.join(pasta_artista, os.path.basename(arquivo_original))
    os.makedirs(pasta_artista, exist_ok=True)

    if os.path.lexists(destino):
        if os.path.realpath(destino) == os.path.realpath(arquivo_original):
            return True
        # Não apaga nem substitui arquivos que já pertencem ao usuário.
        print(f"Aviso: não criei o link; já existe um arquivo diferente em {destino}")
        return False

    alvo_relativo = os.path.relpath(arquivo_original, start=pasta_artista)
    try:
        os.symlink(alvo_relativo, destino)
        if artista.startswith("_"):
            v("Arquivo", f"atalho na pasta de conferência: {os.path.relpath(destino, SONGS_DIR)}")
        else:
            v("Arquivo", f"atalho para o artista secundário: {os.path.relpath(destino, SONGS_DIR)}")
        return True
    except OSError as exc:
        # Em sistemas sem permissão para symlinks, hard links também não duplicam
        # o conteúdo; se não forem suportados, informa a falha no log.
        try:
            os.link(arquivo_original, destino)
            return True
        except OSError as hardlink_exc:
            registrar_erro(arquivo_original, f"Falha criando link: {exc}; {hardlink_exc}")
            return False


def mover_para_artista_principal(path: str, artistas: list[str]) -> str:
    """Move um MP3 já baixado para a pasta do artista principal confirmado.

    Só mexe em arquivos que estão diretamente em <songs>/<pasta>/; nunca
    sobrescreve nada. Atalhos antigos que ficaram quebrados são removidos e os
    novos são criados depois, pelo fluxo normal.
    """
    if not path or not artistas or not os.path.isfile(path) or os.path.islink(path):
        return path
    pasta = os.path.dirname(os.path.abspath(path))
    if os.path.realpath(os.path.dirname(pasta)) != os.path.realpath(SONGS_DIR):
        return path
    destino_nome = pasta_do_artista(artistas[0])
    atual = os.path.basename(pasta)
    if atual == destino_nome or destino_nome.casefold() in {"_pendentes", "_observar", "_library"}:
        return path
    nova_pasta = os.path.join(SONGS_DIR, destino_nome)
    novo = os.path.join(nova_pasta, os.path.basename(path))
    if os.path.lexists(novo):
        if os.path.islink(novo) and os.path.realpath(novo) == os.path.realpath(path):
            os.unlink(novo)  # era só o atalho de colaborador; o arquivo real vai para lá
        else:
            return path
    try:
        os.makedirs(nova_pasta, exist_ok=True)
        shutil.move(path, novo)
    except OSError as exc:
        registrar_erro(path, f"Não foi possível mover para a pasta do artista principal: {exc}")
        return path
    nome_arquivo = os.path.basename(novo)
    for entrada in os.scandir(SONGS_DIR):
        candidato = os.path.join(entrada.path, nome_arquivo)
        if entrada.is_dir(follow_symlinks=False) and os.path.islink(candidato) and not os.path.exists(candidato):
            os.unlink(candidato)
    try:
        os.rmdir(pasta)  # só remove se ficou vazia
    except OSError:
        pass
    print(f"Movido para a pasta do artista principal: {os.path.relpath(novo, SONGS_DIR)}")
    return novo


def limpar_atalhos_conferencia(path: str) -> None:
    """Tira o atalho do MP3 das pastas _observar/_pendentes (a faixa foi conferida)."""
    if not path:
        return
    for pasta in ("_observar", "_pendentes"):
        atalho = os.path.join(SONGS_DIR, pasta, os.path.basename(path))
        if os.path.islink(atalho):
            os.unlink(atalho)
            v("Arquivo", f"atalho removido de {pasta}/ (faixa conferida)")


def _baixar_capa(url: str) -> bytes | None:
    if url in CAPAS_BAIXADAS:
        vv("Capa", "capa já baixada nesta execução (mesmo álbum)")
        return CAPAS_BAIXADAS[url]
    v("Capa", f"baixando a capa do álbum: {url}")
    dados = None
    cliente = MUSICBRAINZ_CLIENT or MusicBrainzClient()
    try:
        dados = cliente.download_image(url)
    except Exception as exc:
        registrar_erro(url, f"Não foi possível baixar a capa do álbum: {exc}")
    CAPAS_BAIXADAS[url] = dados
    if dados:
        v("Capa", f"capa baixada ({len(dados) // 1024} KB)")
    return dados


def recortar_quadrado(imagem: bytes) -> bytes | None:
    """Recorta o centro da thumbnail 16:9 do YouTube para ficar quadrada como uma capa."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not imagem:
        return None
    try:
        processo = subprocess.run(
            [ffmpeg, "-v", "error", "-i", "pipe:0",
             "-vf", "crop='if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'",
             "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "2", "pipe:1"],
            input=imagem, capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    saida = processo.stdout
    vv("Capa", f"thumbnail recortada: {len(imagem) // 1024} KB → {len(saida) // 1024} KB "
               f"(ffmpeg código {processo.returncode})")
    return saida if processo.returncode == 0 and saida[:3] == b"\xff\xd8\xff" else None


def baixar_musica(
    url: str,
    artist: str | list[str],
    title: str,
    video_id: str = "",
    csv_file: str = "",
    hybrid_result: dict[str, Any] | None = None,
) -> str | None:
    """Salva no artista principal e cria links de revisão/colaboradores conforme o caso."""
    if yt_dlp is None:
        registrar_erro(url, "Dependência yt-dlp não instalada", csv_file)
        print("Instale as dependências com: pip install -r requirements.txt")
        return None

    artists = artist if isinstance(artist, list) else [artist]
    artists = [nome.strip() for nome in artists if isinstance(nome, str) and nome.strip()]
    if not artists:
        artists = ["Artista Desconhecido"]
    artists = list(dict.fromkeys(artists))

    safe_title = nome_seguro(title)
    safe_id = nome_seguro(video_id, "sem-id") if video_id else "sem-id"
    filename = f"{safe_title} [{safe_id}].mp3"

    # A primeira posição dos créditos disponíveis define a pasta física, mesmo
    # quando provisórios. Se nenhum artista veio do vídeo/base, usa a pasta
    # explícita de fallback; o uploader nunca é promovido no modo híbrido.
    pendente = bool(hybrid_result and hybrid_result.get("status") == "PENDENTE")
    artista_principal = pasta_do_artista(artists[0])
    if artista_principal.casefold() in {"_pendentes", "_observar", "_library"}:
        artista_principal = "Artista Desconhecido"
    pasta_download = os.path.join(SONGS_DIR, artista_principal)
    os.makedirs(pasta_download, exist_ok=True)

    filepath_mp3 = os.path.join(pasta_download, filename)
    if not os.path.isfile(filepath_mp3):
        if os.path.lexists(filepath_mp3):
            registrar_erro(url, f"O caminho de destino não é um arquivo regular: {filepath_mp3}", csv_file)
            return None

        output_template = os.path.join(pasta_download, f"{safe_title} [{safe_id}].%(ext)s")
        def download_once() -> bool:
            logger = MeuLogger(url, csv_file)
            ydl_opts = {
                "format": "bestaudio/best",
                "writethumbnail": True,
                "outtmpl": output_template,
                "noplaylist": True,
                "logger": logger,
                "ignoreerrors": False,
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                    {"key": "EmbedThumbnail"},
                    {"key": "FFmpegMetadata"},
                ],
            }
            ydl_opts["sleep_interval_requests"] = PAUSA_ENTRE_REQUISICOES
            ydl_opts["progress_hooks"] = [_gancho_download]
            ydl_opts["postprocessor_hooks"] = [_gancho_pos_processamento]
            ydl_opts["verbose"] = verbose.ativo(3)
            ydl_opts["no_warnings"] = not verbose.ativo(2)
            ydl_opts.update(COOKIES_YTDLP)
            if ESPERAR_BLOQUEIO:
                ydl_opts.update({"retries": 0, "extractor_retries": 0, "fragment_retries": 0})
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                baixar_reaproveitando(ydl, url)
            if not os.path.isfile(filepath_mp3):
                raise RuntimeError(
                    "O download não produziu o arquivo esperado. " + " ".join(logger.errors)
                )
            return True

        try:
            executar_com_retentativas(url, csv_file, download_once)
        except BloqueioTemporarioYouTube:
            raise

        if not os.path.isfile(filepath_mp3):
            return None

    if hybrid_result:
        aplicar_tags_hibridas(filepath_mp3, hybrid_result)
    if pendente:
        # `_pendentes` é apenas uma entrada de navegação; o MP3 físico fica
        # junto ao artista e o link não cria uma segunda cópia do áudio.
        criar_link_artista(filepath_mp3, "_pendentes")
    elif ARTIST_LINKS:
        for nome in artists:
            criar_link_artista(filepath_mp3, nome)
    return filepath_mp3


def escrever_playlist(
    m3u_path: str,
    entradas: list[tuple[str, str, int | float, str]],
    csv_file: str,
    append: bool = False,
) -> None:
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    existing_paths: set[str] = set()
    playlist_exists = os.path.isfile(m3u_path)
    if append and playlist_exists:
        try:
            with open(m3u_path, "r", encoding="utf-8") as arquivo:
                existing_paths = {
                    linha.strip() for linha in arquivo
                    if linha.strip() and not linha.lstrip().startswith("#")
                }
        except OSError as exc:
            registrar_erro(m3u_path, str(exc), csv_file)
            return

    modo = "a" if append else "w"
    try:
        with open(m3u_path, modo, encoding="utf-8", newline="\n") as arquivo:
            precisa_cabecalho = not append or not playlist_exists or os.path.getsize(m3u_path) == 0
            if precisa_cabecalho:
                arquivo.write("#EXTM3U\n")
            for title, artist_names, duration, path in entradas:
                if os.path.isabs(path):
                    path = os.path.relpath(path, start=PLAYLISTS_DIR)
                if append and path in existing_paths:
                    continue
                arquivo.write(f"#EXTINF:{duration:g},{artist_names} - {title}\n")
                arquivo.write(f"{path}\n")
                existing_paths.add(path)
    except OSError as exc:
        registrar_erro(m3u_path, str(exc), csv_file)
        print(f"Falha gravando playlist {m3u_path}: {exc}")


def progresso_csv(
    estado: sqlite3.Connection,
    csv_path: str,
    assinatura: str,
    next_row: int,
    complete: bool = False,
    total_rows: int = 0,
) -> None:
    estado.execute(
        """INSERT INTO csv_progress (csv_path, signature, next_row, complete, total_rows)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(csv_path) DO UPDATE SET signature=excluded.signature,
        next_row=excluded.next_row, complete=excluded.complete, total_rows=excluded.total_rows""",
        (csv_path, assinatura, next_row, int(complete), total_rows),
    )
    estado.commit()


def meta_do_registro(registro: dict[str, Any]) -> dict[str, Any]:
    artistas = registro.get("artists") or ["Artista Desconhecido"]
    return {
        "title": registro.get("title") or "Faixa sem título",
        "artists": artistas,
        "artist": artistas[0],
        "duration": registro.get("duration") or 0,
        "id": registro.get("video_id") or "",
        "fonte_artistas": "cache",
    }


def contar_linhas_csv(csv_file: str) -> int:
    """Quantos vídeos (linhas sem o cabeçalho) o CSV tem; usado no total da barra."""
    caminho = csv_file if os.path.isabs(csv_file) else os.path.join(BASE_DIR, csv_file)
    try:
        with open(caminho, "r", encoding="utf-8-sig", newline="") as arquivo:
            return max(0, sum(1 for linha in csv.reader(arquivo) if linha) - 1)
    except OSError:
        return 0


def processar_csv(csv_file: str, append: bool = False, **opcoes: Any) -> bool:
    """Processa ou retoma um CSV, avisando a barra de progresso (-P) do início e do fim."""
    progresso.iniciar_csv(os.path.basename(csv_file))
    continuar = _processar_csv(csv_file, append, **opcoes)
    if continuar:
        progresso.fim_csv(contar_linhas_csv(csv_file))
    return continuar


def _processar_csv(
    csv_file: str,
    append: bool = False,
    *,
    estado: sqlite3.Connection | None = None,
    reprocess: bool = False,
    indice_local: dict[str, str] | None = None,
) -> bool:
    """Processa ou retoma um CSV; o estado opcional evita consultas repetidas ao YouTube."""
    csv_path = os.path.abspath(csv_file if os.path.isabs(csv_file) else os.path.join(BASE_DIR, csv_file))
    if not os.path.isfile(csv_path):
        registrar_erro(csv_path, "Arquivo CSV não encontrado", csv_file)
        print(f"CSV não encontrado: {csv_path}")
        return True

    playlist_name = os.path.splitext(os.path.basename(csv_file))[0]
    m3u_path = os.path.join(PLAYLISTS_DIR, f"{playlist_name}.m3u")
    playlist_entries: list[tuple[str, str, int | float, str]] = []
    state_key = _chave_csv(csv_path)
    assinatura = assinatura_csv(csv_path) if estado is not None else ""
    start_row = 0

    if estado is not None:
        salvo = estado.execute(
            "SELECT * FROM csv_progress WHERE csv_path = ?", (state_key,)
        ).fetchone()
        if reprocess or not salvo or salvo["signature"] != assinatura:
            estado.execute("DELETE FROM playlist_items WHERE csv_path = ?", (state_key,))
            progresso_csv(estado, state_key, assinatura, 0, False, 0)
            start_row = 0
        else:
            start_row = int(salvo["next_row"])
            if salvo["complete"]:
                itens_salvos = listar_itens_playlist(estado, state_key)
                precisa_resolver_hibrido = False
                if MODELO_HIBRIDO:
                    sem_resolucao = estado.execute(
                        """SELECT 1 FROM playlist_items p
                           LEFT JOIN metadata_resolution r ON r.video_key = p.video_key
                           WHERE p.csv_path = ? AND r.video_key IS NULL LIMIT 1""",
                        (state_key,),
                    ).fetchone()
                    precisa_resolver_hibrido = bool(sem_resolucao)
                if (
                    all(os.path.isfile(item[3]) for item in itens_salvos)
                    and not precisa_resolver_hibrido
                ):
                    print(f"CSV já concluído; usando cache local sem reler: {csv_file}")
                    escrever_playlist(m3u_path, itens_salvos, csv_file, append)
                    return True
                if precisa_resolver_hibrido:
                    print(f"Aplicando o modelo híbrido a itens já baixados de {csv_file}")
                    start_row = 0
                else:
                    print(f"Um arquivo do cache não existe; retomando o CSV: {csv_file}")
                    estado.execute("DELETE FROM playlist_items WHERE csv_path = ?", (state_key,))
                    progresso_csv(estado, state_key, assinatura, 0, False, 0)
                    start_row = 0

    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as arquivo:
            reader = csv.reader(arquivo)
            next(reader, None)
            total_rows = 0
            bloqueado = False
            for row_index, row in enumerate(reader):
                total_rows = row_index + 1
                progresso.linha(row_index, row[1] if len(row) > 2 else "")
                if row_index < start_row:
                    continue  # Já foi checkpointado: não consultar yt-dlp de novo.

                numero_linha = row_index + 2
                video_key = ""
                url = ""
                meta: dict[str, Any] | None = None
                dados_locais: dict[str, Any] | None = None
                sem_dados_youtube = False
                music_path: str | None = None
                hybrid_result: dict[str, Any] | None = None

                if not row or not (-len(row) <= ROW < len(row)):
                    registrar_erro("", f"Coluna {ROW} ausente na linha {numero_linha}", csv_file)
                else:
                    url = row[ROW].strip()
                    if url.lower().startswith(("http://", "https://")):
                        video_key, video_id = obter_chave_video(url)
                        registro = ler_video_cache(estado, video_key) if estado is not None else None
                        if estado is not None and not estado.execute(
                            "SELECT 1 FROM download_jobs WHERE video_key = ?", (video_key,)
                        ).fetchone():
                            registrar_job_download(
                                estado, video_key, url, "PENDENTE", video_id=video_id
                            )
                            estado.commit()

                        if registro and registro["status"] == "failed" and not reprocess:
                            print(f"Falha já registrada localmente; ignorando sem nova consulta: {url}")
                        else:
                            if (
                                registro
                                and registro["status"] == "complete"
                                and registro["path"]
                                and os.path.isfile(registro["path"])
                            ):
                                meta = meta_do_registro(registro)
                                music_path = registro["path"]
                                dados_locais = metadados_de_arquivo_local(
                                    music_path, video_id
                                ).get("local_metadata")
                                resolucao_salva = (
                                    carregar_resolucao(estado, video_key)
                                    if MODELO_HIBRIDO and estado is not None else None
                                )
                                precisa_youtube = MODELO_HIBRIDO and reprocess and not (
                                    resolucao_salva and (
                                        resolucao_salva["result"].get("status") not in STATUS_REVISAO
                                        or (resolucao_salva["result"].get("manual_review") or {}).get("fields")
                                        or (resolucao_salva["result"].get("source_youtube") or {}).get("uploader")
                                    )
                                )
                                if precisa_youtube:
                                    # Downloads antigos não guardaram canal/descrição; para
                                    # identificar melhor, consulta só os metadados (sem baixar).
                                    print(f"Consultando metadados para identificar melhor: {url}")
                                    try:
                                        novo = extrair_metadados(url, csv_file)
                                    except BloqueioTemporarioYouTube:
                                        print("Interrompendo esta playlist para evitar novas requisições durante o bloqueio.")
                                        bloqueado = True
                                        break
                                    if novo:
                                        meta = novo
                                else:
                                    print(f"Já está baixada; reutilizando sem acessar o YouTube: {url}")
                            elif video_id and indice_local and os.path.isfile(indice_local.get(video_id, "")):
                                music_path = indice_local[video_id]
                                meta = metadados_de_arquivo_local(
                                    music_path, video_id, row[1] if len(row) > 1 else ""
                                )
                                dados_locais = meta.get("local_metadata")
                                sem_dados_youtube = True
                                if estado is not None:
                                    salvar_video_cache(
                                        estado, video_key, url, "complete", meta, music_path
                                    )
                            else:
                                if registro and registro.get("title"):
                                    meta = meta_do_registro(registro)
                                else:
                                    print(f"Consultando metadados: {url}")
                                    try:
                                        meta = extrair_metadados(url, csv_file)
                                    except BloqueioTemporarioYouTube:
                                        print("Interrompendo esta playlist para evitar novas requisições durante o bloqueio.")
                                        bloqueado = True
                                        break
                                    if meta and estado is not None:
                                        salvar_video_cache(estado, video_key, url, "metadata", meta)
                                        registrar_job_download(
                                            estado, video_key, url, "METADADOS",
                                            video_id=meta.get("id") or video_id,
                                        )
                                        _salvar_metadados_musica(estado, video_key, meta)
                                        estado.commit()
                                    elif estado is not None:
                                        salvar_video_cache(
                                            estado, video_key, url, "failed",
                                            error="Não foi possível obter os metadados",
                                        )
                                        registrar_job_download(
                                            estado, video_key, url, "ERRO", video_id=video_id,
                                            error="Não foi possível obter os metadados",
                                        )

                            if meta and MODELO_HIBRIDO:
                                salvo_hibrido = (
                                    carregar_resolucao(estado, video_key)
                                    if estado is not None else None
                                )
                                tentar_novamente = bool(
                                    salvo_hibrido and reprocess
                                    and salvo_hibrido["result"].get("status") in STATUS_REVISAO
                                    and not (salvo_hibrido["result"].get("manual_review") or {}).get("fields")
                                )
                                if salvo_hibrido and not tentar_novamente:
                                    hybrid_result = salvo_hibrido["result"]
                                    meta = meta_do_resultado_hibrido(hybrid_result)
                                    caminho_salvo = salvo_hibrido.get("audio_path")
                                    if (
                                        not music_path and caminho_salvo
                                        and os.path.isfile(caminho_salvo)
                                    ):
                                        music_path = caminho_salvo
                                else:
                                    if music_path and not dados_locais:
                                        dados_locais = metadados_de_arquivo_local(
                                            music_path, video_id
                                        ).get("local_metadata")
                                    meta, hybrid_result = resolver_meta_hibrido(
                                        meta,
                                        dados_locais=dados_locais,
                                        sem_dados_youtube=sem_dados_youtube,
                                        estado=estado,
                                    )
                                    if hybrid_result.get("status") == "RESOLVIDO":
                                        for nome in meta.get("artists") or []:
                                            if nome not in ARTISTAS_CONHECIDOS:
                                                ARTISTAS_CONHECIDOS.append(nome)
                                        aprender_artistas(estado, hybrid_result, "download")
                                    if estado is not None:
                                        aprender_canal(estado, video_key, hybrid_result)
                                        salvar_resolucao(estado, video_key, hybrid_result)
                                        if hybrid_result.get("status") in ("PENDENTE", "OBSERVAR") \
                                                and video_key not in FAIXAS_PARA_PERGUNTAR:
                                            FAIXAS_PARA_PERGUNTAR.append(video_key)
                                if estado is not None and hybrid_result:
                                    _salvar_metadados_musica(estado, video_key, hybrid_result)

                            if meta and estado is not None and not MODELO_HIBRIDO:
                                _salvar_metadados_musica(estado, video_key, meta)

                            if meta and music_path and hybrid_result:
                                if artista_confiavel(hybrid_result):
                                    music_path = mover_para_artista_principal(
                                        music_path, meta.get("artists") or []
                                    )
                                aplicar_tags_hibridas(music_path, hybrid_result)

                            if (
                                meta and hybrid_result and MODELO_HIBRIDO
                                and estado is not None and not music_path
                            ):
                                salvar_video_cache(
                                    estado, video_key, url, "metadata", meta
                                )
                                estado.commit()

                            if meta and not music_path:
                                try:
                                    if estado is not None:
                                        registrar_job_download(
                                            estado, video_key, url, "BAIXANDO",
                                            video_id=meta.get("id") or video_id,
                                        )
                                        estado.commit()
                                    if MODELO_HIBRIDO:
                                        music_path = baixar_musica(
                                            url, meta["artists"], meta["title"], meta["id"],
                                            csv_file, hybrid_result=hybrid_result,
                                        )
                                    else:
                                        if estado is not None:
                                            salvar_video_cache(
                                                estado, video_key, url, "metadata", meta
                                            )
                                            estado.commit()
                                        music_path = baixar_musica(
                                            url, meta["artists"], meta["title"], meta["id"], csv_file
                                        )
                                except BloqueioTemporarioYouTube:
                                    print("Interrompendo esta playlist para evitar novas requisições durante o bloqueio.")
                                    bloqueado = True
                                    break

                            if music_path and hybrid_result:
                                pasta_revisao = {
                                    "PENDENTE": "_pendentes", "OBSERVAR": "_observar",
                                }.get(hybrid_result.get("status"))
                                if pasta_revisao:
                                    criar_link_artista(music_path, pasta_revisao)

                            if music_path and estado is not None and meta:
                                salvar_video_cache(
                                    estado, video_key, url, "complete", meta, music_path
                                )
                                registrar_job_download(
                                    estado, video_key, url, "CONCLUIDO",
                                    video_id=meta.get("id") or video_id, path=music_path,
                                )
                                if hybrid_result:
                                    salvar_resolucao(
                                        estado, video_key, hybrid_result, music_path
                                    )
                            elif not music_path and estado is not None and meta:
                                salvar_video_cache(
                                    estado, video_key, url, "failed", meta,
                                    error="Não foi possível baixar o áudio",
                                )
                                registrar_job_download(
                                    estado, video_key, url, "ERRO",
                                    video_id=meta.get("id") or video_id,
                                    error="Não foi possível baixar o áudio",
                                )

                            if music_path and estado is not None and meta:
                                # Inclui também reutilização de um MP3 local existente.
                                registrar_job_download(
                                    estado, video_key, url, "CONCLUIDO",
                                    video_id=meta.get("id") or video_id, path=music_path,
                                )

                        if (
                            music_path and meta and ARTIST_LINKS
                            and not (hybrid_result and hybrid_result.get("status") == "PENDENTE")
                        ):
                            for artista in meta["artists"]:
                                criar_link_artista(music_path, artista)
                        if music_path and meta and estado is not None:
                            salvar_item_playlist(
                                estado, state_key, row_index, video_key, meta, music_path
                            )

                if estado is not None:
                    progresso_csv(estado, state_key, assinatura, row_index + 1, False, total_rows)
                if estado is None and music_path and meta:
                    relative_path = os.path.relpath(music_path, start=PLAYLISTS_DIR)
                    playlist_entries.append(
                        (meta["title"], ", ".join(meta["artists"]), meta["duration"], relative_path)
                    )

            if estado is not None and not bloqueado:
                progresso_csv(estado, state_key, assinatura, total_rows, True, total_rows)
    except (OSError, csv.Error, sqlite3.Error) as exc:
        registrar_erro(csv_path, str(exc), csv_file)
        print(f"Falha lendo/processando {csv_path}: {exc}")
        return True

    if estado is not None:
        playlist_entries = listar_itens_playlist(estado, state_key)
    escrever_playlist(m3u_path, playlist_entries, csv_file, append)
    return not bloqueado


def perguntar_faltando(estado: sqlite3.Connection, args: argparse.Namespace) -> None:
    """download --ask: o que não foi confirmado nem pelo banco, nem pela lista de artistas,
    nem pela pontuação do YouTube vira pergunta no fim, só nos campos que faltam."""
    if not FAIXAS_PARA_PERGUNTAR:
        return
    chaves = {
        k for k in FAIXAS_PARA_PERGUNTAR
        if (estado.execute("SELECT status FROM music.metadata_resolution WHERE video_key = ?", (k,))
            .fetchone() or [""])[0] in ("PENDENTE", "OBSERVAR")
    }
    if not chaves:
        return
    if not sys.stdin.isatty():
        print(f"{len(chaves)} faixa(s) desta execução precisam dos seus dados; fora de um terminal não dá "
              "para perguntar. Rode: plpython.py review --answer")
        return
    from comandos import review

    print(f"\n{len(chaves)} faixa(s) desta execução não foram confirmadas (nem pelo banco, nem pela lista "
          "de artistas, nem pelo YouTube). Preencha o que falta; :s pula a faixa, :q termina.\n")
    opcoes = review.criar_parser().parse_args([])
    opcoes.responder = True
    opcoes.chaves = chaves
    opcoes.songs_dir, opcoes.playlist_dir = SONGS_DIR, PLAYLISTS_DIR
    opcoes.ignorar = list(getattr(args, "ignorar", None) or [])
    opcoes.verbose = getattr(args, "verbose", 0)
    review.responder(estado, opcoes)


def executar(args: argparse.Namespace) -> int:
    """Executa o downloader a partir de argumentos já interpretados (usado por plpython.py)."""
    global MUSICBRAINZ_CLIENT, RESOLVER_CACHE
    verificar_diretorios(args)
    MUSICBRAINZ_CLIENT = MusicBrainzClient() if MODELO_HIBRIDO else None
    RESOLVER_CACHE = {}
    criar_diretorios()

    if args.lista:
        csvs = [args.lista] if isinstance(args.lista, str) else list(args.lista)
    else:
        csvs = obter_csvs()

    if not csvs:
        print(f"Nenhum arquivo .csv encontrado em: {BASE_DIR}")
        return 0

    state_path = args.state_file
    if not os.path.isabs(state_path):
        state_path = os.path.join(WORK_DIR, state_path)
    metadata_path = args.metadata_db
    if not os.path.isabs(metadata_path):
        metadata_path = os.path.join(WORK_DIR, metadata_path)
    legacy_path = args.legacy_state_file
    if legacy_path and not os.path.isabs(legacy_path):
        # O banco antigo costumava ficar junto dos CSVs; procura nos dois lugares.
        candidatos = [os.path.join(WORK_DIR, legacy_path), os.path.join(BASE_DIR, legacy_path)]
        legacy_path = next((c for c in candidatos if os.path.isfile(c)), candidatos[0])
    try:
        estado = abrir_banco_estado(state_path, metadata_path)
        migracao = migrar_banco_legado(estado, legacy_path) if legacy_path else {}
        if any(migracao.values()):
            print(
                "Banco antigo migrado sem apagá-lo: "
                f"{migracao['downloads']} vídeo(s), "
                f"{migracao['playlists']} item(ns) de playlist, "
                f"{migracao['metadados']} resolução(ões)."
            )
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"Não foi possível abrir o banco de progresso {state_path}: {exc}")
        return 1

    if COOKIES_YTDLP:
        origem = COOKIES_YTDLP.get("cookiefile") or "navegador " + COOKIES_YTDLP["cookiesfrombrowser"][0]
        print(f"Usando a conta do Google ({origem}). Limite maior (~2000 vídeos/h), mas o YouTube "
              "pode bloquear a conta por uso automatizado: use com moderação.")
    # Memória da biblioteca: artistas já confirmados ajudam a identificar os próximos.
    ARTISTAS_CONHECIDOS[:] = carregar_artistas_conhecidos(estado)
    CAPAS_BAIXADAS.clear()
    FAIXAS_PARA_PERGUNTAR.clear()
    if getattr(args, "progresso", False):
        progresso.iniciar(sum(contar_linhas_csv(c) for c in csvs))
    try:
        indice_local = indexar_musicas_locais()
        for csv_file in csvs:
            print(f"Processando arquivo: {csv_file}")
            continuar = processar_csv(
                csv_file,
                append=args.append,
                estado=estado,
                reprocess=args.reprocess,
                indice_local=indice_local,
            )
            if not continuar:
                print("Processamento interrompido por bloqueio temporário; nenhum outro CSV será consultado agora.")
                break
        if getattr(args, "perguntar", False):
            progresso.encerrar()
            perguntar_faltando(estado, args)
    except KeyboardInterrupt:
        progresso.encerrar()
        print("\nInterrompido. O progresso confirmado foi salvo; execute o mesmo comando para retomar.")
        return 130
    finally:
        progresso.encerrar()
        if MODELO_HIBRIDO:
            try:
                caminho_pendencias = exportar_metadados_pendentes(estado)
                total_pendentes = estado.execute(
                    "SELECT COUNT(*) FROM metadata_resolution WHERE status IN ('PENDENTE', 'OBSERVAR')"
                ).fetchone()[0]
                print(
                    f"Fila de metadados pendentes: {total_pendentes} faixa(s) em "
                    f"{caminho_pendencias}"
                )
            except (OSError, sqlite3.Error) as exc:
                print(f"Não foi possível exportar a fila de metadados pendentes: {exc}")
        estado.close()
    return 0
