"""Comando `review`: consulta e confirma as faixas do banco SQL no terminal.

Não precisa de nenhum programa de SQL instalado. Lê ./sql/ da pasta atual.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys

import pltube
from comandos._comum import ativar_verbose, opcoes_verbose, novo_parser, opcoes_bancos, traduzir_ajuda

NOME = "review"
APELIDOS = ("revisar",)
RESUMO = "mostra e confirma as faixas do banco SQL direto no terminal"
OPCOES = ("-a/--answer  -y/--default-ok  -A/--all-fields  -x/--skip CAMPO  --status  -q/--search\n"
          "-d/--details  --ok ID...  --ok-all  -e/--export  --sql")
EXEMPLOS = """\
  plpython.py review                      # faixas para conferir (OBSERVAR/PENDENTE)
  plpython.py review --answer             # responde uma por uma; só sai da lista o que ficar resolvido
  plpython.py review --answer --skip album   # não pergunta o álbum
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
    p.add_argument("-q", "--search", dest="busca", metavar="TEXTO", help="filtra por título, artista, álbum ou ID do vídeo")
    p.add_argument("-d", "--details", dest="detalhes", action="store_true", help="mostra álbum, ISRC, arquivo e link")
    p.add_argument("--ok", nargs="+", metavar="VIDEO_ID",
                   help="marca as faixas como conferidas (RESOLVIDO), mantendo título/artista atuais")
    p.add_argument("--ok-all", action="store_true", help="confirma todas as faixas OBSERVAR de uma vez")
    p.add_argument("-e", "--export", metavar="CSV", help="salva a lista filtrada num CSV para abrir no Excel")
    p.add_argument("--sql", metavar="SELECT", help="executa uma consulta SQL somente leitura")
    r = p.add_argument_group("responder (corrigir uma por uma)")
    r.add_argument("-a", "--answer", dest="responder", action="store_true",
                   help="percorre as faixas para conferir, mostra o que está errado e pergunta cada campo "
                        "com problema. Só grava o que você alterar ou marcar com :ok; a faixa só sai da "
                        "lista quando todos os campos com problema estiverem resolvidos")
    r.add_argument("-y", "--default-ok", dest="default_ok", action="store_true",
                   help="com --answer: o que ficar em branco é aceito; se YouTube e banco discordarem, "
                        f"usa o do banco (MusicBrainz); se estiver vazio, vira '{DESCONHECIDO}'")
    r.add_argument("-A", "--all-fields", dest="todos_campos", action="store_true",
                   help="com --answer: pergunta todos os campos, não só os que têm problema")
    r.add_argument("-x", "--skip", dest="ignorar", nargs="+", action="extend", default=[], metavar="CAMPO",
                   help="com --answer: não pergunta nem altera estes campos (title, artists, album, isrc; "
                        "ex.: --skip album). Eles continuam pendentes, e as faixas que só têm problema "
                        "neles nem aparecem"),
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
        print(f"{_status(linha['metadata_status'])} {linha['video_id']}  "
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
NOMES = dict(CAMPOS, cover="Capa")
PERGUNTAVEIS = ("title", "artists", "album", "isrc")  # a capa vem com o álbum; não se digita
APELIDOS_CAMPOS = {
    "title": "title", "titulo": "title", "título": "title",
    "artist": "artists", "artists": "artists", "artista": "artists", "artistas": "artists",
    "album": "album", "álbum": "album", "isrc": "isrc", "cover": "cover", "capa": "cover",
}
COR_STATUS = {"RESOLVIDO": "1;32", "OBSERVAR": "1;33", "PENDENTE": "1;31"}


def _cor(texto: str, codigo: str) -> str:
    from verbose import colorir

    return colorir(texto, codigo)


def _status(status: str) -> str:
    return _cor(f"[{status}]", COR_STATUS.get(status, "1"))


def campos_ignorados(valores: list[str] | None) -> set[str]:
    """--skip album,isrc  ou  --skip album isrc  →  {"album", "cover", "isrc"}."""
    campos: set[str] = set()
    for valor in valores or []:
        for parte in str(valor).split(","):
            if not parte.strip():
                continue
            campo = APELIDOS_CAMPOS.get(parte.strip().casefold())
            if not campo:
                raise ValueError(f"campo desconhecido em --skip: {parte.strip()} (use title, artists, album ou isrc)")
            campos.add(campo)
    if "album" in campos:
        campos.add("cover")  # sem álbum não há capa para conferir
    return campos


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
    ("invertid", ("title", "artists")),
)
_REVISAO_PARCIAL = "revisão parcial"


