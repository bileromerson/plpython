# plpython

Baixa músicas de playlists do YouTube em MP3, identifica artista, álbum e capa (YouTube + MusicBrainz), organiza tudo em pastas por artista e gera playlists M3U. O que não dá para confirmar vai para uma lista curta de conferência.

## Instalação

- Python 3.10 ou superior
- FFmpeg instalado no sistema
- `pip install -r requirements.txt` (yt-dlp e mutagen)

O YouTube muda com frequência; mantenha o yt-dlp atualizado com `pip install -U yt-dlp`.

## Começo rápido

```bash
python plpython.py                                  # mostra os comandos
python plpython.py help download                    # todas as opções de um comando

python plpython.py download -c csvs -P              # baixa todos os CSVs da pasta csvs/, com barra de progresso
python plpython.py review                           # o que ficou para conferir
python plpython.py review --ok-all                  # estava tudo certo
python plpython.py update -c csvs                   # depois: tenta de novo falhas e pendências
```

O comando vem sempre primeiro, depois as opções dele. Sem um comando, nada é executado.

### Atalhos dos parâmetros mais usados

| Atalho | Parâmetro | Comandos |
|---|---|---|
| `-c` | `--csvfile` | download, update, playlist, sync |
| `-s` / `-p` | `--songs-dir` / `--playlist-dir` | download, update, review, metadata, revert, sync |
| `-P` | `--progress` | download, update |
| `-v` / `-vv` / `-vvv` | `--verbose` | todos, menos revert |
| `-w` | `--wait-on-limit` | download, update |
| `-r` | `--retry` | download |
| `-db` | `--prefer-db` | download, update |
| `-nr` | `--no-review` | download, update |
| `-nh` | `--no-hybrid` | download, update |
| `-b` / `-k` | `--cookies-from-browser` / `--cookies` | download, update, playlist |
| `-a` | `--answer` (review) / `--append` (download, update) | |
| `-y` | `--default-ok` (review) / `--apply` (metadata, revert, sync) | |
| `-A` | `--all-fields` | review |
| `-q` / `-d` / `-e` | `--search` / `--details` / `--export` | review |
| `-f` | `--sheet` | metadata |
| `-n` | `--name` | playlist |
| `-o` / `-L` | `--output` / `--liked` | spotify |
| `-m` | `--missing` | sync |
| `-u` / `-l` | `--url-column` / `--log-file` | download, update |

Atalhos de uma letra podem ser juntados: `-Pvv` é o mesmo que `-P -v -v`, e `review -ay` é `--answer --default-ok`.

```bash
python plpython.py download -c csvs -Pw -b firefox     # progresso, espera bloqueio, conta do Google
python plpython.py update -c csvs -P -nr               # atualiza sem lista de conferência
python plpython.py review -ay                          # responde, aceitando o que ficar em branco
python plpython.py metadata -y                         # aplica a planilha
```

## Pastas

Os arquivos são criados **na pasta de onde você roda o comando**, não na pasta do programa:

```
pasta onde você rodou/
├── csvs/                    ← só lida (é o que -c/--csvfile indica)
├── songs/<artista>/…mp3     ← MP3 no artista principal; atalhos nos secundários (-s muda o nome)
├── songs/_observar/         ← atalhos das faixas para conferir
├── playlists/*.m3u          ← uma por CSV ou por playlist do Spotify (-p muda)
├── sql/                     ← downloads.sqlite3 (progresso), musicas.sqlite3 (metadados) e m3u_backup/
├── spotify.csv              ← suas playlists do Spotify (comando spotify)
├── nao_baixadas.csv         ← o que o sync achou que falta baixar
├── Logs.txt                 ← erros
└── metadados_pendentes.csv  ← planilha de conferência
```

## Comandos

| Comando | O que faz |
|---|---|
| `playlist` | cria/atualiza CSVs a partir de playlists do YouTube (URL, suas playlists, curtidas, assistir mais tarde) |
| `download` | baixa os CSVs, identifica as músicas e gera as M3Us; retoma de onde parou |
| `update` | relê os CSVs, refaz falhas e reavalia `PENDENTE`/`OBSERVAR` sem baixar de novo o que já existe |
| `review` | mostra e confirma no terminal as faixas guardadas no SQL; `--answer` corrige uma por uma |
| `metadata` | aplica as correções feitas na planilha `metadados_pendentes.csv` (simula sem `--apply`) |
| `spotify` | lê suas playlists do Spotify e salva as músicas em `spotify.csv` (não baixa nada) |
| `sync` | compara o `spotify.csv` com o que já foi baixado, acerta as M3Us e lista o que falta (simula sem `-y`) |
| `revert` | converte uma biblioteca no layout antigo `songs/_library` para pastas por artista (simula sem `--apply`) |

