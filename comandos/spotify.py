"""Comando `spotify`: lista as faixas de uma playlist Spotify (somente leitura)."""

from __future__ import annotations

import argparse

from comandos._comum import novo_parser, traduzir_ajuda

NOME = "spotify"
APELIDOS: tuple[str, ...] = ()
RESUMO = "lista as faixas de uma playlist Spotify (somente leitura)"
OPCOES = "PLAYLIST"
EXEMPLOS = """\
  plpython.py spotify https://open.spotify.com/playlist/ID
  plpython.py spotify spotify:playlist:37i9dQZF1DXcBWIGoYBM5M

requer as variáveis SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET e SPOTIFY_REDIRECT_URI"""


def criar_parser(prog: str = "plpython.py spotify") -> argparse.ArgumentParser:
    p = novo_parser(prog, "Mostra as faixas e links de uma playlist Spotify. Não baixa nada.", EXEMPLOS)
    p.add_argument("playlist", help="URL, URI ou ID da playlist")
    return traduzir_ajuda(p)


def executar(args: argparse.Namespace) -> int:
    import spotify_playlist_viewer

    return spotify_playlist_viewer.executar(args)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
