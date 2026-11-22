"""Banco de artistas (sql/artistas.sqlite3): o nome certo de cada artista e as
variações com que ele aparece por aí ("Henrique e Juliano", "ACDC", "ImagineDragons").

- O download coloca aqui todo artista confirmado.
- Na hora de identificar uma música, os nomes daqui (e as variações) ajudam a achar o
  artista no título, mesmo quando ele ainda não está no banco de músicas.
- Toda faixa identificada passa por aqui: uma variação vira o nome certo.
- `plpython.py artists fix` usa este banco para corrigir as músicas já baixadas.

Fica anexado à conexão principal como o esquema `artistas` (tabelas
artistas.artistas e artistas.variantes), mas é um arquivo separado: dá para
copiar para outra biblioteca.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable

ESQUEMA = "artistas"


def chave(nome: Any) -> str:
    """Nome comparável: sem acento, caixa, pontuação nem espaço ("AC/DC" = "acdc")."""
    from textos import _normalize_text

    return _normalize_text(str(nome or "")).replace(" ", "")


def criar(conexao: sqlite3.Connection, esquema: str = ESQUEMA) -> None:
    conexao.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {esquema}.artistas (
            id INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            chave TEXT NOT NULL UNIQUE,
            origem TEXT NOT NULL DEFAULT '',
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS {esquema}.variantes (
            chave TEXT PRIMARY KEY,
            variante TEXT NOT NULL,
            artista_id INTEGER NOT NULL,
            origem TEXT NOT NULL DEFAULT '',
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS {esquema}.variantes_artista ON variantes(artista_id);
        """
    )


def anexar(conexao: sqlite3.Connection, caminho: str) -> None:
    """Anexa o arquivo de artistas a uma conexão já aberta (cria se não existir)."""
    caminho = os.path.abspath(caminho)
    if any(linha[1] == ESQUEMA for linha in conexao.execute("PRAGMA database_list")):
        return
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    conexao.execute("ATTACH DATABASE ? AS " + ESQUEMA, (caminho,))
    criar(conexao)
    conexao.commit()


def anexado(conexao: sqlite3.Connection | None) -> bool:
    if conexao is None:
        return False
    try:
        return any(linha[1] == ESQUEMA for linha in conexao.execute("PRAGMA database_list"))
    except sqlite3.Error:
        return False


def abrir(caminho: str) -> sqlite3.Connection:
    """Conexão só com o banco de artistas (para testes e ferramentas)."""
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    anexar(conexao, caminho)
    return conexao


# ---------------------------------------------------------------- consultas

def _id(conexao: sqlite3.Connection, nome: str) -> int | None:
    k = chave(nome)
    if not k:
        return None
    linha = conexao.execute(f"SELECT id FROM {ESQUEMA}.artistas WHERE chave = ?", (k,)).fetchone()
    if linha:
        return int(linha[0])
    linha = conexao.execute(f"SELECT artista_id FROM {ESQUEMA}.variantes WHERE chave = ?", (k,)).fetchone()
    return int(linha[0]) if linha else None


def _nome(conexao: sqlite3.Connection, artista_id: int) -> str:
    linha = conexao.execute(f"SELECT nome FROM {ESQUEMA}.artistas WHERE id = ?", (artista_id,)).fetchone()
    return str(linha[0]) if linha else ""


def canonico(conexao: sqlite3.Connection | None, nome: Any) -> str | None:
    """Nome certo de um artista ou variação; None se não está no banco."""
    if not anexado(conexao):
        return None
    artista_id = _id(conexao, str(nome or ""))
    return _nome(conexao, artista_id) if artista_id is not None else None


def corrigir_nomes(conexao: sqlite3.Connection | None, nomes: Iterable[Any]) -> list[str]:
    """Troca cada variação pelo nome certo, sem repetir artista."""
    saida: dict[str, str] = {}
    for nome in nomes or []:
        texto = str(nome or "").strip()
        if not texto:
            continue
        certo = canonico(conexao, texto) or texto
        saida.setdefault(chave(certo) or certo, certo)
    return list(saida.values())


def nomes_conhecidos(conexao: sqlite3.Connection | None) -> list[str]:
    """Nomes e variações, para procurar o artista no título do vídeo."""
    if not anexado(conexao):
        return []
    nomes = [str(l[0]) for l in conexao.execute(f"SELECT nome FROM {ESQUEMA}.artistas ORDER BY nome")]
    nomes += [str(l[0]) for l in conexao.execute(f"SELECT variante FROM {ESQUEMA}.variantes ORDER BY variante")]
    return list(dict.fromkeys(nomes))


def mapa(conexao: sqlite3.Connection | None) -> dict[str, str]:
    """{chave: nome certo} de todos os nomes e variações, lido de uma vez (2 consultas).

    O download usa isto na memória, em vez de perguntar ao SQL a cada música."""
    if not anexado(conexao):
        return {}
    saida: dict[str, str] = {}
    for k, nome in conexao.execute(f"SELECT chave, nome FROM {ESQUEMA}.artistas"):
        saida[str(k)] = str(nome)
    for k, nome in conexao.execute(
            f"SELECT v.chave, a.nome FROM {ESQUEMA}.variantes v JOIN {ESQUEMA}.artistas a ON a.id = v.artista_id"):
        saida.setdefault(str(k), str(nome))
    return saida