```bash
# playlist
python plpython.py playlist "https://www.youtube.com/playlist?list=PL..." --name Rock
python plpython.py playlist mine --cookies-from-browser firefox

# download / update
python plpython.py download -c csvs                       # todos os CSVs da pasta
python plpython.py download -c csvs/Alone.csv             # um CSV
python plpython.py download a.csv b.csv --append          # vários, acrescentando às M3Us
python plpython.py download -c csvs -s Musicas -p m3u     # outras pastas de saída
python plpython.py download -c csvs --prefer-db           # em divergência, vence o MusicBrainz
python plpython.py download -c csvs --review diff         # só divergências vão para conferência
python plpython.py download -c csvs --require-isrc        # só aceita com ISRC confirmado
python plpython.py download -c csvs --no-hybrid           # rápido: só dados do YouTube
python plpython.py download -c csvs --wait-on-limit --sleep 10-20
python plpython.py download -c meu.csv -u -1              # URL na última coluna do CSV
python plpython.py update -c csvs -v

# conferência
python plpython.py review -d
python plpython.py review --answer
python plpython.py review --ok UG1HUOyL9IA
python plpython.py review --export conferir.csv
python plpython.py metadata --apply

# Spotify
python plpython.py spotify -L                             # suas playlists + curtidas → spotify.csv
python plpython.py sync -y                                # acerta as M3Us e salva nao_baixadas.csv

# outros
python plpython.py revert -s songs --apply
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

## Conferir e corrigir

### Responder uma por uma: `review --answer`

```bash
python plpython.py review --answer                 # Enter deixa como está; só muda o que você digitar
python plpython.py review --answer --default-ok    # Enter aceita; a faixa sai da lista
```

Para cada faixa `OBSERVAR`/`PENDENTE`, aparece o que está errado e uma pergunta por campo, com o valor atual entre colchetes e as opções do YouTube e do banco (MusicBrainz) quando elas diferem:

```
[2/12] OBSERVAR  https://youtu.be/aaaaaaaaaaa
  arquivo: songs/Daft Punk/Get Lucky [aaaaaaaaaaa].mp3
  O que está errado:
    • título diverge: YouTube='Get Lucky Remix' / MusicBrainz='Get Lucky'
    • álbum não encontrado
  Título [Get Lucky Remix]  (banco: Get Lucky): :db
  Artistas [Daft Punk]:
  Álbum [—]  (banco: Random Access Memories): :db
  ✓ conferida (Título, Álbum): Daft Punk — Get Lucky | Random Access Memories
