import unittest

import resolvedor_metadados as r


class FakeMB:
    def __init__(self, gravacoes):
        self.gravacoes = gravacoes
        self.buscas = []

    def search_by_isrc(self, isrc, limit=10):
        return []

    def search_by_title_artist(self, title, artist, limit=10):
        self.buscas.append((title, artist))
        return [g for g in self.gravacoes if r._similarity(g["title"], title) > 0.6]

    def search_by_title(self, title, limit=10):
        return self.search_by_title_artist(title, "", limit)

    def get_front_cover(self, release_id):
        return None


def gravacao(title, artists, album="Álbum"):
    return {"recording_id": "11111111-1111-1111-1111-111111111111", "title": title,
            "artists": artists, "duration": 200, "isrcs": [],
            "releases": [{"id": "22222222-2222-2222-2222-222222222222", "title": album}]}


class PrioridadeTests(unittest.TestCase):
    def resolver(self, yt, gravacoes, **kw):
        return r.resolver_com_prioridade("vid", {"duration": 200, **yt},
                                         musicbrainz_client=FakeMB(gravacoes), **kw)

    def test_iguais_usa_grafia_do_musicbrainz_e_resolve(self):
        res = self.resolver({"title": "get lucky (Official Video)", "artists": ["daft punk"]},
                            [gravacao("Get Lucky", ["Daft Punk"])])
        self.assertEqual(res.status, "RESOLVIDO")
        self.assertEqual((res.metadata["title"], res.metadata["artists"]), ("Get Lucky", ["Daft Punk"]))
        self.assertEqual(res.metadata["album"], "Álbum")

    def test_deduz_artista_do_titulo_em_qualquer_ordem(self):
        res = self.resolver({"title": "Won't Go - snuffles", "uploader": "snuffles"},
                            [gravacao("Won't Go", ["snuffles"])])
        self.assertEqual(res.status, "RESOLVIDO")
        self.assertEqual(res.metadata["artists"], ["snuffles"])
        self.assertEqual(res.metadata["title"], "Won't Go")

    def test_mesmo_artista_principal_aceita_creditos_completos(self):
        res = self.resolver({"title": "Get Lucky", "artists": ["Daft Punk"]},
                            [gravacao("Get Lucky", ["Daft Punk", "Pharrell Williams"])])
        self.assertEqual(res.status, "RESOLVIDO")
        self.assertEqual(res.metadata["artists"], ["Daft Punk", "Pharrell Williams"])

    def test_divergencia_prefere_youtube_por_padrao_e_marca_observar(self):
        yt = {"title": "Get Lucky Remix", "artists": ["Daft Punk"]}
        res = self.resolver(yt, [gravacao("Get Lucky", ["Daft Punk"])])
        self.assertEqual(res.metadata["title"], "Get Lucky Remix")
        self.assertEqual(res.status, "OBSERVAR")
        self.assertTrue(any("título diverge" in o for o in res.observations))
        res = self.resolver(yt, [gravacao("Get Lucky", ["Daft Punk"])], preferir="musicbrainz")
        self.assertEqual(res.metadata["title"], "Get Lucky")
        self.assertEqual(res.status, "OBSERVAR")
        res = self.resolver(yt, [gravacao("Get Lucky", ["Daft Punk"])], revisar="none")
        self.assertEqual(res.status, "RESOLVIDO")

    def test_prefer_musicbrainz_usa_banco_mas_continua_na_lista(self):
        yt = {"title": "Get Lucky (feat. Pharrell)", "artists": ["Daft Punk"], "album": "Single"}
        res = self.resolver(yt, [gravacao("Get Lucky", ["Daft Punk"], album="Random Access Memories")],
                            preferir="musicbrainz")
        self.assertEqual(res.metadata["album"], "Random Access Memories")
        self.assertEqual(res.status, "OBSERVAR")
        res = self.resolver(yt, [gravacao("Get Lucky", ["Daft Punk"], album="Random Access Memories")])
        self.assertEqual(res.metadata["album"], "Single")
        self.assertEqual(res.status, "OBSERVAR")

    def test_sem_resultado_usa_youtube_e_observa_ou_pula(self):
        yt = {"title": "Faixa rara", "artists": ["Banda"]}
        self.assertEqual(self.resolver(yt, []).status, "OBSERVAR")
        self.assertEqual(self.resolver(yt, [], revisar="diff").status, "RESOLVIDO")
        res = self.resolver(yt, [], revisar="none")
        self.assertEqual(res.status, "RESOLVIDO")
        self.assertTrue(res.observations)  # anotação continua guardada

    def test_sem_artista_algum_fica_pendente(self):
        res = r.resolver_com_prioridade("v", {"title": "Rain"}, consultar_musicbrainz=False)
        self.assertEqual(res.status, "PENDENTE")

    def test_limpeza_de_titulo(self):
        analise = r.analisar_titulo("Song (Official Music Video) [HD] ft. X & Y")
        self.assertEqual((analise["base"], analise["feat"]), ("Song", ["X", "Y"]))
        h = r.hipoteses_youtube({"title": "Track", "uploader": "Band - Topic", "artists": []})
        self.assertEqual((h[0]["artists"], h[0]["origem"]), (["Band"], "youtube_canal_topic"))


if __name__ == "__main__":
    unittest.main()
