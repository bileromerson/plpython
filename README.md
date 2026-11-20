# plpython

Baixa músicas de playlists do YouTube em MP3, identifica artista, álbum e capa pelo YouTube Music, organiza tudo em pastas por artista e gera playlists M3U. Só vai para a conferência o que tiver sinal forte de erro.

## Instalação

- Python 3.10 ou superior
- FFmpeg instalado no sistema
- `pip install -r requirements.txt` (yt-dlp, mutagen e ytmusicapi)

O YouTube muda com frequência; mantenha o yt-dlp atualizado com `pip install -U yt-dlp`.

## Começo rápido

```bash
python plpython.py                                  # mostra os comandos
python plpython.py help download                    # todas as opções de um comando

python plpython.py download -c csvs -P              # baixa todos os CSVs da pasta csvs/, com barra de progresso
python plpython.py review --answer                  # corrige o que teve sinal de erro
python plpython.py update -c csvs                   # depois: tenta de novo falhas e pendências
python plpython.py check -y                         # procura erros nas faixas que já estão no banco
```

O comando vem sempre primeiro, depois as opções dele. Sem um comando, nada é executado.

### Atalhos dos parâmetros mais usados

| Atalho | Parâmetro | Comandos |
|---|---|---|
| `-c` | `--csvfile` | download, update, playlist, sync |
| `-s` / `-p` | `--songs-dir` / `--playlist-dir` | download, update, review, check, artists, sync |
| `-P` | `--progress` | download, update |
| `-v` / `-vv` / `-vvv` | `--verbose` | todos |
| `-w` | `--wait-on-limit` | download, update |
| `-r` | `--retry` | download |
| `-nr` | `--no-review` | download, update |
| `-nh` | `--no-hybrid` | download, update |
| `-b` / `-k` | `--cookies-from-browser` / `--cookies` | download, update, playlist |
| `-a` | `--answer` (review) / `--append` (download, update) | |
| `-y` | `--default-ok` (review) / `--apply` (check, artists fix, sync) | |
| `-A` | `--all-fields` | review |
| `-x` | `--skip` | review, download, update (com `--ask`) |
| `-i` | `--ask` | download, update |
| `-q` / `-d` / `-e` | `--search` / `--details` / `--export` | review (`-q` também no check) |
| `-n` | `--name` | playlist |
| `-o` / `-L` | `--output` / `--liked` | spotify |
| `-m` | `--missing` | sync |
| `-u` / `-l` | `--url-column` / `--log-file` | download, update |

Atalhos de uma letra podem ser juntados: `-Pvv` é o mesmo que `-P -v -v`, e `review -ay` é `--answer --default-ok`.

```bash
python plpython.py download -c csvs -Pw -b firefox     # progresso, espera bloqueio, conta do Google
python plpython.py update -c csvs -P -nr               # atualiza sem lista de conferência
python plpython.py review -ay                          # responde, aceitando o que ficar em branco
python plpython.py check -y -q snuffles                # procura erros só nas faixas do snuffles
```

## Pastas

Os arquivos são criados **na pasta de onde você roda o comando**, não na pasta do programa:

```
pasta onde você rodou/
├── csvs/                    ← só lida (é o que -c/--csvfile indica)
├── songs/<artista>/…mp3     ← MP3 no artista principal; atalhos nos secundários (-s muda o nome)
├── songs/_observar/         ← atalhos das faixas para conferir
├── playlists/*.m3u          ← uma por CSV ou por playlist do Spotify (-p muda)
├── sql/                     ← downloads.sqlite3 (progresso), musicas.sqlite3 (metadados),
│                              artistas.sqlite3 (nomes e variações) e m3u_backup/
├── spotify.csv              ← suas playlists do Spotify (comando spotify)
├── nao_baixadas.csv         ← o que o sync achou que falta baixar
└── Logs.txt                 ← erros
```

## Comandos

| Comando | O que faz |
|---|---|
| `playlist` | cria/atualiza CSVs a partir de playlists do YouTube (URL, suas playlists, curtidas, assistir mais tarde) |
| `download` | baixa os CSVs, identifica as músicas e gera as M3Us; retoma de onde parou |
| `update` | relê os CSVs, refaz falhas e reavalia `PENDENTE`/`OBSERVAR` sem baixar de novo o que já existe |
| `review` | mostra e confirma no terminal as faixas guardadas no SQL; `--answer` corrige uma por uma |
| `check` | procura no banco as faixas com sinal forte de erro e tira da conferência as que não têm (simula sem `-y`) |
| `spotify` | lê suas playlists do Spotify e salva as músicas em `spotify.csv` (não baixa nada) |
| `sync` | compara o `spotify.csv` com o que já foi baixado, acerta as M3Us e lista o que falta (simula sem `-y`) |
| `artists` | banco de artistas: nome certo, variações e correção das músicas com o nome errado (simula sem `-y`) |

