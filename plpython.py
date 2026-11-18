"""Entrada única do projeto: `python plpython.py <comando> [opções]`.

Comandos:
  download   baixa áudio dos CSVs do YouTube e gera as M3Us
  update     relê os CSVs, tenta de novo o que falhou e atualiza as M3Us
  metadata   revisa/aplica a planilha metadados_pendentes.csv (simula por padrão)
  review     mostra/confirma as faixas do banco SQL no terminal
  spotify    mostra as faixas de uma playlist Spotify (somente consulta)
  revert     desfaz o layout legado songs/_library (simula por padrão)
  legacy-centralize  cria o layout legado songs/_library (não recomendado)

Use `python plpython.py <comando> -h` para ver as opções de cada um.
"""

from __future__ import annotations

import argparse
import os
import sys

EXEMPLOS = """\
exemplos:
  python plpython.py download -c ./csvs
  python plpython.py download csvs/Alone.csv csvs/Rain.csv --artist-links
  python plpython.py download -c ./csvs -s /media/Musicas -p ./m3u --hybrid
  python plpython.py update -c ./csvs --wait-on-limit
  python plpython.py download -c ./csvs --hybrid --prefer-db
  python plpython.py download -c ./csvs --hybrid --review diff
  python plpython.py metadata            # simula
  python plpython.py metadata --apply    # grava
  python plpython.py review
  python plpython.py spotify https://open.spotify.com/playlist/ID
  python plpython.py revert -s songs --apply
"""


def _oculto(parser: argparse.ArgumentParser, *nomes: str, **kwargs) -> None:
    """Aceita nomes antigos sem mostrá-los na ajuda (compatibilidade)."""
    parser.add_argument(*nomes, help=argparse.SUPPRESS, **kwargs)


def _opcoes_pastas(p: argparse.ArgumentParser, *, com_csv: bool) -> None:
    if com_csv:
        p.add_argument("-c", "--csvfile", dest="base_dir", metavar="CAMINHO",
                       help="pasta com os CSVs ou um .csv específico (padrão: pasta atual); "
                            "só serve para ler os CSVs, o resto fica na pasta atual")
        _oculto(p, "-b", "--csv-dir", "--base-dir", "--BaseDir", dest="base_dir")
    p.add_argument("-s", "--songs-dir", dest="songs_dir", metavar="PASTA",
                   help="pasta das músicas (padrão: ./songs)")
    p.add_argument("-p", "--playlist-dir", dest="playlist_dir", metavar="PASTA",
                   help="pasta das M3Us (padrão: ./playlists)")
    _oculto(p, "--SongsDir", dest="songs_dir")
    _oculto(p, "--PlaylistDir", dest="playlist_dir")


