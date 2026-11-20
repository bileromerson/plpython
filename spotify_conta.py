"""Lê as playlists da sua conta Spotify e salva as músicas num CSV.

Não baixa nada. O login é feito no navegador (OAuth com PKCE): basta o Client
ID de um app criado em https://developer.spotify.com/dashboard, com o endereço
de retorno http://127.0.0.1:8888/callback. O login fica salvo em
~/.cache/pltube/spotify_login.json e é renovado sozinho.

Regras do Spotify desde fevereiro de 2026 para apps em modo de
desenvolvimento: o dono do app precisa ter Premium, e as faixas só são
entregues para playlists que a conta criou ou em que colabora. As que você só
segue ficam de fora (o Spotify devolve só o nome delas).

Para economizar consultas, uma playlist cujo snapshot_id não mudou desde a
leitura anterior é copiada do CSV antigo, sem pedir as faixas de novo.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import html
import http.server
import itertools
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from textos import _normalize_text
from verbose import colorir, v, vv

API = "https://api.spotify.com/v1"
URL_LOGIN = "https://accounts.spotify.com/authorize"
URL_TOKEN = "https://accounts.spotify.com/api/token"
ESCOPOS = "playlist-read-private playlist-read-collaborative user-library-read"
REDIRECT_PADRAO = "http://127.0.0.1:8888/callback"
ARQUIVO_LOGIN = Path.home() / ".cache" / "pltube" / "spotify_login.json"
ARQUIVO_PADRAO = "spotify.csv"
COLUNAS = ("playlist", "playlist_id", "snapshot_id", "position", "track_id", "isrc",
           "artists", "title", "album", "duration_ms", "added_at", "url")
CURTIDAS_ID, CURTIDAS_NOME = "liked", "Músicas Curtidas"
ESPERAS = (2.0, 5.0, 15.0)   # novas tentativas em falha de rede ou erro 5xx
MAIOR_PAUSA = 120            # se o Spotify pedir pausa maior (429), para e avisa
PRAZO_LOGIN = 300            # segundos esperando o navegador voltar do login
MOSTRAR = 15                 # mudanças listadas por playlist (com -v: todas)

COMO_CRIAR_APP = """\
Falta o Client ID do seu app no Spotify. Para criar (uma vez só):
  1. Entre em https://developer.spotify.com/dashboard com a sua conta
     (o dono do app precisa ter Spotify Premium).
  2. Clique em "Create app". Em "Redirect URIs" coloque
       http://127.0.0.1:8888/callback
     e marque "Web API".
  3. Copie o "Client ID" (o Client Secret não é usado) e rode:
       python plpython.py spotify --client-id SEU_CLIENT_ID
O Client ID fica salvo; nas próximas vezes basta "python plpython.py spotify"."""

DICA_403 = (
    "No modo de desenvolvimento do Spotify, o dono do app precisa ter Premium, e a conta "
    "que entrou precisa ser a dona do app ou estar cadastrada em User Management "
    "(developer.spotify.com → seu app → Settings)."
)


class ErroSpotify(RuntimeError):
    def __init__(self, mensagem: str, status: int = 0):
        super().__init__(mensagem)
        self.status = status


# --- HTTP -------------------------------------------------------------------

@dataclass
class Resposta:
    status: int
    cabecalhos: Mapping[str, str]
    corpo: bytes

    def json(self) -> dict:
        try:
            dados = json.loads(self.corpo.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            return {}
        return dados if isinstance(dados, dict) else {}


def requisitar(metodo: str, url: str, dados: Mapping[str, str] | None = None,
               cabecalhos: Mapping[str, str] | None = None) -> Resposta:
    """Uma requisição com a biblioteca padrão; erro HTTP também vira Resposta."""
    corpo = urllib.parse.urlencode(dados).encode() if dados is not None else None
    pedido = urllib.request.Request(url, data=corpo, method=metodo, headers=dict(cabecalhos or {}))
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resposta:
            return Resposta(resposta.status, resposta.headers, resposta.read())
    except urllib.error.HTTPError as exc:
        try:
            conteudo = exc.read() or b""
        except OSError:
            conteudo = b""
        return Resposta(exc.code, exc.headers or {}, conteudo)


def _mensagem(resposta: Resposta) -> str:
    dados = resposta.json()
    erro = dados.get("error")
    if isinstance(erro, dict):
        return str(erro.get("message") or erro.get("reason") or "")
    return str(dados.get("error_description") or erro or "")


def _segundos(valor: Any, padrao: float) -> float:
    try:
        return max(0.0, float(valor))
    except (TypeError, ValueError):
        return padrao


# --- Login ------------------------------------------------------------------

def _ler_json(caminho: Path) -> dict:
    try:
        with open(caminho, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _gravar_privado(caminho: Path, dados: dict) -> None:
    """O token dá acesso à conta: o arquivo fica legível só para o seu usuário."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(caminho.name + ".tmp")
    fd = os.open(temporario, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=1)
    os.replace(temporario, caminho)


