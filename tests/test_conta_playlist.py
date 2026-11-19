import argparse
import contextlib
import csv
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import plpython
import pltube
from comandos import _comum, playlist


def ns(**kw):
    return argparse.Namespace(**{"cookies_navegador": None, "cookies_arquivo": None, **kw})


class FakeYDL:
    respostas: dict = {}
    opcoes: list = []

    def __init__(self, opts):
        FakeYDL.opcoes.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        resposta = FakeYDL.respostas[url]
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


class ContaTests(unittest.TestCase):
    def test_converte_opcoes_de_conta(self):
        self.assertEqual(_comum.cookies_ytdlp(ns()), {})
        self.assertEqual(_comum.cookies_ytdlp(ns(cookies_navegador="firefox")),
                         {"cookiesfrombrowser": ("firefox", None, None, None)})
        self.assertEqual(_comum.cookies_ytdlp(ns(cookies_navegador="Chrome:Profile 1")),
                         {"cookiesfrombrowser": ("chrome", "Profile 1", None, None)})
        with tempfile.NamedTemporaryFile(suffix=".txt") as arquivo:
            self.assertEqual(_comum.cookies_ytdlp(ns(cookies_arquivo=arquivo.name)),
                             {"cookiefile": os.path.abspath(arquivo.name)})
        with self.assertRaises(SystemExit):
            _comum.cookies_ytdlp(ns(cookies_navegador="netscape"))
        with self.assertRaises(SystemExit):
            _comum.cookies_ytdlp(ns(cookies_navegador="firefox", cookies_arquivo="x.txt"))

    def test_variavel_de_ambiente_vira_padrao(self):
        with patch.dict(os.environ, {"PLPYTHON_COOKIES_FROM_BROWSER": "firefox"}):
            with patch("pltube.executar", return_value=0) as fake:
                plpython.main(["download"])
        self.assertEqual(fake.call_args.args[0].cookies_ytdlp,
                         {"cookiesfrombrowser": ("firefox", None, None, None)})

    def test_download_usa_a_conta_em_toda_consulta(self):
        with patch("pltube.executar", return_value=0) as fake:
            plpython.main(["download", "--cookies-from-browser", "firefox"])
        args = fake.call_args.args[0]
        with patch.object(pltube, "COOKIES_YTDLP", {}):
            pltube.verificar_diretorios(args)
            self.assertEqual(pltube.COOKIES_YTDLP, {"cookiesfrombrowser": ("firefox", None, None, None)})
            pltube.verificar_diretorios(pltube.criar_parser().parse_args([]))

    def test_dica_quando_o_video_exige_login(self):
        saida = io.StringIO()
        with patch.object(pltube, "_DICA_LOGIN_MOSTRADA", False), patch.object(pltube, "COOKIES_YTDLP", {}), \
                contextlib.redirect_stdout(saida):
            pltube.dica_login("ERROR: Sign in to confirm your age. This video may be inappropriate")
            pltube.dica_login("Private video. Sign in if you've been granted access")
        self.assertEqual(saida.getvalue().count("--cookies-from-browser"), 1)  # só uma vez


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        FakeYDL.respostas, FakeYDL.opcoes = {}, []
        modulo = type("M", (), {"YoutubeDL": FakeYDL})
        patcher = patch.object(pltube, "yt_dlp", modulo)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def rodar(self, *argv):
        saida, erro = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(erro):
            codigo = plpython.main(["playlist", *argv, "-c", self.temp.name])
        return codigo, saida.getvalue() + erro.getvalue()

    def lista(self, titulo, lista_id, ids):
        entradas = [{"id": i, "title": f"Artista - Música {i}", "url": f"https://www.youtube.com/watch?v={i}"}
                    for i in ids]
        entradas.append({"id": "zzzzzzzzzzz", "title": "[Deleted video]", "url": "x"})
        return {"_type": "playlist", "id": lista_id, "title": titulo, "entries": entradas}

    def test_curtidas_exige_conta(self):
        codigo, saida = self.rodar("liked")
        self.assertEqual(codigo, 2)
        self.assertIn("--cookies-from-browser", saida)

    def test_cria_csv_no_formato_do_download(self):
        FakeYDL.respostas[":ytfav"] = self.lista("Liked videos", "LL", ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        codigo, saida = self.rodar("liked", "--cookies-from-browser", "firefox")
        self.assertEqual(codigo, 0, saida)
        self.assertEqual(FakeYDL.opcoes[0]["cookiesfrombrowser"], ("firefox", None, None, None))
        caminho = os.path.join(self.temp.name, "Liked videos.csv")
        with open(caminho, encoding="utf-8") as arquivo:
            linhas = list(csv.reader(arquivo))
        self.assertEqual(linhas[0], playlist.COLUNAS)
        self.assertEqual(len(linhas), 3)  # cabeçalho + 2; o apagado ficou de fora
        self.assertEqual(linhas[1][2], "https://www.youtube.com/watch?v=aaaaaaaaaaa&list=LL&index=1")
        self.assertIn("1 privados/apagados ignorados", saida)
        self.assertIn("download -c", saida)
        # o download lê a URL na coluna padrão (2)
        self.assertEqual(pltube.obter_chave_video(linhas[1][2]), ("youtube:aaaaaaaaaaa", "aaaaaaaaaaa"))

    def test_mine_cria_um_csv_por_playlist(self):
        FakeYDL.respostas["https://www.youtube.com/feed/playlists"] = {"entries": [
            {"url": "https://www.youtube.com/playlist?list=PL1"},
            {"url": "https://www.youtube.com/playlist?list=PL2"}]}
        FakeYDL.respostas["https://www.youtube.com/playlist?list=PL1"] = self.lista("Rock", "PL1", ["aaaaaaaaaaa"])
        FakeYDL.respostas["https://www.youtube.com/playlist?list=PL2"] = self.lista("Lofi/Chill", "PL2", ["bbbbbbbbbbb"])
        codigo, saida = self.rodar("mine", "--cookies-from-browser", "firefox")
        self.assertEqual(codigo, 0, saida)
        self.assertEqual(sorted(os.listdir(self.temp.name)), ["Lofi_Chill.csv", "Rock.csv"])

    def test_url_publica_com_nome_e_rodar_de_novo_atualiza(self):
        url = "https://www.youtube.com/playlist?list=PLx"
        FakeYDL.respostas[url] = self.lista("Qualquer", "PLx", ["aaaaaaaaaaa"])
        self.rodar(url, "--name", "Minha")
        FakeYDL.respostas[url] = self.lista("Qualquer", "PLx", ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        codigo, saida = self.rodar(url, "--name", "Minha")
        self.assertEqual(codigo, 0)
        with open(os.path.join(self.temp.name, "Minha.csv"), encoding="utf-8") as arquivo:
            self.assertEqual(len(list(csv.reader(arquivo))), 3)

    def test_erro_de_login_mostra_dica(self):
        url = "https://www.youtube.com/playlist?list=PLprivada"
        FakeYDL.respostas[url] = RuntimeError("ERROR: [youtube:tab] PLprivada: The playlist does not exist "
                                              "or is private. Use --cookies for the authentication")
        with patch.object(pltube, "_DICA_LOGIN_MOSTRADA", False):
            codigo, saida = self.rodar(url)
        self.assertEqual(codigo, 1)
        self.assertIn("--cookies-from-browser firefox", saida)


if __name__ == "__main__":
    unittest.main()
