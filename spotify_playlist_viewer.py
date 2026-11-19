"""Consulta uma playlist Spotify e exibe suas faixas, sem baixar nem exportar conteúdo."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


API_BASE = "https://api.spotify.com/v1"
SCOPES = "playlist-read-private playlist-read-collaborative"


def extrair_playlist_id(valor: str) -> str:
    """Aceita ID, URI spotify:playlist:... ou URL open.spotify.com/playlist/..."""
    valor = valor.strip()
    uri = re.fullmatch(r"spotify:playlist:([A-Za-z0-9]+)", valor)
    if uri:
        return uri.group(1)

    if "://" in valor:
        caminho = [parte for parte in urlparse(valor).path.split("/") if parte]
        for indice, parte in enumerate(caminho[:-1]):
            if parte == "playlist":
                return caminho[indice + 1]
        raise ValueError("A URL não parece ser de uma playlist Spotify.")

    if re.fullmatch(r"[A-Za-z0-9]{22}", valor):
        return valor
    raise ValueError("Informe um ID Spotify válido, uma URI spotify:playlist:... ou uma URL de playlist.")


def obter_token():
    """Inicia OAuth de usuário; o cache do token fica fora do repositório."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI")
    faltando = [
        nome for nome, valor in (
            ("SPOTIFY_CLIENT_ID", client_id),
            ("SPOTIFY_CLIENT_SECRET", client_secret),
            ("SPOTIFY_REDIRECT_URI", redirect_uri),
        ) if not valor
    ]
    if faltando:
        raise RuntimeError(
            "Configure estas variáveis de ambiente: " + ", ".join(faltando)
        )

    try:
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError as exc:
        raise RuntimeError("Instale as dependências com: pip install -r requirements.txt") from exc

    cache_path = Path.home() / ".cache" / "pltube" / "spotify_token.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    oauth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=str(cache_path),
        open_browser=True,
    )
    return oauth.get_access_token(as_dict=True), oauth


def requisitar_json(session, url: str, token_info: dict, oauth, params=None) -> dict:
    """Faz GET na Web API e respeita Retry-After quando a Spotify responde 429."""
    import requests

    for tentativa in range(5):
        headers = {"Authorization": f"Bearer {token_info['access_token']}"}
        resposta = session.get(url, headers=headers, params=params, timeout=30)
        if resposta.status_code == 401 and token_info.get("refresh_token"):
            token_info = oauth.refresh_access_token(token_info["refresh_token"])
            continue
        if resposta.status_code == 429 and tentativa < 4:
            try:
                espera = float(resposta.headers.get("Retry-After", 2 ** tentativa))
            except (TypeError, ValueError):
                espera = float(2 ** tentativa)
            print(f"A Spotify limitou as consultas; aguardando {espera:g} segundos.")
            time.sleep(max(0, espera))
            continue
        if not resposta.ok:
            try:
                detalhe = resposta.json().get("error", {}).get("message", resposta.text)
            except (ValueError, AttributeError):
                detalhe = resposta.text
            raise RuntimeError(f"Spotify API respondeu HTTP {resposta.status_code}: {detalhe}")
        return resposta.json()

    raise RuntimeError("Não foi possível concluir a consulta após novas tentativas.")


def mostrar_playlist(playlist_id: str) -> None:
    """Exibe os metadados da playlist e as faixas, sem gravar uma cópia local."""
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Instale as dependências com: pip install -r requirements.txt") from exc

    token_info, oauth = obter_token()
    session = requests.Session()
    playlist = requisitar_json(
        session,
        f"{API_BASE}/playlists/{playlist_id}",
        token_info,
        oauth,
        params={"fields": "name,owner(display_name),external_urls(spotify)"},
    )

    nome = playlist.get("name") or "Playlist sem nome"
    proprietario = (playlist.get("owner") or {}).get("display_name") or "desconhecido"
    spotify_url = (playlist.get("external_urls") or {}).get("spotify") or ""
    print(f"Playlist: {nome}\nProprietário: {proprietario}")
    if spotify_url:
        print(f"Abrir no Spotify: {spotify_url}")
    print("\nFaixas (consulta somente; nenhum áudio ou arquivo de playlist é salvo):")

    url = f"{API_BASE}/playlists/{playlist_id}/items"
    params = {"limit": 50, "additional_types": "track"}
    numero = 0
    while url:
        pagina = requisitar_json(session, url, token_info, oauth, params=params)
        params = None  # A URL `next` já contém os parâmetros de paginação.
        for entrada in pagina.get("items", []):
            faixa = entrada.get("item") or entrada.get("track") or {}
            if not faixa or faixa.get("type") != "track":
                continue
            artistas = ", ".join(
                artista.get("name", "") for artista in faixa.get("artists", [])
                if artista.get("name")
            ) or "Artista desconhecido"
            titulo = faixa.get("name") or "Faixa indisponível"
            link = (faixa.get("external_urls") or {}).get("spotify", "")
            numero += 1
            print(f"{numero:03}. {artistas} — {titulo}")
            if link:
                print(f"     {link}")
        url = pagina.get("next")

    print(f"\nTotal de faixas exibidas: {numero}")


def executar(args: argparse.Namespace) -> int:
    """Consulta a playlist a partir de argumentos já interpretados."""
    try:
        playlist_id = extrair_playlist_id(args.playlist)
        mostrar_playlist(playlist_id)
    except (ValueError, RuntimeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nConsulta cancelada.", file=sys.stderr)
        return 130
    return 0
