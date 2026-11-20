import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import banco_artistas as ba
import pltube
import identificar_musica as im


class BancoArtistasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ba.abrir(os.path.join(self.tmp.name, "artistas.sqlite3"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_mesma_chave_e_o_mesmo_artista(self):
        a = ba.adicionar(self.db, "WHSPRS")
        self.assertEqual(ba.adicionar(self.db, "Whsprs"), a)
        self.assertEqual(ba.canonico(self.db, "whsprs"), "WHSPRS")
        self.assertEqual(ba.canonico(self.db, "AC/DC"), None)

    def test_variante_vira_nome_certo(self):
        ba.adicionar(self.db, "Henrique & Juliano")
        ba.adicionar_variante(self.db, "Henrique & Juliano", "Henrique e Juliano")
        self.assertEqual(ba.corrigir_nomes(self.db, ["Henrique e Juliano", "Henrique & Juliano"]),
                         ["Henrique & Juliano"])
        self.assertIn("Henrique e Juliano", ba.nomes_conhecidos(self.db))

    def test_alias_junta_dois_artistas(self):
        ba.adicionar(self.db, "AC/DC")
        ba.adicionar(self.db, "ACDC Band")
        ba.adicionar_variante(self.db, "ACDC Band", "AC D C B")
        msg = ba.adicionar_variante(self.db, "AC/DC", "ACDC Band")
        self.assertIn("juntado", msg)
        self.assertEqual(ba.canonico(self.db, "AC D C B"), "AC/DC")
        self.assertEqual([a.nome for a in ba.listar(self.db)], ["AC/DC"])

    def test_renomear_e_remover(self):
        ba.adicionar(self.db, "Imagine Dragon")
        ba.renomear(self.db, "Imagine Dragon", "Imagine Dragons")
        self.assertEqual(ba.canonico(self.db, "Imagine Dragon"), "Imagine Dragons")
        self.assertEqual(ba.remover(self.db, "Imagine Dragon"), "variação removida")
        self.assertIsNone(ba.canonico(self.db, "Imagine Dragon"))

    def test_corrigir_resultado(self):
        ba.adicionar(self.db, "Earth, Wind & Fire")
        ba.adicionar_variante(self.db, "Earth, Wind & Fire", "EWF")
        res = {"metadata": {"artists": ["EWF"], "album_artists": ["EWF"]}}
        self.assertEqual(ba.corrigir_resultado(self.db, res), [("EWF", "Earth, Wind & Fire")])
        self.assertEqual(res["metadata"]["artists"], ["Earth, Wind & Fire"])

    def test_parecidos(self):
        ba.adicionar(self.db, "Henrique & Juliano")
        ba.adicionar(self.db, "Henrique e Juliano")
        self.assertEqual(len(ba.parecidos(self.db)), 1)


class IntegracaoTests(unittest.TestCase):
    def test_anexado_ao_banco_e_importa_grafia_mais_usada(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = pltube.abrir_banco_estado(os.path.join(tmp, "downloads.sqlite3"),
                                            os.path.join(tmp, "musicas.sqlite3"))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "artistas.sqlite3")))
            cols = [c[1] for c in con.execute("PRAGMA music.table_info(music_tracks)")]
            for i, nomes in enumerate((["WHSPRS"], ["WHSPRS"], ["Whsprs"])):
                valores = {c: "" for c in cols}
                valores.update(video_key=f"youtube:aaaaaaaaaa{i}", artists_json=json.dumps(nomes),
                               metadata_status="RESOLVIDO", provenance_json='{"artists": "youtube_music"}')
                con.execute(f"INSERT INTO music.music_tracks ({','.join(valores)}) VALUES "
                            f"({','.join('?' * len(valores))})", list(valores.values()))
            conhecidos = pltube.carregar_artistas_conhecidos(con)
            self.assertIn("WHSPRS", conhecidos)
            self.assertEqual(ba.canonico(con, "Whsprs"), "WHSPRS")
            con.close()

    def test_variacao_do_banco_acha_artista_no_titulo(self):
        r = im.identificar({"id": "x", "title": "Liberdade Provisória - H e J - com cifras", "uploader": "x"},
                           conhecidos=["Henrique & Juliano", "H e J"])
        self.assertEqual((r["metadata"]["title"], r["metadata"]["artists"]), ("Liberdade Provisória", ["H e J"]))


if __name__ == "__main__":
    unittest.main()
