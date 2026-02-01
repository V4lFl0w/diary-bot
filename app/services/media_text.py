# app/services/media_text.py
from __future__ import annotations

import re
from typing import Iterable, List

# Canonical regexes (single source of truth)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
SXXEYY_RE = re.compile(r"\bS(\d{1,2})\s*E(\d{1,3})\b", re.IGNORECASE)

_EP_TOKENS_RE = re.compile(
    r"\b(s\d{1,2}\s*e\d{1,3}|season\s*\d{1,2}|episode\s*\d{1,3})\b",
    re.IGNORECASE,
)

_WS_RE = re.compile(r"\s+")
_BAD_CHARS_RE = re.compile(r"[\t\r\n]+")


def norm(q: str) -> str:
    """Soft normalize for web queries / candidate strings."""
    if not q:
        return ""
    q = _BAD_CHARS_RE.sub(" ", q)
    q = q.strip()
    q = _WS_RE.sub(" ", q)
    return q


def strip_episode_tokens(q: str) -> str:
    """Remove SxxEyy/season/episode tokens to get a 'title core'."""
    q = norm(q)
    if not q:
        return ""
    q2 = _EP_TOKENS_RE.sub("", q)
    q2 = _WS_RE.sub(" ", q2).strip(" -—:|")
    return q2.strip()


def dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        x = norm(x)
        if not x:
            continue
        k = x.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out
