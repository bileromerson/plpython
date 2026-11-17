"""Migra músicas existentes para songs/_library e cria links nas pastas dos artistas.

Por segurança, a execução padrão é apenas uma simulação. Use --apply para alterar
os arquivos. O conteúdo nunca é baixado novamente nem copiado para cada artista.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

AUDIO_EXTENSIONS = {
    ".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".m4b", ".mp3",
    ".oga", ".ogg", ".opus", ".wav", ".wma",
}
LIBRARY_DIR_NAME = "_library"


def nome_seguro(valor: str, fallback: str = "Artista Desconhecido") -> str:
    valor = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(valor)).strip().strip(".")
    if not valor:
        valor = fallback
    if valor.casefold() == LIBRARY_DIR_NAME.casefold():
        valor = f"Artista {valor}"
    return valor[:180]


def hash_arquivo(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def obter_artistas(path: str, root: str) -> list[str]:
    """Usa a pasta original e, se disponíveis, as tags de artista embutidas."""
    relativo = os.path.relpath(os.path.dirname(path), root)
    partes = Path(relativo).parts
    nomes = []
    if partes and partes[0] not in {".", LIBRARY_DIR_NAME}:
        nomes.append(partes[0])

    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path, easy=True)
        if audio:
            tags = audio.get("artist", []) or audio.get("artists", [])
            if isinstance(tags, str):
                tags = [tags]
            for tag in tags:
                if not isinstance(tag, str):
                    continue
                # Divide apenas créditos explícitos de participação para não
                # quebrar nomes de bandas que contenham vírgulas ou '&'.
                partes_tag = re.split(r"\s+(?:feat(?:uring)?\.?|ft\.?)\s+", tag, flags=re.I)
                nomes.extend(parte.strip(" \t,;-") for parte in partes_tag if parte.strip(" \t,;-"))
    except Exception:
        # Sem tags legíveis, a pasta original continua sendo a referência.
        pass

    resultado = []
    vistos = set()
    for nome in nomes:
        nome = nome_seguro(nome)
        chave = nome.casefold()
        if chave not in vistos:
            resultado.append(nome)
            vistos.add(chave)
    return resultado or ["Artista Desconhecido"]


def listar_musicas(root: str) -> list[str]:
    musicas = []
    for pasta, subpastas, nomes in os.walk(root, followlinks=False):
        subpastas[:] = [
            nome for nome in subpastas
            if nome != LIBRARY_DIR_NAME and not os.path.islink(os.path.join(pasta, nome))
        ]
        for nome in nomes:
            path = os.path.join(pasta, nome)
            if os.path.splitext(nome)[1].lower() in AUDIO_EXTENSIONS and not os.path.islink(path):
                musicas.append(path)
    return sorted(musicas)


def indexar_biblioteca(library: str) -> dict[str, str]:
    por_hash: dict[str, str] = {}
    if not os.path.isdir(library):
        return por_hash
    for pasta, _, nomes in os.walk(library, followlinks=False):
        for nome in nomes:
            path = os.path.join(pasta, nome)
            if os.path.splitext(nome)[1].lower() not in AUDIO_EXTENSIONS or os.path.islink(path):
                continue
            try:
                por_hash.setdefault(hash_arquivo(path), path)
            except OSError as exc:
                print(f"Aviso: não consegui ler {path}: {exc}")
    return por_hash


def escolher_destino(library: str, nome_original: str, digest: str) -> str:
    nome_original = os.path.basename(nome_original)
    candidato = os.path.join(library, nome_original)
    if not os.path.lexists(candidato):
        return candidato

    stem, ext = os.path.splitext(nome_original)
    for sufixo in (digest[:12], digest, f"{digest}-1", f"{digest}-2", f"{digest}-3"):
        candidato = os.path.join(library, f"{stem} [{sufixo}]{ext}")
        if not os.path.lexists(candidato):
            return candidato
        if (
            os.path.isfile(candidato)
            and not os.path.islink(candidato)
            and hash_arquivo(candidato) == digest
        ):
            return candidato
    raise FileExistsError(f"Não encontrei nome livre e seguro para {nome_original}")


def criar_link(arquivo_original: str, pasta_artista: str, nome: str) -> str | None:
    os.makedirs(pasta_artista, exist_ok=True)
    caminho = os.path.join(pasta_artista, nome)
    if os.path.lexists(caminho):
        if os.path.realpath(caminho) == os.path.realpath(arquivo_original):
            return caminho
        stem, ext = os.path.splitext(nome)
        digest = hash_arquivo(arquivo_original)
        caminho = os.path.join(pasta_artista, f"{stem} [{digest[:12]}]{ext}")
        if os.path.lexists(caminho):
            if os.path.realpath(caminho) == os.path.realpath(arquivo_original):
                return caminho
            print(f"Aviso: conflito de nomes; não alterei {caminho}")
            return None

    alvo = os.path.relpath(arquivo_original, start=pasta_artista)
    try:
        os.symlink(alvo, caminho)
        return caminho
    except OSError as symlink_error:
        try:
            os.link(arquivo_original, caminho)
            return caminho
        except OSError as hardlink_error:
            print(f"Aviso: não consegui criar link em {caminho}: {symlink_error}; {hardlink_error}")
            return None


def mover_e_linkar(origem: str, destino: str, pasta_principal: str, nome: str, duplicado: bool) -> bool:
    """Move/deduplica um arquivo e mantém seu caminho antigo como link."""
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    if not duplicado:
        try:
            shutil.move(origem, destino)
        except OSError as exc:
            print(f"Erro movendo {origem} para a biblioteca: {exc}")
            return False
        if criar_link(destino, pasta_principal, nome) is None:
            # Restaura o original se não foi possível preservar o caminho da playlist.
            try:
                shutil.move(destino, origem)
            except OSError as restore_error:
                print(f"ERRO: falha ao restaurar {origem}: {restore_error}")
            return False
        return True

    temporario = origem + ".pltube-sync-tmp"
    if os.path.lexists(temporario):
        print(f"Erro: arquivo temporário já existe; não alterei {origem}: {temporario}")
        return False
    try:
        os.replace(origem, temporario)
    except OSError as exc:
        print(f"Erro preparando deduplicação de {origem}: {exc}")
        return False

    if criar_link(destino, pasta_principal, nome) is None:
        try:
            os.replace(temporario, origem)
        except OSError as restore_error:
            print(f"ERRO: falha ao restaurar {origem}: {restore_error}")
        return False

    try:
        os.remove(temporario)
    except OSError as exc:
        print(f"Aviso: sobrou um arquivo temporário {temporario}: {exc}")
    return True


def sincronizar(root: str, aplicar: bool = False) -> dict[str, int]:
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise ValueError(f"Pasta de músicas não encontrada: {root}")

    library = os.path.join(root, LIBRARY_DIR_NAME)
    musicas = listar_musicas(root)
    por_hash = indexar_biblioteca(library)
    resultado = {"encontradas": len(musicas), "movidas": 0, "deduplicadas": 0, "ignoradas": 0}

    if not musicas:
        print(f"Nenhum arquivo de áudio para sincronizar em {root}")
        return resultado

    modo = "APLICAR" if aplicar else "SIMULAÇÃO (nenhum arquivo será alterado)"
    print(f"{modo}: {len(musicas)} arquivo(s) de áudio encontrados em {root}")
    if not aplicar:
        print("Para realizar as mudanças, repita o comando acrescentando --apply.\n")

    for origem in musicas:
        try:
            digest = hash_arquivo(origem)
            artistas = obter_artistas(origem, root)
        except OSError as exc:
            print(f"Aviso: ignorando {origem}: {exc}")
            resultado["ignoradas"] += 1
            continue

        nome = os.path.basename(origem)
        relativo = os.path.relpath(os.path.dirname(origem), root)
        pasta_principal = os.path.join(root, relativo) if relativo != "." else root
        destino = por_hash.get(digest)
        duplicado = destino is not None
        if destino is None:
            try:
                destino = escolher_destino(library, nome, digest)
            except OSError as exc:
                print(f"Aviso: não consegui escolher um destino seguro para {origem}: {exc}")
                resultado["ignoradas"] += 1
                continue
            por_hash[digest] = destino

        acao = "reutilizar áudio já existente" if duplicado else "mover áudio para a biblioteca"
        print(f"[{acao}] {origem} -> {destino}")
        print(f"  links de artista: {', '.join(artistas)}")
        if not aplicar:
            continue

        if not mover_e_linkar(origem, destino, pasta_principal, nome, duplicado):
            resultado["ignoradas"] += 1
            continue
        if duplicado:
            resultado["deduplicadas"] += 1
        else:
            resultado["movidas"] += 1

        # A pasta original já contém o link. Adiciona links para colaboradores.
        artista_principal = os.path.basename(os.path.normpath(pasta_principal))
        for artista in artistas:
            if artista.casefold() == artista_principal.casefold():
                continue
            criar_link(destino, os.path.join(root, artista), nome)

    if aplicar:
        print(
            f"\nConcluído: {resultado['movidas']} arquivo(s) movido(s), "
            f"{resultado['deduplicadas']} duplicata(s) removida(s), "
            f"{resultado['ignoradas']} ignorado(s)."
        )
    return resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Centraliza músicas existentes e cria links por artista sem duplicar o áudio."
    )
    parser.add_argument("--root", default="songs", help="Pasta atual das músicas (padrão: songs)")
    parser.add_argument(
        "--apply", action="store_true",
        help="Executa a migração. Sem esta opção, só exibe uma simulação.",
    )
    args = parser.parse_args(argv)
    try:
        sincronizar(args.root, aplicar=args.apply)
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nMigração cancelada.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
