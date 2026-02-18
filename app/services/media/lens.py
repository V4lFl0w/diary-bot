from __future__ import annotations

# app/services/assistant.py
import re


def _lens_clean_candidate(s: str) -> str:
    """
    Clean Lens candidate line into something TMDb-friendly BEFORE normalize/sanitize.
    Examples:
      '✨ Film: Deep Water(2022) ...' -> 'Deep Water 2022'
      'Ben Affleck in underwear in the new film Deep Water (2022)' -> 'Deep Water 2022 Ben Affleck'
    """
    s = (s or "").strip()
    if not s:
        return ""

    # remove common prefixes
    s = re.sub(r"(?i)^\s*(✨\s*)?(film|movie|фильм|кино)\s*:\s*", "", s).strip()

    # remove platform-y suffixes
    s = re.sub(r"(?i)\b(official)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Extract Title (YEAR) preference
    m = re.search(r"(.+?)\s*\(\s*(19\d{2}|20\d{2})\s*\)", s)
    if m:
        title = (m.group(1) or "").strip(" -–—,:;")
        year = m.group(2)
        # keep short title + year only
        if title:
            out = f"{title} {year}".strip()
            # слишком описательно — режем
            if len(out.split()) > 8:
                return ""
            return out

    # If YEAR is present elsewhere, keep it (but avoid giant strings)
    m2 = re.search(r"\b(19\d{2}|20\d{2})\b", s)
    year = m2.group(1) if m2 else ""

    # If it contains "new film <Title>" pattern
    m3 = re.search(r"(?i)\bfilm\b\s+([A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){0,5})", s)
    title2 = (m3.group(1) or "").strip() if m3 else ""

    # shorten aggressively
    s2 = s
    if len(s2) > 90:
        s2 = s2[:90].rsplit(" ", 1)[0].strip()

    # try to keep likely title-ish chunk: first 2–6 TitleCased words
    tokens = re.findall(r"[A-Za-z0-9'’\-]+", s2)

    base = ""
    if title2 and len(title2.split()) <= 6:
        base = title2
    else:
        base = " ".join(tokens[:6])

    base = base.strip(" -–—,:;")
    if year and year not in base and len(base) <= 45:
        base = f"{base} {year}".strip()

    if len(base.split()) > 8:
        return ""

    return base.strip()


def _lens_bad_candidate(s: str) -> bool:
    """
    Lens часто возвращает:
      - каналы/эдиты/муз клипы
      - платформы (YouTube/TikTok/Instagram)
      - CTA (subscribe/like/share)
    Это не тайтлы.
    """
    sl = (s or "").lower()
    if not sl:
        return True

    bad = (
        "edits",
        "edit",
        "channel",
        "youtube",
        "tiktok",
        "instagram",
        "reels",
        "shorts",
        "subscribe",
        "like",
        "share",
        "official",
        "music video",
        "mood music",
        "compilation",
        "fanmade",
        "trailer",
        "clip",
        "status",
        "threads",
        "funny",
        "moments",
        "best",
        "scenes",
        "scene",
        "memes",
        "meme",
        "interview",
        "actor",
        "cast",
        "behind the scenes",
        "bts",
        "short",
    )
    if any(b in sl for b in bad):
        return True

    # too generic single words
    if sl.strip() in {"movie", "film", "series", "tv", "deep", "nothing"}:
        return True

    # чистый год — не тайтл
    if re.fullmatch(r"(18|19|20)\d{2}", sl.strip()):
        return True

    # 1–2 слова латиницей без года часто мусор (Chuck Keep / Lovers)
    if re.fullmatch(r"[a-z]+\s*[a-z]*", sl.strip()) and not re.search(r"\b(19\d{2}|20\d{2})\b", sl):
        if len(sl.strip().split()) <= 2:
            return True

    # looks like account/name rather than title (very short + non-title)
    if len(sl.strip()) <= 3:
        return True

    return False


def _lens_score_candidate(raw: str) -> int:
    """
    Higher is better.
    Prefer:
      - Title (YEAR) or Title YEAR
      - 1–6 words, not generic
      - contains some TitleCase / letters
    Penalize:
      - long sentences
      - platform words / edits
      - starts with movie/film
    """
    s = (raw or "").strip()
    if not s:
        return -999

    if _lens_bad_candidate(s):
        return -500

    score = 0

    # explicit (YEAR)
    if re.search(r"\(\s*(19\d{2}|20\d{2})\s*\)", s):
        score += 40
    if re.search(r"\b(19\d{2}|20\d{2})\b", s):
        score += 25

    # бонус: русские/укр буквы часто означают тайтл
    if re.search(r"[А-Яа-яЁёІіЇїЄє]", s):
        score += 12

    # бонус: явный формат Title (YEAR)
    if re.search(r"\(\s*(19\d{2}|20\d{2})\s*\)", s):
        score += 20

    # бонус: год + >= 2 слов
    words2 = re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄє0-9'’\-]+", s)
    if re.search(r"\b(19\d{2}|20\d{2})\b", s) and len(words2) >= 2:
        score += 15

    # word count preference
    words = re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄє0-9'’\-]+", s)
    wc = len(words)
    if 1 <= wc <= 6:
        score += 80
    elif wc <= 10:
        score += 35
    else:
        score -= 60

    # starts with movie/film is suspicious
    if re.match(r"(?i)^\s*(movie|film|фильм|кино)\b", s):
        score -= 80

    # length penalty
    L = len(s)
    if L <= 35:
        score += 35
    elif L <= 60:
        score += 10
    else:
        score -= L - 60

    # must contain letters
    if not any(ch.isalpha() for ch in s):
        score -= 120

    return score


def _pick_best_lens_candidates(lens_cands: list[str], *, limit: int = 12) -> list[str]:
    """
    Returns candidates ordered by best-first.
    Includes cleaned variants; keeps uniqueness.
    """
    cands = [c for c in (lens_cands or []) if (c or "").strip()]
    ranked = sorted(cands, key=_lens_score_candidate, reverse=True)

    out: list[str] = []
    seen = set()

    for raw in ranked:
        if len(out) >= limit:
            break
        # use raw first, then cleaned
        for cand in (raw, _lens_clean_candidate(raw)):
            cand = (cand or "").strip()
            if not cand:
                continue
            key = cand.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)

    return out[:limit]


# --- /FlowPatch: lens_rank_v3 ---
