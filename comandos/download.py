"""Comando `download`: baixa áudio dos CSVs do YouTube e gera as M3Us.

Tudo sobre downloads fica aqui: opções, validações e a chamada ao pltube.
O `update` reaproveita este arquivo, apenas forçando a nova tentativa.
"""

from __future__ import annotations

import argparse
import os

from comandos._comum import novo_parser, oculto, opcoes_bancos, opcoes_pastas, traduzir_ajuda

NOME = "download"
APELIDOS = ("baixar",)
RESUMO = "baixa os CSVs e gera as M3Us"
OPCOES = ("CSV...  -c/--csvfile  -s  -p  -u/--url-column  -a/--append  --artist-links\n"
          "--wait-on-limit  --retry  --hybrid  --prefer-db  --review  --no-review\n"
          "--require-isrc  --require-mb-artist")
EXEMPLOS = """\
  plpython.py download -c ./csvs
  plpython.py download -c ./csvs/Alone.csv            # só uma playlist
  plpython.py download csvs/Alone.csv csvs/Rain.csv --artist-links --append
  plpython.py download -c ./csvs -s Musicas -p ./m3u
  plpython.py download -c ./csvs --hybrid --require-isrc
  plpython.py download -c ./csvs --prefer-db          # MusicBrainz vence divergências
  plpython.py download -c ./csvs --hybrid --no-review"""


def adicionar_opcoes(p: argparse.ArgumentParser, *, update: bool) -> None:
    p.add_argument("csvs", nargs="*", metavar="CSV",
                   help="CSVs específicos; sem eles, processa todos os .csv de --csvfile")
    p.add_argument("-c", "--csvfile", dest="base_dir", metavar="CAMINHO",
                   help="pasta com os CSVs ou um .csv específico (padrão: pasta atual); "
                        "só serve para ler os CSVs, o resto fica na pasta atual")
    oculto(p, "-b", "--csv-dir", "--base-dir", "--BaseDir", dest="base_dir")
    opcoes_pastas(p)
    p.add_argument("-u", "--url-column", dest="row", type=int, default=2, metavar="N",
                   help="coluna da URL, começando em 0; aceita negativo (padrão: 2 = videoUrl)")
    oculto(p, "--row", dest="row", type=int)
    oculto(p, "--lista", dest="csvs_extra", action="append")
    p.add_argument("-a", "--append", action="store_true",
                   help="acrescenta à M3U existente em vez de reescrevê-la")
    p.add_argument("--artist-links", action="store_true",
                   help="cria links do MP3 nas pastas dos artistas colaboradores")
    p.add_argument("--wait-on-limit", dest="esperar_bloqueio", action="store_true",
                   help="espera e tenta de novo se o YouTube bloquear (30s → 10min, até 6x)")
    oculto(p, "--esperar-bloqueio", dest="esperar_bloqueio", action="store_true")
    if not update:
        p.add_argument("--retry", dest="reprocess", action="store_true",
                       help="tenta de novo itens que falharam (o comando update já faz isso)")
        oculto(p, "--reprocess", dest="reprocess", action="store_true")
    p.add_argument("-l", "--log-file", dest="log_file", metavar="ARQ",
                   help="arquivo de log de erros (padrão: ./Logs.txt)")

    h = p.add_argument_group("metadados MusicBrainz (modelo híbrido)")
    h.add_argument("--hybrid", dest="modelo_hibrido", action="store_true",
                   help="confere título/artistas no MusicBrainz e gera metadados_pendentes.csv")
    h.add_argument("--require-isrc", dest="exigir_isrc", action="store_true",
                   help="deixa pendente faixa sem ISRC confirmado (ativa --hybrid)")
    h.add_argument("--require-mb-artist", dest="exigir_artista_musicbrainz", action="store_true",
                   help="exige artista confirmado pelo MusicBrainz (ativa --hybrid)")
    h.add_argument("--prefer-db", dest="preferir", action="store_const", const="musicbrainz",
                   default="youtube",
                   help="se YouTube e MusicBrainz divergirem, usa o MusicBrainz (padrão: YouTube); "
                        "a divergência continua indo para a lista (ativa --hybrid)")
    h.add_argument("--review", dest="revisar", choices=["all", "diff", "none"], default="all",
                   help="o que marcar como OBSERVAR: all = divergências + dados só do YouTube (padrão); "
                        "diff = só divergências; none = não marca nada (ativa --hybrid)")
    h.add_argument("--no-review", dest="revisar", action="store_const", const="none",
                   help="atalho para --review none: segue direto sem lista de conferência")
    oculto(p, "--modelo-hibrido", dest="modelo_hibrido", action="store_true")
    oculto(p, "--exigir-isrc", dest="exigir_isrc", action="store_true")
    oculto(p, "--exigir-artista-musicbrainz", dest="exigir_artista_musicbrainz", action="store_true")

    g = opcoes_bancos(p)
    g.add_argument("--legacy-db", dest="legacy_state_file", default="pltube_state.sqlite3", metavar="ARQ",
                   help="banco antigo único a migrar se existir (padrão: ./pltube_state.sqlite3)")


def criar_parser(prog: str = "plpython.py download") -> argparse.ArgumentParser:
    p = novo_parser(prog, "Baixa o áudio dos vídeos listados nos CSVs e gera uma M3U por CSV.", EXEMPLOS)
    adicionar_opcoes(p, update=False)
    return traduzir_ajuda(p)


def executar(args: argparse.Namespace, *, update: bool = False) -> int:
    import pltube

    # CSVs passados no comando são relativos à pasta atual, não a --csvfile.
    args.lista = [os.path.abspath(c) for c in list(args.csvs) + list(args.csvs_extra or [])]
    args.work_dir = os.getcwd()
    if args.base_dir and os.path.isfile(args.base_dir):
        # --csvfile apontando para um arquivo: processa só ele.
        args.lista.append(os.path.abspath(args.base_dir))
        args.base_dir = os.path.dirname(os.path.abspath(args.base_dir))
    if update:
        args.reprocess = True
    usa_opcao_hibrida = (
        args.exigir_isrc or args.exigir_artista_musicbrainz
        or args.preferir != "youtube" or args.revisar != "all"
    )
    if usa_opcao_hibrida and not args.modelo_hibrido:
        print("Opções de metadados informadas: ativando o modo --hybrid.")
        args.modelo_hibrido = True
    args.state_file = args.downloads_db
    return pltube.executar(args)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
