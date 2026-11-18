"""Reverte a migração antiga de songs/_library para pastas de artistas.

O script move cada MP3 físico para a pasta do artista principal, cria links
para os colaboradores, atualiza referências M3U encontradas e remove a pasta
_library apenas se ela ficar vazia. A execução normal é somente uma simulação;
use --apply para aplicar. Ele não baixa nem altera tags do áudio.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

AUDIO_EXTENSIONS = {
    ".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".m4b", ".mp3",
    ".oga", ".ogg", ".opus", ".wav", ".wma",
}
LIBRARY_DIR_NAME = "_library"


@dataclass(frozen=True)
class PlanoFaixa:
    origem: str
    destino: str
    artistas: tuple[str, ...]
    links_antigos: tuple[str, ...]
    sha256: str
    fallback_alfabetico: bool = False


def nome_seguro(valor: str, fallback: str = "Artista Desconhecido") -> str:
    valor = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(valor)).strip().strip(".")
    if not valor:
        valor = fallback
    if valor.casefold() == LIBRARY_DIR_NAME.casefold():
        valor = "Artista _library"
    return valor[:180]


def hash_arquivo(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def separar_credito(texto: str) -> list[str]:
    partes = re.split(r"\s+(?:feat(?:uring)?\.?|ft\.?)\s+", texto, flags=re.I)
    return [parte.strip(" \t,;-") for parte in partes if parte.strip(" \t,;-")]


def artistas_das_tags(path: str) -> list[str]:
    """Lê os créditos embutidos, se mutagen estiver instalado e houver tags."""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path, easy=True)
        if not audio:
            return []
        tags = audio.get("artists", []) or audio.get("artist", [])
        if isinstance(tags, str):
            tags = [tags]
        resultado: list[str] = []
        for tag in tags:
            if isinstance(tag, str):
                resultado.extend(separar_credito(tag))
        return _deduplicar(resultado)
    except Exception:
        return []


def _deduplicar(nomes: Iterable[str]) -> list[str]:
    resultado: list[str] = []
    vistos: set[str] = set()
    for nome in nomes:
        nome = nome_seguro(nome)
        chave = nome.casefold()
        if nome and chave not in vistos:
            resultado.append(nome)
            vistos.add(chave)
    return resultado


def artistas_do_estado(path: str, state_file: str | None) -> list[str]:
    """Recupera os créditos ordenados originalmente salvos pelo downloader."""
    if not state_file or not os.path.isfile(state_file):
        return []
    match = re.search(r"\[([A-Za-z0-9_-]{6,20})\](?=\.[^.]+$)", os.path.basename(path))
    if not match:
        return []
    try:
        with sqlite3.connect(state_file) as conexao:
            linhas = conexao.execute(
                "SELECT artists_json, path FROM video_cache "
                "WHERE video_id = ? AND status = 'complete'",
                (match.group(1),),
            ).fetchall()
    except sqlite3.Error:
        return []

    caminho_real = os.path.normcase(os.path.realpath(path))
    linhas.sort(
        key=lambda linha: (
            os.path.normcase(os.path.realpath(linha[1] or "")) != caminho_real
        )
    )
    for artistas_json, _ in linhas:
        try:
            nomes = json.loads(artistas_json or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(nomes, list):
            creditos = [nome for nome in nomes if isinstance(nome, str)]
            if creditos:
                return _deduplicar(creditos)
    return []


def links_para_origem(root: str, origem: str) -> list[str]:
    """Encontra symlinks de artista que apontam para o MP3 centralizado."""
    links: list[str] = []
    real_origem = os.path.realpath(origem)
    for pasta, subpastas, nomes in os.walk(root, followlinks=False):
        subpastas[:] = [
            nome for nome in subpastas
            if nome != LIBRARY_DIR_NAME
            and not os.path.islink(os.path.join(pasta, nome))
        ]
        for nome in nomes:
            caminho = os.path.join(pasta, nome)
            if not os.path.islink(caminho):
                continue
            if os.path.realpath(caminho) == real_origem:
                links.append(caminho)
    return sorted(links)


def artistas_dos_links(root: str, links: Iterable[str]) -> list[str]:
    nomes = []
    for link in links:
        relativo = os.path.relpath(os.path.dirname(link), root)
        primeira_pasta = Path(relativo).parts[0] if Path(relativo).parts else ""
        if primeira_pasta and primeira_pasta != LIBRARY_DIR_NAME:
            nomes.append(primeira_pasta)
    return _deduplicar(nomes)


def escolher_destino(root: str, artista: str, nome_original: str, digest: str, origem: str) -> str:
    pasta = os.path.join(root, nome_seguro(artista))
    nome_original = os.path.basename(nome_original)
    candidato = os.path.join(pasta, nome_original)
    if not os.path.lexists(candidato):
        return candidato
    if os.path.islink(candidato) and os.path.realpath(candidato) == os.path.realpath(origem):
        return candidato
    if os.path.isfile(candidato) and not os.path.islink(candidato):
        try:
            if hash_arquivo(candidato) == digest:
                return candidato
        except OSError:
            pass

    stem, ext = os.path.splitext(nome_original)
    for sufixo in (digest[:12], digest):
        alternativo = os.path.join(pasta, f"{stem} [{sufixo}]{ext}")
        if not os.path.lexists(alternativo):
            return alternativo
        if os.path.isfile(alternativo) and not os.path.islink(alternativo):
            try:
                if hash_arquivo(alternativo) == digest:
                    return alternativo
            except OSError:
                pass
    raise FileExistsError(f"Não encontrei nome livre e seguro para {candidato}")


def planejar(
    root: str, state_file: str | None = None
) -> tuple[list[PlanoFaixa], list[str]]:
    root = os.path.abspath(root)
    library = os.path.join(root, LIBRARY_DIR_NAME)
    if not os.path.isdir(root):
        raise ValueError(f"Pasta de músicas não encontrada: {root}")
    if not os.path.isdir(library):
        return [], []
    if state_file is None:
        state_file = os.path.join(os.path.dirname(root), "pltube_state.sqlite3")
    elif not os.path.isabs(state_file):
        state_file = os.path.abspath(state_file)

    planos: list[PlanoFaixa] = []
    avisos: list[str] = []
    for pasta, _, nomes in os.walk(library, followlinks=False):
        for nome in sorted(nomes):
            origem = os.path.join(pasta, nome)
            if os.path.splitext(nome)[1].lower() not in AUDIO_EXTENSIONS:
                continue
            if os.path.islink(origem):
                # Também reconhece o link de compatibilidade deixado por uma
                # execução interrompida, permitindo retomar a limpeza com segurança.
                destino_atual = os.path.realpath(origem)
                if (
                    not os.path.isfile(destino_atual)
                    or os.path.commonpath([root, destino_atual]) != root
                    or os.path.commonpath([library, destino_atual]) == library
                ):
                    continue
            try:
                digest = hash_arquivo(origem)
                links = links_para_origem(root, origem)
                creditos_estado = artistas_do_estado(origem, state_file)
                creditos_tags = artistas_das_tags(origem)
                creditos_links = artistas_dos_links(root, links)
                if creditos_estado:
                    artistas = _deduplicar(
                        [*creditos_estado, *creditos_tags, *creditos_links]
                    )
                    fallback = False
                elif creditos_tags:
                    artistas = _deduplicar([*creditos_tags, *creditos_links])
                    fallback = False
                elif creditos_links:
                    artistas = creditos_links
                    fallback = len(artistas) > 1
                    if fallback:
                        artistas = sorted(artistas, key=str.casefold)
                else:
                    avisos.append(
                        f"Ignorado (sem artista nas tags ou links): {origem}"
                    )
                    continue

                destino = escolher_destino(
                    root, artistas[0], nome, digest, origem
                )
                planos.append(
                    PlanoFaixa(
                        origem=origem,
                        destino=destino,
                        artistas=tuple(artistas),
                        links_antigos=tuple(links),
                        sha256=digest,
                        fallback_alfabetico=fallback,
                    )
                )
            except OSError as exc:
                avisos.append(f"Ignorado ({exc}): {origem}")
    return planos, avisos


def _link_para(arquivo: str, artista: str, destino: str, digest: str) -> str | None:
    """Cria/atualiza o link de um colaborador, sem substituir outro áudio."""
    pasta = os.path.join(os.path.dirname(os.path.dirname(destino)), nome_seguro(artista))
    # destino é root/artista/nome; subir dois níveis obtém root.
    os.makedirs(pasta, exist_ok=True)
    nome = os.path.basename(destino)
    caminho = os.path.join(pasta, nome)
    if os.path.abspath(caminho) == os.path.abspath(destino):
        return caminho

    if os.path.lexists(caminho):
        if os.path.islink(caminho):
            real = os.path.realpath(caminho)
            if real == os.path.realpath(destino) or real == os.path.realpath(arquivo):
                try:
                    os.remove(caminho)
                except OSError:
                    return None
            else:
                return _link_com_sufixo(pasta, nome, destino, digest)
        elif os.path.isfile(caminho):
            try:
                if hash_arquivo(caminho) == digest:
                    os.remove(caminho)
                else:
                    return _link_com_sufixo(pasta, nome, destino, digest)
            except OSError:
                return None
        else:
            return None

    alvo = os.path.relpath(destino, start=pasta)
    try:
        os.symlink(alvo, caminho)
        return caminho
    except OSError:
        try:
            os.link(destino, caminho)
            return caminho
        except OSError:
            return None


def _link_com_sufixo(pasta: str, nome: str, destino: str, digest: str) -> str | None:
    stem, ext = os.path.splitext(nome)
    caminho = os.path.join(pasta, f"{stem} [{digest[:12]}]{ext}")
    if os.path.lexists(caminho):
        if os.path.islink(caminho) and os.path.realpath(caminho) == os.path.realpath(destino):
            return caminho
        if os.path.isfile(caminho) and not os.path.islink(caminho):
            try:
                if hash_arquivo(caminho) == digest:
                    os.remove(caminho)
                else:
                    return None
            except OSError:
                return None
        else:
            return None
    try:
        os.symlink(os.path.relpath(destino, start=pasta), caminho)
        return caminho
    except OSError:
        try:
            os.link(destino, caminho)
            return caminho
        except OSError:
            return None


def _reapontar_link_antigo(
    caminho: str, origem_antiga: str, destino: str, digest: str
) -> bool:
    """Mantém o nome de um link anterior, apontando-o para o arquivo novo."""
    if os.path.abspath(caminho) == os.path.abspath(destino):
        return True
    if os.path.lexists(caminho):
        if os.path.islink(caminho):
            real_atual = os.path.normcase(os.path.realpath(caminho))
            destino_real = os.path.normcase(os.path.realpath(destino))
            origem_abs = os.path.normcase(os.path.abspath(origem_antiga))
            alvo_link = os.path.normcase(
                os.path.abspath(
                    os.path.join(os.path.dirname(caminho), os.readlink(caminho))
                )
            )
            destino_abs = os.path.normcase(os.path.abspath(destino))
            if alvo_link == destino_abs:
                return True
            if alvo_link != origem_abs and real_atual != destino_real:
                return False
            os.remove(caminho)
        elif os.path.isfile(caminho):
            if hash_arquivo(caminho) != digest:
                return False
            os.remove(caminho)
        else:
            return False

    pasta = os.path.dirname(caminho)
    os.makedirs(pasta, exist_ok=True)
    try:
        os.symlink(os.path.relpath(destino, start=pasta), caminho)
        return True
    except OSError:
        try:
            os.link(destino, caminho)
            return True
        except OSError:
            return False


def _atualizar_m3u(playlist_dir: str, origem: str, destino: str, aplicar: bool) -> int:
    """Troca caminhos que apontam diretamente para _library pelo destino novo."""
    if not os.path.isdir(playlist_dir):
        return 0
    origem_norm = os.path.normcase(os.path.abspath(origem))
    atualizadas = 0
    for pasta, _, nomes in os.walk(playlist_dir):
        for nome in nomes:
            if not nome.lower().endswith(".m3u"):
                continue
            caminho_m3u = os.path.join(pasta, nome)
            try:
                with open(caminho_m3u, "r", encoding="utf-8-sig", newline="") as arquivo:
                    linhas = arquivo.read().splitlines()
            except (OSError, UnicodeError):
                continue

            alterou = False
            for indice, linha in enumerate(linhas):
                item = linha.strip()
                if not item or item.startswith("#") or "://" in item:
                    continue
                item_abs = os.path.abspath(
                    item if os.path.isabs(item) else os.path.join(pasta, item)
                )
                if os.path.normcase(item_abs) != origem_norm:
                    continue
                caminho_destino = (
                    destino if os.path.isabs(item)
                    else os.path.relpath(destino, start=pasta)
                )
                linhas[indice] = caminho_destino
                alterou = True

            if alterou:
                atualizadas += 1
                if aplicar:
                    texto = "\n".join(linhas) + "\n"
                    pasta_temp = os.path.dirname(caminho_m3u)
                    fd, temporario = tempfile.mkstemp(
                        prefix=".pltube-m3u-", suffix=".tmp", dir=pasta_temp
                    )
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as arquivo:
                            arquivo.write(texto)
                        os.replace(temporario, caminho_m3u)
                    except Exception:
                        try:
                            os.unlink(temporario)
                        except OSError:
                            pass
                        raise
    return atualizadas


def aplicar_plano(plano: PlanoFaixa, root: str, playlist_dir: str) -> tuple[bool, int, str]:
    origem, destino = plano.origem, plano.destino
    origem_era_link = os.path.islink(origem)
    link_aponta_para_destino = (
        origem_era_link
        and os.path.realpath(origem) == os.path.realpath(destino)
    )
    os.makedirs(os.path.dirname(destino), exist_ok=True)

    destino_existente = os.path.lexists(destino)
    se_destino_mesmo_conteudo = False
    if destino_existente:
        if os.path.islink(destino) and os.path.realpath(destino) == os.path.realpath(origem):
            os.remove(destino)
            destino_existente = False
        elif os.path.isfile(destino) and not os.path.islink(destino):
            se_destino_mesmo_conteudo = hash_arquivo(destino) == plano.sha256
        if destino_existente and not se_destino_mesmo_conteudo:
            return False, 0, f"Conflito de destino não resolvido: {destino}"

    if not se_destino_mesmo_conteudo and not link_aponta_para_destino:
        os.replace(origem, destino)

    # Mantém o caminho antigo funcionando enquanto links/M3Us são atualizados.
    compat_link_criado = False
    if origem != destino and not os.path.lexists(origem):
        try:
            os.symlink(os.path.relpath(destino, start=os.path.dirname(origem)), origem)
            compat_link_criado = True
        except OSError:
            compat_link_criado = False

    # Preserva links existentes (inclusive seus nomes antigos) e redireciona-os.
    artistas_com_link_antigo: set[str] = set()
    for link_antigo in plano.links_antigos:
        if os.path.abspath(link_antigo) == os.path.abspath(destino):
            continue
        artista = Path(os.path.relpath(os.path.dirname(link_antigo), root)).parts[0]
        if not _reapontar_link_antigo(
            link_antigo, origem, destino, plano.sha256
        ):
            return False, 0, f"Não consegui preservar o link antigo: {link_antigo}"
        artistas_com_link_antigo.add(artista.casefold())

    # Cria links adicionais para os créditos que ainda não tinham um atalho.
    for artista in plano.artistas[1:]:
        if artista.casefold() in artistas_com_link_antigo:
            continue
        if _link_para(origem, artista, destino, plano.sha256) is None:
            return False, 0, f"Não consegui criar link para {artista}; mantive o caminho antigo: {origem}"

    try:
        m3u_atualizadas = _atualizar_m3u(
            playlist_dir, origem, destino, aplicar=True
        )
    except OSError as exc:
        return False, 0, f"Falha atualizando M3U; mantive o caminho antigo: {exc}"

    if (compat_link_criado or link_aponta_para_destino) and os.path.islink(origem):
        try:
            os.remove(origem)
        except OSError as exc:
            return False, m3u_atualizadas, f"Não removi o link temporário {origem}: {exc}"
    elif se_destino_mesmo_conteudo and os.path.isfile(origem) and not os.path.islink(origem):
        try:
            if hash_arquivo(origem) == plano.sha256:
                os.remove(origem)
        except OSError as exc:
            return False, m3u_atualizadas, f"Não removi a cópia antiga {origem}: {exc}"

    return True, m3u_atualizadas, ""


def reverter(
    root: str,
    playlist_dir: str | None = None,
    aplicar: bool = False,
    state_file: str | None = None,
) -> dict[str, int]:
    root = os.path.abspath(root)
    playlist_dir = os.path.abspath(
        playlist_dir or os.path.join(os.path.dirname(root), "playlists")
    )
    planos, avisos = planejar(root, state_file)
    resultado = {
        "encontradas": len(planos),
        "movidas": 0,
        "links_criados": 0,
        "playlists_atualizadas": 0,
        "ignoradas": len(avisos),
    }
    modo = "APLICAR" if aplicar else "SIMULAÇÃO (nenhum arquivo será alterado)"
    print(f"{modo}: {len(planos)} MP3(s) encontrados em {os.path.join(root, LIBRARY_DIR_NAME)}")
    for aviso in avisos:
        print(f"Aviso: {aviso}")

    for plano in planos:
        principal = plano.artistas[0]
        nota = " [principal presumido em ordem alfabética; sem tags]" if plano.fallback_alfabetico else ""
        print(f"[{plano.origem}] -> [{plano.destino}] | principal: {principal}{nota}")
        if len(plano.artistas) > 1:
            print(f"  links para: {', '.join(plano.artistas[1:])}")
        if not aplicar:
            # Só conta as M3Us afetadas; não grava nada.
            resultado["playlists_atualizadas"] += _atualizar_m3u(
                playlist_dir, plano.origem, plano.destino, aplicar=False
            )
            continue
        ok, m3u_count, erro = aplicar_plano(plano, root, playlist_dir)
        if not ok:
            print(f"  ERRO: {erro}")
            resultado["ignoradas"] += 1
            continue
        resultado["movidas"] += 1
        resultado["links_criados"] += len(plano.artistas) - 1
        resultado["playlists_atualizadas"] += m3u_count

    library = os.path.join(root, LIBRARY_DIR_NAME)
    if aplicar:
        try:
            if os.path.isdir(library) and not os.listdir(library):
                os.rmdir(library)
        except OSError as exc:
            print(f"Aviso: não removi {library}: {exc}")
        print(
            f"\nConcluído: {resultado['movidas']} faixa(s) reorganizada(s), "
            f"{resultado['links_criados']} link(s) criado(s), "
            f"{resultado['playlists_atualizadas']} playlist(s) atualizada(s), "
            f"{resultado['ignoradas']} item(ns) ignorado(s)."
        )
    else:
        print("\nPara fazer as mudanças, revise a simulação e repita o comando com --apply.")
    return resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reverte songs/_library para MP3 físico no artista principal e links nos colaboradores."
    )
    parser.add_argument("--root", default="songs", help="Pasta de músicas (padrão: songs)")
    parser.add_argument(
        "--playlist-dir",
        help="Pasta das playlists M3U (padrão: pasta irmã playlists, ao lado de songs)",
    )
    parser.add_argument(
        "--state-file",
        help="Banco SQLite do pltube para recuperar a ordem original dos artistas (autodetectado ao lado de songs)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Aplica movimentos/links e atualiza M3Us; sem esta opção, só simula.",
    )
    return executar(parser.parse_args(argv))


def executar(args: argparse.Namespace) -> int:
    """Executa a reversão a partir de argumentos já interpretados."""
    try:
        reverter(
            args.root,
            args.playlist_dir,
            aplicar=args.apply,
            state_file=args.state_file,
        )
    except (OSError, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nReversão interrompida. Execute novamente para conferir o estado; links antigos foram preservados quando possível.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
