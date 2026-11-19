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

Cores: só num terminal de verdade (saída redirecionada para arquivo fica sem
cor). NO_COLOR=1 desliga; FORCE_COLOR=1 força.

Uso nos módulos:  from verbose import v, vv
                  v("SQL", "cache encontrado")      # aparece com -v ou mais
                  vv("Tags", "TIT2 = Legend")       # aparece com -vv ou mais
"""

from __future__ import annotations

import os
import re
import sys
import time

NIVEL = 0
_INICIO = time.monotonic()

# Cor de cada área (códigos ANSI).
_CORES_AREA = {
    "Erro": "1;31",          # vermelho forte
    "Resultado": "1",        # negrito; o status ganha a cor dele
    "Comparação": "1;35",    # magenta forte
    "MusicBrainz": "35",     # magenta
    "YouTube": "36",         # ciano
    "Modelo": "94",          # azul claro
    "SQL": "34",             # azul
    "Álbum": "32",           # verde
    "Capa": "32",
    "Tags": "32",
    "Arquivo": "33",         # amarelo
    "Pausa": "2",            # apagado
    "yt-dlp": "2",
    "Spotify": "92",         # verde claro
}
# Destaques dentro da mensagem.
_DESTAQUES = (
    (re.compile(r"→ bate[^;]*"), "32"),                        # YouTube = banco
    (re.compile(r"≠|→ não bate"), "33"),                       # divergência
    (re.compile(r"\bRESOLVIDO\b"), "1;32"),
    (re.compile(r"\bOBSERVAR\b"), "1;33"),
    (re.compile(r"\bPENDENTE\b"), "1;31"),
    (re.compile(r"\bmotivo:"), "33"),
)
_ANSI_WINDOWS: bool | None = None


def definir(nivel: int | None) -> None:
    global NIVEL, _INICIO
    NIVEL = max(0, int(nivel or 0))
    _INICIO = time.monotonic()


def ativo(nivel: int = 1) -> bool:
    return NIVEL >= nivel


def _habilitar_ansi_windows() -> bool:
    """No cmd/PowerShell do Windows, liga o suporte a cores ANSI uma vez."""
    global _ANSI_WINDOWS
    if _ANSI_WINDOWS is None:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            saida = kernel32.GetStdHandle(-11)
            modo = ctypes.c_uint32()
            _ANSI_WINDOWS = bool(
                kernel32.GetConsoleMode(saida, ctypes.byref(modo))
                and kernel32.SetConsoleMode(saida, modo.value | 0x0004)
            )
        except Exception:
            _ANSI_WINDOWS = False
    return _ANSI_WINDOWS


def cor_ativa() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb" or not getattr(sys.stdout, "isatty", lambda: False)():
        return False
    return os.name != "nt" or _habilitar_ansi_windows()


def _pintar(texto: str, codigo: str) -> str:
    return f"\033[{codigo}m{texto}\033[0m"


def colorir(texto: str, codigo: str) -> str:
    """Pinta um trecho de qualquer saída, só quando há cor (mesmas regras do -v)."""
    return _pintar(texto, codigo) if cor_ativa() else texto


def _colorir(area: str, mensagem: str, tempo: str) -> tuple[str, str, str]:
    for padrao, codigo in _DESTAQUES:
        mensagem = padrao.sub(lambda m, c=codigo: _pintar(m.group(0), c), mensagem)
    rotulo = f"[{area}]"
    if area in _CORES_AREA:
        rotulo = _pintar(rotulo, _CORES_AREA[area])
    return (_pintar(tempo, "2") if tempo else tempo), rotulo, mensagem


def log(nivel: int, area: str, mensagem: str) -> None:
    if NIVEL < nivel:
        return
    tempo = f"{time.monotonic() - _INICIO:7.1f}s " if NIVEL >= 2 else ""
    recuo = "  " if nivel >= 2 else ""
    rotulo = f"[{area}]"
    if cor_ativa():
        tempo, rotulo, mensagem = _colorir(area, mensagem, tempo)
    print(f"{tempo}{recuo}{rotulo} {mensagem}", flush=True)


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
