import csv
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import aplicar_metadados
import pltube


class AplicarMetadadosTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = self.temp.name
        self.songs = os.path.join(self.base, "songs")
        self.playlists = os.path.join(self.base, "playlists")
        self.db_path = os.path.join(self.base, "pltube_state.sqlite3")
        self.report_path = os.path.join(self.base, "metadados_pendentes.csv")
        os.makedirs(os.path.join(self.songs, "_pendentes"))
        os.makedirs(self.playlists)
        self.audio_path = os.path.join(self.songs, "_pendentes", "Titulo provisório [abc123].mp3")
        with open(self.audio_path, "wb") as audio:
            audio.write(b"fake audio")
        self.state = pltube.abrir_banco_estado(self.db_path)
        self.csv_source = os.path.join(self.base, "playlist.csv")
        with open(self.csv_source, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "title", "url"])
            writer.writerow(["abc123", "Titulo provisório", "https://youtube.com/watch?v=abc123"])

        self.result = {
            "video_id": "abc123",
            "status": "PENDENTE",
            "metadata": {
                "id": "abc123", "title": "Titulo provisório",
                "artists": ["Artista provisório"], "album": "Álbum provisório",
                "isrc": "", "duration": 120,
            },
            "source_youtube": {
                "title": "Titulo provisório", "artists": ["Artista provisório"],
                "album": "Álbum provisório", "duration": 120, "id": "abc123",
                "uploader": "Canal publicador",
            },
            "pending_reasons": ["artista sem confirmação independente"],
            "diagnostics": [], "candidates": [],
            "provenance": {"title": "youtube", "artists": "youtube"},
            "confidence": {"title": "provisoria", "artists": "provisoria"},
        }
        self.meta = {
            "title": "Titulo provisório", "artists": ["Artista provisório"],
            "artist": "Artista provisório", "duration": 120, "id": "abc123",
        }
        with patch.multiple(
            pltube,
            BASE_DIR=self.base,
            SONGS_DIR=self.songs,
            PLAYLISTS_DIR=self.playlists,
            LOG_FILE=os.path.join(self.base, "Logs.txt"),
        ):
            pltube.salvar_video_cache(
                self.state, "youtube:abc123", "https://youtube.com/watch?v=abc123",
                "complete", self.meta, self.audio_path,
            )
            pltube.salvar_resolucao(self.state, "youtube:abc123", self.result, self.audio_path)
            pltube.salvar_item_playlist(
                self.state, self.csv_source, 0, "youtube:abc123", self.meta, self.audio_path
            )
            pltube.escrever_playlist(
                os.path.join(self.playlists, "playlist.m3u"),
                [(self.meta["title"], "Artista provisório", 120, self.audio_path)],
                "playlist",
            )
            pltube.exportar_metadados_pendentes(self.state, self.report_path)
        self.state.commit()
        self.sugestao_inicial = self._ler_linha()
        self._editar_linha({
            "aplicar": "sim",
            "usar_titulo": "sim",
            "titulo_final": "Título Corrigido",
            "usar_artistas": "sim",
            "artistas_finais": "Artista Principal|Colaborador",
            "usar_album": "sim",
            "album_final": "Álbum Confirmado",
            "usar_isrc": "nao",
            "confirmar_manual": "sim",
        })

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def _ler_linha(self):
        with open(self.report_path, "r", encoding="utf-8-sig", newline="") as handle:
            return next(csv.DictReader(handle))

    def _editar_linha(self, edicoes):
        with open(self.report_path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames
            rows = list(reader)
        rows[0].update(edicoes)
        with open(self.report_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def _aplicar(self, apply=False):
        with patch.multiple(
            pltube,
            SONGS_DIR=self.songs,
            PLAYLISTS_DIR=self.playlists,
            BASE_DIR=self.base,
            LOG_FILE=os.path.join(self.base, "Logs.txt"),
        ):
            return aplicar_metadados.aplicar_revisoes(
                self.report_path, self.db_path, self.songs, self.playlists, aplicar=apply
            )

    def test_csv_e_gerado_com_sugestoes_e_controles_nao_marcados(self):
        self.assertEqual(self.sugestao_inicial["video_key"], "youtube:abc123")
        self.assertEqual(self.sugestao_inicial["titulo_final"], "Titulo provisório")
        self.assertEqual(self.sugestao_inicial["artistas_finais"], "Artista provisório")
        self.assertEqual(self.sugestao_inicial["album_final"], "Álbum provisório")
        self.assertEqual(self.sugestao_inicial["aplicar"], "nao")
        self.assertEqual(self.sugestao_inicial["usar_titulo"], "nao")
        self.assertEqual(self.sugestao_inicial["confirmar_manual"], "nao")

        with patch.multiple(pltube, BASE_DIR=self.base, SONGS_DIR=self.songs):
            pltube.exportar_metadados_pendentes(self.state, self.report_path)
        row = self._ler_linha()
        self.assertEqual(row["titulo_final"], "Título Corrigido")
        self.assertEqual(row["aplicar"], "sim")

    def test_simulacao_nao_altera_tags_sqlite_nem_planilha(self):
        with open(self.report_path, "rb") as handle:
            before = handle.read()
        result = self._aplicar(apply=False)
        saved = pltube.carregar_resolucao(self.state, "youtube:abc123")
        self.assertEqual(result["planejadas"], 1)
        self.assertEqual(result["aplicadas"], 0)
        self.assertEqual(saved["result"]["status"], "PENDENTE")
        self.assertEqual(saved["result"]["metadata"]["title"], "Titulo provisório")
        with open(self.report_path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_aplicar_atualiza_id3_sqlite_m3u_e_remove_item_resolvido_da_fila(self):
        try:
            from mutagen.id3 import ID3
        except ImportError:
            self.skipTest("mutagen não está instalado")

        result = self._aplicar(apply=True)
        self.assertEqual(result["aplicadas"], 1)
        tags = ID3(self.audio_path)
        self.assertEqual(str(tags["TIT2"]), "Título Corrigido")
        self.assertEqual(tags["TPE1"].text, ["Artista Principal", "Colaborador"])
        self.assertEqual(str(tags["TALB"]), "Álbum Confirmado")
        self.assertEqual(tags.getall("TXXX:PLTUBE_METADATA_STATUS")[0].text, ["RESOLVIDO"])

        saved = pltube.carregar_resolucao(self.state, "youtube:abc123")
        self.assertEqual(saved["result"]["status"], "RESOLVIDO")
        self.assertEqual(saved["result"]["provenance"]["title"], "manual")
        cache = pltube.ler_video_cache(self.state, "youtube:abc123")
        self.assertEqual(cache["title"], "Título Corrigido")
        item = self.state.execute(
            "SELECT title, artist FROM playlist_items WHERE video_key = ?",
            ("youtube:abc123",),
        ).fetchone()
        self.assertEqual(item["title"], "Título Corrigido")
        self.assertEqual(item["artist"], "Artista Principal | Colaborador")
        with open(os.path.join(self.playlists, "playlist.m3u"), encoding="utf-8") as handle:
            self.assertIn("Artista Principal | Colaborador - Título Corrigido", handle.read())
        with open(self.report_path, "r", encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])

    def test_correcao_parcial_permanece_pendente_e_edicao_e_preservada(self):
        self._editar_linha({
            "usar_titulo": "sim", "titulo_final": "Título Parcial",
            "usar_artistas": "nao", "usar_album": "nao", "usar_isrc": "nao",
            "confirmar_manual": "nao",
        })
        result = self._aplicar(apply=True)
        self.assertEqual(result["aplicadas"], 1)
        saved = pltube.carregar_resolucao(self.state, "youtube:abc123")
        self.assertEqual(saved["result"]["status"], "PENDENTE")
        self.assertEqual(saved["result"]["metadata"]["title"], "Título Parcial")
        self.assertIn("title", saved["result"]["manual_review"]["fields"])
        row = self._ler_linha()
        self.assertEqual(row["aplicar"], "sim")
        self.assertEqual(row["titulo_final"], "Título Parcial")
        self.assertEqual(row["usar_titulo"], "sim")

        with patch.multiple(
            pltube,
            MODELO_HIBRIDO=True,
            BASE_DIR=self.base,
            SONGS_DIR=self.songs,
            PLAYLISTS_DIR=self.playlists,
            ROW=2,
            ARTIST_LINKS=False,
            LOG_FILE=os.path.join(self.base, "Logs.txt"),
        ):
            with (
                patch.object(pltube, "resolver_meta_hibrido") as resolver,
                patch.object(pltube, "extrair_metadados") as extract,
            ):
                pltube.processar_csv(
                    "playlist.csv", estado=self.state, reprocess=True, indice_local={}
                )
            resolver.assert_not_called()
            extract.assert_not_called()
        saved_after_reprocess = pltube.carregar_resolucao(self.state, "youtube:abc123")
        self.assertEqual(
            saved_after_reprocess["result"]["metadata"]["title"], "Título Parcial"
        )

    def test_album_pode_ser_limpo_explicitamente(self):
        self._editar_linha({
            "usar_album": "sim", "album_final": "__LIMPAR__",
            "confirmar_manual": "sim",
        })
        result = self._aplicar(apply=True)
        self.assertEqual(result["aplicadas"], 1)
        saved = pltube.carregar_resolucao(self.state, "youtube:abc123")
        self.assertEqual(saved["result"]["metadata"]["album"], "")
        self.assertEqual(saved["result"]["status"], "RESOLVIDO")


if __name__ == "__main__":
    unittest.main()
