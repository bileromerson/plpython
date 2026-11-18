"""Baixa músicas de CSVs do YouTube e gera playlists M3U."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from resolvedor_metadados import MusicBrainzClient, resolver_metadados

try:
    import yt_dlp
except ImportError:  # Mantém helpers e testes importáveis sem a dependência instalada.
    yt_dlp = None


BASE_DIR = os.getcwd()
SONGS_DIR = os.path.join(BASE_DIR, "songs")
PLAYLISTS_DIR = os.path.join(BASE_DIR, "playlists")
LOG_FILE = os.path.join(BASE_DIR, "Logs.txt")
ROW = 2
ARTIST_LINKS = False
ESPERAR_BLOQUEIO = False
MODELO_HIBRIDO = False
MUSICBRAINZ_CLIENT: MusicBrainzClient | None = None
RESOLVER_CACHE: dict[str, Any] = {}
MAX_TENTATIVAS_BLOQUEIO = 6
ESPERA_INICIAL_SEGUNDOS = 30
ESPERA_MAXIMA_SEGUNDOS = 600


def verificar_diretorios(args: argparse.Namespace) -> None:
    """Aplica as opções de diretório e de coluna informadas na linha de comando."""
    global BASE_DIR, SONGS_DIR, PLAYLISTS_DIR, LOG_FILE, ROW, ARTIST_LINKS
    global ESPERAR_BLOQUEIO, MODELO_HIBRIDO

    BASE_DIR = os.path.abspath(args.base_dir or os.getcwd())
    SONGS_DIR = os.path.normpath(os.path.join(BASE_DIR, args.songs_dir or "songs"))
    PLAYLISTS_DIR = os.path.normpath(
        os.path.join(BASE_DIR, args.playlist_dir or "playlists")
    )
    LOG_FILE = os.path.normpath(os.path.join(BASE_DIR, args.log_file or "Logs.txt"))
    ROW = args.row
    ARTIST_LINKS = args.artist_links
    ESPERAR_BLOQUEIO = args.esperar_bloqueio
    MODELO_HIBRIDO = args.modelo_hibrido


def registrar_erro(url: str, motivo: str, playlist: str = "") -> None:
    """Registra falhas sem interromper o processamento do restante da playlist."""
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
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        self.errors.append(str(msg))
        print(f"Erro capturado: {msg}")
        registrar_erro(self.url, msg, self.playlist)


class BloqueioTemporarioYouTube(RuntimeError):
    """Sinaliza que o YouTube está recusando temporariamente novas requisições."""


def parece_bloqueio_temporario(mensagem: str) -> bool:
    texto = mensagem.casefold().replace("’", "'")
    sinais = (
        "http error 429", "429: too many requests", "too many requests",
        "rate limit", "ratelimit", "sign in to confirm you're not a bot",
        "confirm you're not a bot", "temporarily blocked", "unusual traffic",
        "automated queries", "http error 502", "http error 503",
        "http error 504", "service unavailable", "bad gateway",
        "connection reset", "timed out", "timeout",
    )
    return any(sinal in texto for sinal in sinais)


def executar_com_retentativas(url: str, csv_file: str, operacao):
    """Repete apenas falhas transitórias, com espera exponencial se habilitada."""
    espera = ESPERA_INICIAL_SEGUNDOS
    tentativas = 0
    while True:
        try:
            return operacao()
        except Exception as exc:
            mensagem = str(exc)
            if not parece_bloqueio_temporario(mensagem):
                registrar_erro(url, mensagem, csv_file)
                return None

            registrar_erro(url, mensagem, csv_file)
            if not ESPERAR_BLOQUEIO:
                print(
                    "O YouTube recusou temporariamente o acesso. "
                    "Para aguardar e tentar novamente, use --esperar-bloqueio."
                )
                raise BloqueioTemporarioYouTube(mensagem) from exc

            if tentativas >= MAX_TENTATIVAS_BLOQUEIO:
                print(
                    "O acesso continua bloqueado após as tentativas configuradas. "
                    "O processamento desta playlist será interrompido."
                )
                raise BloqueioTemporarioYouTube(mensagem) from exc

            tentativas += 1
            print(
                f"Limite/bloqueio temporário do YouTube. "
                f"Nova tentativa {tentativas}/{MAX_TENTATIVAS_BLOQUEIO} "
                f"em {espera} segundos. Pressione Ctrl+C para cancelar."
            )
            time.sleep(espera)
            espera = min(espera * 2, ESPERA_MAXIMA_SEGUNDOS)


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
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path, easy=True)
        if audio:
            tags = audio.get("title", [])
            if tags and not title:
                title = str(tags[0] if isinstance(tags, list) else tags)
            artistas = normalizar_artistas({"artists": audio.get("artist", [])})
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
    return {"title": title, "artists": artistas, "artist": artistas[0], "duration": 0, "id": video_id}


def abrir_banco_estado(path: str) -> sqlite3.Connection:
    pasta = os.path.dirname(os.path.abspath(path))
    os.makedirs(pasta, exist_ok=True)
    conexao = sqlite3.connect(path, timeout=30)
    conexao.row_factory = sqlite3.Row
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
        CREATE TABLE IF NOT EXISTS metadata_resolution (
            video_key TEXT PRIMARY KEY,
            video_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            result_json TEXT NOT NULL,
            audio_path TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conexao.commit()
    return conexao


def ler_video_cache(conexao: sqlite3.Connection, video_key: str) -> dict[str, Any] | None:
    linha = conexao.execute("SELECT * FROM video_cache WHERE video_key = ?", (video_key,)).fetchone()
    if not linha:
        return None
    registro = dict(linha)
    registro["artists"] = json.loads(registro.pop("artists_json") or "[]")
    return registro


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
            float(meta.get("duration") or 0), path, error,
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
            csv_path, row_index, video_key, meta["title"], ", ".join(meta["artists"]),
            float(meta.get("duration") or 0), os.path.abspath(path),
        ),
    )


def listar_itens_playlist(conexao: sqlite3.Connection, csv_path: str):
    rows = conexao.execute(
        "SELECT title, artist, duration, path FROM playlist_items WHERE csv_path = ? ORDER BY row_index",
        (csv_path,),
    ).fetchall()
    return [(row["title"], row["artist"], row["duration"], row["path"]) for row in rows]


def carregar_resolucao(
    conexao: sqlite3.Connection, video_key: str
) -> dict[str, Any] | None:
    linha = conexao.execute(
        "SELECT result_json, audio_path FROM metadata_resolution WHERE video_key = ?",
        (video_key,),
    ).fetchone()
    if not linha:
        return None
    try:
        return {
            "result": json.loads(linha["result_json"]),
            "audio_path": linha["audio_path"],
        }
    except (TypeError, json.JSONDecodeError):
        return None


def salvar_resolucao(
    conexao: sqlite3.Connection,
    video_key: str,
    resultado: dict[str, Any],
    audio_path: str = "",
) -> None:
    conexao.execute(
        """INSERT INTO metadata_resolution
        (video_key, video_id, status, result_json, audio_path, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(video_key) DO UPDATE SET
        video_id=excluded.video_id, status=excluded.status,
        result_json=excluded.result_json,
        audio_path=CASE WHEN excluded.audio_path != '' THEN excluded.audio_path
                        ELSE metadata_resolution.audio_path END,
        updated_at=CURRENT_TIMESTAMP""",
        (
            video_key,
            str(resultado.get("video_id") or ""),
            str(resultado.get("status") or "PENDENTE"),
            json.dumps(resultado, ensure_ascii=False),
            os.path.abspath(audio_path) if audio_path else "",
        ),
    )
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


def resolver_meta_hibrido(meta: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    resultado = resolver_metadados(
        str(meta.get("id") or ""),
        meta,
        cache=RESOLVER_CACHE,
        musicbrainz_client=MUSICBRAINZ_CLIENT,
    ).to_dict()
    return meta_do_resultado_hibrido(resultado), resultado


def aplicar_tags_hibridas(audio_path: str, resultado: dict[str, Any]) -> bool:
    """Grava tags canônicas e um marcador ID3 explícito de metadados pendentes."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TIT2, TPE1, TSRC, TXXX

        try:
            tags = ID3(audio_path)
        except ID3NoHeaderError:
            tags = ID3()
        metadata = resultado.get("metadata") or {}
        for frame_id in ("TIT2", "TPE1", "TALB", "TSRC"):
            tags.delall(frame_id)
        if metadata.get("title"):
            tags.add(TIT2(encoding=3, text=[str(metadata["title"])]))
        artists = metadata.get("artists") or []
        if isinstance(artists, str):
            artists = [artists]
        if artists:
            tags.add(TPE1(encoding=3, text=[str(item) for item in artists]))
        if metadata.get("album"):
            tags.add(TALB(encoding=3, text=[str(metadata["album"])]))
        if metadata.get("isrc"):
            tags.add(TSRC(encoding=3, text=[str(metadata["isrc"])]))

        valores = {
            "PLTUBE_METADATA_STATUS": str(resultado.get("status") or "PENDENTE"),
            "PLTUBE_METADATA_NOTE": (
                "Metadados provisórios; podem estar incorretos. Consulte metadados_pendentes.csv."
                if resultado.get("status") == "PENDENTE"
                else "Metadados confirmados pelo modelo híbrido."
            ),
            "PLTUBE_METADATA_PROVENANCE": json.dumps(
                resultado.get("provenance") or {}, ensure_ascii=False
            ),
            "PLTUBE_METADATA_CONFIDENCE": json.dumps(
                resultado.get("confidence") or {}, ensure_ascii=False
            ),
            "PLTUBE_METADATA_REVIEW": " | ".join(
                resultado.get("pending_reasons") or []
            ),
        }
        for desc, value in valores.items():
            tags.delall(f"TXXX:{desc}")
            tags.add(TXXX(encoding=3, desc=desc, text=[value]))
        tags.save(audio_path)
        return True
    except Exception as exc:
        registrar_erro(audio_path, f"Não foi possível gravar tags do modelo híbrido: {exc}")
        return False


def exportar_metadados_pendentes(
    conexao: sqlite3.Connection, destino: str | None = None
) -> str:
    """Exporta um CSV legível com uma linha por vídeo ainda sem comprovação clara."""
    destino = destino or os.path.join(BASE_DIR, "metadados_pendentes.csv")
    os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
    colunas = [
        "video_id", "url_youtube", "arquivo_audio", "playlists", "status",
        "titulo_youtube", "artistas_youtube", "canal_youtube", "album_youtube", "titulo_usado",
        "artistas_usados", "album_sugerido", "isrc_youtube", "isrc_sugerido",
        "thumbnail_youtube", "capa_sugerida", "motivos_pendencia", "diagnosticos",
        "candidatos_musicbrainz", "proveniencia", "confianca",
    ]
    linhas = conexao.execute(
        """SELECT r.video_key, r.video_id, r.result_json, r.audio_path,
                  v.source_url
           FROM metadata_resolution r
           LEFT JOIN video_cache v ON v.video_key = r.video_key
           WHERE r.status = 'PENDENTE'
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
                writer.writerow({
                    "video_id": linha["video_id"],
                    "url_youtube": linha["source_url"] or "",
                    "arquivo_audio": linha["audio_path"] or "",
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
                    "candidatos_musicbrainz": json.dumps(
                        resultado.get("candidates") or [], ensure_ascii=False
                    ),
                    "proveniencia": json.dumps(
                        resultado.get("provenance") or {}, ensure_ascii=False
                    ),
                    "confianca": json.dumps(
                        resultado.get("confidence") or {}, ensure_ascii=False
                    ),
                })
        os.replace(temporario, destino)
    finally:
        if os.path.exists(temporario):
            os.unlink(temporario)
    return destino


def normalizar_artistas(
    info: dict[str, Any], permitir_uploader: bool = True
) -> list[str]:
    """Obtém os artistas dos metadados do YouTube, preservando nomes de bandas."""
    candidatos = info.get("artists")
    if isinstance(candidatos, str):
        candidatos = [candidatos]
    if not candidatos:
        artista = info.get("artist")
        candidatos = artista if isinstance(artista, list) else ([artista] if artista else [])

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
        }
        if ESPERAR_BLOQUEIO:
            # Deixa o backoff do programa controlar as novas tentativas em vez
            # de repetir imediatamente dentro do yt-dlp.
            ydl_opts.update({"retries": 0, "extractor_retries": 0, "fragment_retries": 0})
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise RuntimeError("yt-dlp não retornou metadados. " + " ".join(logger.errors))
        return info

    def montar_metadados() -> dict[str, Any]:
        info = obter_info()
        artistas = normalizar_artistas(
            info, permitir_uploader=not MODELO_HIBRIDO
        )
        if not artistas and not MODELO_HIBRIDO:
            raise RuntimeError("O YouTube não informou o artista deste vídeo.")
        return {
            "title": info.get("title") or info.get("id") or "musica-sem-titulo",
            "artists": artistas,
            "artist": artistas[0] if artistas else "",
            "duration": info.get("duration") or 0,
            "id": str(info.get("id") or hashlib.sha1(url.encode("utf-8")).hexdigest()[:11]),
            "album": info.get("album") or info.get("release") or "",
            "isrc": info.get("isrc") or info.get("isrcs") or "",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "thumbnail": info.get("thumbnail") or "",
            "thumbnails": info.get("thumbnails") or [],
        }

    try:
        return executar_com_retentativas(url, csv_file, montar_metadados)
    except BloqueioTemporarioYouTube:
        raise


def nome_seguro(valor: str, fallback: str = "sem-nome") -> str:
    """Remove caracteres que poderiam criar caminhos inválidos ou escapar da pasta."""
    valor = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(valor))
    valor = valor.strip().strip(".")
    if valor in {"", ".", ".."}:
        return fallback
    return valor[:180]


def criar_link_artista(arquivo_original: str, artista: str) -> bool:
    """Cria um link para o arquivo central, sem copiar os dados da música."""
    pasta_artista = os.path.join(SONGS_DIR, nome_seguro(artista, "Artista Desconhecido"))
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


def baixar_musica(
    url: str,
    artist: str | list[str],
    title: str,
    video_id: str = "",
    csv_file: str = "",
    hybrid_result: dict[str, Any] | None = None,
) -> str | None:
    """Salva no artista principal e, se ativado, liga colaboradores ao mesmo MP3."""
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

    # A primeira posição dos créditos é tratada como artista principal.
    # Não há pasta central de MP3: o próprio arquivo fica nesta pasta.
    pendente = bool(hybrid_result and hybrid_result.get("status") == "PENDENTE")
    pasta_download = (
        os.path.join(SONGS_DIR, "_pendentes")
        if pendente
        else os.path.join(SONGS_DIR, nome_seguro(artists[0], "Artista Desconhecido"))
    )
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
            if ESPERAR_BLOQUEIO:
                ydl_opts.update({"retries": 0, "extractor_retries": 0, "fragment_retries": 0})
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
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
    if ARTIST_LINKS and not pendente:
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
    }


def processar_csv(
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
    state_key = os.path.normcase(os.path.realpath(csv_path))
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
                if row_index < start_row:
                    continue  # Já foi checkpointado: não consultar yt-dlp de novo.

                numero_linha = row_index + 2
                video_key = ""
                url = ""
                meta: dict[str, Any] | None = None
                music_path: str | None = None
                hybrid_result: dict[str, Any] | None = None

                if not row or not (-len(row) <= ROW < len(row)):
                    registrar_erro("", f"Coluna {ROW} ausente na linha {numero_linha}", csv_file)
                else:
                    url = row[ROW].strip()
                    if url.lower().startswith(("http://", "https://")):
                        video_key, video_id = obter_chave_video(url)
                        registro = ler_video_cache(estado, video_key) if estado is not None else None

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
                                print(f"Já está baixada; reutilizando sem acessar o YouTube: {url}")
                            elif video_id and indice_local and os.path.isfile(indice_local.get(video_id, "")):
                                music_path = indice_local[video_id]
                                meta = metadados_de_arquivo_local(
                                    music_path, video_id, row[1] if len(row) > 1 else ""
                                )
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
                                        estado.commit()
                                    elif estado is not None:
                                        salvar_video_cache(
                                            estado, video_key, url, "failed",
                                            error="Não foi possível obter os metadados",
                                        )

                            if meta and MODELO_HIBRIDO:
                                salvo_hibrido = (
                                    carregar_resolucao(estado, video_key)
                                    if estado is not None else None
                                )
                                tentar_novamente = bool(
                                    salvo_hibrido and reprocess
                                    and salvo_hibrido["result"].get("status") == "PENDENTE"
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
                                    meta, hybrid_result = resolver_meta_hibrido(meta)
                                    if estado is not None:
                                        salvar_resolucao(estado, video_key, hybrid_result)

                            if meta and music_path and hybrid_result:
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

                            if music_path and estado is not None and meta:
                                salvar_video_cache(
                                    estado, video_key, url, "complete", meta, music_path
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


def atualizar(csv_file: str) -> None:
    """Compatibilidade com o nome usado pela versão beta anterior."""
    processar_csv(csv_file, append=True)


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Baixa músicas de CSVs do YouTube e cria playlists M3U")
    parser.add_argument("-b", "--BaseDir", dest="base_dir", help="Pasta base dos CSVs (padrão: diretório atual)")
    parser.add_argument("-s", "--SongsDir", dest="songs_dir", help="Pasta de músicas (padrão: songs)")
    parser.add_argument("-p", "--PlaylistDir", dest="playlist_dir", help="Pasta das playlists (padrão: playlists)")
    parser.add_argument("-l", "--LogFile", dest="log_file", help="Arquivo de log (padrão: Logs.txt)")
    parser.add_argument("--lista", help="Caminho de um CSV específico; sem esta opção, processa os CSVs da pasta base")
    parser.add_argument("--row", type=int, default=2, help="Índice da coluna da URL, começando em 0 (padrão: 2; aceita índices negativos)")
    parser.add_argument("-a", "--append", action="store_true", help="Acrescenta itens à playlist sem repetir caminhos existentes")
    parser.add_argument(
        "--artist-links", "--links-artista", dest="artist_links", action="store_true",
        help="Guarda o MP3 na pasta do primeiro artista e cria links para os demais artistas, sem duplicar o áudio",
    )
    parser.add_argument(
        "--esperar-bloqueio", "--wait-on-limit", dest="esperar_bloqueio", action="store_true",
        help="Espera e tenta novamente em bloqueios temporários do YouTube (backoff: 30s até 10min; até 6 tentativas)",
    )
    parser.add_argument(
        "--modelo-hibrido",
        action="store_true",
        help="Consulta o MusicBrainz, prioriza correspondências confiáveis e registra metadados provisórios para revisão",
    )
    parser.add_argument(
        "--state-file", default="pltube_state.sqlite3",
        help="Banco local de progresso e downloads reutilizáveis (padrão: pltube_state.sqlite3)",
    )
    parser.add_argument(
        "--reprocess", action="store_true",
        help="Relê o CSV, tenta itens que falharam e reconsulta pendências MusicBrainz sem refazer downloads concluídos",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    global MUSICBRAINZ_CLIENT, RESOLVER_CACHE
    parser = criar_parser()
    args = parser.parse_args(argv)
    verificar_diretorios(args)
    MUSICBRAINZ_CLIENT = MusicBrainzClient() if MODELO_HIBRIDO else None
    RESOLVER_CACHE = {}
    criar_diretorios()

    if args.lista:
        csvs = [args.lista]
    else:
        csvs = obter_csvs()

    if not csvs:
        print(f"Nenhum arquivo .csv encontrado em: {BASE_DIR}")
        return 0

    state_path = args.state_file
    if not os.path.isabs(state_path):
        state_path = os.path.join(BASE_DIR, state_path)
    try:
        estado = abrir_banco_estado(state_path)
    except sqlite3.Error as exc:
        print(f"Não foi possível abrir o banco de progresso {state_path}: {exc}")
        return 1

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
    except KeyboardInterrupt:
        print("\nInterrompido. O progresso confirmado foi salvo; execute o mesmo comando para retomar.")
        return 130
    finally:
        if MODELO_HIBRIDO:
            try:
                caminho_pendencias = exportar_metadados_pendentes(estado)
                total_pendentes = estado.execute(
                    "SELECT COUNT(*) FROM metadata_resolution WHERE status = 'PENDENTE'"
                ).fetchone()[0]
                print(
                    f"Fila de metadados pendentes: {total_pendentes} faixa(s) em "
                    f"{caminho_pendencias}"
                )
            except (OSError, sqlite3.Error) as exc:
                print(f"Não foi possível exportar a fila de metadados pendentes: {exc}")
        estado.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
