import contextlib
import csv
import glob
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import plpython
import pltube
import sincronizar_playlists as sp
from spotify_conta import COLUNAS


class ChavesTests(unittest.TestCase):
    def test_o_que_nao_muda_a_musica_e_ignorado(self):
        mesma = sp.chave_titulo
        self.assertEqual(mesma("Bohemian Rhapsody - Remastered 2011"), mesma("Bohemian Rhapsody"))
        self.assertEqual(mesma("Song (feat. Alguém)"), mesma("Song"))
        self.assertEqual(mesma("Song (with Alguém)"), mesma("Song"))
        self.assertEqual(mesma("Song (Radio Edit)"), mesma("Song"))
        self.assertEqual(mesma("Song - 2009 Remaster"), mesma("Song"))
        self.assertEqual(mesma("Sunflower - Spider-Man: Into the Spider-Verse"), mesma("Sunflower"))
        self.assertEqual(mesma("Song (Official Music Video)"), mesma("Song"))

    def test_outra_gravacao_continua_diferente(self):
        mesma = sp.chave_titulo
        for variante in ("Song (Live)", "Song - Ao Vivo", "Song - Dj Snake Remix", "Song (Acoustic)",
                         "Song (Sir Meow Remix)", "Song - Extended Mix", "Song (Instrumental)"):
            self.assertNotEqual(mesma(variante), mesma("Song"), variante)
        self.assertNotEqual(mesma("Love Story (Taylor's Version)"), mesma("Love Story"))
        self.assertEqual(sp.chave_nucleo("Song (Live)"), sp.chave_nucleo("Song"))

    def test_aproximado_so_aceita_erro_de_digitacao(self):
        self.assertGreaterEqual(sp.quase_igual("somebody told me", "somebody told mee"), 0.9)
        self.assertEqual(sp.quase_igual("symphony no 5", "symphony no 9"), 0.0)
        self.assertEqual(sp.quase_igual("under the bridge", "under the bridge again"), 0.0)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raiz = self.temp.name
        antigo = os.getcwd()
        os.chdir(self.raiz)
        self.addCleanup(os.chdir, antigo)
        os.makedirs("playlists")
        self.sql = os.path.join(self.raiz, "sql")
        conexao = pltube.abrir_banco_estado(os.path.join(self.sql, "downloads.sqlite3"),
                                            os.path.join(self.sql, "musicas.sqlite3"))
        self.conexao = conexao
        self.mp3 = {}
        self.musica("vk1", "Queen", "Bohemian Rhapsody", ["Queen"], isrc="GBUM71029604")
        self.musica("vk2", "Coldplay", "Yellow", ["Coldplay"])
        self.musica("vk3", "The Killers", "Somebody Told Mee", ["The Killers"])  # erro de digitação
        self.musica("vk4", "Banda Antiga", "Old Song", ["Banda Antiga"])
        self.musica("vk5", "Canal Qualquer", "Numb (Official Video)", ["Canal Qualquer"])  # uploader
        self.musica("vk6", "Post Malone", "Sunflower", ["Post Malone", "Swae Lee"])
        self.musica("vk7", "Lionel Richie", "Hello", ["Lionel Richie"])
        self.musica("vk8", "Banda X", "Song (Live)", ["Banda X"])
        conexao.commit()
        conexao.close()
        # MP3 fora do banco (achado pela pasta) e atalho de artista secundário (ignorado).
        os.makedirs("songs/Daft Punk")
        with open("songs/Daft Punk/One More Time.mp3", "wb") as arquivo:
            arquivo.write(b"ID3")
        os.makedirs("songs/Swae Lee")
        os.symlink(os.path.relpath(self.mp3["vk6"], "songs/Swae Lee"), "songs/Swae Lee/Sunflower.mp3")

        self.m3u("Rock.m3u", ["vk2", "vk4", "vk5", "vk1"], extra=["../songs/Sumiu/Apagada.mp3"])
        self.spotify({
            "Rock 🎸": [("Queen", "Bohemian Rhapsody - Remastered 2011", "GBUM71029604"),
                       ("The Killers", "Somebody Told Me", ""),
                       ("Linkin Park", "Numb", ""),
                       ("Coldplay", "Yellow", ""),
                       ("Adele", "Hello", "")],
            "Chill": [("Post Malone; Swae Lee", "Sunflower - Spider-Man: Into the Spider-Verse", ""),
                      ("Daft Punk", "One More Time", ""),
                      ("Linkin Park", "Numb", ""),
                      ("Banda X", "Song", "")],
            "Vazia": [("Artista Z", "Nada Baixado", "")],
        })

    def musica(self, chave, pasta, titulo, artistas, isrc=""):
        caminho = os.path.join(self.raiz, "songs", pasta, titulo + ".mp3")
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "wb") as arquivo:
            arquivo.write(b"ID3")
        relativo = os.path.relpath(caminho, self.sql)
        self.conexao.execute(
            "INSERT INTO video_cache (video_key, source_url, status, title, artists_json, duration, path) "
            "VALUES (?, ?, 'OK', ?, ?, 200, ?)", (chave, f"https://youtu.be/{chave}", titulo, "[]", relativo))
        self.conexao.execute(
            "INSERT INTO music.music_tracks (video_key, title, artists_json, isrc, duration) VALUES (?, ?, ?, ?, 200)",
            (chave, titulo, json.dumps(artistas), isrc))
        self.mp3[chave] = caminho

    def m3u(self, nome, chaves, extra=()):
        linhas = ["#EXTM3U"]
        for chave in chaves:
            linhas += [f"#EXTINF:200,{chave}", os.path.relpath(self.mp3[chave], "playlists")]
        for caminho in extra:
            linhas += ["#EXTINF:100,Sumiu - Apagada", caminho]
        with open(os.path.join("playlists", nome), "w", encoding="utf-8") as arquivo:
            arquivo.write("\n".join(linhas) + "\n")

    def spotify(self, playlists):
        with open("spotify.csv", "w", encoding="utf-8-sig", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS)
            escritor.writeheader()
            for numero, (nome, faixas) in enumerate(playlists.items()):
                for posicao, (artistas, titulo, isrc) in enumerate(faixas, 1):
                    escritor.writerow({"playlist": nome, "playlist_id": f"p{numero}", "position": posicao,
                                       "track_id": f"t{abs(hash((artistas, titulo))) % 10**8}", "isrc": isrc,
                                       "artists": artistas, "title": titulo, "duration_ms": 201000})

    def rodar(self, *argv):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(saida), \
                patch.dict(os.environ, {"NO_COLOR": "1"}):
            codigo = plpython.main(["sync", *argv])
        self.assertEqual(codigo, 0, saida.getvalue())
        return saida.getvalue()

    def caminhos(self, nome):
        with open(os.path.join("playlists", nome), encoding="utf-8") as arquivo:
            return [l.strip() for l in arquivo if l.strip() and not l.startswith("#")]

    def faltando(self):
        with open("nao_baixadas.csv", encoding="utf-8-sig", newline="") as arquivo:
            return {(l["artists"], l["title"]): l for l in csv.DictReader(arquivo)}

    def rel(self, chave):
        return os.path.relpath(self.mp3[chave], "playlists")

    def test_simula_sem_gravar_m3u(self):
        with open("playlists/Rock.m3u", encoding="utf-8") as arquivo:
            antes = arquivo.read()
        texto = self.rodar()
        with open("playlists/Rock.m3u", encoding="utf-8") as arquivo:
            self.assertEqual(arquivo.read(), antes)
        self.assertFalse(os.path.exists("playlists/Chill.m3u"))
        self.assertIn("Biblioteca: 9 músicas", texto)
        self.assertIn("Rock 🎸 → playlists/Rock.m3u: +1, −2, ordem do Spotify (4 de 5 baixadas)", texto)
        self.assertIn("+ The Killers — Somebody Told Mee", texto)
        self.assertIn("− Banda Antiga — Old Song  (saiu da playlist do Spotify)", texto)
        self.assertIn("(o arquivo não existe)", texto)
        self.assertIn("? Linkin Park — Numb  ↔  songs/Canal Qualquer/Numb (Official Video).mp3", texto)
        self.assertIn("Chill → playlists/Chill.m3u (nova): 3 de 4 baixadas", texto)
        self.assertIn("Vazia: a música não está baixada (M3U não criada)", texto)
        self.assertIn("Simulação", texto)
        faltando = self.faltando()
        self.assertEqual(faltando[("Adele", "Hello")]["status"], "não baixada")
        self.assertIn("mesmo título com outro artista", faltando[("Adele", "Hello")]["obs"])
        self.assertIn("outra versão: Banda X — Song (Live)", faltando[("Banda X", "Song")]["obs"])
        self.assertIn("music.youtube.com/search?q=Adele+Hello", faltando[("Adele", "Hello")]["youtube_music"])
        numb = faltando[("Linkin Park", "Numb")]
        self.assertEqual((numb["status"], numb["playlists"]), ("conferir", "Rock 🎸; Chill"))

    def test_grava_na_ordem_do_spotify_com_copia_e_depois_fica_estavel(self):
        texto = self.rodar("-y")
        self.assertIn("2 M3Us gravadas", texto)
        self.assertEqual(self.caminhos("Rock.m3u"),
                         [self.rel("vk1"), self.rel("vk3"), self.rel("vk5"), self.rel("vk2")])
        self.assertEqual(self.caminhos("Chill.m3u"), [
            self.rel("vk6"), os.path.join("..", "songs", "Daft Punk", "One More Time.mp3"), self.rel("vk5"),
        ])
        with open("playlists/Chill.m3u", encoding="utf-8") as arquivo:
            self.assertIn("#EXTINF:200,Post Malone, Swae Lee - Sunflower", arquivo.read())
        self.assertFalse(os.path.exists("playlists/Vazia.m3u"))
        copias = glob.glob("sql/m3u_backup/*/Rock.m3u")
        self.assertEqual(len(copias), 1)
        with open(copias[0], encoding="utf-8") as arquivo:
            self.assertIn("Old Song", arquivo.read())

        de_novo = self.rodar("-y")
        self.assertIn("Rock 🎸: sem mudanças (4 de 5 baixadas)", de_novo)
        self.assertIn("Chill: sem mudanças (3 de 4 baixadas)", de_novo)
        self.assertIn("Resumo: 0 M3Us mudam, 3 sem mudanças", de_novo)

    def test_keep_extra_mantem_o_que_saiu_menos_arquivo_inexistente(self):
        self.rodar("-y", "--keep-extra", "Rock")
        caminhos = self.caminhos("Rock.m3u")
        self.assertEqual(caminhos[-1], self.rel("vk4"))
        self.assertNotIn("../songs/Sumiu/Apagada.mp3", caminhos)
        self.assertFalse(os.path.exists("playlists/Chill.m3u"))  # filtro por nome

    def test_sem_spotify_csv_explica(self):
        os.remove("spotify.csv")
        saida = io.StringIO()
        with contextlib.redirect_stderr(saida):
            self.assertEqual(plpython.main(["sync"]), 1)
        self.assertIn("plpython.py spotify", saida.getvalue())


if __name__ == "__main__":
    unittest.main()
