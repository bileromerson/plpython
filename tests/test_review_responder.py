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
        # B (deduzida + sem álbum): título, artistas e álbum em branco.
        # A (título diverge + sem álbum): só título e álbum são perguntados.
        codigo, saida = self.responder(["", "", "", ":db", "Random Access Memories"])
        self.assertEqual(codigo, 0, saida)
        self.assertIn("título diverge", saida)
        self.assertIn("YT Music: Get Lucky", saida)
        resolucoes, _ = self.banco()
        a, b = resolucoes["aaaaaaaaaaa"]["result"], resolucoes["bbbbbbbbbbb"]["result"]
        self.assertEqual(a["status"], "RESOLVIDO")
        self.assertEqual((a["metadata"]["title"], a["metadata"]["artists"], a["metadata"]["album"]),
                         ("Get Lucky", ["Daft Punk"], "Random Access Memories"))
        self.assertEqual(b["status"], "OBSERVAR")  # em branco: continua na lista
        self.assertEqual(b["metadata"]["album"], "")
        self.assertIn("1 saíram da lista, 0 corrigidas em parte (continuam na lista), 1 sem resposta", saida)
        self.assertFalse(os.path.lexists(os.path.join(self.songs, "_observar", "musica [aaaaaaaaaaa].mp3")))
        self.assertTrue(os.path.lexists(os.path.join(self.songs, "_observar", "musica [bbbbbbbbbbb].mp3")))
        with open(os.path.join("playlists", "lista.m3u"), encoding="utf-8") as m3u:
            self.assertIn("Get Lucky", m3u.read())

    def test_corrigir_so_um_campo_nao_tira_da_lista(self):
        # A: corrige o título e deixa o álbum em branco → salva o título, continua na lista.
        codigo, saida = self.responder([":s", ":db", ""])
        self.assertEqual(codigo, 0, saida)
        a = self.banco()[0]["aaaaaaaaaaa"]["result"]
        self.assertEqual(a["status"], "OBSERVAR")
        self.assertEqual(a["metadata"]["title"], "Get Lucky")
        self.assertEqual(a["provenance"]["title"], "manual")
        motivos = " | ".join(a["pending_reasons"] + a["observations"])
        self.assertNotIn("título diverge", motivos)       # resolvido: sai dos motivos
        self.assertIn("álbum não encontrado", motivos)    # pendente: continua
        self.assertNotIn("revisão parcial", motivos)
        self.assertIn("continua na lista (falta: Álbum)", saida)
        self.assertTrue(os.path.lexists(os.path.join(self.songs, "_observar", "musica [aaaaaaaaaaa].mp3")))

        # Na próxima revisão só o álbum é perguntado; ":ok" no álbum vazio resolve.
        codigo, saida = self.responder([":s", ":ok"])
        self.assertNotIn("⚠ Título [Get Lucky", saida)    # A: título já corrigido, não pergunta de novo
        self.assertIn("  Título    ✓ Get Lucky", saida)
        self.assertIn("⚠ Álbum [—]: ", saida)
        a = self.banco()[0]["aaaaaaaaaaa"]["result"]
        self.assertEqual((a["status"], a["metadata"]["title"], a["metadata"]["album"]), ("RESOLVIDO", "Get Lucky", ""))
        self.assertEqual(a["manual_review"]["fields"], ["title", "album"])

    def test_trocar_artista_move_o_mp3_e_cria_atalho(self):
        self.responder([":s", "", "Artista Real | Convidado", ""], "--all-fields")
        resolucoes, caminhos = self.banco()
        novo = os.path.join(self.songs, "Artista Real", "musica [aaaaaaaaaaa].mp3")
        self.assertTrue(os.path.isfile(novo) and not os.path.islink(novo))
        self.assertFalse(os.path.exists(self.mp3["aaaaaaaaaaa"]))
        self.assertEqual(resolucoes["aaaaaaaaaaa"]["audio_path"], novo)
        self.assertEqual(resolucoes["aaaaaaaaaaa"]["result"]["status"], "OBSERVAR")  # título/álbum em branco
        self.assertTrue(os.path.islink(os.path.join(self.songs, "Convidado", "musica [aaaaaaaaaaa].mp3")))
        with open(os.path.join("playlists", "lista.m3u"), encoding="utf-8") as m3u:
            self.assertIn(os.path.join("Artista Real", "musica [aaaaaaaaaaa].mp3"), m3u.read())

    def test_default_ok_aceita_branco_usa_banco_no_conflito_e_unknown_no_vazio(self):
        codigo, saida = self.responder(["", "", "", "", ""], "--default-ok")
        self.assertEqual(codigo, 0, saida)
        resolucoes, _ = self.banco()
        a, b = resolucoes["aaaaaaaaaaa"]["result"], resolucoes["bbbbbbbbbbb"]["result"]
        self.assertEqual((a["status"], b["status"]), ("RESOLVIDO", "RESOLVIDO"))
        self.assertEqual(a["metadata"]["title"], "Get Lucky")  # conflito → banco
        self.assertEqual(a["metadata"]["album"], "Random Access Memories")  # vazio, mas o banco tinha
        self.assertEqual(b["metadata"]["album"], "Unknown")  # vazio e sem banco → Unknown
        self.assertEqual(b["metadata"]["artists"], ["Canal Qualquer"])  # tinha valor: mantido

    def test_ok_por_campo_tudo_pular_e_sair(self):
        # B: ":ok" só no título, depois ":q" → salva o título como certo e sai; continua na lista.
        codigo, saida = self.responder([":ok", ":q"])
        resolucoes, _ = self.banco()
        b = resolucoes["bbbbbbbbbbb"]["result"]
        self.assertEqual((b["status"], b["metadata"]["title"]), ("OBSERVAR", "Song"))
        self.assertEqual(b["confidence"]["title"], "confirmada_manual")
        self.assertEqual(resolucoes["aaaaaaaaaaa"]["result"]["status"], "OBSERVAR")  # nem chegou nela
        self.assertIn("Saindo", saida)
        # ":tudo" confirma a faixa inteira como está.
        self.responder([":tudo", ":s"])
        b = self.banco()[0]["bbbbbbbbbbb"]["result"]
        self.assertEqual((b["status"], b["metadata"]["title"], b["metadata"]["artists"]),
                         ("RESOLVIDO", "Song", ["Canal Qualquer"]))

    def test_so_pergunta_os_campos_com_problema_e_legenda_fica_no_topo(self):
        codigo, saida = self.responder([":s", ":s"])
        self.assertIn("  Artistas  ✓ Daft Punk", saida)        # A: artista certo, não perguntado
        self.assertIn("  Título    ⚠ Get Lucky Remix    YT Music: Get Lucky", saida)
        self.assertIn("⚠ Título [Get Lucky Remix]: ", saida)
        self.assertNotIn("Artistas [Daft Punk]", saida)
        self.assertIn("  Artistas  ⚠ Canal Qualquer", saida)    # B: deduzida
        # A legenda vem antes de cada faixa, e os dados logo abaixo.
        self.assertEqual(saida.count(":tudo = a faixa toda está certa"), 2)
        self.assertLess(saida.index("faixa 1 de 2"), saida.index("https://youtu.be/bbbbbbbbbbb"))
        self.assertLess(saida.index("faixa 2 de 2"), saida.index("https://youtu.be/aaaaaaaaaaa"))

    def test_skip_album_nao_pergunta_nem_tira_da_lista(self):
        db = pltube.abrir_banco_estado(os.path.join("sql", "downloads.sqlite3"), os.path.join("sql", "musicas.sqlite3"))
        caminho = os.path.join(self.songs, "Zeca", "so album [ccccccccccc].mp3")
        os.makedirs(os.path.dirname(caminho))
        with open(caminho, "wb") as f:
            f.write(b"mp3")
        pltube.salvar_resolucao(db, "youtube:ccccccccccc", resolucao(
            "ccccccccccc", "OBSERVAR", "Só Álbum", ["Zeca"], "", ["álbum não encontrado"], {"title": "Só Álbum"}),
            caminho)
        db.commit()
        db.close()
        # B: título e artistas certos (:ok); A: título pelo banco. Álbum ignorado nas duas; C nem aparece.
        codigo, saida = self.responder([":ok", ":ok", ":db"], "--skip", "album")
        self.assertEqual(codigo, 0, saida)
        self.assertNotIn("⚠ Álbum", saida)
        self.assertNotIn("ccccccccccc", saida)
        self.assertIn("Ignorando nesta revisão (--skip): Álbum", saida)
        self.assertIn("falta: Álbum (ignorado)", saida)
        self.assertIn("1 puladas por só terem problema no que foi ignorado", saida)
        resolucoes, _ = self.banco()
        a, b = resolucoes["aaaaaaaaaaa"]["result"], resolucoes["bbbbbbbbbbb"]["result"]
        self.assertEqual((a["status"], a["metadata"]["title"], a["metadata"]["album"]), ("OBSERVAR", "Get Lucky", ""))
        self.assertEqual(b["status"], "OBSERVAR")
        # Da próxima vez, com --skip album, nenhuma das três aparece (só falta o álbum).
        codigo, saida = self.responder([], "-x", "album")
        self.assertIn("3 puladas", saida)

    def test_skip_campo_desconhecido(self):
        with contextlib.redirect_stderr(io.StringIO()) as erro:
            self.assertEqual(plpython.main(["review", "-a", "-x", "letra"]), 2)
        self.assertIn("campo desconhecido", erro.getvalue())

    def test_cores_so_no_terminal(self):
        with patch.dict(os.environ, {"FORCE_COLOR": "1"}):
            _, colorido = self.responder([":s", ":s"])
        self.assertIn("\033[1;33m⚠\033[0m", colorido)
        self.assertIn("\033[1;33m[OBSERVAR]\033[0m", colorido)
        self.assertIn("\033[35mYT Music: Get Lucky\033[0m", colorido)
        _, simples = self.responder([":s", ":s"])
        self.assertNotIn("\033[", simples)

    def test_virgula_mostra_como_entendeu_e_aspas_ou_barra_corrigem(self):
        # B: título em branco; artistas "Anitta, Kevinho" → confirma; álbum em branco.
        # A: título em branco; álbum em branco.
        codigo, saida = self.responder(["", "Anitta, Kevinho", "", "", "", ""])
        self.assertEqual(codigo, 0, saida)
        self.assertIn("→ 2 artistas: Anitta | Kevinho", saida)
        b = self.banco()[0]["bbbbbbbbbbb"]["result"]
        self.assertEqual(b["metadata"]["artists"], ["Anitta", "Kevinho"])
        self.assertEqual(b["status"], "OBSERVAR")  # título e álbum continuam pendentes

    def test_digitar_de_novo_com_barra(self):
        codigo, saida = self.responder(["", "Fulano & Beltrano", "Fulano | Beltrano", "", "", ""])
        self.assertIn("→ 2 artistas: Fulano | Beltrano", saida)  # " & " com espaços já separa
        self.assertEqual(self.banco()[0]["bbbbbbbbbbb"]["result"]["metadata"]["artists"], ["Fulano", "Beltrano"])

    def test_inv_troca_titulo_e_artista(self):
        # B: ":inv" no título → título "Canal Qualquer", artista "Song"; álbum ":ok" → sai da lista.
        codigo, saida = self.responder([":inv", ":ok", ":s"])
        self.assertIn("↔ título: Canal Qualquer · artistas: Song", saida)
        self.assertNotIn("⚠ Artistas [", saida.split("faixa 2 de 2")[0])  # não pergunta de novo
        b = self.banco()[0]["bbbbbbbbbbb"]["result"]
        self.assertEqual((b["metadata"]["title"], b["metadata"]["artists"], b["status"]),
                         ("Canal Qualquer", ["Song"], "RESOLVIDO"))

    def test_mapa_de_motivos_para_campos(self):
        from comandos.review import campos_com_problema
        self.assertEqual(campos_com_problema({"observations": ["artista diverge: YouTube='x' / MusicBrainz='y'"]}),
                         ["artists"])
        self.assertEqual(campos_com_problema({"pending_reasons": ["ISRC não confirmado pelo MusicBrainz"]}), ["isrc"])
        self.assertEqual(campos_com_problema({"observations": ["capa do álbum não encontrada; usando a thumbnail"]}),
                         ["album", "cover"])

    def test_default_ok_exige_answer(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(plpython.main(["review", "--default-ok"]), 2)


if __name__ == "__main__":
    unittest.main()
