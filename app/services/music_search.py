from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import aiohttp


@dataclass(frozen=True)
class SearchTrack:
    source: str
    title: str
    artist: str | None
    url: str | None
    preview_url: str | None
    artwork_url: str | None


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
            data: dict[str, Any] = await r.json()

    out: list[SearchTrack] = []
    for item in (data.get("results") or [])[:limit]:
        title = str(item.get("trackName") or "").strip()
        artist = str(item.get("artistName") or "").strip() or None
        url = str(item.get("trackViewUrl") or "").strip() or None
        preview = str(item.get("previewUrl") or "").strip() or None
        art = str(item.get("artworkUrl100") or "").strip() or None
        if not title:
            continue
        out.append(
            SearchTrack(
                source="itunes",
                title=title,
                artist=artist,
                url=url,
                preview_url=preview,
                artwork_url=art,
            )
        )
    return out
