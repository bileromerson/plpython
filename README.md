# plpython

Baixa músicas de playlists do YouTube em MP3, procura o artista no banco de artistas e no YouTube Music, organiza tudo em pastas por artista e gera playlists M3U. O que não der para confirmar fica `NAO_CONFIRMADO` no SQL para você conferir.

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
python plpython.py review --answer                  # corrige as NAO_CONFIRMADO uma por uma
python plpython.py update -c csvs                   # depois: tenta de novo falhas e pendências
```

O comando vem sempre primeiro, depois as opções dele. Sem um comando, nada é executado.

### Atalhos dos parâmetros mais usados

| Atalho | Parâmetro | Comandos |
|---|---|---|
| `-c` | `--csvfile` | download, update, playlist, sync |
| `-s` / `-p` | `--songs-dir` / `--playlist-dir` | download, update, review, artists, sync |
| `-P` | `--progress` | download, update |
| `-v` / `-vv` / `-vvv` | `--verbose` | todos |
| `-w` | `--wait-on-limit` | download, update |
| `-r` | `--retry` | download |
| `-b` / `-k` | `--cookies-from-browser` / `--cookies` | download, update, playlist |
| `-a` | `--answer` (review) / `--append` (download, update) | |
| `-y` | `--default-ok` (review) / `--apply` (artists fix, sync) | |
| `-A` | `--all-fields` | review |
| `-x` | `--skip` | review, download, update (com `--ask`) |
| `-i` | `--ask` | download, update |
| `-q` / `-d` / `-e` | `--search` / `--details` / `--export` | review |
| `-n` | `--name` | playlist |
| `-o` / `-L` | `--output` / `--liked` | spotify |
| `-m` | `--missing` | sync |
| `-u` / `-l` | `--url-column` / `--log-file` | download, update |

Atalhos de uma letra podem ser juntados: `-Pvv` é o mesmo que `-P -v -v`, e `review -ay` é `--answer --default-ok`.

```bash
python plpython.py download -c csvs -Pw -b firefox     # progresso, espera bloqueio, conta do Google
python plpython.py update -c csvs -P                  # atualiza com barra de progresso
python plpython.py review -ay                          # responde, aceitando o que ficar em branco
```

## Pastas

Os arquivos são criados **na pasta de onde você roda o comando**, não na pasta do programa:

```
pasta onde você rodou/
├── csvs/                    ← só lida (é o que -c/--csvfile indica)
├── songs/<artista>/…mp3     ← MP3 no artista principal; atalhos nos secundários (-s muda o nome)
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
| `update` | relê os CSVs, refaz falhas, identifica de novo as `NAO_CONFIRMADO` e aplica o que você confirmou no SQL, sem baixar de novo o que já existe |
| `review` | mostra e confirma no terminal as faixas guardadas no SQL; `--answer` corrige uma por uma |
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
python plpython.py download -c csvs --wait-on-limit --sleep 10-20
python plpython.py download -c meu.csv -u -1              # URL na última coluna do CSV
python plpython.py update -c csvs -v
python plpython.py download -c csvs --ask                 # no fim, pergunta o artista das NAO_CONFIRMADO
python plpython.py download -c csvs -i -x album           # idem, sem perguntar o álbum

# conferência
python plpython.py review -d
python plpython.py review --answer
python plpython.py review --ok UG1HUOyL9IA
python plpython.py review --export conferir.csv

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

A música é **sempre baixada**. O programa só decide quem é o artista, nesta ordem (para no primeiro que responder):

