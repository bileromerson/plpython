"""Comando `download`: baixa áudio dos CSVs do YouTube e gera as M3Us.

Tudo sobre downloads fica aqui: opções, validações e a chamada ao pltube.
O `update` reaproveita este arquivo, apenas forçando a nova tentativa.
"""

from __future__ import annotations

import argparse
import os

from comandos._comum import (
    ativar_verbose, cookies_ytdlp, novo_parser, opcoes_bancos, opcoes_conta, opcoes_pastas,
    opcoes_verbose, traduzir_ajuda,
)

NOME = "download"
APELIDOS = ("baixar",)
RESUMO = "baixa os CSVs e gera as M3Us"
OPCOES = ("CSV...  -c/--csvfile  -s  -p  -u/--url-column  -a/--append  -P/--progress  -v/-vv/-vvv\n"
          "-w/--wait-on-limit  -r/--retry  -i/--ask  -x/--skip  -nr/--no-review  -nh/--no-hybrid  --review\n"
          "-b/--cookies-from-browser  -k/--cookies  --sleep  --no-artist-links")
EXEMPLOS = """\
  plpython.py download -c ./csvs                      # identifica pelo YouTube Music: artista, álbum e capa
  plpython.py download -c ./csvs/Alone.csv            # só uma playlist
  plpython.py download csvs/Alone.csv csvs/Rain.csv --append
  plpython.py download -c ./csvs -s Musicas -p ./m3u
  plpython.py download -c ./csvs --ask                # no fim, pergunta o que tiver sinal de erro
  plpython.py download -c ./csvs --review all         # confere também o que não está no YouTube Music
  plpython.py download -c ./csvs --no-hybrid          # sem identificar: título e canal do vídeo
  plpython.py download -c ./csvs --wait-on-limit --sleep 10-20   # playlist grande, sem pressa
  plpython.py download -c ./csvs --cookies-from-browser firefox  # com a sua conta do Google
  plpython.py download -c ./csvs/Alone.csv -v         # mostra cada passo (-vv: tudo)
  plpython.py download -c ./csvs -P                   # barra de progresso no rodapé"""


def _pausa(valor: str) -> tuple[float, float]:
    try:
        partes = [float(v) for v in valor.replace(",", ".").split("-", 1)]
    except ValueError:
        raise argparse.ArgumentTypeError("use segundos, ex.: 8 ou 5-10") from None
    minimo, maximo = (partes[0], partes[-1])
    if minimo < 0 or maximo < minimo:
        raise argparse.ArgumentTypeError("use MIN-MAX com 0 <= MIN <= MAX, ex.: 5-10")
    return (minimo, maximo)


