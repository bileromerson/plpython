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
from comandos._comum import ativar_verbose, opcoes_verbose, novo_parser, opcoes_bancos, traduzir_ajuda

NOME = "review"
APELIDOS = ("revisar",)
RESUMO = "mostra e confirma as faixas do banco SQL direto no terminal"
OPCOES = "--answer  --default-ok  --status  --search  -d/--details  --ok ID...  --ok-all  --export  --sql"
EXEMPLOS = """\
  plpython.py review                      # faixas para conferir (OBSERVAR/PENDENTE)
  plpython.py review --answer             # responde uma por uma (Enter = deixa na lista)
  plpython.py review --answer --default-ok   # Enter = aceita (conflito: banco; vazio: Unknown)
  plpython.py review -d                   # com álbum, arquivo e link do YouTube
  plpython.py review --status all --search snuffles
  plpython.py review --ok UG1HUOyL9IA njowoGhmw6E
  plpython.py review --ok-all             # confirma todas as OBSERVAR
  plpython.py review --export conferir.csv
  plpython.py review --sql "SELECT title, album FROM music_tracks\""""

DESCONHECIDO = "Unknown"
STATUS_FILTRO = {
    "open": ("PENDENTE", "OBSERVAR"),
    "observe": ("OBSERVAR",),
    "pending": ("PENDENTE",),
    "resolved": ("RESOLVIDO",),
    "all": (),
}


def criar_parser(prog: str = "plpython.py review") -> argparse.ArgumentParser:
    p = novo_parser(prog, "Mostra as faixas guardadas em ./sql/ e permite confirmá-las.", EXEMPLOS)
    p.add_argument("--status", choices=list(STATUS_FILTRO), default="open",
                   help="quais faixas mostrar: open = OBSERVAR + PENDENTE (padrão), observe = OBSERVAR, "
                        "pending = PENDENTE, resolved = RESOLVIDO, all = todas")
    p.add_argument("--search", dest="busca", metavar="TEXTO", help="filtra por título, artista, álbum ou ID do vídeo")
    p.add_argument("-d", "--details", dest="detalhes", action="store_true", help="mostra álbum, ISRC, arquivo e link")
    p.add_argument("--ok", nargs="+", metavar="VIDEO_ID",
                   help="marca as faixas como conferidas (RESOLVIDO), mantendo título/artista atuais")
    p.add_argument("--ok-all", action="store_true", help="confirma todas as faixas OBSERVAR de uma vez")
    p.add_argument("--export", metavar="CSV", help="salva a lista filtrada num CSV para abrir no Excel")
    p.add_argument("--sql", metavar="SELECT", help="executa uma consulta SQL somente leitura")
    r = p.add_argument_group("responder (corrigir uma por uma)")
    r.add_argument("-a", "--answer", dest="responder", action="store_true",
                   help="percorre as faixas para conferir, mostra o que está errado e pergunta cada campo; "
                        "Enter mantém o valor. Só muda o que você digitar; quem ficar todo em branco "
                        "continua na lista")
    r.add_argument("--default-ok", dest="default_ok", action="store_true",
                   help="com --answer: o que ficar em branco é aceito; se YouTube e banco discordarem, "
                        f"usa o do banco (MusicBrainz); se estiver vazio, vira '{DESCONHECIDO}'. A faixa sai da lista")
    r.add_argument("--all-fields", dest="todos_campos", action="store_true",
                   help="com --answer: pergunta todos os campos, não só os que têm problema")
    r.add_argument("-s", "--songs-dir", dest="songs_dir", default="songs", metavar="PASTA",
                   help="pasta das músicas (padrão: ./songs)")
    r.add_argument("-p", "--playlist-dir", dest="playlist_dir", default="playlists", metavar="PASTA",
                   help="pasta das M3Us, regravadas após cada correção (padrão: ./playlists)")
    opcoes_bancos(p)
    opcoes_verbose(p)
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
    if args.status in ("open", "observe", "pending"):
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
            (resultado.setdefault("provenance", {}))[campo] = "manual"
            (resultado.setdefault("scores", {}))[campo] = 1.0
        resultado["observations"] = []
        pltube.aprender_canal(conexao, linha["video_key"], resultado)
        pltube.salvar_resolucao(conexao, linha["video_key"], resultado, salvo.get("audio_path") or "")
        caminho = salvo.get("audio_path") or ""
        if caminho and os.path.isfile(caminho):
            pltube.aplicar_tags_hibridas(caminho, resultado)
            pltube.limpar_atalhos_conferencia(caminho)
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