@dataclass
class Artista:
    id: int
    nome: str
    origem: str
    variantes: list[str] = field(default_factory=list)
    faixas: int = 0


def listar(conexao: sqlite3.Connection, busca: str = "") -> list[Artista]:
    artistas = {
        int(l["id"]): Artista(int(l["id"]), l["nome"], l["origem"])
        for l in conexao.execute(f"SELECT id, nome, origem FROM {ESQUEMA}.artistas ORDER BY nome COLLATE NOCASE")
    }
    for l in conexao.execute(f"SELECT artista_id, variante FROM {ESQUEMA}.variantes ORDER BY variante"):
        if int(l[0]) in artistas:
            artistas[int(l[0])].variantes.append(l[1])
    if _tem_tabela(conexao, "music", "music_tracks"):
        for (artistas_json,) in conexao.execute("SELECT artists_json FROM music.music_tracks"):
            try:
                nomes = json.loads(artistas_json or "[]")
            except (TypeError, ValueError):
                continue
            vistos = set()
            for nome in nomes:
                artista_id = _id(conexao, str(nome))
                if artista_id in artistas and artista_id not in vistos:
                    artistas[artista_id].faixas += 1
                    vistos.add(artista_id)
    lista = list(artistas.values())
    if busca:
        k = chave(busca)
        lista = [a for a in lista if k in chave(a.nome) or any(k in chave(v) for v in a.variantes)]
    return lista


def _tem_tabela(conexao: sqlite3.Connection, esquema: str, tabela: str) -> bool:
    try:
        return bool(conexao.execute(
            f"SELECT 1 FROM {esquema}.sqlite_master WHERE type='table' AND name=?", (tabela,)
        ).fetchone())
    except sqlite3.Error:
        return False


# ---------------------------------------------------------------- alterações

def adicionar(conexao: sqlite3.Connection | None, nome: Any, origem: str = "download") -> int | None:
    """Coloca o artista no banco, se ainda não estiver (nem como variação)."""
    if not anexado(conexao):
        return None
    texto = str(nome or "").strip()
    k = chave(texto)
    if not k or texto.casefold() in {"unknown", "desconhecido", "various artists", "vários artistas"}:
        return None
    existente = _id(conexao, texto)
    if existente is not None:
        return existente
    cursor = conexao.execute(
        f"INSERT INTO {ESQUEMA}.artistas (nome, chave, origem) VALUES (?, ?, ?)", (texto, k, origem)
    )
    return int(cursor.lastrowid)


def adicionar_variante(conexao: sqlite3.Connection, nome: str, variante: str, origem: str = "manual") -> str:
    """`variante` passa a significar `nome`. Se a variação era um artista à parte,
    os dois viram um só (as variações dele vêm junto). Devolve o que aconteceu."""
    alvo = adicionar(conexao, nome, origem)
    if alvo is None:
        raise ValueError(f"nome inválido: {nome!r}")
    k = chave(variante)
    if not k:
        raise ValueError(f"variação inválida: {variante!r}")
    if k == chave(_nome(conexao, alvo)):
        # "WHSPRS" e "Whsprs", "ImagineDragons" e "Imagine Dragons": já são o mesmo artista
        return f"já é o mesmo artista (só muda maiúscula/espaço/pontuação); vale a grafia {_nome(conexao, alvo)}"
    outro = conexao.execute(f"SELECT id, nome FROM {ESQUEMA}.artistas WHERE chave = ?", (k,)).fetchone()
    mensagem = "variação adicionada"
    if outro and int(outro["id"]) != alvo:
        conexao.execute(f"UPDATE {ESQUEMA}.variantes SET artista_id = ? WHERE artista_id = ?", (alvo, outro["id"]))
        conexao.execute(f"DELETE FROM {ESQUEMA}.artistas WHERE id = ?", (outro["id"],))
        mensagem = f"{outro['nome']} juntado a {_nome(conexao, alvo)}"
    conexao.execute(
        f"""INSERT INTO {ESQUEMA}.variantes (chave, variante, artista_id, origem) VALUES (?, ?, ?, ?)
            ON CONFLICT(chave) DO UPDATE SET variante = excluded.variante, artista_id = excluded.artista_id,
            origem = excluded.origem""",
        (k, str(variante).strip(), alvo, origem),
    )
    conexao.execute(f"UPDATE {ESQUEMA}.artistas SET atualizado_em = CURRENT_TIMESTAMP WHERE id = ?", (alvo,))
    return mensagem


