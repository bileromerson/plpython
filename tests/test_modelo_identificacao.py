"""Modelo de identificação: canal x artista, álbum, capa e lista de conferência."""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import pltube
import resolvedor_metadados as r

UUID = "{:08d}-1111-1111-1111-111111111111"


def release(n, title, *, status="Official", tipo="Album", secundarios=(), data="2020-01-01",
            faixa=3, total=10, artistas=("The Score",)):
    return {"id": UUID.format(n), "title": title, "date": data, "status": status,
            "release_group_id": UUID.format(n + 500), "primary_type": tipo,
            "secondary_types": list(secundarios), "track_number": faixa, "track_total": total,
            "disc_number": 1, "disc_total": 1, "artists": list(artistas), "artist_ids": []}


def gravacao(title, artists, releases=(), n=1, duration=200):
    return {"recording_id": UUID.format(n), "title": title, "artists": list(artists),
            "artist_ids": [UUID.format(900 + i) for i, _ in enumerate(artists)],
            "duration": duration, "isrcs": [], "releases": list(releases)}


class FakeMB:
    """Simula a busca do MusicBrainz: casa título e artista pelo texto."""

    def __init__(self, gravacoes, capas=None):
        self.gravacoes, self.capas, self.buscas = gravacoes, capas or {}, []

    def search_by_isrc(self, isrc, limit=10):
        return []

    def search_by_title_artist(self, title, artist, limit=10):
        self.buscas.append((title, artist))
        return [g for g in self.gravacoes
                if r._similarity(g["title"], title) > 0.6
                and any(r._mesmo_nome(a, artist) or r._similarity(" ".join(g["artists"]), artist) > 0.7
                        for a in g["artists"])]

    def search_by_title(self, title, limit=10):
        self.buscas.append((title, ""))
        return [g for g in self.gravacoes if r._similarity(g["title"], title) > 0.8]

    def get_album_cover(self, release_group_id="", release_id=""):
        return self.capas.get(release_group_id) or self.capas.get(release_id)


def resolver(yt, gravacoes=(), capas=None, **kw):
    dados = {"duration": 200, "thumbnail": "https://i.ytimg.com/vi/x/maxresdefault.jpg", **yt}
    return r.resolver_com_prioridade("vid", dados, musicbrainz_client=FakeMB(list(gravacoes), capas), **kw)


