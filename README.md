# pltube

Ferramentas em Python para organizar playlists a partir de URLs do YouTube, baixar áudio, criar listas M3U e manter uma biblioteca de músicas. O projeto também inclui uma consulta somente de leitura a playlists do Spotify e um utilitário para migrar bibliotecas já baixadas.

O README separa os recursos disponíveis dos itens que ainda faltam implementar.

## O que já funciona

### Downloader do YouTube

`pltube.py` lê um ou vários CSVs com URLs de vídeos do YouTube, usa o `yt-dlp` para obter as informações disponíveis e baixar o áudio em MP3. O FFmpeg é usado para conversão e para incorporar ao arquivo as tags e a thumbnail recebidas no processamento.

`--BaseDir` aponta somente para a pasta dos CSVs. Se `--SongsDir` e `--PlaylistDir` não forem informados, os MP3s organizados em pastas de artistas e as M3Us ficam nessa mesma pasta. Se forem informados, os caminhos especificados são usados sem acrescentar automaticamente `songs/` ou `playlists/`; caminhos relativos desses dois argumentos são resolvidos a partir do diretório atual.

O downloader cria uma playlist M3U para cada CSV. A coluna da URL pode ser indicada com `--row` (índice começando em zero; o padrão é `2`). A opção `--append` acrescenta itens à M3U existente sem duplicar caminhos já listados.

Por padrão, o arquivo MP3 fica dentro da pasta do primeiro artista informado pelos metadados. Com `--artist-links`, essa mesma organização é mantida e o programa cria links para o mesmo arquivo nas pastas dos outros artistas em colaborações. Assim, o áudio existe fisicamente uma vez, na pasta do artista principal; não é criada uma pasta central `_library`. A ordem dos artistas depende dos créditos fornecidos pelo YouTube/yt-dlp.

Essa mudança vale para os novos downloads e para arquivos que o downloader consiga reutilizar no local indicado. Ela não move automaticamente arquivos que já estejam em `_library` na raiz de músicas indicada.

O downloader mantém uma linha de estado para cada download desde que a URL é processada, além do progresso por CSV; retoma itens interrompidos e reutiliza o mesmo vídeo quando ele aparece em outra playlist. `--reprocess` tenta novamente itens que falharam e, no modo híbrido, reconsulta pendências do MusicBrainz sem baixar novamente faixas concluídas nem refazer a consulta ao YouTube. Para bloqueios temporários, `--esperar-bloqueio` ativa tentativas com espera progressiva; se o bloqueio continuar, a execução para antes de consultar outros CSVs.

O estado é separado em dois arquivos no diretório `<BaseDir>/sql/`: `downloads.sqlite3` acompanha fila, tentativas, erros, progresso dos CSVs e relações com playlists; `musicas.sqlite3` guarda uma tabela pesquisável por faixa com título, artistas, álbum, ISRC, dados de origem, proveniência, confiança e diagnósticos, além dos resultados completos do modo híbrido. O MP3 não é embutido no banco. Caminhos de áudio e de CSV são relativos para que a estrutura possa ser movida preservando sua relação com a pasta `sql/`. O banco antigo único `pltube_state.sqlite3`, se existir, é migrado uma vez sem ser apagado. Os arquivos podem ser personalizados com `--downloads-db` e `--metadata-db`.

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
python pltube.py --BaseDir ./csvs

# Usar pastas de saída específicas; sem estas opções, áudio e M3Us ficam junto dos CSVs
python pltube.py --BaseDir ./csvs --SongsDir /media/Musicas --PlaylistDir ./m3u

# Processar uma playlist específica, usando a coluna de URLs de índice 2
python pltube.py --lista csvs/Alone.csv --row 2

# Guardar no artista principal, linkar colaboradores e acrescentar à playlist
python pltube.py --lista csvs/Alone.csv --artist-links --append

# Repetir falhas anteriores e aguardar se houver bloqueio temporário
python pltube.py --lista csvs/Alone.csv --reprocess --esperar-bloqueio

# Ativar o modelo híbrido e gerar/atualizar a fila de metadados pendentes
python pltube.py --BaseDir ./csvs --modelo-hibrido

# Exigir ISRC e confirmação do artista pelo MusicBrainz ou revisão manual
python pltube.py --BaseDir ./csvs --modelo-hibrido --exigir-isrc --exigir-artista-musicbrainz

# Simular as edições selecionadas na planilha
python aplicar_metadados.py --BaseDir ./csvs

# Aplicar as edições depois de revisar o plano
python aplicar_metadados.py --BaseDir ./csvs --apply
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
- [ ] **Comando unificado:** oferecer uma única entrada com subcomandos, por exemplo `pltube download`, `pltube migrate`, `pltube spotify`, `pltube catalog` e `pltube metadata`, encaminhando cada um para a função correspondente sem alterar sua lógica.
- [ ] **Transferência Spotify → YouTube:** pesquisar correspondências de faixas e gerar uma lista para revisão. O visualizador atual não faz essa conversão.

A descrição do modelo híbrido está em [MODELO_HIBRIDO_METADADOS.md](MODELO_HIBRIDO_METADADOS.md). As propostas de catálogo/backup e da interface unificada ainda não fazem parte da implementação do repositório.

## Requisitos e testes

- Python 3.10 ou superior.
- Instalar as dependências Python com `pip install -r requirements.txt`.
- FFmpeg disponível no sistema para conversão e incorporação de tags/imagens.

Para executar os testes:

```bash
python -m unittest discover -s tests -v
```
