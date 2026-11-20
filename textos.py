"""Textos, nomes e títulos: as regras de comparação usadas no plpython inteiro.

- `_normalize_text`/`_similarity`/`_mesmo_nome`: comparam nomes e títulos tolerando
  maiúsculas, acentos, pontuação e ruído ("(Official Video)").
- `dividir_artistas`: separa os artistas por vírgula, " & ", " / ", "|", ";" e "feat.",
  sem quebrar nomes conhecidos ("Earth, Wind & Fire").
- `partes_do_titulo`/`artista_nas_partes`: cortam o título do vídeo nas divisórias e
  procuram o canal ou um artista conhecido em cada pedaço.
- `dados_youtube`: os campos do yt-dlp que interessam, já normalizados.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


ISRC_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")


_FEATURE_SPLIT = re.compile(r"\s+(?:feat(?:uring)?\.?|ft\.?)\s+", re.IGNORECASE)


_UNKNOWN_VALUES = {
    "",
    "unknown",
    "unknown artist",
    "artista desconhecido",
    "desconhecido",
    "n/a",
    "na",
    "none",
    "null",
    "-",
}


def normalize_isrc(value: Any) -> str:
    """Normaliza ISRC e retorna vazio se não corresponder ao formato de 12 chars."""
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if normalize_isrc(item)), "")
    normalized = re.sub(r"[\s-]", "", _text(value)).upper()
    return normalized if ISRC_PATTERN.fullmatch(normalized) else ""


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    result = str(value).strip()
    if result.casefold() in _UNKNOWN_VALUES:
        return ""
    return result


def _normalize_text(value: Any) -> str:
    text = _text(value).casefold()
    text = text.replace("’", "").replace("'", "")
    # Variações comuns de apresentação no YouTube não são parte do título.
    ruido = r"(?:official(?:\s+(?:music\s+)?(?:audio|video))?|lyrics?(?:\s+video)?|visuali[sz]er|hd|4k)"
    text = re.sub(rf"\s*[\[(]\s*{ruido}\s*[\])]\s*$", " ", text, flags=re.I)
    text = re.sub(rf"\s*[-–—]\s*{ruido}\s*$", " ", text, flags=re.I)
    text = _FEATURE_SPLIT.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _similarity(left: str, right: str) -> float:
    normalized_left = _normalize_text(left)
    normalized_right = _normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    direct = difflib.SequenceMatcher(None, normalized_left, normalized_right).ratio()
    sorted_left = " ".join(sorted(normalized_left.split()))
    sorted_right = " ".join(sorted(normalized_right.split()))
    reordered = difflib.SequenceMatcher(None, sorted_left, sorted_right).ratio()
    return max(direct, reordered)


_VARIANTE = re.compile(
    r"\s*[\[(【]([^\])】]*\b(?:slowed|sped\s*up|speed\s*up|nightcore|daycore|reverb|8d|"
    r"bass\s*boost(?:ed)?|pitched|acelerad[oa]|lento)\b[^\])】]*)[\])】]",
    re.I,
)


_RUIDO_PARENTESES = re.compile(
    r"\s*[\[(【](?:[^\])】]*?\b(?:official|oficial|lyrics?|letras?|legendad[oa]|"
    r"tradu[cç][aã]o|visuali[sz]er|audio|áudio|video|vídeo|clipe|mv|hd|4k|hq|release|"
    r"explicit|copyright|original\s+song|m[uú]sica\s+original|from\s+the|from\s+[\"“]|ost)\b[^\])】]*)[\])】]",
    re.I,
)


_RUIDO_FINAL = re.compile(
    r"(?:\s*[-–—]\s*(?:official|oficial|lyrics?|letra|audio|áudio|video|vídeo|visuali[sz]er)\b.*$"
    r"|\s+No\.\s*\d+\s*$"
    r"|\s+official\s+(?:music\s+|lyric\s+)?(?:video|audio)\s*$)",
    re.I,
)


_TITLE_FEAT = re.compile(
    r"\s*[\[(]?\s*\b(?:feat(?:uring)?\.?|ft\.?|part\.?|participa[cç][aã]o\s+(?:de\s+)?)\s+([^\])]+?)\s*[\])]?\s*$",
    re.I,
)


_CHANNEL_SUFFIX = re.compile(
    r"\s*(?:-\s*topic|vevo|official|oficial|music|m[uú]sica|tv|channel|canal)\s*$", re.I
)


_CANAL_CURADOR = re.compile(
    r"\b(?:lyrics?|letras?|legendad[oa]s?|tradu[cç][oõ]es|nightcore|slowed|reverb|sped\s*up|"
    r"8d|bass\s*boost(?:ed)?|vibes|lo-?fi|chill(?:hop)?|mix(?:es)?|playlists?|hits|r[aá]dio|"
    r"fans?|fanpage|nation|collective|network|records|recordings|copyright|nocopyright|"
    r"sounds|tunes|beats|trap|edm|remix(?:es)?|1\s*hour|hora|compila[cç][aã]o|uploads?)\b",
    re.I,
)


_DESCRICAO_REUPLOAD = re.compile(
    r"(?:no copyright infringement|i do not own|i don'?t own|all rights (?:go|belong|reserved) to|"
    r"copyright disclaimer|fair use|n[aã]o possuo|n[aã]o tenho os direitos|todos os direitos|"
    r"submit your (?:music|track)|promo(?:tion)? submissions?|follow the artist)",
    re.I,
)


def _remover_simbolos(texto: str) -> str:
    """Tira emojis e símbolos decorativos, mantendo letras de qualquer idioma."""
    texto = "".join(
        c for c in texto
        if not unicodedata.category(c).startswith(("So", "Sk", "Cs", "Co")) and c not in "\ufe0f\u200d"
    )
    return re.sub(r"\s{2,}", " ", texto).strip()


def _nome_canal_limpo(nome: str) -> str:
    anterior = None
    nome = _remover_simbolos(_text(nome))
    while anterior != nome:
        anterior = nome
        nome = _CHANNEL_SUFFIX.sub("", nome).strip(" -_|")
    return nome


def _mesmo_nome(a: str, b: str) -> bool:
    """Compara nomes tolerando grafia e nomes compostos ("Imagine Dragons" em
    "Imagine Dragons x J.I.D")."""
    na, nb = _normalize_text(a), _normalize_text(b)
    if not na or not nb:
        return False
    if na == nb or na.replace(" ", "") == nb.replace(" ", ""):
        return True
    if _similarity(na, nb) >= 0.88:
        return True
    ta, tb = set(na.split()), set(nb.split())
    menor, maior = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len("".join(menor)) >= 4 and menor <= maior


_PARENTESES = re.compile(r"\s*[\[(【][^\])】]*[\])】]")


def _sem_parenteses(texto: str) -> str:
    """'Urgently Jammin' (Sir Meow Remix)' -> 'Urgently Jammin''."""
    return _PARENTESES.sub("", _text(texto)).strip()


# Nomes com vírgula, "&", "+", " x " ou "and" que são UM artista só. Completam os
# artistas já confirmados na sua biblioteca (que valem do mesmo jeito).
NOMES_COMPOSTOS = (
    "Earth, Wind & Fire", "Crosby, Stills, Nash & Young", "Crosby, Stills & Nash", "Emerson, Lake & Palmer",
    "Blood, Sweat & Tears", "Tyler, The Creator", "Peter Bjorn and John", "Simon & Garfunkel", "Hall & Oates",
    "Daryl Hall & John Oates", "Mumford & Sons", "Florence + The Machine", "Dan + Shay", "Chloe x Halle",
    "Years & Years", "Brooks & Dunn", "Sam & Dave", "Ike & Tina Turner", "Belle & Sebastian",
    "Of Monsters and Men", "Above & Beyond", "Macklemore & Ryan Lewis", "Nico & Vinz", "Tegan and Sara",
    "She & Him", "Sonny & Cher", "Captain & Tennille", "Seals & Crofts", "Loggins & Messina", "Matt & Kim",
    "Aly & AJ", "Jack & Jack", "Big & Rich", "Iron & Wine", "Angus & Julia Stone", "Chase & Status",
    "Bob Marley & The Wailers", "Tom Petty & The Heartbreakers", "Huey Lewis & The News",
    "Bruce Springsteen & The E Street Band", "Prince & The Revolution", "Marina & The Diamonds",
    "Lil Nas X",
    "Jorge & Mateus", "Henrique & Juliano", "Zé Neto & Cristiano", "Chitãozinho & Xororó",
    "Zezé Di Camargo & Luciano", "Bruno & Marrone", "Maiara & Maraisa", "Simone & Simaria",
    "Matheus & Kauan", "Victor & Leo", "César Menotti & Fabiano", "Fernando & Sorocaba", "Edson & Hudson",
    "João Neto & Frederico", "Hugo & Guilherme", "Diego & Victor Hugo", "Israel & Rodolffo",
    "Marcos & Belutti", "Munhoz & Mariano", "Gian & Giovani", "Leandro & Leonardo", "Rionegro & Solimões",
    "Milionário & José Rico", "Tião Carreiro & Pardinho", "Guilherme & Benuto", "Clayton & Romário",
    "Jads & Jadson", "Thaeme & Thiago", "Sandy & Junior", "Claudinho & Buchecha", "Kleiton & Kledir",
    "Sá & Guarabyra", "Rick & Renner", "João Paulo & Daniel", "Chrystian & Ralf", "Matogrosso & Mathias",
    "Zé Henrique & Gabriel", "Pedro Paulo & Alex", "Henrique & Diego", "Tonico & Tinoco",
    "Pena Branca & Xavantinho", "Cascatinha & Inhana", "Day & Lara", "Antony & Gabriel",
    "Bruninho & Davi", "Mayck & Lyan", "Felipe & Rodrigo",
)


_INDICE_BASE: dict[str, str] = {}


def _indice_nomes(conhecidos: Iterable[str]) -> dict[str, str]:
    """Nome normalizado → grafia oficial, para os nomes compostos e os já conhecidos."""
    if not _INDICE_BASE:
        _INDICE_BASE.update({_normalize_text(n): n for n in NOMES_COMPOSTOS})
    indice = dict(_INDICE_BASE)
    contagem: dict[str, dict[str, int]] = {}
    for nome in conhecidos or ():
        chave = _normalize_text(nome)
        if chave and chave not in _INDICE_BASE:
            grafias = contagem.setdefault(chave, {})
            grafias[_text(nome)] = grafias.get(_text(nome), 0) + 1
    for chave, grafias in contagem.items():  # a grafia mais usada vale ("Kennyoung" 3× > "kennyoung" 1×)
        indice[chave] = max(grafias, key=lambda g: (grafias[g], g))
    return indice


def _limpar_nomes(nomes: Iterable[str]) -> list[str]:
    limpos: dict[str, str] = {}
    for nome in nomes:
        nome = _text(nome).strip(" .,;-")
        if nome and nome.casefold() not in limpos:
            limpos[nome.casefold()] = nome
    return list(limpos.values())


# Separadores de nomes: vírgula, " & ", " / " e " x " (com espaço dos dois lados), "|", ";" e
# participação ("feat.", "ft.", "part.", "with"). "AC/DC", "Simon&Garfunkel",
# "Florence + The Machine" e "Henrique e Juliano" não são separados.
_SEP_NOMES = re.compile(
    r"\s*[,;|｜]\s*|\s+[&/]\s+|\s+(?:x|×|vs\.?)\s+(?=[^\s&,+/])"
    r"|\s+(?:feat(?:uring)?\.?|ft\.?|part\.?|participa[çc][ãa]o(?:\s+especial)?(?:\s+de)?)\s+",
    re.I,
)


def dividir_artistas(texto: Any, conhecidos: Iterable[str] = ()) -> list[str]:
    """Separa os artistas: "A, B & C" -> A, B, C; "A | B" -> A, B; "A feat. B" -> A, B.

    Um nome conhecido (banco de artistas, faixas confirmadas ou NOMES_COMPOSTOS) fica
    inteiro mesmo tendo vírgula ou " & " ("Earth, Wind & Fire", "Jorge & Mateus"), com a
    grafia oficial. "aspas" também mantêm um nome junto.
    """
    texto = _remover_simbolos(_text(texto))
    if not texto:
        return []
    aspas: list[str] = []

    def guardar(m: re.Match) -> str:
        aspas.append(m.group(1).strip())
        return f"\x00{len(aspas) - 1}\x00"

    texto = re.sub(r"[\"“”]([^\"“”]+)[\"“”]", guardar, texto)

    def restaurar(trecho: str) -> str:
        return re.sub(r"\x00(\d+)\x00", lambda m: aspas[int(m.group(1))], trecho)

    pecas: list[tuple[int, int]] = []
    posicao = 0
    for achado in _SEP_NOMES.finditer(texto):
        if achado.start() <= posicao or achado.end() >= len(texto):
            continue
        pecas.append((posicao, achado.start()))
        posicao = achado.end()
    pecas.append((posicao, len(texto)))

    indice = _indice_nomes(conhecidos)
    nomes: list[str] = []
    i = 0
    while i < len(pecas):
        fim, oficial = i, ""
        for j in range(len(pecas) - 1, i - 1, -1):  # o maior trecho conhecido primeiro
            oficial = indice.get(_normalize_text(restaurar(texto[pecas[i][0]:pecas[j][1]])), "")
            if oficial:
                fim = j
                break
        nomes.append(oficial or restaurar(texto[pecas[i][0]:pecas[fim][1]]))
        i = fim + 1
    return _limpar_nomes(nomes)


_OBRA = re.compile(
    r"\b(?:o\.?s\.?t\.?|soundtrack|trilha(?:\s+sonora)?|banda\s+sonora|bso|tema\s+d[aeo]s?|abertura|"
    r"encerramento|opening|ending|netflix|disney|s[ée]rie|novela|filme)\b",
    re.I,
)


def parece_obra(texto: Any) -> bool:
    """"La Casa de Papel OST", "Tema da novela X", "Netflix": filme/série/trilha, não artista."""
    return bool(_OBRA.search(_text(texto)))


def ler_descricao_youtube_music(descricao: Any) -> dict[str, Any]:
    """Lê a descrição padrão das faixas "Provided to YouTube by ..." (YouTube Music).

    Formato:  Provided to YouTube by <Distribuidora>
              <Título> · <Artista> · <Artista 2>
              <Álbum>
              ℗ 2021 <Dono>
              Released on: 2021-11-01
    Esses dados vêm da gravadora/distribuidora e são bem confiáveis.
    """
    texto = _text(descricao)
    if "provided to youtube by" not in texto.casefold():
        return {}
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    inicio = next(
        (i for i, linha in enumerate(linhas) if linha.casefold().startswith("provided to youtube by")), None
    )
    if inicio is None or inicio + 1 >= len(linhas):
        return {}
    resultado: dict[str, Any] = {"label": linhas[inicio][len("provided to youtube by"):].strip()}
    partes = [p.strip() for p in re.split(r"\s+·\s+", linhas[inicio + 1]) if p.strip()]
    if len(partes) < 2:
        return {}
    resultado["title"] = partes[0]
    resultado["artists"] = list(dict.fromkeys(partes[1:]))
    ignorar = re.compile(r"^(?:℗|©|released on|auto-generated|composer|lyricist|producer|"
                         r"artist|associated|music publisher|\d{4}\b)", re.I)
    if inicio + 2 < len(linhas) and not ignorar.match(linhas[inicio + 2]):
        resultado["album"] = linhas[inicio + 2]
    for linha in linhas[inicio + 2:]:
        if (m := re.match(r"released on:\s*(\d{4}-\d{2}-\d{2})", linha, re.I)):
            resultado["date"] = m.group(1)
        elif (m := re.match(r"℗\s*(\d{4})", linha)) and "date" not in resultado:
            resultado["year"] = m.group(1)
    if "date" not in resultado and resultado.get("year"):
        resultado["date"] = resultado["year"]
    return resultado


_DIVISORIAS = re.compile(
    r"\s+[-–—|｜·•]\s+|\s*//\s*|\s*;\s+|\s*:\s+|\s*[\"“”«»]\s*|(?<=\S)\s+[-–—](?=\S)|(?<=\S)[-–—]\s+"
)


# Pedaços que só descrevem o vídeo: "com cifras", "Vídeo Clip versão Amanda", "Lyrics"
_PARTE_RUIDO = re.compile(
    r"^(?:com\s+cifras?|cifras?|letra|lyrics?|legendad[oa]|karaok[eê]|official\b.*|oficial\b.*|"
    r"v[ií]deo\s*clip.*|clipe\b.*|music\s+video|audio|áudio.*|hd|4k|hq|ao\s+vivo|live)$",
    re.I,
)


def partes_do_titulo(texto: Any) -> list[str]:
    """Todos os pedaços do título, cortando em qualquer divisória
    ("-", "–", "|", "·", "//", ";", ":", aspas). O que está entre parênteses não é cortado
    ("Heathens (from Suicide Squad: The Album)"), e um pedaço que é só um parêntese
    ("Overwhelmed - (Ryan Mack Remix)") fica junto do anterior."""
    guardados: list[str] = []

    def guardar(m: re.Match) -> str:
        guardados.append(m.group(0))
        return f"\x00{len(guardados) - 1}\x00"

    texto = re.sub(r"[(\[【][^()\[\]【】]*[)\]】]", guardar, _text(texto))
    restaurar = lambda t: re.sub(r"\x00(\d+)\x00", lambda m: guardados[int(m.group(1))], t)  # noqa: E731
    partes: list[str] = []
    for bruto in _DIVISORIAS.split(texto):
        parte = re.sub(r"^(?:(?:remix|cover|vers[aã]o)\s+)?(?:by|por|de)\s+", "",  # '"Música" by Artista'
                       restaurar(bruto or "").strip(" -–—|'"), flags=re.I)
        if not parte:
            continue
        if partes and re.fullmatch(r"[(\[【][^()\[\]【】]*\b(?:remix|mix|edit|version|vers[aã]o|live|ao vivo|"
                                   r"remaster(?:ed)?|vip|bootleg|flip|cover|instrumental|acoustic|ac[uú]stico)\b"
                                   r"[^()\[\]【】]*[)\]】]", parte, re.I):
            partes[-1] = f"{partes[-1]} {parte}"  # "(Ryan Mack Remix)" é versão da música anterior
        else:
            partes.append(parte)
    return partes


def _sem_espaco(texto: str) -> str:
    return _normalize_text(texto).replace(" ", "")


def _mesmo_titulo(a: str, b: str) -> bool:
    return bool(_sem_espaco(a)) and _sem_espaco(_sem_parenteses(a)) == _sem_espaco(_sem_parenteses(b))


def _nome_inteiro(nome: str, pedaco: str) -> bool:
    """O pedaço é o nome todo (só muda a grafia), e não "Outro & Nome"."""
    a, b = _sem_espaco(nome), _sem_espaco(_sem_parenteses(pedaco))
    return bool(a and b) and (a == b or _similarity(a, b) >= 0.88)


def artista_nas_partes(
    texto: Any, candidatos: Sequence[str], preferidos: Sequence[str] = ()
) -> tuple[str, str, str] | None:
    """Procura, entre os pedaços do título, um que seja um dos `candidatos`
    (canal que enviou, artistas do banco). Devolve (candidato, pedaço do artista, música).

    "Liberdade Provisória - Henrique e Juliano - com cifras" + "Henrique & Juliano"
    → ("Henrique & Juliano", "Henrique e Juliano", "Liberdade Provisória").
    Sem divisória, aceita o nome no começo ou no fim: "ACDC Thunderstruck" + "AC/DC"."""
    candidatos = [c for c in dict.fromkeys(_text(c) for c in candidatos) if c]
    if not candidatos:
        return None
    partes = partes_do_titulo(texto)

    def sem_feat(pedaco: str) -> str:
        limpo = _sem_parenteses(pedaco)
        ft = _TITLE_FEAT.search(limpo)
        return limpo[: ft.start()].strip() if ft and ft.start() > 0 else limpo

    def quem(pedaco: str) -> str | None:
        limpo = sem_feat(pedaco)  # "Bad Habits feat. BMTH": BMTH é participação, não o artista
        for nome in candidatos:
            if _sem_espaco(nome) == _sem_espaco(limpo) or (
                    len(_sem_espaco(nome)) >= 4 and _mesmo_nome(nome, limpo)
                    and len(_normalize_text(limpo).split()) <= len(_normalize_text(nome).split()) + 2):
                return nome
        for nome in candidatos:
            if any(_mesmo_nome(nome, a) and _sem_espaco(a) == _sem_espaco(nome)
                   for a in dividir_artistas(limpo)):
                return nome  # "Calvin Harris, Dua Lipa" contém "Dua Lipa"
        return None

    if len(partes) >= 2:
        for i, pedaco in enumerate(partes):
            nome = quem(pedaco)
            if not nome:
                continue
            # a música é o pedaço mais perto do artista que não é ruído nem a obra/trilha
            resto = sorted(
                (j for j, p in enumerate(partes) if j != i and not _PARTE_RUIDO.match(_sem_parenteses(p))
                 and not quem(p)),
                key=lambda j: (parece_obra(partes[j]), not any(_mesmo_titulo(partes[j], p) for p in preferidos),
                               abs(j - i), j))
            if resto:
                musica = re.sub(r"\s+(?:oficial|official)\b\s*(?=(?:[(\[][^)\]]*[)\]])?\s*$)", " ",
                                partes[resto[0]], flags=re.I).strip()
                musica = re.sub(r"^\[[^\]]*\]\s*", "", musica) or musica or partes[resto[0]]  # "[Deluxe] X"
                if (ft := _TITLE_FEAT.search(musica)) and ft.start() > 0:
                    musica = musica[: ft.start()].strip()  # o "feat." já entra nos artistas
                return nome, sem_feat(pedaco), musica
        return None
    # um pedaço só: o nome pode estar colado no começo ou no fim
    palavras = sem_feat(_text(partes[0] if partes else texto)).split()
    for nome in candidatos:
        alvo = _sem_espaco(nome)
        if len(alvo) < 3:
            continue
        for k in range(1, len(palavras)):
            for artista, musica in ((palavras[:k], palavras[k:]), (palavras[-k:], palavras[:-k])):
                musica = re.sub(r"\s+(?:by|por|de)$", "", " ".join(musica), flags=re.I)  # "My Demons by"
                if _sem_espaco(" ".join(artista)) == alvo and _sem_espaco(musica):
                    return nome, " ".join(artista), musica
    return None


def _mesmo_texto(a: str, b: str, limiar: float = 0.9) -> bool:
    return _similarity(a, b) >= limiar


def _mesmos_artistas(a: Sequence[str], b: Sequence[str]) -> bool:
    if not a or not b:
        return False
    juntos = lambda v: " ".join(sorted(" ".join(map(_normalize_text, v)).split()))  # noqa: E731
    return _mesmo_texto(juntos(a), juntos(b), 0.85) or _mesmo_nome(a[0], b[0])


def _artists(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        values: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                item = item.get("name") or item.get("artist") or ""
            name = _text(item)
            if name:
                values.append(name)
        return list(dict.fromkeys(values))
    # Texto solto: "|" é explícito; vírgula, "&", "feat." seguem dividir_artistas.
    return dividir_artistas(value)


def _extract_youtube_metadata(data: Mapping[str, Any]) -> dict[str, Any]:
    title = _text(data.get("track")) or _text(data.get("title")) or _text(data.get("fulltitle"))
    artists = _artists(data.get("artists")) or _artists(data.get("artist"))
    album = _text(data.get("album")) or _text(data.get("release"))
    isrc = normalize_isrc(data.get("isrc") or data.get("isrcs"))
    uploader = _text(data.get("uploader")) or _text(data.get("channel"))
    try:
        duration = max(0.0, float(data.get("duration") or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    thumbnails: list[dict[str, Any]] = []
    raw_thumbnails = data.get("thumbnails")
    if isinstance(raw_thumbnails, list):
        for item in raw_thumbnails:
            if not isinstance(item, Mapping):
                continue
            url = _text(item.get("url"))
            if not url:
                continue
            try:
                width = max(0, int(item.get("width") or 0))
                height = max(0, int(item.get("height") or 0))
            except (TypeError, ValueError):
                width, height = 0, 0
            thumbnails.append({"url": url, "width": width, "height": height})
    single_thumbnail = _text(data.get("thumbnail"))
    if single_thumbnail and all(item["url"] != single_thumbnail for item in thumbnails):
        thumbnails.append({"url": single_thumbnail, "width": 0, "height": 0})
    thumbnails.sort(key=lambda item: (item["width"] * item["height"], item["width"]), reverse=True)
    return {
        "title": title,
        "artists": artists,
        "album": album,
        "isrc": isrc,
        "uploader": uploader,
        "duration": duration,
        "thumbnails": thumbnails,
        "thumbnail_url": thumbnails[0]["url"] if thumbnails else "",
    }


def _data_youtube(data: Mapping[str, Any]) -> str:
    bruto = _text(data.get("release_date"))
    if re.fullmatch(r"\d{8}", bruto):
        return f"{bruto[:4]}-{bruto[4:6]}-{bruto[6:]}"
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", bruto):
        return bruto
    ano = _text(data.get("release_year"))
    return ano if re.fullmatch(r"\d{4}", ano) else ""


def dados_youtube(data: Mapping[str, Any]) -> dict[str, Any]:
    """Campos do yt-dlp usados na identificação, já normalizados."""
    yt = _extract_youtube_metadata(data)
    ytm = data.get("youtube_music")
    if not isinstance(ytm, Mapping):
        ytm = ler_descricao_youtube_music(data.get("description"))
    reupload = data.get("descricao_reupload")
    if reupload is None:
        reupload = bool(_DESCRICAO_REUPLOAD.search(_text(data.get("description"))))
    yt.update({
        "video_title": _text(data.get("title")) or _text(data.get("fulltitle")),
        "track": _text(data.get("track")),
        "channel_id": _text(data.get("channel_id")),
        "channel_verified": bool(data.get("channel_is_verified")),
        "release_date": _data_youtube(data),
        "youtube_music": dict(ytm or {}),
        "descricao_reupload": bool(reupload),
        "fonte_artistas": _text(data.get("fonte_artistas")) or "youtube",
    })
    # Artista igual ao nome do canal sem campo de faixa é o "uploader como artista"
    # dos downloads antigos, não um crédito do YouTube.
    if yt["artists"] and not yt["track"] and len(yt["artists"]) == 1 and _mesmo_nome(
        yt["artists"][0], _nome_canal_limpo(yt["uploader"])
    ) and not yt["youtube_music"]:
        yt["fonte_artistas"] = "canal"
    return yt
