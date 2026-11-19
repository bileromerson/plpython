"""Aplica revisões manuais da planilha metadados_pendentes.csv.

Por segurança, apenas simula por padrão. Use --apply para gravar as tags e o
estado local. Não consulta YouTube nem MusicBrainz e não baixa áudio.
"""

from __future__ import annotations

import argparse
import csv
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


def _caminho(base: str, valor: str) -> str:
    return os.path.abspath(valor if os.path.isabs(valor) else os.path.join(base, valor))


def _artistas(valor: str) -> list[str]:
    if valor.strip().casefold() == LIMPAR.casefold():
        raise ValueError("não é permitido limpar todos os artistas")
    nomes = [parte.strip() for parte in valor.split("|") if parte.strip()]
    nomes = pltube.normalizar_artistas({"artists": nomes}, permitir_uploader=False)
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
    resultado["manual_review"] = {
        "fields": list(selecionados),
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


def aplicar_revisoes(
    arquivo_csv: str,
    arquivo_estado: str,
    pasta_musicas: str,
    pasta_playlists: str,
    *,
    arquivo_metadados: str | None = None,
    aplicar: bool = False,
) -> dict[str, int]:
    """Simula ou aplica somente linhas/campos explicitamente selecionados."""
    if not os.path.isfile(arquivo_csv):
        raise FileNotFoundError(f"CSV de revisão não encontrado: {arquivo_csv}")
    if not os.path.isfile(arquivo_estado):
        raise FileNotFoundError(
            f"Banco SQLite não encontrado: {arquivo_estado}. Rode na mesma pasta do download (ou use --downloads-db)."
        )
    os.makedirs(pasta_playlists, exist_ok=True)
    pltube.SONGS_DIR = os.path.abspath(pasta_musicas)
    pltube.PLAYLISTS_DIR = os.path.abspath(pasta_playlists)
    pltube.BASE_DIR = os.path.dirname(os.path.abspath(arquivo_csv))
    pltube.LOG_FILE = os.path.join(pltube.BASE_DIR, "Logs.txt")

    with open(arquivo_csv, "r", encoding="utf-8-sig", newline="") as arquivo:
        reader = csv.DictReader(arquivo)
        obrigatorias = {
            "video_key", "arquivo_audio", "aplicar", "usar_titulo", "titulo_final",
            "usar_artistas", "artistas_finais", "usar_album", "album_final",
            "usar_isrc", "isrc_final", "confirmar_manual",
        }
        faltantes = obrigatorias - set(reader.fieldnames or [])
        if faltantes:
            raise ValueError(
                "a planilha não tem colunas de revisão atualizadas: "
                + ", ".join(sorted(faltantes))
                + ". Rode novamente pltube.py com --modelo-hibrido."
            )
        registros = list(reader)

    conexao = pltube.abrir_banco_estado(arquivo_estado, arquivo_metadados)
    try:
        planos: list[dict[str, Any]] = []
        erros = 0
        selecionadas = 0
        for numero, registro in enumerate(registros, start=2):
            if not _verdadeiro(registro.get("aplicar")):
                continue
            selecionadas += 1
            try:
                planos.append(_montar_plano(registro, conexao, pasta_musicas))
            except (ValueError, TypeError, KeyError) as exc:
                erros += 1
                print(f"Linha {numero}: não aplicada — {exc}")

        if not selecionadas:
            print("Nenhuma linha marcada com aplicar=sim.")
            return {"planejadas": 0, "aplicadas": 0, "erros": 0}

        modo = "APLICAR" if aplicar else "SIMULAÇÃO — nenhuma alteração será feita"
        print(f"{modo}: {len(planos)} faixa(s) válida(s); {erros} erro(s).")
        for plano in planos:
            campos = ", ".join(plano["fields"]) or "confirmação manual"
            estado = "RESOLVIDO" if plano["confirmed"] else "continua pendente"
            print(
                f"{plano['video_id'] or plano['video_key']}: {plano['audio_path']} "
                f"| campos: {campos} | {estado}"
            )

        aplicadas = 0
        if aplicar:
            for plano in planos:
                try:
                    _aplicar_plano(conexao, plano, pasta_playlists)
                    aplicadas += 1
                    print(f"Atualizado: {plano['audio_path']}")
                except Exception as exc:
                    erros += 1
                    print(f"Falha ao aplicar {plano['video_key']}: {exc}")
            pltube.exportar_metadados_pendentes(conexao, arquivo_csv)
            print(f"Planilha atualizada: {arquivo_csv}")
        else:
            print("Revise o plano e repita o comando com --apply para gravar as alterações.")
        return {"planejadas": len(planos), "aplicadas": aplicadas, "erros": erros}
    finally:
        conexao.close()


def executar(args: argparse.Namespace) -> int:
    """Executa o aplicador a partir de argumentos já interpretados (usado por plpython.py)."""
    # A pasta base só serve para ler CSVs no download; aqui tudo vem da pasta atual.
    base = os.getcwd()
    arquivo_csv = _caminho(base, args.arquivo)
    arquivo_estado = _caminho(base, args.downloads_db)
    arquivo_metadados = _caminho(base, args.metadata_db)
    padrao_musicas = os.path.join(base, "songs")
    pasta_musicas = os.path.abspath(args.songs_dir) if args.songs_dir else padrao_musicas
    pasta_playlists = (
        os.path.abspath(args.playlist_dir)
        if args.playlist_dir
        else os.path.join(base, "playlists")
    )
    try:
        resultado = aplicar_revisoes(
            arquivo_csv,
            arquivo_estado,
            pasta_musicas,
            pasta_playlists,
            arquivo_metadados=arquivo_metadados,
            aplicar=args.apply,
        )
    except (OSError, ValueError) as exc:
        print(f"Erro: {exc}")
        return 1
    return 1 if resultado["erros"] else 0
