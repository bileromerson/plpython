"""Barra de progresso fixa na última linha do terminal (-P / --progress).

Como no `pip install`: as mensagens continuam rolando acima e a última linha
mostra o andamento total, por exemplo:

  [██████████░░░░░░░░░░]  42% 21/50 · ~3m10s · Alone.csv: Won't Go - snuffles · baixando 63%

Funciona apagando a linha da barra, escrevendo a mensagem e redesenhando a
barra logo abaixo (a mesma técnica do tqdm). Usa só "\\r" e espaços, então
funciona até no cmd do Windows. Sem terminal (saída redirecionada para um
arquivo), a barra fica desligada.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time

ATUAL: "Progresso | None" = None


class _Saida:
    """Envolve stdout/stderr: apaga a barra antes de cada mensagem e a redesenha depois."""

    def __init__(self, original, barra: "Progresso") -> None:
        self._original = original
        self._barra = barra

    def write(self, texto: str) -> int:
        if not texto:
            return 0
        with self._barra.trava:
            self._barra._apagar()
            escrito = self._original.write(texto)
            self._original.flush()
            self._barra.inicio_de_linha = texto.endswith("\n")
            if self._barra.inicio_de_linha:
                self._barra._desenhar(forcar=True)
        return escrito

    def flush(self) -> None:
        self._original.flush()

    def isatty(self) -> bool:
        return self._original.isatty()

    def __getattr__(self, nome):
        return getattr(self._original, nome)


class Progresso:
    def __init__(self, total: int, *, terminal=None) -> None:
        self.terminal = terminal or sys.__stdout__ or sys.stdout
        self.total = max(0, int(total))
        self.feito = 0
        self.base = 0  # itens dos CSVs já terminados
        self.csv = ""
        self.titulo = ""
        self.etapa = ""
        self.inicio = time.monotonic()
        self.trava = threading.RLock()
        self.visivel = False
        self.inicio_de_linha = True
        self._ultimo_desenho = 0.0
        self._streams: tuple = ()
        codificacao = getattr(self.terminal, "encoding", None) or "ascii"
        try:
            "█░·…".encode(codificacao)
            self.cheio, self.vazio, self.ponto = "█", "░", "·"
        except (UnicodeEncodeError, LookupError):
            self.cheio, self.vazio, self.ponto = "#", "-", "|"

    # -- ciclo de vida -------------------------------------------------
    def ligar(self) -> None:
        self._streams = (sys.stdout, sys.stderr)
        sys.stdout = _Saida(sys.stdout, self)
        sys.stderr = _Saida(sys.stderr, self)
        self._desenhar(forcar=True)

    def desligar(self) -> None:
        with self.trava:
            self._apagar()
            if self._streams:
                sys.stdout, sys.stderr = self._streams
                self._streams = ()
            decorrido = _duracao(time.monotonic() - self.inicio)
            print(f"Progresso: {min(self.feito, self.total)}/{self.total} itens "
                  f"({self._porcentagem():.0f}%) em {decorrido}.")

    # -- atualizações ----------------------------------------------------
    def iniciar_csv(self, nome: str) -> None:
        with self.trava:
            self.csv, self.titulo, self.etapa = nome, "", ""
            self.feito = self.base
            self._desenhar(forcar=True)

    def linha(self, indice: int, titulo: str = "") -> None:
        """Começou a linha `indice` (0 = primeira) do CSV atual: as anteriores estão feitas."""
        with self.trava:
            self.feito = min(self.base + indice, self.total)
            self.titulo, self.etapa = titulo, ""
            self._desenhar()  # limitado a 10x/s: retomar milhares de linhas não pisca

    def fim_csv(self, linhas: int) -> None:
        with self.trava:
            self.base = min(self.base + max(0, linhas), self.total)
            self.feito = self.base
            self.titulo, self.etapa = "", ""
            self._desenhar(forcar=True)

    def definir_etapa(self, texto: str) -> None:
        with self.trava:
            self.etapa = texto
            self._desenhar()

    def definir_titulo(self, texto: str) -> None:
        with self.trava:
            self.titulo = texto
            self._desenhar()

    # -- desenho ---------------------------------------------------------
    def _porcentagem(self) -> float:
        return 100.0 if not self.total else 100.0 * min(self.feito, self.total) / self.total

    def texto(self, largura: int) -> str:
        porcentagem = self._porcentagem()
        tamanho = 20 if largura >= 70 else 10
        cheios = int(tamanho * porcentagem / 100)
        partes = [f"[{self.cheio * cheios}{self.vazio * (tamanho - cheios)}] {porcentagem:3.0f}% "
                  f"{min(self.feito, self.total)}/{self.total}"]
        decorrido = time.monotonic() - self.inicio
        if 0 < self.feito < self.total and decorrido > 5:
            partes.append("~" + _duracao(decorrido / self.feito * (self.total - self.feito)))
        atual = ": ".join(p for p in (self.csv, self.titulo) if p)
        if atual:
            partes.append(atual)
        if self.etapa:
            partes.append(self.etapa)
        linha = f" {self.ponto} ".join(partes)
        limite = max(10, largura - 1)  # sem quebrar linha
        return linha if len(linha) <= limite else linha[: limite - 1] + ("…" if self.ponto == "·" else ".")

    def _largura(self) -> int:
        return shutil.get_terminal_size(fallback=(80, 24)).columns

    def _apagar(self) -> None:
        if self.visivel:
            self.terminal.write("\r" + " " * (self._largura() - 1) + "\r")
            self.visivel = False

    def _desenhar(self, forcar: bool = False) -> None:
        agora = time.monotonic()
        if not self.inicio_de_linha or (not forcar and agora - self._ultimo_desenho < 0.1):
            return
        self._ultimo_desenho = agora
        largura = self._largura()
        self.terminal.write("\r" + " " * (largura - 1) + "\r" + self.texto(largura))
        self.terminal.flush()
        self.visivel = True


def _duracao(segundos: float) -> str:
    segundos = int(max(0, segundos))
    if segundos >= 3600:
        return f"{segundos // 3600}h{segundos % 3600 // 60:02d}m"
    if segundos >= 60:
        return f"{segundos // 60}m{segundos % 60:02d}s"
    return f"{segundos}s"


# -- atalhos usados pelo downloader (não fazem nada se a barra estiver desligada) --
def iniciar(total: int, terminal=None) -> "Progresso | None":
    global ATUAL
    terminal = terminal or sys.__stdout__ or sys.stdout
    if not getattr(terminal, "isatty", lambda: False)():
        print("Aviso: --progress precisa de um terminal; barra desligada (saída redirecionada).")
        return None
    ATUAL = Progresso(total, terminal=terminal)
    ATUAL.ligar()
    return ATUAL


def encerrar() -> None:
    global ATUAL
    if ATUAL is not None:
        ATUAL.desligar()
        ATUAL = None


def etapa(texto: str) -> None:
    if ATUAL is not None:
        ATUAL.definir_etapa(texto)


def titulo(texto: str) -> None:
    if ATUAL is not None:
        ATUAL.definir_titulo(texto)


def iniciar_csv(nome: str) -> None:
    if ATUAL is not None:
        ATUAL.iniciar_csv(nome)


def linha(indice: int, titulo_linha: str = "") -> None:
    if ATUAL is not None:
        ATUAL.linha(indice, titulo_linha)


def fim_csv(linhas: int) -> None:
    if ATUAL is not None:
        ATUAL.fim_csv(linhas)
