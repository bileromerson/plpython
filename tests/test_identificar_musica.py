"""Identificação pelo YouTube Music e os sinais fortes de erro."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import identificar_musica as im
from ytm_falso import YTMFalso, album, artistas, faixa


def identificar(titulo, canal="Canal Qualquer", segundos=200, api=None, video="vvvvvvvvvvv", **kw):
    cliente = im.ClienteYTMusic(api, pausa=0) if api is not None else None
    meta = {"id": video, "title": titulo, "uploader": canal, "duration": segundos, **kw.pop("meta", {})}
    return im.identificar(meta, cliente=cliente, **kw)


class FontesTests(unittest.TestCase):
    def test_faixa_oficial_no_youtube_music_traz_album_numero_e_capa(self):
        api = YTMFalso(
            videos=[faixa("aaaaaaaaaaa", "Believer", ["Imagine Dragons"], album="Evolve", album_id="MPREb_ev", ano="2017")],
            albuns={"MPREb_ev": album("Evolve", ["Imagine Dragons"], [("x1", "Next To Me"), ("aaaaaaaaaaa", "Believer")], "2017")},
        )
        r = identificar("Believer", "Imagine Dragons - Topic", api=api, video="aaaaaaaaaaa")
        m = r["metadata"]
        self.assertEqual((r["status"], m["artists"], m["title"], m["album"]), ("RESOLVIDO", ["Imagine Dragons"], "Believer", "Evolve"))
        self.assertEqual((m["track_number"], m["track_total"], m["date"]), (2, 2, "2017"))
        self.assertEqual(m["cover_source"], "youtube_music")
        self.assertTrue(m["cover_url"].endswith("=w544-h544-l90-rj"))
        self.assertEqual(r["provenance"]["artists"], "youtube_music")

    def test_clipe_oficial_usa_a_faixa_de_audio_correspondente(self):
        audio = faixa("bbbbbbbbbbb", "Thunderstruck", ["AC/DC"], album="The Razors Edge", segundos=292)
        clipe = faixa("ccccccccccc", "Thunderstruck (Official Video)", ["AC/DC"], tipo="OMV", segundos=293,
                      contraparte=audio)
        r = identificar("AC/DC - Thunderstruck (Official Video)", "AC/DC", 293, YTMFalso([clipe]), video="ccccccccccc")
        self.assertEqual((r["metadata"]["artists"], r["metadata"]["title"], r["metadata"]["album"]),
                         (["AC/DC"], "Thunderstruck", "The Razors Edge"))
        self.assertEqual(r["provenance"]["artists"], "youtube_music_clipe")

    def test_clipe_sem_faixa_de_audio_limpa_o_titulo(self):
        clipe = faixa("ccccccccccc", "Seven Lions - Not Even Love (Official Visualizer)", ["Seven Lions"], tipo="OMV")
        r = identificar("Seven Lions - Not Even Love (Official Visualizer)", "Seven Lions", api=YTMFalso([clipe]),
                        video="ccccccccccc")
        self.assertEqual((r["metadata"]["artists"], r["metadata"]["title"]), (["Seven Lions"], "Not Even Love"))

    def test_reupload_e_achado_no_catalogo(self):
        api = YTMFalso(
            videos=[faixa("ddddddddddd", "Believer - Imagine Dragons (Lyrics)", ["Lyrics Hub"], tipo="UGC")],
            catalogo=[faixa("aaaaaaaaaaa", "Believer", ["Imagine Dragons"], album="Evolve", segundos=204)],
        )
        r = identificar("Believer - Imagine Dragons (Lyrics)", "Lyrics Hub", 205, api, video="ddddddddddd")
        self.assertEqual((r["status"], r["metadata"]["artists"], r["metadata"]["title"], r["metadata"]["album"]),
                         ("RESOLVIDO", ["Imagine Dragons"], "Believer", "Evolve"))
        self.assertEqual(r["provenance"]["artists"], "youtube_music_busca")
        self.assertEqual(r["candidates"][0]["artists"], ["Imagine Dragons"])  # aparece como :db no review

    def test_so_o_nome_da_musica_vale_com_a_mesma_duracao(self):
        catalogo = [faixa("aaaaaaaaaaa", "Thunderstruck", ["AC/DC"], segundos=292)]
        r = identificar("Thunderstruck", "Fulano Uploads", 293, YTMFalso(catalogo=catalogo))
        self.assertEqual(r["metadata"]["artists"], ["AC/DC"])
        r = identificar("Thunderstruck", "Fulano Uploads", 180, YTMFalso(catalogo=catalogo))  # outra música
        self.assertNotEqual(r["metadata"]["artists"], ["AC/DC"])

    def test_sobra_do_titulo_explicada_pelo_album(self):
        catalogo = [faixa("aaaaaaaaaaa", "Bella Ciao (Música Original de la Serie)", ["Manu Pilas"],
                          album="La Casa de Papel", segundos=150)]
        r = identificar("Bella Ciao - La Casa de Papel", "Musicas Top", 151, YTMFalso(catalogo=catalogo))
        self.assertEqual((r["metadata"]["artists"], r["metadata"]["album"]), (["Manu Pilas"], "La Casa de Papel"))

    def test_versao_diferente_nao_e_a_mesma_musica(self):
        catalogo = [faixa("aaaaaaaaaaa", "Heat Waves", ["Glass Animals"], segundos=238)]
        r = identificar("Glass Animals - Heat Waves (slowed + reverb)", "sad vibes", 290, YTMFalso(catalogo=catalogo))
        self.assertEqual(r["metadata"]["title"], "Heat Waves (slowed + reverb)")
        self.assertNotEqual(r["provenance"]["artists"], "youtube_music_busca")

    def test_creditos_do_youtube_e_versao_no_titulo(self):
        r = identificar("Heat Waves (slowed + reverb)", "sad vibes", 290,
                        meta={"track": "Heat Waves", "artists": ["Glass Animals"]})
        self.assertEqual((r["metadata"]["artists"], r["metadata"]["title"]), (["Glass Animals"], "Heat Waves (slowed + reverb)"))
        self.assertEqual((r["status"], r["provenance"]["artists"]), ("RESOLVIDO", "youtube_creditos"))

    def test_creditos_sem_album_completam_pelo_catalogo(self):
        catalogo = [faixa("aaaaaaaaaaa", "Terremoto", ["Anitta", "Kevinho"], album="Terremoto", segundos=180)]
        r = identificar("Anitta & Kevinho - Terremoto [Official Music Video]", "Anitta", 182, YTMFalso(catalogo=catalogo),
                        meta={"track": "Terremoto", "artists": ["Anitta", "Kevinho"]})
        self.assertEqual((r["metadata"]["album"], r["metadata"]["cover_source"]), ("Terremoto", "youtube_music"))

    def test_descricao_do_youtube_music(self):
        r = identificar("Thunderstruck", "AC/DC - Topic", meta={
            "youtube_music": {"title": "Thunderstruck", "artists": ["AC/DC"], "album": "The Razors Edge"}})
        self.assertEqual((r["status"], r["metadata"]["artists"], r["metadata"]["album"]),
                         ("RESOLVIDO", ["AC/DC"], "The Razors Edge"))

    def test_sem_ytmusicapi_avisa_uma_vez_e_segue(self):
        avisos = []
        with mock.patch.dict(sys.modules, {"ytmusicapi": None}):
            cliente = im.ClienteYTMusic(avisar=avisos.append, pausa=0)
            self.assertIsNone(cliente.faixa("aaaaaaaaaaa"))
            self.assertEqual(cliente.buscar("x"), [])
        self.assertEqual(len(avisos), 1)
        self.assertIn("pip install", avisos[0])

    def test_youtube_music_fora_do_ar_nao_derruba(self):
        api = YTMFalso()  # get_watch_playlist levanta exceção, busca não acha nada
        r = identificar("Glyde // Latte", "Glyde", api=api)
        self.assertEqual((r["metadata"]["artists"], r["metadata"]["title"]), (["Glyde"], "Latte"))


class DeduzidoTests(unittest.TestCase):
    """Sem YouTube Music: o título e o canal, como nas primeiras versões."""

    CASOS = (  # (título do vídeo, canal, artistas do banco, artistas, música)
        ('Cult of the Lamb Song - "Fool\'s Gold" by ivycomb', "Ivycomb Music", [], ["ivycomb"], "Fool's Gold"),
        ("INSTINCT - 02. Instinct", "NIIC", [], ["NIIC"], "Instinct"),
        ("Domalomn - Urgently Jammin' (Sir Meow Remix)", "Sir Meow", [], ["Domalomn"], "Urgently Jammin' (Sir Meow Remix)"),
        ("Just a Little Better - snuffles", "snuffles", [], ["snuffles"], "Just a Little Better"),
        ("THIS THOUGHT - Frizk & snuffles", "snuffles", [], ["Frizk", "snuffles"], "THIS THOUGHT"),
        ("Seven Lions & ILLENIUM ft. ÁSDÍS - Not Even Love (Official Visualizer)", "Seven Lions", [],
         ["Seven Lions", "ILLENIUM", "ÁSDÍS"], "Not Even Love"),
        ("[Dubstep] - Tut Tut Child - Hummingbird (feat. Augustus Ghost) [Monstercat Release]", "Monstercat Uncaged", [],
         ["Tut Tut Child", "Augustus Ghost"], "Hummingbird"),
        ("My Ordinary Life-The Living Tombstone", "The Living Tombstone", [], ["The Living Tombstone"], "My Ordinary Life"),
        ("twenty one pilots: Goner", "twenty one pilots", [], ["twenty one pilots"], "Goner"),
        ("INFAMOUS (with YonKaGor and Stephanafro)", "Ivycomb Music", [], ["Ivycomb", "YonKaGor", "Stephanafro"], "INFAMOUS"),
        ("Liberdade Provisória - Henrique e Juliano - com cifras", "Fulano Cifras", ["Henrique & Juliano"],
         ["Henrique & Juliano"], "Liberdade Provisória"),
        ("ACDC Thunderstruck ( official video )", "Some Guy", ["AC/DC"], ["AC/DC"], "Thunderstruck"),
        ("Earth Wind & Fire “September ”", "Rand", ["Earth, Wind & Fire"], ["Earth, Wind & Fire"], "September"),
        ("Imagine Dragons - Believer (Official Music Video)", "ImagineDragons", [], ["Imagine Dragons"], "Believer"),
        ("[FORMER FAN LYRIC VIDEO] My Demons by Starset", "GWLyrics", ["STARSET"], ["STARSET"], "My Demons"),
        ("[VRChat] I'M IN LOVE WITH A FOX BOY ON THE INTERNET 🦊 (WHSPRS - Fox Boy)", "WHSPRS", [], ["WHSPRS"], "Fox Boy"),
        ("Fulano & Ciclano - Canção", "Canal Qualquer", [], ["Fulano", "Ciclano"], "Canção"),
    )

    def test_casos(self):
        for titulo, canal, banco, artistas_esperados, musica in self.CASOS:
            r = identificar(titulo, canal, conhecidos=banco)
            self.assertEqual((r["metadata"]["artists"], r["metadata"]["title"]), (artistas_esperados, musica), titulo)
            self.assertEqual(r["status"], "RESOLVIDO", (titulo, r["observations"]))

    def test_review_all_confere_tambem_o_deduzido(self):
        r = identificar("Coisa Um - Coisa Dois", revisar="all")
        self.assertEqual(r["status"], "OBSERVAR")
        self.assertIn("deduzidos", r["observations"][0])
        self.assertEqual(identificar("Coisa Um - Coisa Dois")["status"], "RESOLVIDO")


class SinaisDeErroTests(unittest.TestCase):
    def sinais(self, titulo, artistas_, fonte="titulo", **kw):
        return im.sinais_de_erro({"title": titulo, "artists": artistas_}, fonte, **kw)

    def test_artista_com_cara_de_titulo(self):
        for nome in ("The Living Tombstone (FNAF2)", "Season 2022 Cinematic - League of Legends (ft. 2WEI",
                     "2. I MONSTER", "[Dubstep]", "Astronaut In The Ocean (Alok Remix)", "Charlie Puth Furious 7 Soundtrack"):
            self.assertTrue(any(s.startswith("artista parece") for s in self.sinais("Música", [nome])), nome)
        self.assertEqual(len(self.sinais("Música", ["Lyrics Hub (Official)"])), 1)  # um aviso por faixa
        self.assertEqual(self.sinais("Música", ["Earth, Wind & Fire"]), [])

    def test_canal_de_reupload_ou_selo_como_artista(self):
        self.assertIn("re-upload", self.sinais("Believer", ["Lyrics Hub"])[0])
        self.assertIn("re-upload", self.sinais("Tema", ["La Casa de Papel OST"])[0])
        self.assertIn("re-upload", self.sinais("X", ["Fulano"], canal="Fulano", canal_tipo="curador")[0])
        # o catálogo do YouTube Music (gravadora) não é questionado
        self.assertEqual(self.sinais("Lo-fi Night", ["Lofi Fruits Music"], fonte="youtube_music"), [])

    def test_sobras_no_titulo(self):
        for titulo in ("Believer (Lyrics) #shorts", "02. Instinct", 'Cult Song - "Fool\'s Gold" by ivycomb', "Song Official Video"):
            self.assertTrue(any(s.startswith("título com sobras") for s in self.sinais(titulo, ["A"])), titulo)
        self.assertEqual(self.sinais("Stand by Me", ["Ben E. King"], fonte="youtube_music"), [])

    def test_invertido_quando_o_titulo_e_um_artista_conhecido(self):
        s = self.sinais("Ed Sheeran", ["Bad Habits"], conhecidos=["Ed Sheeran"])
        self.assertIn("invertidos", s[0])
        self.assertEqual(self.sinais("Bad Habits", ["Ed Sheeran"], conhecidos=["Ed Sheeran"]), [])

    def test_video_longo(self):
        self.assertIn("vídeo longo", self.sinais("Mix", ["A"], duracao=3600)[0])
        self.assertEqual(self.sinais("Suite", ["A"], fonte="youtube_music", duracao=3600), [])

    def test_sinal_manda_para_conferencia_e_aponta_o_campo(self):
        r = identificar("Lofi beats to study 24/7 compilation", "Chill Vibes Records", 3600)
        self.assertEqual(r["status"], "OBSERVAR")
        from comandos.review import campos_com_problema
        self.assertIn("artists", campos_com_problema(r))
        self.assertEqual(identificar("Lofi", "Chill Vibes Records", 3600, revisar="none")["status"], "RESOLVIDO")


class TextoTests(unittest.TestCase):
    def test_ler_faixa(self):
        f = im.ler_faixa({"videoId": "a", "title": "T", "length": "1:02:03", "videoType": "MUSIC_VIDEO_TYPE_ATV",
                          "artists": artistas("Jorge & Mateus") + [{"name": "Fulano & Beltrano", "id": None}],
                          "counterpart": faixa("b", "T", ["X"], tipo="OMV")})
        self.assertEqual((f["duration"], f["tipo"], f["artists"]), (3723, "musica", ["Jorge & Mateus", "Fulano", "Beltrano"]))
        self.assertEqual(f["contraparte"]["tipo"], "clipe")
        self.assertIsNone(im.ler_faixa({"title": ""}))

    def test_url_capa(self):
        self.assertEqual(im.url_capa("https://lh3.googleusercontent.com/abc=w60-h60-l90-rj"),
                         "https://lh3.googleusercontent.com/abc=w544-h544-l90-rj")
        self.assertEqual(im.url_capa("https://i.ytimg.com/vi/x/hq.jpg"), "")

    def test_limpar_titulo(self):
        casos = {
            "Believer (Official Music Video) [HD] #believer": "Believer",
            "[Dubstep] - Hummingbird": "Hummingbird",
            "02. Instinct": "Instinct",
            "Song (Lyrics) | Letra": "Song",
            "Heat Waves (slowed + reverb)": "Heat Waves (slowed + reverb)",
            "Liberdade Provisória - Henrique e Juliano - com cifras": "Liberdade Provisória - Henrique e Juliano",
        }
        for entrada, esperado in casos.items():
            self.assertEqual(im.limpar_titulo(entrada), esperado, entrada)

    def test_versoes(self):
        self.assertEqual(im.versoes("Song (Sped Up + Slowed)"), {"sped up", "slowed"})
        self.assertEqual(im.versoes("Song (Alok Remix)"), {"remix"})
        self.assertEqual(im.versoes("Song (Live at Wembley)"), {"ao vivo"})
        self.assertEqual(im.versoes("Song"), set())


if __name__ == "__main__":
    unittest.main()


class DownloadComYouTubeMusicTests(unittest.TestCase):
    """O download inteiro com o YouTube Music falso: identifica, grava no banco e manda a capa."""

    def test_download_usa_o_youtube_music(self):
        import csv
        import tempfile
        import pltube

        api = YTMFalso(
            videos=[faixa("abc123abc12", "Believer - Imagine Dragons (Lyrics)", ["Lyrics Hub"], tipo="UGC")],
            catalogo=[faixa("aaaaaaaaaaa", "Believer", ["Imagine Dragons"], album="Evolve", album_id="MPREb_ev", segundos=204)],
            albuns={"MPREb_ev": album("Evolve", ["Imagine Dragons"], [("aaaaaaaaaaa", "Believer")], "2017")},
        )
        with tempfile.TemporaryDirectory() as temp:
            with open(os.path.join(temp, "lista.csv"), "w", encoding="utf-8", newline="") as h:
                csv.writer(h).writerows([["id", "titulo", "url"], ["1", "x", "https://youtube.com/watch?v=abc123abc12"]])
            songs = os.path.join(temp, "songs")
            meta = {"title": "Believer - Imagine Dragons (Lyrics)", "artists": [], "uploader": "Lyrics Hub",
                    "duration": 205, "id": "abc123abc12"}
            baixados = []

            def baixar(url, artistas_, titulo, video_id, csv_file, hybrid_result=None):
                caminho = os.path.join(songs, artistas_[0], f"{titulo} [{video_id}].mp3")
                os.makedirs(os.path.dirname(caminho), exist_ok=True)
                open(caminho, "wb").close()
                baixados.append((artistas_, titulo, hybrid_result["metadata"]["cover_source"]))
                return caminho

            with mock.patch.multiple(pltube, BASE_DIR=temp, SONGS_DIR=songs, PLAYLISTS_DIR=os.path.join(temp, "pl"),
                                     LOG_FILE=os.path.join(temp, "Logs.txt"), ROW=2, MODELO_HIBRIDO=True,
                                     YTMUSIC=im.ClienteYTMusic(api, pausa=0), ARTISTAS_CONHECIDOS=[]), \
                    mock.patch.object(pltube, "extrair_metadados", return_value=meta), \
                    mock.patch.object(pltube, "baixar_musica", side_effect=baixar):
                estado = pltube.abrir_banco_estado(os.path.join(temp, "sql", "downloads.sqlite3"))
                try:
                    self.assertTrue(pltube.processar_csv("lista.csv", estado=estado, indice_local={}))
                    salvo = pltube.carregar_resolucao(estado, "youtube:abc123abc12")["result"]
                    import banco_artistas
                    no_banco = banco_artistas.canonico(estado, "imagine dragons")
                finally:
                    estado.close()
        self.assertEqual(baixados, [(["Imagine Dragons"], "Believer", "youtube_music")])
        self.assertEqual((salvo["status"], salvo["metadata"]["album"], salvo["metadata"]["track_number"]),
                         ("RESOLVIDO", "Evolve", 1))
        self.assertEqual(no_banco, "Imagine Dragons")  # fonte confiável: entra no banco de artistas
