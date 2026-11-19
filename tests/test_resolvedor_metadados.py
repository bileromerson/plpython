"""Garantias do resolvedor que vieram da primeira versão, agora no modelo atual."""
import json
import unittest
from urllib.parse import parse_qs, urlsplit

import resolvedor_metadados as resolver


class FakeMusicBrainz:
    def __init__(self, by_isrc=None, by_title_artist=None, by_title=None, cover_url=None):
        self.by_isrc = by_isrc or []
        self.by_title_artist = by_title_artist or []
        self.by_title = by_title or []
        self.cover_url = cover_url
        self.isrc_calls, self.title_artist_calls, self.title_calls, self.cover_calls = [], [], [], []

    def search_by_isrc(self, isrc, limit=10):
        self.isrc_calls.append((isrc, limit))
        return self.by_isrc

    def search_by_title_artist(self, title, artist, limit=10):
        self.title_artist_calls.append((title, artist, limit))
        return self.by_title_artist

    def search_by_title(self, title, limit=10):
        self.title_calls.append((title, limit))
        return self.by_title

    def get_album_cover(self, release_group_id="", release_id=""):
        self.cover_calls.append((release_group_id, release_id))
        return self.cover_url


class ResolverTests(unittest.TestCase):
    def test_correcao_manual_confirmada_vence_tudo_e_nao_consulta_o_banco(self):
        client = FakeMusicBrainz()
        resultado = resolver.resolver_com_prioridade(
            "vid001",
            {"title": "Título do YouTube", "artists": ["Artista do YouTube"], "album": "Álbum do YouTube"},
            correcoes_locais={"vid001": {
                "status_revisao": "CORRIGIDO", "titulo_corrigido": "Título confirmado",
                "artistas_corrigidos": "Artista A|Artista B", "album_corrigido": "Álbum confirmado",
            }},
            musicbrainz_client=client,
        )
        m = resultado.metadata
        self.assertEqual((m["title"], m["artists"], m["artist"], m["album"]),
                         ("Título confirmado", ["Artista A", "Artista B"], "Artista A", "Álbum confirmado"))
        self.assertEqual(resultado.provenance["artists"], "manual")
        self.assertEqual((client.isrc_calls, client.title_artist_calls), ([], []))
        self.assertNotIn("PENDENTE", resultado.status)

    def test_correcao_nao_confirmada_e_ignorada(self):
        resultado = resolver.resolver_com_prioridade(
            "vid001", {"title": "Título", "artists": ["Artista"], "album": "Álbum"},
            correcoes_locais={"vid001": {"status_revisao": "PENDENTE", "titulo_corrigido": "Não aplicar"}},
            consultar_musicbrainz=False,
        )
        self.assertEqual(resultado.metadata["title"], "Título")
        self.assertNotEqual(resultado.provenance["title"], "manual")

    def test_isrc_exato_usa_o_banco_mesmo_com_titulo_do_video_diferente(self):
        release_id = "11111111-1111-4111-8111-111111111111"
        candidato = {
            "recording_id": "22222222-2222-4222-8222-222222222222", "title": "Título MusicBrainz",
            "artists": ["Artista MB"], "isrcs": ["USAAA9900001"], "duration": 180.0,
            "releases": [{"id": release_id, "title": "Álbum MB", "date": "1999", "status": "Official",
                          "primary_type": "Album", "release_group_id": "", "artists": ["Artista MB"]}],
        }
        client = FakeMusicBrainz(by_isrc=[candidato], cover_url="https://coverartarchive.org/x-1200.jpg")
        resultado = resolver.resolver_com_prioridade(
            "vid001",
            {"title": "Título do vídeo (Official Video)", "artists": ["Canal não é artista"],
             "isrc": "us-aaa-99-00001", "uploader": "Canal não é artista", "duration": 180},
            musicbrainz_client=client,
        )
        m = resultado.metadata
        self.assertEqual(resultado.status, "RESOLVIDO", resultado.observations)
        self.assertEqual((m["title"], m["artists"], m["album"]), ("Título MusicBrainz", ["Artista MB"], "Álbum MB"))
        self.assertEqual(m["isrc"], "USAAA9900001")
        self.assertEqual(m["cover_url"], "https://coverartarchive.org/x-1200.jpg")
        self.assertEqual(m["uploader"], "Canal não é artista")
        self.assertEqual(client.isrc_calls, [("USAAA9900001", 10)])
        self.assertEqual(client.cover_calls, [("", release_id)])

    def test_entradas_repetidas_da_mesma_musica_sao_juntadas(self):
        iguais = [
            {"recording_id": f"{n}6666666-6666-4666-8666-666666666666", "title": "A Moment Apart",
             "artists": ["ODESZA"], "isrcs": [], "duration": 180 + n,
             "releases": [{"id": f"{n}1111111-1111-4111-8111-111111111111", "title": t, "status": "Official",
                           "primary_type": p, "date": d, "artists": ["ODESZA"]}]}
            for n, t, p, d in ((6, "A Moment Apart", "Album", "2017-09-08"), (7, "A Moment Apart (single)", "Single", "2017-11-01"))
        ]
        resultado = resolver.resolver_com_prioridade(
            "vid001", {"title": "A Moment Apart", "artists": ["ODESZA"], "duration": 180},
            musicbrainz_client=FakeMusicBrainz(by_title_artist=iguais, cover_url="https://c/x.jpg"),
        )
        self.assertNotEqual(resultado.metadata["musicbrainz_recording_id"], "")
        self.assertEqual(resultado.metadata["album"], "A Moment Apart")  # álbum oficial, entre os dois
        self.assertEqual(resultado.status, "RESOLVIDO", resultado.observations)

    def test_empate_entre_musicas_diferentes_nao_e_escolhido_as_cegas(self):
        diferentes = [
            {"recording_id": f"{n}6666666-6666-4666-8666-666666666666", "title": "Canção Nova",
             "artists": [artista], "isrcs": [], "duration": 180, "releases": []}
            for n, artista in ((6, "Banda A"), (7, "Banda B"))
        ]
        resultado = resolver.resolver_com_prioridade(
            "vid001", {"title": "Canção Nova", "duration": 180},
            musicbrainz_client=FakeMusicBrainz(by_title=diferentes),
        )
        self.assertEqual(resultado.metadata["musicbrainz_recording_id"], "")
        self.assertNotEqual(resultado.status, "RESOLVIDO")

    def test_musicbrainz_ocupado_tenta_de_novo(self):
        import urllib.error
        respostas = [urllib.error.HTTPError("u", 503, "busy", {}, None), b'{"recordings": []}']
        esperas = []

        def transporte(url, headers, timeout):
            r = respostas.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        client = resolver.MusicBrainzClient(transport=transporte, sleeper=esperas.append, min_interval=0)
        self.assertEqual(client.search_by_isrc("USAAA9900001"), [])
        self.assertEqual(esperas[0], resolver.ESPERAS_MUSICBRAINZ[0])

    def test_canal_que_fez_o_remix_nao_vira_o_artista(self):
        resultado = resolver.resolver_com_prioridade(
            "v", {"title": "Domalomn - Urgently Jammin' (Sir Meow Remix)", "uploader": "Sir Meow"},
            consultar_musicbrainz=False)
        self.assertEqual((resultado.metadata["artists"], resultado.metadata["title"]),
                         (["Domalomn"], "Urgently Jammin' (Sir Meow Remix)"))

    def test_capa_frontal_e_nao_a_de_tras(self):
        payload = {"images": [
            {"front": False, "types": ["Back"], "image": "https://img/back.jpg"},
            {"front": True, "types": ["Front"], "image": "https://img/front.jpg",
             "thumbnails": {"250": "https://img/front-250.jpg", "1200": "https://img/front-1200.jpg"}},
        ]}
        client = resolver.MusicBrainzClient(transport=lambda url, headers, timeout: json.dumps(payload).encode())
        self.assertEqual(client.get_album_cover(release_id="99999999-9999-4999-8999-999999999999"),
                         "https://img/front-1200.jpg")

    def test_normaliza_isrc_estrito(self):
        self.assertEqual(resolver.normalize_isrc("us-aaa-99-00001"), "USAAA9900001")
        self.assertEqual(resolver.normalize_isrc("not-an-isrc"), "")

    def test_musicbrainz_client_usa_user_agent_e_respeita_intervalo(self):
        current_time = [10.0]
        waits = []
        requests = []

        def clock():
            return current_time[0]

        def sleep(seconds):
            waits.append(seconds)
            current_time[0] += seconds

        def transport(url, headers, timeout):
            requests.append((url, headers, timeout))
            return b'{"recordings": []}'

        client = resolver.MusicBrainzClient(
            min_interval=1.0,
            transport=transport,
            clock=clock,
            sleeper=sleep,
        )
        client.search_by_isrc("USAAA9900001")
        client.search_by_isrc("USAAA9900002")

        self.assertEqual(waits, [1.0])
        self.assertIn("pltube-metadata-resolver/", requests[0][1]["User-Agent"])
        self.assertEqual(parse_qs(urlsplit(requests[0][0]).query)["fmt"], ["json"])


if __name__ == "__main__":
    unittest.main()
