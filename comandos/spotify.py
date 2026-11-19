"""Comando `spotify`: lê as playlists da sua conta e salva as músicas em spotify.csv."""

from __future__ import annotations

import argparse

from comandos._comum import ativar_verbose, novo_parser, opcoes_verbose, traduzir_ajuda

NOME = "spotify"
APELIDOS: tuple[str, ...] = ()
RESUMO = "lê suas playlists do Spotify e salva as músicas em spotify.csv (não baixa nada)"
OPCOES = "PLAYLIST...  -o/--output  -L/--liked  --client-id  --login  -v"
EXEMPLOS = """\
  plpython.py spotify --client-id SEU_CLIENT_ID    # 1ª vez: abre o navegador para entrar
  plpython.py spotify                              # todas as suas playlists → spotify.csv
  plpython.py spotify -L                           # inclui as Músicas Curtidas
  plpython.py spotify Rock "Pop 2024"              # só estas (e lista as músicas delas)
  plpython.py spotify https://open.spotify.com/playlist/ID
  plpython.py spotify --login                      # entra de novo (trocar de conta)

depois: plpython.py sync   (compara com as músicas baixadas e acerta as M3Us)

Como criar o app (uma vez): developer.spotify.com/dashboard → Create app → em
Redirect URIs coloque http://127.0.0.1:8888/callback e marque Web API; copie o
Client ID. O dono do app precisa ter Premium. Desde 2026 o Spotify só entrega as
faixas das playlists que você criou ou em que colabora."""


def criar_parser(prog: str = "plpython.py spotify") -> argparse.ArgumentParser:
    p = novo_parser(
        prog,
        "Lê as playlists da sua conta Spotify e salva nome da playlist, código, ISRC, artista e "
        "título de cada música em spotify.csv. Não baixa nada. Mostra o que entrou e saiu de "
        "cada playlist desde a última leitura.",
        EXEMPLOS,
    )
    p.add_argument("playlists", nargs="*", metavar="PLAYLIST",
                   help="só estas playlists (nome, URL, URI ou ID); as outras ficam como estavam no CSV")
    p.add_argument("-o", "--output", default="spotify.csv", metavar="ARQ",
                   help="onde salvar a lista (padrão: ./spotify.csv)")
    p.add_argument("-L", "--liked", action="store_true", help="inclui as Músicas Curtidas")
    conta = p.add_argument_group("login no Spotify")
    conta.add_argument("--client-id", metavar="ID",
                       help="Client ID do seu app no Spotify; fica salvo depois do 1º login "
                            "(também lido da variável SPOTIFY_CLIENT_ID)")
    conta.add_argument("--redirect-uri", metavar="URI",
                       help="endereço de retorno cadastrado no app (padrão: "
                            "http://127.0.0.1:8888/callback; variável SPOTIFY_REDIRECT_URI)")
    conta.add_argument("--login", action="store_true", help="entra de novo, por exemplo para trocar de conta")
    opcoes_verbose(p)
    return traduzir_ajuda(p)


def executar(args: argparse.Namespace) -> int:
    ativar_verbose(args)
    import spotify_conta

    return spotify_conta.executar(args)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