# ---------------------------------------------------------------------------
# --answer: responder uma por uma
# ---------------------------------------------------------------------------

CAMPOS = (("title", "Título"), ("artists", "Artistas"), ("album", "Álbum"), ("isrc", "ISRC"))
AJUDA_RESPOSTA = (
    "Só os campos com problema (⚠) são perguntados (--all-fields pergunta todos).\n"
    "Responda: Enter = mantém | texto = novo valor | :db = valor do banco (MusicBrainz) | "
    ":yt = valor do YouTube | :- = apagar (álbum/ISRC)\n"
    "Na 1ª pergunta de cada faixa: :ok = está tudo certo | :s = pular | :q = sair. "
    "Vários artistas: separe com |  (o primeiro é o principal)."
)


def _mostrar(valor) -> str:
    if isinstance(valor, list):
        return " | ".join(str(v) for v in valor if str(v).strip()) or "—"
    return str(valor or "").strip() or "—"


def _iguais(campo: str, a, b) -> bool:
    import resolvedor_metadados as r
    if campo == "artists":
        return (not a and not b) or bool(a and b and r._mesmos_artistas(list(a), list(b)))
    return r._normalize_text(str(a or "")) == r._normalize_text(str(b or ""))


def _fontes(resultado: dict) -> tuple[dict, dict]:
    """Valores do YouTube e do banco (MusicBrainz) para oferecer como opção."""
    yt = resultado.get("source_youtube") or {}
    youtube = {
        "title": yt.get("title") or yt.get("video_title") or "",
        "artists": list(yt.get("artists") or []),
        "album": yt.get("album") or "",
        "isrc": yt.get("isrc") or "",
    }
    candidato = (resultado.get("candidates") or [{}])[0] or {}
    meta = resultado.get("metadata") or {}
    prov = resultado.get("provenance") or {}
    banco = {
        "title": candidato.get("title") or "",
        "artists": list(candidato.get("artists") or []),
        "album": (meta.get("album") if prov.get("album") == "musicbrainz" else "")
                 or next((rel.get("title") for rel in candidato.get("releases") or [] if rel.get("title")), ""),
        "isrc": next(iter(candidato.get("isrcs") or []), ""),
    }
    return youtube, banco


# Cada motivo de conferência aponta para o(s) campo(s) que precisam de atenção.
_MOTIVO_CAMPOS = (
    ("título diverge", ("title",)),
    ("artista diverge", ("artists",)),
    ("álbum", ("album",)),
    ("isrc", ("isrc",)),
    ("deduzidos", ("title", "artists")),
    ("nenhum artista", ("artists",)),
    ("artista não confirmado", ("artists",)),
    ("ausente: title", ("title",)),
    ("capa", ("cover",)),
)


def campos_com_problema(resultado: dict) -> list[str]:
    """Quais campos os motivos de conferência apontam (na ordem título, artistas, álbum, ISRC, capa)."""
    motivos = " | ".join((resultado.get("pending_reasons") or []) + (resultado.get("observations") or []))
    motivos = motivos.casefold()
    achados = {campo for chave, campos in _MOTIVO_CAMPOS if chave in motivos for campo in campos}
    return [c for c in ("title", "artists", "album", "isrc", "cover") if c in achados]


