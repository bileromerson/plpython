import os
import unittest
from unittest.mock import patch

import plpython


class PlpythonCliTests(unittest.TestCase):
    def _capturar(self, modulo, argv):
        with patch(f"{modulo}.executar", return_value=0) as fake:
            self.assertEqual(plpython.main(argv), 0)
        return fake.call_args.args[0]

    def test_download_com_varios_csvs_e_opcoes(self):
        args = self._capturar("pltube", [
            "download", "a.csv", "b.csv", "-c", "base", "-u", "-1",
            "--wait-on-limit", "--require-isrc",
        ])
        self.assertEqual(args.lista, [os.path.abspath("a.csv"), os.path.abspath("b.csv")])
        self.assertEqual(args.base_dir, "base")
        self.assertEqual(args.work_dir, os.getcwd())
        self.assertEqual(args.row, -1)
        self.assertTrue(args.artist_links and args.esperar_bloqueio)
        self.assertTrue(args.modelo_hibrido)  # identificação ligada por padrão
        self.assertFalse(args.reprocess)

    def test_prioridades_de_metadados(self):
        args = self._capturar("pltube", ["download", "--prefer-db", "--no-review"])
        self.assertEqual((args.preferir, args.revisar), ("musicbrainz", "none"))
        self.assertTrue(args.modelo_hibrido)
        args = self._capturar("pltube", ["download"])
        self.assertEqual((args.preferir, args.revisar), ("youtube", "all"))
        args = self._capturar("pltube", ["download", "--review", "diff"])
        self.assertEqual(args.revisar, "diff")

    def test_csv_dir_nao_muda_pasta_de_trabalho(self):
        import tempfile
        import pltube
        with tempfile.TemporaryDirectory() as temp:
            antigo = os.getcwd()
            os.chdir(temp)
            try:
                os.makedirs("listas")
                args = self._capturar("pltube", ["download", "--csvfile", "listas"])
                pltube.verificar_diretorios(args)
                real = os.path.realpath(temp)
                self.assertEqual(os.path.realpath(pltube.BASE_DIR), os.path.join(real, "listas"))
                self.assertEqual(os.path.realpath(pltube.SONGS_DIR), os.path.join(real, "songs"))
                self.assertEqual(os.path.realpath(pltube.PLAYLISTS_DIR), os.path.join(real, "playlists"))
                self.assertEqual(os.path.realpath(pltube.WORK_DIR), real)
            finally:
                os.chdir(antigo)
                from comandos import download
                pltube.verificar_diretorios(download.criar_parser().parse_args([]))

    def test_csvfile_aceita_arquivo_unico(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            arquivo = os.path.join(temp, "Alone.csv")
            open(arquivo, "w").close()
            args = self._capturar("pltube", ["download", "--csvfile", arquivo])
            self.assertEqual(args.lista, [arquivo])
            self.assertEqual(args.base_dir, temp)

    def test_exige_comando_antes_das_opcoes(self):
        import contextlib, io
        with patch("pltube.executar") as fake, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(plpython.main(["-c", "csvs"]), 2)
            self.assertEqual(plpython.main(["csvs/a.csv"]), 2)
        fake.assert_not_called()

    def test_help_lista_comandos(self):
        import contextlib, io
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            self.assertEqual(plpython.main(["help"]), 0)
            self.assertEqual(plpython.main([]), 2)
        for comando in ("download", "update", "metadata", "review", "spotify", "revert"):
            self.assertIn(comando, saida.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as ajuda, self.assertRaises(SystemExit):
            plpython.main(["help", "download"])
        self.assertIn("--csvfile", ajuda.getvalue())

    def test_update_forca_retry(self):
        args = self._capturar("pltube", ["update", "-c", "x"])
        self.assertTrue(args.reprocess)
        self.assertEqual(args.lista, [])


    def test_metadata_simula_por_padrao(self):
        args = self._capturar("aplicar_metadados", ["metadata", "--sheet", "r.csv"])
        self.assertFalse(args.apply)
        self.assertEqual(args.arquivo, "r.csv")

    def test_revert_e_spotify(self):
        args = self._capturar("reverter_biblioteca", ["revert", "-s", "m", "--apply"])
        self.assertEqual((args.root, args.apply), ("m", True))
        args = self._capturar("spotify_conta", ["spotify", "abc", "-L", "-o", "x.csv"])
        self.assertEqual((args.playlists, args.liked, args.output), (["abc"], True, "x.csv"))
        args = self._capturar("sincronizar_playlists", ["sync", "Rock", "-y", "--keep-extra"])
        self.assertEqual((args.playlists, args.apply, args.manter_extras, args.csvfile),
                         (["Rock"], True, True, "spotify.csv"))


if __name__ == "__main__":
    unittest.main()