class CanalEArtistaTests(unittest.TestCase):
    def test_canal_oficial_album_e_capa_resolvem_sem_conferencia(self):
        rels = [release(1, "Greatest Hits", secundarios=["Compilation"], data="2018"),
                release(2, "Pressure", data="2019-08-30", faixa=5, total=12)]
        res = resolver({"title": "The Score - Legend (Audio)", "uploader": "The Score"},
                       [gravacao("Legend", ["The Score"], rels)],
                       capas={UUID.format(502): "https://coverartarchive.org/x-1200.jpg"})
        m = res.metadata
        self.assertEqual(res.status, "RESOLVIDO", res.observations)
        self.assertEqual((m["artists"], m["title"], m["album"]), (["The Score"], "Legend", "Pressure"))
        self.assertEqual((m["track_number"], m["track_total"], m["date"]), (5, 12, "2019-08-30"))
        self.assertEqual(m["cover_source"], "cover_art_archive")
        self.assertEqual(m["musicbrainz_release_group_id"], UUID.format(502))

    def test_reupload_nao_usa_o_canal_e_acha_a_ordem_certa_no_musicbrainz(self):
        res = resolver({"title": "Won't Go - snuffles", "uploader": "ChillVibes Lofi"},
                       [gravacao("Won't Go", ["snuffles"], [release(3, "Won't Go", tipo="Single", artistas=["snuffles"])])],
                       capas={UUID.format(503): "https://c/1.jpg"})
        self.assertEqual(res.policy["canal"]["tipo"], "curador")
        self.assertEqual((res.metadata["artists"], res.metadata["title"]), (["snuffles"], "Won't Go"))
        self.assertEqual(res.status, "RESOLVIDO", res.observations)

    def test_reupload_sem_musicbrainz_vai_para_conferencia(self):
        res = resolver({"title": "Won't Go - snuffles", "uploader": "ChillVibes Lofi"})
        self.assertEqual(res.status, "OBSERVAR")
        self.assertTrue(any("deduzidos" in o for o in res.observations))
        self.assertNotIn("ChillVibes", " ".join(res.metadata["artists"]))

    def test_artista_ja_conhecido_decide_o_lado_do_titulo(self):
        res = resolver({"title": "Won't Go - snuffles", "uploader": "ChillVibes Lofi"},
                       artistas_conhecidos=["snuffles"])
        self.assertEqual(res.metadata["artists"], ["snuffles"])
        self.assertFalse(any("deduzidos" in o for o in res.observations))

    def test_canal_aprendido_como_reupload_nunca_vira_artista(self):
        yt = {"title": "Lavender Town", "uploader": "GameMusicArchive"}
        self.assertEqual(resolver(yt).metadata["artists"], ["GameMusicArchive"])  # palpite fraco
        self.assertEqual(resolver(yt).status, "OBSERVAR")
        res = resolver(yt, canal_conhecido={"kind": "curador"})
        self.assertEqual(res.status, "PENDENTE")
        res = resolver(yt, canal_conhecido={"kind": "artista", "artists": ["Junichi Masuda"]})
        self.assertEqual(res.metadata["artists"], ["Junichi Masuda"])

    def test_varios_artistas_principal_primeiro(self):
        rels = [release(4, "Fallen Down", tipo="Single", artistas=["VGR", "CG5"])]
        res = resolver({"title": "VGR, CG5 - Fallen Down (Undertale Music Video)", "uploader": "CG5"},
                       [gravacao("Fallen Down", ["VGR", "CG5"], rels)], capas={UUID.format(504): "u"})
        self.assertEqual(res.metadata["artists"], ["VGR", "CG5"])
        self.assertEqual(res.status, "RESOLVIDO", res.observations)

    def test_feat_e_ruido_do_titulo(self):
        a = r.analisar_titulo("Unknown Brain & Hoober - Phenomenon (ft. Dax & VinDon) | Hype | NCS - Copyright Free Music")
        self.assertEqual(a["lados"], ("Unknown Brain & Hoober", "Phenomenon"))
        self.assertEqual(a["feat"], ["Dax", "VinDon"])
        self.assertEqual(r.dividir_artistas("Alok & JØRD & DJ MP4"), ["Alok", "JØRD", "DJ MP4"])
        self.assertEqual(r.dividir_artistas("Mumford & Sons"), ["Mumford & Sons"])
        self.assertEqual(r.dividir_artistas("R3HAB x A Touch Of Class"), ["R3HAB", "A Touch Of Class"])

    def test_versao_slowed_mantem_a_marca_no_titulo(self):
        res = resolver({"title": "TICKING (SLOWED Version)", "artists": ["Artist"], "duration": 300},
                       [gravacao("TICKING", ["Artist"], [release(5, "TICKING", tipo="Single", artistas=["Artist"])])],
                       capas={UUID.format(505): "u"})
        self.assertEqual(res.metadata["title"], "TICKING (SLOWED Version)")
        self.assertEqual(res.provenance["title"], "youtube_validado_musicbrainz")

    def test_youtube_music_resolve_sem_musicbrainz(self):
        desc = ("Provided to YouTube by DistroKid\n\nWon't Go · snuffles\n\nWon't Go\n\n"
                "℗ 2021 snuffles\n\nReleased on: 2021-11-01\n\nAuto-generated by YouTube.")
        res = r.resolver_com_prioridade("v", {"title": "Won't Go", "uploader": "snuffles - Topic",
                                              "description": desc, "thumbnail": "t"},
                                        consultar_musicbrainz=False)
        m = res.metadata
        self.assertEqual(res.status, "RESOLVIDO", res.observations)
        self.assertEqual((m["artists"], m["album"], m["date"], m["label"]),
                         (["snuffles"], "Won't Go", "2021-11-01", "DistroKid"))
        self.assertEqual(m["cover_source"], "youtube_music_thumbnail")

    def test_artista_antigo_igual_ao_canal_nao_conta_como_credito(self):
        yt = r.dados_youtube_completos({"title": "Song", "artists": ["Some Uploader"], "uploader": "Some Uploader"})
        self.assertEqual(yt["fonte_artistas"], "canal")

    def test_escolha_do_lancamento(self):
        rec = gravacao("X", ["A"], [
            release(10, "Now That's Music 99", secundarios=["Compilation"], data="2010", artistas=["Various Artists"]),
            release(11, "X (Live)", secundarios=["Live"], data="2011", artistas=["A"]),
            release(12, "Album", data="2015", artistas=["A"]),
            release(13, "Album", data="2012", artistas=["A"]),
        ])
        escolhido, _ = r.escolher_release(rec, "", ["A"])
        self.assertEqual(escolhido["id"], UUID.format(13))  # oficial, álbum, o mais antigo
        escolhido, conf = r.escolher_release(rec, "X (Live)", ["A"])
        self.assertEqual(escolhido["id"], UUID.format(11))  # o álbum do YouTube decide
        self.assertGreaterEqual(conf, 0.95)


class MemoriaEArquivosTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = pltube.abrir_banco_estado(os.path.join(self.temp.name, "d.sqlite3"),
                                            os.path.join(self.temp.name, "m.sqlite3"))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def resultado(self, artistas, canal="snuffles", prov="youtube_validado_musicbrainz"):
        return {"status": "RESOLVIDO", "provenance": {"artists": prov},
                "metadata": {"artists": artistas, "uploader": canal, "channel_id": "UC1"}}

    def test_canal_aprende_por_maioria(self):
        chave = pltube.chave_canal({"channel_id": "UC1"})
        self.assertIsNone(pltube.perfil_canal(self.db, chave))
        pltube.aprender_canal(self.db, "v1", self.resultado(["snuffles"]))
        pltube.aprender_canal(self.db, "v2", self.resultado(["snuffles", "friend"]))
        pltube.aprender_canal(self.db, "v3", self.resultado(["Other Artist"]))  # colaboração isolada
        perfil = pltube.perfil_canal(self.db, chave)
        self.assertEqual((perfil["kind"], perfil["artists"]), ("artista", ["snuffles"]))
        pltube.aprender_canal(self.db, "v1", self.resultado(["snuffles"]))  # mesmo vídeo não vota 2x
        self.assertEqual(perfil["votos"], pltube.perfil_canal(self.db, chave)["votos"])

    def test_artistas_conhecidos_vem_das_faixas_resolvidas(self):
        pltube.salvar_resolucao(self.db, "youtube:a", {"video_id": "a", "status": "RESOLVIDO",
                                                       "metadata": {"title": "t", "artists": ["snuffles"]}})
        pltube.salvar_resolucao(self.db, "youtube:b", {"video_id": "b", "status": "OBSERVAR",
                                                       "metadata": {"title": "t", "artists": ["Palpite"]}})
        self.assertEqual(pltube.carregar_artistas_conhecidos(self.db), ["snuffles"])

    def test_dados_crus_nao_apagam_o_status_e_banco_antigo_e_consertado(self):
        pltube.salvar_resolucao(self.db, "youtube:x", {"video_id": "x", "status": "OBSERVAR",
                                                      "metadata": {"title": "t", "artists": ["a"]}})
        pltube._salvar_metadados_musica(self.db, "youtube:x", {"title": "t", "artists": ["a"], "id": "x"})
        status = lambda: self.db.execute("SELECT metadata_status FROM music.music_tracks WHERE video_key='youtube:x'").fetchone()[0]  # noqa: E731
        self.assertEqual(status(), "OBSERVAR")
        self.db.execute("UPDATE music.music_tracks SET metadata_status='NAO_VALIDADO'")
        self.db.commit()
        caminhos = {r[1]: r[2] for r in self.db.execute("PRAGMA database_list")}
        self.db.close()
        self.db = pltube.abrir_banco_estado(caminhos["main"], caminhos["music"])
        self.assertEqual(status(), "OBSERVAR")

    def test_sem_confirmacao_nao_aprende(self):
        pltube.aprender_canal(self.db, "v1", self.resultado(["x"], prov="youtube_titulo_artista_primeiro"))
        self.assertIsNone(pltube.perfil_canal(self.db, pltube.chave_canal({"channel_id": "UC1"})))

    def test_move_para_o_artista_principal_sem_sobrescrever(self):
        songs = os.path.join(self.temp.name, "songs")
        with patch.object(pltube, "SONGS_DIR", songs):
            errado = os.path.join(songs, "Canal Qualquer", "Song [id1].mp3")
            os.makedirs(os.path.dirname(errado))
            with open(errado, "wb") as f:
                f.write(b"mp3")
            os.makedirs(os.path.join(songs, "Velho"))
            os.symlink(os.path.relpath(errado, os.path.join(songs, "Velho")), os.path.join(songs, "Velho", "Song [id1].mp3"))
            novo = pltube.mover_para_artista_principal(errado, ["VGR", "CG5"])
            self.assertEqual(novo, os.path.join(songs, "VGR", "Song [id1].mp3"))
            self.assertTrue(os.path.isfile(novo))
            self.assertFalse(os.path.exists(os.path.join(songs, "Canal Qualquer")))  # pasta vazia removida
            self.assertFalse(os.path.lexists(os.path.join(songs, "Velho", "Song [id1].mp3")))  # atalho quebrado
            outro = os.path.join(songs, "X", "Song [id1].mp3")
            os.makedirs(os.path.dirname(outro))
            with open(outro, "wb") as f:
                f.write(b"outro")
            self.assertEqual(pltube.mover_para_artista_principal(outro, ["VGR"]), outro)  # não sobrescreve