def validar_redirect(uri: str) -> None:
    partes = urllib.parse.urlsplit(uri)
    host = (partes.hostname or "").lower()
    if host == "localhost":
        raise ErroSpotify("O Spotify não aceita mais 'localhost' como endereço de retorno. "
                          f"Use {REDIRECT_PADRAO} no app (developer.spotify.com) e aqui.")
    if partes.scheme != "http" or host not in ("127.0.0.1", "::1"):
        raise ErroSpotify("Para o login funcionar aqui, o endereço de retorno precisa ser local, "
                          f"por exemplo {REDIRECT_PADRAO} (cadastre o mesmo no app do Spotify).")
    try:
        porta = partes.port
    except ValueError:
        porta = None
    if not porta:
        raise ErroSpotify(f"Inclua a porta no endereço de retorno, por exemplo {REDIRECT_PADRAO}.")


def configurar(client_id: str | None = None, redirect_uri: str | None = None,
               arquivo: Path | None = None) -> tuple[str, str]:
    """Client ID e endereço de retorno: opção > variável de ambiente > login salvo."""
    salvo = _ler_json(Path(arquivo or ARQUIVO_LOGIN))
    client_id = (client_id or os.environ.get("SPOTIFY_CLIENT_ID") or salvo.get("client_id") or "").strip()
    redirect = (redirect_uri or os.environ.get("SPOTIFY_REDIRECT_URI")
                or salvo.get("redirect_uri") or REDIRECT_PADRAO).strip()
    if not client_id:
        raise ErroSpotify(COMO_CRIAR_APP)
    validar_redirect(redirect)
    return client_id, redirect


def pkce() -> tuple[str, str]:
    """Par verificador/desafio do PKCE (RFC 7636, método S256)."""
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest())
    return verificador, desafio.rstrip(b"=").decode()


_PAGINA = ("<!doctype html><meta charset=utf-8><title>pltube</title>"
           "<p style='font:16px sans-serif;margin:3em'>{}</p>")


def receber_codigo(redirect_uri: str, url_login: str, estado: str, *,
                   abrir: Callable[[str], Any] = webbrowser.open, prazo: float = PRAZO_LOGIN) -> str:
    """Abre o login no navegador e espera o Spotify voltar para 127.0.0.1."""
    partes = urllib.parse.urlsplit(redirect_uri)
    caminho = (partes.path or "/").rstrip("/")
    resultado: dict[str, str] = {}

    class Retorno(http.server.BaseHTTPRequestHandler):
        timeout = 10

        def do_GET(self) -> None:  # noqa: N802 - nome exigido pelo http.server
            pedido = urllib.parse.urlsplit(self.path)
            if pedido.path.rstrip("/") != caminho:
                self.send_error(404)
                return
            consulta = urllib.parse.parse_qs(pedido.query)
            if consulta.get("state", [""])[0] != estado:
                resultado["erro"] = "resposta inesperada do navegador; tente de novo"
            elif "error" in consulta:
                resultado["erro"] = consulta["error"][0]
            elif consulta.get("code"):
                resultado["codigo"] = consulta["code"][0]
            else:
                self.send_error(400)
                return
            texto = ("Pronto! Pode fechar esta aba e voltar ao terminal." if "codigo" in resultado
                     else "O login não foi concluído: " + html.escape(resultado["erro"]))
            corpo = _PAGINA.format(texto).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

        def log_message(self, *args: Any) -> None:
            pass

    class Servidor(http.server.HTTPServer):
        address_family = socket.AF_INET6 if ":" in (partes.hostname or "") else socket.AF_INET

    try:
        servidor = Servidor((partes.hostname, partes.port), Retorno)
    except OSError as exc:
        raise ErroSpotify(
            f"Não consegui usar {partes.hostname}:{partes.port} para receber o login ({exc}). "
            "Feche o programa que está usando essa porta, ou cadastre outra no app do Spotify "
            "e passe --redirect-uri."
        ) from exc
    servidor.timeout = 1
    print("Abrindo o navegador para você entrar no Spotify e autorizar a leitura das playlists.")
    print(f"Se ele não abrir, copie este endereço no navegador:\n  {url_login}")
    print(f"Se aparecer \"INVALID_CLIENT: Invalid redirect URI\", cadastre {redirect_uri} "
          "em Redirect URIs, no seu app em developer.spotify.com.")
    try:
        abrir(url_login)
    except Exception:  # noqa: BLE001 - sem navegador, o endereço já foi mostrado
        pass
    limite = time.monotonic() + prazo
    try:
        while not resultado and time.monotonic() < limite:
            servidor.handle_request()
    finally:
        servidor.server_close()
    if "codigo" in resultado:
        return resultado["codigo"]
    if resultado.get("erro") == "access_denied":
        raise ErroSpotify("Login cancelado no navegador.")
    if resultado:
        raise ErroSpotify(f"O login não foi concluído: {resultado['erro']}")
    raise ErroSpotify(f"O login não terminou em {max(1, round(prazo / 60))} minutos.")


