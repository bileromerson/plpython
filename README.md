# pltube

Ferramentas em Python para organizar playlists a partir de URLs do YouTube, baixar áudio, criar listas M3U e manter uma biblioteca de músicas. O projeto também inclui uma consulta somente de leitura a playlists do Spotify e um utilitário para migrar bibliotecas já baixadas.

O README separa os recursos disponíveis dos itens que ainda faltam implementar.

## Comando unificado: `plpython.py`

Todas as ferramentas agora têm uma entrada única com subcomandos. Os scripts antigos continuam funcionando como antes.

O `plpython.py` é só o menu: sozinho, ele mostra os comandos e as opções principais de cada um, e encaminha `plpython.py COMANDO ...` para o arquivo do comando em `comandos/`. Tudo de um comando (opções, validações, ajuda e exemplos) fica no arquivo dele:

```
plpython.py              ← menu: lista os comandos e encaminha
comandos/
├── download.py          ← tudo do download
├── update.py            ← usa as opções do download e sempre tenta de novo
├── metadata.py
├── review.py
├── spotify.py
├── revert.py
├── legacy_centralize.py
└── _comum.py            ← opções compartilhadas (pastas, bancos)
```

A lógica pesada continua em `pltube.py`, `aplicar_metadados.py` etc., que os arquivos de `comandos/` chamam. Para criar um comando novo, basta um arquivo em `comandos/` e acrescentar o nome em `comandos/__init__.py`.

| Comando | Faz | Antes era |
|---|---|---|
| `download` (`baixar`) | baixa os CSVs e gera as M3Us | `pltube.py` |
| `update` (`atualizar`) | relê os CSVs, refaz falhas e pendências sem rebaixar o que já existe | `pltube.py --reprocess` |
| `metadata` (`metadados`) | aplica a planilha `metadados_pendentes.csv` (simula sem `--apply`) | `aplicar_metadados.py` |
| `review` (`revisar`) | mostra/confirma as faixas do banco SQL no terminal | novo |
| `spotify` | lista as faixas de uma playlist Spotify | `spotify_playlist_viewer.py` |
| `revert` (`reverter`) | desfaz o layout legado `songs/_library` | `reverter_biblioteca.py` |
| `legacy-centralize` | cria o layout legado (não recomendado) | `sincronizar_biblioteca.py` |

Pastas: rodando `plpython.py` em `~/Musica`, o padrão é `~/Musica/songs`, `~/Musica/playlists` e `~/Musica/sql`, mesmo usando `--csvfile ./outra/pasta`. CSVs passados direto no comando (`download a.csv`) são relativos à pasta atual.

Opções renomeadas (os nomes antigos ainda são aceitos, mas não aparecem na ajuda):

| Novo | Antigo | Motivo |
|---|---|---|
| `-c/--csvfile` | `--BaseDir`, `--csvfile` | indica a pasta dos CSVs ou um `.csv` específico, e só isso; `songs/`, `playlists/`, `sql/`, `Logs.txt` e a planilha ficam na pasta de onde você roda o comando |
| `-s/--songs-dir`, `-p/--playlist-dir`, `-l/--log-file` | `--SongsDir`, `--PlaylistDir`, `--LogFile` | padronizar em minúsculas com hífen |
| CSVs posicionais: `download a.csv b.csv` | `--lista a.csv` | aceita vários CSVs |
| `-u/--url-column` | `--row` | nome descreve o que é |
| `--retry` (já embutido em `update`) | `--reprocess` | mais claro |
| `--wait-on-limit` | `--esperar-bloqueio` | tinha dois nomes |
| `--no-hybrid` | `--modelo-hibrido` | a identificação agora é ligada por padrão no `plpython.py`; `--no-hybrid` desliga |
| `--require-isrc`, `--require-mb-artist` | `--exigir-isrc`, `--exigir-artista-musicbrainz` | nomes curtos |
| `--no-artist-links` | `--artist-links` | os atalhos para artistas secundários agora são o padrão |
| `--downloads-db` | `--state-file` | um só nome em todos os comandos |
| `--legacy-db` | `--legacy-state-file` | movido para opções avançadas |
| `--sheet` | `--arquivo` | consistência |
| `revert/legacy-centralize -s/--songs-dir` | `--root` | mesmo nome do download |

