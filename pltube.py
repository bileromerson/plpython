"""Baixa músicas de CSVs do YouTube e gera playlists M3U."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import time
from typing import Any

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
MAX_TENTATIVAS_BLOQUEIO = 6
ESPERA_INICIAL_SEGUNDOS = 30
ESPERA_MAXIMA_SEGUNDOS = 600


def verificar_diretorios(args: argparse.Namespace) -> None:
    """Aplica as opções de diretório e de coluna informadas na linha de comando."""
    global BASE_DIR, SONGS_DIR, PLAYLISTS_DIR, LOG_FILE, ROW, ARTIST_LINKS, ESPERAR_BLOQUEIO

    BASE_DIR = os.path.abspath(args.base_dir or os.getcwd())
    SONGS_DIR = os.path.normpath(os.path.join(BASE_DIR, args.songs_dir or "songs"))
    PLAYLISTS_DIR = os.path.normpath(
        os.path.join(BASE_DIR, args.playlist_dir or "playlists")
    )
    LOG_FILE = os.path.normpath(os.path.join(BASE_DIR, args.log_file or "Logs.txt"))
    ROW = args.row
    ARTIST_LINKS = args.artist_links
    ESPERAR_BLOQUEIO = args.esperar_bloqueio


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


def normalizar_artistas(info: dict[str, Any]) -> list[str]:
    """Obtém os artistas dos metadados do YouTube, preservando nomes de bandas."""
    candidatos = info.get("artists")
    if isinstance(candidatos, str):
        candidatos = [candidatos]
    if not candidatos:
        artista = info.get("artist")
        candidatos = artista if isinstance(artista, list) else ([artista] if artista else [])

    artistas: list[str] = []
    for candidato in candidatos:
        if not isinstance(candidato, str):
            continue
        # Só separa créditos explícitos de participação; não divide nomes de bandas
        # como "Earth, Wind & Fire" nem nomes próprios que contenham '&'.
        partes = re.split(r"\s+(?:feat(?:uring)?\.?|ft\.?)\s+", candidato, flags=re.I)
        for parte in partes:
            nome = parte.strip(" \t,;-")
            if nome and nome.casefold() not in {item.casefold() for item in artistas}:
                artistas.append(nome)

    if not artistas:
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
        artistas = normalizar_artistas(info)
        if not artistas:
            raise RuntimeError("O YouTube não informou o artista deste vídeo.")
        return {
            "title": info.get("title") or info.get("id") or "musica-sem-titulo",
            "artists": artistas,
            "artist": artistas[0],
            "duration": info.get("duration") or 0,
            "id": str(info.get("id") or hashlib.sha1(url.encode("utf-8")).hexdigest()[:11]),
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
) -> str | None:
    """Baixa a música uma vez e, opcionalmente, cria links nas pastas de artistas."""
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

    if ARTIST_LINKS:
        pasta_download = os.path.join(SONGS_DIR, "_library")
    else:
        pasta_download = os.path.join(SONGS_DIR, nome_seguro(artists[0], "Artista Desconhecido"))
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

    if ARTIST_LINKS:
        for nome in artists:
            criar_link_artista(filepath_mp3, nome)
    return filepath_mp3


def processar_csv(csv_file: str, append: bool = False) -> None:
    """Processa um CSV e cria ou atualiza sua playlist M3U."""
    csv_path = csv_file if os.path.isabs(csv_file) else os.path.join(BASE_DIR, csv_file)
    if not os.path.isfile(csv_path):
        registrar_erro(csv_path, "Arquivo CSV não encontrado", csv_file)
        print(f"CSV não encontrado: {csv_path}")
        return

    playlist_name = os.path.splitext(os.path.basename(csv_file))[0]
    m3u_path = os.path.join(PLAYLISTS_DIR, f"{playlist_name}.m3u")
    playlist_entries: list[tuple[str, str, int | float, str]] = []

    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as arquivo:
            reader = csv.reader(arquivo)
            next(reader, None)  # Os CSVs usados pelo projeto incluem cabeçalho.
            for numero_linha, row in enumerate(reader, start=2):
                if not row or not (-len(row) <= ROW < len(row)):
                    registrar_erro("", f"Coluna {ROW} ausente na linha {numero_linha}", csv_file)
                    continue
                url = row[ROW].strip()
                if not url.lower().startswith(("http://", "https://")):
                    continue

                print(f"Verificando: {url}")
                try:
                    meta = extrair_metadados(url, csv_file)
                except BloqueioTemporarioYouTube:
                    print("Interrompendo esta playlist para evitar novas requisições durante o bloqueio.")
                    break
                if not meta:
                    continue
                try:
                    music_path = baixar_musica(
                        url, meta["artists"], meta["title"], meta["id"], csv_file
                    )
                except BloqueioTemporarioYouTube:
                    print("Interrompendo esta playlist para evitar novas requisições durante o bloqueio.")
                    break
                if not music_path:
                    continue

                relative_path = os.path.relpath(music_path, start=PLAYLISTS_DIR)
                playlist_entries.append(
                    (meta["title"], ", ".join(meta["artists"]), meta["duration"], relative_path)
                )
    except (OSError, csv.Error) as exc:
        registrar_erro(csv_path, str(exc), csv_file)
        print(f"Falha lendo {csv_path}: {exc}")
        return

    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    existing_paths: set[str] = set()
    playlist_exists = os.path.isfile(m3u_path)
    if append and playlist_exists:
        try:
            with open(m3u_path, "r", encoding="utf-8") as arquivo:
                existing_paths = {
                    linha.strip()
                    for linha in arquivo
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
            for title, artist_names, duration, path in playlist_entries:
                if append and path in existing_paths:
                    continue
                arquivo.write(f"#EXTINF:{duration},{artist_names} - {title}\n")
                arquivo.write(f"{path}\n")
                existing_paths.add(path)
    except OSError as exc:
        registrar_erro(m3u_path, str(exc), csv_file)
        print(f"Falha gravando playlist {m3u_path}: {exc}")


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
        help="Ativa links sem duplicação: armazena cada música em songs/_library e cria links nas pastas de todos os artistas",
    )
    parser.add_argument(
        "--esperar-bloqueio", "--wait-on-limit", dest="esperar_bloqueio", action="store_true",
        help="Espera e tenta novamente em bloqueios temporários do YouTube (backoff: 30s até 10min; até 6 tentativas)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = criar_parser()
    args = parser.parse_args(argv)
    verificar_diretorios(args)
    criar_diretorios()

    if args.lista:
        csvs = [args.lista]
    else:
        csvs = obter_csvs()

    if not csvs:
        print(f"Nenhum arquivo .csv encontrado em: {BASE_DIR}")
        return 0

    for csv_file in csvs:
        print(f"Processando arquivo: {csv_file}")
        processar_csv(csv_file, append=args.append)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
