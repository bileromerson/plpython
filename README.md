# pltube

Ferramentas em Python para organizar playlists a partir de URLs do YouTube, baixar áudio, criar listas M3U e manter uma biblioteca de músicas. O projeto também inclui uma consulta somente de leitura a playlists do Spotify e um utilitário para migrar bibliotecas já baixadas.

O README separa os recursos disponíveis dos itens que ainda faltam implementar.

## Comando unificado: `plpython.py`

Todas as ferramentas agora têm uma entrada única com subcomandos. Os scripts antigos continuam funcionando como antes.

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
| `--hybrid` | `--modelo-hibrido` | curto |
| `--require-isrc`, `--require-mb-artist` | `--exigir-isrc`, `--exigir-artista-musicbrainz` | agora ativam `--hybrid` sozinhos; antes eram ignorados sem ele |
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

# Só duas playlists, linkando colaboradores e acrescentando às M3Us
python plpython.py download csvs/Alone.csv csvs/Rain.csv --artist-links --append

# CSV cuja URL está na última coluna
python plpython.py download minha.csv -u -1

# Conferir no MusicBrainz exigindo ISRC (ativa --hybrid automaticamente)
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

### Prioridades do modelo híbrido (YouTube × MusicBrainz)

Com `--hybrid`, cada faixa é comparada campo a campo (título, artistas, álbum, ISRC) entre o YouTube e o MusicBrainz:

- **Iguais** (tolerando maiúsculas, acentos, "(Official Video)", "feat."): usa a grafia oficial do MusicBrainz e a faixa fica `RESOLVIDO`.
- **Diferentes:** por padrão vence o **YouTube**; com `--prefer-db` vence o **MusicBrainz**. Nos dois casos a faixa fica `OBSERVAR` com a divergência anotada.
- **Não encontrada no MusicBrainz:** usa o YouTube e marca `OBSERVAR` ("veio só do YouTube").
- **`PENDENTE`** fica só para o que realmente falta: sem artista algum, ou um requisito de `--require-isrc`/`--require-mb-artist`.

Para identificar melhor quando o YouTube não dá crédito de artista, o título é limpo (remove "(Official Video)", "[Lyrics]", "HD"…), participações "feat./ft." viram artistas, canais "Artista - Topic" são usados, e títulos "A - B" são testados nas duas ordens no MusicBrainz, ficando a que bater melhor.

| Opção | Efeito |
|---|---|
| *(padrão)* | divergência → YouTube; marca `OBSERVAR` divergências **e** dados só do YouTube |
| `--prefer-db` | divergência → MusicBrainz; ainda marca `OBSERVAR` |
| `--review diff` | marca só divergências; dados só do YouTube passam direto |
| `--no-review` (`--review none`) | não marca nada, segue direto (as notas ficam guardadas no banco) |

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
python plpython.py download -c ./csvs --hybrid
python plpython.py download -c ./csvs --prefer-db
python plpython.py download -c ./csvs --hybrid --review diff
python plpython.py download -c ./csvs --hybrid --no-review
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

- [ ] **Reorganização após revisão:** mover o MP3 confirmado para a pasta do artista principal e atualizar links de colaboradores, se essa opção for escolhida. O aplicador atual preserva o caminho do arquivo.
- [ ] **Capa do lançamento:** baixar a imagem frontal confirmada do Cover Art Archive, incorporá-la ao MP3 e preservar uma única arte escolhida; por enquanto a URL é apenas uma sugestão.
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