class Login:
    """Guarda e renova o token; abre o navegador só quando precisa."""

    def __init__(self, client_id: str, redirect_uri: str, *, novo: bool = False,
                 arquivo: Path | None = None, http: Callable[..., Resposta] | None = None,
                 receber: Callable[..., str] | None = None):
        self.client_id, self.redirect_uri, self.novo = client_id, redirect_uri, novo
        self.arquivo = Path(arquivo or ARQUIVO_LOGIN)
        self._http = http or requisitar
        self._receber = receber or receber_codigo
        salvo = {} if novo else _ler_json(self.arquivo)
        self.dados = salvo if salvo.get("client_id") == client_id else {}

    def token(self) -> str:
        if not self.dados.get("access_token"):
            self.entrar()
        elif time.time() >= float(self.dados.get("expires_at") or 0):
            self.renovar()
        return str(self.dados["access_token"])

    def renovar(self) -> None:
        refresh = str(self.dados.get("refresh_token") or "")
        if refresh:
            try:
                self._guardar(self._pedir_token({
                    "grant_type": "refresh_token", "refresh_token": refresh, "client_id": self.client_id,
                }), refresh)
                vv("Spotify", "login renovado")
                return
            except ErroSpotify as exc:
                v("Spotify", f"não deu para renovar o login ({exc}); entrando de novo")
        self.entrar()

    def entrar(self) -> None:
        verificador, desafio = pkce()
        estado = secrets.token_urlsafe(16)
        parametros = {
            "client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect_uri,
            "code_challenge_method": "S256", "code_challenge": desafio, "scope": ESCOPOS, "state": estado,
        }
        if self.novo:
            parametros["show_dialog"] = "true"  # deixa trocar de conta
        codigo = self._receber(self.redirect_uri, f"{URL_LOGIN}?{urllib.parse.urlencode(parametros)}", estado)
        self._guardar(self._pedir_token({
            "grant_type": "authorization_code", "code": codigo, "redirect_uri": self.redirect_uri,
            "client_id": self.client_id, "code_verifier": verificador,
        }), "")
        print("Login no Spotify feito e salvo.")

    def _pedir_token(self, dados: dict[str, str]) -> dict:
        try:
            resposta = self._http("POST", URL_TOKEN, dados=dados)
        except OSError as exc:  # URLError, tempo esgotado, conexão recusada
            raise ErroSpotify(f"sem conexão com o Spotify: {exc}") from exc
        corpo = resposta.json()
        if resposta.status != 200 or not corpo.get("access_token"):
            motivo = _mensagem(resposta) or f"HTTP {resposta.status}"
            raise ErroSpotify(f"o Spotify recusou o login: {motivo}", resposta.status)
        return corpo

    def _guardar(self, corpo: dict, refresh_anterior: str) -> None:
        self.dados = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "access_token": corpo["access_token"],
            "refresh_token": corpo.get("refresh_token") or refresh_anterior,
            "expires_at": time.time() + int(corpo.get("expires_in") or 3600) - 60,
            "scope": corpo.get("scope", ""),
        }
        _gravar_privado(self.arquivo, self.dados)


# --- Web API ----------------------------------------------------------------

