"""Comando `legacy-centralize`: cria o layout legado songs/_library (não recomendado)."""

from __future__ import annotations

import argparse
import sys

from comandos._comum import novo_parser, oculto, traduzir_ajuda

NOME = "legacy-centralize"
APELIDOS: tuple[str, ...] = ()
RESUMO = "[legado] centraliza MP3s em songs/_library (o oposto de revert)"
OPCOES = "-s  --apply"
EXEMPLOS = """\
  plpython.py legacy-centralize -s songs     # só simula

Não use --apply se quiser manter o layout atual, com uma pasta por artista."""


def criar_parser(prog: str = "plpython.py legacy-centralize") -> argparse.ArgumentParser:
    p = novo_parser(prog, "Move os MP3s para songs/_library e deixa links nas pastas dos artistas.", EXEMPLOS)
    p.add_argument("-s", "--songs-dir", dest="root", default="songs", metavar="PASTA",
                   help="pasta das músicas (padrão: songs)")
    oculto(p, "--root", dest="root")
    p.add_argument("--apply", action="store_true", help="aplica; sem isto só simula")
    return traduzir_ajuda(p)


def executar(args: argparse.Namespace) -> int:
    import sincronizar_biblioteca

    if args.apply:
        print("Aviso: isto cria o layout legado songs/_library.", file=sys.stderr)
    return sincronizar_biblioteca.executar(args)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
