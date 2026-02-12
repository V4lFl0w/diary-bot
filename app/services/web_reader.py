from __future__ import annotations

import re
from html import unescape
from typing import Optional, Tuple

import httpx


_URL_RE = re.compile(r"https?://\S+", re.I)

# characters to trim from the end of detected URL
URL_RSTRIP_CHARS = ").,;!\"'”’"


def extract_first_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = _URL_RE.search(text)
    if not m:
        return None
    url = m.group(0).strip().rstrip(URL_RSTRIP_CHARS)
    return url or None


def _strip_tags(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    txt = re.sub(r"(?is)<[^>]+>", " ", html)
    txt = unescape(txt)
    txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _extract_title(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if not m:
        return ""
    t = unescape(m.group(1) or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t[:180]


def _extract_best_text(html: str) -> str:
    m = re.search(r"(?is)<article[^>]*>(.*?)</article>", html)
    if m:
        return _strip_tags(m.group(1) or "")
    return _strip_tags(html)


async def fetch_page_text(
    url: str,
    *,
    timeout: float = 18.0,
    max_chars: int = 12000,
) -> Tuple[str, str]:
    if not url:
        return ("", "")

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "DiaryBotWebReader/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        ) as client:
            r = await client.get(url)
            if r.status_code >= 400:
                return ("", "")
            html = r.text or ""
    except Exception:
        return ("", "")

    title = _extract_title(html)
    text = _extract_best_text(html)

    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].strip()

    return (title, text)
