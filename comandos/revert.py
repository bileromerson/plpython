"""Comando `revert`: desfaz o layout legado songs/_library."""

from __future__ import annotations

import argparse

from comandos._comum import novo_parser, traduzir_ajuda

NOME = "revert"
APELIDOS = ("reverter",)
RESUMO = "move MP3s de songs/_library de volta para as pastas dos artistas"
OPCOES = "-y/--apply  -s  -p  --downloads-db"
EXEMPLOS = """\
  plpython.py revert -s songs                 # simula
  plpython.py revert -s songs --apply
  plpython.py revert -s songs -p ./minhas_playlists --apply"""


def criar_parser(prog: str = "plpython.py revert") -> argparse.ArgumentParser:
    p = novo_parser(prog, "Move o MP3 de songs/_library para a pasta do artista principal, cria "
                          "links para colaboradores e corrige as M3Us. Simula sem --apply.", EXEMPLOS)
    p.add_argument("-s", "--songs-dir", dest="root", default="songs", metavar="PASTA",
                   help="pasta das músicas (padrão: songs)")
    p.add_argument("-p", "--playlist-dir", metavar="PASTA",
                   help="pasta das M3Us (padrão: playlists ao lado da pasta de músicas)")
    p.add_argument("--downloads-db", dest="state_file", metavar="ARQ",
                   help="banco do download para recuperar a ordem dos artistas (autodetectado)")
    p.add_argument("-y", "--apply", action="store_true", help="aplica; sem isto só simula")
    return traduzir_ajuda(p)


def executar(args: argparse.Namespace) -> int:
    import reverter_biblioteca

    return reverter_biblioteca.executar(args)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
