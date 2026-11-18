# pltube

Ferramentas em Python para organizar playlists a partir de URLs do YouTube, baixar áudio, criar listas M3U e manter uma biblioteca de músicas. O projeto também inclui uma consulta somente de leitura a playlists do Spotify e um utilitário para migrar bibliotecas já baixadas.

O README separa o que **já funciona** do que está no **plano de implementação**. As tarefas futuras não estão disponíveis como comandos no código atual.

## O que já funciona

### Downloader do YouTube

`pltube.py` lê um ou vários CSVs com URLs de vídeos do YouTube, usa o `yt-dlp` para obter as informações disponíveis e baixar o áudio em MP3. O FFmpeg é usado para conversão e para incorporar ao arquivo as tags e a thumbnail recebidas no processamento.

O downloader cria uma playlist M3U para cada CSV. A coluna da URL pode ser indicada com `--row` (índice começando em zero; o padrão é `2`). A opção `--append` acrescenta itens à M3U existente sem duplicar caminhos já listados.

Por padrão, as músicas ficam organizadas pela pasta do artista principal identificado nos metadados. Com `--artist-links`, o áudio é guardado uma vez na biblioteca central e o programa cria links nas pastas dos artistas reconhecidos, inclusive em colaborações. Essa opção evita cópias do áudio, mas depende dos créditos informados pelo YouTube/yt-dlp.

O downloader também mantém progresso por CSV, retoma itens já processados e reutiliza o mesmo vídeo quando ele aparece em outra playlist. `--reprocess` tenta novamente itens que falharam sem baixar de novo as faixas já concluídas. Para bloqueios temporários, `--esperar-bloqueio` ativa tentativas com espera progressiva; se o bloqueio continuar, a execução para antes de consultar outros CSVs.

Exemplos:

```bash
# Processar todos os CSVs da pasta indicada
python pltube.py --BaseDir ./csvs

# Processar uma playlist específica, usando a coluna de URLs de índice 2
python pltube.py --lista csvs/Alone.csv --row 2

# Ativar links por artista e acrescentar sem repetir caminhos
python pltube.py --lista csvs/Alone.csv --artist-links --append

# Repetir falhas anteriores e aguardar se houver bloqueio temporário
python pltube.py --lista csvs/Alone.csv --reprocess --esperar-bloqueio
```

### Consulta de playlist do Spotify

`spotify_playlist_viewer.py` autentica o usuário e exibe as faixas e os links de uma playlist Spotify autorizada. É somente uma consulta: não baixa músicas, não altera a playlist e não converte automaticamente os itens para o YouTube.

Para usar, é preciso configurar um app no Spotify for Developers e definir `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` e `SPOTIFY_REDIRECT_URI`. Depois, execute:

```bash
python spotify_playlist_viewer.py "https://open.spotify.com/playlist/ID_DA_PLAYLIST"
```

### Migração da biblioteca existente

`sincronizar_biblioteca.py` ajuda a adaptar músicas que já estão em pastas de artistas para a organização com uma biblioteca central e links. Primeiro apresenta uma simulação; só altera arquivos com `--apply`.

```bash
# Conferir as mudanças propostas
python sincronizar_biblioteca.py --root songs

# Aplicar depois de revisar a simulação
python sincronizar_biblioteca.py --root songs --apply
```

O migrador reconhece duplicatas idênticas e links existentes, e não baixa novamente as músicas.

## O que falta implementar

A lista abaixo é a direção planejada para o projeto. Os itens ainda não estão prontos para uso.

- [ ] **Modelo híbrido de metadados:** combinar dados do YouTube com buscas em bases musicais, priorizando correções manuais e ISRC quando disponível; não tratar o canal/uploader como artista confirmado.
- [ ] **Tratamento de ambiguidades:** quando artista, álbum ou capa não puderem ser confirmados, marcar os campos como pendentes em vez de inventar um valor.
- [ ] **Revisão e correção de metadados:** gerar uma lista editável com título, artistas, álbum, ISRC, capa e caminho do MP3; permitir atualizar as tags do arquivo já baixado, sem baixar o áudio novamente.
- [ ] **Aplicação segura de correções:** pré-visualização sem alterações por padrão, aplicação explícita, seleção por ID de faixa e correção em lote somente dos itens marcados como revisados.
- [ ] **Catálogo global da biblioteca:** indexar todos os arquivos locais, seus caminhos, fontes, tags e relações com playlists; permitir filtros como `unknown` e `*` para localizar pendências ou revisar o catálogo completo.
- [ ] **Backup portátil de metadados:** criar e restaurar um catálogo SQLite com URLs, metadados e referências dos arquivos, sem exigir que os MP3s estejam conectados para consultar o catálogo. Esse backup não conterá o áudio.
- [ ] **Comando unificado:** oferecer uma única entrada com subcomandos, por exemplo `pltube download`, `pltube migrate`, `pltube spotify`, `pltube catalog` e `pltube metadata`, encaminhando cada um para a função correspondente sem alterar sua lógica.
- [ ] **Transferência Spotify → YouTube:** pesquisar correspondências de faixas e gerar uma lista para revisão. O visualizador atual não faz essa conversão.

A especificação do modelo híbrido está em [MODELO_HIBRIDO_METADADOS.md](MODELO_HIBRIDO_METADADOS.md). As propostas de catálogo/backup e da interface unificada ainda não fazem parte da implementação do repositório.

## Requisitos e testes

- Python 3.10 ou superior.
- Instalar as dependências Python com `pip install -r requirements.txt`.
- FFmpeg disponível no sistema para conversão e incorporação de tags/imagens.

Para executar os testes:

```bash
python -m unittest discover -s tests -v
```
