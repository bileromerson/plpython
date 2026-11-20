# Exemplo dos dois bancos SQLite do plpython

O downloader mantém dois arquivos dentro de `sql/`, na pasta de onde você roda o `plpython.py`:

- `downloads.sqlite3`: fila, progresso e relações com CSVs/playlists.
- `musicas.sqlite3`: cadastro estruturado de faixas e detalhes de resolução de metadados.

Os exemplos abaixo são fictícios. O áudio MP3 **não** fica dentro do SQLite. O banco guarda caminhos relativos para encontrá-lo. Se os arquivos estiverem organizados assim:

```text
Projeto/
├── csvs/
├── songs/Artista A/musica1.mp3
└── sql/downloads.sqlite3
```

E a pasta `sql/` contém o banco, o caminho armazenado para o MP3 é `../songs/Artista A/musica1.mp3`. Se `--SongsDir` não for informado, os arquivos ficam na pasta dos CSVs, e o caminho relativo será calculado para essa estrutura, sem criar automaticamente uma pasta `songs/`.

O SQLite guarda valores tabulares num arquivo binário local. Listas e objetos mais detalhados são guardados como JSON em algumas colunas de texto. URLs de thumbnail/capa podem ser guardadas, mas não o arquivo de imagem em si.

## Banco `downloads.sqlite3`

### `download_jobs` — acompanhamento desde o início

Uma linha por vídeo processado; é criada quando o programa encontra a URL. O status muda durante o fluxo.

| video_key | video_id | source_url | status | attempts | audio_path | error |
|---|---|---|---|---:|---|---|
| `youtube:abc123` | `abc123` | `https://www.youtube.com/watch?v=abc123` | `CONCLUIDO` | 1 | `../songs/Artista A/musica1.mp3` | *(vazio)* |

Estados usados: `PENDENTE`, `METADADOS`, `BAIXANDO`, `CONCLUIDO` e `ERRO`. `attempts` conta as tentativas de download. Se a execução for interrompida durante o download, o registro preserva o estado e permite tentar de novo. O caminho do áudio é relativo à pasta onde fica `downloads.sqlite3`.

### `video_cache` — cache de metadados/download

| video_key | video_id | source_url | status | title | artists_json | duration | path | error |
|---|---|---|---|---|---|---:|---|---|
| `youtube:abc123` | `abc123` | `https://www.youtube.com/watch?v=abc123` | `complete` | `Canção Exemplo` | `["Artista A","Artista B"]` | 215.4 | `../songs/Artista A/musica1.mp3` | *(vazio)* |

Esta tabela é utilizada para reutilizar informação de vídeos sem nova consulta ao YouTube e para reconhecer downloads concluídos.

### `csv_progress` — retomada do CSV

| csv_path | signature | next_row | complete | total_rows |
|---|---|---:|---:|---:|
| `MinhaPlaylist.csv` | `1840:1731870000000000000` | 12 | 0 | 40 |

O caminho do CSV é relativo à pasta indicada por `-c/--csvfile`. `next_row` indica a próxima linha a processar; `complete` é `1` quando o CSV terminou. A assinatura ajuda o programa a perceber se o CSV foi alterado.

### `playlist_items` — faixas associadas a cada CSV

| csv_path | row_index | video_key | title | artist | duration | path |
|---|---:|---|---|---|---:|---|
| `MinhaPlaylist.csv` | 0 | `youtube:abc123` | `Canção Exemplo` | `Artista A, Artista B` | 215.4 | `../songs/Artista A/musica1.mp3` |

A combinação de `csv_path` e `row_index` identifica uma linha da playlist. O mesmo `video_key` pode aparecer em diferentes CSVs sem gerar outro download. `path` é relativo à pasta do banco de downloads.

### `migration_log` — controle da migração antiga

Guarda o caminho relativo do antigo `pltube_state.sqlite3` e a data em que foi migrado. A migração automática é idempotente e não apaga o banco antigo.

## Banco `musicas.sqlite3`

### `music_tracks` — cadastro consultável por faixa

| video_key | title | artists_json | album | isrc | metadata_status | provenance_json | diagnostics_json |
|---|---|---|---|---|---|---|---|
| `youtube:abc123` | `Canção Exemplo` | `["Artista A","Artista B"]` | `Álbum Exemplo` | `USAAA9900001` | `RESOLVIDO` | `{"title":"musicbrainz","artists":"musicbrainz","album":"musicbrainz"}` | `{"youtube":["yt-dlp/YouTube não forneceu um ISRC válido"],"musicbrainz":[]}` |