def adicionar_opcoes(p: argparse.ArgumentParser, *, update: bool) -> None:
    p.add_argument("csvs", nargs="*", metavar="CSV",
                   help="CSVs específicos; sem eles, processa todos os .csv de --csvfile")
    p.add_argument("-c", "--csvfile", dest="base_dir", metavar="CAMINHO",
                   help="pasta com os CSVs ou um .csv específico (padrão: pasta atual); "
                        "só serve para ler os CSVs, o resto fica na pasta atual")
    opcoes_pastas(p)
    p.add_argument("-u", "--url-column", dest="row", type=int, default=2, metavar="N",
                   help="coluna da URL, começando em 0; aceita negativo (padrão: 2 = videoUrl)")
    p.add_argument("-a", "--append", action="store_true",
                   help="acrescenta à M3U existente em vez de reescrevê-la")
    p.add_argument("--no-artist-links", dest="artist_links", action="store_false", default=True,
                   help="não cria atalhos nas pastas dos artistas secundários "
                        "(padrão: o MP3 fica no artista principal e os outros recebem um atalho)")
    p.add_argument("-w", "--wait-on-limit", dest="esperar_bloqueio", action="store_true",
                   help="se o YouTube bloquear, espera sozinho e tenta de novo: limite da sessão "
                        "(dura até 1 h) 5, 15, 30 e 60 min; falha de rede 30 s até 10 min")
    p.add_argument("-P", "--progress", dest="progresso", action="store_true",
                   help="barra de progresso fixa na última linha do terminal, como no pip: "
                        "porcentagem do total, música atual, etapa e tempo restante")
    p.add_argument("--sleep", dest="pausa", type=_pausa, default=(5.0, 10.0), metavar="MIN[-MAX]",
                   help="pausa entre vídeos no YouTube, em segundos, sorteada entre MIN e MAX "
                        "(padrão: 5-10, como recomenda o yt-dlp; o tempo do download já conta). "
                        "0 desliga")
    if not update:
        p.add_argument("-r", "--retry", dest="reprocess", action="store_true",
                       help="tenta de novo itens que falharam (o comando update já faz isso)")
    p.add_argument("-l", "--log-file", dest="log_file", metavar="ARQ",
                   help="arquivo de log de erros (padrão: ./Logs.txt)")

    h = p.add_argument_group("identificação pelo YouTube Music (ligada por padrão)")
    h.add_argument("-nh", "--no-hybrid", dest="modelo_hibrido", action="store_false", default=True,
                   help="desliga a identificação: usa o título e o canal do vídeo, sem YouTube Music, "
                        "capa do álbum nem lista de conferência (mais rápido)")
    h.add_argument("--review", dest="revisar", choices=["errors", "all", "none", "diff"], default="errors",
                   help="o que vai para conferência (OBSERVAR): errors = só faixas com sinal forte de erro "
                        "(padrão); all = também as que não estão no YouTube Music (deduzidas do título/canal); "
                        "none = nada")
    h.add_argument("-i", "--ask", dest="perguntar", action="store_true",
                   help="no fim, pergunta os campos com sinal de erro das faixas desta execução; "
                        "é o mesmo que rodar review --answer só nelas")
    h.add_argument("-x", "--skip", dest="ignorar", nargs="+", action="extend", default=[], metavar="CAMPO",
                   help="com --ask: não pergunta estes campos (title, artists, album, isrc)")
    h.add_argument("-nr", "--no-review", dest="revisar", action="store_const", const="none",
                   help="atalho para --review none: segue direto sem lista de conferência")

    opcoes_verbose(p)
    opcoes_conta(p)
    g = opcoes_bancos(p)
    g.add_argument("--legacy-db", dest="legacy_state_file", default="pltube_state.sqlite3", metavar="ARQ",
                   help="banco antigo único a migrar se existir (padrão: ./pltube_state.sqlite3)")


def criar_parser(prog: str = "plpython.py download") -> argparse.ArgumentParser:
    p = novo_parser(prog, "Baixa o áudio dos vídeos dos CSVs, identifica artista/álbum/capa e gera "
                          "uma M3U por CSV. O MP3 fica na pasta do artista principal; os demais "
                          "artistas recebem um atalho. O que não der para confirmar vai para "
                          "conferência (plpython.py review).", EXEMPLOS)
    adicionar_opcoes(p, update=False)
    return traduzir_ajuda(p)


def executar(args: argparse.Namespace, *, update: bool = False) -> int:
    import pltube

    ativar_verbose(args)
    # CSVs passados no comando são relativos à pasta atual, não a --csvfile.
    args.lista = [os.path.abspath(c) for c in list(args.csvs)]
    args.work_dir = os.getcwd()
    if args.base_dir and os.path.isfile(args.base_dir):
        # --csvfile apontando para um arquivo: processa só ele.
        args.lista.append(os.path.abspath(args.base_dir))
        args.base_dir = os.path.dirname(os.path.abspath(args.base_dir))
    if update:
        args.reprocess = True
    if args.revisar not in ("errors", "diff") and not args.modelo_hibrido:
        print("Aviso: --no-hybrid ignora --review.")
    args.state_file = args.downloads_db
    args.cookies_ytdlp = cookies_ytdlp(args)
    return pltube.executar(args)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
