"""Comando `review`: consulta e confirma as faixas do banco SQL no terminal.

Não precisa de nenhum programa de SQL instalado. Lê ./sql/ da pasta atual.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys

import pltube
from comandos._comum import novo_parser, opcoes_bancos, traduzir_ajuda

NOME = "review"
APELIDOS = ("revisar",)
RESUMO = "mostra e confirma as faixas do banco SQL direto no terminal"
OPCOES = "--status  --busca  -d  --ok ID...  --ok-all  --export  --sql"
EXEMPLOS = """\
  plpython.py review                      # faixas para conferir (OBSERVAR/PENDENTE)
  plpython.py review -d                   # com álbum, arquivo e link do YouTube
  plpython.py review --status todas --busca snuffles
  plpython.py review --ok UG1HUOyL9IA njowoGhmw6E
  plpython.py review --ok-all             # confirma todas as OBSERVAR
  plpython.py review --export conferir.csv
  plpython.py review --sql "SELECT title, album FROM music_tracks\""""

STATUS_FILTRO = {
    "revisar": ("PENDENTE", "OBSERVAR"),
    "observar": ("OBSERVAR",),
    "pendente": ("PENDENTE",),
    "resolvido": ("RESOLVIDO",),
    "todas": (),
}


def criar_parser(prog: str = "plpython.py review") -> argparse.ArgumentParser:
    p = novo_parser(prog, "Mostra as faixas guardadas em ./sql/ e permite confirmá-las.", EXEMPLOS)
    p.add_argument("--status", choices=list(STATUS_FILTRO), default="revisar",
                   help="quais faixas mostrar (padrão: revisar = OBSERVAR + PENDENTE)")
    p.add_argument("--busca", metavar="TEXTO", help="filtra por título, artista, álbum ou ID do vídeo")
    p.add_argument("-d", "--detalhes", action="store_true", help="mostra álbum, ISRC, arquivo e link")
    p.add_argument("--ok", nargs="+", metavar="VIDEO_ID",
                   help="marca as faixas como conferidas (RESOLVIDO), mantendo título/artista atuais")
    p.add_argument("--ok-all", action="store_true", help="confirma todas as faixas OBSERVAR de uma vez")
    p.add_argument("--export", metavar="CSV", help="salva a lista filtrada num CSV para abrir no Excel")
    p.add_argument("--sql", metavar="SELECT", help="executa uma consulta SQL somente leitura")
    opcoes_bancos(p)
    return traduzir_ajuda(p)


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))


def _abrir(args: argparse.Namespace) -> sqlite3.Connection:
    base = os.getcwd()
    downloads = args.downloads_db if os.path.isabs(args.downloads_db) else os.path.join(base, args.downloads_db)
    metadados = args.metadata_db if os.path.isabs(args.metadata_db) else os.path.join(base, args.metadata_db)
    if not os.path.isfile(metadados):
        raise FileNotFoundError(
            f"banco não encontrado: {metadados}\n"
            "Rode o comando na mesma pasta onde rodou o download, ou use --metadata-db/--downloads-db."
        )
    return pltube.abrir_banco_estado(downloads, metadados)


def _artistas(texto: str) -> str:
    try:
        return ", ".join(json.loads(texto or "[]"))
    except (TypeError, ValueError):
        return texto or ""


def _linhas(conexao: sqlite3.Connection, status: tuple[str, ...], busca: str) -> list[sqlite3.Row]:
    where, params = [], []
    if status:
        where.append(f"t.metadata_status IN ({','.join('?' * len(status))})")
        params += list(status)
    if busca:
        where.append("(t.title LIKE ? OR t.artists_json LIKE ? OR t.album LIKE ? OR t.video_id = ?)")
        params += [f"%{busca}%"] * 3 + [busca]
    sql = (
        "SELECT t.video_key, t.video_id, t.title, t.artists_json, t.album, t.isrc, "
        "t.metadata_status, t.review_note, r.audio_path "
        "FROM music.music_tracks t LEFT JOIN music.metadata_resolution r ON r.video_key = t.video_key"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY t.metadata_status, t.artists_json, t.title"
    )
    return conexao.execute(sql, params).fetchall()