def _campos_do_motivo(motivo: str) -> set[str]:
    texto = motivo.casefold()
    return {campo for chave, campos in _MOTIVO_CAMPOS if chave in texto for campo in campos}


def _confirmados(resultado: dict) -> set[str]:
    """Campos que você já corrigiu ou marcou como certos (em qualquer revisão)."""
    campos = {c for c, valor in (resultado.get("confidence") or {}).items() if valor == "confirmada_manual"}
    if "album" in campos:
        campos.add("cover")
    return campos


def campos_com_problema(resultado: dict) -> list[str]:
    """Campos apontados pelos motivos de conferência e ainda não conferidos à mão
    (na ordem título, artistas, álbum, ISRC, capa)."""
    motivos = (resultado.get("pending_reasons") or []) + (resultado.get("observations") or [])
    achados = {campo for motivo in motivos for campo in _campos_do_motivo(motivo)} - _confirmados(resultado)
    return [c for c in ("title", "artists", "album", "isrc", "cover") if c in achados]


def _podar_motivos(resultado: dict) -> None:
    """Revisão parcial: tira os motivos cujos campos já foram resolvidos; ficam só os que faltam."""
    confirmados = _confirmados(resultado)
    for chave in ("pending_reasons", "observations"):
        restantes = []
        for motivo in resultado.get(chave) or []:
            campos = _campos_do_motivo(motivo)
            if _REVISAO_PARCIAL in motivo.casefold() or (campos and campos <= confirmados):
                continue
            restantes.append(motivo)
        resultado[chave] = restantes


def _ler(pergunta: str) -> str:
    return input(pergunta).strip()


def _converter(campo: str, texto: str, conhecidos: list[str] | tuple = ()):
    if campo == "artists":
        import resolvedor_metadados as r

        return r.dividir_artistas(texto, conhecidos) or [p.strip() for p in texto.split("|") if p.strip()]
    return texto


_SEPARADOR_DIGITADO = re.compile(r"[,&+/]|\s(?:x|×|vs\.?|e|and|feat\.?|ft\.?|part\.?|with)\s", re.I)


def _pedir_artistas(resposta: str, conhecidos: list[str]) -> list[str]:
    """Mostra como os artistas digitados foram entendidos quando há vírgula, "&" etc.
    e deixa corrigir; com "|" a divisão é a que você escreveu."""
    nomes = _converter("artists", resposta, conhecidos)
    while _SEPARADOR_DIGITADO.search(resposta) and not re.search(r"[|;]", resposta):
        quantos = "1 artista" if len(nomes) == 1 else f"{len(nomes)} artistas"
        print(_cor(f"    → {quantos}: {' | '.join(nomes)}", "36"))
        nova = _ler('    Enter = isso · ou digite de novo (| separa, "aspas" mantêm junto): ')
        if not nova:
            break
        resposta, nomes = nova, _converter("artists", nova, conhecidos)
    return nomes


def _limpar_tela() -> None:
    """Num terminal, cada faixa começa numa tela limpa (com a legenda no topo)."""
    if not getattr(sys.stdout, "isatty", lambda: False)() or os.environ.get("TERM") == "dumb":
        print()
        return
    import verbose

    if os.name == "nt" and not verbose._habilitar_ansi_windows():
        os.system("cls")
    else:
        print("\033[H\033[2J", end="", flush=True)


def _legenda(args: argparse.Namespace, ignorar: set[str]) -> list[str]:
    def k(comando: str) -> str:
        return _cor(comando, "1;36")

    enter = ("Enter = aceita (conflito: valor do banco; vazio: Unknown)" if args.default_ok
             else "Enter = não mexe (o campo continua pendente)")
    aspas = k('"aspas"')
    linhas = [
        f"{enter}   texto = novo valor   {k(':ok')} = este campo está certo",
        f"{k(':db')} = valor do banco   {k(':yt')} = valor do YouTube   {k(':-')} = apagar (álbum/ISRC)"
        f"   {k(':inv')} = troca título e artista",
        f"{k(':tudo')} = a faixa toda está certa   {k(':s')} = pular a faixa   {k(':q')} = salvar e sair",
        f"Vários artistas: A {k('|')} B (vírgula e & também valem; {aspas} mantêm um nome junto)",
        "A faixa só sai da lista quando todos os campos com " + _cor("⚠", "1;33")
        + " forem corrigidos ou marcados com :ok.",
    ]
    if ignorar - {"cover"}:
        nomes = ", ".join(NOMES[c] for c in PERGUNTAVEIS if c in ignorar)
        linhas.append(_cor(f"Ignorando nesta revisão (--skip): {nomes}. Esses campos não são perguntados "
                           "nem alterados e continuam pendentes.", "2"))
    return linhas


