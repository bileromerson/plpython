# Exemplo dos dois bancos SQLite do pltube

O downloader mantém dois arquivos dentro de `<BaseDir>/sql/` por padrão:

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

O caminho do CSV é relativo à pasta definida por `--BaseDir`. `next_row` indica a próxima linha a processar; `complete` é `1` quando o CSV terminou. A assinatura ajuda o programa a perceber se o CSV foi alterado.

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

### `metadata_resolution` — resultado completo do modo híbrido

| video_key | video_id | status | result_json | audio_path | updated_at |
|---|---|---|---|---|---|
| `youtube:abc123` | `abc123` | `PENDENTE` | JSON da resolução | `../songs/Artista A/musica1.mp3` | `2026-11-18 18:47:00` |

`result_json` preserva os dados de origem, metadados escolhidos, campos pendentes, candidatos MusicBrainz e diagnósticos separados por fonte. `audio_path` é relativo à pasta do banco de downloads, não é um caminho absoluto do computador.

Exemplo abreviado do JSON:

```json
{
  "status": "PENDENTE",
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
    "title": "musicbrainz",
    "artists": "musicbrainz",
    "album": "musicbrainz"
  },
  "diagnostics_by_source": {
    "youtube": ["yt-dlp/YouTube não forneceu um ISRC válido"],
    "local": ["ISRC recuperado das tags locais do MP3"],
    "musicbrainz": ["MusicBrainz não encontrou uma correspondência"]
  },
  "pending_reasons": ["álbum associado a mais de uma edição possível"]
}
```

Os avisos do yt-dlp/YouTube, das tags locais e do MusicBrainz são registros locais no SQLite e, para faixas pendentes, no CSV de revisão. O programa não escreve alterações de volta no YouTube.

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
