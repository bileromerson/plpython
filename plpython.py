"""plpython.py: o menu central do projeto.

Este arquivo não baixa nem altera nada. Ele só mostra os comandos disponíveis
e encaminha cada um para o seu próprio arquivo na pasta `comandos/`:

  plpython.py download ...  ->  comandos/download.py
  plpython.py update ...    ->  comandos/update.py
  plpython.py metadata ...  ->  comandos/metadata.py
  plpython.py review ...    ->  comandos/review.py
  plpython.py spotify ...   ->  comandos/spotify.py
  plpython.py revert ...    ->  comandos/revert.py
  plpython.py playlist ...  ->  comandos/playlist.py

Todas as opções, validações e a ajuda de cada comando ficam no arquivo dele.
"""

from __future__ import annotations

import importlib
import os
import sys

# Permite rodar `python /qualquer/caminho/plpython.py` de qualquer pasta.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from comandos import ORDEM  # noqa: E402


def _carregar(modulo: str):
    return importlib.import_module(f"comandos.{modulo}")


def _mapa_comandos() -> dict[str, str]:
    """Nome ou apelido digitado -> módulo em comandos/."""
    mapa: dict[str, str] = {}
    for modulo in ORDEM:
        cmd = _carregar(modulo)
        for nome in (cmd.NOME, *cmd.APELIDOS):
            mapa[nome] = modulo
    return mapa


def mostrar_menu(arquivo=None) -> None:
    linhas = [
        "uso: plpython.py COMANDO [opções do comando]",
        "",
        "Playlists do YouTube → MP3 + M3U, com revisão de metadados.",
        "O comando vem sempre primeiro; cada um tem suas próprias opções.",
        "",
        "comandos:",
    ]
    for modulo in ORDEM:
        cmd = _carregar(modulo)
        nome = cmd.NOME + (f" ({', '.join(cmd.APELIDOS)})" if cmd.APELIDOS else "")
        linhas.append(f"  {nome:<22}{cmd.RESUMO}")
        for opcoes in cmd.OPCOES.splitlines():
            linhas.append(f"  {'':<22}  {opcoes}")
    linhas += [
        "",
        "ajuda de um comando:",
        "  plpython.py help download        (ou: plpython.py download -h)",
        "",
        "exemplos:",
        "  python plpython.py download -c ./csvs --hybrid",
        "  python plpython.py update -c ./csvs --wait-on-limit",
        "  python plpython.py review",
        "  python plpython.py review --ok-all",
        "  python plpython.py metadata --apply",
        "  python plpython.py spotify && python plpython.py sync -y",
        "",
        "Os arquivos (songs/, playlists/, sql/) são criados na pasta de onde você roda o comando.",
    ]
    print("\n".join(linhas), file=arquivo or sys.stdout)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    comandos = _mapa_comandos()

    if not argv or argv[0] in ("help", "ajuda", "-h", "--help"):
        if len(argv) > 1 and argv[1] in comandos:
            return _carregar(comandos[argv[1]]).main(["-h"])
        mostrar_menu()
        return 0 if argv else 2

    if argv[0] not in comandos:
        # Toda ação exige um comando antes das opções: nada roda por engano.
        print(f"Comando ausente ou desconhecido: {argv[0]!r}\n", file=sys.stderr)
        print("Escreva o comando primeiro e depois as opções dele, por exemplo:", file=sys.stderr)
        print("  python plpython.py download -c csvs --hybrid\n", file=sys.stderr)
        mostrar_menu(sys.stderr)
        return 2

    return _carregar(comandos[argv[0]]).main(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
