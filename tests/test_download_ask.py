import argparse
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pltube
from comandos import review


class DownloadAskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = pltube.abrir_banco_estado(os.path.join(self.tmp.name, "downloads.sqlite3"),
                                             os.path.join(self.tmp.name, "musicas.sqlite3"))
        for chave, status in (("youtube:aaaaaaaaaaa", "PENDENTE"), ("youtube:bbbbbbbbbbb", "RESOLVIDO"),
                              ("youtube:ccccccccccc", "OBSERVAR")):
            self.con.execute("INSERT INTO music.metadata_resolution (video_key, video_id, status, result_json) "
                             "VALUES (?, ?, ?, ?)", (chave, chave[8:], status, json.dumps({"status": status})))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()
        pltube.FAIXAS_PARA_PERGUNTAR.clear()

    def test_pergunta_so_o_que_nao_foi_confirmado(self):
        pltube.FAIXAS_PARA_PERGUNTAR[:] = ["youtube:aaaaaaaaaaa", "youtube:bbbbbbbbbbb", "youtube:ccccccccccc"]
        visto = {}
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(review, "responder", side_effect=lambda con, op: visto.update(vars(op))):
            pltube.perguntar_faltando(self.con, argparse.Namespace(ignorar=["album"], verbose=0))
        self.assertEqual(visto["chaves"], {"youtube:aaaaaaaaaaa", "youtube:ccccccccccc"})
        self.assertEqual(visto["ignorar"], ["album"])

    def test_fora_do_terminal_so_avisa(self):
        pltube.FAIXAS_PARA_PERGUNTAR[:] = ["youtube:aaaaaaaaaaa"]
        with mock.patch.object(sys.stdin, "isatty", return_value=False), \
                mock.patch.object(review, "responder") as resp, mock.patch("builtins.print") as imp:
            pltube.perguntar_faltando(self.con, argparse.Namespace())
        resp.assert_not_called()
        self.assertIn("review --answer", imp.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
