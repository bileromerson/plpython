"""Comandos do plpython.py: um arquivo por comando.

Cada módulo define:
  NOME, APELIDOS, RESUMO, OPCOES  -> usados pelo menu do plpython.py
  criar_parser(prog)              -> todas as opções e a ajuda do comando
  executar(args)                  -> roda o comando com os argumentos já lidos
  main(argv)                      -> criar_parser + executar

Para adicionar um comando novo, crie o arquivo aqui e coloque o nome em ORDEM.
"""

ORDEM = ("playlist", "download", "update", "metadata", "review", "spotify", "revert", "legacy_centralize")
