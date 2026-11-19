import argparse
import base64
import contextlib
import csv
import hashlib
import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

import plpython
import spotify_conta as sc


def resposta(status=200, corpo=None, cabecalhos=None):
    return sc.Resposta(status, cabecalhos or {}, json.dumps(corpo or {}).encode())


def faixa(tid, titulo, artistas, isrc="", chave="item", **extra):
    dados = {
        "type": "track", "id": tid, "name": titulo, "duration_ms": 200000,
        "artists": [{"name": a} for a in artistas], "album": {"name": "Álbum " + titulo},
        "external_ids": {"isrc": isrc} if isrc else {},
        "external_urls": {"spotify": f"https://open.spotify.com/track/{tid}"} if tid else {},
    }
    dados.update(extra)
    return {"added_at": "2026-11-01T10:00:00Z", chave: dados}


class FakeSpotify:
    """Web API falsa no formato de 2026 (/items e campo "item")."""

    def __init__(self):
        self.eu = {"id": "eu", "display_name": "Eu Mesmo"}
        self.playlists = []   # objetos de /me/playlists
        self.itens = {}       # id -> entradas da playlist
        self.curtidas = []
        self.bloqueadas = set()
        self.pedidos = []
        self.limite = 2       # páginas pequenas para testar a paginação

    def playlist(self, pid, nome, entradas, dono="eu", snapshot="s1", colaborativa=False):
        self.playlists.append({"id": pid, "name": nome, "snapshot_id": snapshot,
                               "owner": {"id": dono}, "collaborative": colaborativa,
                               "items": {"total": len(entradas)}})
        self.itens[pid] = entradas

    def _pagina(self, base, lista, consulta):
        offset = int(consulta.get("offset", ["0"])[0])
        fim = offset + self.limite
        proxima = f"{sc.API}{base}?offset={fim}&limit={self.limite}" if fim < len(lista) else None
        return {"items": lista[offset:fim], "next": proxima, "total": len(lista), "offset": offset}

    def __call__(self, metodo, url, dados=None, cabecalhos=None):
        self.pedidos.append((metodo, url))
        partes = urllib.parse.urlsplit(url)
        consulta = urllib.parse.parse_qs(partes.query)
        caminho = partes.path.replace("/v1", "", 1)
        if caminho == "/me":
            return resposta(200, self.eu)
        if caminho == "/me/playlists":
            return resposta(200, self._pagina(caminho, self.playlists, consulta))
        if caminho == "/me/tracks":
            return resposta(200, self._pagina(caminho, self.curtidas, consulta))
        if caminho.startswith("/playlists/") and caminho.endswith("/items"):
            pid = caminho.split("/")[2]
            if pid in self.bloqueadas:
                return resposta(403, {"error": {"status": 403, "message": "Forbidden"}})
            return resposta(200, self._pagina(caminho, self.itens[pid], consulta))
        if caminho.startswith("/playlists/"):
            pid = caminho.split("/")[2]
            achada = [p for p in self.playlists if p["id"] == pid]
            return resposta(200, achada[0]) if achada else resposta(404, {"error": {"message": "nada"}})
        raise AssertionError(f"rota inesperada: {url}")

    def consultas(self, trecho):
        return [url for _, url in self.pedidos if trecho in url]


class LoginFixo:
    def token(self):
        return "TOKEN"

    def renovar(self):
        pass


