# Especificação: modelo híbrido de metadados

**Estado:** proposta funcional; ainda não implementada no downloader.

## Objetivo

Melhorar os metadados obtidos para cada vídeo sem tratar automaticamente o nome do canal ou de quem publicou como sendo o artista. O modelo combina correções locais, dados do YouTube obtidos pelo `yt-dlp` e consultas ao MusicBrainz. Quando não houver informação confiável, a faixa continua sendo baixada, mas fica marcada para revisão humana.

A chave primária de associação será o ID do vídeo do YouTube. Assim, a mesma URL ou vídeo repetido em vários CSVs aponta para uma única faixa em cache. Isso não identifica automaticamente uploads diferentes da mesma gravação.

## Ordem de resolução

1. **Correção manual local:** se existir uma correção confirmada para o ID do vídeo, ela prevalece sobre as demais fontes. Isso permite corrigir uma atribuição errada uma vez e reutilizá-la nas próximas execuções e playlists.
2. **Dados do vídeo:** usar os campos que o `yt-dlp` encontrar, como título, artista, álbum, ISRC e thumbnails. O canal/uploader será guardado separadamente como origem/publicador; nunca será usado como artista por padrão.
3. **Correspondência por ISRC:** se houver ISRC válido, consultar gravações associadas no MusicBrainz. O ISRC identifica uma gravação, não a composição abstrata, e versões, remixes e remasterizações podem ter códigos diferentes. Um ISRC, portanto, ajuda a identificar a gravação, mas não escolhe sozinho a edição correta do álbum. [3]
4. **Busca por título e artista:** se não houver ISRC ou não houver correspondência útil, buscar pelo título e pelos créditos de artista. A duração pode ajudar a descartar versões incompatíveis, mas não deve ser usada sozinha para decidir. A API do MusicBrainz oferece busca de gravações por campos como título, artista, ISRC e duração. [1] [2]
5. **Capa:** depois de escolher um lançamento específico no MusicBrainz, procurar a imagem frontal correspondente no Cover Art Archive. A API trabalha com o ID do lançamento e informa se uma imagem é frontal e quais thumbnails estão disponíveis. Se não houver capa, pode-se usar uma única thumbnail do YouTube como alternativa, registrada como tal. [4]

A correspondência automática deve ser conservadora. Um ISRC exato pode confirmar a gravação, mas, se houver mais de uma edição possível, o álbum e a capa continuam pendentes. Busca por título/artista com mais de um resultado plausível também não deve escolher silenciosamente. Nesse caso, a aplicação salva as opções encontradas para consulta humana.

## Como uma pendência aparece

Uma faixa fica com status `PENDENTE` quando não for possível confirmar algum campo que se deseja preencher — em especial artista, álbum ou capa — sem adivinhação. O status poderá incluir um motivo, por exemplo `artista não confirmado`, `vários lançamentos possíveis`, `sem capa cadastrada` ou `nenhuma correspondência confiável`.

O nome do canal/uploader permanece disponível para referência, mas não é gravado como crédito de artista. Os campos desconhecidos ficam vazios, em vez de receberem um valor inventado. O título original do vídeo fica preservado para que a faixa continue identificável.

Enquanto a revisão estiver pendente, o áudio fica em `songs/_pendentes/`, com o ID do vídeo no nome. Isso o torna fácil de localizar sem colocá-lo numa pasta de artista possivelmente incorreta. A playlist M3U pode incluí-lo com indicação de pendência. Depois de corrigido, o downloader move o áudio para a organização final, atualiza os metadados e reconstrói as playlists afetadas. Com `--artist-links`, a organização final usa um único arquivo de áudio e links nas pastas dos artistas confirmados; nenhum link de artista é criado enquanto os créditos estiverem pendentes.

## Arquivo de revisão fácil de editar

A proposta é gerar/manter um arquivo UTF-8 chamado `metadados_revisao.csv`, compatível com Excel e LibreOffice. Ele terá uma linha por ID de vídeo, mesmo quando a faixa aparecer em vários CSVs. O cache SQLite continua sendo o estado técnico do downloader; o CSV será a interface legível para a revisão humana.

Colunas sugeridas:

