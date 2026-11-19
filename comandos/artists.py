"""Comando `artists`: o banco de artistas (nome certo + variações) e a correção das músicas."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sqlite3

from comandos._comum import (ativar_verbose, novo_parser, opcoes_bancos, opcoes_pastas, opcoes_verbose,
                             traduzir_ajuda)

NOME = "artists"
APELIDOS = ("artistas",)
RESUMO = "banco de artistas: nome certo, variações e correção das músicas com nome errado"
OPCOES = ("list [BUSCA]  add NOME [-a VAR...]  alias NOME VAR...  rename NOME NOVO  remove NOME\n"
          "import  similar  fix [-y]")
EXEMPLOS = """\
  plpython.py artists                                  # lista artistas e variações
  plpython.py artists list henrique
  plpython.py artists add "Henrique & Juliano" -a "Henrique e Juliano" "H&J"
  plpython.py artists alias "AC/DC" "ACDC" "AC-DC"     # ACDC passa a ser AC/DC
  plpython.py artists alias "Imagine Dragons" "ImagineDragons"
  plpython.py artists rename "WHSPRS" "Whsprs"         # troca o nome certo (o antigo vira variação)
  plpython.py artists remove "H&J"                     # tira a variação (ou o artista inteiro)
  plpython.py artists similar                          # nomes parecidos que podem ser o mesmo artista
  plpython.py artists import                           # traz os artistas das faixas confirmadas
  plpython.py artists fix                              # mostra as músicas com nome de artista errado
  plpython.py artists fix -y                           # corrige: tags, banco, pasta e M3U