1. **Código do vídeo:** se o YouTube/YouTube Music já diz quem é o artista, vale isso: descrição **"Provided to YouTube by …"**, créditos **"Música neste vídeo"**, faixa de áudio oficial (canal "- Topic") ou clipe oficial. Dessas fontes vêm também álbum, ano, número da faixa e capa quadrada.
2. **Pastas e banco de artistas (sem servidor):** o programa separa os **nomes possíveis** (os dois lados de `Artista - Música`, o contrário `Música - Artista`, um artista conhecido dentro do título e o canal) e compara com as **pastas que já existem em `songs/`** e com o **banco de artistas**. Os dois são lidos **uma vez** no começo e ficam na memória, então nada é consultado no servidor nem no SQL a cada música. Bateu → `CONFIRMADO`, e o MP3 vai para a pasta desse artista. Basta um dos artistas bater (`Frizk & snuffles` com a pasta `snuffles` confirma os dois). Também confirma quando **o canal é um dos lados do título** (`i don't wanna - snuffles` publicado pelo canal snuffles): é o artista publicando a própria música. Canal de re-upload ("Lyrics", "Records"…) não vale.
3. **Música + artista no YouTube Music:** busca o título do vídeo (e, se o canal não está no título, `canal + título`). Só vale o resultado em que **o nome da música e o artista batem com o mesmo palpite**. Assim um nome de música nunca vira artista, mesmo que exista um artista com esse nome.
4. **YouTube Music × YouTube:** sem artista que bata, compara os resultados com o vídeo: **mesmo nome de música, mesma versão** (slowed, remix, ao vivo…) **e mesma duração** (até 2 s de diferença).
5. **Nada disso → `NAO_CONFIRMADO`:** o MP3 é baixado do mesmo jeito, na pasta do palpite mais provável (`Artista - Música` → Artista; se o lado direito tem vários artistas, como `Música - A & B`, ele; sem nada, o canal). O artista **não** entra no banco de artistas até você confirmar.

**Pastas que não contam como artista** (para não repetir a confusão antiga de título no lugar do nome):
- pasta com o nome de uma música que está dentro dela, como `songs/Believer/Believer [id].mp3`;
- pasta que só tem faixas `NAO_CONFIRMADO` (foi criada por um palpite que ninguém conferiu). Isso sai de **uma** consulta ao SQL no começo da execução.

Com `-v` aparecem quantas pastas contam e quais foram ignoradas. Se o YouTube Music não responder (biblioteca `ytmusicapi` faltando ou desatualizada, sem rede), aparece um aviso na primeira falha: sem ele, a consulta do vídeo no YouTube Music e os passos 3 e 4 não funcionam, e quase tudo fica `NAO_CONFIRMADO`. Se `A - B` tem os dois lados conhecidos, fica o lado esquerdo como artista.

| Título do vídeo (canal) | Resultado |
|---|---|
| `Believer` (vídeo oficial) | `CONFIRMADO` Imagine Dragons — Believer, álbum Evolve (código do vídeo) |
| `snuffles - Keep on With It` (Chill Nation), já existe `songs/snuffles/` | `CONFIRMADO` snuffles — Keep on With It (pasta, sem consulta) |
| `Believer - Imagine Dragons (Lyrics)` (Lyrics Hub) | `CONFIRMADO` Imagine Dragons — Believer (música + artista no YouTube Music) |
| `Thunderstruck` (Rock Hits Records), 4:52 | `CONFIRMADO` AC/DC — Thunderstruck (mesmo título e duração no YouTube Music) |
| `Fulano - Música Legal` (Canal Qualquer) | `NAO_CONFIRMADO` Fulano — Música Legal (palpite: título) |

Canal de re-upload/curadoria ("Lyrics", "Records", "Music"…) só vale como artista se já estiver no banco de artistas. O título é limpo ("(Official Video)", "[Lyrics]", emojis, "#hashtags"); "ft./feat./(with …)" viram artistas; versões ("slowed", "sped up", "remix"…) ficam no título.

**Vários artistas.** **Vírgula, ` & `, ` / `, ` x `, `|`, `;` e `feat.`** separam (o `&`, o `/` e o `x` só com espaço dos dois lados: `AC/DC` e `Simon&Garfunkel` ficam juntos). Nomes do banco de artistas e uma lista embutida (Earth, Wind & Fire, Tyler, The Creator…) ficam inteiros. Para uma dupla nova não ser separada: `plpython.py artists add "Fulano & Ciclano"`.

**Pastas.** O MP3 fica na pasta do **artista principal**, e cada artista secundário recebe um **atalho** para o mesmo arquivo (`--no-artist-links` desliga os atalhos).