def _opcoes_bancos(p: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    g = p.add_argument_group("bancos SQLite (avançado)")
    g.add_argument("--downloads-db", default=os.path.join("sql", "downloads.sqlite3"), metavar="ARQ",
                   help="fila/progresso de downloads (padrão: ./sql/downloads.sqlite3)")
    g.add_argument("--metadata-db", default=os.path.join("sql", "musicas.sqlite3"), metavar="ARQ",
                   help="metadados das faixas (padrão: ./sql/musicas.sqlite3)")
    _oculto(p, "--state-file", dest="downloads_db")
    return g


def _opcoes_download(p: argparse.ArgumentParser, *, update: bool) -> None:
    p.add_argument("csvs", nargs="*", metavar="CSV",
                   help="CSVs específicos; sem eles, processa todos os .csv de --csvfile")
    _opcoes_pastas(p, com_csv=True)
    p.add_argument("-u", "--url-column", dest="row", type=int, default=2, metavar="N",
                   help="coluna da URL, começando em 0; aceita negativo (padrão: 2 = videoUrl)")
    _oculto(p, "--row", dest="row", type=int)
    _oculto(p, "--lista", dest="csvs_extra", action="append")
    p.add_argument("-a", "--append", action="store_true",
                   help="acrescenta à M3U existente em vez de reescrevê-la")
    p.add_argument("--artist-links", action="store_true",
                   help="cria links do MP3 nas pastas dos artistas colaboradores")
    p.add_argument("--wait-on-limit", dest="esperar_bloqueio", action="store_true",
                   help="espera e tenta de novo se o YouTube bloquear (30s → 10min, até 6x)")
    _oculto(p, "--esperar-bloqueio", dest="esperar_bloqueio", action="store_true")
    if not update:
        p.add_argument("--retry", dest="reprocess", action="store_true",
                       help="tenta de novo itens que falharam (o comando update já faz isso)")
        _oculto(p, "--reprocess", dest="reprocess", action="store_true")
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
    _oculto(p, "--modelo-hibrido", dest="modelo_hibrido", action="store_true")
    _oculto(p, "--exigir-isrc", dest="exigir_isrc", action="store_true")
    _oculto(p, "--exigir-artista-musicbrainz", dest="exigir_artista_musicbrainz", action="store_true")

    g = _opcoes_bancos(p)
    g.add_argument("--legacy-db", dest="legacy_state_file", default="pltube_state.sqlite3", metavar="ARQ",
                   help="banco antigo único a migrar se existir (padrão: ./pltube_state.sqlite3)")


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plpython.py",
        description="Playlists do YouTube → MP3 + M3U, com revisão de metadados.",
        epilog=EXEMPLOS,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="comando", metavar="COMANDO", required=True)
    fmt = argparse.RawDescriptionHelpFormatter

    p = sub.add_parser("download", aliases=["baixar"], formatter_class=fmt,
                       help="baixa os CSVs e gera as M3Us",
                       epilog="exemplos:\n"
                              "  plpython.py download -c ./csvs\n"
                              "  plpython.py download csvs/Alone.csv --artist-links --append\n"
                              "  plpython.py download -c ./csvs --hybrid --require-isrc\n"
                              "  plpython.py download -c ./csvs --prefer-db       # MusicBrainz vence divergências\n"
                              "  plpython.py download -c ./csvs --hybrid --no-review")
    _opcoes_download(p, update=False)

    p = sub.add_parser("update", aliases=["atualizar"], formatter_class=fmt,
                       help="relê os CSVs, refaz falhas e pendências sem rebaixar o que já existe",
                       epilog="exemplos:\n"
                              "  plpython.py update -c ./csvs\n"
                              "  plpython.py update csvs/Alone.csv --wait-on-limit --hybrid")
    _opcoes_download(p, update=True)

    p = sub.add_parser("metadata", aliases=["metadados"], formatter_class=fmt,
                       help="aplica as revisões de metadados_pendentes.csv (simula sem --apply)",
                       epilog="exemplos:\n"
                              "  plpython.py metadata\n"
                              "  plpython.py metadata --apply\n"
                              "  plpython.py metadata --sheet revisao.csv --apply")
    _opcoes_pastas(p, com_csv=False)
    p.add_argument("--sheet", dest="arquivo", default="metadados_pendentes.csv", metavar="CSV",
                   help="planilha revisada (padrão: ./metadados_pendentes.csv)")
    _oculto(p, "--arquivo", dest="arquivo")
    p.add_argument("--apply", action="store_true", help="grava as mudanças; sem isto só simula")
    _opcoes_bancos(p)

    p = sub.add_parser("review", aliases=["revisar"], formatter_class=fmt,
                       help="mostra/confirma as faixas do banco SQL direto no terminal",
                       epilog="exemplos:\n"
                              "  plpython.py review                      # faixas para conferir (OBSERVAR/PENDENTE)\n"
                              "  plpython.py review -d                   # com álbum, arquivo e link do YouTube\n"
                              "  plpython.py review --status todas --busca snuffles\n"
                              "  plpython.py review --ok UG1HUOyL9IA njowoGhmw6E\n"
                              "  plpython.py review --ok-all             # confirma todas as OBSERVAR\n"
                              "  plpython.py review --export conferir.csv\n"
                              "  plpython.py review --sql \"SELECT title, album FROM music_tracks\"")
    p.add_argument("--status", choices=["revisar", "observar", "pendente", "resolvido", "todas"],
                   default="revisar", help="quais faixas mostrar (padrão: revisar = OBSERVAR + PENDENTE)")
    p.add_argument("--busca", metavar="TEXTO", help="filtra por título, artista, álbum ou ID do vídeo")
    p.add_argument("-d", "--detalhes", action="store_true", help="mostra álbum, ISRC, arquivo e link")
    p.add_argument("--ok", nargs="+", metavar="VIDEO_ID",
                   help="marca as faixas como conferidas (RESOLVIDO), mantendo título/artista atuais")
    p.add_argument("--ok-all", action="store_true", help="confirma todas as faixas OBSERVAR de uma vez")
    p.add_argument("--export", metavar="CSV", help="salva a lista filtrada num CSV para abrir no Excel")
    p.add_argument("--sql", metavar="SELECT", help="executa uma consulta SQL somente leitura")
    _opcoes_bancos(p)

    p = sub.add_parser("spotify", formatter_class=fmt,
                       help="lista faixas de uma playlist Spotify (somente leitura)",
                       epilog="requer SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET e SPOTIFY_REDIRECT_URI\n"
                              "exemplo:\n  plpython.py spotify spotify:playlist:37i9dQZF1DXcBWIGoYBM5M")
    p.add_argument("playlist", help="URL, URI ou ID da playlist")

    p = sub.add_parser("revert", aliases=["reverter"], formatter_class=fmt,
                       help="move MP3s de songs/_library de volta para as pastas dos artistas",
                       epilog="exemplos:\n"
                              "  plpython.py revert -s songs\n"
                              "  plpython.py revert -s songs -p ./minhas_playlists --apply")
    p.add_argument("-s", "--songs-dir", dest="root", default="songs", metavar="PASTA",
                   help="pasta das músicas (padrão: songs)")
    _oculto(p, "--root", dest="root")
    p.add_argument("-p", "--playlist-dir", metavar="PASTA",
                   help="pasta das M3Us (padrão: playlists ao lado da pasta de músicas)")
    p.add_argument("--downloads-db", dest="state_file", metavar="ARQ",
                   help="banco do download para recuperar a ordem dos artistas (autodetectado)")
    _oculto(p, "--state-file", dest="state_file")
    p.add_argument("--apply", action="store_true", help="aplica; sem isto só simula")

    p = sub.add_parser("legacy-centralize", formatter_class=fmt,
                       help="[legado] centraliza MP3s em songs/_library (o oposto de revert)",
                       epilog="Não use --apply se quiser manter o layout atual por artista.\n"
                              "exemplo:\n  plpython.py legacy-centralize -s songs")
    p.add_argument("-s", "--songs-dir", dest="root", default="songs", metavar="PASTA",
                   help="pasta das músicas (padrão: songs)")
    _oculto(p, "--root", dest="root")
    p.add_argument("--apply", action="store_true", help="aplica; sem isto só simula")
    return parser