- `video_id`, `url_youtube`, `arquivo_audio` e `playlists`: identificação da faixa e locais em que aparece.
- `titulo_youtube`, `canal_youtube`, `artistas_youtube`, `album_youtube` e `isrc_youtube`: dados de origem, preservados para comparação.
- `titulo_sugerido`, `artistas_sugeridos`, `album_sugerido`, `isrc_sugerido`, `lancamento_sugerido`, `capa_sugerida` e `fonte_sugestao`: resultado automático, se existir.
- `motivo_pendente` e `candidatos`: explicação curta e alternativas relevantes encontradas. Os candidatos devem incluir uma referência identificável, como link ou MBID, quando houver.
- `titulo_corrigido`, `artistas_corrigidos`, `album_corrigido`, `isrc_corrigido` e `capa_arquivo`: campos editáveis pelo usuário. `capa_arquivo` aponta para uma imagem local escolhida pelo usuário ou baixada de uma fonte selecionada.
- `status_revisao`: `PENDENTE`, `CORRIGIDO` ou `APLICADO`.

Para múltiplos artistas, `artistas_corrigidos` usa `|` como separador, por exemplo `Artista Um|Artista Dois`. Isso preserva vírgulas e `&` que façam parte do nome de uma banda. Em caso de álbum intencionalmente desconhecido, o usuário pode deixar o campo vazio e confirmar a revisão; o programa não deve obrigar a inventar um álbum.

O downloader deve atualizar os campos de origem e sugestão sem apagar o conteúdo escrito nas colunas de correção. Ao abrir o CSV em uma planilha, o usuário preenche os campos corrigidos e muda o status para `CORRIGIDO`. Na execução seguinte, o programa valida e aplica as correções às tags do áudio, à pasta/link de artista e às M3Us; depois muda o status para `APLICADO`. Se a correção for editada mais tarde, o usuário pode voltar o status para `CORRIGIDO` para reaplicá-la.

## Integração proposta com o downloader

Criar um módulo separado, por exemplo `resolvedor_metadados.py`, com uma interface conceitual como:

```text
resolver_metadados(video_id, dados_ytdlp, correcoes_locais, cache)
    -> metadados_resolvidos + status + proveniencia + candidatos
```

O fluxo do downloader passaria a ser:

1. Obter do `yt-dlp` os dados do vídeo, como já faz hoje.
2. Consultar correções locais pelo ID do vídeo.
3. Se não houver correção, consultar o MusicBrainz conforme a política acima e obter uma capa adequada quando possível.
4. Se os dados forem suficientes, enviar os metadados confirmados para a função de download atual.
5. Se houver incerteza, baixar mesmo assim, guardar a faixa em `songs/_pendentes/` e registrar/atualizar a linha em `metadados_revisao.csv`.
6. Quando uma correção for confirmada, aplicar as tags e reorganizar o arquivo uma única vez; atualizar a entrada do cache compartilhado para que todos os CSVs reutilizem o mesmo caminho.

O banco SQLite existente deve continuar controlando retomada e reutilização entre playlists. A camada nova acrescentaria status de revisão e proveniência dos campos — por exemplo, `manual`, `youtube`, `musicbrainz` ou `cover_art_archive` — sem refazer chamadas para vídeos já resolvidos.

A implementação pode começar atrás de uma opção explícita, como `--modelo-hibrido`, mantendo o comportamento atual como padrão até a integração ser testada. A opção exata e se o modelo híbrido se tornará padrão ficam para a etapa de implementação.

## Respeito às fontes e limites

A API do MusicBrainz não exige chave de API para consultas comuns, mas exige um `User-Agent` identificável e limita cada aplicação a no máximo uma chamada por segundo. O downloader deve armazenar resultados em cache, espaçar chamadas e respeitar respostas de limitação/indisponibilidade, em vez de repetir requisições rapidamente. [1] [5]

A capa escolhida deve manter a URL de origem e a referência do lançamento. A existência de uma imagem no Cover Art Archive não significa automaticamente que ela possa ser redistribuída sem restrições; o uso deve respeitar as condições aplicáveis à imagem. A integração não fará scraping de serviços de streaming nem baixará áudio do Spotify.

## Testes de aceitação para a futura implementação

- Um canal/uploader não é gravado como artista quando não há crédito confiável.
- Uma correção manual prevalece sobre o YouTube e sobre resultados automáticos.
- ISRC exato reutiliza uma gravação, mas deixa álbum/capa pendentes quando houver mais de uma edição plausível.
- Busca ambígua não escolhe um resultado silenciosamente e cria uma linha legível no arquivo de revisão.
- Uma faixa pendente continua baixável, aparece em `songs/_pendentes/` e pode ser localizada pelo `video_id`.
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