```

| Resposta | Efeito |
|---|---|
| Enter | mantém o valor |
| texto | novo valor (artistas: separe com `|`; o primeiro é o principal) |
| `:db` / `:yt` | usa o valor do banco / do YouTube |
| `:-` | apaga (álbum ou ISRC) |
| `:ok` (no título) | está tudo certo como está |
| `:s` / `:q` (no título) | pula esta faixa / sai (o que já foi respondido fica salvo) |

- **Só os campos com problema (⚠) são perguntados.** Cada motivo aponta um campo: "título diverge" → título; "artista diverge" → artistas; "deduzidos do título/canal" → título e artistas; "álbum não encontrado" → álbum; ISRC → ISRC. Os demais aparecem com ✓. Use `--all-fields` para perguntar todos.
- **Sem `--default-ok`:** a faixa que você deixar toda em branco **continua na lista**. Se você mudou algum campo (ou respondeu `:ok`), ela é dada como conferida, e só os campos digitados mudam.
- **Com `--default-ok`:** o que ficar em branco é aceito. Se o banco tiver o valor (porque havia conflito com o YouTube ou porque o campo estava vazio), usa o do banco. Se ninguém tiver, vira `Unknown`. Todas saem da lista.
- **Cada resposta é gravada na hora**, igual ao `metadata --apply`: tags do MP3 (com backup), banco e M3Us. Se o artista principal mudar, o MP3 vai para a pasta do novo artista, com atalhos para os secundários, e sai de `_observar/`. O `update` não desfaz correções manuais.
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

Rode na mesma pasta onde rodou o download, porque ele lê `./sql/`. Para corrigir título ou artista, use a planilha e o comando `metadata`.

Outros lugares para conferir: `metadados_pendentes.csv` (coluna `observacoes`), a pasta `_observar/` com atalhos para os MP3s, ou direto no SQL:

```sql
-- sqlite3 sql/musicas.sqlite3
SELECT title, artists_json, review_note FROM music_tracks WHERE metadata_status = 'OBSERVAR';
```

Se estiver certo, marque `aplicar=sim` e `confirmar_manual=sim` na planilha e rode `plpython.py metadata --apply`. `update` reconsulta automaticamente as faixas `PENDENTE` e `OBSERVAR` que você ainda não revisou.

```bash
python plpython.py download -c ./csvs               # identifica tudo (padrão)
python plpython.py download -c ./csvs --prefer-db
python plpython.py download -c ./csvs --review diff
python plpython.py download -c ./csvs --no-review
```

Para corrigir na planilha: para cada campo que quiser mudar, preencha `titulo_final`, `artistas_finais` (separados por `|`), `album_final` ou `isrc_final` e marque o `usar_…` correspondente com `sim`. Marque `aplicar=sim` na linha e `confirmar_manual=sim` para dar a faixa como conferida. Depois rode `plpython.py metadata --apply` (sem `--apply`, só simula). A correção vale para o vídeo em todas as playlists e não é desfeita pelo `update`.

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
- **Pausa entre vídeos:** 5 a 10 s, sorteados, como recomenda a wiki do yt-dlp. O tempo gasto com MusicBrainz e com o download já conta, então só se espera o que faltar. Ajuste com `--sleep 10-20` ou desligue com `--sleep 0`. Dentro de cada consulta há 0,75 s entre requisições, como no preset `-t sleep` do yt-dlp.
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

Funciona no `download`, `update`, `playlist`, `metadata` e `review`. Sem `-v`, a saída é a de sempre.

| Nível | Mostra |
|---|---|
| `-v` | erros que normalmente só iam para o `Logs.txt`; consultas ao SQL (encontrado/novo, canal já conhecido, o que foi aprendido); leitura e download no YouTube (inclusive quando reaproveita os metadados); pausas; buscas no MusicBrainz; **YouTube × banco campo a campo** (bate ou não bate, e qual preferência foi aplicada); álbum escolhido; capa; status final com os motivos; atalhos criados |
| `-vv` | tudo do `-v` e mais: tempo decorrido; cada hipótese de artista/título com a confiança; os motivos da avaliação do canal; os candidatos do MusicBrainz com nota; os metadados lidos do YouTube campo a campo; cada tag gravada no MP3; requisições HTTP; avisos do yt-dlp; confiança final de cada campo |
| `-vvv` | tudo do `-vv` e mais a depuração do próprio yt-dlp |

No terminal, a saída é colorida. Cada área tem uma cor, "bate" e `RESOLVIDO` aparecem em verde, "não bate" e `OBSERVAR` em amarelo, e erros e `PENDENTE` em vermelho. Se a saída for redirecionada para um arquivo, ela sai sem cor. `NO_COLOR=1` desliga as cores.

Exemplo de `-v`:

```
[Modelo] canal 'CG5' → artista (+3 pontos)
[Modelo] conferindo no MusicBrainz: VGR & CG5 — Fallen Down (youtube_titulo_canal)
[MusicBrainz] buscando title_artist:fallen down / vgr cg5
[Comparação] título: YouTube 'Fallen Down' = banco 'Fallen Down' → bate, usando a grafia do banco
[Comparação] artista: YouTube 'VGR & CG5' = banco 'VGR & CG5' → bate, usando a grafia do banco
[Álbum] lançamento escolhido: 'Fallen Down' (Single, Official, 2020-01-01) entre 1; faixa 3/10
[Resultado] RESOLVIDO: VGR & CG5 — Fallen Down | álbum Fallen Down | capa: cover_art_archive
[SQL] aprendido: canal 'CG5' é do artista CG5
[Arquivo] atalho para o artista secundário: CG5/Fallen Down [aaaaaaaaaaa].mp3
```

## Estrutura do código

```
plpython.py              ← único ponto de entrada: mostra o menu e encaminha cada comando
comandos/                ← um arquivo por comando: opções, ajuda e exemplos
├── playlist.py  download.py  update.py  review.py  metadata.py  spotify.py  sync.py  revert.py
└── _comum.py            ← opções compartilhadas (pastas, bancos, conta, -v)
pltube.py                ← motor do download: YouTube, pastas, M3U, SQL, tags e capa
resolvedor_metadados.py  ← identificação: canal x artista, hipóteses, MusicBrainz, álbum, capa
aplicar_metadados.py     ← aplica a planilha de correções
reverter_biblioteca.py   ← conversão do layout antigo songs/_library
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