Exemplos:

```bash
python plpython.py -h                     # lista os comandos
python plpython.py download -h            # opções de um comando

# Baixar todos os CSVs da pasta; músicas e M3Us em pastas próprias
python plpython.py download -c ./csvs -s /media/Musicas -p ./m3u

# Só duas playlists, acrescentando às M3Us
python plpython.py download csvs/Alone.csv csvs/Rain.csv --append

# CSV cuja URL está na última coluna
python plpython.py download minha.csv -u -1

# Exigir ISRC confirmado no MusicBrainz
python plpython.py download -c ./csvs --require-isrc

# Atualizar depois de editar CSVs ou após falhas, esperando se o YouTube bloquear
python plpython.py update -c ./csvs --wait-on-limit

# Revisar metadados: simular e depois gravar
python plpython.py metadata
python plpython.py metadata --apply

# Spotify (precisa das variáveis SPOTIFY_*)
python plpython.py spotify "https://open.spotify.com/playlist/ID"

# Desfazer songs/_library
python plpython.py revert -s songs            # simula
python plpython.py revert -s songs --apply
```

### Identificação de artista, álbum e capa (ligada por padrão)

No `plpython.py download`/`update`, cada vídeo passa por um modelo que não depende de acertar de primeira: ele junta várias pistas, dá uma confiança a cada uma e só pede conferência do que ficou incerto. Para desligar e usar só os dados do YouTube, use `--no-hybrid`.

**1. Canal do artista ou re-upload?** O canal recebe pontos a favor ou contra:

| A favor de ser o artista | Contra (re-upload, gravadora, lyrics…) |
|---|---|
| canal "Artista - Topic" (YouTube Music), VEVO, verificado | título traz outro artista ("Fulano - Música" num canal que não é o Fulano) |
| nome do canal aparece no título como artista | nome típico de curadoria: lyrics, lofi, nightcore, slowed, records, NCS… |
| canal igual ao artista creditado pelo YouTube | descrição de re-upload ("no copyright infringement", "I do not own"…) |
| canal já confirmado antes (memória) | canal já confirmado antes como re-upload (memória) |

O nome de um canal de re-upload nunca vira artista.

**2. Quem é o artista e qual é a música?** O título é limpo: sai "(Official Video)", "[Lyrics]", "| NCS - Copyright Free Music", emojis e "No.203"; "ft./feat." vira artista; "(Slowed Version)", "sped up" e "nightcore" ficam marcados no título final. Depois o modelo monta hipóteses com confiança:

| Fonte | Confiança |
|---|---|
| descrição "Provided to YouTube by…" (YouTube Music: título, artistas, álbum, data, distribuidora) | 97% |
| canal Topic | 95% |
| canal já aprendido como do artista | 92% |
| crédito oficial do YouTube (campo artista/faixa) ou artista já confirmado na sua biblioteca | 90% |
| "Artista - Música" quando o canal é o artista | 85–90% |
| "Artista - Música" em canal de re-upload | 72% |
| só o nome do canal (canal desconhecido) | 40% |

Vários artistas ("VGR, CG5", "R3HAB x A Touch Of Class", "Alok & JØRD & DJ MP4") são separados; um único "&" é mantido ("Mumford & Sons") e o MusicBrainz corrige se for colaboração.

**3. Confirmação no MusicBrainz.** As melhores hipóteses são buscadas (as duas ordens de "A - B" quando há dúvida). Com uma correspondência, entram a grafia oficial, os créditos completos, o ISRC e os IDs MusicBrainz.

**4. Álbum e capa.** Entre os lançamentos da gravação, é escolhido o mais provável, como o Picard faz: o álbum citado pelo YouTube, se houver; senão oficial, álbum/EP/single antes de coletânea/ao vivo, o mais antigo. Vão para o MP3: álbum, artista do álbum, data, faixa "n/total", disco, distribuidora, ISRC e IDs MusicBrainz. A **capa do álbum** vem do Cover Art Archive (a do release-group, que é igual entre edições) e substitui a thumbnail do vídeo. Sem capa no Cover Art Archive, a thumbnail do vídeo é recortada para ficar quadrada; no YouTube Music ela já é a capa.