class FluxoCompletoTests(unittest.TestCase):
    def test_csv_ate_pastas_links_e_sql(self):
        import csv
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "lista.csv"), "w", newline="", encoding="utf-8") as h:
                w = csv.writer(h)
                w.writerow(["#", "title", "videoUrl"])
                w.writerow(["1", "x", "https://www.youtube.com/watch?v=aaaaaaaaaaa"])
                w.writerow(["2", "y", "https://www.youtube.com/watch?v=bbbbbbbbbbb"])
            songs, playlists = os.path.join(temp, "songs"), os.path.join(temp, "playlists")
            videos = {
                "aaaaaaaaaaa": {"title": "VGR, CG5 - Fallen Down (Undertale Music Video)", "uploader": "CG5",
                                "channel_id": "UCcg5", "artists": [], "duration": 200, "id": "aaaaaaaaaaa"},
                "bbbbbbbbbbb": {"title": "Obscure B-side - Banda Nenhuma", "uploader": "Random Reuploads",
                                "channel_id": "UCrr", "artists": [], "duration": 180, "id": "bbbbbbbbbbb"},
            }
            mb = FakeMB([gravacao("Fallen Down", ["VGR", "CG5"],
                                  [release(4, "Fallen Down", tipo="Single", artistas=["VGR", "CG5"])])],
                        capas={UUID.format(504): "https://caa/fd.jpg"})

            def baixar(url, artists, title, video_id, csv_file, hybrid_result=None):
                pasta = os.path.join(songs, artists[0])
                os.makedirs(pasta, exist_ok=True)
                caminho = os.path.join(pasta, f"{title} [{video_id}].mp3")
                with open(caminho, "wb") as f:
                    f.write(b"mp3")
                return caminho

            with patch.multiple(pltube, BASE_DIR=temp, WORK_DIR=temp, SONGS_DIR=songs, PLAYLISTS_DIR=playlists,
                                LOG_FILE=os.path.join(temp, "Logs.txt"), ROW=2, ARTIST_LINKS=True,
                                MODELO_HIBRIDO=True, MUSICBRAINZ_CLIENT=mb, RESOLVER_CACHE={},
                                ARTISTAS_CONHECIDOS=[]), \
                    patch.object(pltube, "extrair_metadados", side_effect=lambda url, csv_file="": dict(videos[url[-11:]])), \
                    patch.object(pltube, "baixar_musica", side_effect=baixar), \
                    patch.object(pltube, "aplicar_tags_hibridas", return_value=True):
                db = pltube.abrir_banco_estado(os.path.join(temp, "sql", "d.sqlite3"),
                                               os.path.join(temp, "sql", "m.sqlite3"))
                try:
                    pltube.processar_csv("lista.csv", estado=db, indice_local={})
                    principal = os.path.join(songs, "VGR", "Fallen Down [aaaaaaaaaaa].mp3")
                    secundario = os.path.join(songs, "CG5", "Fallen Down [aaaaaaaaaaa].mp3")
                    self.assertTrue(os.path.isfile(principal) and not os.path.islink(principal))
                    self.assertTrue(os.path.islink(secundario))
                    self.assertEqual(os.path.realpath(secundario), os.path.realpath(principal))
                    status = dict(db.execute("SELECT video_id, metadata_status FROM music.music_tracks").fetchall())
                    self.assertEqual(status, {"aaaaaaaaaaa": "RESOLVIDO", "bbbbbbbbbbb": "OBSERVAR"})
                    # o canal de re-upload não virou artista; o palpite está no título
                    artistas_b = db.execute("SELECT artists_json FROM music.music_tracks WHERE video_id='bbbbbbbbbbb'").fetchone()[0]
                    self.assertNotIn("Random", artistas_b)
                    self.assertEqual(pltube.perfil_canal(db, "youtube:UCcg5")["kind"], "artista")
                    self.assertIn("VGR", pltube.ARTISTAS_CONHECIDOS)
                    with open(pltube.exportar_metadados_pendentes(db), encoding="utf-8-sig") as h:
                        linhas = list(csv.DictReader(h))
                    self.assertEqual([l["video_id"] for l in linhas], ["bbbbbbbbbbb"])  # só o incerto
                finally:
                    db.close()


