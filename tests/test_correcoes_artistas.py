"""Correção de artista que vale para as outras faixas: review --answer guarda, review --apply confere."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # helpers dos outros testes

import correcoes_artistas as ca
import plpython
import pltube
from test_modelo_identificacao import FakeMB, UUID, gravacao, release

DEDUZIDOS = "não confirmado no MusicBrainz; título/artista deduzidos do nome do canal (confiança 40%)"


def resolucao(vid, titulo, artistas, video, canal="Canal X"):
    return {
        "video_id": vid, "status": "OBSERVAR", "observations": [DEDUZIDOS], "pending_reasons": [],
        "metadata": {"id": vid, "title": titulo, "artists": artistas, "album": "", "isrc": ""},
        "provenance": {"title": "youtube_canal", "artists": "youtube_canal"},
        "source_youtube": {"title": video, "video_title": video, "artists": [], "uploader": canal,
                           "duration": 200, "thumbnails": [], "youtube_music": {}},
        "candidates": [],
    }


FAIXAS = (  # na ordem em que o review mostra (artista, título)
    ("aaaaaaaaaaa", "Crawling", ["Canal X"], "Crawling (Official Video)"),
    ("bbbbbbbbbbb", "In the End", ["Canal X"], "Linkin Park - In the End (Lyrics)"),
    ("ccccccccccc", "Numb", ["Canal X"], "Numb (Lyrics)"),
    ("ddddddddddd", "Thunder", ["Canal X"], "Thunder (Lyrics)"),
    ("eeeeeeeeeee", "Back One Day", ["TheFatRat & NEFFEX"], "TheFatRat & NEFFEX - Back One Day"),
    ("fffffffffff", "Never Be Alone", ["TheFatRat & NEFFEX"], "TheFatRat & NEFFEX - Never Be Alone"),
)


class CorrecoesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        antigo = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, antigo)
        db = pltube.abrir_banco_estado(os.path.join("sql", "downloads.sqlite3"), os.path.join("sql", "musicas.sqlite3"))
        for indice, (vid, titulo, artistas, video) in enumerate(FAIXAS):
            caminho = os.path.join(self.temp.name, "songs", artistas[0], f"{titulo} [{vid}].mp3")
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            with open(caminho, "wb") as arquivo:
                arquivo.write(b"mp3")
            pltube.salvar_resolucao(db, f"youtube:{vid}", resolucao(vid, titulo, artistas, video), caminho)
            db.execute("INSERT INTO playlist_items (csv_path, row_index, video_key, title, artist, duration, path) "
                       "VALUES ('lista.csv', ?, ?, ?, ?, 200, ?)", (indice, f"youtube:{vid}", titulo, artistas[0], caminho))
        db.commit()
        db.close()
        mb = FakeMB([gravacao("In the End", ["Linkin Park"], [release(20, "Hybrid Theory", artistas=["Linkin Park"])], n=20)],
                    capas={UUID.format(520): "https://c/hybrid.jpg"})
        for alvo, valor in (("aplicar_tags_hibridas", lambda *a, **k: True), ("MUSICBRAINZ_CLIENT", mb),
                            ("PREFERIR", pltube.PREFERIR), ("ARTISTAS_CONHECIDOS", []), ("RESOLVER_CACHE", {})):
            patcher = patch.object(pltube, alvo, valor)
            patcher.start()
            self.addCleanup(patcher.stop)

    def rodar(self, argv, respostas=()):
        saida, fila = io.StringIO(), list(respostas)

        def entrada(pergunta):
            saida.write(pergunta + "\n")
            return fila.pop(0)

        with patch("builtins.input", side_effect=entrada), contextlib.redirect_stdout(saida), \
                patch.dict(os.environ, {"NO_COLOR": "1"}):
            codigo = plpython.main(argv)
        self.assertEqual(codigo, 0, saida.getvalue())
        return saida.getvalue()

    def faixa(self, vid):
        db = pltube.abrir_banco_estado(os.path.join("sql", "downloads.sqlite3"), os.path.join("sql", "musicas.sqlite3"))
        try:
            return pltube.carregar_resolucao(db, f"youtube:{vid}")["result"]
        finally:
            db.close()

    def revisar_e_corrigir(self):
        # Crawling: título certo, artista "Linkin Park" → guarda Canal X → Linkin Park.
        # In the End / Numb / Thunder: só o título é perguntado (o artista vai para o --apply) → pula.
        # Back One Day: artista "TheFatRat | NEFFEX"; Never Be Alone: só o título → pula.
        return self.rodar(["review", "-a"], [":ok", "Linkin Park", ":s", ":s", ":s", ":ok", "TheFatRat | NEFFEX", ":s"])

    def test_corrigir_um_artista_dispensa_a_pergunta_nas_faixas_parecidas(self):
        saida = self.revisar_e_corrigir()
        self.assertEqual(saida.count("⚠ Artistas [Canal X]"), 1)
        self.assertEqual(saida.count("⚠ Artistas [TheFatRat & NEFFEX]"), 1)
        self.assertIn("correção guardada: Canal X → Linkin Park. Nas outras 3 faixa(s)", saida)
        self.assertIn("Canal X → Linkin Park  (corrigido em outra faixa; vai no --apply)", saida)
        self.assertIn("4 com o artista para o --apply", saida)
        self.assertIn("python plpython.py review --apply", saida)
        self.assertEqual(self.faixa("aaaaaaaaaaa")["status"], "RESOLVIDO")
        self.assertEqual(self.faixa("bbbbbbbbbbb")["metadata"]["artists"], ["Canal X"])  # nada aplicado ainda

    def test_apply_confere_uma_por_uma(self):
        self.revisar_e_corrigir()
        saida = self.rodar(["review", "--apply"])
        fim = self.faixa("bbbbbbbbbbb")  # "Linkin Park" está no título do vídeo: aplica e identifica de novo
        self.assertEqual((fim["status"], fim["metadata"]["artists"], fim["metadata"]["album"]),
                         ("RESOLVIDO", ["Linkin Park"], "Hybrid Theory"))
        self.assertEqual(fim["manual_review"]["via"], "review --apply")
        for vid in ("ccccccccccc", "ddddddddddd"):  # nada confirma: o canal publica vários artistas
            self.assertEqual(self.faixa(vid)["metadata"]["artists"], ["Canal X"])
        self.assertIn("✗ Canal X — Thunder: não apliquei Canal X → Linkin Park: nada nesta faixa confirma", saida)
        nunca = self.faixa("fffffffffff")  # mesmos nomes, só separados: aplica; sem álbum continua na lista
        self.assertEqual((nunca["metadata"]["artists"], nunca["status"]), (["TheFatRat", "NEFFEX"], "OBSERVAR"))
        self.assertIn("continua na lista (falta: Álbum)", saida)
        self.assertIn("Resumo: 1 saíram da lista, 1 corrigidas em parte (continuam na lista), 2 não confirmadas", saida)

        # No próximo review, as não confirmadas mostram a correção como sugestão.
        saida = self.rodar(["review", "-a", "-q", "Numb"], [":ok", ":sug"])
        self.assertIn("sugestão de outra faixa: Linkin Park — :sug", saida)
        numb = self.faixa("ccccccccccc")
        self.assertEqual((numb["status"], numb["metadata"]["artists"]), ("RESOLVIDO", ["Linkin Park"]))

    def test_correcoes_diferentes_para_o_mesmo_artista_nao_propagam(self):
        db = pltube.abrir_banco_estado(os.path.join("sql", "downloads.sqlite3"), os.path.join("sql", "musicas.sqlite3"))
        ca.registrar(db, ["Canal X"], ["Linkin Park"], "youtube:aaaaaaaaaaa")
        ca.registrar(db, ["Canal X"], ["Imagine Dragons"], "youtube:ddddddddddd")
        db.close()
        saida = self.rodar(["review", "--apply"])
        self.assertIn("já foi corrigido para nomes diferentes: Linkin Park / Imagine Dragons", saida)
        self.assertEqual(self.faixa("bbbbbbbbbbb")["metadata"]["artists"], ["Canal X"])

    def test_sem_correcoes_guardadas(self):
        self.assertIn("Nenhuma correção de artista guardada", self.rodar(["review", "--apply"]))

    def test_mesmos_nomes(self):
        self.assertTrue(ca.mesmos_nomes(["TheFatRat & NEFFEX"], ["thefatrat", "NEFFEX"]))
        self.assertTrue(ca.mesmos_nomes(["The Fat Rat"], ["TheFatRat"]))
        self.assertFalse(ca.mesmos_nomes(["TheFatRat & NEFFEX"], ["TheFatRat & RIELL"]))
        self.assertFalse(ca.mesmos_nomes(["Canal"], ["Canal X"]))


if __name__ == "__main__":
    unittest.main()