class ClienteSpotify:
    def __init__(self, login: Login, *, http: Callable[..., Resposta] | None = None,
                 dormir: Callable[[float], Any] | None = None):
        self.login = login
        self._http = http or requisitar
        self._dormir = dormir or time.sleep
        self.requisicoes = 0

    def get(self, caminho: str, parametros: Mapping[str, Any] | None = None) -> dict:
        url = caminho if caminho.startswith("http") else API + caminho
        if parametros:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(parametros)
        renovou, tentativa = False, 0
        while True:
            self.requisicoes += 1
            try:
                resposta = self._http("GET", url, cabecalhos={"Authorization": f"Bearer {self.login.token()}"})
            except OSError as exc:  # sem rede, DNS, tempo esgotado
                if tentativa >= len(ESPERAS):
                    raise ErroSpotify(f"sem conexão com o Spotify: {exc}") from exc
                self._pausa(ESPERAS[tentativa], f"falha de rede ({exc})")
                tentativa += 1
                continue
            vv("Spotify", f"GET {url.replace(API, '')} → {resposta.status}")
            if resposta.status == 401 and not renovou:
                renovou = True
                self.login.renovar()
                continue
            if resposta.status == 429:
                espera = _segundos(resposta.cabecalhos.get("Retry-After"), 5.0)
                if espera > MAIOR_PAUSA or tentativa >= 5:
                    raise ErroSpotify("o Spotify limitou as consultas desta conta; tente de novo em "
                                      f"{max(1, round(espera / 60))} min", 429)
                print(f"O Spotify pediu uma pausa; aguardando {espera:g} s.")
                self._dormir(espera)
                tentativa += 1
                continue
            if resposta.status >= 500 and tentativa < len(ESPERAS):
                self._pausa(ESPERAS[tentativa], f"erro {resposta.status} no Spotify")
                tentativa += 1
                continue
            if resposta.status >= 400:
                detalhe = _mensagem(resposta)
                raise ErroSpotify(f"o Spotify respondeu {resposta.status}" + (f": {detalhe}" if detalhe else ""),
                                  resposta.status)
            return resposta.json()

    def _pausa(self, segundos: float, motivo: str) -> None:
        v("Spotify", f"{motivo}; nova tentativa em {segundos:g} s")
        self._dormir(segundos)

    def paginas(self, caminho: str, parametros: Mapping[str, Any] | None = None) -> Iterator[dict]:
        url: str | None = caminho
        while url:
            pagina = self.get(url, parametros)
            yield pagina
            url, parametros = pagina.get("next"), None  # o "next" já traz offset e limit


# --- Playlists --------------------------------------------------------------

def extrair_playlist_id(valor: str) -> str:
    """Aceita ID, URI spotify:playlist:... ou URL open.spotify.com/playlist/..."""
    valor = valor.strip()
    uri = re.fullmatch(r"spotify:playlist:([A-Za-z0-9]+)", valor)
    if uri:
        return uri.group(1)
    if "://" in valor:
        caminho = [parte for parte in urllib.parse.urlparse(valor).path.split("/") if parte]
        for indice, parte in enumerate(caminho[:-1]):
            if parte == "playlist":
                return caminho[indice + 1]
        raise ValueError("A URL não parece ser de uma playlist Spotify.")
    if re.fullmatch(r"[A-Za-z0-9]{22}", valor):
        return valor
    raise ValueError("Informe um ID Spotify válido, uma URI spotify:playlist:... ou uma URL de playlist.")


def chave_nome(nome: Any) -> str:
    """Nome comparável: sem acento, caixa, pontuação ou emoji ("Rock 🎸" = "rock")."""
    return _normalize_text(nome) or str(nome or "").strip().casefold()


@dataclass
class PlaylistLida:
    id: str
    nome: str
    snapshot: str = ""
    faixas: list[dict] = field(default_factory=list)
    motivo: str = ""             # preenchido quando o Spotify não entregou as faixas
    reaproveitada: bool = False  # snapshot igual ao da leitura anterior
    anterior: bool = False       # já existia na leitura anterior
    novas: list[dict] = field(default_factory=list)
    saidas: list[dict] = field(default_factory=list)