class PkceERedirectTests(unittest.TestCase):
    def test_pkce_s256(self):
        verificador, desafio = sc.pkce()
        self.assertTrue(43 <= len(verificador) <= 128)
        esperado = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=")
        self.assertEqual(desafio, esperado.decode())

    def test_endereco_de_retorno(self):
        sc.validar_redirect("http://127.0.0.1:8888/callback")
        sc.validar_redirect("http://[::1]:9000/cb")
        with self.assertRaisesRegex(sc.ErroSpotify, "localhost"):
            sc.validar_redirect("http://localhost:8888/callback")
        with self.assertRaisesRegex(sc.ErroSpotify, "local"):
            sc.validar_redirect("https://meusite.com/callback")
        with self.assertRaisesRegex(sc.ErroSpotify, "porta"):
            sc.validar_redirect("http://127.0.0.1/callback")

    def test_configurar_prioridade_e_instrucoes(self):
        with tempfile.TemporaryDirectory() as temp:
            arquivo = Path(temp) / "login.json"
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(sc.ErroSpotify, "developer.spotify.com/dashboard"):
                    sc.configurar(arquivo=arquivo)
                arquivo.write_text(json.dumps({"client_id": "salvo"}))
                self.assertEqual(sc.configurar(arquivo=arquivo), ("salvo", sc.REDIRECT_PADRAO))
            with patch.dict(os.environ, {"SPOTIFY_CLIENT_ID": "env"}, clear=True):
                self.assertEqual(sc.configurar(arquivo=arquivo)[0], "env")
                self.assertEqual(sc.configurar("opcao", arquivo=arquivo)[0], "opcao")
            with patch.dict(os.environ, {"SPOTIFY_REDIRECT_URI": "http://localhost:8888/callback"}, clear=True):
                with self.assertRaisesRegex(sc.ErroSpotify, "127.0.0.1"):
                    sc.configurar(arquivo=arquivo)


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.arquivo = Path(self.temp.name) / "cache" / "login.json"
        self.posts = []
        self.recebidos = []
        self.respostas_token = []

    def http(self, metodo, url, dados=None, cabecalhos=None):
        self.posts.append(dict(dados))
        return self.respostas_token.pop(0)

    def receber(self, redirect, url, estado):
        self.recebidos.append((redirect, url, estado))
        return "CODIGO"

    def login(self, **extra):
        return sc.Login("cid", sc.REDIRECT_PADRAO, arquivo=self.arquivo, http=self.http,
                        receber=self.receber, **extra)

    def test_entra_pelo_navegador_salva_e_renova(self):
        self.respostas_token = [resposta(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.login().token(), "A1")
        _, url, estado = self.recebidos[0]
        consulta = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(consulta["code_challenge_method"], ["S256"])
        self.assertEqual(consulta["state"], [estado])
        self.assertIn("playlist-read-private", consulta["scope"][0])
        verificador = self.posts[0]["code_verifier"]
        desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(consulta["code_challenge"], [desafio])
        self.assertEqual(self.posts[0]["code"], "CODIGO")
        self.assertNotIn("client_secret", self.posts[0])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(self.arquivo).st_mode), 0o600)

        # Segunda vez: usa o salvo, sem navegador.
        self.assertEqual(self.login().token(), "A1")
        self.assertEqual(len(self.recebidos), 1)

        # Expirado: renova com o refresh token, sem navegador.
        dados = json.loads(self.arquivo.read_text())
        dados["expires_at"] = 0
        self.arquivo.write_text(json.dumps(dados))
        self.respostas_token = [resposta(200, {"access_token": "A2", "expires_in": 3600})]
        login = self.login()
        self.assertEqual(login.token(), "A2")
        self.assertEqual(self.posts[-1]["grant_type"], "refresh_token")
        self.assertEqual(login.dados["refresh_token"], "R1")  # mantém o anterior
        self.assertEqual(len(self.recebidos), 1)

    def test_renovacao_recusada_entra_de_novo(self):
        self.arquivo.parent.mkdir(parents=True)
        self.arquivo.write_text(json.dumps({"client_id": "cid", "access_token": "velho",
                                            "refresh_token": "R0", "expires_at": 0}))
        self.respostas_token = [
            resposta(400, {"error": "invalid_grant", "error_description": "Refresh token revoked"}),
            resposta(200, {"access_token": "novo", "refresh_token": "R2", "expires_in": 3600}),
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.login().token(), "novo")
        self.assertEqual(len(self.recebidos), 1)

    def test_outro_client_id_ou_login_pede_novo_login(self):
        self.arquivo.parent.mkdir(parents=True)
        self.arquivo.write_text(json.dumps({"client_id": "outro", "access_token": "x", "expires_at": 9e12}))
        self.respostas_token = [resposta(200, {"access_token": "A", "expires_in": 3600})] * 2
        with contextlib.redirect_stdout(io.StringIO()):
            self.login().token()
            self.login(novo=True).token()
        self.assertEqual(len(self.recebidos), 2)
        self.assertIn("show_dialog=true", self.recebidos[1][1])


class ReceberCodigoTests(unittest.TestCase):
    def _porta_livre(self):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _rodar(self, consulta):
        redirect = f"http://127.0.0.1:{self._porta_livre()}/callback"

        def abrir(_url):
            def visitar():
                time.sleep(0.2)
                urllib.request.urlopen(f"{redirect}?{consulta}", timeout=5).read()
            threading.Thread(target=visitar, daemon=True).start()

        with contextlib.redirect_stdout(io.StringIO()):
            return sc.receber_codigo(redirect, "https://accounts.spotify.com/authorize?x=1", "EST",
                                     abrir=abrir, prazo=10)

    def test_recebe_o_codigo_no_127(self):
        self.assertEqual(self._rodar("code=ABC&state=EST"), "ABC")

    def test_cancelado_e_state_errado(self):
        with self.assertRaisesRegex(sc.ErroSpotify, "cancelado"):
            self._rodar("error=access_denied&state=EST")
        with self.assertRaisesRegex(sc.ErroSpotify, "inesperada"):
            self._rodar("code=ABC&state=OUTRO")