def _tela(*, posicao: int, total: int, legenda: list[str], anterior: str, linha, salvo: dict, atual: dict,
          youtube: dict, banco: dict, problemas: list[str], ignorados: list[str]) -> None:
    _limpar_tela()
    titulo = f"━━ Revisão · faixa {posicao} de {total} "
    print(_cor(titulo + "━" * max(3, 72 - len(titulo)), "1"))
    for texto in legenda:
        print(texto)
    print(_cor("─" * 72, "2"))
    if anterior:
        print(anterior)
    caminho = salvo.get("audio_path") or ""
    if caminho:
        try:
            caminho = os.path.relpath(caminho)
        except ValueError:  # outro disco no Windows
            pass
    print(f"\n{_status(linha['metadata_status'])}  https://youtu.be/{linha['video_id']}"
          + (f"   {caminho}" if caminho else ""))
    resultado = salvo["result"]
    motivos = (resultado.get("pending_reasons") or []) + (resultado.get("observations") or [])
    print("O que está errado:")
    for motivo in motivos or ["(sem motivo registrado)"]:
        print(f"  {_cor('•', '33')} {motivo}")
    print()
    for campo, rotulo in CAMPOS:
        valor = atual.get(campo)
        if campo == "isrc" and campo not in problemas and not valor:
            continue
        texto = _mostrar(valor)
        if campo in ignorados:
            marca, texto = _cor("–", "2"), texto + _cor("  (ignorado)", "2")
        elif campo in problemas:
            marca, texto = _cor("⚠", "1;33"), _cor(texto, "1")
        else:
            marca = _cor("✓", "32")
        fontes = []
        if youtube[campo] and not _iguais(campo, youtube[campo], valor):
            fontes.append(_cor(f"YouTube: {_mostrar(youtube[campo])}", "36"))
        if banco[campo] and not _iguais(campo, banco[campo], valor):
            fontes.append(_cor(f"banco: {_mostrar(banco[campo])}", "35"))
        print(f"  {rotulo:<9} {marca} {texto}" + ("    " + "   ".join(fontes) if fontes else ""))
    if "cover" in problemas and "cover" not in ignorados:
        print(_cor("  Capa      vem com o álbum: com o álbum certo, o update busca a capa", "2"))
    print()


