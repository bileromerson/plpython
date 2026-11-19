"""Opções e ajustes compartilhados entre os arquivos de comandos."""

from __future__ import annotations

import argparse
import os
import textwrap


def novo_parser(prog: str, descricao: str, exemplos: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog=prog,
        description=textwrap.fill(descricao, 79),
        epilog="exemplos:\n" + exemplos,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def traduzir_ajuda(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser._optionals.title = "opções"
    parser._positionals.title = "argumentos"
    for acao in parser._actions:
        if isinstance(acao, argparse._HelpAction):
            acao.help = "mostra esta ajuda e sai"
    return parser


def opcoes_pastas(p: argparse.ArgumentParser) -> None:
    p.add_argument("-s", "--songs-dir", dest="songs_dir", metavar="PASTA",
                   help="pasta das músicas (padrão: ./songs)")
    p.add_argument("-p", "--playlist-dir", dest="playlist_dir", metavar="PASTA",
                   help="pasta das M3Us (padrão: ./playlists)")


def opcoes_bancos(p: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    g = p.add_argument_group("bancos SQLite (avançado)")
    g.add_argument("--downloads-db", default=os.path.join("sql", "downloads.sqlite3"), metavar="ARQ",
                   help="fila/progresso de downloads (padrão: ./sql/downloads.sqlite3)")
    g.add_argument("--metadata-db", default=os.path.join("sql", "musicas.sqlite3"), metavar="ARQ",
                   help="metadados das faixas (padrão: ./sql/musicas.sqlite3)")
    g.add_argument("--artists-db", dest="artists_db", metavar="ARQ",
                   help="banco de artistas e variações (padrão: artistas.sqlite3 na pasta do --metadata-db)")
    return g


NAVEGADORES = ("firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi", "whale", "safari")


def opcoes_conta(p: argparse.ArgumentParser) -> None:
    """Conta do Google via cookies (o único login que o YouTube aceita no yt-dlp)."""
    g = p.add_argument_group(
        "conta do Google (playlists privadas, curtidas, vídeos restritos a quem está logado)"
    )
    g.add_argument(
        "-b", "--cookies-from-browser", dest="cookies_navegador", metavar="NAVEGADOR[:PERFIL]",
        default=os.environ.get("PLPYTHON_COOKIES_FROM_BROWSER") or None,
        help="usa a conta já logada no navegador; recomendado: firefox. Chrome/Edge/Brave no "
             "Windows costumam falhar (criptografia app-bound). Também lido da variável "
             "PLPYTHON_COOKIES_FROM_BROWSER",
    )
    g.add_argument(
        "-k", "--cookies", dest="cookies_arquivo", metavar="ARQUIVO.txt",
        default=os.environ.get("PLPYTHON_COOKIES") or None,
        help="arquivo cookies.txt (formato Netscape) exportado do youtube.com; também lido da "
             "variável PLPYTHON_COOKIES. Guarde-o com cuidado: dá acesso à sua conta",
    )


def cookies_ytdlp(args: argparse.Namespace) -> dict:
    """Converte as opções de conta para os parâmetros do yt-dlp."""
    navegador = getattr(args, "cookies_navegador", None)
    arquivo = getattr(args, "cookies_arquivo", None)
    if navegador and arquivo:
        raise SystemExit("Use --cookies-from-browser OU --cookies, não os dois.")
    if arquivo:
        caminho = os.path.abspath(os.path.expanduser(arquivo))
        if not os.path.isfile(caminho):
            raise SystemExit(f"Arquivo de cookies não encontrado: {caminho}")
        return {"cookiefile": caminho}
    if navegador:
        nome, _, perfil = navegador.partition(":")
        nome = nome.strip().casefold()
        if nome not in NAVEGADORES:
            raise SystemExit(f"Navegador não suportado: {nome}. Use um de: {', '.join(NAVEGADORES)}")
        return {"cookiesfrombrowser": (nome, perfil.strip() or None, None, None)}
    return {}


def descrever_conta(cookies: dict) -> str:
    if "cookiefile" in cookies:
        return f"cookies do arquivo {os.path.basename(cookies['cookiefile'])}"
    if "cookiesfrombrowser" in cookies:
        nome, perfil = cookies["cookiesfrombrowser"][:2]
        return f"conta logada no {nome}" + (f" (perfil {perfil})" if perfil else "")
    return ""


def opcoes_verbose(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "-v", "--verbose", dest="verbose", action="count", default=0,
        help="mostra detalhes, como no nmap: -v (erros silenciosos, consultas ao SQL/YouTube/"
             "MusicBrainz, YouTube x banco), -vv (tudo: hipóteses, metadados campo a campo, tags), "
             "-vvv (também a depuração do yt-dlp)",
    )


def ativar_verbose(args: argparse.Namespace) -> None:
    import verbose

    nivel = getattr(args, "verbose", 0) or 0
    verbose.definir(nivel)
    if nivel:
        nomes = {1: "-v", 2: "-vv"}
        print(f"Modo detalhado {nomes.get(nivel, '-vvv')} ativo.")