def renomear(conexao: sqlite3.Connection, nome: str, novo: str) -> None:
    """Troca o nome certo; o antigo fica como variação."""
    artista_id = _id(conexao, nome)
    if artista_id is None:
        raise ValueError(f"artista não encontrado: {nome}")
    antigo = _nome(conexao, artista_id)
    k_novo = chave(novo)
    if not k_novo:
        raise ValueError(f"nome inválido: {novo!r}")
    outro = _id(conexao, novo)
    if outro is not None and outro != artista_id:
        raise ValueError(f"{novo} já é outro artista; use `artists alias \"{antigo}\" \"{novo}\"` para juntar")
    conexao.execute(f"DELETE FROM {ESQUEMA}.variantes WHERE chave = ?", (k_novo,))
    conexao.execute(
        f"UPDATE {ESQUEMA}.artistas SET nome = ?, chave = ?, origem = 'manual', "
        "atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
        (novo.strip(), k_novo, artista_id),
    )
    if chave(antigo) != k_novo:
        conexao.execute(
            f"INSERT OR REPLACE INTO {ESQUEMA}.variantes (chave, variante, artista_id, origem) VALUES (?, ?, ?, 'manual')",
            (chave(antigo), antigo, artista_id),
        )


def remover(conexao: sqlite3.Connection, nome: str) -> str:
    """Tira uma variação; se `nome` for o nome certo, tira o artista inteiro."""
    k = chave(nome)
    if conexao.execute(f"DELETE FROM {ESQUEMA}.variantes WHERE chave = ?", (k,)).rowcount:
        return "variação removida"
    linha = conexao.execute(f"SELECT id FROM {ESQUEMA}.artistas WHERE chave = ?", (k,)).fetchone()
    if not linha:
        raise ValueError(f"não está no banco de artistas: {nome}")
    conexao.execute(f"DELETE FROM {ESQUEMA}.variantes WHERE artista_id = ?", (linha[0],))
    conexao.execute(f"DELETE FROM {ESQUEMA}.artistas WHERE id = ?", (linha[0],))
    return "artista removido (com as variações)"


def importar_biblioteca(conexao: sqlite3.Connection) -> int:
    """Traz os artistas das faixas CONFIRMADO do banco de músicas."""
    if not _tem_tabela(conexao, "music", "music_tracks"):
        return 0
    from collections import Counter

    antes = conexao.execute(f"SELECT COUNT(*) FROM {ESQUEMA}.artistas").fetchone()[0]
    grafias: Counter[str] = Counter()
    for (artistas_json,) in conexao.execute(
        "SELECT artists_json FROM music.music_tracks WHERE metadata_status = 'CONFIRMADO'"
    ).fetchall():
        try:
            grafias.update(str(n).strip() for n in json.loads(artistas_json or "[]") if str(n).strip())
        except (TypeError, ValueError, AttributeError):
            continue
    # "WHSPRS" (3 faixas) e "Whsprs" (1): fica a grafia mais usada
    for nome, _ in sorted(grafias.items(), key=lambda i: (-i[1], i[0].isupper() or i[0].islower(), i[0])):
        adicionar(conexao, nome, "biblioteca")
    conexao.commit()
    return conexao.execute(f"SELECT COUNT(*) FROM {ESQUEMA}.artistas").fetchone()[0] - antes


def vazio(conexao: sqlite3.Connection) -> bool:
    return not conexao.execute(f"SELECT 1 FROM {ESQUEMA}.artistas LIMIT 1").fetchone()


def parecidos(conexao: sqlite3.Connection) -> list[tuple[Artista, Artista]]:
    """Pares que podem ser o mesmo artista escrito diferente ("Henrique e Juliano" e
    "Henrique & Juliano"), para juntar com `artists alias`."""
    from textos import _normalize_text, _similarity

    artistas = listar(conexao)
    pares = []
    for i, a in enumerate(artistas):
        na = _normalize_text(a.nome)
        for b in artistas[i + 1:]:
            nb = _normalize_text(b.nome)
            sem_e = lambda t: " ".join(p for p in t.split() if p not in {"e", "and", "y", "the", "os", "as"})
            if (sem_e(na) and sem_e(na).replace(" ", "") == sem_e(nb).replace(" ", "")) or (
                    min(len(na), len(nb)) >= 5 and _similarity(na.replace(" ", ""), nb.replace(" ", "")) >= 0.9):
                pares.append((a, b))
    return pares


def corrigir_resultado(conexao: sqlite3.Connection | None, resultado: dict[str, Any]) -> list[tuple[str, str]]:
    """Troca variações pelo nome certo no resultado da identificação.
    Devolve as trocas feitas [(de, para)]."""
    if not anexado(conexao) or not isinstance(resultado, dict):
        return []
    metadata = resultado.get("metadata")
    if not isinstance(metadata, dict):
        return []
    trocas = []
    for campo in ("artists", "album_artists"):
        antes = [str(a) for a in metadata.get(campo) or [] if str(a).strip()]
        depois = corrigir_nomes(conexao, antes)
        if depois != antes:
            trocas += [(a, canonico(conexao, a)) for a in antes if canonico(conexao, a) not in (None, a)]
            metadata[campo] = depois
    return list(dict.fromkeys(trocas))