def _perguntar(campos: list[str], atual: dict, youtube: dict, banco: dict,
               problemas: list[str], conhecidos: list[str] = ()) -> tuple[dict, set[str], str]:
    """Pergunta campo a campo. Devolve (novos valores, campos marcados como certos, ação)."""
    import aplicar_metadados

    novos: dict = {}
    certos: set[str] = set()
    for campo in campos:
        if campo in novos:  # já definido pelo :inv
            continue
        rotulo = NOMES[campo]
        valor_atual = atual.get(campo) or ([] if campo == "artists" else "")
        marca = _cor("⚠", "1;33") if campo in problemas else " "
        try:
            resposta = _ler(f"{marca} {_cor(rotulo, '1')} [{_mostrar(valor_atual)}]: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return novos, certos, "abortar"
        comando = resposta.casefold()
        if comando in (":q", ":sair"):
            return novos, certos, "sair"
        if comando in (":s", ":pular"):
            return novos, certos, "pular"
        if comando in (":tudo", ":all"):
            certos.update(c for c in campos if c not in novos)
            return novos, certos, "seguir"
        if not resposta:
            continue
        if comando in (":inv", ":troca"):
            titulo = str(novos.get("title") or atual.get("title") or "").strip()
            artistas = novos.get("artists") or atual.get("artists") or []
            if not titulo or not artistas:
                print(_cor("    (precisa de título e artista para trocar; o campo continua pendente)", "2"))
                continue
            novos["title"] = ", ".join(artistas)
            novos["artists"] = _converter("artists", titulo, conhecidos)
            certos.discard("title")
            certos.discard("artists")
            print(_cor(f"    ↔ título: {novos['title']} · artistas: {_mostrar(novos['artists'])}", "36"))
            continue
        if comando == ":ok":
            certos.add(campo)
        elif comando in (":db", ":yt"):
            fonte = banco if comando == ":db" else youtube
            if fonte[campo]:
                novos[campo] = fonte[campo]
            else:
                print(_cor(f"    ({'o banco' if comando == ':db' else 'o YouTube'} não tem esse valor; "
                           "o campo continua pendente)", "2"))
        elif comando == ":-":
            if campo in ("album", "isrc"):
                novos[campo] = aplicar_metadados.LIMPAR
            else:
                print(_cor("    (só álbum e ISRC podem ser apagados; o campo continua pendente)", "2"))
        elif resposta.startswith(":"):
            print(_cor(f"    (comando desconhecido: {resposta}; o campo continua pendente)", "2"))
        elif campo == "artists":
            try:
                novos[campo] = _pedir_artistas(resposta, list(conhecidos))
            except (EOFError, KeyboardInterrupt):
                print()
                return novos, certos, "abortar"
        else:
            novos[campo] = resposta
    return novos, certos, "seguir"


def _aceitar_em_branco(campos: list[str], novos: dict, certos: set[str], atual: dict,
                       youtube: dict, banco: dict) -> None:
    """--default-ok: o que ficou em branco é aceito. Conflito, ou vazio com valor no banco → banco;
    vazio sem banco → Unknown; o resto fica como está, marcado como certo."""
    for campo in campos:
        if campo in novos or campo in certos:
            continue
        valor_atual = atual.get(campo)
        conflito = youtube[campo] and not _iguais(campo, youtube[campo], banco[campo])
        if campo != "isrc" and banco[campo] and (not valor_atual or conflito) \
                and not _iguais(campo, banco[campo], valor_atual):
            novos[campo] = banco[campo]
        elif campo != "isrc" and not valor_atual:
            novos[campo] = [DESCONHECIDO] if campo == "artists" else DESCONHECIDO
        else:
            certos.add(campo)


def _registro(video_key: str, atual: dict, novos: dict, certos: set[str], confirmar: bool) -> dict:
    """Linha no formato da planilha de revisão, só com os campos tratados."""
    import aplicar_metadados

    registro = {"video_key": video_key, "arquivo_audio": "", "aplicar": "sim",
                "confirmar_manual": "sim" if confirmar else ""}
    colunas = {"title": ("usar_titulo", "titulo_final"), "artists": ("usar_artistas", "artistas_finais"),
               "album": ("usar_album", "album_final"), "isrc": ("usar_isrc", "isrc_final")}
    for campo in PERGUNTAVEIS:
        if campo not in novos and campo not in certos:
            continue
        valor = novos[campo] if campo in novos else atual.get(campo)
        texto = " | ".join(valor or []) if campo == "artists" else str(valor or "")
        if not texto.strip():
            if campo in ("title", "artists"):
                continue  # vazio não confirma título/artista
            texto = aplicar_metadados.LIMPAR  # "sem álbum/ISRC" também é uma resposta
        uso, coluna = colunas[campo]
        registro[uso], registro[coluna] = "sim", texto
    return registro


def responder(conexao: sqlite3.Connection, args: argparse.Namespace) -> int:
    import aplicar_metadados

    try:
        ignorar = campos_ignorados(getattr(args, "ignorar", None))
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    pasta_musicas = os.path.abspath(args.songs_dir)
    pasta_playlists = os.path.abspath(args.playlist_dir)
    pltube.SONGS_DIR, pltube.PLAYLISTS_DIR = pasta_musicas, pasta_playlists
    pltube.LOG_FILE = os.path.join(os.getcwd(), "Logs.txt")
    os.makedirs(pasta_playlists, exist_ok=True)

    linhas = _linhas(conexao, STATUS_FILTRO[args.status], args.busca or "")
    if not linhas:
        print("Nenhuma faixa para conferir.")
        return 0
    legenda = _legenda(args, ignorar)
    no_terminal = getattr(sys.stdout, "isatty", lambda: False)()
    conhecidos = pltube.carregar_artistas_conhecidos(conexao)  # para entender "A & B" digitado
    resolvidas = parciais = sem_resposta = puladas_skip = erros = 0
    anterior, acao = "", ""
    for posicao, linha in enumerate(linhas, 1):
        salvo = pltube.carregar_resolucao(conexao, linha["video_key"])
        if not salvo:
            continue
        resultado = salvo["result"]
        atual = dict(resultado.get("metadata") or {})
        youtube, banco = _fontes(resultado)
        problemas = campos_com_problema(resultado)
        ignorados = [c for c in problemas if c in ignorar]
        pendentes = [c for c in problemas if c in PERGUNTAVEIS and c not in ignorar]
        if ignorados and not pendentes and not args.todos_campos:
            puladas_skip += 1  # só tem problema nos campos ignorados
            continue
        if args.todos_campos:
            perguntar = [c for c in PERGUNTAVEIS if c not in ignorar and (c != "isrc" or "isrc" in problemas)]
        else:
            perguntar = pendentes or ["title"]  # sem campo apontado: o título serve de confirmação

        _tela(posicao=posicao, total=len(linhas), legenda=legenda, anterior=anterior if no_terminal else "",
              linha=linha, salvo=salvo, atual=atual, youtube=youtube, banco=banco,
              problemas=problemas, ignorados=ignorados)
        da_faixa = list(youtube["artists"]) + list(banco["artists"]) + list(atual.get("artists") or [])
        novos, certos, acao = _perguntar(perguntar, atual, youtube, banco, problemas, conhecidos + da_faixa)
        if acao == "abortar":
            print("Saindo; esta faixa não foi alterada. As anteriores já estão salvas.")
            break
        if acao == "seguir" and args.default_ok:
            _aceitar_em_branco(perguntar, novos, certos, atual, youtube, banco)

        rotulo = f"{_mostrar(novos.get('artists') or atual.get('artists'))} — {novos.get('title') or atual.get('title') or '?'}"
        tratados = set(novos) | certos
        faltam = [c for c in pendentes if c not in tratados] + ignorados
        if not tratados:
            sem_resposta += 1
            anterior = _cor(f"· {rotulo}: sem resposta; continua na lista", "2")
            print(anterior)
            if acao == "sair":
                break
            continue

        confirmar = not faltam
        titulo_final = novos.get("title") or atual.get("title")
        artistas_finais = novos.get("artists") or atual.get("artists")
        if confirmar and not (str(titulo_final or "").strip() and artistas_finais):
            confirmar = False  # sem título/artista não dá para tirar da lista
        try:
            plano = aplicar_metadados._montar_plano(
                _registro(linha["video_key"], atual, novos, certos, confirmar), conexao, pasta_musicas)
            if not confirmar:
                _podar_motivos(plano["result"])
            aplicar_metadados._aplicar_plano(conexao, plano, pasta_playlists)
        except Exception as exc:  # noqa: BLE001 - mostra e segue para a próxima
            erros += 1
            anterior = _cor(f"✗ {rotulo}: não foi possível gravar: {exc}", "1;31")
            print(anterior)
            if acao == "sair":
                break
            continue

        meta = plano["result"]["metadata"]
        rotulo = f"{_mostrar(meta.get('artists'))} — {meta.get('title')}"
        mudou = ", ".join(NOMES[c] for c in PERGUNTAVEIS if c in novos)
        certo = ", ".join(NOMES[c] for c in PERGUNTAVEIS if c in certos and c not in novos)
        detalhe = "; ".join(filter(None, (f"corrigido: {mudou}" if mudou else "", f"certo: {certo}" if certo else "")))
        detalhe = f" ({detalhe})" if detalhe else ""
        if confirmar:
            resolvidas += 1
            anterior = _cor(f"✓ {rotulo}{detalhe}: saiu da lista", "32")
        else:
            parciais += 1
            falta = ", ".join(NOMES[c] + (" (ignorado)" if c in ignorados else "") for c in faltam) or "título/artista"
            anterior = _cor(f"◐ {rotulo}{detalhe}: salvo; continua na lista (falta: {falta})", "33")
        print(anterior)
        if acao == "sair":
            break

    if acao == "sair":
        print("Saindo. O que já foi respondido está salvo.")
    partes = [f"{resolvidas} saíram da lista", f"{parciais} corrigidas em parte (continuam na lista)",
              f"{sem_resposta} sem resposta"]
    if puladas_skip:
        partes.append(f"{puladas_skip} puladas por só terem problema no que foi ignorado (--skip)")
    if erros:
        partes.append(f"{erros} com erro")
    print("\nResumo: " + ", ".join(partes) + ".")
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
        if (args.default_ok or args.ignorar) and not args.responder:
            print("--default-ok e --skip só valem junto com --answer.", file=sys.stderr)
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
