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
        self.isrc_calls = []
        self.title_artist_calls = []
        self.title_calls = []
        self.cover_calls = []

    def search_by_isrc(self, isrc, limit=10):
        self.isrc_calls.append((isrc, limit))
        return self.by_isrc

    def search_by_title_artist(self, title, artist, limit=10):
        self.title_artist_calls.append((title, artist, limit))
        return self.by_title_artist

    def search_by_title(self, title, limit=10):
        self.title_calls.append((title, limit))
        return self.by_title

    def get_front_cover(self, release_id):
        self.cover_calls.append(release_id)
        return self.cover_url


class ResolverTests(unittest.TestCase):
    def test_uploader_nunca_vira_artista_e_dados_incompletos_ficam_pendentes(self):
        resultado = resolver.resolver_metadados(
            "vid001",
            {"title": "Canção", "uploader": "Canal Oficial", "thumbnail": "https://img/video.jpg"},
            consultar_musicbrainz=False,
        )

        self.assertEqual(resultado.status, "PENDENTE")
        self.assertEqual(resultado.metadata["artists"], [])
        self.assertEqual(resultado.metadata["uploader"], "Canal Oficial")
        self.assertEqual(resultado.metadata["thumbnail_url"], "https://img/video.jpg")
        self.assertIn("campo obrigatório ausente: artists", resultado.pending_reasons)
        self.assertIn("campo obrigatório ausente: album", resultado.pending_reasons)
        self.assertIn(
            "título informado somente pelo vídeo, sem confirmação independente",
            resultado.pending_reasons,
        )

    def test_correcoes_confirmadas_sobrescrevem_fontes_e_pulam_busca_quando_completas(self):
        client = FakeMusicBrainz()
        resultado = resolver.resolver_metadados(
            "vid001",
            {
                "title": "Título do YouTube",
                "artists": ["Artista do YouTube"],
                "album": "Álbum do YouTube",
            },
            correcoes_locais={
                "vid001": {
                    "status_revisao": "CORRIGIDO",
                    "titulo_corrigido": "Título confirmado",
                    "artistas_corrigidos": "Artista A|Artista B",
                    "album_corrigido": "Álbum confirmado",
                }
            },
            musicbrainz_client=client,
        )

        self.assertEqual(resultado.status, "RESOLVIDO")
        self.assertEqual(resultado.metadata["title"], "Título confirmado")
        self.assertEqual(resultado.metadata["artists"], ["Artista A", "Artista B"])
        self.assertEqual(resultado.metadata["artist"], "Artista A")
        self.assertEqual(resultado.metadata["album"], "Álbum confirmado")
        self.assertEqual(resultado.provenance["artists"], "manual")
        self.assertEqual(client.isrc_calls, [])
        self.assertEqual(client.title_artist_calls, [])

    def test_correcoes_nao_confirmadas_sao_ignoradas(self):
        resultado = resolver.resolver_metadados(
            "vid001",
            {"title": "Título", "artists": ["Artista"], "album": "Álbum"},
            correcoes_locais={
                "vid001": {
                    "status_revisao": "PENDENTE",
                    "titulo_corrigido": "Não aplicar ainda",
                }
            },
            consultar_musicbrainz=False,
        )

        self.assertEqual(resultado.metadata["title"], "Título")
        self.assertEqual(resultado.provenance["title"], "youtube")
        self.assertEqual(resultado.status, "PENDENTE")

    def test_album_vazio_pode_ser_confirmado_sem_inventar_valor(self):
        resultado = resolver.resolver_metadados(
            "vid001",
            {"title": "Canção", "artists": ["Artista"], "album": "Álbum duvidoso"},
            correcoes_locais={
                "vid001": {
                    "status_revisao": "CORRIGIDO",
                    "campos_confirmados_vazios": ["album"],
                }
            },
            consultar_musicbrainz=False,
            campos_obrigatorios=("album",),
        )

        self.assertEqual(resultado.status, "RESOLVIDO")
        self.assertEqual(resultado.metadata["album"], "")
        self.assertEqual(resultado.provenance["album"], "manual")
        self.assertEqual(resultado.confidence["album"], "confirmada_vazia")

    def test_capa_local_manual_satisfaz_campo_cover(self):
        resultado = resolver.resolver_metadados(
            "vid001",
            {"title": "Canção"},
            correcoes_locais={
                "vid001": {
                    "status_revisao": "CORRIGIDO",
                    "capa_arquivo": "/covers/capa escolhida.jpg",
                }
            },
            musicbrainz_client=FakeMusicBrainz(),
            campos_obrigatorios=("cover",),
        )

        self.assertEqual(resultado.status, "RESOLVIDO")
        self.assertEqual(resultado.metadata["cover_file"], "/covers/capa escolhida.jpg")
        self.assertEqual(resultado.provenance["cover_file"], "manual")

    def test_isrc_exato_usa_creditos_e_capa_front_de_release_unico(self):
        mbid = "11111111-1111-4111-8111-111111111111"
        candidate = {
            "recording_id": "22222222-2222-4222-8222-222222222222",
            "title": "Título MusicBrainz",
            "artists": ["Artista MB"],
            "isrcs": ["USAAA9900001"],
            "duration": 180.0,
            "releases": [{"id": mbid, "title": "Álbum MB", "date": "1999"}],
        }
        client = FakeMusicBrainz(
            by_isrc=[candidate],
            cover_url="https://coverartarchive.org/release/example/front-500",
        )
        resultado = resolver.resolver_metadados(
            "vid001",
            {
                "title": "Título do vídeo",
                "artists": ["Canal não é artista"],
                "album": "Álbum incerto",
                "isrc": "us-aaa-99-00001",
                "uploader": "Canal não é artista",
            },
            musicbrainz_client=client,
        )

        self.assertEqual(resultado.status, "RESOLVIDO")
        self.assertEqual(resultado.metadata["title"], "Título MusicBrainz")
        self.assertEqual(resultado.metadata["artists"], ["Artista MB"])
        self.assertEqual(resultado.metadata["album"], "Álbum MB")
        self.assertEqual(resultado.metadata["cover_url"], "https://coverartarchive.org/release/example/front-500")
        self.assertEqual(resultado.metadata["uploader"], "Canal não é artista")
        self.assertEqual(resultado.provenance["cover_url"], "cover_art_archive")
        self.assertEqual(resultado.source_youtube["artists"], ["Canal não é artista"])
        self.assertEqual(resultado.source_youtube["album"], "Álbum incerto")
        self.assertEqual(client.cover_calls, [mbid])

    def test_match_forte_sem_release_nao_confirma_album_do_youtube(self):
        candidate = {
            "recording_id": "12121212-1212-4212-8212-121212121212",
            "title": "Canção Nova",
            "artists": ["Artista"],
            "isrcs": [],
            "duration": 180,
            "releases": [],
        }
        client = FakeMusicBrainz(by_title_artist=[candidate])
        resultado = resolver.resolver_metadados(
            "vid001",
            {
                "title": "Canção Nova",
                "artists": ["Artista"],
                "album": "Álbum apenas do vídeo",
            },
            musicbrainz_client=client,
        )

        self.assertEqual(resultado.status, "PENDENTE")
        self.assertIn(
            "nenhum lançamento foi localizado para confirmar o álbum",
            resultado.pending_reasons,
        )
        self.assertEqual(resultado.metadata["album"], "Álbum apenas do vídeo")
        self.assertEqual(resultado.provenance["title"], "musicbrainz")
        self.assertEqual(resultado.provenance["artists"], "musicbrainz")
        self.assertEqual(resultado.confidence["title"], "correspondencia_forte")

    def test_varias_edicoes_para_o_mesmo_isrc_ficam_pendentes(self):
        recording = {
            "recording_id": "33333333-3333-4333-8333-333333333333",
            "title": "Canção",
            "artists": ["Artista"],
            "isrcs": ["USAAA9900001"],
            "duration": 120.0,
            "releases": [
                {"id": "44444444-4444-4444-8444-444444444444", "title": "Edição A"},
                {"id": "55555555-5555-4555-8555-555555555555", "title": "Edição B"},
            ],
        }
        client = FakeMusicBrainz(by_isrc=[recording])
        resultado = resolver.resolver_metadados(
            "vid001",
            {"title": "Canção", "artists": ["Artista"], "isrc": "USAAA9900001"},
            musicbrainz_client=client,
        )

        self.assertEqual(resultado.status, "PENDENTE")
        self.assertIn("álbum associado a mais de uma edição possível", resultado.pending_reasons)
        self.assertEqual(client.cover_calls, [])
        self.assertEqual(len(resultado.candidates[0]["releases"]), 2)

    def test_busca_ambigua_por_titulo_e_artista_nao_escolhe_silenciosamente(self):
        candidates = [
            {
                "recording_id": "66666666-6666-4666-8666-666666666666",
                "title": "Canção Nova",
                "artists": ["Artista"],
                "isrcs": [],
                "duration": 180,
                "releases": [],
            },
            {
                "recording_id": "77777777-7777-4777-8777-777777777777",
                "title": "Canção Nova",
                "artists": ["Artista"],
                "isrcs": [],
                "duration": 181,
                "releases": [],
            },
        ]
        client = FakeMusicBrainz(by_title_artist=candidates)
        resultado = resolver.resolver_metadados(
            "vid001",
            {"title": "Canção Nova", "artists": ["Artista"]},
            musicbrainz_client=client,
        )

        self.assertEqual(resultado.status, "PENDENTE")
        self.assertIn(
            "várias correspondências plausíveis no MusicBrainz",
            resultado.pending_reasons,
        )
        self.assertEqual(resultado.metadata["musicbrainz_recording_id"], "")
        self.assertEqual(len(resultado.candidates), 2)

    def test_correspondencia_aceita_pequenas_diferencas_de_titulo_e_artista(self):
        candidate = {
            "recording_id": "abababab-abab-4bab-8bab-abababababab",
            "title": "Don't Stop Believin'",
            "artists": ["Journey"],
            "isrcs": [],
            "duration": 240,
            "releases": [
                {"id": "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd", "title": "Escape"}
            ],
        }
        client = FakeMusicBrainz(by_title_artist=[candidate])
        resultado = resolver.resolver_metadados(
            "vid-fuzzy",
            {
                "title": "Dont stop believing (Official Audio)",
                "artists": ["Journiy"],
            },
            musicbrainz_client=client,
        )

        self.assertEqual(resultado.status, "RESOLVIDO")
        self.assertEqual(resultado.metadata["title"], "Don't Stop Believin'")
        self.assertEqual(resultado.metadata["artists"], ["Journey"])
        self.assertEqual(resultado.provenance["title"], "musicbrainz")
        self.assertGreaterEqual(resultado.candidates[0]["score"], 0.82)

    def test_busca_por_titulo_quando_youtube_nao_fornece_artista(self):
        candidate = {
            "recording_id": "dededede-dede-4ded-8ded-dededededede",
            "title": "Hello",
            "artists": ["Adele"],
            "isrcs": [],
            "duration": 180,
            "releases": [
                {"id": "efefefef-efef-4fef-8fef-efefefefefef", "title": "25"}
            ],
        }
        client = FakeMusicBrainz(by_title=[candidate])
        resultado = resolver.resolver_metadados(
            "vid-title-only",
            {"title": "Helo (Official Audio)", "duration": 181},
            musicbrainz_client=client,
        )

        self.assertEqual(client.title_calls, [("Helo (Official Audio)", 10)])
        self.assertEqual(resultado.metadata["artists"], ["Adele"])
        self.assertEqual(resultado.metadata["title"], "Hello")
        self.assertEqual(resultado.status, "RESOLVIDO")

    def test_sem_correspondencia_usa_youtube_como_valor_provisorio_e_pendente(self):
        client = FakeMusicBrainz()
        original = {
            "title": "Título do vídeo",
            "artists": ["Artista do vídeo"],
            "album": "Álbum do vídeo",
        }
        resultado = resolver.resolver_metadados(
            "vid-pendente", original, musicbrainz_client=client
        )

        self.assertEqual(resultado.status, "PENDENTE")
        self.assertEqual(resultado.metadata["title"], "Título do vídeo")
        self.assertEqual(resultado.metadata["artists"], ["Artista do vídeo"])
        self.assertEqual(resultado.metadata["album"], "Álbum do vídeo")
        self.assertEqual(resultado.provenance["title"], "youtube")
        self.assertIn(
            "título informado somente pelo vídeo, sem confirmação independente",
            resultado.pending_reasons,
        )

    def test_cache_reutiliza_busca_por_isrc_entre_resolucoes(self):
        candidate = {
            "recording_id": "88888888-8888-4888-8888-888888888888",
            "title": "Canção",
            "artists": ["Artista"],
            "isrcs": ["USAAA9900001"],
            "duration": 120,
            "releases": [],
        }
        client = FakeMusicBrainz(by_isrc=[candidate])
        cache = {}
        data = {
            "title": "Canção",
            "artists": ["Artista"],
            "isrc": "USAAA9900001",
        }
        resolver.resolver_metadados(
            "vid001", data, musicbrainz_client=client, cache=cache
        )
        resolver.resolver_metadados(
            "vid001", data, musicbrainz_client=client, cache=cache
        )

        self.assertEqual(len(client.isrc_calls), 1)
        self.assertIn("isrc:USAAA9900001", cache)

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

    def test_cliente_capa_escolhe_thumbnail_front_nao_back(self):
        release_id = "99999999-9999-4999-8999-999999999999"
        payload = {
            "images": [
                {"front": False, "types": ["Back"], "image": "https://img/back.jpg"},
                {
                    "front": True,
                    "types": ["Front"],
                    "image": "https://img/front.jpg",
                    "thumbnails": {"250": "https://img/front-250.jpg"},
                },
            ]
        }

        def transport(url, headers, timeout):
            return json.dumps(payload).encode("utf-8")

        client = resolver.MusicBrainzClient(transport=transport)
        self.assertEqual(
            client.get_front_cover(release_id), "https://img/front-250.jpg"
        )

    def test_normaliza_isrc_estrito(self):
        self.assertEqual(resolver.normalize_isrc("us-aaa-99-00001"), "USAAA9900001")
        self.assertEqual(resolver.normalize_isrc("not-an-isrc"), "")


if __name__ == "__main__":
    unittest.main()
