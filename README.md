# pltube

Ferramenta em Python para baixar áudios de URLs do YouTube listadas em arquivos CSV e criar playlists no formato M3U. O programa usa os metadados do YouTube/yt-dlp para identificar os artistas.

O desenho proposto para enriquecer esses dados e revisar metadados incertos está descrito em [MODELO_HIBRIDO_METADADOS.md](MODELO_HIBRIDO_METADADOS.md). É uma especificação para implementação futura; o resolvedor híbrido e a fila editável de revisão ainda não fazem parte do downloader.

## Requisitos

- Python 3.10 ou superior
- Dependências Python: `pip install -r requirements.txt`
- FFmpeg disponível no sistema para converter o áudio para MP3 e incorporar metadados/miniaturas

## Uso

Por padrão, o programa procura arquivos `.csv` diretamente na pasta atual, salva as músicas em `songs/<artista>/` e cria as playlists em `playlists/`:

```bash
python pltube.py
```

Para processar um CSV específico e apontar a coluna da URL (o índice começa em zero; o padrão é `2`):

```bash
python pltube.py --lista csvs/Alone.csv --row 2
```

Também é possível configurar as pastas:

```bash
python pltube.py --BaseDir ./csvs --SongsDir ./songs --PlaylistDir ./playlists --LogFile ./Logs.txt
```

Para ativar **opcionalmente** a organização sem duplicar o áudio, use `--artist-links` (ou `--links-artista`):

```bash
python pltube.py --lista csvs/Alone.csv --artist-links
```

Nesse modo, cada música é baixada uma vez em `songs/_library/`. O programa cria um link para esse arquivo dentro da pasta de cada artista que os metadados do YouTube informarem, inclusive em colaborações. Ele tenta usar links simbólicos e, se o sistema não permitir, tenta hard links; nenhum dos dois copia o conteúdo do áudio. Sem a opção, continua usando a pasta do artista principal como antes.

Para acrescentar itens à playlist existente sem repetir caminhos já presentes:

```bash
python pltube.py --lista csvs/Alone.csv --append
```

Se o YouTube responder com um bloqueio temporário ou limite de requisições, você pode habilitar esperas progressivas com `--esperar-bloqueio` (alias `--wait-on-limit`):

```bash
python pltube.py --lista csvs/Alone.csv --esperar-bloqueio
```

Esse modo tenta novamente após 30 segundos, dobrando a espera a cada falha até o limite de 10 minutos. Ele faz no máximo 6 novas tentativas e então interrompe toda a execução, sem consultar os CSVs seguintes enquanto o acesso estiver bloqueado. Sem o parâmetro, não há espera automática; ao detectar um bloqueio, a execução também é interrompida. `-h` ou `--help` mostra todas as opções.

## Cache e retomada

O programa mantém por padrão `pltube_state.sqlite3` na pasta base (o arquivo é ignorado pelo Git). Depois de cada linha processada, salva o ponto de retomada e os dados da faixa. Se o programa for interrompido, execute o mesmo comando novamente: as linhas já checkpointadas não serão consultadas de novo no YouTube. Um CSV inalterado e concluído nem precisa ser aberto; a playlist M3U é reconstruída a partir do banco local.

O cache de vídeos é compartilhado entre todos os CSVs: se o mesmo vídeo do YouTube aparece em outra playlist, o caminho local é reutilizado, sem nova consulta nem novo download. A deduplicação usa o ID do vídeo; uploads diferentes da mesma música não são considerados iguais automaticamente. Arquivos antigos com o ID no nome (`[ID-do-video].mp3`) também podem ser reconhecidos localmente. Arquivos sem ID, e que ainda não estejam no banco de estado, podem exigir uma consulta inicial ao YouTube para identificá-los.

Para tentar novamente faixas que falharam anteriormente, reprocessando a lista com o cache de sucessos:

```bash
python pltube.py --lista csvs/Alone.csv --reprocess
```