def _linha(entrada: Mapping, playlist: PlaylistLida, posicao: int) -> dict | None:
    faixa = entrada.get("item") or entrada.get("track")  # "track" virou "item" em 2026
    if not isinstance(faixa, dict) or faixa.get("type", "track") != "track":
        return None  # removida do Spotify, indisponível ou episódio de podcast
    titulo = str(faixa.get("name") or "").strip()
    if not titulo:
        return None
    artistas = [str(a["name"]).strip() for a in faixa.get("artists") or []
                if isinstance(a, dict) and a.get("name")]
    return {
        "playlist": playlist.nome,
        "playlist_id": playlist.id,
        "snapshot_id": playlist.snapshot,
        "position": posicao,
        "track_id": str(faixa.get("id") or ""),  # vazio nos arquivos locais do Spotify
        "isrc": str((faixa.get("external_ids") or {}).get("isrc") or "").upper(),
        "artists": "; ".join(artistas),
        "title": titulo,
        "album": str((faixa.get("album") or {}).get("name") or ""),
        "duration_ms": int(faixa.get("duration_ms") or 0),
        "added_at": str(entrada.get("added_at") or ""),
        "url": str((faixa.get("external_urls") or {}).get("spotify") or ""),
    }


def _ler_paginas(paginas: Iterator[dict], lida: PlaylistLida, primeira: dict | None = None) -> None:
    posicao = 0
    for pagina in itertools.chain([primeira] if primeira else [], paginas):
        for entrada in pagina.get("items") or []:
            posicao += 1
            linha = _linha(entrada, lida, posicao) if isinstance(entrada, dict) else None
            if linha:
                lida.faixas.append(linha)


def _reaproveitar(lida: PlaylistLida, velha: PlaylistLida | None) -> bool:
    if not (velha and lida.snapshot and velha.snapshot == lida.snapshot):
        return False
    lida.faixas = [dict(f, playlist=lida.nome) for f in velha.faixas]
    lida.reaproveitada = True
    v("Spotify", f"{lida.nome}: igual à última leitura (snapshot_id), sem pedir as faixas de novo")
    return True


def ler_playlist(cliente: ClienteSpotify, dados: Mapping, anterior: Mapping[str, PlaylistLida]) -> PlaylistLida:
    lida = PlaylistLida(str(dados["id"]), str(dados.get("name") or "Playlist sem nome"),
                        str(dados.get("snapshot_id") or ""))
    if _reaproveitar(lida, anterior.get(lida.id)):
        return lida
    try:
        _ler_paginas(cliente.paginas(f"/playlists/{lida.id}/items",
                                     {"limit": 50, "additional_types": "track"}), lida)
    except ErroSpotify as exc:
        if exc.status not in (403, 404):
            raise
        lida.faixas, lida.motivo = [], "o Spotify não liberou as faixas"
    return lida


def ler_curtidas(cliente: ClienteSpotify, anterior: Mapping[str, PlaylistLida]) -> PlaylistLida:
    """Músicas Curtidas. Sem snapshot_id: usa total + a curtida mais recente, que
    muda sempre que algo é curtido (entra no topo) ou descurtido (total cai)."""
    lida = PlaylistLida(CURTIDAS_ID, CURTIDAS_NOME)
    paginas = cliente.paginas("/me/tracks", {"limit": 50})
    primeira = next(paginas, {})
    itens = primeira.get("items") or []
    topo = itens[0] if itens and isinstance(itens[0], dict) else {}
    faixa = topo.get("item") or topo.get("track") or {}
    lida.snapshot = f"{primeira.get('total', 0)}:{faixa.get('id', '')}:{topo.get('added_at', '')}"
    if not _reaproveitar(lida, anterior.get(lida.id)):
        _ler_paginas(paginas, lida, primeira)
    return lida


def ler_csv(caminho: str) -> dict[str, PlaylistLida]:
    """O spotify.csv anterior, por playlist, na ordem em que aparece."""
    playlists: dict[str, PlaylistLida] = {}
    try:
        with open(caminho, encoding="utf-8-sig", newline="") as arquivo:
            for linha in csv.DictReader(arquivo):
                pid = (linha.get("playlist_id") or "").strip()
                if not pid:
                    continue
                if pid not in playlists:
                    playlists[pid] = PlaylistLida(pid, linha.get("playlist") or "", linha.get("snapshot_id") or "")
                playlists[pid].faixas.append({coluna: linha.get(coluna) or "" for coluna in COLUNAS})
    except FileNotFoundError:
        return {}
    return playlists