class ClienteTests(unittest.TestCase):
    def test_401_renova_429_espera_e_5xx_tenta_de_novo(self):
        sequencia = [
            resposta(401, {"error": {"message": "expired"}}),
            resposta(429, cabecalhos={"Retry-After": "3"}),
            resposta(503),
            resposta(200, {"ok": True}),
        ]
        renovacoes, pausas = [], []
        login = LoginFixo()
        login.renovar = lambda: renovacoes.append(1)
        cliente = sc.ClienteSpotify(login, http=lambda *a, **k: sequencia.pop(0), dormir=pausas.append)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cliente.get("/me"), {"ok": True})
        self.assertEqual(renovacoes, [1])
        self.assertEqual(pausas, [3.0, sc.ESPERAS[1]])

    def test_429_longo_para_com_aviso(self):
        cliente = sc.ClienteSpotify(LoginFixo(), http=lambda *a, **k: resposta(429, cabecalhos={"Retry-After": "7200"}),
                                    dormir=lambda s: self.fail("não deveria esperar 2 horas"))
        with self.assertRaisesRegex(sc.ErroSpotify, "120 min"):
            cliente.get("/me")

    def test_sem_rede_tenta_e_desiste(self):
        def sem_rede(*a, **k):
            raise urllib.error.URLError("sem rota")
        pausas = []
        cliente = sc.ClienteSpotify(LoginFixo(), http=sem_rede, dormir=pausas.append)
        with self.assertRaisesRegex(sc.ErroSpotify, "sem conexão"):
            cliente.get("/me")
        self.assertEqual(pausas, list(sc.ESPERAS))


class ExportarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.saida = os.path.join(self.temp.name, "spotify.csv")
        self.api = FakeSpotify()
        login = Path(self.temp.name) / "login.json"
        login.write_text(json.dumps({"client_id": "cid", "redirect_uri": sc.REDIRECT_PADRAO,
                                     "access_token": "T", "refresh_token": "R", "expires_at": 9e12}))
        for alvo, valor in (("ARQUIVO_LOGIN", login), ("requisitar", self.api)):
            patcher = patch.object(sc, alvo, valor)
            patcher.start()
            self.addCleanup(patcher.stop)

    def rodar(self, *argv):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(saida), \
                patch.dict(os.environ, {"NO_COLOR": "1"}):
            codigo = plpython.main(["spotify", "-o", self.saida, *argv])
        return codigo, saida.getvalue()

    def linhas(self):
        with open(self.saida, encoding="utf-8-sig", newline="") as arquivo:
            return list(csv.DictReader(arquivo))

    def test_exporta_paginas_campos_e_ignora_o_que_nao_e_musica(self):
        self.api.playlist("p1", "Rock", [
            faixa("t1", "Song A", ["Banda A"], isrc="usabc1234567"),
            {"added_at": "x", "item": None},                                  # removida
            {"added_at": "x", "item": {"type": "episode", "id": "e1", "name": "Podcast"}},
            faixa("t2", "Song B", ["Artista B", "Convidado"], chave="track"),  # formato antigo
            faixa(None, "Arquivo Local", ["Alguém"], is_local=True),
        ])
        self.api.playlist("p2", "Mix do Fulano", [faixa("t9", "X", ["Y"])], dono="fulano")
        self.api.playlist("p3", "Colab", [faixa("t3", "Song C", ["C"])], dono="ciclano", colaborativa=True)
        codigo, texto = self.rodar()
        self.assertEqual(codigo, 0, texto)
        linhas = self.linhas()
        self.assertEqual(list(linhas[0].keys()), list(sc.COLUNAS))
        self.assertEqual([(l["playlist"], l["title"]) for l in linhas], [
            ("Rock", "Song A"), ("Rock", "Song B"), ("Rock", "Arquivo Local"), ("Colab", "Song C"),
        ])
        primeira = linhas[0]
        self.assertEqual((primeira["track_id"], primeira["isrc"], primeira["position"]), ("t1", "USABC1234567", "1"))
        self.assertEqual(linhas[1]["artists"], "Artista B; Convidado")
        self.assertEqual(linhas[1]["position"], "4")  # posição real na playlist
        self.assertEqual(linhas[2]["track_id"], "")
        self.assertEqual(self.api.consultas("/playlists/p2/"), [])  # nem tentou: é de outra pessoa
        self.assertIn("ficou de fora (Mix do Fulano)", texto)
        self.assertIn("plpython.py sync", texto)

    def test_segunda_leitura_mostra_mudancas_e_reaproveita_snapshot(self):
        self.api.playlist("p1", "Rock", [faixa("t1", "Song A", ["A"]), faixa("t2", "Song B", ["B"])])
        self.api.playlist("p2", "Calma", [faixa("t5", "Song E", ["E"])], snapshot="c1")
        self.rodar()
        self.api.itens["p1"] = [faixa("t2", "Song B", ["B"]), faixa("t3", "Song C", ["C"])]
        self.api.playlists[0]["snapshot_id"] = "s2"
        self.api.pedidos.clear()
        codigo, texto = self.rodar()
        self.assertEqual(codigo, 0, texto)
        self.assertIn("Rock (2 músicas): +1 −1 desde a última leitura", texto)
        self.assertIn("+ C — Song C", texto)
        self.assertIn("− A — Song A", texto)
        self.assertIn("Calma (1 música): sem mudanças", texto)
        self.assertEqual(self.api.consultas("/playlists/p2/items"), [])  # snapshot igual
        self.assertEqual([l["title"] for l in self.linhas()], ["Song B", "Song C", "Song E"])

    def test_so_uma_playlist_mantem_as_outras_e_lista_as_faixas(self):
        self.api.playlist("p1", "Rock", [faixa("t1", "Song A", ["A"])])
        self.api.playlist("p2", "Pop Brasil", [faixa("t2", "Song B", ["B"])])
        self.rodar()
        self.api.itens["p2"] = [faixa("t3", "Song C", ["C"])]
        self.api.playlists[1]["snapshot_id"] = "s9"
        codigo, texto = self.rodar("pop brasil")
        self.assertEqual(codigo, 0, texto)
        self.assertIn("001. C — Song C", texto)
        self.assertEqual([l["title"] for l in self.linhas()], ["Song A", "Song C"])

    def test_playlist_pedida_sem_acesso_e_curtidas(self):
        self.api.playlist("p1", "Rock", [faixa("t1", "Song A", ["A"])])
        self.api.playlist("p2", "Do Fulano", [faixa("t9", "X", ["Y"])], dono="fulano")
        self.api.bloqueadas.add("p2")
        self.api.curtidas = [faixa("t7", "Curtida 1", ["K"], chave="track"),
                             faixa("t8", "Curtida 2", ["K"], chave="track"),
                             faixa("t1", "Song A", ["A"], chave="track")]
        codigo, texto = self.rodar("Do Fulano", "-L")
        self.assertIn("Do Fulano: sem acesso", texto)
        self.assertEqual([(l["playlist"], l["title"]) for l in self.linhas()],
                         [("Músicas Curtidas", "Curtida 1"), ("Músicas Curtidas", "Curtida 2"),
                          ("Músicas Curtidas", "Song A")])
        # Nada mudou nas curtidas: só a 1ª página é pedida.
        self.api.pedidos.clear()
        self.rodar("-L")
        self.assertEqual(len(self.api.consultas("/me/tracks")), 1)

    def test_403_no_inicio_explica_premium_e_user_management(self):
        original = self.api.__call__

        def proibido(metodo, url, dados=None, cabecalhos=None):
            if url.endswith("/me"):
                return resposta(403, {"error": {"message": "Active premium subscription required"}})
            return original(metodo, url, dados, cabecalhos)

        with patch.object(sc, "requisitar", proibido):
            codigo, texto = self.rodar()
        self.assertEqual(codigo, 1)
        self.assertIn("premium subscription required", texto.lower())
        self.assertIn("User Management", texto)
        self.assertFalse(os.path.exists(self.saida))

    def test_extrair_id(self):
        pid = "3cEYpjA9oz9GiPac4AsH4n"
        self.assertEqual(sc.extrair_playlist_id(f"https://open.spotify.com/intl-pt/playlist/{pid}?si=abc"), pid)
        self.assertEqual(sc.extrair_playlist_id(f"spotify:playlist:{pid}"), pid)
        self.assertEqual(sc.extrair_playlist_id(pid), pid)
        with self.assertRaises(ValueError):
            sc.extrair_playlist_id("https://open.spotify.com/album/123")


if __name__ == "__main__":
    unittest.main()
