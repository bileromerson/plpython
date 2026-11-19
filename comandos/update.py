"""Comando `update`: relê os CSVs, refaz falhas e reconsulta pendências.

Usa as mesmas opções do download (comandos/download.py), sem --retry,
porque a nova tentativa já é sempre feita aqui. Não baixa de novo o que já existe.
"""

from __future__ import annotations

import argparse

from comandos import download
from comandos._comum import novo_parser, traduzir_ajuda

NOME = "update"
APELIDOS = ("atualizar",)
RESUMO = "relê os CSVs, refaz falhas e pendências sem baixar de novo o que já existe"
OPCOES = "as mesmas do download, exceto --retry"
EXEMPLOS = """\
  plpython.py update -c ./csvs
  plpython.py update -c ./csvs --wait-on-limit
  plpython.py update csvs/Alone.csv --no-review"""


def criar_parser(prog: str = "plpython.py update") -> argparse.ArgumentParser:
    p = novo_parser(prog, RESUMO[0].upper() + RESUMO[1:] + ".", EXEMPLOS)
    download.adicionar_opcoes(p, update=True)
    return traduzir_ajuda(p)


def executar(args: argparse.Namespace) -> int:
    return download.executar(args, update=True)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
