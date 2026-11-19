"""Comando `sync`: acerta as M3Us com as playlists do Spotify (spotify.csv)."""

from __future__ import annotations

import argparse

from comandos._comum import (ativar_verbose, novo_parser, opcoes_bancos, opcoes_pastas, opcoes_verbose,
                             traduzir_ajuda)

NOME = "sync"
APELIDOS = ("sincronizar",)
RESUMO = "compara o spotify.csv com as músicas baixadas, acerta as M3Us e lista o que falta"
OPCOES = "PLAYLIST...  -y/--apply  -c/--csvfile  -m/--missing  --keep-extra  -s  -p  -v"
EXEMPLOS = """\
  plpython.py sync                       # mostra o que mudaria (não grava nada)
  plpython.py sync -y                    # grava as M3Us
  plpython.py sync Rock "Pop 2024" -y    # só estas playlists
  plpython.py sync --keep-extra -y       # não tira da M3U o que saiu do Spotify
  plpython.py sync -v                    # lista tudo, inclusive cada música não baixada

Rode depois de download/update/review: eles regravam as M3Us a partir dos CSVs
do YouTube. As músicas que faltam vão para nao_baixadas.csv, com o link de busca
no YouTube Music."""


def criar_parser(prog: str = "plpython.py sync") -> argparse.ArgumentParser:
    p = novo_parser(
        prog,
        "Compara as playlists do spotify.csv (gerado pelo comando spotify) com as músicas já "
        "baixadas. Mostra o que entra e o que sai de cada M3U, que passa a seguir a ordem do "
        "Spotify, e salva as músicas que ainda não foram baixadas em nao_baixadas.csv. Simula "
        "sem -y; nenhum MP3 é apagado.",
        EXEMPLOS,
    )
    p.add_argument("playlists", nargs="*", metavar="PLAYLIST", help="só estas playlists (nome ou ID); padrão: todas")
    p.add_argument("-c", "--csvfile", default="spotify.csv", metavar="ARQ",
                   help="lista gerada pelo comando spotify (padrão: ./spotify.csv)")
    p.add_argument("-y", "--apply", action="store_true", help="grava as M3Us; sem isto só mostra o que mudaria")
    p.add_argument("-m", "--missing", dest="faltando", default="nao_baixadas.csv", metavar="ARQ",
                   help="onde salvar as músicas não baixadas (padrão: ./nao_baixadas.csv)")
    p.add_argument("--keep-extra", dest="manter_extras", action="store_true",
                   help="mantém na M3U as músicas que não estão na playlist do Spotify")
    opcoes_pastas(p)
    opcoes_bancos(p)
    opcoes_verbose(p)
    return traduzir_ajuda(p)


def executar(args: argparse.Namespace) -> int:
    ativar_verbose(args)
    import sincronizar_playlists

    return sincronizar_playlists.executar(args)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
