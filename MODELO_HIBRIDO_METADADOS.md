# Especificação: modelo híbrido de metadados

> **Atualização:** o modelo atual (canal × artista, confiança por fonte, álbum, capa do Cover Art Archive, memória de canais e conferência mínima) está descrito no README, em "Identificação de artista, álbum e capa", e implementado em `resolver_com_prioridade` (`resolvedor_metadados.py`). Este documento descreve a primeira versão.

**Estado:** o downloader e o resolvedor funcionam por `--modelo-hibrido`. A planilha editável e o aplicador seguro estão disponíveis; reorganizar os arquivos após a revisão e incorporar capas continuam futuros.

## Objetivo

Melhorar os metadados obtidos para cada vídeo sem tratar automaticamente o nome do canal ou de quem publicou como sendo o artista. O modo opcional combina dados do YouTube obtidos pelo `yt-dlp` e consultas ao MusicBrainz. A comparação aceita diferenças pequenas de grafia/formatação, mas não escolhe silenciosamente entre candidatos próximos. Quando não houver informação confiável, a faixa continua sendo baixada, marcada como provisória e incluída na fila de revisão.

A chave primária de associação será o ID do vídeo do YouTube. Assim, a mesma URL ou vídeo repetido em vários CSVs aponta para uma única faixa em cache. Isso não identifica automaticamente uploads diferentes da mesma gravação.

## Ordem de resolução

1. **Correção manual local:** se existir uma correção confirmada para o ID do vídeo, ela prevalece sobre as demais fontes. Isso permite corrigir uma atribuição errada uma vez e reutilizá-la nas próximas execuções e playlists.
2. **Dados do vídeo:** usar os campos que o `yt-dlp` encontrar, como título, artista, álbum, ISRC e thumbnails. O canal/uploader será guardado separadamente como origem/publicador; nunca será usado como artista por padrão.
3. **Correspondência por ISRC:** se houver ISRC válido, consultar gravações associadas no MusicBrainz. O ISRC identifica uma gravação, não a composição abstrata, e versões, remixes e remasterizações podem ter códigos diferentes. Um ISRC, portanto, ajuda a identificar a gravação, mas não escolhe sozinho a edição correta do álbum. [3]
4. **Busca fuzzy por título e artista:** se não houver ISRC exato, buscar por termos do título e dos créditos, tolerando um erro de grafia em palavras maiores. O ranking compara texto normalizado, acentos, pontuação, sufixos de apresentação do YouTube e duração. Se o YouTube não informar artista, pode buscar pelo título, mas exige pontuação/margem mais cautelosas. Uma diferença pequena aceita usa os campos canônicos do MusicBrainz como valores finais. A duração ajuda a descartar versões incompatíveis, mas não decide sozinha. [1] [2]
5. **Capa:** depois de escolher um lançamento específico no MusicBrainz, procurar a imagem frontal correspondente no Cover Art Archive. A API trabalha com o ID do lançamento e informa se uma imagem é frontal e quais thumbnails estão disponíveis. Nesta primeira integração, a URL de capa pode ficar no resultado/relatório, mas baixar e embutir a imagem no MP3 ou substituir a thumbnail atual ainda falta implementar. [4]

A correspondência é flexível, não permissiva: pontuação de título/artista e margem entre o primeiro e o segundo candidato controlam a aceitação. Um ISRC exato pode confirmar a gravação, mas, se houver mais de uma edição possível, o álbum e a capa continuam pendentes. Busca por título/artista com mais de um resultado plausível também não escolhe silenciosamente; as opções ficam no resultado guardado e no relatório de pendências.

## Como uma pendência aparece

Uma faixa fica com status `PENDENTE` quando não for possível confirmar algum campo que se deseja preencher — em especial artista, álbum ou capa — sem adivinhação. O status poderá incluir um motivo, por exemplo `artista não confirmado`, `vários lançamentos possíveis`, `sem capa cadastrada` ou `nenhuma correspondência confiável`.

O nome do canal/uploader permanece disponível para referência, mas não é gravado como crédito de artista. Os campos desconhecidos ficam vazios, em vez de receberem um valor inventado. O título original do vídeo fica preservado para que a faixa continue identificável.