É possível mudar a localização do banco com `--state-file caminho.sqlite3`. Não apague esse banco se quiser conservar o progresso; apague-o apenas se desejar reiniciar todo o histórico local. O CSV parcialmente processado ainda é percorrido localmente até o cursor salvo, mas as linhas concluídas não geram novas chamadas ao yt-dlp/YouTube. O cache reduz consultas repetidas, mas faixas novas ainda exigem acesso ao YouTube e podem estar sujeitas aos limites definidos pelo serviço.

As falhas são registradas em `Logs.txt` por padrão. Os testes locais podem ser executados com:

```bash
python -m unittest discover -s tests -v
```

## Observações

- CSVs devem ter cabeçalho e uma coluna com URL do vídeo. Se o formato for diferente, ajuste `--row`.
- `--row` aceita índices negativos, como `-1` para a última coluna.
- O tempo de bloqueio é decidido pelo YouTube e não tem uma duração fixa que o programa possa prever. A opção de espera apenas consulta novamente com pausas crescentes; ela não remove bloqueios de IP, exigências de login ou CAPTCHA.
- O código ignora linhas sem URLs HTTP(S) e continua o processamento se uma música falhar.
- A identificação de vários artistas usa o campo `artists` dos metadados quando disponível. Créditos explícitos de participação (`feat.`/`ft.`) no campo `artist` também são reconhecidos.

## Consulta de playlists Spotify

O módulo separado `spotify_playlist_viewer.py` consulta e mostra as faixas de uma playlist Spotify autorizada. Ele não baixa áudio, não cria cópias locais da lista e não usa os dados do Spotify para procurar downloads em outros serviços.

A Web API oficial disponibiliza os itens e metadados da playlist, mas proíbe baixar o conteúdo de áudio. Para escutar offline, use o botão **Download** no app Spotify com Premium; os arquivos ficam disponíveis dentro do app, não como MP3s exportáveis.

Para usar o visualizador, crie um app no [Spotify for Developers](https://developer.spotify.com/dashboard), cadastre um Redirect URI como `http://127.0.0.1:8888/callback` nas configurações desse app e defina as três variáveis abaixo no terminal:

```bash
export SPOTIFY_CLIENT_ID="seu_client_id"
export SPOTIFY_CLIENT_SECRET="seu_client_secret"
export SPOTIFY_REDIRECT_URI="http://127.0.0.1:8888/callback"
python spotify_playlist_viewer.py "https://open.spotify.com/playlist/ID_DA_PLAYLIST"
```

Na primeira execução, o navegador abre para autorizar a leitura. A API pode limitar o acesso a playlists que pertencem à conta autenticada ou nas quais ela é colaboradora. O token OAuth fica no cache privado `~/.cache/pltube/`, e as faixas são apenas exibidas no terminal.

## Migrar músicas que já foram baixadas

Se as músicas ainda estão em `songs/<artista>/` porque foram baixadas antes de ativar `--artist-links`, use o script separado `sincronizar_biblioteca.py`. Primeiro ele só mostra o que faria, sem alterar arquivos:

```bash
python sincronizar_biblioteca.py --root songs
```

Revise o plano. Para aplicar a mudança, repita com `--apply`:

```bash
python sincronizar_biblioteca.py --root songs --apply
```

No conjunto de exemplos deste repositório, os áudios estão em `csvs/songs`, então use `--root csvs/songs` nos dois comandos acima.

O script move cada áudio uma vez para `<root>/_library/`, substitui o caminho antigo por um link e remove cópias idênticas repetidas. Assim, playlists M3U que usam os caminhos antigos continuam apontando para os links. Ele usa tags de artista embutidas quando disponíveis e, na falta delas, considera a pasta atual como artista. Instale `requirements.txt` para habilitar a leitura das tags. A biblioteca não é baixada novamente. Faça uma cópia de segurança antes de aplicar qualquer migração em uma pasta com dados importantes.
