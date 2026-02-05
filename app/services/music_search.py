from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


@dataclass(frozen=True)
class SearchTrack:
    source: str
    title: str
    artist: str | None
    url: str | None          # страница трека
    preview_url: str | None  # то, что реально проигрывается (full или preview)
    artwork_url: str | None


_AUDIO_EXT_RE = re.compile(r"\.(mp3|ogg|m4a|aac|wav)(\?|$)", re.IGNORECASE)


def is_direct_audio_url(u: str) -> bool:
    s = (u or "").strip()
    if not s.startswith("https://"):
        return False
    return bool(_AUDIO_EXT_RE.search(s))


async def itunes_search(query: str, *, limit: int = 8, country: str = "US") -> list[SearchTrack]:
    q = (query or "").strip()
    if not q:
        return []

    params = {
        "term": q,
        "media": "music",
        "entity": "song",
        "limit": str(max(1, min(limit, 25))),
        "country": country,
    }

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
        async with s.get("https://itunes.apple.com/search", params=params) as r:
            if r.status != 200:
                return []
            text = await r.text()

    try:
        data: dict[str, Any] = json.loads(text)
    except Exception:
        return []

    out: list[SearchTrack] = []
    for item in (data.get("results") or [])[:limit]:
        title = str(item.get("trackName") or "").strip()
        artist = str(item.get("artistName") or "").strip() or None
        url = str(item.get("trackViewUrl") or "").strip() or None
        preview = str(item.get("previewUrl") or "").strip() or None
        art = str(item.get("artworkUrl100") or "").strip() or None
        if not title:
            continue
        out.append(SearchTrack(
            source="itunes",
            title=title,
            artist=artist,
            url=url,
            preview_url=preview,  # это preview, НЕ full
            artwork_url=art,
        ))
    return out


async def jamendo_search(query: str, *, limit: int = 8) -> list[SearchTrack]:
    client_id = (os.getenv("JAMENDO_CLIENT_ID") or "").strip()
    if not client_id:
        return []

    q = (query or "").strip()
    if not q:
        return []

    params = {
        "client_id": client_id,
        "format": "json",
        "limit": str(max(1, min(limit, 25))),
        "include": "musicinfo",
        "search": q,
        "audioformat": "mp32",  # Jamendo даёт playable stream
    }

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get("https://api.jamendo.com/v3.0/tracks/", params=params) as r:
            if r.status != 200:
                return []
            data = await r.json()

    out: list[SearchTrack] = []
    for item in (data.get("results") or [])[:limit]:
        title = str(item.get("name") or "").strip()
        artist = str(item.get("artist_name") or "").strip() or None
        page = str(item.get("shareurl") or "").strip() or None
        audio = str(item.get("audio") or "").strip() or None  # direct stream url
        art = str(item.get("image") or "").strip() or None
        if not title or not audio or not audio.startswith("http"):
            continue
        # Jamendo иногда отдаёт http — чинем на https
        if audio.startswith("http://"):
            audio = "https://" + audio[len("http://"):]
        out.append(SearchTrack(
            source="jamendo",
            title=title,
            artist=artist,
            url=page,
            preview_url=audio,  # это FULL stream
            artwork_url=art,
        ))
    return out


async def archive_search(query: str, *, limit: int = 8) -> list[SearchTrack]:
    # Internet Archive: берём items через advancedsearch, потом metadata -> files -> mp3/ogg
    q = (query or "").strip()
    if not q:
        return []

    params = {
        "q": f'title:("{q}") AND mediatype:(audio)',
        "fl[]": ["identifier", "title"],
        "rows": str(max(1, min(limit, 25))),
        "page": "1",
        "output": "json",
    }

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
        async with s.get("https://archive.org/advancedsearch.php", params=params) as r:
            if r.status != 200:
                return []
            data = await r.json()

        docs = (data.get("response") or {}).get("docs") or []
        out: list[SearchTrack] = []

        for d in docs[:limit]:
            ident = str(d.get("identifier") or "").strip()
            if not ident:
                continue

            async with s.get(f"https://archive.org/metadata/{ident}") as r2:
                if r2.status != 200:
                    continue
                meta = await r2.json()

            files = meta.get("files") or []
            # ищем mp3/ogg
            best: Optional[str] = None
            for f in files:
                name = str(f.get("name") or "")
                if not name:
                    continue
                if name.lower().endswith((".mp3", ".ogg", ".m4a", ".aac", ".wav")):
                    best = f"https://archive.org/download/{ident}/{name}"
                    break

            if not best:
                continue

            title = str(d.get("title") or ident).strip()
            out.append(SearchTrack(
                source="archive",
                title=title,
                artist=None,
                url=f"https://archive.org/details/{ident}",
                preview_url=best,  # full file
                artwork_url=None,
            ))

        return out


async def search_tracks(query: str, *, limit: int = 10) -> list[SearchTrack]:
    q = (query or "").strip()
    if not q:
        return []

    # 1) Если дали прямую ссылку на аудио — это сразу “full”
    if is_direct_audio_url(q):
        name = q.split("/")[-1].split("?")[0]
        return [SearchTrack(source="link", title=name or "Audio link", artist=None, url=q, preview_url=q, artwork_url=None)]

    # 2) Внешние full источники (легальные)
    jam = await jamendo_search(q, limit=limit)
    arc = await archive_search(q, limit=limit)

    # 3) iTunes preview (в конце, как fallback)
    it = await itunes_search(q, limit=limit)

    merged = jam + arc + it
    return merged[:limit]