**5. Pastas.** O MP3 fica na pasta do **artista principal**, e cada artista secundário recebe um **atalho** para o mesmo arquivo, sem duplicar o áudio (`--no-artist-links` desliga os atalhos). Se um download antigo estiver na pasta errada e o artista for confirmado, ele é movido para a pasta certa, sem sobrescrever nada.

**6. O que vai para conferência no SQL** (o mínimo possível, sem perder precisão):

| Situação | Resultado |
|---|---|
| Confirmado no MusicBrainz, com álbum e capa | `RESOLVIDO` (não vai para a lista) |
| Fonte forte (≥85%: YouTube Music, Topic, crédito oficial, canal/artista aprendido) com álbum | `RESOLVIDO` |
| YouTube e MusicBrainz divergem | `OBSERVAR`: vence o YouTube, ou o MusicBrainz com `--prefer-db` |
| Artista/título só deduzidos (<85%) | `OBSERVAR` |
| Álbum ou capa não encontrados | `OBSERVAR` |
| Nenhum artista ou falhou `--require-*` | `PENDENTE` |

**A lista diminui sozinha com o tempo.** Toda faixa confirmada, pelo MusicBrainz ou por você (`review --ok`, `metadata --apply`), ensina duas coisas guardadas no SQL:
- se o canal é do artista ou um re-upload (decidido pela maioria dos votos, para um erro isolado não estragar);
- quais artistas existem na sua biblioteca.

Assim, os próximos vídeos do mesmo canal ou artista saem resolvidos. No `update`, downloads antigos que estavam na lista têm os metadados consultados de novo no YouTube (sem baixar o áudio), para usar essas pistas.

| Opção | Efeito |
|---|---|
| *(padrão)* | divergência → YouTube; marca divergências, deduções e faltas |
| `--prefer-db` | divergência → MusicBrainz; ainda marca |
| `--review diff` | marca só divergências |
| `--no-review` | não marca nada (as notas ficam guardadas no banco) |
| `--no-hybrid` | desliga a identificação (mais rápido, só YouTube) |

### Consultas ao YouTube e bloqueios

- **Uma leitura por música nova:** os metadados lidos para identificar a música são reaproveitados no download, do mesmo jeito que o `yt-dlp --load-info-json`. Se o link guardado expirar, o próprio yt-dlp volta a consultar pela URL. Músicas já baixadas não consultam o YouTube.
- **Pausa entre vídeos:** 5 a 10 s, sorteados, como recomenda a wiki do yt-dlp. O tempo gasto com MusicBrainz e com o download já conta, então só se espera o que faltar. Ajuste com `--sleep 10-20` ou desligue com `--sleep 0`. Dentro de cada consulta há 0,75 s entre requisições, como no preset `-t sleep` do yt-dlp.
- **Limites (wiki do yt-dlp):** cerca de 300 vídeos por hora sem conta, cerca de 2000 com conta.
- **Bloqueio da sessão** ("This content isn't available, try again later", HTTP 429, "confirm you're not a bot"): segundo o yt-dlp, dura **até 1 hora**. Sem `--wait-on-limit`, o programa para na hora e avisa para esperar cerca de 1 hora; o progresso fica salvo e basta rodar de novo. Com `--wait-on-limit`, ele espera 5, 15, 30 e 60 min (quase 2 h no total) antes de desistir.
- **Falha de rede** (502/503/504, timeout): com `--wait-on-limit`, novas tentativas em 30 s, 1, 2, 4, 8 e 10 min.

**Conferir no terminal** (não precisa de programa de SQL):