def _mutagen_falso():
    """Implementação mínima do mutagen.id3 para testar as tags sem a dependência."""
    arquivos: dict[str, list] = {}

    class Frame:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        @property
        def HashKey(self):
            nome = type(self).__name__
            if nome in ("TXXX", "APIC"):
                return f"{nome}:{self.desc}"
            if nome == "UFID":
                return f"UFID:{self.owner}"
            return nome

        def __str__(self):
            return "\u0000".join(self.text) if hasattr(self, "text") else ""

    modulo = types.ModuleType("mutagen.id3")
    for nome in ("APIC", "TALB", "TDRC", "TIT2", "TPE1", "TPE2", "TPOS", "TPUB", "TRCK", "TSRC", "TXXX", "UFID"):
        setattr(modulo, nome, type(nome, (Frame,), {}))

    class ID3NoHeaderError(Exception):
        pass

    class ID3:
        def __init__(self, path=None):
            if path not in arquivos:
                raise ID3NoHeaderError()
            self.frames = list(arquivos[path])

        def _bate(self, frame, chave):
            return frame.HashKey == chave or frame.HashKey.startswith(chave + ":")

        def delall(self, chave):
            self.frames = [f for f in self.frames if not self._bate(f, chave)]

        def getall(self, chave):
            return [f for f in self.frames if self._bate(f, chave)]

        def __getitem__(self, chave):
            achado = self.get(chave)
            if achado is None:
                raise KeyError(chave)
            return achado

        def get(self, chave, padrao=None):
            achados = self.getall(chave)
            return achados[0] if achados else padrao

        def add(self, frame):
            self.frames.append(frame)

        def save(self, path):
            arquivos[path] = list(self.frames)

    modulo.ID3, modulo.ID3NoHeaderError = ID3, ID3NoHeaderError
    pacote = types.ModuleType("mutagen")
    pacote.id3 = modulo
    return pacote, modulo, arquivos


