# plpython

Baixa músicas de playlists do YouTube em MP3, identifica artista, álbum e capa (YouTube + MusicBrainz), organiza tudo em pastas por artista e gera playlists M3U. O que não dá para confirmar vai para uma lista curta de conferência.

## Instalação

- Python 3.10 ou superior
- FFmpeg instalado no sistema
- `pip install -r requirements.txt` (yt-dlp, mutagen, spotipy)

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

## Pastas

Os arquivos são criados **na pasta de onde você roda o comando**, não na pasta do programa:

```
pasta onde você rodou/
├── csvs/                    ← só lida (é o que -c/--csvfile indica)
├── songs/<artista>/…mp3     ← MP3 no artista principal; atalhos nos secundários (-s muda o nome)
├── songs/_observar/         ← atalhos das faixas para conferir
├── playlists/*.m3u          ← uma por CSV (-p muda)
├── sql/                     ← downloads.sqlite3 (progresso) e musicas.sqlite3 (metadados)
├── Logs.txt                 ← erros
└── metadados_pendentes.csv  ← planilha de conferência
```

## Comandos

| Comando | O que faz |
|---|---|
| `playlist` | cria/atualiza CSVs a partir de playlists do YouTube (URL, suas playlists, curtidas, assistir mais tarde) |
| `download` | baixa os CSVs, identifica as músicas e gera as M3Us; retoma de onde parou |
| `update` | relê os CSVs, refaz falhas e reavalia `PENDENTE`/`OBSERVAR` sem baixar de novo o que já existe |
| `review` | mostra e confirma no terminal as faixas guardadas no SQL |
| `metadata` | aplica as correções feitas na planilha `metadados_pendentes.csv` (simula sem `--apply`) |
| `spotify` | lista as faixas de uma playlist do Spotify (só consulta) |
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
python plpython.py review --ok UG1HUOyL9IA
python plpython.py review --export conferir.csv
python plpython.py metadata --apply

# outros
python plpython.py spotify "https://open.spotify.com/playlist/ID"
python plpython.py revert -s songs --apply
```

O `spotify` precisa de um app no Spotify for Developers e das variáveis `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` e `SPOTIFY_REDIRECT_URI`.

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

No terminal, sem precisar de programa de SQL:

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
├── playlist.py  download.py  update.py  review.py  metadata.py  spotify.py  revert.py
└── _comum.py            ← opções compartilhadas (pastas, bancos, conta, -v)
pltube.py                ← motor do download: YouTube, pastas, M3U, SQL, tags e capa
resolvedor_metadados.py  ← identificação: canal x artista, hipóteses, MusicBrainz, álbum, capa
aplicar_metadados.py     ← aplica a planilha de correções
reverter_biblioteca.py   ← conversão do layout antigo songs/_library
spotify_playlist_viewer.py
progresso.py  verbose.py ← barra -P e modo -v
```

Só o `plpython.py` é executável; os outros arquivos são usados por ele. Para criar um comando novo, basta um arquivo em `comandos/` e acrescentar o nome em `comandos/__init__.py`.

Testes: `python -m unittest discover -s tests -v`.

A lista abaixo contém apenas itens ainda não disponíveis.

- [ ] **Reorganização após correção manual:** o `download`/`update` já movem o MP3 para a pasta do artista principal quando o artista é confirmado; o `metadata --apply` ainda preserva o caminho do arquivo quando você troca o artista na planilha.
- [ ] **Catálogo global da biblioteca:** indexar todos os arquivos locais, seus caminhos, fontes, tags e relações com playlists; permitir filtros como `unknown` e `*` para localizar pendências ou revisar o catálogo completo.
- [ ] **Backup portátil de metadados:** criar e restaurar um catálogo SQLite com URLs, metadados e referências dos arquivos, sem exigir que os MP3s estejam conectados para consultar o catálogo. Esse backup não conterá o áudio.
- [ ] **Transferência Spotify → YouTube:** pesquisar correspondências de faixas e gerar uma lista para revisão. O visualizador atual não faz essa conversão.