def gravar_csv(caminho: str, playlists: list[PlaylistLida]) -> int:
    pasta = os.path.dirname(os.path.abspath(caminho))
    os.makedirs(pasta, exist_ok=True)
    fd, temporario = tempfile.mkstemp(prefix=".spotify-", suffix=".csv", dir=pasta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS, extrasaction="ignore")
            escritor.writeheader()
            for playlist in playlists:
                escritor.writerows(playlist.faixas)
        os.replace(temporario, caminho)
    except BaseException:
        try:
            os.unlink(temporario)
        except OSError:
            pass
        raise
    return sum(len(p.faixas) for p in playlists)


def _chave_faixa(faixa: Mapping) -> str:
    return str(faixa.get("track_id") or "") or f"{faixa.get('title', '')}|{faixa.get('artists', '')}".casefold()


def _separar(faixas: list[dict], contagem: Counter) -> list[dict]:
    restante, escolhidas = Counter(contagem), []
    for faixa in faixas:
        chave = _chave_faixa(faixa)
        if restante[chave] > 0:
            escolhidas.append(faixa)
            restante[chave] -= 1
    return escolhidas


def comparar(nova: PlaylistLida, velha: PlaylistLida | None) -> None:
    """O que entrou e o que saiu desde a leitura anterior."""
    if velha is None:
        return
    nova.anterior = True
    if nova.reaproveitada:
        return
    antes = Counter(_chave_faixa(f) for f in velha.faixas)
    depois = Counter(_chave_faixa(f) for f in nova.faixas)
    nova.novas = _separar(nova.faixas, depois - antes)
    nova.saidas = _separar(velha.faixas, antes - depois)


def rotulo(faixa: Mapping) -> str:
    artistas = str(faixa.get("artists") or "").replace("; ", ", ")
    return f"{artistas} — {faixa.get('title')}" if artistas else str(faixa.get("title"))


