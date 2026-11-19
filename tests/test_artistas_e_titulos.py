"""Separação de artistas ("&", "|", vírgula...) e títulos invertidos ("Música - Artista")."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # helpers dos outros testes

import aplicar_metadados
import pltube
import resolvedor_metadados as r
from test_modelo_identificacao import FakeMB, UUID, gravacao, release


class FakeMBCompleto(FakeMB):
    """Também responde a busca de artistas e de música dentro de um lançamento."""

    def __init__(self, gravacoes=(), artistas=(), capas=None):
        super().__init__(list(gravacoes), capas)
        self.artistas = list(artistas)

    def search_artist(self, name, limit=5):
        self.buscas.append(("artista", name))
        return [{"name": a, "score": 100, "aliases": []} for a in self.artistas
                if r._normalize_text(a) == r._normalize_text(name)]

    def search_by_title_release(self, title, release_title, limit=10):
        self.buscas.append((title, "lançamento: " + release_title))
        alvo = r._normalize_text(release_title)
        return [g for g in self.gravacoes if r._similarity(g["title"], title) > 0.8
                and any(alvo in r._normalize_text(x["title"]) for x in g["releases"])]


def resolver(titulo, gravacoes=(), artistas=(), capas=None, **kw):
    dados = {"title": titulo, "uploader": "Canal Qualquer de Músicas", "duration": 200,
             "thumbnail": "https://i.ytimg.com/vi/x/maxresdefault.jpg"}
    cliente = FakeMBCompleto(gravacoes, artistas, capas)
    return r.resolver_com_prioridade("vid", dados, musicbrainz_client=cliente, **kw), cliente


class DividirArtistasTests(unittest.TestCase):
    def test_separadores_e_nomes_que_os_contem(self):
        casos = {
            "Anitta, Kevinho": ["Anitta", "Kevinho"],
            "Anitta | Kevinho": ["Anitta", "Kevinho"],
            "Anitta; Kevinho": ["Anitta", "Kevinho"],
            "A, B & C": ["A", "B", "C"],
            "Alok & JØRD & DJ MP4": ["Alok", "JØRD", "DJ MP4"],
            "Daft Punk / Pharrell Williams": ["Daft Punk", "Pharrell Williams"],
            "R3HAB x A Touch Of Class": ["R3HAB", "A Touch Of Class"],
            "Jorge & Mateus feat. Anitta": ["Jorge & Mateus", "Anitta"],
            "Zé Neto & Cristiano & Marília Mendonça": ["Zé Neto & Cristiano", "Marília Mendonça"],
            "Earth, Wind & Fire": ["Earth, Wind & Fire"],
            "Earth, Wind & Fire | Anitta": ["Earth, Wind & Fire", "Anitta"],
            "Tyler, The Creator, Kali Uchis": ["Tyler, The Creator", "Kali Uchis"],
            "Bob Marley & The Wailers": ["Bob Marley & The Wailers"],
            "Florence + The Machine": ["Florence + The Machine"],
            "Lil Nas X & Billy Ray Cyrus": ["Lil Nas X", "Billy Ray Cyrus"],
            "AC/DC": ["AC/DC"],
            "jorge & mateus": ["Jorge & Mateus"],                 # grafia oficial
            '"Fulano & Beltrano", Outro': ["Fulano & Beltrano", "Outro"],  # aspas juntam
        }
        for texto, esperado in casos.items():
            self.assertEqual(r.dividir_artistas(texto), esperado, texto)

    def test_e_sozinho_depende_do_que_ja_se_conhece(self):
        self.assertEqual(r.dividir_artistas("Anitta & Kevinho"), ["Anitta & Kevinho"])  # pode ser dupla
        self.assertEqual(r.dividir_artistas("Anitta & Kevinho", ["Anitta"]), ["Anitta", "Kevinho"])
        self.assertEqual(r.dividir_artistas("Ariana Grande & The Weeknd", ["The Weeknd"]),
                         ["Ariana Grande", "The Weeknd"])
        self.assertEqual(r.dividir_artistas("Caetano Veloso e Gilberto Gil", ["Caetano Veloso"]),
                         ["Caetano Veloso", "Gilberto Gil"])
        self.assertEqual(r.analisar_titulo("Song (part. Jorge & Mateus)")["feat"], ["Jorge & Mateus"])
        self.assertEqual(r.analisar_titulo("Song (ft. Dax & VinDon)")["feat"], ["Dax", "VinDon"])

    def test_dados_do_youtube_lista_e_texto(self):
        self.assertEqual(pltube.normalizar_artistas({"artist": "Anitta, Kevinho"}), ["Anitta", "Kevinho"])
        self.assertEqual(pltube.normalizar_artistas({"artist": "Jorge & Mateus"}), ["Jorge & Mateus"])
        self.assertEqual(pltube.normalizar_artistas({"artists": ["Banda, Com Vírgula"]}), ["Banda, Com Vírgula"])
        self.assertEqual(aplicar_metadados._artistas("Unknown"), ["Unknown"])
        self.assertEqual(aplicar_metadados._artistas("Anitta, Kevinho"), ["Anitta", "Kevinho"])


class TituloInvertidoTests(unittest.TestCase):
    def test_musica_e_serie_acha_o_artista_na_trilha(self):
        trilha = release(7, "La Casa de Papel (Música Original de la Serie)", tipo="Album",
                         secundarios=["Soundtrack"], artistas=["Manel Santisteban"])
        res, cliente = resolver("Bella Ciao - La Casa de Papel",
                                [gravacao("Bella Ciao", ["Manu Pilas"], [trilha])],
                                capas={UUID.format(507): "https://c/trilha.jpg"})
        m = res.metadata
        self.assertEqual((m["title"], m["artists"]), ("Bella Ciao", ["Manu Pilas"]), res.observations)
        self.assertEqual(m["album"], "La Casa de Papel (Música Original de la Serie)")
        self.assertIn(("Bella Ciao", "lançamento: La Casa de Papel"), cliente.buscas)

    def test_lado_que_existe_como_artista_decide(self):
        res, _ = resolver("Música Rara - Banda Real", artistas=["Banda Real"])
        self.assertEqual((res.metadata["title"], res.metadata["artists"]), ("Música Rara", ["Banda Real"]))
        self.assertEqual(res.status, "OBSERVAR")  # sem a gravação, ainda vale conferir

    def test_nome_de_musica_conhecido_decide(self):
        versoes = [gravacao("Bella Ciao", [nome], n=i) for i, nome in enumerate(("Goran Bregović", "Milva", "Manu Pilas"), 10)]
        res, _ = resolver("Bella Ciao - Fulano Desconhecido", versoes)
        self.assertEqual((res.metadata["title"], res.metadata["artists"]), ("Bella Ciao", ["Fulano Desconhecido"]))

    def test_sem_pista_fica_o_formato_comum(self):
        res, _ = resolver("Coisa Um - Coisa Dois")
        self.assertEqual((res.metadata["artists"], res.metadata["title"]), (["Coisa Um"], "Coisa Dois"))
        self.assertFalse(any("invertidos" in o for o in res.observations), res.observations)

    def test_pistas_empatadas_avisam_que_pode_estar_invertido(self):
        res, _ = resolver("Banda Um - Banda Dois", artistas=["Banda Um", "Banda Dois"])
        self.assertEqual(res.status, "OBSERVAR")
        self.assertTrue(any("podem estar invertidos" in o for o in res.observations), res.observations)
        from comandos.review import campos_com_problema
        self.assertEqual(campos_com_problema(res.to_dict())[:2], ["title", "artists"])

    def test_obra_no_titulo_nao_vira_artista(self):
        res, _ = resolver("Tema de Abertura da Novela - Canção do Mar")
        self.assertNotIn("Tema de Abertura da Novela", " ".join(res.metadata["artists"]))

    def test_dupla_ou_dois_artistas_pelo_musicbrainz(self):
        res, _ = resolver("Fulano & Beltrano - Canção Nova", artistas=["Fulano", "Beltrano"])
        self.assertEqual(res.metadata["artists"], ["Fulano", "Beltrano"])
        res, _ = resolver("Dupla Exemplo & Parceiro - Canção Nova", artistas=["Dupla Exemplo & Parceiro"])
        self.assertEqual(res.metadata["artists"], ["Dupla Exemplo & Parceiro"])
        res, _ = resolver("Banda, Nome & Outro - Canção", artistas=["Banda, Nome & Outro"])
        self.assertEqual(res.metadata["artists"], ["Banda, Nome & Outro"])
        self.assertFalse(any("invertidos" in o for o in res.observations), res.observations)

    def test_gravacao_achada_normalmente_nao_faz_consultas_extras(self):
        _, cliente = resolver("Banda Real - Música Rara", [gravacao("Música Rara", ["Banda Real"])])
        self.assertFalse([b for b in cliente.buscas if b[0] == "artista" or "lançamento" in str(b[1])])


if __name__ == "__main__":
    unittest.main()


class TitulosReaisTests(unittest.TestCase):
    """Títulos de verdade que geravam pasta com nome de música ou artista errado."""

    CASOS = (  # (título do vídeo, canal, artistas, título)
        ('Cult of the Lamb Song - "Fool\'s Gold" by ivycomb', "Ivycomb Music", ["ivycomb"], "Fool's Gold"),
        ('Death Song (Puss in Boots) - "Ninth Life" by ivycomb', "Ivycomb Music", ["ivycomb"], "Ninth Life"),
        ("INSTINCT - 02. Instinct", "NIIC", ["NIIC"], "Instinct"),
        ("Domalomn - Urgently Jammin' (Sir Meow Remix)", "Sir Meow", ["Domalomn"], "Urgently Jammin' (Sir Meow Remix)"),
        ("Just a Little Better - snuffles", "snuffles", ["snuffles"], "Just a Little Better"),
        ("THIS THOUGHT - Frizk & snuffles", "snuffles", ["Frizk", "snuffles"], "THIS THOUGHT"),
        ("TheFatRat & NEFFEX - Back One Day (Outro Song)", "TheFatRat", ["TheFatRat", "NEFFEX"], "Back One Day (Outro Song)"),
        ("Seven Lions & ILLENIUM ft. ÁSDÍS - Not Even Love (Official Visualizer)", "Seven Lions",
         ["Seven Lions", "ILLENIUM", "ÁSDÍS"], "Not Even Love"),
        ("[Dubstep] - Tut Tut Child - Hummingbird (feat. Augustus Ghost) [Monstercat Release]", "Monstercat Uncaged",
         ["Tut Tut Child", "Augustus Ghost"], "Hummingbird"),
        ("My Ordinary Life-The Living Tombstone", "The Living Tombstone", ["The Living Tombstone"], "My Ordinary Life"),
        ("twenty one pilots: Goner", "twenty one pilots", ["twenty one pilots"], "Goner"),
        ("Glyde // Latte", "Glyde", ["Glyde"], "Latte"),
        ("INFAMOUS (with YonKaGor and Stephanafro)", "Ivycomb Music", ["ivycomb", "YonKaGor", "Stephanafro"], "INFAMOUS"),
        ("Final Space Official Soundtrack | When the Night is Long (feat. Shelby Merry) | WaterTower",
         "WaterTower Music", ["WaterTower", "Shelby Merry"], "When the Night is Long"),
    )

    def test_titulos(self):
        for video, canal, artistas, titulo in self.CASOS:
            dados = {"title": video, "uploader": canal, "duration": 200}
            res = r.resolver_com_prioridade("x", dados, consultar_musicbrainz=False)
            self.assertEqual(([a.casefold() for a in res.metadata["artists"]], res.metadata["title"]),
                             ([a.casefold() for a in artistas], titulo), video)