class TagsTests(unittest.TestCase):
    def test_tags_completas_e_capa(self):
        pacote, id3, arquivos = _mutagen_falso()
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"mutagen": pacote, "mutagen.id3": id3}):
            mp3 = os.path.join(temp, "a.mp3")
            thumb = b"\xff\xd8\xff" + b"thumb"
            arquivos[mp3] = [id3.APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=thumb)]
            resultado = {"status": "RESOLVIDO", "metadata": {
                "title": "Fallen Down", "artists": ["VGR", "CG5"], "album": "Fallen Down",
                "album_artists": ["VGR", "CG5"], "date": "2021-05-01", "track_number": 1, "track_total": 2,
                "disc_number": 1, "isrc": "USAAA2100001", "musicbrainz_recording_id": UUID.format(1),
                "musicbrainz_release_id": UUID.format(2), "musicbrainz_artist_ids": [UUID.format(3)],
                "cover_source": "youtube_thumbnail"}}
            with patch.object(pltube, "recortar_quadrado", return_value=b"\xff\xd8\xffQUADRADA") as recorte:
                self.assertTrue(pltube.aplicar_tags_hibridas(mp3, resultado))
                self.assertTrue(pltube.aplicar_tags_hibridas(mp3, resultado))
            recorte.assert_called_once()  # o marcador evita recortar de novo
            tags = id3.ID3(mp3)
            texto = lambda k: str(tags.get(k)) if tags.get(k) else ""  # noqa: E731
            self.assertEqual(tags.get("TPE1").text, ["VGR", "CG5"])
            self.assertEqual((texto("TPE2"), texto("TRCK"), texto("TDRC"), texto("TPOS")),
                             ("VGR & CG5", "1/2", "2021-05-01", "1"))
            self.assertEqual(tags.get("UFID:http://musicbrainz.org").data, UUID.format(1).encode())
            self.assertEqual(texto("TXXX:MusicBrainz Album Id"), UUID.format(2))
            self.assertEqual(tags.getall("APIC")[0].data, b"\xff\xd8\xffQUADRADA")

            resultado["metadata"].update(cover_source="cover_art_archive", cover_url="https://caa/front.jpg")
            capa = b"\xff\xd8\xffCAPA_DO_ALBUM"
            with patch.object(pltube, "_baixar_capa", return_value=capa) as baixar:
                pltube.aplicar_tags_hibridas(mp3, resultado)
                pltube.aplicar_tags_hibridas(mp3, resultado)
            baixar.assert_called_once()
            apics = id3.ID3(mp3).getall("APIC")
            self.assertEqual([a.data for a in apics], [capa])  # thumbnail substituída, sem duplicar

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg não instalado")
    def test_recorte_quadrado_real(self):
        thumb = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=1280x720", "-frames:v", "1",
             "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"], capture_output=True).stdout
        quadrada = pltube.recortar_quadrado(thumb)
        self.assertTrue(quadrada and quadrada[:3] == b"\xff\xd8\xff")
        dims = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", "-i", "pipe:0"], input=quadrada, capture_output=True).stdout
        self.assertEqual(dims.decode().strip(), "720,720")


if __name__ == "__main__":
    unittest.main()
