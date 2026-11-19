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


class IsrcNoBancoTests(unittest.TestCase):
    def test_mesmo_isrc_usa_faixa_confirmada_sem_musicbrainz(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = pltube.abrir_banco_estado(os.path.join(tmp, "d.sqlite3"), os.path.join(tmp, "m.sqlite3"))
            confirmada = {"status": "RESOLVIDO", "video_id": "aaaaaaaaaaa",
                          "metadata": {"title": "Believer", "artists": ["Imagine Dragons"], "album": "Evolve",
                                       "isrc": "USUM71700626"}}
            con.execute("INSERT INTO music.metadata_resolution (video_key, video_id, status, result_json) "
                        "VALUES ('youtube:aaaaaaaaaaa', 'aaaaaaaaaaa', 'RESOLVIDO', ?)", (json.dumps(confirmada),))
            pltube._salvar_metadados_musica(con, "youtube:aaaaaaaaaaa", confirmada)
            con.commit()
            cliente = mock.Mock()
            with mock.patch.object(pltube, "MUSICBRAINZ_CLIENT", cliente), \
                    mock.patch.object(pltube, "ARTISTAS_CONHECIDOS", []):
                meta, res = pltube.resolver_meta_hibrido(
                    {"id": "zzzzzzzzzzz", "title": "believer lyrics 1 hour", "uploader": "Lyrics Hub",
                     "isrc": "USUM71700626", "duration": 204}, estado=con)
            con.close()
        self.assertEqual(res["status"], "RESOLVIDO")
        self.assertEqual((res["metadata"]["title"], res["metadata"]["artists"], res["metadata"]["album"]),
                         ("Believer", ["Imagine Dragons"], "Evolve"))
        self.assertEqual(res["provenance"]["artists"], "banco_isrc")
        cliente.search_by_isrc.assert_not_called()
