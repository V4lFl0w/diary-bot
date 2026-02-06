from __future__ import annotations

import os
import re
import asyncio
from dataclasses import dataclass
from typing import List

import aiohttp


@dataclass
class TrackResult:
    title: str
    artist: str = ""
    source: str = ""
    url: str = ""  # страница/источник
    audio_url: str = ""  # ПРЯМАЯ ссылка на full аудио (mp3/m4a/ogg/aac/wav)

    def display_title(self) -> str:
        t = (self.title or "").strip() or "Track"
        a = (self.artist or "").strip()
        return f"{t} — {a}" if a else t


_AUDIO_EXT_RE = re.compile(r"\.(mp3|m4a|ogg|aac|wav)(\?|$)", re.IGNORECASE)


def _is_audio_url(u: str) -> bool:
    if not u:
        return False
    u = u.strip()
    if not (u.startswith("https://") or u.startswith("http://")):
        return False
    return bool(_AUDIO_EXT_RE.search(u))


async def _head_is_audio(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as r:
            ct = (r.headers.get("Content-Type") or "").lower()
            if "audio/" in ct:
                return True
            if "text/html" in ct:
                return False
            return _is_audio_url(str(r.url))
    except Exception:
        return _is_audio_url(url)


# ---------------- JAMENDO (FULL TRACKS) ----------------
# Jamendo требует client_id. Без него вернём пусто.
JAMENDO_CLIENT_ID = os.getenv("JAMENDO_CLIENT_ID", "").strip()


async def _jamendo_search(q: str, limit: int) -> List[TrackResult]:
    if not JAMENDO_CLIENT_ID:
        return []
    url = "https://api.jamendo.com/v3.0/tracks/"
    params = {
        "client_id": JAMENDO_CLIENT_ID,
        "format": "json",
        "limit": str(limit),
        "search": q,
        "audioformat": "mp32",  # mp3
        "include": "musicinfo",
    }
    out: List[TrackResult] = []
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(url, params=params) as r:
            if r.status != 200:
                return []
            js = await r.json()
            items = js.get("results") or []
            for it in items:
                name = (it.get("name") or "").strip()
                artist = (it.get("artist_name") or "").strip()
                audio = (it.get("audio") or "").strip()  # direct mp3
                share = (it.get("shareurl") or "").strip()
                if not name or not audio:
                    continue
                if not _is_audio_url(audio):
                    continue
                out.append(
                    TrackResult(title=name, artist=artist, source="jamendo", url=share or audio, audio_url=audio)
                )
                if len(out) >= limit:
                    break
    return out


# ---------------- INTERNET ARCHIVE (FULL FILES) ----------------
async def _archive_search(q: str, limit: int) -> List[TrackResult]:
    search_url = "https://archive.org/advancedsearch.php"
    params = {
        "q": f"({q}) AND mediatype:(audio)",
        "fl[]": ["identifier", "title", "creator"],
        "rows": str(max(limit, 10)),
        "page": "1",
        "output": "json",
    }

    timeout = aiohttp.ClientTimeout(total=15)
    out: List[TrackResult] = []
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(search_url, params=params) as r:
            if r.status != 200:
                return []
            js = await r.json()
            docs = (js.get("response") or {}).get("docs") or []
            for d in docs:
                ident = (d.get("identifier") or "").strip()
                title = (d.get("title") or "").strip() or "Track"
                creator = (d.get("creator") or "").strip()
                if not ident:
                    continue

                meta_url = f"https://archive.org/metadata/{ident}"
                try:
                    async with s.get(meta_url) as mr:
                        if mr.status != 200:
                            continue
                        mj = await mr.json()
                except Exception:
                    continue

                files = mj.get("files") or []
                best_audio = ""
                for f in files:
                    name = (f.get("name") or "").strip()
                    if not name:
                        continue
                    if not _AUDIO_EXT_RE.search(name):
                        continue
                    low = name.lower()
                    if "64kb" in low or "vbr" in low:
                        continue
                    best_audio = f"https://archive.org/download/{ident}/{name}"
                    if _is_audio_url(best_audio):
                        break

                if not best_audio:
                    continue

                ok = await _head_is_audio(s, best_audio)
                if not ok:
                    continue

                page = f"https://archive.org/details/{ident}"
                out.append(TrackResult(title=title, artist=creator, source="archive", url=page, audio_url=best_audio))
                if len(out) >= limit:
                    break

    return out


async def search_tracks(q: str, limit: int = 10) -> List[TrackResult]:
    q = (q or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit or 10), 10))

    tasks = [
        _jamendo_search(q, limit=limit),
        _archive_search(q, limit=limit),
    ]
    res = await asyncio.gather(*tasks, return_exceptions=True)

    merged: List[TrackResult] = []
    for part in res:
        if isinstance(part, Exception):
            continue
        for t in part:
            if t and t.audio_url and _is_audio_url(t.audio_url):
                merged.append(t)

    seen = set()
    uniq: List[TrackResult] = []
    for t in merged:
        key = t.audio_url.strip()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)

    return uniq[:limit]
