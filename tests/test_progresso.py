import io
import sys
import unittest
from unittest.mock import patch

import plpython
import progresso


class Terminal(io.StringIO):
    def __init__(self, encoding="utf-8"):
        super().__init__()
        self._enc = encoding

    @property
    def encoding(self):
        return self._enc

    def isatty(self):
        return True


def tela(bruto: str) -> list[str]:
    """Emulador mínimo: aplica \\n e \\r e devolve as linhas visíveis."""
    linhas, atual = [], ""
    for parte in bruto.split("\n"):
        visivel = ""
        for pedaco in parte.split("\r"):
            visivel = pedaco + visivel[len(pedaco):]
        linhas.append(visivel.rstrip())
    return [l for l in linhas if l]


class ProgressoTests(unittest.TestCase):
    def tearDown(self):
        progresso.encerrar()

    def test_texto_da_barra(self):
        barra = progresso.Progresso(4, terminal=Terminal())
        barra.feito, barra.csv, barra.titulo, barra.etapa = 1, "Alone.csv", "Won't Go", "baixando 63%"
        texto = barra.texto(120)
        self.assertTrue(texto.startswith("[█████░░░░░░░░░░░░░░░]  25% 1/4"))
        self.assertIn("Alone.csv: Won't Go · baixando 63%", texto)
        self.assertLessEqual(len(barra.texto(40)), 39)  # nunca quebra a linha

    def test_terminal_sem_unicode_usa_ascii(self):
        barra = progresso.Progresso(2, terminal=Terminal(encoding="ascii"))
        barra.feito = 1
        self.assertTrue(barra.texto(80).startswith("[##########----------]  50% 1/2"))

    def test_mensagens_rolam_acima_e_a_barra_fica_embaixo(self):
        terminal = Terminal()
        with patch("progresso.shutil.get_terminal_size", return_value=type("T", (), {"columns": 80})()):
            with patch.object(sys, "stdout", terminal), patch.object(sys, "stderr", terminal):
                progresso.iniciar(2, terminal=terminal)
                progresso.iniciar_csv("a.csv")
                progresso.linha(0, "primeira")
                print("mensagem 1")
                progresso.etapa("baixando 50%")
                progresso.ATUAL._desenhar(forcar=True)
                print("mensagem 2", file=sys.stderr)
                meio = tela(terminal.getvalue())
                progresso.fim_csv(2)
                progresso.encerrar()
        self.assertEqual(meio[:2], ["mensagem 1", "mensagem 2"])
        self.assertTrue(meio[-1].startswith("[░"), meio)
        self.assertIn("a.csv: primeira", meio[-1])
        final = tela(terminal.getvalue())
        self.assertEqual(final[-1], final[-1].lstrip())
        self.assertTrue(final[-1].startswith("Progresso: 2/2 itens (100%)"), final)
        self.assertFalse(any(l.startswith("[") for l in final), final)  # barra apagada no fim
        self.assertIsNot(sys.stdout, None)

    def test_sem_terminal_desliga(self):
        saida = io.StringIO()
        with patch.object(sys, "stdout", saida):
            self.assertIsNone(progresso.iniciar(3, terminal=saida))
        self.assertIn("precisa de um terminal", saida.getvalue())
        progresso.etapa("nada")  # sem barra, os atalhos não fazem nada

    def test_opcao_na_linha_de_comando(self):
        for argv, esperado in ((["download"], False), (["download", "-P"], True), (["update", "--progress"], True)):
            with patch("pltube.executar", return_value=0) as fake:
                plpython.main(argv)
            self.assertIs(fake.call_args.args[0].progresso, esperado)

    def test_porcentagem_por_csv_e_linha(self):
        barra = progresso.Progresso(10, terminal=Terminal())
        barra.iniciar_csv("a.csv")
        barra.linha(3)
        self.assertEqual(barra.feito, 3)
        barra.fim_csv(4)
        barra.iniciar_csv("b.csv")
        barra.linha(2)
        self.assertEqual((barra.base, barra.feito), (4, 6))
        barra.fim_csv(6)
        self.assertEqual(barra._porcentagem(), 100.0)


if __name__ == "__main__":
    unittest.main()