A linha é criada quando os metadados da faixa são preparados, antes de o download do áudio terminar. Campos importantes ficam em colunas pesquisáveis, e os objetos JSON conservam as informações mais detalhadas. A proveniência informa de onde veio cada campo, por exemplo `youtube`, `local`, `musicbrainz`, `youtube_validado_musicbrainz` ou `manual`.

### `metadata_resolution` — resultado completo da identificação

| video_key | video_id | status | result_json | audio_path | updated_at |
|---|---|---|---|---|---|
| `youtube:abc123` | `abc123` | `PENDENTE` | JSON da resolução | `../songs/Artista A/musica1.mp3` | `2026-11-18 18:47:00` |

`result_json` preserva os dados de origem, metadados escolhidos, a fonte de cada campo, os sinais de erro e a sugestão do YouTube Music. `audio_path` é relativo à pasta do banco de downloads, não é um caminho absoluto do computador.

Exemplo abreviado do JSON:

```json
{
  "status": "OBSERVAR",
  "metadata": {
    "title": "Canção Exemplo",
    "artists": ["Artista A", "Artista B"],
    "album": "Álbum Exemplo",
    "isrc": "USAAA9900001"
  },
  "source_youtube": {
    "title": "Canção Exemplo (Official Video)",
    "artists": ["Artista A"],
    "isrc": "",
    "uploader": "Canal Oficial"
  },
  "source_local": {
    "title": "Canção Exemplo",
    "artists": ["Artista A"],
    "album": "",
    "isrc": "USAAA9900001"
  },
  "provenance": {
    "title": "youtube_music_busca",
    "artists": "youtube_music_busca",
    "album": "youtube_music_busca"
  },
  "observations": ["título com sobras do vídeo: 'Canção Exemplo (Lyrics)'"],
  "pending_reasons": []
}
```

As fontes possíveis são `youtube_music` (faixa oficial), `youtube_music_descricao`, `youtube_music_clipe`, `youtube_creditos`, `youtube_music_busca`, `titulo`, `canal`, `local`, `banco_isrc` e `manual` (correção sua). Tudo fica só no SQLite local. O programa não escreve alterações de volta no YouTube.

## Consultas de exemplo

Com o comando `sqlite3` instalado:

```bash
sqlite3 -header -column csvs/sql/downloads.sqlite3 ".tables"
sqlite3 -header -column csvs/sql/downloads.sqlite3 \
  "SELECT video_key, status, attempts, audio_path, error FROM download_jobs;"
sqlite3 -header -column csvs/sql/musicas.sqlite3 \
  "SELECT video_key, title, artists_json, album, isrc, metadata_status FROM music_tracks;"
```

Para examinar o JSON completo de uma faixa com Python:

```python
import json
import sqlite3

con = sqlite3.connect("csvs/sql/musicas.sqlite3")
row = con.execute(
    "SELECT result_json FROM metadata_resolution WHERE video_key = ?",
    ("youtube:abc123",),
).fetchone()
if row:
    print(json.dumps(json.loads(row[0]), ensure_ascii=False, indent=2))
con.close()
```

Mover `csvs/`, `songs/` e `sql/` juntos, mantendo a mesma estrutura relativa, preserva os caminhos. Mover só o banco ou só a biblioteca exige ajustar a estrutura ou reconfigurar os caminhos correspondentes.

Apagar os bancos remove cache, progresso e metadados locais, mas não apaga CSVs nem MP3s. O antigo `pltube_state.sqlite3` é mantido após migração para que haja uma cópia de segurança.

## Conferência e memória (em `musicas.sqlite3`)

- `music_tracks.metadata_status`: `RESOLVIDO`, `OBSERVAR` (sinal de erro para conferir) ou `PENDENTE` (falta o artista).
- `music_tracks.review_note`: o sinal de erro, por exemplo "artista parece canal de re-upload, selo ou trilha: 'Lyrics Hub'".
- `artistas.sqlite3` (anexado como `artistas`): `artistas` (nome certo) e `variantes` (outras grafias), usados pelo comando `artists`.
- `channel_votes`: o que o programa aprendeu sobre cada canal. É um voto por vídeo confirmado: `artista` (o canal é do artista) ou `curador` (re-upload/gravadora/lyrics). A maioria decide.

```bash
sqlite3 sql/musicas.sqlite3 \
  "SELECT title, artists_json, review_note FROM music_tracks WHERE metadata_status = 'OBSERVAR';"
sqlite3 sql/musicas.sqlite3 \
  "SELECT channel_name, kind, COUNT(*) FROM channel_votes GROUP BY channel_key, kind;"
```

Sem o programa `sqlite3`, use `python plpython.py review --sql "SELECT ..."`.