def listar(conexao: sqlite3.Connection, args: argparse.Namespace) -> int:
    linhas = _linhas(conexao, STATUS_FILTRO[args.status], args.busca or "")
    if not linhas:
        print("Nenhuma faixa encontrada com esse filtro.")
        return 0
    for linha in linhas:
        print(f"[{linha['metadata_status']}] {linha['video_id']}  "
              f"{_artistas(linha['artists_json']) or '?'} — {linha['title'] or '?'}")
        if args.detalhes:
            if linha["album"]:
                print(f"    álbum: {linha['album']}")
            if linha["isrc"]:
                print(f"    ISRC: {linha['isrc']}")
            if linha["audio_path"]:
                print(f"    arquivo: {pltube._resolver_caminho_banco(linha['audio_path'], conexao)}")
            print(f"    https://youtu.be/{linha['video_id']}")
        if linha["review_note"]:
            for nota in linha["review_note"].split(" | "):
                print(f"    • {nota}")
    contagem = conexao.execute(
        "SELECT metadata_status, COUNT(*) FROM music.music_tracks GROUP BY metadata_status"
    ).fetchall()
    print("\nTotal: " + ", ".join(f"{s}={n}" for s, n in contagem))
    if args.status in ("revisar", "observar", "pendente"):
        print("Se estiver certo: plpython.py review --ok VIDEO_ID [VIDEO_ID ...]  (ou --ok-all)")
    return 0


def confirmar(conexao: sqlite3.Connection, ids: list[str], todos_observar: bool) -> int:
    if todos_observar:
        ids = [r[0] for r in conexao.execute(
            "SELECT video_id FROM music.metadata_resolution WHERE status = 'OBSERVAR'"
        ).fetchall()]
    erros = 0
    for video_id in ids:
        linha = conexao.execute(
            "SELECT video_key FROM music.metadata_resolution WHERE video_id = ? OR video_key = ?",
            (video_id, video_id),
        ).fetchone()
        salvo = pltube.carregar_resolucao(conexao, linha["video_key"]) if linha else None
        if not salvo:
            print(f"Não encontrado: {video_id}")
            erros += 1
            continue
        resultado = salvo["result"]
        metadata = resultado.get("metadata") or {}
        if not metadata.get("title") or not metadata.get("artists"):
            print(f"{video_id}: sem título/artista; corrija pela planilha (plpython.py metadata).")
            erros += 1
            continue
        resultado["status"] = "RESOLVIDO"
        resultado["pending_reasons"] = []
        resultado["observations"] = []
        resultado["manual_review"] = {"fields": [], "confirmed": True, "via": "review --ok"}
        for campo in ("title", "artists"):
            (resultado.setdefault("confidence", {}))[campo] = "confirmada_manual"
        pltube.salvar_resolucao(conexao, linha["video_key"], resultado, salvo.get("audio_path") or "")
        caminho = salvo.get("audio_path") or ""
        if caminho and os.path.isfile(caminho):
            pltube.aplicar_tags_hibridas(caminho, resultado)
            atalho = os.path.join(os.path.dirname(os.path.dirname(caminho)), "_observar", os.path.basename(caminho))
            if os.path.islink(atalho):
                os.unlink(atalho)
        print(f"Confirmado: {_artistas(json.dumps(metadata.get('artists')))} — {metadata.get('title')}")
    return 1 if erros else 0


def consulta_sql(conexao: sqlite3.Connection, sql: str) -> int:
    """Executa um SELECT somente leitura e imprime o resultado."""
    conexao.execute("PRAGMA query_only = ON")
    try:
        cursor = conexao.execute(sql)
    except sqlite3.Error as exc:
        print(f"Erro no SQL: {exc}")
        print("Tabelas: music.music_tracks, music.metadata_resolution, download_jobs, video_cache, playlist_items")
        return 1
    colunas = [c[0] for c in cursor.description or []]
    linhas = cursor.fetchall()
    if colunas:
        print(" | ".join(colunas))
        print("-" * min(120, len(" | ".join(colunas))))
    for linha in linhas:
        print(" | ".join("" if v is None else str(v) for v in linha))
    print(f"({len(linhas)} linha(s))")
    return 0


def exportar(conexao: sqlite3.Connection, args: argparse.Namespace) -> int:
    linhas = _linhas(conexao, STATUS_FILTRO[args.status], args.busca or "")
    with open(args.export, "w", encoding="utf-8-sig", newline="") as arquivo:
        writer = csv.writer(arquivo)
        writer.writerow(["status", "video_id", "artistas", "titulo", "album", "isrc", "observacoes", "arquivo", "youtube"])
        for l in linhas:
            writer.writerow([
                l["metadata_status"], l["video_id"], _artistas(l["artists_json"]), l["title"],
                l["album"], l["isrc"], l["review_note"],
                pltube._resolver_caminho_banco(l["audio_path"] or "", conexao),
                f"https://youtu.be/{l['video_id']}",
            ])
    print(f"{len(linhas)} faixa(s) exportada(s) para {args.export} (abre no Excel/LibreOffice)")
    return 0


def executar(args: argparse.Namespace) -> int:
    try:
        conexao = _abrir(args)
    except (FileNotFoundError, sqlite3.Error, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    try:
        if args.sql:
            return consulta_sql(conexao, args.sql)
        if args.ok or args.ok_all:
            return confirmar(conexao, args.ok or [], args.ok_all)
        if args.export:
            return exportar(conexao, args)
        return listar(conexao, args)
    finally:
        conexao.close()
