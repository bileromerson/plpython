"""Correções de artista que valem para outras faixas (review --answer e review --apply).

Quando você corrige o artista de uma faixa no `review --answer` ("Canal Qualquer" →
"Linkin Park"), a correção fica guardada no banco. Nas outras faixas da revisão com
o mesmo artista errado, o artista não é mais perguntado: fica para o
`review --apply`, que passa por elas uma por uma e só aplica onde a própria faixa
confirma a correção:

  * são os mesmos nomes, só com outra grafia ou divisão
    ("TheFatRat & NEFFEX" → "TheFatRat | NEFFEX"); ou
  * o novo artista aparece no título do vídeo, nos créditos do YouTube (Music) ou
    nos candidatos do MusicBrainz desta faixa.

Um canal de re-upload publica vários artistas; sem essa confirmação a correção não
é aplicada e, no próximo review, aparece como sugestão (:sug). Se o mesmo artista
errado foi corrigido para nomes diferentes em faixas diferentes, nada é propagado.

Aplicada a correção, a faixa é identificada de novo com o artista certo (MusicBrainz:
título oficial, álbum, capa). Ela só sai da lista se tudo o que é necessário ficar
completo; senão continua OBSERVAR, só com o que falta.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable

import resolvedor_metadados as rm
from verbose import colorir, v


def chave(nomes: Iterable[str]) -> str:
    """"TheFatRat & NEFFEX", "thefatrat|neffex" e "The Fat Rat, NEFFEX" dão a mesma chave."""
    partes = []
    for nome in nomes or []:
        partes += rm._normalize_text(nome).split()
    return " ".join(sorted(partes))


def mesmos_nomes(a: Iterable[str], b: Iterable[str]) -> bool:
    """Mesmo artista: igual ignorando grafia, espaços, pontuação e divisão."""
    a, b = list(a or []), list(b or [])
    if not a or not b:
        return False
    colado = lambda nomes: "".join(rm._normalize_text(n).replace(" ", "") for n in nomes)  # noqa: E731
    if chave(a) == chave(b) or colado(a) == colado(b):
        return True
    return len(a) == len(b) == 1 and rm._similarity(a[0], b[0]) >= 0.92


@dataclass
class Regra:
    de: list[str]
    opcoes: list[list[str]] = field(default_factory=list)  # correções diferentes já feitas
    faixas: list[str] = field(default_factory=list)

    @property
    def para(self) -> list[str]:
        return self.opcoes[-1]

    @property
    def conflito(self) -> bool:
        return len(self.opcoes) > 1


def registrar(conexao: sqlite3.Connection, de: Iterable[str], para: Iterable[str], video_key: str,
              canal: str = "") -> bool:
    """Guarda "de → para" (uma linha por faixa corrigida). Devolve True se guardou."""
    de, para = [str(n) for n in de or [] if str(n).strip()], [str(n) for n in para or [] if str(n).strip()]
    if not de or not para or de == para or any(rm._normalize_text(n) == "unknown" for n in para):
        return False
    conexao.execute(
        "INSERT OR REPLACE INTO music.correcoes_artistas (chave, de_json, para_json, video_key, canal) "
        "VALUES (?, ?, ?, ?, ?)",
        (chave(de), json.dumps(de, ensure_ascii=False), json.dumps(para, ensure_ascii=False), video_key, canal),
    )
    conexao.commit()
    v("SQL", f"correção de artista guardada: {' | '.join(de)} → {' | '.join(para)}")
    return True


def carregar(conexao: sqlite3.Connection) -> list[Regra]:
    try:
        linhas = conexao.execute(
            "SELECT chave, de_json, para_json, video_key FROM music.correcoes_artistas ORDER BY criada_em, rowid"
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    regras: dict[str, Regra] = {}
    for linha in linhas:
        regra = regras.setdefault(linha["chave"], Regra(json.loads(linha["de_json"])))
        para = json.loads(linha["para_json"])
        regra.opcoes = [o for o in regra.opcoes if chave(o) != chave(para)] + [para]
        regra.faixas.append(linha["video_key"])
    return list(regras.values())


def regra_para(regras: Iterable[Regra], artistas: Iterable[str]) -> Regra | None:
    return next((r for r in regras if mesmos_nomes(r.de, artistas)), None)


def verificar(regra: Regra, resultado: dict) -> tuple[bool, str]:
    """A própria faixa confirma a correção? (sim/não, por quê)."""
    if regra.conflito:
        return False, "o mesmo artista já foi corrigido para nomes diferentes: " + \
            " / ".join(" | ".join(o) for o in regra.opcoes)
    if chave(regra.de) == chave(regra.para):
        return True, "são os mesmos nomes, só com outra grafia ou divisão"
    yt = resultado.get("source_youtube") or {}
    titulo = f" {rm._normalize_text(yt.get('video_title') or yt.get('title'))} "
    creditos = list(yt.get("artists") or []) + list((yt.get("youtube_music") or {}).get("artists") or [])
    for candidato in resultado.get("candidates") or []:
        creditos += list(candidato.get("artists") or [])
    principal = regra.para[0]
    nome = rm._normalize_text(principal)
    if nome and f" {nome} " in titulo:
        return True, f"'{principal}' aparece no título do vídeo"
    if any(rm._mesmo_nome(principal, c) for c in creditos):
        return True, f"'{principal}' aparece nos créditos desta faixa"
    return False, f"nada nesta faixa confirma '{principal}' (o canal pode publicar vários artistas)"


# --- review --apply -----------------------------------------------------------------------

def _rotulo(meta: dict) -> str:
    return f"{', '.join(meta.get('artists') or []) or '?'} — {meta.get('title') or '?'}"


def _reidentificar(conexao: sqlite3.Connection, salvo: dict, regra: Regra) -> dict | None:
    """Refaz a identificação com o artista corrigido; None se não der (faixa já corrigida à mão)."""
    import pltube

    resultado = salvo["result"]
    if (resultado.get("manual_review") or {}).get("fields"):
        return None  # já tem correção sua: só troca o artista, sem refazer o resto
    yt = resultado.get("source_youtube") or {}
    titulo_video = yt.get("video_title") or yt.get("title")
    if not titulo_video:
        return None
    meta = {
        "id": resultado.get("video_id") or "", "title": titulo_video, "track": yt.get("track") or "",
        "artists": list(regra.para), "fonte_artistas": "youtube",
        "album": yt.get("album") or "", "isrc": yt.get("isrc") or "", "uploader": yt.get("uploader") or "",
        "duration": yt.get("duration") or 0, "thumbnails": yt.get("thumbnails") or [],
        "channel_id": yt.get("channel_id") or "", "channel_is_verified": bool(yt.get("channel_verified")),
        "release_date": yt.get("release_date") or "", "youtube_music": yt.get("youtube_music") or {},
        "descricao_reupload": bool(yt.get("descricao_reupload")),
    }
    try:
        _, novo = pltube.resolver_meta_hibrido(meta, estado=conexao)
    except Exception as exc:  # noqa: BLE001 - sem MusicBrainz: só troca o artista
        v("MusicBrainz", f"não deu para identificar de novo: {exc}")
        return None
    provenance = novo.setdefault("provenance", {})
    if provenance.get("artists") not in {"musicbrainz", "youtube_validado_musicbrainz"}:
        provenance["artists"] = "manual_regra"
    novo.setdefault("confidence", {})["artists"] = "confirmada_manual"
    novo.setdefault("scores", {})["artists"] = 1.0
    return novo


def _faltando(resultado: dict) -> list[str]:
    from comandos.review import PERGUNTAVEIS, campos_com_problema

    if resultado.get("status") == "RESOLVIDO":
        return []
    return [c for c in campos_com_problema(resultado) if c in PERGUNTAVEIS]


def _plano(conexao: sqlite3.Connection, salvo: dict, linha: sqlite3.Row, regra: Regra,
           pasta_musicas: str) -> dict:
    import aplicar_metadados
    from comandos.review import PERGUNTAVEIS, _podar_motivos, campos_com_problema

    marca = {"via": "review --apply", "regra": {"de": regra.de, "para": regra.para}}
    novo = _reidentificar(conexao, salvo, regra)
    if novo is not None:
        resolvido = novo.get("status") == "RESOLVIDO"
        if not resolvido:
            _podar_motivos(novo)
        novo["manual_review"] = {"fields": ["artists"], "confirmed": resolvido, **marca}
        return {"video_key": linha["video_key"], "video_id": str(novo.get("video_id") or ""),
                "audio_path": aplicar_metadados._validar_audio(salvo.get("audio_path") or "", pasta_musicas),
                "result": novo, "fields": ["artists"], "confirmed": resolvido}
    # Só troca o artista; sai da lista apenas se nada mais estiver faltando.
    restantes = [c for c in campos_com_problema(salvo["result"]) if c in PERGUNTAVEIS and c != "artists"]
    registro = {"video_key": linha["video_key"], "arquivo_audio": "", "aplicar": "sim",
                "confirmar_manual": "" if restantes else "sim",
                "usar_artistas": "sim", "artistas_finais": " | ".join(regra.para)}
    plano = aplicar_metadados._montar_plano(registro, conexao, pasta_musicas)
    if restantes:
        _podar_motivos(plano["result"])
    plano["result"]["manual_review"].update(marca)
    return plano


def aplicar(conexao: sqlite3.Connection, args: Any) -> int:
    import aplicar_metadados
    import pltube
    from comandos.review import STATUS_FILTRO, _linhas

    regras = carregar(conexao)
    if not regras:
        print("Nenhuma correção de artista guardada. Corrija um artista no `review --answer` e rode de novo.")
        return 0
    pasta_musicas, pasta_playlists = os.path.abspath(args.songs_dir), os.path.abspath(args.playlist_dir)
    pltube.SONGS_DIR, pltube.PLAYLISTS_DIR = pasta_musicas, pasta_playlists
    pltube.LOG_FILE = os.path.join(os.getcwd(), "Logs.txt")
    os.makedirs(pasta_playlists, exist_ok=True)
    pltube.ARTISTAS_CONHECIDOS[:] = pltube.carregar_artistas_conhecidos(conexao)
    pltube.PREFERIR = "youtube"  # o artista que você corrigiu vale; divergência do MusicBrainz fica anotada
    if pltube.MUSICBRAINZ_CLIENT is None:
        pltube.MUSICBRAINZ_CLIENT = rm.MusicBrainzClient()

    print(f"{len(regras)} correção(ões) de artista guardada(s). Conferindo as faixas da lista uma por uma…\n")
    resolvidas = parciais = recusadas = erros = 0
    for linha in _linhas(conexao, STATUS_FILTRO["open"], getattr(args, "busca", "") or ""):
        salvo = pltube.carregar_resolucao(conexao, linha["video_key"])
        if not salvo:
            continue
        meta = salvo["result"].get("metadata") or {}
        regra = regra_para(regras, meta.get("artists") or [])
        if regra is None:
            continue
        rotulo = _rotulo(meta)
        correcao = f"{' | '.join(regra.de)} → {' | '.join(regra.para)}"
        ok, motivo = verificar(regra, salvo["result"])
        if not ok:
            recusadas += 1
            print(colorir(f"✗ {rotulo}: não apliquei {correcao}: {motivo}", "33"))
            continue
        try:
            plano = _plano(conexao, salvo, linha, regra, pasta_musicas)
            aplicar_metadados._aplicar_plano(conexao, plano, pasta_playlists)
        except Exception as exc:  # noqa: BLE001 - mostra e segue
            erros += 1
            print(colorir(f"✗ {rotulo}: não foi possível gravar: {exc}", "1;31"))
            continue
        novo = plano["result"]
        if novo.get("status") == "RESOLVIDO":
            resolvidas += 1
            print(colorir(f"✓ {_rotulo(novo.get('metadata') or {})}: {correcao} ({motivo}); saiu da lista", "32"))
        else:
            parciais += 1
            from comandos.review import NOMES

            falta = ", ".join(NOMES[c] for c in _faltando(novo)) or "conferir"
            print(colorir(f"◐ {_rotulo(novo.get('metadata') or {})}: {correcao} ({motivo}); "
                          f"continua na lista (falta: {falta})", "33"))

    print(f"\nResumo: {resolvidas} saíram da lista, {parciais} corrigidas em parte (continuam na lista), "
          f"{recusadas} não confirmadas" + (f", {erros} com erro" if erros else "") + ".")
    if recusadas:
        print("As não confirmadas aparecem no próximo `review --answer` com a correção como sugestão (:sug).")
    return 1 if erros else 0