O download coloca aqui todo artista confirmado e usa os nomes e as variações para
achar o artista no título do vídeo. Uma variação nunca fica no MP3: vira o nome certo."""


def criar_parser(prog: str = "plpython.py artists") -> argparse.ArgumentParser:
    p = novo_parser(
        prog,
        "Guarda o nome certo de cada artista e as variações com que ele aparece (\"Henrique e "
        "Juliano\", \"ACDC\"). O download usa este banco para achar o artista no título e grava "
        "sempre o nome certo; fix corrige as músicas já baixadas.",
        EXEMPLOS,
    )
    p.add_argument("acao", nargs="?", default="list",
                   choices=("list", "add", "alias", "rename", "remove", "import", "similar", "fix"),
                   help="o que fazer (padrão: list)")
    p.add_argument("nomes", nargs="*", metavar="NOME", help="artista(s), conforme a ação")
    p.add_argument("-a", "--alias", dest="variantes", nargs="+", default=[], metavar="VAR",
                   help="com add: variações do nome")
    p.add_argument("-y", "--apply", action="store_true", help="com fix: grava; sem isto só mostra")
    opcoes_pastas(p)
    opcoes_bancos(p)
    opcoes_verbose(p)
    return traduzir_ajuda(p)


def _abrir(args: argparse.Namespace) -> sqlite3.Connection:
    import pltube

    base = os.getcwd()
    downloads = os.path.join(base, args.downloads_db) if not os.path.isabs(args.downloads_db) else args.downloads_db
    metadados = os.path.join(base, args.metadata_db) if not os.path.isabs(args.metadata_db) else args.metadata_db
    if args.artists_db:
        pltube.ARTISTAS_DB = os.path.abspath(args.artists_db)
    return pltube.abrir_banco_estado(downloads, metadados)


def _caminho_banco(conexao: sqlite3.Connection) -> str:
    return next((l[2] for l in conexao.execute("PRAGMA database_list") if l[1] == "artistas"), "")


def _listar(conexao: sqlite3.Connection, busca: str) -> int:
    import banco_artistas
    from verbose import colorir

    artistas = banco_artistas.listar(conexao, busca)
    if not artistas:
        print("Nenhum artista" + (f" com \"{busca}\"" if busca else " no banco") + ".")
        return 0
    largura = min(40, max(len(a.nome) for a in artistas))
    for a in artistas:
        faixas = f"{a.faixas} faixa(s)" if a.faixas else colorir("sem faixas", "2")
        linha = f"  {colorir(a.nome.ljust(largura), '1')}  {faixas}"
        if a.variantes:
            linha += "  " + colorir("também: " + ", ".join(a.variantes), "36")
        print(linha)
    total_var = sum(len(a.variantes) for a in artistas)
    print(f"\n{len(artistas)} artista(s), {total_var} variação(ões). Banco: {_caminho_banco(conexao)}")
    return 0


def _corrigir_faixas(conexao: sqlite3.Connection, args: argparse.Namespace) -> int:
    """Faixas cujo artista é uma variação: troca pelo nome certo (tags, banco, pasta, M3U)."""
    import aplicar_metadados
    import banco_artistas
    import pltube
    from verbose import colorir

    pasta_musicas = os.path.abspath(args.songs_dir or "songs")
    pasta_playlists = os.path.abspath(args.playlist_dir or "playlists")
    pltube.SONGS_DIR, pltube.PLAYLISTS_DIR = pasta_musicas, pasta_playlists
    pltube.LOG_FILE = os.path.join(os.getcwd(), "Logs.txt")

    planos = []
    for linha in conexao.execute("SELECT video_key FROM music.metadata_resolution ORDER BY video_key").fetchall():
        salvo = pltube.carregar_resolucao(conexao, linha[0])
        if not salvo:
            continue
        resultado = copy.deepcopy(salvo["result"])
        antes = list((resultado.get("metadata") or {}).get("artists") or [])
        trocas = banco_artistas.corrigir_resultado(conexao, resultado)
        if trocas:
            planos.append((linha[0], salvo, resultado, antes, trocas))

    if not planos:
        print("Nenhuma música com nome de artista diferente do banco de artistas.")
        return 0
    modo = "Corrigindo" if args.apply else "Simulação (use -y para gravar)"
    print(f"{modo}: {len(planos)} música(s) com nome de artista a corrigir.\n")
    erros = 0
    for video_key, salvo, resultado, antes, trocas in planos:
        meta = resultado.get("metadata") or {}
        rotulo = f"{', '.join(antes)} — {meta.get('title') or video_key}"
        mudanca = "; ".join(f"{de} → {para}" for de, para in trocas)
        if not args.apply:
            print(f"  {rotulo}: {colorir(mudanca, '36')}")
            continue
        try:
            caminho = salvo.get("audio_path") or ""
            if caminho and os.path.isfile(caminho):
                plano = {
                    "video_key": video_key, "video_id": resultado.get("video_id") or "",
                    "audio_path": aplicar_metadados._validar_audio(caminho, pasta_musicas),
                    "result": resultado, "fields": ["artists"], "confirmed": False,
                }
                aplicar_metadados._aplicar_plano(conexao, plano, pasta_playlists)
            else:  # sem MP3: só o banco
                conexao.execute(
                    "UPDATE music.metadata_resolution SET result_json = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE video_key = ?", (json.dumps(resultado, ensure_ascii=False), video_key))
                pltube._salvar_metadados_musica(conexao, video_key, resultado)
                conexao.commit()
            print(colorir(f"  ✓ {rotulo}: {mudanca}", "32"))
        except Exception as exc:  # noqa: BLE001 - mostra e segue
            conexao.rollback()
            erros += 1
            print(colorir(f"  ✗ {rotulo}: {exc}", "1;31"))
    if not args.apply:
        print("\nNada foi gravado. Repita com -y para corrigir tags, banco, pastas e M3Us.")
    return 1 if erros else 0


def executar(args: argparse.Namespace) -> int:
    ativar_verbose(args)
    import banco_artistas
    from verbose import colorir

    try:
        conexao = _abrir(args)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"Não foi possível abrir o banco: {exc}")
        return 1
    try:
        if banco_artistas.vazio(conexao) and args.acao != "import":
            novos = banco_artistas.importar_biblioteca(conexao)
            if novos:
                print(f"Banco de artistas criado com {novos} artista(s) das faixas já confirmadas.\n")
        acao, nomes = args.acao, args.nomes
        if acao == "list":
            return _listar(conexao, " ".join(nomes))
        if acao == "import":
            print(f"{banco_artistas.importar_biblioteca(conexao)} artista(s) novo(s) importado(s).")
            return 0
        if acao == "similar":
            pares = banco_artistas.parecidos(conexao)
            if not pares:
                print("Nenhum par de nomes parecidos.")
            for a, b in pares:
                print(f"  {a.nome} ({a.faixas})  ≈  {b.nome} ({b.faixas})")
                print(colorir(f"      juntar: plpython.py artists alias \"{a.nome}\" \"{b.nome}\"", "2"))
            return 0
        if acao == "fix":
            return _corrigir_faixas(conexao, args)
        try:
            if acao == "add":
                if not nomes:
                    raise ValueError("diga o nome: artists add \"Nome\" [-a variação ...]")
                for nome in nomes:
                    banco_artistas.adicionar(conexao, nome, "manual")
                    for var in args.variantes:
                        print(f"{var} → {banco_artistas.canonico(conexao, nome)}: "
                              f"{banco_artistas.adicionar_variante(conexao, nome, var)}")
                    print(f"Artista: {banco_artistas.canonico(conexao, nome)}")
            elif acao == "alias":
                if len(nomes) < 2:
                    raise ValueError("use: artists alias \"Nome certo\" \"variação\" [...]")
                for var in nomes[1:]:
                    print(f"{var} → {banco_artistas.canonico(conexao, nomes[0]) or nomes[0]}: "
                          f"{banco_artistas.adicionar_variante(conexao, nomes[0], var)}")
            elif acao == "rename":
                if len(nomes) != 2:
                    raise ValueError("use: artists rename \"Nome atual\" \"Nome novo\"")
                banco_artistas.renomear(conexao, nomes[0], nomes[1])
                extra = "" if banco_artistas.chave(nomes[0]) == banco_artistas.chave(nomes[1]) \
                    else f" ({nomes[0]} ficou como variação)"
                print(f"Agora o nome certo é {nomes[1]}{extra}.")
            elif acao == "remove":
                if not nomes:
                    raise ValueError("diga o nome ou a variação a tirar")
                for nome in nomes:
                    print(f"{nome}: {banco_artistas.remover(conexao, nome)}")
            conexao.commit()
        except ValueError as exc:
            conexao.rollback()
            print(f"Erro: {exc}")
            return 1
        if acao in ("add", "alias", "rename"):
            print(colorir("Rode `plpython.py artists fix` para ver as músicas que mudam.", "2"))
        return 0
    finally:
        conexao.close()


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
