import contextlib
import io
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

    def test_comparacao_youtube_x_banco_no_v(self):
        import resolvedor_metadados as r
        from test_modelo_identificacao import FakeMB, gravacao, release
        saida = self.capturar(1, lambda: r.resolver_com_prioridade(
            "v", {"title": "Get Lucky Remix", "artists": ["Daft Punk"], "duration": 200},
            musicbrainz_client=FakeMB([gravacao("Get Lucky", ["Daft Punk"], [release(1, "RAM", artistas=["Daft Punk"])])])))
        self.assertIn("título: YouTube 'Get Lucky Remix' ≠ banco 'Get Lucky' → não bate; preferência youtube", saida)
        self.assertIn("artista: YouTube 'Daft Punk' = banco 'Daft Punk' → bate", saida)
        self.assertIn("[Resultado] OBSERVAR", saida)


if __name__ == "__main__":
    unittest.main()
