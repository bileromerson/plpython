# Especificação: modelo híbrido de metadados

**Estado:** implementação inicial disponível no downloader por `--modelo-hibrido`. A importação de correções manuais e sua aplicação em lote ainda não estão implementadas.

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

Novos downloads pendentes ficam em `songs/_pendentes/`, com o ID do vídeo no nome, sem link de artista. Os valores disponíveis do YouTube (exceto uploader como artista) continuam utilizáveis, mas recebem a marca ID3 `PLTUBE_METADATA_STATUS=PENDENTE` e uma observação de que podem estar incorretos. Uma faixa já existente não é movida automaticamente: seu caminho original é mantido no relatório e o MP3 recebe as tags de status quando o modo é executado novamente. Depois de uma futura correção manual, a organização final deverá usar um único arquivo físico e links somente para créditos confirmados.

## Relatório de pendências

O downloader gera/atualiza `metadados_pendentes.csv` em UTF-8 com BOM, compatível com Excel e LibreOffice. Ele tem uma linha por ID de vídeo, mesmo quando a faixa aparece em vários CSVs. O SQLite mantém os resultados completos, caminhos, candidatos e playlists; o CSV atual é um relatório de leitura, ainda não importa correções manuais.

Colunas atualmente exportadas:

- `video_id`, `url_youtube`, `arquivo_audio`, `playlists` e `status`: identificação/localização.
- `titulo_youtube`, `artistas_youtube`, `canal_youtube`, `album_youtube`, `isrc_youtube` e `thumbnail_youtube`: origem do vídeo; canal/uploader é apenas publicador.
- `titulo_usado`, `artistas_usados`, `album_sugerido`, `isrc_sugerido` e `capa_sugerida`: valores em uso ou sugestões da resolução.
- `motivos_pendencia`, `diagnosticos`, `candidatos_musicbrainz`, `proveniencia` e `confianca`: motivo/erro, candidatos identificáveis e origem/confiança por campo.
- Os campos de correção (`titulo_corrigido`, `artistas_corrigidos`, `album_corrigido`, `isrc_corrigido`, `capa_arquivo` e `status_revisao`) são parte do próximo passo; não edite o relatório atual esperando que o downloader aplique mudanças, pois ele é regenerado e ainda não importa correções.

Para múltiplos artistas, `artistas_corrigidos` usa `|` como separador, por exemplo `Artista Um|Artista Dois`. Isso preserva vírgulas e `&` que façam parte do nome de uma banda. Em caso de álbum intencionalmente desconhecido, o usuário pode deixar o campo vazio e confirmar a revisão; o programa não deve obrigar a inventar um álbum.

Quando a entrada de correções for implementada, o programa deverá preservar campos editados, validar o estado `CORRIGIDO`, aplicar mudanças às tags/caminhos/M3Us e marcar `APLICADO`. Essa parte permanece futura; o CSV atual não é editável como entrada.

## Integração proposta com o downloader

O módulo `resolvedor_metadados.py` já fornece a resolução e é chamado pelo downloader quando informado `--modelo-hibrido`:

```text
resolver_metadados(video_id, dados_ytdlp, correcoes_locais, cache)
    -> metadados_resolvidos + status + proveniencia + candidatos
```

O fluxo atual do downloader é:

1. Obter do `yt-dlp` os dados do vídeo, como já faz hoje.
2. Em `--modelo-hibrido`, consultar o MusicBrainz por ISRC e, quando necessário, por título/artista ou título isolado; valores aceitos do banco substituem os do YouTube.
3. Baixar usando os dados resolvidos. Se pendentes, guardar novos arquivos em `songs/_pendentes/`, marcar tags ID3 e persistir candidatos/proveniência no SQLite.
4. Gerar `metadados_pendentes.csv` com caminho, motivo, fontes e candidatos. Vídeos repetidos em várias playlists têm uma única entrada e reutilizam o resultado/cache.
5. Uma futura ferramenta de revisão deverá importar correções e atualizar o arquivo, tags, links e M3Us sem baixar o áudio novamente.

O banco SQLite existente deve continuar controlando retomada e reutilização entre playlists. A camada nova acrescentaria status de revisão e proveniência dos campos — por exemplo, `manual`, `youtube`, `musicbrainz` ou `cover_art_archive` — sem refazer chamadas para vídeos já resolvidos.

O modelo permanece opt-in: sem `--modelo-hibrido`, o comportamento anterior do downloader continua valendo. A ferramenta ainda não aplica correções editadas no CSV nem transfere automaticamente áudios já existentes para `songs/_pendentes/`.

## Respeito às fontes e limites

A API do MusicBrainz não exige chave de API para consultas comuns, mas exige um `User-Agent` identificável e limita cada aplicação a no máximo uma chamada por segundo. O downloader deve armazenar resultados em cache, espaçar chamadas e respeitar respostas de limitação/indisponibilidade, em vez de repetir requisições rapidamente. [1] [5]

A capa escolhida deve manter a URL de origem e a referência do lançamento. A existência de uma imagem no Cover Art Archive não significa automaticamente que ela possa ser redistribuída sem restrições; o uso deve respeitar as condições aplicáveis à imagem. A integração não fará scraping de serviços de streaming nem baixará áudio do Spotify.

## Testes de aceitação

- Um canal/uploader não é gravado como artista quando não há crédito confiável.
- Uma correção manual prevalece sobre o YouTube e sobre resultados automáticos.
- ISRC exato reutiliza uma gravação, mas deixa álbum/capa pendentes quando houver mais de uma edição plausível.
- Busca fuzzy aceita erros pequenos com pontuação/margem; busca ambígua não escolhe um resultado silenciosamente.
- Sem correspondência, os dados do vídeo são provisórios, o uploader não vira artista e o arquivo ganha status ID3 `PENDENTE`.
- Um novo arquivo pendente fica em `songs/_pendentes/` e aparece uma vez em `metadados_pendentes.csv`, com o ID, motivos e candidatos.
- Preencher as correções e executar novamente atualiza tags, destino do áudio, links de artistas (se habilitados) e M3Us de todas as playlists que usam o vídeo.
- O processamento de um vídeo já resolvido não repete consultas externas em execuções posteriores.
- Nomes de artista contendo vírgulas ou `&` são preservados; `|` separa créditos múltiplos no campo editável.
- Uma capa de álbum confirmada tem prioridade sobre thumbnails do vídeo; no máximo uma imagem é incorporada por padrão.

## Referências

[1]: https://musicbrainz.org/doc/MusicBrainz_API "MusicBrainz API"
[2]: https://musicbrainz.org/doc/MusicBrainz_API/Search/RecordingSearch "Busca de gravações no MusicBrainz"
[3]: https://musicbrainz.org/doc/ISRC "ISRC"
[4]: https://musicbrainz.org/doc/Cover_Art_Archive/API "API do Cover Art Archive"
[5]: https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting "Limites de requisição da API MusicBrainz"