Novos downloads pendentes ficam fisicamente na pasta do primeiro artista disponível dentro da raiz de músicas configurada; sem crédito de artista, usam `Artista Desconhecido/`. A pasta `_pendentes/`, dentro dessa mesma raiz, contém um link de atalho para o mesmo MP3, sem ocupar espaço adicional. Não são criados links para artistas colaboradores enquanto os créditos estiverem pendentes. Os valores disponíveis do YouTube (exceto uploader como artista) recebem a marca ID3 `PLTUBE_METADATA_STATUS=PENDENTE` e uma observação de que podem estar incorretos. Uma faixa já existente não é movida automaticamente: seu caminho original é mantido no relatório e o MP3 recebe as tags de status quando o modo é executado novamente.

## Relatório de pendências

O downloader gera/atualiza `metadados_pendentes.csv` em UTF-8 com BOM, compatível com Excel e LibreOffice. Há uma linha por ID de vídeo, mesmo quando a faixa aparece em vários CSVs. A planilha contém sugestões preenchidas e colunas editáveis de seleção/valor final. As edições manuais são preservadas quando a fila é reexportada. `downloads.sqlite3` mantém estados, caminhos relativos e playlists; `musicas.sqlite3` mantém os metadados estruturados, proveniência e resultados completos.

Colunas atualmente exportadas:

- `video_key`, `video_id`, `url_youtube`, `arquivo_audio`, `playlists` e `status`: identificação/localização.
- `titulo_youtube`, `artistas_youtube`, `canal_youtube`, `album_youtube`, `isrc_youtube` e `thumbnail_youtube`: origem do vídeo; canal/uploader é apenas publicador.
- `titulo_usado`, `artistas_usados`, `album_sugerido`, `isrc_sugerido` e `capa_sugerida`: valores em uso ou sugestões da resolução.
- `motivos_pendencia`, `diagnosticos`, `diagnosticos_youtube`, `diagnosticos_local`, `diagnosticos_musicbrainz`, `candidatos_musicbrainz`, `proveniencia` e `confianca`: motivo/erro, diagnósticos rotulados por fonte, candidatos identificáveis e origem/confiança por campo.
- `aplicar`, `usar_titulo`, `titulo_final`, `usar_artistas`, `artistas_finais`, `usar_album`, `album_final`, `usar_isrc`, `isrc_final` e `confirmar_manual`: controles editáveis. Todos os controles `usar_*`, `aplicar` e `confirmar_manual` começam como `nao`; as sugestões aparecem nos campos `_final`, mas só são gravadas se você marcar explicitamente os controles.

Para múltiplos artistas, `artistas_finais` usa `|` como separador, por exemplo `Artista Um|Artista Dois`. Isso preserva vírgulas e `&` que façam parte do nome de uma banda. Para apagar explicitamente o álbum ou ISRC, preencha o respectivo campo `_final` com `__LIMPAR__`. `confirmar_manual=sim` marca a revisão concluída quando título e ao menos um artista estão preenchidos; álbum pode ficar vazio se você decidiu que é desconhecido.

Para simular as escolhas sem alterar arquivos, execute `python aplicar_metadados.py --BaseDir ./csvs`. Depois de revisar o plano, acrescente `--apply` para gravar tags ID3, atualizar os dois bancos SQLite e os nomes nas M3Us. O aplicador não consulta serviços externos, não baixa áudio e não move/renomeia o MP3. Revisões parciais continuam pendentes; campos manuais parciais em cache não são substituídos por `--reprocess`.

## Integração proposta com o downloader

O módulo `resolvedor_metadados.py` já fornece a resolução e é chamado pelo downloader quando informado `--modelo-hibrido`:

```text
resolver_metadados(video_id, dados_ytdlp, correcoes_locais, cache)
    -> metadados_resolvidos + status + proveniencia + candidatos
```

O fluxo atual do downloader é:

1. Obter do `yt-dlp` os dados do vídeo, como já faz hoje.
2. Em `--modelo-hibrido`, consultar o MusicBrainz por ISRC e, quando necessário, por título/artista ou título isolado; valores aceitos do banco substituem os do YouTube.
3. Baixar usando os dados resolvidos. Se pendentes, guardar o MP3 físico na pasta do artista disponível (ou em `Artista Desconhecido`), criar em `_pendentes` somente um link para ele, marcar tags ID3 e persistir candidatos/proveniência no SQLite.
4. Gerar `metadados_pendentes.csv` com caminho, motivo, fontes e candidatos. Vídeos repetidos em várias playlists têm uma única entrada e reutilizam o resultado/cache.
5. `aplicar_metadados.py` lê as linhas explicitamente marcadas, simula por padrão e, com `--apply`, atualiza as tags escolhidas, o estado SQLite e as M3Us sem baixar áudio novamente.

O banco antigo único `pltube_state.sqlite3`, quando encontrado, é migrado uma vez sem ser apagado. O novo `downloads.sqlite3` controla retomada e reutilização entre playlists; `musicas.sqlite3` mantém uma linha por faixa, campos consultáveis e o JSON detalhado. Os caminhos para áudio são relativos à pasta do banco de downloads (por exemplo, `../songs/Artista A/musica1.mp3`) e os caminhos dos CSVs são relativos a `--BaseDir`.

Os argumentos opcionais `--exigir-isrc` e `--exigir-artista-musicbrainz` tornam esses campos requisitos explícitos no download. Diagnósticos como ausência de ISRC no yt-dlp ou falta de artista retornado pelo MusicBrainz ficam separados por fonte nos bancos e, para faixas pendentes, na planilha. O programa não escreve metadados de volta no YouTube.

O modelo permanece opt-in: sem `--modelo-hibrido`, o comportamento anterior do downloader continua valendo. O aplicador não move os áudios para outra pasta nem cria links de artista; o caminho existente é preservado.

## Respeito às fontes e limites

A API do MusicBrainz não exige chave de API para consultas comuns, mas exige um `User-Agent` identificável e limita cada aplicação a no máximo uma chamada por segundo. O downloader deve armazenar resultados em cache, espaçar chamadas e respeitar respostas de limitação/indisponibilidade, em vez de repetir requisições rapidamente. [1] [5]

A capa escolhida deve manter a URL de origem e a referência do lançamento. A existência de uma imagem no Cover Art Archive não significa automaticamente que ela possa ser redistribuída sem restrições; o uso deve respeitar as condições aplicáveis à imagem. A integração não fará scraping de serviços de streaming nem baixará áudio do Spotify.

## Testes de aceitação

- Um canal/uploader não é gravado como artista quando não há crédito confiável.
- A planilha começa sem ações aprovadas; só `aplicar=sim` e os controles `usar_*` selecionados alteram campos.
- A simulação não altera MP3/SQLite; `--apply` atualiza tags, provenance, estado local e M3Us relacionadas.
- ISRC exato reutiliza uma gravação, mas deixa álbum/capa pendentes quando houver mais de uma edição plausível.
- Busca fuzzy aceita erros pequenos com pontuação/margem; busca ambígua não escolhe um resultado silenciosamente.
- Sem correspondência, os dados do vídeo são provisórios, o uploader não vira artista e o arquivo ganha status ID3 `PENDENTE`.
- Um novo MP3 pendente fica fisicamente na pasta do artista, e `_pendentes` aponta para ele com um link; a faixa aparece uma vez em `metadados_pendentes.csv`.
- Correções parciais permanecem pendentes e preservadas; uma revisão confirmada com título e artista atualiza os dados e remove a faixa da fila.
- O processamento de um vídeo já resolvido não repete consultas externas em execuções posteriores.
- Nomes de artista contendo vírgulas ou `&` são preservados; `|` separa créditos múltiplos no campo editável.
- Uma capa de álbum confirmada tem prioridade sobre thumbnails do vídeo; no máximo uma imagem é incorporada por padrão.

## Referências

[1]: https://musicbrainz.org/doc/MusicBrainz_API "MusicBrainz API"
[2]: https://musicbrainz.org/doc/MusicBrainz_API/Search/RecordingSearch "Busca de gravações no MusicBrainz"
[3]: https://musicbrainz.org/doc/ISRC "ISRC"
[4]: https://musicbrainz.org/doc/Cover_Art_Archive/API "API do Cover Art Archive"
[5]: https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting "Limites de requisição da API MusicBrainz"