def _ler(pergunta: str) -> str:
    return input(pergunta).strip()


def _converter(campo: str, texto: str):
    if campo == "artists":
        return [a.strip() for a in texto.split("|") if a.strip()]
    return texto


def responder(conexao: sqlite3.Connection, args: argparse.Namespace) -> int:
    import aplicar_metadados

    pasta_musicas = os.path.abspath(args.songs_dir)
    pasta_playlists = os.path.abspath(args.playlist_dir)
    pltube.SONGS_DIR, pltube.PLAYLISTS_DIR = pasta_musicas, pasta_playlists
    pltube.LOG_FILE = os.path.join(os.getcwd(), "Logs.txt")
    os.makedirs(pasta_playlists, exist_ok=True)

    linhas = _linhas(conexao, STATUS_FILTRO[args.status], args.busca or "")
    if not linhas:
        print("Nenhuma faixa para conferir.")
        return 0
    print(AJUDA_RESPOSTA)
    if args.default_ok:
        print(f"--default-ok: em branco = aceito; conflito YouTube x banco = banco; vazio = {DESCONHECIDO}.")
    corrigidas = mantidas = erros = 0
    for posicao, linha in enumerate(linhas, 1):
        salvo = pltube.carregar_resolucao(conexao, linha["video_key"])
        if not salvo:
            continue
        resultado = salvo["result"]
        atual = dict(resultado.get("metadata") or {})
        youtube, banco = _fontes(resultado)
        print(f"\n[{posicao}/{len(linhas)}] {linha['metadata_status']}  https://youtu.be/{linha['video_id']}")
        if salvo.get("audio_path"):
            print(f"  arquivo: {os.path.relpath(salvo['audio_path'])}")
        motivos = (resultado.get("pending_reasons") or []) + (resultado.get("observations") or [])
        print("  O que está errado:")
        for motivo in motivos or ["(sem motivo registrado)"]:
            print(f"    • {motivo}")

        problemas = campos_com_problema(resultado)
        nomes = dict(CAMPOS, cover="Capa")
        if problemas:
            print("  Campos com problema: " + ", ".join(nomes[c] for c in problemas))
        if "cover" in problemas and len(problemas) == 1 or problemas == ["album", "cover"]:
            print("  (a capa vem junto com o álbum: com o álbum certo, o `update` busca a capa)")
        if args.todos_campos:
            campos = [c for c in CAMPOS if c[0] != "isrc" or "isrc" in problemas]
        else:
            campos = [c for c in CAMPOS if c[0] in problemas] or [CAMPOS[0]]
            for campo, rotulo in CAMPOS:
                if campo not in problemas and campo != "isrc" and (campo, rotulo) not in campos:
                    print(f"  {rotulo}: {_mostrar(atual.get(campo))}  ✓")
        novos: dict = {}
        confirmar_tudo = pular = sair = False
        for campo, rotulo in campos:
            valor_atual = atual.get(campo) or ([] if campo == "artists" else "")
            opcoes = []
            if youtube[campo] and not _iguais(campo, youtube[campo], valor_atual):
                opcoes.append(f"YouTube: {_mostrar(youtube[campo])}")
            if banco[campo] and not _iguais(campo, banco[campo], valor_atual):
                opcoes.append(f"banco: {_mostrar(banco[campo])}")
            extra = f"  ({'; '.join(opcoes)})" if opcoes else ""
            marca = "⚠ " if campo in problemas else ""
            try:
                resposta = _ler(f"  {marca}{rotulo} [{_mostrar(valor_atual)}]{extra}: ")
            except (EOFError, KeyboardInterrupt):
                print()
                sair = True
                break
            comando = resposta.casefold()
            if (campo, rotulo) == campos[0] and comando in (":ok", ":s", ":q", ":pular", ":sair"):
                confirmar_tudo, pular, sair = comando == ":ok", comando in (":s", ":pular"), comando in (":q", ":sair")
                break
            if not resposta:
                continue
            if comando == ":db":
                if not banco[campo]:
                    print("    (o banco não tem esse valor; mantido)")
                    continue
                novos[campo] = banco[campo]
            elif comando == ":yt":
                if not youtube[campo]:
                    print("    (o YouTube não tem esse valor; mantido)")
                    continue
                novos[campo] = youtube[campo]
            elif comando == ":-":
                if campo not in ("album", "isrc"):
                    print("    (só álbum e ISRC podem ser apagados; mantido)")
                    continue
                novos[campo] = aplicar_metadados.LIMPAR
            else:
                novos[campo] = _converter(campo, resposta)
        if sair:
            print("Saindo. O que já foi respondido está salvo.")
            break
        if pular or (not novos and not confirmar_tudo and not args.default_ok):
            mantidas += 1
            print("  → mantida na lista")
            continue

        if args.default_ok:
            # O que ficou em branco: conflito ou vazio com valor no banco → banco; vazio sem banco → Unknown.
            for campo, _ in CAMPOS:
                if campo in novos or campo == "isrc" or (campo not in problemas and not args.todos_campos):
                    continue
                valor_atual = atual.get(campo)
                conflito = youtube[campo] and not _iguais(campo, youtube[campo], banco[campo])
                if banco[campo] and (not valor_atual or conflito) and not _iguais(campo, banco[campo], valor_atual):
                    novos[campo] = banco[campo]  # o banco tem: usa o do banco
                elif not valor_atual:
                    novos[campo] = [DESCONHECIDO] if campo == "artists" else DESCONHECIDO

        registro = {"video_key": linha["video_key"], "arquivo_audio": "", "aplicar": "sim", "confirmar_manual": "sim"}
        for campo, coluna_uso, coluna_valor in (("title", "usar_titulo", "titulo_final"),
                                                ("artists", "usar_artistas", "artistas_finais"),
                                                ("album", "usar_album", "album_final"),
                                                ("isrc", "usar_isrc", "isrc_final")):
            if campo in novos:
                registro[coluna_uso] = "sim"
                registro[coluna_valor] = " | ".join(novos[campo]) if campo == "artists" else novos[campo]
        try:
            plano = aplicar_metadados._montar_plano(registro, conexao, pasta_musicas)
            aplicar_metadados._aplicar_plano(conexao, plano, pasta_playlists)
        except Exception as exc:
            erros += 1
            print(f"  ✗ não foi possível gravar: {exc}")
            continue
        corrigidas += 1
        meta = plano["result"]["metadata"]
        mudou = ", ".join(rotulo for campo, rotulo in CAMPOS if campo in novos) or "nada alterado"
        print(f"  ✓ conferida ({mudou}): {_mostrar(meta.get('artists'))} — {meta.get('title')}"
              f"{' | ' + meta['album'] if meta.get('album') else ''}")

    print(f"\nResumo: {corrigidas} conferida(s)/corrigida(s), {mantidas} mantida(s) na lista"
          + (f", {erros} com erro" if erros else "") + ".")
    print("Tags, banco e M3Us já foram atualizados; o update não desfaz correções manuais.")
    return 1 if erros else 0


def executar(args: argparse.Namespace) -> int:
    ativar_verbose(args)
    try:
        conexao = _abrir(args)
    except (FileNotFoundError, sqlite3.Error, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    try:
        if args.sql:
            return consulta_sql(conexao, args.sql)
        if args.default_ok and not args.responder:
            print("--default-ok só vale junto com --answer.", file=sys.stderr)
            return 2
        if args.responder:
            return responder(conexao, args)
        if args.ok or args.ok_all:
            return confirmar(conexao, args.ok or [], args.ok_all)
        if args.export:
            return exportar(conexao, args)
        return listar(conexao, args)
    finally:
        conexao.close()
