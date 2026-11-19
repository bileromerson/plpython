"""Comando `metadata`: aplica as correções marcadas em metadados_pendentes.csv."""

from __future__ import annotations

import argparse
import os

from comandos._comum import ativar_verbose, opcoes_verbose, novo_parser, oculto, opcoes_bancos, opcoes_pastas, traduzir_ajuda

NOME = "metadata"
APELIDOS = ("metadados",)
RESUMO = "aplica as correções da planilha metadados_pendentes.csv (simula sem --apply)"
OPCOES = "--sheet  --apply  -s  -p"
EXEMPLOS = """\
  plpython.py metadata                      # simula
  plpython.py metadata --apply              # grava tags, SQL e M3Us
  plpython.py metadata --sheet revisao.csv --apply"""


def criar_parser(prog: str = "plpython.py metadata") -> argparse.ArgumentParser:
    p = novo_parser(prog, "Aplica nos MP3s, no banco e nas M3Us as correções marcadas na planilha. "
                          "Lê tudo da pasta atual.", EXEMPLOS)
    opcoes_pastas(p)
    p.add_argument("--sheet", dest="arquivo", default="metadados_pendentes.csv", metavar="CSV",
                   help="planilha revisada (padrão: ./metadados_pendentes.csv)")
    oculto(p, "--arquivo", dest="arquivo")
    p.add_argument("--apply", action="store_true", help="grava as mudanças; sem isto só simula")
    opcoes_bancos(p)
    opcoes_verbose(p)
    return traduzir_ajuda(p)


def executar(args: argparse.Namespace) -> int:
    ativar_verbose(args)
    import aplicar_metadados

    args.base_dir = os.getcwd()
    args.work_dir = args.base_dir
    return aplicar_metadados.executar(args)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