### Conferir no SQL

Abra `sql/musicas.sqlite3` (no DB Browser for SQLite, por exemplo, ou com `sqlite3`):

```sql
-- o que falta conferir
SELECT video_id, artists_json, title, review_note FROM music_tracks WHERE metadata_status = 'NAO_CONFIRMADO';

-- está certo: confirme
UPDATE music_tracks SET metadata_status = 'CONFIRMADO' WHERE video_id = 'UG1HUOyL9IA';

-- está errado: corrija e confirme
UPDATE music_tracks SET artists_json = '["Artista Certo"]', title = 'Música', metadata_status = 'CONFIRMADO'
 WHERE video_id = 'UG1HUOyL9IA';
```

Na próxima execução (`download` ou `update`), o que você confirmou vale: os artistas entram no banco de artistas, e o `update -c csvs` move o MP3 para a pasta certa e regrava as tags. Também dá para conferir pelo terminal com `plpython.py review` (veja abaixo).

Os status antigos (`RESOLVIDO`, `OBSERVAR`, `PENDENTE`) são convertidos sozinhos na primeira execução: `RESOLVIDO` → `CONFIRMADO`; os outros → `NAO_CONFIRMADO`.

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
python plpython.py review                       # faixas NAO_CONFIRMADO com o motivo
python plpython.py review -d                    # + álbum, ISRC, arquivo e link do YouTube
python plpython.py review --status all --search snuffles
python plpython.py review --ok UG1HUOyL9IA      # está certo: marca como conferida
python plpython.py review --ok-all              # confirma todas as NAO_CONFIRMADO
python plpython.py review --export conferir.csv # abre no Excel/LibreOffice
python plpython.py review --sql "SELECT title, album FROM music_tracks"   # SQL somente leitura
```

Rode na mesma pasta onde rodou o download, porque ele lê `./sql/`. Para corrigir título ou artista, use `review --answer`.

Ou direto no SQL: veja [Conferir no SQL](#conferir-no-sql).

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
| `-v` | erros que normalmente só iam para o `Logs.txt`; consultas ao SQL (encontrado/novo, canal já conhecido, o que foi aprendido); leitura e download no YouTube (inclusive quando reaproveita os metadados); pausas; **de onde veio o artista** (código do vídeo, pasta, banco de artistas, busca no YouTube Music, título/canal); capa; atalhos criados |
| `-vv` | tudo do `-v` e mais: tempo decorrido; os nomes possíveis do artista; os metadados lidos do YouTube campo a campo; cada tag gravada no MP3; avisos do yt-dlp |
| `-vvv` | tudo do `-vv` e mais a depuração do próprio yt-dlp |

No terminal, a saída é colorida. Cada área tem uma cor, "bate" e `CONFIRMADO` aparecem em verde, "não bate" e `NAO_CONFIRMADO` em amarelo, e erros em vermelho. Se a saída for redirecionada para um arquivo, ela sai sem cor. `NO_COLOR=1` desliga as cores.

Exemplo de `-v`:

```
[Identificação] CONFIRMADO: Imagine Dragons — Believer | álbum Evolve (faixa oficial no YouTube Music)
[Capa] baixando a capa do álbum: https://lh3.googleusercontent.com/…=w544-h544-l90-rj
[Identificação] 'snuffles' já existe (pasta de artista em songs/)
[Identificação] CONFIRMADO: snuffles — Keep on With It (pasta de artista em songs/)
[Identificação] NAO_CONFIRMADO: Chill Vibes Records — Lofi beats to study (nome do canal)
```

## Estrutura do código

```
plpython.py              ← único ponto de entrada: mostra o menu e encaminha cada comando
comandos/                ← um arquivo por comando: opções, ajuda e exemplos
├── playlist.py  download.py  update.py  review.py  artists.py  spotify.py  sync.py
└── _comum.py            ← opções compartilhadas (pastas, bancos, conta, -v)
pltube.py                ← motor do download: YouTube, pastas, M3U, SQL, tags e capa
identificar_musica.py    ← identificação: código do vídeo, pastas/banco de artistas, busca no YouTube Music
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