```bash
python plpython.py review                       # faixas OBSERVAR/PENDENTE com o motivo
python plpython.py review -d                    # + álbum, ISRC, arquivo e link do YouTube
python plpython.py review --status todas --busca snuffles
python plpython.py review --ok UG1HUOyL9IA      # está certo: marca como conferida
python plpython.py review --ok-all              # confirma todas as OBSERVAR
python plpython.py review --export conferir.csv # abre no Excel/LibreOffice
python plpython.py review --sql "SELECT title, album FROM music_tracks"   # SQL somente leitura
```

Rode na mesma pasta onde rodou o download, porque ele lê `./sql/`. Para corrigir título ou artista, use a planilha e o comando `metadata`.

Outros lugares para conferir: `metadados_pendentes.csv` (agora com a coluna `observacoes`), a pasta `_observar/` com atalhos para os MP3s, ou direto no SQL:

```sql
-- sqlite3 csvs/sql/musicas.sqlite3
SELECT title, artists_json, review_note FROM music_tracks WHERE metadata_status = 'OBSERVAR';
```

Se estiver certo, marque `aplicar=sim` e `confirmar_manual=sim` na planilha e rode `plpython.py metadata --apply`. `update` reconsulta automaticamente as faixas `PENDENTE` e `OBSERVAR` que você ainda não revisou.

```bash
python plpython.py download -c ./csvs               # identifica tudo (padrão)
python plpython.py download -c ./csvs --prefer-db
python plpython.py download -c ./csvs --review diff
python plpython.py download -c ./csvs --no-review
```

## O que já funciona

### Downloader do YouTube

`pltube.py` lê um ou vários CSVs com URLs de vídeos do YouTube, usa o `yt-dlp` para obter as informações disponíveis e baixar o áudio em MP3. O FFmpeg é usado para conversão e para incorporar ao arquivo as tags e a thumbnail recebidas no processamento.

`--csvfile` (`-c`; o antigo `--BaseDir` ainda funciona) aponta somente para a pasta dos CSVs ou para um `.csv` específico. `songs/`, `playlists/`, `sql/`, `Logs.txt` e `metadados_pendentes.csv` ficam na pasta de onde você roda o programa. `--SongsDir`/`-s` e `--PlaylistDir`/`-p` escolhem outros nomes ou caminhos; caminhos relativos são resolvidos a partir do diretório atual.

O downloader cria uma playlist M3U para cada CSV. A coluna da URL pode ser indicada com `--row` (índice começando em zero; o padrão é `2`). A opção `--append` acrescenta itens à M3U existente sem duplicar caminhos já listados.

Por padrão, o arquivo MP3 fica dentro da pasta do primeiro artista informado pelos metadados. Com `--artist-links`, essa mesma organização é mantida e o programa cria links para o mesmo arquivo nas pastas dos outros artistas em colaborações. Assim, o áudio existe fisicamente uma vez, na pasta do artista principal; não é criada uma pasta central `_library`. A ordem dos artistas depende dos créditos fornecidos pelo YouTube/yt-dlp.

Essa mudança vale para os novos downloads e para arquivos que o downloader consiga reutilizar no local indicado. Ela não move automaticamente arquivos que já estejam em `_library` na raiz de músicas indicada.

O downloader mantém uma linha de estado para cada download desde que a URL é processada, além do progresso por CSV; retoma itens interrompidos e reutiliza o mesmo vídeo quando ele aparece em outra playlist. `--reprocess` tenta novamente itens que falharam e, no modo híbrido, reconsulta pendências do MusicBrainz sem baixar novamente faixas concluídas nem refazer a consulta ao YouTube. Para bloqueios temporários, `--esperar-bloqueio` ativa tentativas com espera progressiva; se o bloqueio continuar, a execução para antes de consultar outros CSVs.

O estado é separado em dois arquivos no diretório `sql/` (na pasta atual): `downloads.sqlite3` acompanha fila, tentativas, erros, progresso dos CSVs e relações com playlists; `musicas.sqlite3` guarda uma tabela pesquisável por faixa com título, artistas, álbum, ISRC, dados de origem, proveniência, confiança e diagnósticos, além dos resultados completos do modo híbrido. O MP3 não é embutido no banco. Caminhos de áudio e de CSV são relativos para que a estrutura possa ser movida preservando sua relação com a pasta `sql/`. O banco antigo único `pltube_state.sqlite3`, se existir, é migrado uma vez sem ser apagado. Os arquivos podem ser personalizados com `--downloads-db` e `--metadata-db`.

