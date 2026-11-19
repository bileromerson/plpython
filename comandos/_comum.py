"""Opções e ajustes compartilhados entre os arquivos de comandos."""

from __future__ import annotations

import argparse
import os


def novo_parser(prog: str, descricao: str, exemplos: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog=prog,
        description=descricao,
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


def oculto(parser: argparse.ArgumentParser, *nomes: str, **kwargs) -> None:
    """Aceita nomes antigos sem mostrá-los na ajuda (compatibilidade)."""
    parser.add_argument(*nomes, help=argparse.SUPPRESS, **kwargs)


def opcoes_pastas(p: argparse.ArgumentParser) -> None:
    p.add_argument("-s", "--songs-dir", dest="songs_dir", metavar="PASTA",
                   help="pasta das músicas (padrão: ./songs)")
    p.add_argument("-p", "--playlist-dir", dest="playlist_dir", metavar="PASTA",
                   help="pasta das M3Us (padrão: ./playlists)")
    oculto(p, "--SongsDir", dest="songs_dir")
    oculto(p, "--PlaylistDir", dest="playlist_dir")


def opcoes_bancos(p: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    g = p.add_argument_group("bancos SQLite (avançado)")
    g.add_argument("--downloads-db", default=os.path.join("sql", "downloads.sqlite3"), metavar="ARQ",
                   help="fila/progresso de downloads (padrão: ./sql/downloads.sqlite3)")
    g.add_argument("--metadata-db", default=os.path.join("sql", "musicas.sqlite3"), metavar="ARQ",
                   help="metadados das faixas (padrão: ./sql/musicas.sqlite3)")
    oculto(p, "--state-file", dest="downloads_db")
    return g
