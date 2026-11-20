"""Comando `check`: procura no banco as faixas com sinal forte de erro.

Em vez de exigir que cada artista seja confirmado, olha só o que costuma estar
errado de verdade (ver identificar_musica.sinais_de_erro):

- artista com cara de pedaço do título ("The Living Tombstone (FNAF2)", "[Dubstep]");
- artista que é canal de re-upload, selo ou trilha ("Lyrics Hub", "... OST");
- título com sobras do vídeo ("(Lyrics)", "#shorts", "02. ", '"Música" by Artista');
- título e artista trocados (o título é um artista que você já tem);
- vídeo longo demais para ser uma música (compilação, álbum inteiro, loop).

Com -y, as faixas com sinal vão para a conferência (OBSERVAR) e as que estavam lá só
por motivos antigos (álbum ou capa faltando, "não confirmado no MusicBrainz") saem.
Correções suas (review) nunca são questionadas.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

from comandos._comum import ativar_verbose, novo_parser, opcoes_bancos, opcoes_pastas, opcoes_verbose, traduzir_ajuda

NOME = "check"
APELIDOS = ("conferir",)
RESUMO = "procura no banco faixas com sinal forte de erro (artista/título com cara de errado)"
OPCOES = "-y/--apply  -q/--search  -s  -p  -v"
EXEMPLOS = """\
  plpython.py check                 # mostra o que tem sinal de erro e o que sai da conferência
  plpython.py check -y              # grava: sinal de erro → OBSERVAR; sem sinal → RESOLVIDO
  plpython.py check -q snuffles     # só as faixas com "snuffles"
  plpython.py review --answer       # depois, corrige as que ficaram para conferir"""

# De onde veio o artista nas faixas antigas (modelo anterior) → as fontes de agora.
_FONTES_ANTIGAS = {
    "musicbrainz": "youtube_music", "youtube_validado_musicbrainz": "youtube_music",
    "youtube_canal_topic": "youtube_music", "youtube_artista": "youtube_creditos",
    "manual": "manual", "manual_regra": "manual", "banco_isrc": "banco_isrc",
    "cache_local": "local", "local": "local",
}
_CANAL = {"youtube_canal", "youtube_canal_oficial", "canal_aprendido", "youtube_canal_album"}


def criar_parser(prog: str = "plpython.py check") -> argparse.ArgumentParser:
    p = novo_parser(
        prog,
        "Procura no banco as faixas que têm sinal forte de erro no artista ou no título e as "
        "manda para a conferência; as que estavam lá só por álbum/capa faltando saem. "
        "Simula sem -y.",
        EXEMPLOS,
    )
    p.add_argument("-y", "--apply", action="store_true", help="grava; sem isto só mostra o que mudaria")
    p.add_argument("-q", "--search", dest="busca", metavar="TEXTO", help="só faixas com este texto no título/artista")
    opcoes_pastas(p)
    opcoes_bancos(p)
    opcoes_verbose(p)
    return traduzir_ajuda(p)


def fonte_do_resultado(resultado: dict) -> str:
    prov = str((resultado.get("provenance") or {}).get("artists") or "")
    if prov in _FONTES_ANTIGAS:
        return _FONTES_ANTIGAS[prov]
    if prov in _CANAL:
        return "canal"
    import identificar_musica as im

    return prov if prov in im.CONFIANCA else "titulo"


def avaliar(resultado: dict, conhecidos: list[str], canal_conhecido: dict | None = None) -> tuple[str, list[str]]:
    """Novo status e sinais de erro de uma faixa salva (sem consultar a internet)."""
    import identificar_musica as im
    import textos as tx

    revisao = resultado.get("manual_review") or {}
    metadata = resultado.get("metadata") or {}
    if not metadata.get("title") or not metadata.get("artists"):
        return "PENDENTE", list(resultado.get("pending_reasons") or ["nenhum artista identificado"])
    if revisao.get("confirmed"):
        return "RESOLVIDO", []
    yt = resultado.get("source_youtube") or {}
    canal = tx._nome_canal_limpo(str(yt.get("uploader") or metadata.get("uploader") or ""))
    tipo = str(((resultado.get("policy") or {}).get("canal") or {}).get("tipo") or "")
    if tipo not in ("artista", "curador"):
        tipo = im._canal_tipo(yt, canal_conhecido)
    sinais = im.sinais_de_erro(
        metadata, fonte_do_resultado(resultado), titulo_video=str(yt.get("video_title") or ""), canal=canal,
        canal_tipo=tipo, duracao=float(yt.get("duration") or metadata.get("duration") or 0),
        conhecidos=conhecidos, canal_conhecido=canal_conhecido,
    )
    corrigidos = set(revisao.get("fields") or [])  # campo que você corrigiu não é questionado
    if "artists" in corrigidos:
        sinais = [s for s in sinais if not s.startswith("artista") and "invertidos" not in s]
    if "title" in corrigidos:
        sinais = [s for s in sinais if not s.startswith("título")]
    return ("OBSERVAR" if sinais else "RESOLVIDO"), sinais