```bash
# playlist
python plpython.py playlist "https://www.youtube.com/playlist?list=PL..." --name Rock
python plpython.py playlist mine --cookies-from-browser firefox

# download / update
python plpython.py download -c csvs                       # todos os CSVs da pasta
python plpython.py download -c csvs/Alone.csv             # um CSV
python plpython.py download a.csv b.csv --append          # vários, acrescentando às M3Us
python plpython.py download -c csvs -s Musicas -p m3u     # outras pastas de saída
python plpython.py download -c csvs --review all          # confere também o que não está no YouTube Music
python plpython.py download -c csvs --no-hybrid           # rápido: só o título e o canal do vídeo
python plpython.py download -c csvs --wait-on-limit --sleep 10-20
python plpython.py download -c meu.csv -u -1              # URL na última coluna do CSV
python plpython.py update -c csvs -v
python plpython.py download -c csvs --ask                 # no fim, pergunta o que teve sinal de erro
python plpython.py download -c csvs -i -x album           # idem, sem perguntar o álbum

# conferência
python plpython.py review -d
python plpython.py review --answer
python plpython.py review --ok UG1HUOyL9IA
python plpython.py review --export conferir.csv
python plpython.py check                                  # sinais de erro nas faixas já no banco
python plpython.py check -y                               # grava: com sinal → conferir; sem sinal → sai

# Spotify
python plpython.py spotify -L                             # suas playlists + curtidas → spotify.csv
python plpython.py sync -y                                # acerta as M3Us e salva nao_baixadas.csv

# artistas
python plpython.py artists                                # lista artistas e variações
python plpython.py artists alias "AC/DC" "ACDC"           # ACDC passa a ser AC/DC
python plpython.py artists fix -y                         # corrige as músicas com o nome errado
```

