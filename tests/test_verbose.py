import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch

import plpython
import pltube
import verbose


class VerboseTests(unittest.TestCase):
    def tearDown(self):
        verbose.definir(0)

    def capturar(self, nivel, funcao):
        verbose.definir(nivel)
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            funcao()
        return saida.getvalue()

    def test_niveis_como_no_nmap(self):
        def falar():
            verbose.v("SQL", "nivel1")
            verbose.vv("Tags", "nivel2")
            verbose.vvv("yt-dlp", "nivel3")
        self.assertEqual(self.capturar(0, falar), "")
        self.assertEqual(self.capturar(1, falar), "[SQL] nivel1\n")
        dois = self.capturar(2, falar)
        self.assertIn("[SQL] nivel1", dois)
        self.assertIn("[Tags] nivel2", dois)
        self.assertNotIn("nivel3", dois)
        self.assertIn("nivel3", self.capturar(3, falar))

    def test_cores_so_quando_ativadas(self):
        def falar():
            verbose.v("Comparação", "título: YouTube 'A' ≠ banco 'B' → não bate; preferência youtube")
            verbose.v("Resultado", "RESOLVIDO: X — Y")
        with patch.dict("os.environ", {"FORCE_COLOR": "1"}, clear=False):
            colorido = self.capturar(1, falar)
        self.assertIn("\033[1;35m[Comparação]\033[0m", colorido)
        self.assertIn("\033[33m≠\033[0m", colorido)
        self.assertIn("\033[1;32mRESOLVIDO\033[0m", colorido)
        with patch.dict("os.environ", {"FORCE_COLOR": "1", "NO_COLOR": "1"}, clear=False):
            self.assertNotIn("\033", self.capturar(1, falar))
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("FORCE_COLOR", None)
            self.assertNotIn("\033", self.capturar(1, falar))  # saída que não é terminal: sem cor

    def test_contagem_de_v_na_linha_de_comando(self):
        for argv, esperado in ((["download"], 0), (["download", "-v"], 1), (["download", "-vv"], 2),
                               (["download", "-v", "-v", "-v"], 3), (["update", "--verbose"], 1)):
            with patch("pltube.executar", return_value=0) as fake, contextlib.redirect_stdout(io.StringIO()):
                plpython.main(argv)
            self.assertEqual(fake.call_args.args[0].verbose, esperado, argv)
            self.assertEqual(verbose.NIVEL, esperado)

    def test_erro_silencioso_aparece_so_com_v(self):
        with patch.object(pltube, "LOG_FILE", "/dev/null"):
            self.assertEqual(self.capturar(0, lambda: pltube.registrar_erro("u", "falhou x")), "")
            self.assertIn("[Erro] falhou x", self.capturar(1, lambda: pltube.registrar_erro("u", "falhou x")))

    def test_identificacao_no_v(self):
        import identificar_musica as im
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from ytm_falso import YTMFalso, faixa
        api = YTMFalso(catalogo=[faixa("aaaaaaaaaaa", "Believer", ["Imagine Dragons"], album="Evolve", segundos=204)])
        saida = self.capturar(2, lambda: im.identificar(
            {"id": "vvvvvvvvvvv", "title": "Believer - Imagine Dragons (Lyrics)", "uploader": "Lyrics Hub", "duration": 205},
            cliente=im.ClienteYTMusic(api, pausa=0)))
        self.assertIn("[YouTube Music] busca 'Believer Imagine Dragons'", saida)
        self.assertIn("[Identificação] RESOLVIDO: Imagine Dragons — Believer | álbum Evolve (catálogo do YouTube Music)", saida)
        saida = self.capturar(1, lambda: im.identificar({"id": "x", "title": "Mix 1 hour", "uploader": "Lofi Hits", "duration": 3600}))
        self.assertIn("conferir: vídeo longo", saida)

if __name__ == "__main__":
    unittest.main()
