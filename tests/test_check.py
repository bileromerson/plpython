"""check: só sinais fortes de erro mandam para a conferência; álbum/capa faltando não."""
import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import plpython
import pltube


def resultado(vid, titulo, artistas, status, observacoes, fonte="youtube_titulo_artista_primeiro", **extra):
    return {
        "video_id": vid, "status": status, "observations": observacoes, "pending_reasons": [],
        "metadata": {"id": vid, "title": titulo, "artists": artistas, "album": "", "isrc": ""},
        "provenance": {"title": fonte, "artists": fonte},
        "source_youtube": {"video_title": f"{', '.join(artistas)} - {titulo}", "uploader": "Canal Qualquer",
                           "duration": 200, "artists": [], "thumbnails": []},
        **extra,
    }


FAIXAS = (
    ("aaaaaaaaaaa", resultado("aaaaaaaaaaa", "Skyfall", ["Adele"], "OBSERVAR",
                              ["álbum não encontrado", "capa do álbum não encontrada; usando a thumbnail"],
                              fonte="musicbrainz")),
    ("bbbbbbbbbbb", resultado("bbbbbbbbbbb", "Overwhelmed", ["(Ryan Mack Remix)"], "RESOLVIDO", [])),
    ("ccccccccccc", resultado("ccccccccccc", "02. Instinct", ["INSTINCT"], "OBSERVAR",
                              ["não confirmado no MusicBrainz; título/artista deduzidos do título do vídeo"])),
    ("ddddddddddd", resultado("ddddddddddd", "Song (Lyrics)", ["Lyrics Hub"], "RESOLVIDO", [],
                              manual_review={"fields": [], "confirmed": True})),
    ("eeeeeeeeeee", resultado("eeeeeeeeeee", "Ed Sheeran", ["Bad Habits"], "RESOLVIDO", [])),
    ("fffffffffff", resultado("fffffffffff", "Divide", ["Ed Sheeran"], "RESOLVIDO", [], fonte="musicbrainz")),
)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        antigo = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, antigo)
        db = self.abrir()
        for vid, res in FAIXAS:
            pltube.salvar_resolucao(db, f"youtube:{vid}", res)
        db.close()

    def abrir(self):
        return pltube.abrir_banco_estado(os.path.join("sql", "downloads.sqlite3"), os.path.join("sql", "musicas.sqlite3"))

    def rodar(self, *argv):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertEqual(plpython.main(["check", *argv]), 0)
        return saida.getvalue()

    def status(self):
        db = self.abrir()
        try:
            return {vid: pltube.carregar_resolucao(db, f"youtube:{vid}")["result"] for vid, _ in FAIXAS}
        finally:
            db.close()

    def test_simula_sem_gravar(self):
        saida = self.rodar()
        self.assertIn("⚠ (Ryan Mack Remix) — Overwhelmed", saida)
        self.assertIn("✓ Adele — Skyfall", saida)
        self.assertIn("Nada foi gravado", saida)
        self.assertEqual(self.status()["bbbbbbbbbbb"]["status"], "RESOLVIDO")

    def test_grava_so_sinais_fortes(self):
        self.rodar("-y")
        r = self.status()
        self.assertEqual((r["aaaaaaaaaaa"]["status"], r["aaaaaaaaaaa"]["observations"]), ("RESOLVIDO", []))  # só álbum/capa
        self.assertEqual(r["bbbbbbbbbbb"]["status"], "OBSERVAR")
        self.assertIn("artista parece", r["bbbbbbbbbbb"]["observations"][0])
        self.assertIn("título com sobras", r["ccccccccccc"]["observations"][0])
        self.assertEqual(r["ddddddddddd"]["status"], "RESOLVIDO")  # você confirmou: não é questionado
        self.assertIn("invertidos", r["eeeeeeeeeee"]["observations"][0])  # "Ed Sheeran" é artista conhecido
        self.assertEqual(r["fffffffffff"]["status"], "RESOLVIDO")
        # o review pergunta só os campos apontados
        from comandos.review import campos_com_problema
        self.assertEqual(campos_com_problema(r["bbbbbbbbbbb"]), ["artists"])
        self.assertEqual(self.rodar().count("⚠"), 0)  # rodar de novo não muda nada

    def test_busca(self):
        saida = self.rodar("-q", "Instinct")
        self.assertIn("02. Instinct", saida)
        self.assertNotIn("Overwhelmed", saida)


if __name__ == "__main__":
    unittest.main()
