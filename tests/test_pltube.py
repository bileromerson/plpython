import csv
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pltube


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
    def setUp(self):
        # Sem a pausa real de 5-10 s entre vídeos durante os testes.
        # Estado inicial fixo: estes testes cobrem o modo sem identificação e sem
        # atalhos, independente do que outro teste deixou configurado.
        for nome, valor in (("PAUSA_YOUTUBE", (0.0, 0.0)), ("MODELO_HIBRIDO", False),
                            ("ARTIST_LINKS", False), ("COOKIES_YTDLP", {})):
            patcher = patch.object(pltube, nome, valor)
            patcher.start()
            self.addCleanup(patcher.stop)



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
            # Re-upload: canal qualquer, artista só no título -> fica para conferência.
            origem = {
                "title": "Artista do vídeo - Canção provisória", "artists": [],
                "uploader": "Canal Qualquer", "album": "Álbum do vídeo",
                "duration": 120, "id": "abc123",
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
                self.assertEqual(hybrid_result["status"], "OBSERVAR")
                pasta = os.path.join(songs, artists[0])
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

                    path = os.path.join(songs, "Artista do vídeo", "Canção provisória [abc123].mp3")
                    self.assertTrue(os.path.isfile(path))
                    pending_link = os.path.join(songs, "_observar", os.path.basename(path))
                    self.assertTrue(os.path.islink(pending_link))
                    self.assertEqual(os.path.realpath(pending_link), os.path.realpath(path))
                    saved = pltube.carregar_resolucao(state, "youtube:abc123")
                    self.assertEqual(saved["result"]["status"], "OBSERVAR")
                    self.assertEqual(saved["audio_path"], path)
                    job = state.execute(
                        "SELECT status, audio_path FROM download_jobs WHERE video_key = ?",
                        ("youtube:abc123",),
                    ).fetchone()
                    self.assertEqual(job["status"], "CONCLUIDO")
                    self.assertFalse(os.path.isabs(job["audio_path"]))
                    track = state.execute(
                        "SELECT title, artists_json, metadata_status FROM music.music_tracks "
                        "WHERE video_key = ?",
                        ("youtube:abc123",),
                    ).fetchone()
                    self.assertEqual(track["title"], "Canção provisória")
                    self.assertEqual(track["metadata_status"], "OBSERVAR")
                    self.assertFalse(os.path.samefile(
                        dict((row[1], row[2]) for row in state.execute("PRAGMA database_list"))["main"],
                        dict((row[1], row[2]) for row in state.execute("PRAGMA database_list"))["music"],
                    ))

                    report = pltube.exportar_metadados_pendentes(
                        state, os.path.join(temp, "metadados_pendentes.csv")
                    )
                    with open(report, encoding="utf-8-sig", newline="") as handle:
                        rows = list(csv.DictReader(handle))
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["video_id"], "abc123")
                    self.assertIn("deduzidos do título do vídeo", rows[0]["observacoes"])
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
                    self.assertEqual(saved["result"]["status"], "OBSERVAR")
                    self.assertEqual(saved["audio_path"], audio_existente)
                    pending_link = os.path.join(songs, "_observar", os.path.basename(audio_existente))
                    self.assertTrue(os.path.islink(pending_link))
                    self.assertEqual(os.path.realpath(pending_link), os.path.realpath(audio_existente))
                finally:
                    state.close()

    def test_baixar_musica_pendente_fica_no_artista_principal_com_link_de_revisao(self):
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

            self.assertIn(os.path.join("songs", "Artista Um"), path)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(os.path.islink(os.path.join(songs, "_pendentes", os.path.basename(path))))
            self.assertEqual(
                os.path.realpath(os.path.join(songs, "_pendentes", os.path.basename(path))),
                os.path.realpath(path),
            )
            self.assertFalse(os.path.exists(os.path.join(songs, "Artista Dois")))
            tagger.assert_called_once_with(path, resultado)

    def test_pendente_sem_artista_usa_pasta_de_fallback_e_link_de_revisao(self):
        with tempfile.TemporaryDirectory() as temp:
            songs = os.path.join(temp, "songs")
            with (
                patch.multiple(
                    pltube, SONGS_DIR=songs, ARTIST_LINKS=False,
                    yt_dlp=type("FakeModule", (), {"YoutubeDL": FakeYoutubeDL}),
                ),
                patch.object(pltube, "aplicar_tags_hibridas", return_value=True),
            ):
                path = pltube.baixar_musica(
                    "https://youtube.com/watch?v=abc123", [], "Título", "abc123",
                    hybrid_result={"status": "PENDENTE", "metadata": {"title": "Título"}},
                )
            self.assertTrue(path.endswith(os.path.join("Artista Desconhecido", "Título [abc123].mp3")))
            self.assertTrue(os.path.isfile(path))
            pending_link = os.path.join(songs, "_pendentes", os.path.basename(path))
            self.assertTrue(os.path.islink(pending_link))
            self.assertEqual(os.path.realpath(pending_link), os.path.realpath(path))

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

    def test_metadados_locais_leem_isrc_do_frame_id3_tsrc(self):
        try:
            from mutagen.id3 import ID3, TSRC
        except ImportError:
            self.skipTest("mutagen não está instalado neste ambiente")

        with tempfile.TemporaryDirectory() as temp:
            songs = os.path.join(temp, "songs")
            pasta_artista = os.path.join(songs, "Artista A")
            os.makedirs(pasta_artista)
            path = os.path.join(pasta_artista, "Faixa [abc123].mp3")
            tags = ID3()
            tags.add(TSRC(encoding=3, text=["us-aaa-99-00001"]))
            tags.save(path)

            with patch.object(pltube, "SONGS_DIR", songs):
                meta = pltube.metadados_de_arquivo_local(path, "abc123")

        self.assertEqual(meta["isrc"], "USAAA9900001")
        self.assertEqual(meta["local_metadata"]["isrc"], "USAAA9900001")

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
        # 429 é limite da sessão: esperas longas (5 e 15 min)
        self.assertEqual([chamada.args[0] for chamada in dormir.call_args_list], [300, 900])

    def test_falha_de_rede_espera_pouco(self):
        tentativas = []

        def operacao():
            tentativas.append(1)
            if len(tentativas) < 3:
                raise RuntimeError("HTTP Error 503: Service Unavailable")
            return "ok"

        with (
            patch.object(pltube, "ESPERAR_BLOQUEIO", True),
            patch.object(pltube, "registrar_erro"),
            patch("pltube.time.sleep") as dormir,
        ):
            self.assertEqual(pltube.executar_com_retentativas("u", "", operacao), "ok")
        self.assertEqual([c.args[0] for c in dormir.call_args_list], [30, 60])

    def test_reconhece_as_mensagens_reais_de_bloqueio(self):
        real = ("ERROR: [youtube] abc: Video unavailable. This content isn’t available, try again later. "
                "The current session has been rate-limited by YouTube for up to an hour. It is recommended "
                "to use `-t sleep` to add a delay between video requests")
        self.assertEqual(pltube.tipo_bloqueio(real), "sessao")
        self.assertEqual(pltube.tipo_bloqueio("Sign in to confirm you’re not a bot"), "sessao")
        self.assertEqual(pltube.tipo_bloqueio("HTTP Error 504: Gateway Timeout"), "rede")
        self.assertEqual(pltube.tipo_bloqueio("Video unavailable. This video is private"), "")

    def test_limite_da_sessao_desiste_depois_de_mais_de_uma_hora(self):
        with (
            patch.object(pltube, "ESPERAR_BLOQUEIO", True),
            patch.object(pltube, "registrar_erro"),
            patch("pltube.time.sleep") as dormir,
        ):
            with self.assertRaises(pltube.BloqueioTemporarioYouTube):
                pltube.executar_com_retentativas(
                    "u", "", lambda: (_ for _ in ()).throw(RuntimeError("try again later")))
        esperas = [c.args[0] for c in dormir.call_args_list]
        self.assertEqual(esperas, [300, 900, 1800, 3600])
        self.assertGreater(sum(esperas), 3600)

    def test_pausa_entre_videos_desconta_o_tempo_ja_passado(self):
        with (
            patch.object(pltube, "PAUSA_YOUTUBE", (5.0, 10.0)),
            patch.object(pltube, "_ULTIMA_CONSULTA_YOUTUBE", 100.0),
            patch("pltube.random.uniform", return_value=8.0),
            patch("pltube.time.monotonic", side_effect=[103.0, 111.0]),
            patch("pltube.time.sleep") as dormir,
        ):
            pltube.aguardar_vez_youtube()
        dormir.assert_called_once_with(5.0)  # 8 s sorteados - 3 s já passados
        with (
            patch.object(pltube, "_ULTIMA_CONSULTA_YOUTUBE", 100.0),
            patch("pltube.time.monotonic", side_effect=[130.0, 130.0]),
            patch("pltube.time.sleep") as dormir,
        ):
            pltube.aguardar_vez_youtube()
        dormir.assert_not_called()  # já passou mais que a pausa

    def test_download_reaproveita_os_metadados_lidos(self):
        class FakeYDL:
            def __init__(self):
                self.chamadas = []

            def download_with_info_file(self, caminho):
                with open(caminho, encoding="utf-8") as arquivo:
                    self.chamadas.append(("info", json.load(arquivo)["id"]))

            def download(self, urls):
                self.chamadas.append(("url", urls[0]))

        modulo = type("M", (), {"YoutubeDL": type("Y", (), {
            "sanitize_info": staticmethod(lambda info, remove_private_keys=False: info)})})
        with (
            patch.object(pltube, "yt_dlp", modulo),
            patch.object(pltube, "INFO_YOUTUBE", {}),
            patch.object(pltube, "aguardar_vez_youtube") as pausa,
        ):
            pltube.guardar_info_youtube("https://y/1", {"id": "v1", "title": "t"})
            ydl = FakeYDL()
            pltube.baixar_reaproveitando(ydl, "https://y/1")   # usa o que já foi lido
            pltube.baixar_reaproveitando(ydl, "https://y/1")   # não tem mais: consulta pela URL
        self.assertEqual(ydl.chamadas, [("info", "v1"), ("url", "https://y/1")])
        pausa.assert_called_once()  # só a consulta nova espera a vez

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

    def test_csv_e_caminho_de_audio_sao_salvos_relativos_nos_dois_bancos(self):
        with tempfile.TemporaryDirectory() as temp:
            base = os.path.join(temp, "csvs")
            songs = os.path.join(temp, "songs")
            pasta_artist = os.path.join(songs, "Artista A")
            os.makedirs(base)
            os.makedirs(pasta_artist)
            audio = os.path.join(pasta_artist, "musica1.mp3")
            with open(audio, "wb") as handle:
                handle.write(b"audio")
            csv_path = os.path.join(base, "lista.csv")
            downloads = os.path.join(base, "sql", "downloads.sqlite3")
            metadata_db = os.path.join(base, "sql", "musicas.sqlite3")
            estado = pltube.abrir_banco_estado(downloads, metadata_db)
            try:
                with patch.object(pltube, "BASE_DIR", base):
                    meta = {
                        "id": "abc123", "title": "Musica 1", "artists": ["Artista A"],
                        "duration": 100,
                    }
                    pltube.salvar_video_cache(
                        estado, "youtube:abc123", "https://youtube.com/watch?v=abc123",
                        "complete", meta, audio,
                    )
                    pltube.registrar_job_download(
                        estado, "youtube:abc123", "https://youtube.com/watch?v=abc123",
                        "CONCLUIDO", video_id="abc123", path=audio,
                    )
                    pltube.salvar_item_playlist(
                        estado, csv_path, 0, "youtube:abc123", meta, audio,
                    )
                    video = estado.execute(
                        "SELECT path FROM video_cache WHERE video_key = ?", ("youtube:abc123",)
                    ).fetchone()[0]
                    job = estado.execute(
                        "SELECT audio_path FROM download_jobs WHERE video_key = ?",
                        ("youtube:abc123",),
                    ).fetchone()[0]
                    item = estado.execute(
                        "SELECT csv_path, path FROM playlist_items WHERE video_key = ?",
                        ("youtube:abc123",),
                    ).fetchone()
                    esperado = os.path.relpath(audio, start=os.path.dirname(downloads))
                    self.assertEqual(video, esperado)
                    self.assertEqual(job, esperado)
                    self.assertEqual(item["csv_path"], "lista.csv")
                    self.assertEqual(item["path"], esperado)
                    self.assertEqual(pltube.ler_video_cache(estado, "youtube:abc123")["path"], audio)
                    self.assertEqual(
                        pltube.listar_itens_playlist(estado, "lista.csv")[0][3], audio
                    )
            finally:
                estado.close()

    def test_migra_banco_legado_sem_apaga_lo_e_converte_caminho(self):
        with tempfile.TemporaryDirectory() as temp:
            base = os.path.join(temp, "csvs")
            pasta_sql = os.path.join(base, "sql")
            audio = os.path.join(temp, "songs", "Artista A", "faixa.mp3")
            os.makedirs(pasta_sql)
            os.makedirs(os.path.dirname(audio))
            with open(audio, "wb") as handle:
                handle.write(b"audio")
            legado = os.path.join(base, "pltube_state.sqlite3")
            antigo = sqlite3.connect(legado)
            antigo.executescript(
                """CREATE TABLE video_cache (
                    video_key TEXT PRIMARY KEY, video_id TEXT, source_url TEXT,
                    status TEXT, title TEXT, artists_json TEXT, duration REAL,
                    path TEXT, error TEXT);
                    CREATE TABLE metadata_resolution (
                    video_key TEXT PRIMARY KEY, video_id TEXT, status TEXT,
                    result_json TEXT, audio_path TEXT, updated_at TEXT);"""
            )
            resultado = {
                "video_id": "abc123", "status": "PENDENTE",
                "metadata": {"id": "abc123", "title": "Faixa", "artists": ["Artista A"]},
                "source_youtube": {"title": "Faixa", "artists": ["Artista A"]},
            }
            antigo.execute(
                "INSERT INTO video_cache VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("youtube:abc123", "abc123", "https://youtube.com/watch?v=abc123",
                 "complete", "Faixa", '["Artista A"]', 90, audio, ""),
            )
            antigo.execute(
                "INSERT INTO metadata_resolution VALUES (?, ?, ?, ?, ?, ?)",
                ("youtube:abc123", "abc123", "PENDENTE",
                 json.dumps(resultado), audio, "2026-01-01"),
            )
            antigo.commit()
            antigo.close()

            estado = pltube.abrir_banco_estado(
                os.path.join(pasta_sql, "downloads.sqlite3"),
                os.path.join(pasta_sql, "musicas.sqlite3"),
            )
            try:
                with patch.object(pltube, "BASE_DIR", base):
                    migracao = pltube.migrar_banco_legado(estado, legado)
                    novamente = pltube.migrar_banco_legado(estado, legado)
                self.assertEqual(migracao, {"downloads": 1, "playlists": 0, "metadados": 1})
                self.assertEqual(novamente, {"downloads": 0, "playlists": 0, "metadados": 0})
                job = estado.execute(
                    "SELECT audio_path FROM download_jobs WHERE video_key = ?",
                    ("youtube:abc123",),
                ).fetchone()
                self.assertFalse(os.path.isabs(job["audio_path"]))
                self.assertEqual(
                    pltube.carregar_resolucao(estado, "youtube:abc123")["audio_path"], audio
                )
                self.assertTrue(os.path.isfile(legado))
            finally:
                estado.close()

    def test_parametros_do_download(self):
        from comandos import download
        args = download.criar_parser().parse_args(
            ["--wait-on-limit", "--retry", "--downloads-db", "cache.sqlite3",
             "--require-isrc", "--require-mb-artist"]
        )
        self.assertTrue(args.esperar_bloqueio)
        self.assertTrue(args.modelo_hibrido)
        self.assertTrue(args.reprocess)
        self.assertTrue(args.exigir_isrc)
        self.assertTrue(args.exigir_artista_musicbrainz)
        self.assertEqual(args.downloads_db, "cache.sqlite3")

    def test_base_dir_padrao_salva_m3u_na_pasta_playlists(self):
        with tempfile.TemporaryDirectory() as temp:
            antigos = (pltube.BASE_DIR, pltube.SONGS_DIR, pltube.PLAYLISTS_DIR)
            try:
                from comandos import download
                args = download.criar_parser().parse_args(["-c", temp])
                pltube.verificar_diretorios(args)
                self.assertEqual(pltube.BASE_DIR, os.path.abspath(temp))
                # BaseDir só muda os CSVs; o resto fica na pasta atual.
                self.assertEqual(pltube.SONGS_DIR, os.path.join(os.getcwd(), "songs"))
                pasta_playlists = os.path.join(os.getcwd(), "playlists")
                self.assertEqual(pltube.PLAYLISTS_DIR, pasta_playlists)
                pltube.PLAYLISTS_DIR = os.path.join(temp, "playlists")
                pltube.criar_diretorios()
                self.assertTrue(os.path.isdir(pltube.PLAYLISTS_DIR))
            finally:
                pltube.BASE_DIR, pltube.SONGS_DIR, pltube.PLAYLISTS_DIR = antigos


if __name__ == "__main__":
    unittest.main()
