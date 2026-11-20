"""Separação de artistas: vírgula, " & ", " / ", "|", ";" e "feat." separam; nomes conhecidos não."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aplicar_metadados
import pltube
import textos as r


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
            "Chloe x Halle": ["Chloe x Halle"],                         # conhecido: fica junto
            "Fulano & Ciclano": ["Fulano", "Ciclano"],                  # " & " com espaços separa
            "Simon&Garfunkel": ["Simon & Garfunkel"],                   # sem espaço: nome (conhecido)
            "Henrique e Juliano": ["Henrique e Juliano"],               # " e " não separa
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

    def test_conhecido_fica_inteiro(self):
        self.assertEqual(r.dividir_artistas("Anitta & Kevinho"), ["Anitta", "Kevinho"])
        self.assertEqual(r.dividir_artistas("Zé & Lu, Outro", ["Zé & Lu"]), ["Zé & Lu", "Outro"])
        self.assertEqual(r.dividir_artistas("Ariana Grande & The Weeknd"), ["Ariana Grande", "The Weeknd"])
        import identificar_musica as im
        self.assertEqual(im._feat_do_titulo("Song (part. Jorge & Mateus)"), ("Song", ["Jorge & Mateus"]))
        self.assertEqual(im._feat_do_titulo("Song (ft. Dax & VinDon)"), ("Song", ["Dax", "VinDon"]))

    def test_dados_do_youtube_lista_e_texto(self):
        self.assertEqual(pltube.normalizar_artistas({"artist": "Anitta, Kevinho"}), ["Anitta", "Kevinho"])
        self.assertEqual(pltube.normalizar_artistas({"artist": "Jorge & Mateus"}), ["Jorge & Mateus"])
        self.assertEqual(pltube.normalizar_artistas({"artists": ["Banda, Com Vírgula"]}), ["Banda, Com Vírgula"])
        self.assertEqual(aplicar_metadados._artistas("Unknown"), ["Unknown"])
        self.assertEqual(aplicar_metadados._artistas("Anitta, Kevinho"), ["Anitta", "Kevinho"])


class PartesDoTituloTests(unittest.TestCase):
    def test_partes(self):
        self.assertEqual(r.partes_do_titulo('Cult of the Lamb Song - "Fool\'s Gold" by ivycomb'),
                         ["Cult of the Lamb Song", "Fool's Gold", "ivycomb"])
        self.assertEqual(r.partes_do_titulo("A // B | C · D"), ["A", "B", "C", "D"])

    def test_artista_nas_partes(self):
        self.assertEqual(r.artista_nas_partes("ACDC Thunderstruck", ["AC/DC"]), ("AC/DC", "ACDC", "Thunderstruck"))
        self.assertEqual(r.artista_nas_partes("Liberdade Provisória - Henrique e Juliano - com cifras",
                                              ["Henrique & Juliano"]),
                         ("Henrique & Juliano", "Henrique e Juliano", "Liberdade Provisória"))
        self.assertIsNone(r.artista_nas_partes("Coisa Um - Coisa Dois", ["Fulano"]))


if __name__ == "__main__":
    unittest.main()