_ALIASES = {"revisar": "review", "baixar": "download", "atualizar": "update", "metadados": "metadata", "reverter": "revert"}


def main(argv: list[str] | None = None) -> int:
    args = criar_parser().parse_args(argv)
    comando = _ALIASES.get(args.comando, args.comando)

    if comando in ("download", "update"):
        import pltube
        # CSVs passados no comando são relativos à pasta atual, não a --csvfile.
        args.lista = [os.path.abspath(c) for c in list(args.csvs) + list(args.csvs_extra or [])]
        args.work_dir = os.getcwd()
        if args.base_dir and os.path.isfile(args.base_dir):
            # --csvfile apontando para um arquivo: processa só ele.
            args.lista.append(os.path.abspath(args.base_dir))
            args.base_dir = os.path.dirname(os.path.abspath(args.base_dir))
        if comando == "update":
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

    if comando == "metadata":
        import aplicar_metadados
        args.base_dir = os.getcwd()
        args.work_dir = args.base_dir
        return aplicar_metadados.executar(args)

    if comando == "review":
        import revisao
        return revisao.executar(args)

    if comando == "spotify":
        import spotify_playlist_viewer
        return spotify_playlist_viewer.executar(args)

    if comando == "revert":
        import reverter_biblioteca
        return reverter_biblioteca.executar(args)

    if comando == "legacy-centralize":
        import sincronizar_biblioteca
        if args.apply:
            print("Aviso: isto cria o layout legado songs/_library.", file=sys.stderr)
        return sincronizar_biblioteca.executar(args)

    return 2  # inalcançável: subcomando é obrigatório


if __name__ == "__main__":
    raise SystemExit(main())