def numero(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def qtd(n: int, singular: str, plural: str) -> str:
    """qtd(1, "música", "músicas") -> "1 música"; qtd(1200, ...) -> "1.200 músicas"."""
    return f"{numero(n)} {singular if n == 1 else plural}"


def caminho_curto(caminho: str) -> str:
    try:
        relativo = os.path.relpath(caminho)
    except ValueError:  # outro disco no Windows
        return caminho
    return caminho if relativo.startswith("..") else relativo


def mostrar(lida: PlaylistLida, *, detalhado: bool, listar: bool, havia_csv: bool) -> None:
    total = qtd(len(lida.faixas), "música", "músicas")
    if lida.novas or lida.saidas:
        mudancas = " ".join(filter(None, (
            colorir(f"+{len(lida.novas)}", "32") if lida.novas else "",
            colorir(f"−{len(lida.saidas)}", "31") if lida.saidas else "",
        )))
        print(f"{lida.nome} ({total}): {mudancas} desde a última leitura")
        for marca, cor, faixas in (("+", "32", lida.novas), ("−", "31", lida.saidas)):
            for faixa in faixas if detalhado else faixas[:MOSTRAR]:
                print(f"    {colorir(marca, cor)} {rotulo(faixa)}")
            if not detalhado and len(faixas) > MOSTRAR:
                print(f"    … e mais {len(faixas) - MOSTRAR} (use -v para ver todas)")
    elif lida.anterior:
        print(f"{lida.nome} ({total}): sem mudanças")
    else:
        print(f"{lida.nome} ({total})" + (" — nova" if havia_csv else ""))
    if listar:
        for faixa in lida.faixas:
            print(f"    {int(faixa['position']):03}. {rotulo(faixa)}")


def _de_outra_pessoa(dados: Mapping, meu_id: str) -> bool:
    dono = str((dados.get("owner") or {}).get("id") or "")
    return bool(meu_id and dono and dono != meu_id and not dados.get("collaborative"))


def escolher(playlists: list[dict], pedidos: list[str],
             cliente: ClienteSpotify) -> tuple[list[dict], set[str], list[str]]:
    """Filtra por nome, URL ou ID. Devolve (escolhidas, pedidas pelo ID/nome, não achadas)."""
    if not pedidos:
        return playlists, set(), []
    escolhidas: list[dict] = []
    faltando: list[str] = []
    por_id = {str(p.get("id")): p for p in playlists}
    for pedido in pedidos:
        alvo = chave_nome(pedido)
        achadas = [p for p in playlists if chave_nome(p.get("name")) == alvo]
        if not achadas:
            try:
                pid = extrair_playlist_id(pedido)
            except ValueError:
                pid = ""
            if pid in por_id:
                achadas = [por_id[pid]]
            elif pid:
                try:
                    achadas = [cliente.get(f"/playlists/{pid}",
                                           {"fields": "id,name,snapshot_id,owner(id),collaborative"})]
                except ErroSpotify as exc:
                    if exc.status not in (400, 403, 404):
                        raise
        if not achadas:
            faltando.append(pedido)
        for playlist in achadas:
            if all(playlist.get("id") != e.get("id") for e in escolhidas):
                escolhidas.append(playlist)
    return escolhidas, {str(p.get("id")) for p in escolhidas}, faltando


def executar(args: Any) -> int:
    saida = os.path.abspath(args.output)
    detalhado = (getattr(args, "verbose", 0) or 0) >= 1
    pedidos = list(getattr(args, "playlists", None) or [])
    try:
        client_id, redirect = configurar(args.client_id, args.redirect_uri)
        cliente = ClienteSpotify(Login(client_id, redirect, novo=args.login))
        anterior = ler_csv(saida)
        try:
            eu = cliente.get("/me")
        except ErroSpotify as exc:
            if exc.status == 403:
                raise ErroSpotify(f"{exc}\n{DICA_403}", 403) from exc
            raise
        print(f"Conta Spotify: {eu.get('display_name') or eu.get('id')}")
        playlists = [p for pagina in cliente.paginas("/me/playlists", {"limit": 50})
                     for p in pagina.get("items") or [] if isinstance(p, dict) and p.get("id")]
        escolhidas, pedidas, faltando = escolher(playlists, pedidos, cliente)
        for pedido in faltando:
            print(f"Playlist não encontrada na sua conta: {pedido}")
        if pedidos and not escolhidas and not args.liked:
            return 1

        lidas: list[PlaylistLida] = []
        de_outros: list[PlaylistLida] = []
        for dados in escolhidas:
            if str(dados["id"]) not in pedidas and _de_outra_pessoa(dados, str(eu.get("id") or "")):
                de_outros.append(PlaylistLida(str(dados["id"]), str(dados.get("name") or "")))
                continue
            lida = ler_playlist(cliente, dados, anterior)
            if lida.motivo:
                print(f"{lida.nome}: sem acesso — {lida.motivo} (playlist de outra pessoa?)")
            else:
                comparar(lida, anterior.get(lida.id))
                mostrar(lida, detalhado=detalhado, listar=bool(pedidos), havia_csv=bool(anterior))
            lidas.append(lida)
        if args.liked:
            lida = ler_curtidas(cliente, anterior)
            comparar(lida, anterior.get(lida.id))
            mostrar(lida, detalhado=detalhado, listar=False, havia_csv=bool(anterior))
            lidas.append(lida)
    except ErroSpotify as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelado; o spotify.csv anterior ficou como estava.", file=sys.stderr)
        return 130

    if pedidos:
        # Só algumas playlists: as outras continuam como estavam no CSV.
        novas = {lida.id: lida for lida in lidas}
        resultado = [novas.pop(pid, velha) for pid, velha in anterior.items()] + list(novas.values())
    else:
        resultado = lidas
        vistas = {lida.id for lida in lidas} | {outra.id for outra in de_outros} | {CURTIDAS_ID}
        sumiram = [velha.nome for pid, velha in anterior.items() if pid not in vistas]
        if sumiram:
            print(f"Não estão mais na sua conta: {', '.join(sumiram)}")
    resultado = [p for p in resultado if not p.motivo]
    total = gravar_csv(saida, resultado)

    print(f"\nSalvo em {caminho_curto(saida)}: {qtd(len(resultado), 'playlist', 'playlists')}, "
          f"{qtd(total, 'música', 'músicas')}.")
    if de_outros:
        nomes = [p.nome for p in de_outros]
        lista = ", ".join(nomes if detalhado else nomes[:5]) + ("" if detalhado or len(nomes) <= 5 else ", …")
        quantas = ("1 playlist que você só segue ficou" if len(de_outros) == 1
                   else f"{len(de_outros)} playlists que você só segue ficaram")
        print(f"{quantas} de fora ({lista}): o Spotify só entrega as faixas das suas playlists. "
              "Para incluir uma, copie as músicas para uma playlist sua.")
    print("Próximo passo: python plpython.py sync   (mostra o que muda nas M3Us e o que falta baixar)")
    v("Spotify", f"{cliente.requisicoes} consultas à API")
    return 0
