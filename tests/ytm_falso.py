"""YouTube Music de mentira para os testes: responde como a ytmusicapi, sem internet."""
from __future__ import annotations

import textos as tx


def artistas(*nomes: str, pagina: bool = True) -> list[dict]:
    return [{"name": n, "id": ("UC" + "".join(ch for ch in n if ch.isalnum())[:20]) if pagina else None}
            for n in nomes]


def faixa(video_id: str, titulo: str, nomes, *, tipo: str = "ATV", album: str = "", album_id: str = "",
          segundos: int = 200, ano: str = "", contraparte: dict | None = None) -> dict:
    """Um item como os de get_watch_playlist()["tracks"] e search(filter="songs")."""
    dados = {
        "videoId": video_id, "title": titulo, "length": f"{segundos // 60}:{segundos % 60:02d}",
        "duration_seconds": segundos, "videoType": f"MUSIC_VIDEO_TYPE_{tipo}",
        "artists": artistas(*nomes) if isinstance(nomes, (list, tuple)) else nomes,
        "thumbnail": [{"url": f"https://lh3.googleusercontent.com/{video_id}=w60-h60-l90-rj", "width": 60, "height": 60}]
        if tipo == "ATV" else [{"url": f"https://i.ytimg.com/vi/{video_id}/hq.jpg", "width": 480, "height": 360}],
        "resultType": "song" if tipo == "ATV" else "video",
    }
    if album:
        dados["album"] = {"name": album, "id": album_id or "MPREb_" + "".join(c for c in album if c.isalnum())}
    if ano:
        dados["year"] = ano
    if contraparte:
        dados["counterpart"] = contraparte
    return dados


class YTMFalso:
    """videos: vídeo -> item; catalogo: músicas que a busca acha; albuns: browseId -> álbum."""

    def __init__(self, videos=(), catalogo=(), albuns=None):
        self.videos = {v["videoId"]: v for v in videos}
        self.catalogo = list(catalogo)
        self.albuns = dict(albuns or {})
        self.chamadas: list[tuple[str, str]] = []

    def get_watch_playlist(self, videoId, limit=25):
        self.chamadas.append(("vídeo", videoId))
        if videoId not in self.videos:
            raise Exception("Video unavailable")
        return {"tracks": [self.videos[videoId]], "playlistId": "RDAMVM" + videoId}

    def search(self, query, filter=None, limit=20):
        self.chamadas.append(("busca", query))
        consulta = f" {tx._normalize_text(query)} "
        achados = [m for m in self.catalogo if f" {tx._normalize_text(tx._sem_parenteses(m['title']))} " in consulta]
        # como a busca de verdade: quem tem o artista na consulta vem primeiro
        achados.sort(key=lambda m: not any(f" {tx._normalize_text(a['name'])} " in consulta for a in m["artists"]))
        return achados[:limit]

    def get_album(self, browseId):
        self.chamadas.append(("álbum", browseId))
        if browseId not in self.albuns:
            raise Exception("Album not found")
        return self.albuns[browseId]


def album(titulo: str, nomes, faixas, ano: str = "2020") -> dict:
    return {
        "title": titulo, "type": "Album", "year": ano, "trackCount": len(faixas),
        "artists": artistas(*nomes),
        "thumbnails": [{"url": "https://lh3.googleusercontent.com/capa-" + "".join(c for c in titulo if c.isalnum())
                        + "=w544-h544-l90-rj", "width": 544, "height": 544}],
        "tracks": [{"videoId": vid, "title": t, "trackNumber": i} for i, (vid, t) in enumerate(faixas, 1)],
    }
