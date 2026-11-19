import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import plpython
import pltube


def resolucao(vid, status, titulo, artistas, album, observacoes, youtube, candidato=None):
    return {
        "video_id": vid, "status": status, "observations": observacoes, "pending_reasons": [],
        "metadata": {"id": vid, "title": titulo, "artists": artistas, "album": album, "isrc": ""},
        "provenance": {"title": "youtube_artista", "artists": "youtube_artista"},
        "source_youtube": youtube,
        "candidates": [candidato] if candidato else [],
    }


class ResponderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.antigo = os.getcwd()
        os.chdir(self.temp.name)
        self.songs = os.path.join(self.temp.name, "songs")
        db = pltube.abrir_banco_estado(os.path.join("sql", "downloads.sqlite3"), os.path.join("sql", "musicas.sqlite3"))
        self.mp3 = {}
        for vid, pasta in (("aaaaaaaaaaa", "Daft Punk"), ("bbbbbbbbbbb", "Canal Qualquer")):
            caminho = os.path.join(self.songs, pasta, f"musica [{vid}].mp3")
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            with open(caminho, "wb") as f:
                f.write(b"mp3")
            self.mp3[vid] = caminho
            os.makedirs(os.path.join(self.songs, "_observar"), exist_ok=True)
            os.symlink(caminho, os.path.join(self.songs, "_observar", os.path.basename(caminho)))
            db.execute("INSERT INTO playlist_items (csv_path, row_index, video_key, title, artist, duration, path) "
                       "VALUES ('lista.csv', ?, ?, 'velho', 'velho', 200, ?)",
                       (0 if vid.startswith("a") else 1, f"youtube:{vid}", caminho))
        pltube.salvar_resolucao(db, "youtube:aaaaaaaaaaa", resolucao(
            "aaaaaaaaaaa", "OBSERVAR", "Get Lucky Remix", ["Daft Punk"], "",
            ["título diverge: YouTube='Get Lucky Remix' / MusicBrainz='Get Lucky'", "álbum não encontrado"],
            {"title": "Get Lucky Remix", "artists": ["Daft Punk"]},
            {"title": "Get Lucky", "artists": ["Daft Punk", "Pharrell Williams"], "isrcs": [],
             "releases": [{"title": "Random Access Memories"}]}), self.mp3["aaaaaaaaaaa"])
        pltube.salvar_resolucao(db, "youtube:bbbbbbbbbbb", resolucao(
            "bbbbbbbbbbb", "OBSERVAR", "Song", ["Canal Qualquer"], "",
            ["não confirmado no MusicBrainz; título/artista deduzidos do nome do canal", "álbum não encontrado"],
            {"title": "Song", "artists": [], "uploader": "Canal Qualquer"}), self.mp3["bbbbbbbbbbb"])
        db.commit()
        db.close()
        patcher = patch.object(pltube, "aplicar_tags_hibridas", return_value=True)
        self.tags = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        os.chdir(self.antigo)
        self.temp.cleanup()

    def responder(self, respostas, *extra):
        """A lista sai ordenada por artista: primeiro 'Canal Qualquer' (B), depois 'Daft Punk' (A)."""
        saida, fila = io.StringIO(), list(respostas)

        def entrada(pergunta):
            saida.write(pergunta + "\n")
            return fila.pop(0)

        with patch("builtins.input", side_effect=entrada), contextlib.redirect_stdout(saida):
            codigo = plpython.main(["review", "--answer", *extra])
        return codigo, saida.getvalue()

    def banco(self):
        db = pltube.abrir_banco_estado(os.path.join("sql", "downloads.sqlite3"), os.path.join("sql", "musicas.sqlite3"))
        try:
            return {vid: pltube.carregar_resolucao(db, f"youtube:{vid}") for vid in self.mp3}, \
                dict(db.execute("SELECT video_key, path FROM playlist_items").fetchall())
        finally:
            db.close()

    def test_so_muda_o_que_foi_digitado_e_branco_fica_na_lista(self):
        # faixa B: tudo em branco; faixa A: título do banco, artistas mantidos, álbum digitado
        codigo, saida = self.responder(["", "", "", ":db", "", "Random Access Memories"])
        self.assertEqual(codigo, 0, saida)
        self.assertIn("título diverge", saida)
        self.assertIn("banco: Get Lucky", saida)
        resolucoes, _ = self.banco()
        a, b = resolucoes["aaaaaaaaaaa"]["result"], resolucoes["bbbbbbbbbbb"]["result"]
        self.assertEqual(a["status"], "RESOLVIDO")
        self.assertEqual((a["metadata"]["title"], a["metadata"]["artists"], a["metadata"]["album"]),
                         ("Get Lucky", ["Daft Punk"], "Random Access Memories"))
        self.assertEqual(b["status"], "OBSERVAR")  # em branco: continua na lista
        self.assertEqual(b["metadata"]["album"], "")
        self.assertIn("1 conferida(s)/corrigida(s), 1 mantida(s)", saida)
        self.assertFalse(os.path.lexists(os.path.join(self.songs, "_observar", "musica [aaaaaaaaaaa].mp3")))
        self.assertTrue(os.path.lexists(os.path.join(self.songs, "_observar", "musica [bbbbbbbbbbb].mp3")))
        with open(os.path.join("playlists", "lista.m3u"), encoding="utf-8") as m3u:
            self.assertIn("Get Lucky", m3u.read())

    def test_trocar_artista_move_o_mp3_e_cria_atalho(self):
        self.responder([":s", "", "Artista Real | Convidado", ""])
        resolucoes, caminhos = self.banco()
        novo = os.path.join(self.songs, "Artista Real", "musica [aaaaaaaaaaa].mp3")
        self.assertTrue(os.path.isfile(novo) and not os.path.islink(novo))
        self.assertFalse(os.path.exists(self.mp3["aaaaaaaaaaa"]))
        self.assertEqual(resolucoes["aaaaaaaaaaa"]["audio_path"], novo)
        self.assertTrue(os.path.islink(os.path.join(self.songs, "Convidado", "musica [aaaaaaaaaaa].mp3")))
        with open(os.path.join("playlists", "lista.m3u"), encoding="utf-8") as m3u:
            self.assertIn(os.path.join("Artista Real", "musica [aaaaaaaaaaa].mp3"), m3u.read())

    def test_default_ok_aceita_branco_usa_banco_no_conflito_e_unknown_no_vazio(self):
        codigo, saida = self.responder(["", "", "", "", "", ""], "--default-ok")
        self.assertEqual(codigo, 0, saida)
        resolucoes, _ = self.banco()
        a, b = resolucoes["aaaaaaaaaaa"]["result"], resolucoes["bbbbbbbbbbb"]["result"]
        self.assertEqual((a["status"], b["status"]), ("RESOLVIDO", "RESOLVIDO"))
        self.assertEqual(a["metadata"]["title"], "Get Lucky")  # conflito → banco
        self.assertEqual(a["metadata"]["album"], "Random Access Memories")  # vazio, mas o banco tinha
        self.assertEqual(b["metadata"]["album"], "Unknown")  # vazio e sem banco → Unknown
        self.assertEqual(b["metadata"]["artists"], ["Canal Qualquer"])  # tinha valor: mantido

    def test_atalhos_ok_pular_e_sair(self):
        codigo, saida = self.responder([":ok", ":q"])
        resolucoes, _ = self.banco()
        self.assertEqual(resolucoes["bbbbbbbbbbb"]["result"]["status"], "RESOLVIDO")  # :ok confirma como está
        self.assertEqual(resolucoes["bbbbbbbbbbb"]["result"]["metadata"]["title"], "Song")
        self.assertEqual(resolucoes["aaaaaaaaaaa"]["result"]["status"], "OBSERVAR")  # :q parou antes
        self.assertIn("Saindo", saida)

    def test_default_ok_exige_answer(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(plpython.main(["review", "--default-ok"]), 2)


if __name__ == "__main__":
    unittest.main()