O `spotify` precisa de um app seu no Spotify; veja [Spotify → M3U](#spotify--m3u).

## Spotify → M3U

São dois passos, e nada é baixado. O `spotify` lê as suas playlists e salva tudo em `spotify.csv`: nome da playlist, código da música no Spotify, ISRC, artistas, título e álbum. O `sync` compara esse arquivo com as músicas já baixadas, acerta as M3Us e lista o que falta baixar.

```bash
python plpython.py spotify --client-id SEU_CLIENT_ID   # 1ª vez: abre o navegador para entrar
python plpython.py spotify                             # depois, basta isto
python plpython.py spotify -L                          # inclui as Músicas Curtidas
python plpython.py sync                                # mostra o que mudaria nas M3Us
python plpython.py sync -y                             # grava
```

**Criar o app no Spotify (uma vez):**

1. Entre em https://developer.spotify.com/dashboard com a sua conta e clique em **Create app**.
2. Em **Redirect URIs**, coloque `http://127.0.0.1:8888/callback` e marque **Web API**.
3. Copie o **Client ID** e rode `python plpython.py spotify --client-id SEU_CLIENT_ID`. O Client Secret não é usado: o login é feito no navegador, com PKCE.

O login fica salvo em `~/.cache/pltube/spotify_login.json` e é renovado sozinho. Para trocar de conta, use `--login`.

**Regras do Spotify desde 2026** para apps de uso pessoal (modo de desenvolvimento):

- O dono do app precisa ter **Premium**. Outra conta só entra se for cadastrada em *User Management*, nas configurações do app.
- As faixas só vêm das playlists que **você criou ou em que colabora**. As que você só segue ficam de fora; para incluir uma, copie as músicas dela para uma playlist sua.
- `localhost` não é aceito como endereço de retorno; use `127.0.0.1`.

**O que o `spotify` mostra:** o que entrou (+) e o que saiu (−) de cada playlist desde a última leitura. As playlists que não mudaram (mesmo `snapshot_id`) são copiadas da leitura anterior, sem pedir as faixas de novo. Com nomes de playlists (`spotify Rock "Pop 2024"`), lê só essas, lista as músicas delas e mantém as outras como estavam no CSV.

**O que o `sync` faz em cada playlist:**

- As músicas já baixadas entram na M3U (`playlists/<nome>.m3u`), na ordem do Spotify. Se já existe uma M3U com o mesmo nome (sem contar acentos, maiúsculas e emojis), ela é usada; senão, uma nova é criada.
- O que está na M3U mas saiu da playlist do Spotify sai da M3U (`--keep-extra` mantém). Nenhum MP3 é apagado.
- A música é reconhecida pelo ISRC. Sem ISRC, pelo título com artista compatível, ignorando o que não muda a música ("- Remastered 2011", "(feat. …)", "(Radio Edit)"). Se ainda não achar, por um título quase igual do mesmo artista e com duração parecida. Remix, ao vivo, acústico e "Taylor's Version" contam como outra música.
- `?` **conferir**: a música não casou com certeza, mas parece ser uma que já estava na M3U. Ela fica na M3U e aparece para você conferir; corrigindo os dados dela com o `review`, ela passa a casar.
- As que não estão baixadas vão para `nao_baixadas.csv`, uma linha por música, com as playlists em que aparece, o link do Spotify, um link de busca no YouTube Music e uma dica quando você tem outra versão ou outra música com o mesmo título.
- Sem `-y`, só mostra. Com `-y`, grava e guarda as M3Us antigas em `sql/m3u_backup/`.

O `download`, o `update` e o `review` regravam as M3Us a partir dos CSVs do YouTube. Por isso, rode o `sync` depois deles.

## Como a música é identificada

A ideia é **confiar em quem publica a música** e só desconfiar quando houver um sinal forte de erro, em vez de tentar confirmar cada artista em vários lugares. O YouTube Music é consultado pela biblioteca `ytmusicapi` (sem login); sem ela instalada, ficam os dados do próprio vídeo. Para não identificar nada (só o título e o canal), use `--no-hybrid`.

**1. De onde vêm o artista e o título** (para na primeira fonte que responder):

| Ordem | Fonte | Exemplo |
|---|---|---|
| 0 | o mesmo **ISRC** de uma faixa já confirmada no seu banco: é a mesma gravação | — |
| 1 | o **próprio vídeo no YouTube Music**: faixa de áudio oficial (canal "- Topic") ou clipe oficial; do clipe, usa a faixa de áudio que o YouTube Music liga a ele | `Believer` → Imagine Dragons, álbum Evolve, faixa 3/11, capa |
| 2 | a descrição **"Provided to YouTube by …"** | título, artistas, álbum, data, distribuidora |
| 3 | os **créditos de música do YouTube** ("Música neste vídeo"), que valem até para re-uploads | `Heat Waves (slowed + reverb)` → Glass Animals |
| 4 | uma **busca de músicas no YouTube Music** com o título do vídeo | `Believer - Imagine Dragons (Lyrics)` num canal de letras → Imagine Dragons — Believer, com álbum e capa |
| 5 | o **título e o canal** do vídeo, como nas primeiras versões | `too late - snuffles` → snuffles — too late |

Na busca (4), um resultado só vale se o nome da música está no título do vídeo, a **versão bate** ("Heat Waves" não serve para um vídeo "slowed + reverb"; remix, ao vivo, instrumental, nightcore e sped up também contam) e o artista aparece no título ou no canal. Se o vídeo só tem o nome da música ("Thunderstruck"), vale o resultado com a **mesma duração** (diferença de até 3 s).

Com as fontes 1 a 4 vêm também **álbum, ano, número da faixa e capa quadrada** (544×544). Sem capa de álbum, a thumbnail do vídeo é recortada para ficar quadrada. **Álbum ou capa que faltam não mandam a faixa para a conferência.**

**2. Lendo o título do vídeo** (fonte 5). O título é limpo: saem "(Official Video)", "[Lyrics]", "[Monstercat Release]", "| Lyric Video", emojis, "#hashtags" do fim e "02. " do começo; "ft./feat./(with …)" viram artistas; versões ("slowed", "sped up", "remix"…) ficam no título final. Depois:

- `"Música" by Artista` → a música é o que está entre aspas;
- o título é lido em **blocos** separados por `|` ou `//`; o bloco principal é o primeiro que não é trilha/obra ("OST", "Soundtrack", "série"…) nem o próprio canal;
- dentro do bloco, cada pedaço (`-`, `–`, `:`, `·`, aspas) é comparado com o **canal** e com os artistas que você já tem (banco de músicas e banco de artistas): o pedaço que bate é o artista, e a música é o pedaço mais próximo;
- `Álbum - 02. Música` num canal → o artista é o canal e o álbum é a primeira parte;
- sem nada disso, vale `Artista - Música`; com um pedaço só, o artista é o canal.

| Título | Resultado |
|---|---|
| Liberdade Provisória - Henrique e Juliano - com cifras | Henrique & Juliano — Liberdade Provisória (banco) |
| ACDC Thunderstruck ( official video ) | AC/DC — Thunderstruck (banco) |
| Cult of the Lamb Song - "Fool's Gold" by ivycomb | ivycomb — Fool's Gold |
| Final Space Official Soundtrack \| When the Night is Long (feat. Shelby Merry) \| WaterTower | WaterTower, Shelby Merry — When the Night is Long |
| INSTINCT - 02. Instinct (canal NIIC) | NIIC — Instinct, álbum INSTINCT |
| My Ordinary Life-The Living Tombstone | The Living Tombstone — My Ordinary Life |

**Vários artistas.** A mesma regra vale no título, nos dados do YouTube e no que você digita no `review`: **vírgula, ` & `, ` / `, ` x `, `|`, `;` e `feat.`** separam (o `&`, o `/` e o `x` só com espaço dos dois lados: `AC/DC` e `Simon&Garfunkel` ficam juntos). Nomes **conhecidos** ficam inteiros, com a grafia oficial: os do banco de artistas e de faixas confirmadas, e uma lista embutida (Earth, Wind & Fire, Tyler, The Creator, Jorge & Mateus, Henrique & Juliano…). Para uma dupla nova não ser separada: `plpython.py artists add "Fulano & Ciclano"`. `"aspas"` também mantêm um nome junto. No YouTube Music, cada artista com página já vem separado.

**3. Sinais de erro** (só isso manda a faixa para a conferência, `OBSERVAR`). Os dados do catálogo do YouTube Music (gravadora/distribuidora) não são questionados; os deduzidos do título/canal são:

| Sinal | Exemplos reais |
|---|---|
| **artista com cara de pedaço do título**: parênteses, colchetes, `-`, `#`, `@`, número de faixa, "lyrics", "remix", "soundtrack", nome longo demais | `The Living Tombstone (FNAF2)`, `[Dubstep]`, `2. I MONSTER`, `Astronaut In The Ocean (Alok Remix)` |
| **artista que é canal de re-upload, selo ou trilha** | `Lyrics Hub`, `… OST`, canal já conhecido como re-upload |
| **título com sobras do vídeo**: "lyrics", "official video", "#hashtags", `[… Release]`, aspas, ` by `, ` - `, número de faixa | `02. Instinct`, `Dreams [Monstercat Release]`, `RET TRO - Happy (prod. ivycomb)` |
| **título contém o nome do artista** | `Glyde // Latte` com artista Glyde |
| **título e artista trocados**: o título é um artista que você tem em mais faixas que o "artista" desta | `Bad Habits — Ed Sheeran` |
| **artista incerto**: o título tem três partes ou mais, ou o canal aparece num bloco à parte (selo publicando `Música - Artista`) | `Wonka 2023 - Pure Imagination - Timothée Chalamet` |
| **vídeo longo** (mais de 15 min) fora do catálogo: compilação, álbum inteiro ou loop | `Lofi beats 24/7` |

Sem artista nenhum, a faixa fica `PENDENTE`. Correções suas (`review`) nunca são questionadas.

| Opção | O que vai para a conferência |
|---|---|
| *(padrão)* `--review errors` | só as faixas com sinal de erro |
| `--review all` | também as que não estão no YouTube Music (deduzidas do título/canal) |
| `--no-review` | nada (os sinais ficam anotados no banco) |
| `--no-hybrid` | não identifica: título e canal do vídeo, sem YouTube Music |

**4. Pastas.** O MP3 fica na pasta do **artista principal**, e cada artista secundário recebe um **atalho** para o mesmo arquivo, sem duplicar o áudio (`--no-artist-links` desliga os atalhos). Se um download antigo estiver na pasta errada, ele é movido para a pasta certa, sem sobrescrever nada.

**5. Memória.** Toda faixa confirmada por uma fonte confiável ou por você ensina se o canal é do artista ou um re-upload (pela maioria dos votos) e coloca o artista no banco de artistas. Artistas só deduzidos do título não entram, para um erro não virar "conhecido". No `update`, as faixas da lista são identificadas de novo pelo YouTube Music, com os dados do vídeo guardados no banco (sem baixar o áudio).

**Faixas que já estão no banco:** rode `plpython.py update -c csvs` (identifica de novo as da lista pelo YouTube Music) e depois `plpython.py check -y`, que passa os mesmos sinais de erro em todas as faixas: as que estavam na lista só por álbum/capa faltando saem, e as já resolvidas com sinal de erro entram.

## Banco de artistas: `artists`

`sql/artistas.sqlite3` guarda o **nome certo** de cada artista e as **variações** com que ele aparece por aí ("Henrique e Juliano", "ACDC", "ImagineDragons"). É um arquivo separado, então dá para copiá-lo para outra biblioteca (`--artists-db` escolhe outro).

- **Enche sozinho:** na primeira vez ele traz os artistas das faixas já confirmadas, escolhendo a grafia mais usada. Depois, o `download` e o `review` colocam aqui todo artista confirmado ou digitado por você.
- **Ajuda a identificar:** os nomes e as variações são procurados em cada pedaço do título do vídeo, mesmo para artistas que ainda não têm música no banco de músicas.
- **Corrige a grafia:** uma variação nunca fica no MP3. Ao identificar, ela vira o nome certo, e o `artists fix -y` faz o mesmo nas músicas já baixadas (tags, banco, pasta e M3U).

Nomes que só mudam em maiúscula, espaço ou pontuação ("WHSPRS"/"Whsprs", "TheFatRat"/"The Fat Rat") já são o mesmo artista. Para escolher a grafia, use `rename`.

```bash
python plpython.py artists                                   # lista: nome, faixas e variações
python plpython.py artists list henrique                     # procura
python plpython.py artists add "Henrique & Juliano" -a "Henrique e Juliano"
python plpython.py artists alias "AC/DC" "ACDC" "AC-DC"      # variações (junta dois artistas, se for o caso)
python plpython.py artists rename "WHSPRS" "Whsprs"          # troca o nome certo
python plpython.py artists remove "AC-DC"                    # tira a variação (ou o artista)
python plpython.py artists similar                           # nomes parecidos que podem ser o mesmo
python plpython.py artists fix                               # o que mudaria nas músicas
python plpython.py artists fix -y                            # corrige
```

## Conferir e corrigir

### Perguntar no fim do download: `--ask`

Com `-i` / `--ask`, o `download` e o `update` guardam as faixas desta execução que não foram confirmadas: o artista não estava no banco de músicas nem no de artistas, e a pontuação do YouTube não bastou. No fim, ele faz a mesma tela do `review --answer`, só com essas faixas e só com os campos que faltam. O que você responde é gravado junto com o que ele já tinha achado (tags, banco, pasta, M3U), e o artista digitado entra no banco de artistas para as próximas. `-x album` não pergunta o álbum; `:s` pula a faixa, `:q` termina. Fora de um terminal ele não pergunta e só avisa para rodar `review --answer`.

### Responder uma por uma: `review --answer`

```bash
python plpython.py review --answer                 # só sai da lista o que ficar todo resolvido
python plpython.py review --answer --skip album    # não pergunta o álbum
python plpython.py review --answer --default-ok    # Enter aceita
python plpython.py review --apply                  # leva os artistas corrigidos às faixas parecidas
```

Cada faixa aparece numa tela limpa: a legenda fica sempre no topo e os dados da faixa logo abaixo. As cores ajudam a bater o olho: ⚠ amarelo é campo com problema, ✓ verde está certo, o valor do vídeo vem em ciano e a sugestão do YouTube Music em magenta.

```
━━ Revisão · faixa 2 de 12 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Enter = não mexe (o campo continua pendente)   texto = novo valor   :ok = este campo está certo
:db = valor do YouTube Music   :yt = valor do vídeo   :- = apagar (álbum/ISRC)   :inv = troca título e artista
:tudo = a faixa toda está certa   :s = pular a faixa   :q = salvar e sair
Vários artistas: A | B (vírgula e & também valem; "aspas" mantêm um nome junto)
A faixa só sai da lista quando todos os campos com ⚠ forem corrigidos ou marcados com :ok.
────────────────────────────────────────────────────────────────────────
✓ Queen — Bohemian Rhapsody: saiu da lista

[OBSERVAR]  https://youtu.be/aaaaaaaaaaa   songs/Lyrics Hub/Believer [aaaaaaaaaaa].mp3
O que está errado:
  • artista parece canal de re-upload, selo ou trilha: 'Lyrics Hub'

  Título    ✓ Believer
  Artistas  ⚠ Lyrics Hub    YT Music: Imagine Dragons

⚠ Artistas [Lyrics Hub]: :db
✓ Imagine Dragons — Believer (corrigido: Artistas): saiu da lista
```

| Resposta | Efeito |
|---|---|
| Enter | não mexe; o campo continua pendente (com `--default-ok`, aceita) |
| texto | novo valor; em artistas, o primeiro é o principal |
| `:ok` | este campo está certo como está (vale também para álbum vazio) |
| `:db` / `:yt` | usa a sugestão do YouTube Music / o valor do vídeo |
| `:-` | apaga (álbum ou ISRC) |
| `:inv` | troca título e artista ("Bella Ciao" no artista e "La Casa de Papel" no título) |
| `:tudo` | a faixa inteira está certa |
| `:s` / `:q` | pula o resto desta faixa / salva o que já respondeu e sai |

- **Só sai da lista o que ficou resolvido.** A faixa vira `RESOLVIDO` quando todos os campos com ⚠ foram corrigidos ou marcados com `:ok`. Se faltar algum, o que você respondeu é gravado e ela continua `OBSERVAR`; na próxima revisão, só os campos que faltam são perguntados.
- **Artistas digitados com vírgula, "&", " x " etc.** seguem a [regra de vários artistas](#como-a-música-é-identificada), e o `review` mostra como entendeu antes de gravar ("→ 2 artistas: Anitta | Kevinho"). Enter confirma; para mudar, digite de novo usando `|` para separar ou "aspas" para manter um nome junto.
- **Uma correção de artista vale para as faixas parecidas.** Ao corrigir o artista de uma faixa ("Canal X" → "Linkin Park"), a correção fica guardada, e nas outras faixas da lista com o mesmo artista errado ele não é mais perguntado (aparece como "→ Linkin Park, vai no --apply"). Depois rode `review --apply`, que passa por elas uma por uma:
  - só aplica se a própria faixa confirmar: o nome aparece no título do vídeo ou nos créditos, ou são os mesmos nomes com outra grafia/divisão ("TheFatRat & NEFFEX" → "TheFatRat | NEFFEX"). Um canal de re-upload publica vários artistas, então sem confirmação nada muda, e no próximo `review` a correção aparece como sugestão (`:sug`);
  - se o mesmo artista errado foi corrigido para nomes diferentes, nada é propagado;
  - aplicada a correção, a faixa é identificada de novo com o artista certo (o YouTube Music completa álbum e capa), e só sai da lista se não sobrar sinal de erro; senão continua `OBSERVAR`, só com o que falta.
- **Só os campos com problema (⚠) são perguntados.** Cada sinal aponta um campo: "artista parece…" → artistas; "título com sobras…"/"título contém…" → título; "invertidos", "artista incerto" e "vídeo longo" → título e artistas. Os demais aparecem com ✓. Use `--all-fields` para perguntar todos.
- **`--skip CAMPO`** (`-x`): não pergunta nem altera esse campo (`title`, `artists`, `album` ou `isrc`; vários: `--skip album isrc`). Ele continua pendente, então a faixa não sai da lista por causa dele, e as faixas que só têm problema nele nem aparecem. `--skip album` também ignora a capa.
- **Com `--default-ok`:** o que ficar em branco é aceito. Se o YouTube Music tiver uma sugestão para o campo, usa a dela. Se ninguém tiver, vira `Unknown`.
- **Cada resposta é gravada na hora**: tags do MP3 (com backup), banco e M3Us. Se o artista principal mudar, o MP3 vai para a pasta do novo artista, com atalhos para os secundários; quando a faixa sai da lista, sai também de `_observar/`. O `update` não desfaz correções manuais.
- Use `-s`/`-p` se as suas pastas de músicas/playlists não forem `./songs` e `./playlists`.

### Outras formas

No terminal, sem precisar de programa de SQL:

```bash
python plpython.py review                       # faixas OBSERVAR/PENDENTE com o motivo
python plpython.py review -d                    # + álbum, ISRC, arquivo e link do YouTube
python plpython.py review --status all --search snuffles
python plpython.py review --ok UG1HUOyL9IA      # está certo: marca como conferida
python plpython.py review --ok-all              # confirma todas as OBSERVAR
python plpython.py review --export conferir.csv # abre no Excel/LibreOffice
python plpython.py review --sql "SELECT title, album FROM music_tracks"   # SQL somente leitura
```

Rode na mesma pasta onde rodou o download, porque ele lê `./sql/`. Para corrigir título ou artista, use `review --answer`.

Outros lugares para conferir: a pasta `_observar/` com atalhos para os MP3s, ou direto no SQL:

```sql
-- sqlite3 sql/musicas.sqlite3
SELECT title, artists_json, review_note FROM music_tracks WHERE metadata_status = 'OBSERVAR';
```

`update` identifica de novo as faixas `PENDENTE` e `OBSERVAR` que você ainda não revisou, e `check -y` passa os sinais de erro nas que já estão resolvidas.

## Sua conta do Google: playlists privadas, curtidas e vídeos restritos

O YouTube não aceita mais login por senha nem OAuth no yt-dlp. O único jeito suportado é usar os **cookies** da sua conta já logada. O programa nunca vê nem pede sua senha.

```bash
# 1. Crie os CSVs a partir da conta (todas as suas playlists, curtidas ou assistir mais tarde)
python plpython.py playlist mine --cookies-from-browser firefox
python plpython.py playlist liked --cookies-from-browser firefox
python plpython.py playlist watch-later --cookies-from-browser firefox
python plpython.py playlist "https://www.youtube.com/playlist?list=PL..." --name Rock

# 2. Baixe usando a mesma conta (libera vídeos +18, privados compartilhados com você etc.)
python plpython.py download -c csvs --cookies-from-browser firefox
```

- **Navegador:** use o **Firefox**. No Windows, Chrome/Edge/Brave (versão 127 ou mais nova) usam uma criptografia que o yt-dlp não consegue ler, e dá o erro "Failed to decrypt with DPAPI". Faça login no YouTube pelo Firefox uma vez e use `--cookies-from-browser firefox`. Para escolher um perfil: `firefox:NomeDoPerfil`.
- **Alternativa:** um arquivo `cookies.txt`, com `--cookies cookies.txt`. A wiki do yt-dlp explica como exportar sem que ele expire: entre numa janela anônima, abra `youtube.com/robots.txt`, exporte e feche a janela. O arquivo dá acesso à sua conta; o `.gitignore` já impede que ele vá para o Git.
- **Para não repetir a opção:** defina a variável `PLPYTHON_COOKIES_FROM_BROWSER=firefox` (ou `PLPYTHON_COOKIES=caminho/cookies.txt`).
- **Atualizar uma playlist:** rodar `playlist` de novo atualiza o CSV. O `download` só baixa os vídeos novos. Vídeos apagados ou privados ficam fora do CSV (`--include-unavailable` mantém).
- **Risco:** segundo a wiki do yt-dlp, usar a conta com o yt-dlp pode fazer o YouTube **bloqueá-la temporária ou permanentemente**. Use só quando precisar, mantenha a pausa entre vídeos ou use uma conta secundária. Com conta, o limite sobe de cerca de 300 para cerca de 2000 vídeos por hora.

## Consultas ao YouTube e bloqueios

- **Uma leitura por música nova:** os metadados lidos para identificar a música são reaproveitados no download, do mesmo jeito que o `yt-dlp --load-info-json`. Se o link guardado expirar, o próprio yt-dlp volta a consultar pela URL. Músicas já baixadas não consultam o YouTube.
- **Pausa entre vídeos:** 5 a 10 s, sorteados, como recomenda a wiki do yt-dlp. O tempo gasto com o YouTube Music e com o download já conta, então só se espera o que faltar. Ajuste com `--sleep 10-20` ou desligue com `--sleep 0`. Dentro de cada consulta há 0,75 s entre requisições, como no preset `-t sleep` do yt-dlp.
- **Limites (wiki do yt-dlp):** cerca de 300 vídeos por hora sem conta, cerca de 2000 com conta.
- **Bloqueio da sessão** ("This content isn't available, try again later", HTTP 429, "confirm you're not a bot"): segundo o yt-dlp, dura **até 1 hora**. Sem `--wait-on-limit`, o programa para na hora e avisa para esperar cerca de 1 hora; o progresso fica salvo e basta rodar de novo. Com `--wait-on-limit`, ele espera 5, 15, 30 e 60 min (quase 2 h no total) antes de desistir.
- **Falha de rede** (502/503/504, timeout): com `--wait-on-limit`, novas tentativas em 30 s, 1, 2, 4, 8 e 10 min.

## Barra de progresso: `-P` / `--progress`

No `download` e no `update`, `-P` deixa uma barra fixa na **última linha** do terminal, como no `pip install`. As mensagens, inclusive as do `-v`, continuam rolando acima dela:

```
Consultando metadados: https://www.youtube.com/watch?v=bbbbbbbbbbb
Concluído: VGR - Fallen Down
[██████░░░░░░░░░░░░░░]  33% 1/3 · ~2m10s · Alone.csv: Won't Go - snuffles · baixando 45% de 4.8 MB
```

A barra mostra:
- a porcentagem do total (todas as linhas de todos os CSVs);
- o tempo restante estimado;
- o CSV e o vídeo atuais;
- a etapa: lendo metadados, pausa, identificando, baixando com a %, convertendo para MP3, gravando tags e capa, ou bloqueado até tal hora.

Ela é apagada no fim e fica só um resumo. Funciona no Windows; em terminais sem Unicode, a barra usa `#` e `-`. Se a saída for redirecionada para um arquivo, a barra fica desligada.

## Modo detalhado: `-v`, `-vv`, `-vvv` (como no nmap)

Funciona em todos os comandos. Sem `-v`, a saída é a de sempre.

| Nível | Mostra |
|---|---|
| `-v` | erros que normalmente só iam para o `Logs.txt`; consultas ao SQL (encontrado/novo, canal já conhecido, o que foi aprendido); leitura e download no YouTube (inclusive quando reaproveita os metadados); pausas; **de onde veio o artista** (YouTube Music, créditos, título/canal) e os **sinais de erro**; capa; atalhos criados |
| `-vv` | tudo do `-v` e mais: tempo decorrido; o que cada busca no YouTube Music devolveu; os metadados lidos do YouTube campo a campo; cada tag gravada no MP3; avisos do yt-dlp |
| `-vvv` | tudo do `-vv` e mais a depuração do próprio yt-dlp |

No terminal, a saída é colorida. Cada área tem uma cor, "bate" e `RESOLVIDO` aparecem em verde, "não bate" e `OBSERVAR` em amarelo, e erros e `PENDENTE` em vermelho. Se a saída for redirecionada para um arquivo, ela sai sem cor. `NO_COLOR=1` desliga as cores.

Exemplo de `-v`:

```
[Identificação] RESOLVIDO: Imagine Dragons — Believer | álbum Evolve (catálogo do YouTube Music)
[Capa] baixando a capa do álbum: https://lh3.googleusercontent.com/…=w544-h544-l90-rj
[SQL] aprendido: canal 'Lyrics Hub' é re-upload/curadoria
[Identificação] OBSERVAR: Chill Vibes Records — Lofi beats to study (nome do canal)
[Identificação]   conferir: artista parece canal de re-upload, selo ou trilha: 'Chill Vibes Records'
[Identificação]   conferir: vídeo longo (60 min): pode ser compilação, álbum inteiro ou loop
```

## Estrutura do código

```
plpython.py              ← único ponto de entrada: mostra o menu e encaminha cada comando
comandos/                ← um arquivo por comando: opções, ajuda e exemplos
├── playlist.py  download.py  update.py  review.py  check.py  artists.py  spotify.py  sync.py
└── _comum.py            ← opções compartilhadas (pastas, bancos, conta, -v)
pltube.py                ← motor do download: YouTube, pastas, M3U, SQL, tags e capa
identificar_musica.py    ← identificação pelo YouTube Music e os sinais de erro
textos.py                ← regras de nomes e títulos (vários artistas, pedaços do título)
banco_artistas.py        ← banco de artistas (nome certo e variações)
aplicar_metadados.py     ← grava uma correção: tags, banco, pasta e M3U
correcoes_artistas.py    ← correções de artista que valem para outras faixas (review --apply)
spotify_conta.py         ← login no Spotify e leitura das playlists → spotify.csv
sincronizar_playlists.py ← spotify.csv × biblioteca → M3Us e nao_baixadas.csv
progresso.py  verbose.py ← barra -P e modo -v
```

Só o `plpython.py` é executável; os outros arquivos são usados por ele. Para criar um comando novo, basta um arquivo em `comandos/` e acrescentar o nome em `comandos/__init__.py`.

Testes: `python -m unittest discover -s tests -v`.

A lista abaixo contém apenas itens ainda não disponíveis.

- [ ] **Catálogo global da biblioteca:** indexar todos os arquivos locais, seus caminhos, fontes, tags e relações com playlists; permitir filtros como `unknown` e `*` para localizar pendências ou revisar o catálogo completo.
- [ ] **Backup portátil de metadados:** criar e restaurar um catálogo SQLite com URLs, metadados e referências dos arquivos, sem exigir que os MP3s estejam conectados para consultar o catálogo. Esse backup não conterá o áudio.
- [ ] **Baixar o que falta do Spotify:** procurar no YouTube as músicas de `nao_baixadas.csv` e baixá-las sozinho. Hoje o `sync` lista as que faltam, com um link de busca para cada uma.
