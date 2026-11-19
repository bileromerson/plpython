"""Comando `playlist`: cria/atualiza CSVs a partir de playlists do YouTube.

Lê qualquer playlist pela URL e, com a sua conta do Google (cookies), também as
privadas, os vídeos curtidos, o "assistir mais tarde" e todas as suas playlists.
O CSV sai no mesmo formato dos que você já usa, pronto para `plpython.py download`.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import urllib.parse

from comandos._comum import cookies_ytdlp, descrever_conta, novo_parser, opcoes_conta, traduzir_ajuda

NOME = "playlist"
APELIDOS = ("importar",)
RESUMO = "cria CSVs das suas playlists do YouTube (privadas, curtidas, assistir mais tarde)"
OPCOES = "URL|mine|liked|watch-later...  -c/--csvfile  --name  --cookies-from-browser  --cookies"
EXEMPLOS = """\
  plpython.py playlist mine --cookies-from-browser firefox      # todas as suas playlists
  plpython.py playlist liked --cookies-from-browser firefox     # vídeos curtidos
  plpython.py playlist watch-later --cookies-from-browser firefox
  plpython.py playlist "https://www.youtube.com/playlist?list=PL..." --name Rock
  plpython.py playlist liked -c ./csvs --cookies minha_conta.txt

depois: plpython.py download -c ./csvs --cookies-from-browser firefox"""

ATALHOS = {
    "mine": "https://www.youtube.com/feed/playlists",
    "minhas": "https://www.youtube.com/feed/playlists",
    "liked": ":ytfav",
    "curtidas": ":ytfav",
    "watch-later": ":ytwatchlater",
    "assistir-depois": ":ytwatchlater",
}
PRECISA_CONTA = {"https://www.youtube.com/feed/playlists", ":ytfav", ":ytwatchlater"}
INDISPONIVEIS = {"[deleted video]", "[private video]", "[unavailable video]"}
COLUNAS = ["#", "title", "videoUrl", "videoId", "publishedAt", "position"]


def criar_parser(prog: str = "plpython.py playlist") -> argparse.ArgumentParser:
    p = novo_parser(prog, "Cria um CSV por playlist do YouTube, no formato usado pelo download. "
                          "Rodar de novo atualiza o CSV com os vídeos novos. Playlists privadas, "
                          "curtidas e assistir mais tarde precisam da sua conta (cookies).", EXEMPLOS)
    p.add_argument("alvos", nargs="+", metavar="PLAYLIST",
                   help="URL de playlist, ou: mine (todas as suas), liked (curtidas), "
                        "watch-later (assistir mais tarde)")
    p.add_argument("-c", "--csvfile", dest="pasta", default="csvs", metavar="PASTA",
                   help="pasta onde salvar os CSVs (padrão: ./csvs)")
    p.add_argument("--name", dest="nome", metavar="NOME",
                   help="nome do CSV (só com uma playlist; padrão: o título da playlist)")
    p.add_argument("--include-unavailable", dest="incluir_indisponiveis", action="store_true",
                   help="mantém vídeos privados/apagados no CSV (padrão: ficam de fora)")
    opcoes_conta(p)
    return traduzir_ajuda(p)


def _nome_arquivo(titulo: str) -> str:
    import pltube

    return pltube.nome_seguro(titulo or "playlist", "playlist") + ".csv"


def _url_video(entrada: dict, lista_id: str, posicao: int) -> tuple[str, str]:
    video_id = str(entrada.get("id") or "")
    if not video_id:
        consulta = urllib.parse.parse_qs(urllib.parse.urlparse(str(entrada.get("url") or "")).query)
        video_id = (consulta.get("v") or [""])[0]
    if not video_id:
        return "", ""
    parametros = {"v": video_id}
    if lista_id:
        parametros.update({"list": lista_id, "index": str(posicao)})
    return "https://www.youtube.com/watch?" + urllib.parse.urlencode(parametros), video_id


def escrever_csv(info: dict, pasta: str, nome: str | None, incluir_indisponiveis: bool) -> tuple[str, int, int]:
    """Grava o CSV da playlist. Retorna (caminho, vídeos gravados, ignorados)."""
    lista_id = str(info.get("id") or "")
    if not lista_id.startswith(("PL", "LL", "WL", "OL", "RD", "FL", "UU")):
        lista_id = ""
    linhas, ignorados = [], 0
    for entrada in info.get("entries") or []:
        if not isinstance(entrada, dict):
            continue
        titulo = str(entrada.get("title") or "")
        if titulo.casefold() in INDISPONIVEIS and not incluir_indisponiveis:
            ignorados += 1
            continue
        url, video_id = _url_video(entrada, lista_id, len(linhas) + 1)
        if not url:
            ignorados += 1
            continue
        data = str(entrada.get("timestamp") or entrada.get("upload_date") or "")
        linhas.append([len(linhas) + 1, titulo, url, video_id, data, len(linhas)])
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, _nome_arquivo(nome or str(info.get("title") or lista_id)))
    temporario = caminho + ".tmp"
    with open(temporario, "w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(COLUNAS)
        escritor.writerows(linhas)
    os.replace(temporario, caminho)
    return caminho, len(linhas), ignorados


def executar(args: argparse.Namespace) -> int:
    import pltube

    if pltube.yt_dlp is None:
        print("yt-dlp não está instalado: pip install -r requirements.txt", file=sys.stderr)
        return 1
    cookies = cookies_ytdlp(args)
    alvos = [ATALHOS.get(a.casefold(), a) for a in args.alvos]
    if args.nome and (len(alvos) > 1 or alvos[0] == ATALHOS["mine"]):
        print("--name só vale para uma única playlist.", file=sys.stderr)
        return 2
    sem_conta = [a for a in args.alvos if ATALHOS.get(a.casefold()) in PRECISA_CONTA]
    if sem_conta and not cookies:
        print(f"'{sem_conta[0]}' precisa da sua conta. Rode com --cookies-from-browser firefox "
              "(ou --cookies arquivo.txt).", file=sys.stderr)
        return 2
    if cookies:
        print(f"Usando {descrever_conta(cookies)}.")

    opcoes = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": "in_playlist", "sleep_interval_requests": pltube.PAUSA_ENTRE_REQUISICOES,
        **cookies,
    }
    criados, erros = [], 0
    with pltube.yt_dlp.YoutubeDL(opcoes) as ydl:
        fila = list(alvos)
        while fila:
            alvo = fila.pop(0)
            try:
                info = ydl.extract_info(alvo, download=False)
            except Exception as exc:  # yt-dlp: DownloadError/ExtractorError
                erros += 1
                print(f"Não foi possível ler {alvo}: {exc}", file=sys.stderr)
                pltube.dica_login(str(exc))
                if pltube.tipo_bloqueio(str(exc)) == "sessao":
                    print("O YouTube limitou a sessão; espere cerca de 1 hora.", file=sys.stderr)
                    break
                continue
            if not info:
                erros += 1
                continue
            if alvo == ATALHOS["mine"]:
                # A página "suas playlists" lista playlists; cada uma vira um CSV.
                urls = [e.get("url") for e in info.get("entries") or [] if isinstance(e, dict) and e.get("url")]
                print(f"{len(urls)} playlists encontradas na sua conta.")
                fila = urls + fila
                continue
            caminho, gravados, ignorados = escrever_csv(info, args.pasta, args.nome, args.incluir_indisponiveis)
            criados.append(caminho)
            extra = f" ({ignorados} privados/apagados ignorados)" if ignorados else ""
            print(f"{os.path.relpath(caminho)}: {gravados} vídeos{extra}")

    if criados:
        if args.cookies_navegador:
            conta = f" --cookies-from-browser {args.cookies_navegador}"
        elif args.cookies_arquivo:
            conta = f" --cookies {args.cookies_arquivo}"
        else:
            conta = ""
        print(f"\nPronto. Para baixar: python plpython.py download -c {args.pasta}{conta}")
    return 1 if erros and not criados else 0


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
