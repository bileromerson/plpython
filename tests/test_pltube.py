import csv
import os
import tempfile
import unittest
from unittest.mock import patch

import pltube
import spotify_playlist_viewer
import sincronizar_biblioteca


class FakeYoutubeDL:
    def __init__(self, options=None):
        self.options = options or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def extract_info(self, url, download=False):
        return {
            "title": "Música de Teste",
            "artists": ["Artista Um", "Artista Dois"],
            "duration": 123,
            "id": "abc123",
        }

    def download(self, urls):
        template = self.options["outtmpl"].replace("%(ext)s", "mp3")
        with open(template, "wb") as audio:
            audio.write(b"fake audio bytes")


class PltubeTests(unittest.TestCase):
    def test_migracao_em_simulacao_nao_altera_arquivos(self):
        with tempfile.TemporaryDirectory() as temp:
            artist_dir = os.path.join(temp, "Artista Um")
            os.makedirs(artist_dir)
            song_path = os.path.join(artist_dir, "faixa.mp3")
            with open(song_path, "wb") as audio:
                audio.write(b"audio")

            resultado = sincronizar_biblioteca.sincronizar(temp, aplicar=False)

            self.assertEqual(resultado["encontradas"], 1)
            self.assertTrue(os.path.isfile(song_path))
            self.assertFalse(os.path.exists(os.path.join(temp, "_library")))

    def test_migracao_centraliza_duplicatas_e_preserva_caminhos_antigos(self):
        with tempfile.TemporaryDirectory() as temp:
            originais = []
            for artist in ("Artista A", "Artista B"):
                artist_dir = os.path.join(temp, artist)
                os.makedirs(artist_dir)
                song_path = os.path.join(artist_dir, "faixa.mp3")
                with open(song_path, "wb") as audio:
                    audio.write(b"same audio")
                originais.append(song_path)

            resultado = sincronizar_biblioteca.sincronizar(temp, aplicar=True)

            biblioteca = os.path.join(temp, "_library")
            arquivos = os.listdir(biblioteca)
            self.assertEqual(len(arquivos), 1)
            canonical = os.path.join(biblioteca, arquivos[0])
            self.assertEqual(resultado["movidas"], 1)
            self.assertEqual(resultado["deduplicadas"], 1)
            for caminho in originais:
                self.assertTrue(os.path.lexists(caminho))
                self.assertEqual(os.path.realpath(caminho), os.path.realpath(canonical))

            novamente = sincronizar_biblioteca.sincronizar(temp, aplicar=True)
            self.assertEqual(novamente["encontradas"], 0)

    def test_extrair_id_de_playlist_spotify_de_url_uri_e_id(self):
        playlist_id = "3cEYpjA9oz9GiPac4AsH4n"
        self.assertEqual(
            spotify_playlist_viewer.extrair_playlist_id(
                f"https://open.spotify.com/intl-pt/playlist/{playlist_id}?si=abc"
            ),
            playlist_id,
        )
        self.assertEqual(
            spotify_playlist_viewer.extrair_playlist_id(f"spotify:playlist:{playlist_id}"),
            playlist_id,
        )
        self.assertEqual(spotify_playlist_viewer.extrair_playlist_id(playlist_id), playlist_id)

    def test_rejeitar_link_que_nao_seja_playlist_spotify(self):
        with self.assertRaises(ValueError):
            spotify_playlist_viewer.extrair_playlist_id("https://open.spotify.com/album/123")

    def test_mesmo_video_em_dois_csvs_usa_cache_sem_nova_consulta(self):
        with tempfile.TemporaryDirectory() as temp:
            urls = {
                "um.csv": "https://www.youtube.com/watch?v=abc123&utm_source=a",
                "dois.csv": "https://youtu.be/abc123?si=b",
            }
            for nome, url in urls.items():
                with open(os.path.join(temp, nome), "w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["id", "titulo", "url"])
                    writer.writerow(["abc123", "Faixa cacheada", url])

            songs, playlists = os.path.join(temp, "songs"), os.path.join(temp, "playlists")
            state = None

            def download(url, artists, title, video_id, csv_file):
                folder = os.path.join(songs, artists[0])
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, f"{title} [{video_id}].mp3")
                with open(path, "wb") as audio:
                    audio.write(b"audio")
                return path

            meta = {"title": "Faixa cacheada", "artists": ["Artista"], "duration": 20, "id": "abc123"}
            with patch.multiple(
                pltube, BASE_DIR=temp, SONGS_DIR=songs, PLAYLISTS_DIR=playlists,
                LOG_FILE=os.path.join(temp, "Logs.txt"), ROW=2, ARTIST_LINKS=False,
            ):
                pltube.criar_diretorios()
                state = pltube.abrir_banco_estado(os.path.join(temp, "state.sqlite3"))
                try:
                    with (
                        patch.object(pltube, "extrair_metadados", return_value=meta) as extract,
                        patch.object(pltube, "baixar_musica", side_effect=download) as download_mock,
                    ):
                        pltube.processar_csv("um.csv", estado=state, indice_local={})
                        pltube.processar_csv("dois.csv", estado=state, indice_local={})
                        pltube.processar_csv("um.csv", estado=state, indice_local={})
                    self.assertEqual(extract.call_count, 1)
                    self.assertEqual(download_mock.call_count, 1)
                    for nome in ("um", "dois"):
                        with open(os.path.join(playlists, f"{nome}.m3u"), encoding="utf-8") as handle:
                            self.assertIn("Faixa cacheada", handle.read())
                finally:
                    state.close()

    def test_download_antigo_com_id_no_nome_e_reutilizado_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "lista.csv"), "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "titulo", "url"])
                writer.writerow(["abc123", "Faixa antiga", "https://youtube.com/watch?v=abc123"])

            songs, playlists = os.path.join(temp, "songs"), os.path.join(temp, "playlists")
            old_folder = os.path.join(songs, "Artista Antigo")
            os.makedirs(old_folder)
            old_path = os.path.join(old_folder, "Faixa antiga [abc123].mp3")
            with open(old_path, "wb") as audio:
                audio.write(b"already downloaded")

            with patch.multiple(
                pltube, BASE_DIR=temp, SONGS_DIR=songs, PLAYLISTS_DIR=playlists,
                LOG_FILE=os.path.join(temp, "Logs.txt"), ROW=2, ARTIST_LINKS=False,
            ):
                pltube.criar_diretorios()
                state = pltube.abrir_banco_estado(os.path.join(temp, "state.sqlite3"))
                try:
                    with (
                        patch.object(pltube, "extrair_metadados") as extract,
                        patch.object(pltube, "baixar_musica") as download,
                    ):
                        pltube.processar_csv(
                            "lista.csv", estado=state,
                            indice_local=pltube.indexar_musicas_locais(),
                        )
                    extract.assert_not_called()
                    download.assert_not_called()
                    with open(os.path.join(playlists, "lista.m3u"), encoding="utf-8") as handle:
                        self.assertIn("Faixa antiga", handle.read())
                finally:
                    state.close()

    def test_retoma_apos_linha_checkpointada_sem_reconsultar_a_anterior(self):
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "lista.csv"), "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "titulo", "url"])
                writer.writerow(["1", "Primeira", "https://youtube.com/watch?v=abc123"])
                writer.writerow(["2", "Segunda", "https://youtube.com/watch?v=def456"])

            songs, playlists = os.path.join(temp, "songs"), os.path.join(temp, "playlists")
            chamadas = {"abc123": 0, "def456": 0}
            downloads = []

            def extract(url, csv_file):
                video_id = url.rsplit("=", 1)[1]
                chamadas[video_id] += 1
                if video_id == "def456" and chamadas[video_id] == 1:
                    raise pltube.BloqueioTemporarioYouTube("bloqueio simulado")
                return {"title": video_id, "artists": ["Artista"], "duration": 10, "id": video_id}

            def download(url, artists, title, video_id, csv_file):
                downloads.append(video_id)
                folder = os.path.join(songs, artists[0])
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, f"{title} [{video_id}].mp3")
                with open(path, "wb") as audio:
                    audio.write(video_id.encode())
                return path

            with patch.multiple(
                pltube, BASE_DIR=temp, SONGS_DIR=songs, PLAYLISTS_DIR=playlists,
                LOG_FILE=os.path.join(temp, "Logs.txt"), ROW=2, ARTIST_LINKS=False,
            ):
                pltube.criar_diretorios()
                state = pltube.abrir_banco_estado(os.path.join(temp, "state.sqlite3"))
                try:
                    with (
                        patch.object(pltube, "extrair_metadados", side_effect=extract),
                        patch.object(pltube, "baixar_musica", side_effect=download),
                    ):
                        self.assertFalse(
                            pltube.processar_csv("lista.csv", estado=state, indice_local={})
                        )
                        self.assertTrue(
                            pltube.processar_csv("lista.csv", estado=state, indice_local={})
                        )
                    self.assertEqual(chamadas, {"abc123": 1, "def456": 2})
                    self.assertEqual(downloads, ["abc123", "def456"])
                    with open(os.path.join(playlists, "lista.m3u"), encoding="utf-8") as handle:
                        self.assertEqual(handle.read().count("#EXTINF:"), 2)
                finally:
                    state.close()

    def test_normalizar_artistas_preserva_lista_e_nome_de_banda(self):
        self.assertEqual(
            pltube.normalizar_artistas({"artists": ["Artista Um", "Artista Dois"]}),
            ["Artista Um", "Artista Dois"],
        )
        self.assertEqual(
            pltube.normalizar_artistas({"artist": "Earth, Wind & Fire"}),
            ["Earth, Wind & Fire"],
        )
        self.assertEqual(
            pltube.normalizar_artistas({"artist": "Artista Um feat. Artista Dois"}),
            ["Artista Um", "Artista Dois"],
        )

    def test_nome_seguro_impede_separadores_de_caminho(self):
        resultado = pltube.nome_seguro("../pasta\u0000\nome")
        self.assertNotIn("/", resultado)
        self.assertNotIn("\\", resultado)
        self.assertNotIn("..", resultado)

    def test_link_criado_aponta_para_o_mesmo_arquivo(self):
        with tempfile.TemporaryDirectory() as temp:
            songs = os.path.join(temp, "songs")
            library = os.path.join(songs, "_library")
            os.makedirs(library)
            original = os.path.join(library, "track.mp3")
            with open(original, "wb") as audio:
                audio.write(b"audio")
            with patch.object(pltube, "SONGS_DIR", songs):
                self.assertTrue(pltube.criar_link_artista(original, "Artista Um"))
            link_path = os.path.join(songs, "Artista Um", "track.mp3")
            self.assertTrue(os.path.lexists(link_path))
            self.assertEqual(os.path.realpath(link_path), os.path.realpath(original))
            self.assertEqual(os.stat(link_path).st_ino, os.stat(original).st_ino)

    def test_opcao_links_processa_colaboracao_sem_duplicar_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            csv_path = os.path.join(temp, "playlist.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["titulo", "id", "url"])
                writer.writerow(["Música", "abc123", "https://youtube.com/watch?v=abc123"])

            songs = os.path.join(temp, "songs")
            playlists = os.path.join(temp, "playlists")
            with patch.multiple(
                pltube,
                BASE_DIR=temp,
                SONGS_DIR=songs,
                PLAYLISTS_DIR=playlists,
                LOG_FILE=os.path.join(temp, "Logs.txt"),
                ROW=2,
                ARTIST_LINKS=True,
                yt_dlp=type("FakeModule", (), {"YoutubeDL": FakeYoutubeDL}),
            ):
                pltube.criar_diretorios()
                pltube.processar_csv("playlist.csv")
                library = os.path.join(songs, "_library")
                self.assertEqual(len(os.listdir(library)), 1)
                original = os.path.join(library, os.listdir(library)[0])
                for artist in ("Artista Um", "Artista Dois"):
                    linked = os.path.join(songs, artist, os.path.basename(original))
                    self.assertTrue(os.path.lexists(linked))
                    self.assertEqual(os.path.realpath(linked), os.path.realpath(original))

                m3u = os.path.join(playlists, "playlist.m3u")
                with open(m3u, encoding="utf-8") as handle:
                    content = handle.read()
                self.assertIn("#EXTM3U", content)
                self.assertIn("Artista Um, Artista Dois - Música de Teste", content)
                self.assertIn("../songs/_library/", content)

                pltube.processar_csv("playlist.csv", append=True)
                with open(m3u, encoding="utf-8") as handle:
                    appended = handle.read()
                self.assertEqual(appended.count("#EXTINF:"), 1)

    def test_espera_progressivamente_quando_opcao_esta_ativa(self):
        tentativas = []

        def operacao_com_falha_inicial():
            tentativas.append(1)
            if len(tentativas) < 3:
                raise RuntimeError("HTTP Error 429: Too Many Requests")
            return "ok"

        with (
            patch.object(pltube, "ESPERAR_BLOQUEIO", True),
            patch.object(pltube, "registrar_erro"),
            patch("pltube.time.sleep") as dormir,
        ):
            resultado = pltube.executar_com_retentativas(
                "https://youtube.com/watch?v=x", "lista.csv", operacao_com_falha_inicial
            )

        self.assertEqual(resultado, "ok")
        self.assertEqual([chamada.args[0] for chamada in dormir.call_args_list], [30, 60])

    def test_bloqueio_sem_parametro_nao_espera_e_interrompe(self):
        def bloqueada():
            raise RuntimeError("HTTP Error 429: Too Many Requests")

        with (
            patch.object(pltube, "ESPERAR_BLOQUEIO", False),
            patch.object(pltube, "registrar_erro"),
            patch("pltube.time.sleep") as dormir,
        ):
            with self.assertRaises(pltube.BloqueioTemporarioYouTube):
                pltube.executar_com_retentativas("https://youtube.com", "", bloqueada)

        dormir.assert_not_called()

    def test_parametro_esperar_bloqueio_aparece_no_parser(self):
        args = pltube.criar_parser().parse_args(
            ["--esperar-bloqueio", "--reprocess", "--state-file", "cache.sqlite3"]
        )
        self.assertTrue(args.esperar_bloqueio)
        self.assertTrue(args.reprocess)
        self.assertEqual(args.state_file, "cache.sqlite3")


if __name__ == "__main__":
    unittest.main()
