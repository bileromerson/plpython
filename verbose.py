"""Modo detalhado no estilo do nmap: -v, -vv e -vvv.

  -v    o que acontece em cada música: erros que normalmente só iriam para o
        Logs.txt, consultas ao SQL, ao YouTube e ao MusicBrainz, se os dados do
        YouTube e do banco batem ou divergem (e qual preferência foi aplicada),
        pausas, arquivos movidos e o resumo do que foi gravado no MP3.
  -vv   tudo do -v e mais: cada hipótese de artista/título com a confiança, os
        motivos da avaliação do canal, os candidatos do MusicBrainz com nota,
        os metadados lidos do YouTube campo a campo, cada tag gravada no MP3,
        requisições HTTP ao MusicBrainz, avisos do yt-dlp e o tempo decorrido.
  -vvv  tudo do -vv e mais a saída de depuração do próprio yt-dlp.

Uso nos módulos:  from verbose import v, vv
                  v("SQL", "cache encontrado")      # aparece com -v ou mais
                  vv("Tags", "TIT2 = Legend")       # aparece com -vv ou mais
"""

from __future__ import annotations

import time

NIVEL = 0
_INICIO = time.monotonic()


def definir(nivel: int | None) -> None:
    global NIVEL, _INICIO
    NIVEL = max(0, int(nivel or 0))
    _INICIO = time.monotonic()


def ativo(nivel: int = 1) -> bool:
    return NIVEL >= nivel


def log(nivel: int, area: str, mensagem: str) -> None:
    if NIVEL < nivel:
        return
    tempo = f"{time.monotonic() - _INICIO:7.1f}s " if NIVEL >= 2 else ""
    recuo = "  " if nivel >= 2 else ""
    print(f"{tempo}{recuo}[{area}] {mensagem}", flush=True)


def v(area: str, mensagem: str) -> None:
    log(1, area, mensagem)


def vv(area: str, mensagem: str) -> None:
    log(2, area, mensagem)


def vvv(area: str, mensagem: str) -> None:
    log(3, area, mensagem)


def curto(valor: object, limite: int = 90) -> str:
    """Texto de uma linha, cortado, para não poluir o terminal."""
    texto = " ".join(str(valor).split())
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"