### Modelo híbrido de metadados (opcional)

O modo híbrido só é ativado com `--modelo-hibrido`; sem esse argumento, o comportamento atual não muda. Ele consulta o MusicBrainz e, quando encontra uma correspondência suficientemente clara, prefere título e créditos canônicos da base. A comparação tolera pequenas diferenças de acentos, pontuação, sufixos como “Official Audio” e erros simples de grafia; se houver candidatos próximos ou conflito, não escolhe silenciosamente. Quando reutiliza um MP3 local, também lê o frame ID3 `TSRC` para recuperar um ISRC válido. Esse valor entra na consulta MusicBrainz com proveniência `local`, não é apresentado como se tivesse vindo do YouTube, e só é considerado confirmado se o MusicBrainz ou uma revisão manual o corroborar.

Se a base não confirmar os dados, o programa mantém os valores disponíveis do vídeo como provisórios — sem transformar o canal/uploader em artista — e marca o MP3 com tags ID3 `PLTUBE_METADATA_STATUS=PENDENTE` e uma observação de que os dados podem estar incorretos. O arquivo físico continua na pasta do artista disponível (ou `Artista Desconhecido` se não houver crédito de artista). A pasta `_pendentes`, dentro da raiz de músicas configurada, contém apenas um link para localizar cada faixa pendente, sem duplicar o áudio. Arquivos que já existiam são identificados e registrados sem serem movidos automaticamente.

As faixas que precisam de revisão ficam em `metadados_pendentes.csv`, na pasta base: uma linha por vídeo, com o caminho do áudio, playlists onde aparece, motivos e candidatos do MusicBrainz. A planilha inclui sugestões preenchidas e colunas para você escolher os campos finais. As escolhas são preservadas quando a fila é atualizada; os campos `usar_*` e `aplicar` começam como `nao`, então nada é aprovado automaticamente.

Abra a planilha no Excel ou LibreOffice. Para cada faixa que revisou, marque `aplicar=sim`; marque `usar_titulo`, `usar_artistas`, `usar_album` ou `usar_isrc` como `sim` somente para os campos que quer gravar e ajuste o respectivo campo `_final`. Separe vários artistas com `|`. Use `__LIMPAR__` em `album_final` ou `isrc_final` para apagar explicitamente o campo. Depois de conferir todos os dados, marque `confirmar_manual=sim` para retirar a faixa da fila de pendências; isso exige título e pelo menos um artista.

O aplicador altera as tags ID3 do MP3, registra a origem manual no SQLite e atualiza os nomes mostrados nas M3Us relacionadas. O arquivo de áudio não é movido ou renomeado. Primeiro faça uma simulação; somente `--apply` grava as mudanças. Nenhuma consulta ao YouTube ou MusicBrainz é feita pelo aplicador.

O resolvedor também pode registrar uma URL de capa do Cover Art Archive como sugestão. Nesta primeira integração, baixar essa imagem e substituir a thumbnail já incorporada ao MP3 ainda não foi implementado.

Exemplos:

```bash
# Processar todos os CSVs da pasta indicada
python pltube.py --csvfile ./csvs

# Usar pastas de saída específicas; sem estas opções, ficam em ./songs e ./playlists
python pltube.py --csvfile ./csvs --SongsDir /media/Musicas --PlaylistDir ./m3u

# Processar uma playlist específica, usando a coluna de URLs de índice 2
python pltube.py --lista csvs/Alone.csv --row 2

# Guardar no artista principal, linkar colaboradores e acrescentar à playlist
python pltube.py --lista csvs/Alone.csv --artist-links --append

# Repetir falhas anteriores e aguardar se houver bloqueio temporário
python pltube.py --lista csvs/Alone.csv --reprocess --esperar-bloqueio

# Ativar o modelo híbrido e gerar/atualizar a fila de metadados pendentes
python pltube.py --csvfile ./csvs --modelo-hibrido

# Exigir ISRC e confirmação do artista pelo MusicBrainz ou revisão manual
python pltube.py --csvfile ./csvs --modelo-hibrido --exigir-isrc --exigir-artista-musicbrainz

# Simular as edições selecionadas na planilha
python aplicar_metadados.py

# Aplicar as edições depois de revisar o plano
python aplicar_metadados.py --apply
```

