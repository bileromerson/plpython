"""Grava uma correção de metadados numa faixa: tags do MP3, banco, pasta do artista e M3U.

Usado pelo `review --answer`, pelo `review --apply`, pelo `check -y` e pelo `artists fix`.
Uma correção é descrita como um registro com campos "usar_*"/"*_final" (título, artistas,
álbum, ISRC) e "confirmar_manual"; `_montar_plano` monta o resultado novo e
`_aplicar_plano` grava tudo, voltando o MP3 ao original se algo falhar.
Não consulta a internet e não baixa áudio.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from typing import Any

import pltube

SIM = {"sim", "s", "yes", "y", "1", "true", "verdadeiro"}
LIMPAR = "__LIMPAR__"


def _verdadeiro(valor: str | None) -> bool:
    return str(valor or "").strip().casefold() in SIM


def _artistas(valor: str) -> list[str]:
    if valor.strip().casefold() == LIMPAR.casefold():
        raise ValueError("não é permitido limpar todos os artistas")
    # "|" separa sem dúvida; vírgula e "&" também valem, sem quebrar "Jorge & Mateus".
    nomes = pltube.dividir_artistas(valor, pltube.ARTISTAS_CONHECIDOS) or [
        parte.strip() for parte in valor.split("|") if parte.strip()  # "Unknown" digitado de propósito
    ]
    if not nomes:
        raise ValueError("informe pelo menos um artista; separe colaboradores com |")
    return nomes


def _validar_audio(caminho: str, pasta_musicas: str) -> str:
    if not caminho:
        raise ValueError("o banco não tem caminho de áudio para esta faixa")
    caminho_real = os.path.realpath(caminho)
    raiz_real = os.path.realpath(pasta_musicas)
    try:
        dentro_da_biblioteca = os.path.commonpath([raiz_real, caminho_real]) == raiz_real
    except ValueError:
        dentro_da_biblioteca = False
    if not dentro_da_biblioteca:
        raise ValueError("o caminho do áudio está fora da pasta de músicas configurada")
    if not caminho_real.lower().endswith(".mp3") or not os.path.isfile(caminho_real):
        raise ValueError("o MP3 informado não existe ou não é um arquivo regular")
    return caminho_real


def _montar_plano(
    registro: dict[str, str],
    conexao,
    pasta_musicas: str,
) -> dict[str, Any]:
    chave = (registro.get("video_key") or "").strip()
    if not re.fullmatch(r"youtube:[A-Za-z0-9_-]{6,20}|url:[a-f0-9]{64}", chave):
        raise ValueError("video_key ausente ou inválida; gere novamente a fila com --modelo-hibrido")
    salvo = pltube.carregar_resolucao(conexao, chave)
    if not salvo:
        raise ValueError(f"não há resolução no SQLite para {chave}")
    audio_path = _validar_audio(salvo.get("audio_path") or "", pasta_musicas)
    informado = (registro.get("arquivo_audio") or "").strip()
    if informado and os.path.realpath(informado) != audio_path:
        raise ValueError("o caminho no CSV não corresponde ao caminho confiável guardado no SQLite")

    resultado = salvo["result"]
    metadata = resultado.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("o registro não contém metadados válidos no SQLite")
    metadata = dict(metadata)
    resultado = dict(resultado)
    resultado["metadata"] = metadata
    selecionados: list[str] = []

    campos = (
        ("usar_titulo", "titulo_final", "title"),
        ("usar_album", "album_final", "album"),
        ("usar_isrc", "isrc_final", "isrc"),
    )
    for coluna_uso, coluna_valor, campo in campos:
        if not _verdadeiro(registro.get(coluna_uso)):
            continue
        valor = (registro.get(coluna_valor) or "").strip()
        if valor.casefold() == LIMPAR.casefold():
            if campo not in {"album", "isrc"}:
                raise ValueError(f"não é permitido limpar {campo}")
            metadata[campo] = ""
        elif not valor:
            raise ValueError(f"{coluna_valor} está vazio; para apagar use {LIMPAR}")
        else:
            metadata[campo] = valor
        selecionados.append(campo)

    if _verdadeiro(registro.get("usar_artistas")):
        metadata["artists"] = _artistas(registro.get("artistas_finais") or "")
        selecionados.append("artists")

    confirmar = _verdadeiro(registro.get("confirmar_manual"))
    if not selecionados and not confirmar:
        raise ValueError("marque ao menos um campo `usar_*` ou `confirmar_manual=sim`")
    if confirmar:
        titulo = str(metadata.get("title") or "").strip()
        artistas = metadata.get("artists") or []
        if not titulo or not artistas:
            raise ValueError("para confirmar, título e ao menos um artista precisam estar preenchidos")

    provenance = dict(resultado.get("provenance") or {})
    confidence = dict(resultado.get("confidence") or {})
    campos_manualmente_confirmados = list(selecionados)
    if confirmar:
        campos_manualmente_confirmados = ["title", "artists", "album", "isrc"]
    for campo in campos_manualmente_confirmados:
        provenance[campo] = "manual"
        confidence[campo] = "confirmada_manual"
    resultado["provenance"] = provenance
    resultado["confidence"] = confidence
    anteriores = list((resultado.get("manual_review") or {}).get("fields") or [])
    resultado["manual_review"] = {
        # Revisões anteriores continuam valendo: o update não refaz campos corrigidos à mão.
        "fields": list(dict.fromkeys(anteriores + selecionados)),
        "confirmed": confirmar,
    }

    if confirmar:
        resultado["status"] = "RESOLVIDO"
        resultado["pending_reasons"] = []
        resultado["observations"] = []
    else:
        resultado["status"] = "OBSERVAR" if resultado.get("status") == "OBSERVAR" else "PENDENTE"
        razoes = list(resultado.get("pending_reasons") or [])
        razao_parcial = "revisão parcial; confirme manualmente após revisar os campos restantes"
        if razao_parcial not in razoes:
            razoes.append(razao_parcial)
        resultado["pending_reasons"] = razoes

    video_id = str(resultado.get("video_id") or "")
    return {
        "video_key": chave,
        "video_id": video_id,
        "audio_path": audio_path,
        "result": resultado,
        "fields": selecionados,
        "confirmed": confirmar,
    }


def _atualizar_playlist(conexao, video_key: str, playlist_dir: str) -> None:
    csv_paths = conexao.execute(
        "SELECT DISTINCT csv_path FROM playlist_items WHERE video_key = ?",
        (video_key,),
    ).fetchall()
    for item in csv_paths:
        csv_path = item[0]
        nome = os.path.splitext(os.path.basename(csv_path))[0]
        linhas = conexao.execute(
            "SELECT title, artist, duration, path FROM playlist_items "
            "WHERE csv_path = ? ORDER BY row_index",
            (csv_path,),
        ).fetchall()
        entradas = [
            (
                linha["title"], linha["artist"], linha["duration"],
                pltube._resolver_caminho_banco(linha["path"], conexao),
            )
            for linha in linhas
        ]
        pltube.escrever_playlist(
            os.path.join(playlist_dir, f"{nome}.m3u"), entradas, nome, append=False
        )


def _aplicar_plano(conexao, plano: dict[str, Any], playlist_dir: str) -> None:
    caminho = plano["audio_path"]
    pasta = os.path.dirname(caminho)
    fd, backup = tempfile.mkstemp(prefix=".pltube-backup-", suffix=".mp3", dir=pasta)
    os.close(fd)
    backup_pronto = False
    try:
        shutil.copy2(caminho, backup)
        backup_pronto = True
        if not pltube.aplicar_tags_hibridas(caminho, plano["result"]):
            raise RuntimeError("não foi possível gravar as tags do MP3")

        metadata = plano["result"]["metadata"]
        artistas = metadata.get("artists") or []
        conexao.execute(
            """UPDATE music.metadata_resolution
               SET status = ?, result_json = ?, updated_at = CURRENT_TIMESTAMP
               WHERE video_key = ?""",
            (
                plano["result"]["status"],
                json.dumps(plano["result"], ensure_ascii=False),
                plano["video_key"],
            ),
        )
        conexao.execute(
            """UPDATE video_cache SET title = ?, artists_json = ?, status = 'complete',
               error = '' WHERE video_key = ?""",
            (
                str(metadata.get("title") or ""),
                json.dumps(artistas, ensure_ascii=False),
                plano["video_key"],
            ),
        )
        conexao.execute(
            "UPDATE playlist_items SET title = ?, artist = ? WHERE video_key = ?",
            (str(metadata.get("title") or ""), " | ".join(artistas), plano["video_key"]),
        )
        pltube._salvar_metadados_musica(
            conexao, plano["video_key"], plano["result"]
        )
        # Correção manual também ensina se o canal é do artista ou um re-upload.
        pltube.aprender_canal(conexao, plano["video_key"], plano["result"])
        pltube.aprender_artistas(conexao, plano["result"], "review")
        conexao.commit()
    except Exception:
        conexao.rollback()
        if backup_pronto:
            shutil.copy2(backup, caminho)
        raise
    finally:
        if os.path.exists(backup):
            os.unlink(backup)

    _reorganizar_arquivo(conexao, plano)
    _atualizar_playlist(conexao, plano["video_key"], playlist_dir)


def _reorganizar_arquivo(conexao, plano: dict[str, Any]) -> None:
    """Depois de gravar: MP3 na pasta do artista principal, atalhos para os
    secundários e fora das pastas de conferência quando foi confirmado."""
    resultado = plano["result"]
    artistas = [str(a) for a in (resultado.get("metadata") or {}).get("artists") or [] if str(a).strip()]
    caminho = plano["audio_path"]
    if not artistas or not os.path.isfile(caminho):
        return
    novo = caminho
    if resultado.get("status") != "PENDENTE":
        novo = pltube.mover_para_artista_principal(caminho, artistas)
    if novo != caminho:
        relativo = pltube._caminho_relativo_banco(novo, conexao)
        conexao.execute("UPDATE music.metadata_resolution SET audio_path = ? WHERE video_key = ?",
                        (relativo, plano["video_key"]))
        conexao.execute("UPDATE video_cache SET path = ? WHERE video_key = ?", (relativo, plano["video_key"]))
        conexao.execute("UPDATE playlist_items SET path = ? WHERE video_key = ?", (relativo, plano["video_key"]))
        conexao.commit()
        plano["audio_path"] = novo
    for artista in artistas[1:]:
        pltube.criar_link_artista(novo, artista)
    if resultado.get("status") == "RESOLVIDO":
        pltube.limpar_atalhos_conferencia(novo)