def _rotulo(metadata: dict) -> str:
    return f"{', '.join(metadata.get('artists') or []) or '?'} — {metadata.get('title') or '?'}"


def executar(args: argparse.Namespace) -> int:
    ativar_verbose(args)
    import pltube
    from comandos.review import _abrir
    from verbose import colorir

    try:
        conexao = _abrir(args)
    except (FileNotFoundError, sqlite3.Error, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    pltube.SONGS_DIR = os.path.abspath(args.songs_dir or "songs")
    pltube.PLAYLISTS_DIR = os.path.abspath(args.playlist_dir or "playlists")
    try:
        import banco_artistas
        from collections import Counter

        # Em quantas faixas confirmadas cada artista aparece (para "título e artista trocados").
        vezes: Counter = Counter()
        for (artistas_json,) in conexao.execute(
                "SELECT artists_json FROM music.music_tracks WHERE metadata_status = 'RESOLVIDO'"):
            vezes.update(json.loads(artistas_json or "[]"))
        for nome in banco_artistas.nomes_conhecidos(conexao):
            vezes[nome] = max(vezes[nome], 1)
        filtro, params = "", []
        if args.busca:
            filtro = " WHERE t.title LIKE ? OR t.artists_json LIKE ? OR t.video_id = ?"
            params = [f"%{args.busca}%", f"%{args.busca}%", args.busca]
        linhas = conexao.execute(
            "SELECT t.video_key FROM music.music_tracks t" + filtro + " ORDER BY t.artists_json, t.title", params
        ).fetchall()
        para_conferir, saem, continuam = [], [], 0
        for linha in linhas:
            salvo = pltube.carregar_resolucao(conexao, linha[0])
            if not salvo:
                continue
            resultado = salvo["result"]
            antes = resultado.get("status") or "PENDENTE"
            meta_canal = pltube.chave_canal({
                "channel_id": (resultado.get("source_youtube") or {}).get("channel_id"),
                "uploader": (resultado.get("source_youtube") or {}).get("uploader"),
            })
            conhecidos = Counter(vezes)
            if antes == "RESOLVIDO":  # a própria faixa não conta
                conhecidos.subtract((resultado.get("metadata") or {}).get("artists") or [])
            status, sinais = avaliar(resultado, +conhecidos, pltube.perfil_canal(conexao, meta_canal))
            if status == antes and sinais == list(resultado.get("observations") or []):
                continuam += antes in ("OBSERVAR", "PENDENTE")
                continue
            if status == "PENDENTE":
                continuam += 1
                continue
            metadata = resultado.get("metadata") or {}
            if status == "OBSERVAR":
                para_conferir.append((linha[0], salvo, status, sinais))
                print(colorir(f"⚠ {_rotulo(metadata)}", "33"))
                for sinal in sinais:
                    print(colorir(f"    • {sinal}", "2"))
            elif status == "RESOLVIDO":
                saem.append((linha[0], salvo, status, []))
                motivo = "; ".join(resultado.get("observations") or []) or "sem sinal de erro"
                print(colorir(f"✓ {_rotulo(metadata)}", "32") + colorir(f"  (estava: {motivo[:90]})", "2"))
        print(f"\n{len(para_conferir)} faixa(s) com sinal de erro vão para a conferência; "
              f"{len(saem)} saem dela (não tinham sinal de erro); {continuam} já estavam certas na lista.")
        if not args.apply:
            if para_conferir or saem:
                print("Nada foi gravado. Repita com -y para gravar.")
            return 0
        for video_key, salvo, status, sinais in para_conferir + saem:
            resultado = salvo["result"]
            resultado["status"] = status
            resultado["observations"] = sinais
            resultado["pending_reasons"] = []
            resultado.setdefault("policy", {})["check"] = "sinais de erro"
            pltube.salvar_resolucao(conexao, video_key, resultado, salvo.get("audio_path") or "")
            caminho = salvo.get("audio_path") or ""
            if caminho and os.path.isfile(caminho):
                pltube.aplicar_tags_hibridas(caminho, resultado)
                if status == "RESOLVIDO":
                    pltube.limpar_atalhos_conferencia(caminho)
                else:
                    pltube.criar_link_artista(caminho, "_observar")
        print(f"Gravado. Para corrigir as que ficaram: plpython.py review --answer")
        return 0
    finally:
        conexao.close()


def main(argv: list[str] | None = None) -> int:
    return executar(criar_parser().parse_args(argv))