### Consulta de playlist do Spotify

`spotify_playlist_viewer.py` autentica o usuário e exibe as faixas e os links de uma playlist Spotify autorizada. É somente uma consulta: não baixa músicas, não altera a playlist e não converte automaticamente os itens para o YouTube.

Para usar, é preciso configurar um app no Spotify for Developers e definir `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` e `SPOTIFY_REDIRECT_URI`. Depois, execute:

```bash
python spotify_playlist_viewer.py "https://open.spotify.com/playlist/ID_DA_PLAYLIST"
```

### Ferramenta de migração antiga

`sincronizar_biblioteca.py` foi feita para o layout anterior, que centralizava os MP3s em `songs/_library` e mantinha links nas pastas dos artistas. Ela ainda pode ser usada para esse formato legado, mas **não a execute com `--apply` se quiser manter o layout novo sem pasta central**.

```bash
# Conferir as mudanças propostas
python sincronizar_biblioteca.py --root songs

# Aplicar depois de revisar a simulação
python sincronizar_biblioteca.py --root songs --apply
```

O migrador reconhece duplicatas idênticas e links existentes, e não baixa novamente as músicas. Ele cria o layout legado com `songs/_library`.

### Reverter para pastas de artistas

`reverter_biblioteca.py` desfaz esse layout legado: move o MP3 físico para a pasta do artista principal, cria links para os colaboradores, atualiza caminhos M3U que apontam diretamente para `_library` e remove a pasta central se ela ficar vazia. Por padrão, o script apenas simula; nada é movido até acrescentar `--apply`.

```bash
# Conferir o plano sem alterar arquivos
python reverter_biblioteca.py --root songs

# Aplicar depois de revisar o resultado
python reverter_biblioteca.py --root songs --apply

# Se suas M3Us estiverem em outra pasta
python reverter_biblioteca.py --root songs --playlist-dir ./minhas_playlists
```

O script tenta primeiro recuperar a ordem original do banco SQLite do `pltube` (autodetectado ao lado de `songs`), depois usa as tags do MP3 e, por último, os links existentes. Se só houver vários links e nenhuma dessas fontes informar a ordem, escolhe a primeira pasta em ordem alfabética e avisa na simulação. Conflitos de nomes não sobrescrevem arquivos: um nome alternativo com hash é usado.

## O que falta implementar

A lista abaixo contém apenas itens ainda não disponíveis.

- [ ] **Reorganização após correção manual:** o `download`/`update` já movem o MP3 para a pasta do artista principal quando o artista é confirmado; o `metadata --apply` ainda preserva o caminho do arquivo quando você troca o artista na planilha.
- [ ] **Catálogo global da biblioteca:** indexar todos os arquivos locais, seus caminhos, fontes, tags e relações com playlists; permitir filtros como `unknown` e `*` para localizar pendências ou revisar o catálogo completo.
- [ ] **Backup portátil de metadados:** criar e restaurar um catálogo SQLite com URLs, metadados e referências dos arquivos, sem exigir que os MP3s estejam conectados para consultar o catálogo. Esse backup não conterá o áudio.
- [ ] **Transferência Spotify → YouTube:** pesquisar correspondências de faixas e gerar uma lista para revisão. O visualizador atual não faz essa conversão.

A descrição do modelo híbrido está em [MODELO_HIBRIDO_METADADOS.md](MODELO_HIBRIDO_METADADOS.md). As propostas de catálogo/backup ainda não fazem parte da implementação do repositório.

## Requisitos e testes

- Python 3.10 ou superior.
- Instalar as dependências Python com `pip install -r requirements.txt`.
- FFmpeg disponível no sistema para conversão e incorporação de tags/imagens.

Para executar os testes:

```bash
python -m unittest discover -s tests -v
```
