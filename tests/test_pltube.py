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

    def test_modelo_hibrido_salva_pendencia_em_sqlite_e_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "lista.csv"), "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "titulo", "url"])
                writer.writerow(["abc123", "Canção provisória", "https://youtube.com/watch?v=abc123"])

            songs, playlists = os.path.join(temp, "songs"), os.path.join(temp, "playlists")
            origem = {
                "title": "Canção provisória", "artists": ["Artista do vídeo"],
                "album": "Álbum do vídeo", "duration": 120, "id": "abc123",
            }
            class BaseSemResultados:
                def search_by_isrc(self, isrc, limit=10):
                    return []

                def search_by_title_artist(self, title, artist, limit=10):
                    return []

                def search_by_title(self, title, limit=10):
                    return []

                def get_front_cover(self, release_id):
                    return None

            cliente = BaseSemResultados()

            def download(url, artists, title, video_id, csv_file, hybrid_result=None):
                self.assertEqual(hybrid_result["status"], "PENDENTE")
                pasta = os.path.join(songs, "_pendentes")
                os.makedirs(pasta, exist_ok=True)
                path = os.path.join(pasta, f"{title} [{video_id}].mp3")
                with open(path, "wb") as audio:
                    audio.write(b"provisional audio")
                return path

            with patch.multiple(
                pltube, BASE_DIR=temp, SONGS_DIR=songs, PLAYLISTS_DIR=playlists,
                LOG_FILE=os.path.join(temp, "Logs.txt"), ROW=2, ARTIST_LINKS=True,
                MODELO_HIBRIDO=True, MUSICBRAINZ_CLIENT=cliente, RESOLVER_CACHE={},
            ):
                state = pltube.abrir_banco_estado(os.path.join(temp, "state.sqlite3"))
                try:
                    with (
                        patch.object(pltube, "extrair_metadados", return_value=origem),
                        patch.object(pltube, "baixar_musica", side_effect=download),
                    ):
                        self.assertTrue(
                            pltube.processar_csv("lista.csv", estado=state, indice_local={})
                        )

                    path = os.path.join(songs, "_pendentes", "Canção provisória [abc123].mp3")
                    self.assertTrue(os.path.isfile(path))
                    self.assertFalse(os.path.exists(os.path.join(songs, "Artista do vídeo")))
                    saved = pltube.carregar_resolucao(state, "youtube:abc123")
                    self.assertEqual(saved["result"]["status"], "PENDENTE")
                    self.assertEqual(saved["audio_path"], path)

                    report = pltube.exportar_metadados_pendentes(
                        state, os.path.join(temp, "metadados_pendentes.csv")
                    )
                    with open(report, encoding="utf-8-sig", newline="") as handle:
                        rows = list(csv.DictReader(handle))
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["video_id"], "abc123")
                    self.assertIn("sem confirmação", rows[0]["motivos_pendencia"])
                    self.assertIn("lista", rows[0]["playlists"])

                    resultado_confirmado = {
                        **saved["result"],
                        "status": "RESOLVIDO",
                        "metadata": {
                            "id": "abc123", "title": "Canção canônica",
                            "artists": ["Artista confirmado"], "album": "Álbum confirmado",
                            "isrc": "", "duration": 120,
                        },
                        "pending_reasons": [],
                    }
                    meta_confirmada = {
                        "title": "Canção canônica", "artists": ["Artista confirmado"],
                        "artist": "Artista confirmado", "album": "Álbum confirmado",
                        "duration": 120, "id": "abc123",
                    }
                    with (
                        patch.object(
                            pltube, "resolver_meta_hibrido",
                            return_value=(meta_confirmada, resultado_confirmado),
                        ) as resolve_again,
                        patch.object(pltube, "extrair_metadados") as extract_again,
                        patch.object(pltube, "baixar_musica") as download_again,
                        patch.object(pltube, "aplicar_tags_hibridas", return_value=True) as apply_again,
                    ):
                        self.assertTrue(
                            pltube.processar_csv(
                                "lista.csv", estado=state, reprocess=True, indice_local={}
                            )
                        )

                    resolve_again.assert_called_once()
                    extract_again.assert_not_called()
                    download_again.assert_not_called()
                    apply_again.assert_called_once_with(path, resultado_confirmado)
                    resolucao_atualizada = pltube.carregar_resolucao(state, "youtube:abc123")
                    self.assertEqual(resolucao_atualizada["result"]["status"], "RESOLVIDO")
                    self.assertEqual(resolucao_atualizada["audio_path"], path)
                finally:
                    state.close()

    def test_modo_hibrido_processa_audio_existente_sem_rebaixar(self):
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "lista.csv"), "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "titulo", "url"])
                writer.writerow(["abc123", "Faixa antiga", "https://youtube.com/watch?v=abc123"])
            songs, playlists = os.path.join(temp, "songs"), os.path.join(temp, "playlists")
            pasta = os.path.join(songs, "Artista antigo")
            os.makedirs(pasta)
            audio_existente = os.path.join(pasta, "Faixa antiga [abc123].mp3")

            def download(url, artists, title, video_id, csv_file):
                with open(audio_existente, "wb") as handle:
                    handle.write(b"old audio")
                return audio_existente

            class BaseSemResultados:
                def search_by_isrc(self, isrc, limit=10):
                    return []

                def search_by_title_artist(self, title, artist, limit=10):
                    return []

                def search_by_title(self, title, limit=10):
                    return []

                def get_front_cover(self, release_id):
                    return None

            with patch.multiple(
                pltube, BASE_DIR=temp, SONGS_DIR=songs, PLAYLISTS_DIR=playlists,
                LOG_FILE=os.path.join(temp, "Logs.txt"), ROW=2, ARTIST_LINKS=False,
                MODELO_HIBRIDO=False,
            ):
                state = pltube.abrir_banco_estado(os.path.join(temp, "state.sqlite3"))
                try:
                    with (
                        patch.object(
                            pltube, "extrair_metadados",
                            return_value={"title": "Faixa antiga", "artists": ["Artista antigo"], "duration": 80, "id": "abc123"},
                    ) as extract,
                        patch.object(pltube, "baixar_musica", side_effect=download) as downloader,
                    ):
                        pltube.processar_csv("lista.csv", estado=state, indice_local={})
                    extract.reset_mock()
                    downloader.reset_mock()

                    with (
                        patch.object(pltube, "MODELO_HIBRIDO", True),
                        patch.object(pltube, "MUSICBRAINZ_CLIENT", BaseSemResultados()),
                        patch.object(pltube, "RESOLVER_CACHE", {}),
                        patch.object(pltube, "aplicar_tags_hibridas", return_value=True) as apply_tags,
                    ):
                        self.assertTrue(
                            pltube.processar_csv("lista.csv", estado=state, indice_local={})
                        )

                    extract.assert_not_called()
                    downloader.assert_not_called()
                    apply_tags.assert_called_once()
                    self.assertEqual(apply_tags.call_args.args[0], audio_existente)
                    saved = pltube.carregar_resolucao(state, "youtube:abc123")
                    self.assertEqual(saved["result"]["status"], "PENDENTE")
                    self.assertEqual(saved["audio_path"], audio_existente)
                finally:
                    state.close()

    def test_baixar_musica_pendente_nao_cria_pastas_ou_links_de_artista(self):
        with tempfile.TemporaryDirectory() as temp:
            songs = os.path.join(temp, "songs")
            resultado = {"status": "PENDENTE", "metadata": {"title": "Título"}}
            with (
                patch.multiple(
                    pltube, SONGS_DIR=songs, ARTIST_LINKS=True,
                    yt_dlp=type("FakeModule", (), {"YoutubeDL": FakeYoutubeDL}),
                ),
                patch.object(pltube, "aplicar_tags_hibridas", return_value=True) as tagger,
            ):
                path = pltube.baixar_musica(
                    "https://youtube.com/watch?v=abc123",
                    ["Artista Um", "Artista Dois"], "Título", "abc123",
                    hybrid_result=resultado,
                )

            self.assertIn(os.path.join("songs", "_pendentes"), path)
            self.assertTrue(os.path.isfile(path))
            self.assertFalse(os.path.exists(os.path.join(songs, "Artista Um")))
            self.assertFalse(os.path.exists(os.path.join(songs, "Artista Dois")))
            tagger.assert_called_once_with(path, resultado)

    def test_modo_hibrido_nao_promove_uploader_a_artista(self):
        class YoutubeDLSemArtista(FakeYoutubeDL):
            def extract_info(self, url, download=False):
                return {
                    "title": "Faixa sem crédito", "uploader": "Canal que publicou",
                    "duration": 90, "id": "abc123",
                }

        with patch.multiple(
            pltube,
            yt_dlp=type("FakeModule", (), {"YoutubeDL": YoutubeDLSemArtista}),
            MODELO_HIBRIDO=True,
            LOG_FILE=os.path.join(tempfile.gettempdir(), "pltube-test-log.txt"),
        ):
            meta = pltube.extrair_metadados("https://youtube.com/watch?v=abc123")

        self.assertEqual(meta["artists"], [])
        self.assertEqual(meta["uploader"], "Canal que publicou")

    def test_tag_id3_marca_audio_como_pendente_e_potencialmente_incorreto(self):
        try:
            from mutagen.id3 import ID3
        except ImportError:
            self.skipTest("mutagen não está instalado neste ambiente")

        resultado = {
            "status": "PENDENTE",
            "metadata": {
                "title": "Título provisório", "artists": ["Artista provisório"],
                "album": "Álbum provisório", "isrc": "",
            },
            "provenance": {"title": "youtube"},
            "confidence": {"title": "provisoria"},
            "pending_reasons": ["sem confirmação independente"],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "faixa.mp3")
            with open(path, "wb") as audio:
                audio.write(b"fake audio")

            self.assertTrue(pltube.aplicar_tags_hibridas(path, resultado))
            tags = ID3(path)

        status = tags.getall("TXXX:PLTUBE_METADATA_STATUS")
        note = tags.getall("TXXX:PLTUBE_METADATA_NOTE")
        self.assertEqual(status[0].text, ["PENDENTE"])
        self.assertIn("podem estar incorretos", note[0].text[0])
        self.assertEqual(str(tags["TIT2"]), "Título provisório")

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

    def test_indice_local_prefere_arquivo_fisico_a_link_de_colaborador(self):
        with tempfile.TemporaryDirectory() as temp:
            songs = os.path.join(temp, "songs")
            principal = os.path.join(songs, "Z Artista")
            colaborador = os.path.join(songs, "A Artista")
            os.makedirs(principal)
            os.makedirs(colaborador)
            original = os.path.join(principal, "Faixa [abc123].mp3")
            with open(original, "wb") as audio:
                audio.write(b"audio")
            os.symlink(
                os.path.relpath(original, start=colaborador),
                os.path.join(colaborador, os.path.basename(original)),
            )

            with patch.object(pltube, "SONGS_DIR", songs):
                indice = pltube.indexar_musicas_locais()

            self.assertEqual(indice["abc123"], original)

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
                self.assertFalse(os.path.exists(os.path.join(songs, "_library")))
                pasta_principal = os.path.join(songs, "Artista Um")
                pasta_colaborador = os.path.join(songs, "Artista Dois")
                self.assertEqual(len(os.listdir(pasta_principal)), 1)
                original = os.path.join(pasta_principal, os.listdir(pasta_principal)[0])
                link_colaborador = os.path.join(pasta_colaborador, os.path.basename(original))
                self.assertTrue(os.path.isfile(original))
                self.assertFalse(os.path.islink(original))
                self.assertTrue(os.path.islink(link_colaborador))
                self.assertEqual(os.path.realpath(link_colaborador), os.path.realpath(original))

                m3u = os.path.join(playlists, "playlist.m3u")
                with open(m3u, encoding="utf-8") as handle:
                    content = handle.read()
                self.assertIn("#EXTM3U", content)
                self.assertIn("Artista Um, Artista Dois - Música de Teste", content)
                self.assertIn("../songs/Artista Um/", content)

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
            ["--esperar-bloqueio", "--modelo-hibrido", "--reprocess", "--state-file", "cache.sqlite3"]
        )
        self.assertTrue(args.esperar_bloqueio)
        self.assertTrue(args.modelo_hibrido)
        self.assertTrue(args.reprocess)
        self.assertEqual(args.state_file, "cache.sqlite3")


if __name__ == "__main__":
    unittest.main()
