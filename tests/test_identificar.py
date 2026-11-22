"""Identificação: código do vídeo → pastas/banco → música + artista no YouTube Music →
título + duração → palpite. Usa um YouTube Music falso (formato da ytmusicapi)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import identificar_musica as im  # noqa: E402


def musica(titulo, artistas, duracao=200, video="zzzzzzzzzzz", album=""):
    return {"title": titulo, "artists": [{"name": a, "id": "UC" + a} for a in artistas],
            "videoId": video, "resultType": "song", "duration_seconds": duracao,
            "album": {"name": album, "id": ""} if album else None}


class APIFalsa:
    def __init__(self, buscas=None, videos=None):
        self.buscas = buscas or {}
        self.videos = videos or {}
        self.chamadas = []

    def get_watch_playlist(self, videoId, limit=1):
        self.chamadas.append(("vídeo", videoId))
        return {"tracks": [self.videos[videoId]] if videoId in self.videos else []}

    def search(self, consulta, filter="songs", limit=10):
        self.chamadas.append(("busca", consulta))
        return self.buscas.get(consulta, [])

    def get_album(self, browse_id):
        return None


def video(titulo, canal="Canal Qualquer", duracao=200, vid="aaaaaaaaaaa"):
    return {"id": vid, "title": titulo, "uploader": canal, "duration": duracao}


class Pastas:
    def __init__(self, nomes, arquivos=None):
        self.dir = tempfile.TemporaryDirectory()
        for nome in nomes:
            os.makedirs(os.path.join(self.dir.name, nome))
        for pasta, arquivo in (arquivos or []):
            os.makedirs(os.path.join(self.dir.name, pasta), exist_ok=True)
            open(os.path.join(self.dir.name, pasta, arquivo), "wb").close()


class TestIdentificar(unittest.TestCase):
    def rodar(self, meta, api=None, pastas=(), arquivos=None, banco=None, duvidosos=()):
        p = Pastas(pastas, arquivos)
        self.addCleanup(p.dir.cleanup)
        artistas = im.ArtistasConhecidos(p.dir.name, banco or {}, duvidosos)
        cliente = im.ClienteYTMusic(api=api or APIFalsa(), pausa=0)
        r = im.identificar(meta, cliente=cliente, artistas=artistas)
        return r["status"], r["metadata"]["artists"], r["metadata"]["title"], r["provenance"]["artists"]

    def test_codigo_do_video_oficial(self):
        api = APIFalsa(videos={"aaaaaaaaaaa": {**musica("Believer", ["Imagine Dragons"], video="aaaaaaaaaaa"),
                                               "videoType": "MUSIC_VIDEO_TYPE_ATV"}})
        self.assertEqual(self.rodar(video("Believer"), api),
                         ("CONFIRMADO", ["Imagine Dragons"], "Believer", im.FONTE_MUSICA))

    def test_pasta_existente_nao_consulta_servidor(self):
        api = APIFalsa()
        r = self.rodar(video("snuffles - Keep on With It", "Chill Nation"), api, pastas=["snuffles"])
        self.assertEqual(r, ("CONFIRMADO", ["snuffles"], "Keep on With It", im.FONTE_PASTA))
        self.assertFalse([c for c in api.chamadas if c[0] == "busca"])

    def test_pasta_com_grafia_diferente(self):
        r = self.rodar(video("ImagineDragons - Thunder"), pastas=["Imagine Dragons"])
        self.assertEqual(r[:3], ("CONFIRMADO", ["Imagine Dragons"], "Thunder"))

    def test_pasta_com_nome_de_musica_nao_conta(self):
        # songs/Believer/Believer [x].mp3: pasta criada com o título no lugar do artista
        api = APIFalsa(buscas={"Believer - Imagine Dragons": [musica("Believer", ["Imagine Dragons"], 204)]})
        r = self.rodar(video("Believer - Imagine Dragons", "Lyrics Hub", 204), api,
                       arquivos=[("Believer", "Believer [bbbbbbbbbbb].mp3")])
        self.assertEqual(r, ("CONFIRMADO", ["Imagine Dragons"], "Believer", im.FONTE_BUSCA))

    def test_pasta_so_de_palpites_nao_conta(self):
        r = self.rodar(video("Fulano - Música Legal"), pastas=["Fulano"], duvidosos=["Fulano"])
        self.assertEqual(r[0], "NAO_CONFIRMADO")

    def test_busca_artista_e_titulo(self):
        api = APIFalsa(buscas={"Imagine Dragons - Believer": [musica("Believer", ["Imagine Dragons"], 204,
                                                                     album="Evolve")]})
        r = self.rodar(video("Imagine Dragons - Believer (Official Lyrics)", "Lyrics Hub", 204), api)
        self.assertEqual(r, ("CONFIRMADO", ["Imagine Dragons"], "Believer", im.FONTE_BUSCA))

    def test_titulo_invertido_nao_vira_artista(self):
        # "Believer - Imagine Dragons": o artista é o lado direito, achado pela busca
        api = APIFalsa(buscas={"Believer - Imagine Dragons": [musica("Believer", ["Imagine Dragons"], 204)]})
        r = self.rodar(video("Believer - Imagine Dragons", "Lyrics Hub", 204), api)
        self.assertEqual(r[:3], ("CONFIRMADO", ["Imagine Dragons"], "Believer"))

    def test_artista_com_nome_de_musica_nao_confirma(self):
        # existe um artista chamado "Believer", mas a música dele não é "Imagine Dragons"
        api = APIFalsa(buscas={"Believer - Imagine Dragons": [musica("Outra Coisa", ["Believer"], 100)]})
        r = self.rodar(video("Believer - Imagine Dragons", duracao=204), api)
        self.assertEqual(r[0], "NAO_CONFIRMADO")
        self.assertEqual(r[1], ["Believer"])  # palpite "Artista - Música", só para a pasta provisória

    def test_comparacao_titulo_e_duracao(self):
        api = APIFalsa(buscas={"Thunderstruck": [musica("Thunderstruck", ["AC/DC"], 292)]})
        r = self.rodar(video("Thunderstruck", "Rock Hits Records", 293), api)
        self.assertEqual(r, ("CONFIRMADO", ["AC/DC"], "Thunderstruck", im.FONTE_COMPARACAO))

    def test_duracao_diferente_nao_confirma(self):
        api = APIFalsa(buscas={"Thunderstruck": [musica("Thunderstruck", ["AC/DC"], 292)]})
        r = self.rodar(video("Thunderstruck", "Rock Hits Records", 330), api)
        self.assertEqual(r[0], "NAO_CONFIRMADO")

    def test_versao_diferente_nao_confirma(self):
        api = APIFalsa(buscas={"Heat Waves (slowed + reverb)": [musica("Heat Waves", ["Glass Animals"], 238)]})
        r = self.rodar(video("Heat Waves (slowed + reverb)", "Slowed Vibes", 238), api)
        self.assertEqual(r[0], "NAO_CONFIRMADO")

    def test_canal_entra_na_busca(self):
        api = APIFalsa(buscas={"snuffles Won't Go": [musica("Won't Go", ["snuffles"], 180)]})
        r = self.rodar(video("Won't Go", "snuffles", 999), api)
        self.assertEqual(r[:3], ("CONFIRMADO", ["snuffles"], "Won't Go"))

    def test_banco_corrige_variacao(self):
        r = self.rodar(video("Henrique e Juliano - Liberdade Provisória"),
                       banco={"henriqueejuliano": "Henrique & Juliano"})
        self.assertEqual(r[:3], ("CONFIRMADO", ["Henrique & Juliano"], "Liberdade Provisória"))

    def test_dois_lados_conhecidos_fica_com_o_esquerdo(self):
        r = self.rodar(video("Alok - Hear Me Now"), pastas=["Alok", "Hear Me Now"])
        self.assertEqual(r[:3], ("CONFIRMADO", ["Alok"], "Hear Me Now"))


if __name__ == "__main__":
    unittest.main()
