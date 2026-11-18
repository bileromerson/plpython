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
            "--artist-links", "--wait-on-limit", "--require-isrc",
        ])
        self.assertEqual(args.lista, [os.path.abspath("a.csv"), os.path.abspath("b.csv")])
        self.assertEqual(args.base_dir, "base")
        self.assertEqual(args.work_dir, os.getcwd())
        self.assertEqual(args.row, -1)
        self.assertTrue(args.artist_links and args.esperar_bloqueio)
        self.assertTrue(args.modelo_hibrido)  # implícito por --require-isrc
        self.assertFalse(args.reprocess)

    def test_prioridades_de_metadados(self):
        args = self._capturar("pltube", ["download", "--prefer-db", "--no-review"])
        self.assertEqual((args.preferir, args.revisar), ("musicbrainz", "none"))
        self.assertTrue(args.modelo_hibrido)
        args = self._capturar("pltube", ["download", "--hybrid"])
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
                args = self._capturar("pltube", ["download", "--csv-dir", "listas"])
                pltube.verificar_diretorios(args)
                real = os.path.realpath(temp)
                self.assertEqual(os.path.realpath(pltube.BASE_DIR), os.path.join(real, "listas"))
                self.assertEqual(os.path.realpath(pltube.SONGS_DIR), os.path.join(real, "songs"))
                self.assertEqual(os.path.realpath(pltube.PLAYLISTS_DIR), os.path.join(real, "playlists"))
                self.assertEqual(os.path.realpath(pltube.WORK_DIR), real)
            finally:
                os.chdir(antigo)
                pltube.verificar_diretorios(pltube.criar_parser().parse_args([]))

    def test_update_forca_retry(self):
        args = self._capturar("pltube", ["update", "-c", "x"])
        self.assertTrue(args.reprocess)
        self.assertEqual(args.lista, [])

    def test_nomes_antigos_continuam_aceitos(self):
        args = self._capturar("pltube", [
            "baixar", "--BaseDir", "x", "--lista", "y.csv", "--row", "3",
            "--modelo-hibrido", "--state-file", "s.db",
        ])
        self.assertEqual((args.base_dir, args.lista, args.row), ("x", [os.path.abspath("y.csv")], 3))
        self.assertTrue(args.modelo_hibrido)
        self.assertEqual(args.state_file, "s.db")

    def test_metadata_simula_por_padrao(self):
        args = self._capturar("aplicar_metadados", ["metadata", "--sheet", "r.csv"])
        self.assertFalse(args.apply)
        self.assertEqual(args.arquivo, "r.csv")

    def test_revert_e_spotify(self):
        args = self._capturar("reverter_biblioteca", ["revert", "-s", "m", "--apply"])
        self.assertEqual((args.root, args.apply), ("m", True))
        args = self._capturar("spotify_playlist_viewer", ["spotify", "abc"])
        self.assertEqual(args.playlist, "abc")


if __name__ == "__main__":
    unittest.main()
