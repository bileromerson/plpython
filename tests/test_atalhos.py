"""Formas curtas dos parâmetros mais usados (não podem quebrar -vv, -Pv, -u -1...)."""
import unittest

from comandos import check, download, playlist, review, update


class AtalhosTests(unittest.TestCase):
    def confere(self, parser, argv, **esperado):
        args = vars(parser.parse_args(argv))
        self.assertEqual({k: args[k] for k in esperado}, esperado, argv)

    def test_download_e_update(self):
        p = download.criar_parser()
        self.confere(p, ["-w", "-r"], esperar_bloqueio=True, reprocess=True)
        self.confere(p, ["-nr", "-nh"], revisar="none", modelo_hibrido=False)
        self.confere(p, ["-i", "-x", "album"], perguntar=True, ignorar=["album"])
        self.confere(p, ["-b", "firefox:Perfil 1"], cookies_navegador="firefox:Perfil 1")
        self.confere(p, ["-k", "cookies.txt"], cookies_arquivo="cookies.txt")
        self.confere(p, ["-Pvv", "-u", "-1"], progresso=True, verbose=2, row=-1)
        self.confere(update.criar_parser(), ["-w", "-nr", "-P"], esperar_bloqueio=True, revisar="none", progresso=True)

    def test_review(self):
        p = review.criar_parser()
        self.confere(p, ["-ay"], responder=True, default_ok=True)
        self.confere(p, ["-a", "-A"], responder=True, todos_campos=True)
        self.confere(p, ["-q", "snuffles", "-d", "-e", "x.csv"], busca="snuffles", detalhes=True, export="x.csv")
        self.confere(p, ["--status", "resolved"], status="resolved")

    def test_check_e_playlist(self):
        self.confere(check.criar_parser(), ["-y", "-q", "snuffles"], apply=True, busca="snuffles")
        self.confere(playlist.criar_parser(), ["liked", "-n", "X", "-b", "firefox"], nome="X", cookies_navegador="firefox")


if __name__ == "__main__":
    unittest.main()
