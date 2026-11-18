import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import reverter_biblioteca


class ReverterBibliotecaTests(unittest.TestCase):
    def _preparar_layout_antigo(self, temp):
        root = os.path.join(temp, "songs")
        library = os.path.join(root, "_library")
        playlists = os.path.join(temp, "playlists")
        os.makedirs(library)
        os.makedirs(playlists)
        original = os.path.join(library, "Faixa [abc123].mp3")
        with open(original, "wb") as audio:
            audio.write(b"audio bytes")
        for artist in ("Artista A", "Artista B"):
            folder = os.path.join(root, artist)
            os.makedirs(folder)
            os.symlink(
                os.path.relpath(original, start=folder),
                os.path.join(folder, os.path.basename(original)),
            )
        playlist = os.path.join(playlists, "lista.m3u")
        with open(playlist, "w", encoding="utf-8") as handle:
            handle.write("#EXTM3U\n#EXTINF:180,Artista A, Artista B - Faixa\n")
            handle.write("../songs/_library/Faixa [abc123].mp3\n")
        return root, library, playlists, original, playlist

    def test_simulacao_nao_move_arquivos_links_ou_m3u(self):
        with tempfile.TemporaryDirectory() as temp:
            root, library, _, original, playlist = self._preparar_layout_antigo(temp)
            with open(playlist, encoding="utf-8") as handle:
                before = handle.read()
            with patch.object(reverter_biblioteca, "artistas_das_tags", return_value=[]):
                resultado = reverter_biblioteca.reverter(root, aplicar=False)

            self.assertEqual(resultado["encontradas"], 1)
            self.assertTrue(os.path.isfile(original))
            self.assertTrue(os.path.isdir(library))
            self.assertTrue(os.path.islink(os.path.join(root, "Artista A", os.path.basename(original))))
            with open(playlist, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), before)

    def test_aplica_artista_principal_links_secundarios_e_atualiza_m3u(self):
        with tempfile.TemporaryDirectory() as temp:
            root, library, _, original, playlist = self._preparar_layout_antigo(temp)
            with patch.object(
                reverter_biblioteca,
                "artistas_das_tags",
                return_value=["Artista A", "Artista B"],
            ):
                resultado = reverter_biblioteca.reverter(root, aplicar=True)

            principal = os.path.join(root, "Artista A", os.path.basename(original))
            colaborador = os.path.join(root, "Artista B", os.path.basename(original))
            self.assertEqual(resultado["movidas"], 1)
            self.assertTrue(os.path.isfile(principal))
            self.assertFalse(os.path.islink(principal))
            self.assertTrue(os.path.islink(colaborador))
            self.assertEqual(os.path.realpath(colaborador), os.path.realpath(principal))
            self.assertFalse(os.path.exists(library))
            with open(playlist, encoding="utf-8") as handle:
                conteudo = handle.read()
            self.assertIn("../songs/Artista A/Faixa [abc123].mp3", conteudo)
            self.assertNotIn("songs/_library", conteudo)
            segunda_execucao = reverter_biblioteca.reverter(root, aplicar=True)
            self.assertEqual(segunda_execucao["encontradas"], 0)
            self.assertTrue(os.path.isfile(principal))

    def test_conflito_na_pasta_principal_nao_sobrescreve_audio_existente(self):
        with tempfile.TemporaryDirectory() as temp:
            root, library, _, original, playlist = self._preparar_layout_antigo(temp)
            principal_folder = os.path.join(root, "Artista A")
            conflict = os.path.join(principal_folder, os.path.basename(original))
            # Retira o antigo symlink da pasta principal e coloca outro áudio.
            os.remove(conflict)
            with open(conflict, "wb") as audio:
                audio.write(b"different existing audio")

            with patch.object(
                reverter_biblioteca,
                "artistas_das_tags",
                return_value=["Artista A", "Artista B"],
            ):
                resultado = reverter_biblioteca.reverter(root, aplicar=True)

            with open(conflict, "rb") as audio:
                self.assertEqual(audio.read(), b"different existing audio")
            self.assertEqual(resultado["movidas"], 1)
            principal_files = os.listdir(principal_folder)
            hashed_name = next(name for name in principal_files if name.startswith("Faixa [abc123] ["))
            canonical = os.path.join(principal_folder, hashed_name)
            old_collaborator_link = os.path.join(
                root, "Artista B", os.path.basename(original)
            )
            self.assertTrue(os.path.isfile(canonical))
            self.assertTrue(os.path.islink(old_collaborator_link))
            self.assertEqual(os.path.realpath(old_collaborator_link), os.path.realpath(canonical))
            self.assertFalse(os.path.exists(library))
            with open(playlist, encoding="utf-8") as handle:
                conteudo = handle.read()
            self.assertIn(f"../songs/Artista A/{hashed_name}", conteudo)

    def test_sem_tags_e_sem_links_nao_inventa_artista(self):
        with tempfile.TemporaryDirectory() as temp:
            root = os.path.join(temp, "songs")
            library = os.path.join(root, "_library")
            os.makedirs(library)
            original = os.path.join(library, "Faixa.mp3")
            with open(original, "wb") as audio:
                audio.write(b"audio")

            with patch.object(reverter_biblioteca, "artistas_das_tags", return_value=[]):
                planos, avisos = reverter_biblioteca.planejar(root)

            self.assertEqual(planos, [])
            self.assertIn("sem artista nas tags ou links", avisos[0])
            self.assertTrue(os.path.isfile(original))

    def test_estado_sqlite_preserva_ordem_original_dos_artistas(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _, _, original, _ = self._preparar_layout_antigo(temp)
            state_file = os.path.join(temp, "pltube_state.sqlite3")
            with sqlite3.connect(state_file) as conexao:
                conexao.execute(
                    "CREATE TABLE video_cache "
                    "(video_id TEXT, status TEXT, artists_json TEXT, path TEXT)"
                )
                conexao.execute(
                    "INSERT INTO video_cache VALUES (?, ?, ?, ?)",
                    ("abc123", "complete", '["Artista B", "Artista A"]', original),
                )

            with patch.object(reverter_biblioteca, "artistas_das_tags", return_value=[]):
                planos, avisos = reverter_biblioteca.planejar(root, state_file)

            self.assertEqual(avisos, [])
            self.assertEqual(planos[0].artistas, ("Artista B", "Artista A"))
            self.assertEqual(
                planos[0].destino,
                os.path.join(root, "Artista B", os.path.basename(original)),
            )


if __name__ == "__main__":
    unittest.main()
