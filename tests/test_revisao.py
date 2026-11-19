import contextlib
import io
import os
import tempfile
import unittest

import plpython
import pltube


class RevisaoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.antigo = os.getcwd()
        os.chdir(self.temp.name)
        conexao = pltube.abrir_banco_estado(
            os.path.join("sql", "downloads.sqlite3"), os.path.join("sql", "musicas.sqlite3")
        )
        for vid, status, nota in (("aaa", "OBSERVAR", "título diverge"), ("bbb", "RESOLVIDO", "")):
            pltube.salvar_resolucao(conexao, f"youtube:{vid}", {
                "video_id": vid, "status": status,
                "metadata": {"id": vid, "title": f"Faixa {vid}", "artists": ["snuffles"]},
                "observations": [nota] if nota else [],
            })
        conexao.close()

    def tearDown(self):
        os.chdir(self.antigo)
        self.temp.cleanup()

    def rodar(self, *argv):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            codigo = plpython.main(["review", *argv])
        return codigo, saida.getvalue()

    def test_lista_so_o_que_precisa_conferir(self):
        codigo, saida = self.rodar()
        self.assertEqual(codigo, 0)
        self.assertIn("[OBSERVAR] aaa  snuffles — Faixa aaa", saida)
        self.assertIn("título diverge", saida)
        self.assertNotIn("Faixa bbb", saida)
        _, saida = self.rodar("--status", "all")
        self.assertIn("Faixa bbb", saida)

    def test_ok_confirma(self):
        codigo, saida = self.rodar("--ok", "aaa")
        self.assertEqual(codigo, 0)
        self.assertIn("Confirmado", saida)
        _, saida = self.rodar()
        self.assertIn("Nenhuma faixa", saida)

    def test_sql_somente_leitura_e_export(self):
        _, saida = self.rodar("--sql", "SELECT title FROM music_tracks ORDER BY title")
        self.assertIn("Faixa aaa", saida)
        codigo, saida = self.rodar("--sql", "DELETE FROM music_tracks")
        self.assertEqual(codigo, 1)
        self.rodar("--export", "conferir.csv")
        with open("conferir.csv", encoding="utf-8-sig") as arquivo:
            self.assertIn("Faixa aaa", arquivo.read())

    def test_sem_banco_explica(self):
        os.chdir(self.antigo)
        with tempfile.TemporaryDirectory() as vazio:
            os.chdir(vazio)
            codigo, _ = self.rodar()
            self.assertEqual(codigo, 1)
            os.chdir(self.temp.name)


if __name__ == "__main__":
    unittest.main()
