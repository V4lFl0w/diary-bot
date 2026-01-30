from __future__ import annotations
import re

from app.services.media_text import SXXEYY_RE as _SXXEYY_RE
from app.services.media_text import YEAR_RE as _YEAR_RE



# Generic captions / non-informative titles that should be treated as "bad candidates"
_GENERIC_MEDIA_CAPTIONS: set[str] = {
    "фильм", "кино", "сериал", "мультфильм", "мульт", "аниме", "мультик",
    "документальный", "докфильм", "док", "шоу", "тв", "телешоу",
    "movie", "film", "series", "tv", "tv series", "show", "cartoon", "anime",
}

def _clean_tmdb_query(q: str) -> str:
    t = (q or "").strip()

    # убираем префиксы
    t = re.sub(r"^(название\s+(фильма|сериала)\s*:\s*)", "", t, flags=re.I)
    t = re.sub(r"^(title\s*:\s*)", "", t, flags=re.I)

    # убираем кавычки-ёлочки и обычные
    t = t.replace("«", "").replace("»", "").replace('"', "").replace("“", "").replace("”", "")

    # убираем год в скобках
    t = re.sub(r"\(\s*\d{4}\s*\)\s*$", "", t)

    # финальная нормализация пробелов
    t = " ".join(t.split())
    return t

def _parse_media_hints(text: str) -> dict:
    t_raw = (text or "").strip()
    t = t_raw.lower()

    year = None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", t)
    if m:
        year = m.group(1)

    kind = None
    if "сериал" in t:
        kind = "tv"
    elif "фильм" in t or "кино" in t:
        kind = "movie"

    # актёры: поддержка кириллицы + латиницы
    cast_ru = re.findall(r"\b[А-ЯЁІЇЄ][а-яёіїє]+ [А-ЯЁІЇЄ][а-яёіїє]+\b", t_raw)
    cast_en = re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", t_raw)
    cast = (cast_ru + cast_en)[:2]

    keywords = re.sub(r"[^a-zA-Zа-яА-ЯёЁІіЇїЄє0-9 ]", " ", t_raw)
    keywords = " ".join(w for w in keywords.split() if len(w) > 3)[:80]

    return {"year": year, "kind": kind, "cast": cast, "keywords": keywords.strip()}

def _clean_query_for_tmdb(q: str) -> str:
    """
    Clean noisy captions/hashtags/emojis before sending to TMDb.
    Keeps letters/digits/basic punctuation, strips hashtags and weird symbols.
    """
    q = (q or "").strip()
    if not q:
        return ""
    # remove hashtags like #anadearmas
    q = re.sub(r"#\w+", " ", q, flags=re.UNICODE)
    # remove excessive punctuation/emojis; keep words, spaces, dash and apostrophe
    q = re.sub(r"[^\w\s\-']", " ", q, flags=re.UNICODE)
    # collapse spaces
    q = re.sub(r"\s+", " ", q, flags=re.UNICODE).strip()
    # avoid too-short junk
    return q

def _looks_like_freeform_media_query(q: str) -> bool:
    ql = (q or "").lower().strip()
    if not ql:
        return False
    bad_words = (
        "сцена",
        "момент",
        "в конце",
        "в начале",
        "актёр",
        "актер",
        "в очках",
        "в костюмах",
        "про",
        "где",
        "когда",
        "как называется",
        "помогите найти",
        "полиция",
        "женщина",
        "мужчина",
        "сериал",
        "фильм",
        "серия",
        "эпизод",
    )
    if any(w in ql for w in bad_words):
        return True
    if len(ql) >= 45 or ql.count(" ") >= 6:
        return True
    return False

def _tmdb_sanitize_query(q: str) -> str:
    q = (q or "").strip()
    q = re.sub(r"\s+", " ", q)

    # remove common RU "scene" words and punctuation clutter
    q = re.sub(
        r"(?i)\b(сцена|что происходит|что происходит в сцене|факт|факта|актер|актёр|страна|язык|мем|meme)\b.*$",
        "",
        q,
    ).strip()
    q = re.sub(r"[\"“”‘’]+", "", q).strip()

    # Keep only: title-ish part + optional year
    year = None
    m = _YEAR_RE.search(q)
    if m:
        year = m.group(1)

    # If query has SxxEyy, transform into short canonical form
    m2 = _SXXEYY_RE.search(q)
    if m2:
        s = int(m2.group(1))
        e = int(m2.group(2))
        # remove SxxEyy tokens from base title
        base = _SXXEYY_RE.sub("", q).strip()
        base = re.sub(r"\s+", " ", base).strip(" -–—,:;")
        if base:
            return f"{base} S{s}E{e}"

    # Hard length cap (TMDB works best with short queries)
    q = q.strip(" -–—,:;")
    if year and year not in q:
        # don't append year blindly if it bloats; only if short
        if len(q) <= 40:
            q = f"{q} {year}"

    if len(q) > 60:
        q = q[:60].rsplit(" ", 1)[0].strip()

    return q

def _good_tmdb_cand(q: str) -> bool:
    q = (q or "").strip()
    if not q:
        return False

    # hard caps
    if len(q) > 70:
        return False

    ql = q.lower()

    # must contain letters
    if not any(ch.isalpha() for ch in q):
        return False

    # too many words => not a title
    if q.count(" ") >= 7:
        return False

    # reject short adjective-only phrases (often model prose, not a title)
    if (
        ql.startswith("легендарн")
        or ql.startswith("российск")
        or ql.startswith("советск")
    ) and q.count(" ") <= 1:
        return False

    # reject obvious list/headline queries
    bad = (
        "ведомост",
        "топ",
        "лучших",
        "подбор",
        "подборк",
        "список",
        "15 ",
        "10 ",
        "20 ",
    )
    if any(b in ql for b in bad):
        return False

    return True

def _is_generic_media_caption(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    t = re.sub(r"\s+", " ", t).strip()

    if t in _GENERIC_MEDIA_CAPTIONS:
        return True

    # legacy/common phrases (keep behavior, avoid unreachable)
    if t in {
        "откуда кадр",
        "откуда кадр?",
        "что за фильм",
        "что за фильм?",
        "что за сериал",
        "что за сериал?",
        "что за мультик",
        "что за мультик?",
        "как называется",
        "как называется?",
        "как называется фильм",
        "как называется сериал",
    }:
        return True

    return False

def _extract_media_kind_marker(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^__MEDIA_KIND__:(voice|video|video_note)\b", t)
    return m.group(1) if m else ""

def _is_asking_for_title(text: str) -> bool:
    t = (text or "").strip().lower()
    pats = (
        "какое название",
        "как называется",
        "название фильма",
        "название у фильма",
        "как называется фильм",
        "как называется этот фильм",
        "что за название",
    )
    return any(x in t for x in pats)

def _is_affirmation(text: str) -> bool:
    t = (text or "").strip().lower()
    return (
        bool(re.match(r"^(да|ага|угу)\b", t))
        or t.startswith("это ")
        or t.startswith("да,")
        or t.startswith("да ")
    )

def _clean_media_search_query(text: str) -> str:
    """
    Aggressive cleanup of user description before sending to TMDB.
    Removes conversational junk and keeps only meaningful keywords.
    """
    t = (text or "").lower()

    junk_phrases = [
        "название фильма", "название сериала", "как называется", "что за фильм",
        "что за сериал", "подскажи фильм", "подскажите фильм",
        "в главной роли", "главную роль играет", "где играет",
        "помоги найти фильм", "ищу фильм", "ищу сериал",
        "кто знает фильм", "кто знает сериал"
    ]

    for j in junk_phrases:
        t = t.replace(j, " ")

    # remove punctuation
    t = re.sub(r"[^\w\s]", " ", t)

    # remove short words
    words = [w for w in t.split() if len(w) > 2]

    # keep first 8 meaningful words max
    return " ".join(words[:8])

def _normalize_tmdb_query(q: str, *, max_len: int = 140) -> str:
    """
    TMDb search query must be short and clean.
    - collapse whitespace/newlines
    - strip quotes/markdown-ish noise
    - hard truncate
    """
    q = (q or "").strip()
    if not q:
        return ""

    # remove "SEARCH_QUERY:" if user pasted it
    q = re.sub(r"(?im)^\s*SEARCH_QUERY:\s*", "", q).strip()

    # collapse whitespace/newlines
    q = re.sub(r"\s+", " ", q).strip()

    # avoid super-long paragraphs (TMDb can return 400)
    if len(q) > max_len:
        q = q[:max_len].rsplit(" ", 1)[0].strip()

    # remove leading generic junk
    q = re.sub(r"^(что за|как называется)\s+", "", q, flags=re.I).strip()
    q = re.sub(r"^(фильм|сериал|мульт(ик)?|кино)\s+", "", q, flags=re.I).strip()
    return q

BAD_MEDIA_QUERY_WORDS = {
    "news",
    "sport",
    "sports",
    "channel",
    "subscribe",
    "live",
    "official",
    "trailer",
    "shorts",
    "tiktok",
    "instagram",
    "reels",
    "главные",
    "новости",
    "канал",
    "подпишись",
    "подписаться",
    "смотрите",
    "запись",
    "обзор",
    "интервью",
    "edit",
    "edits",
    "compilation",
    "fanmade",
    "youtube",
    "music",
    "video",
}

GENERIC_TITLE_WORDS = {
    # EN
    "man",
    "men",
    "woman",
    "women",
    "boy",
    "girl",
    "guy",
    "people",
    "person",
    "kid",
    "kids",
    "movie",
    "film",
    "series",
    "tv",
    "show",
    "clip",
    "scene",
    "video",
    "shorts",
    "trailer",
    # RU/UA
    "мужчина",
    "мужчины",
    "женщина",
    "женщины",
    "парень",
    "девушка",
    "люди",
    "человек",
    "ребенок",
    "ребёнок",
    "фильм",
    "кино",
    "сериал",
    "мульт",
    "мультик",
    "кадр",
    "сцена",
    "момент",
    "видео",
    "шортс",
    "трейлер",
}

_MEDIA_LEADING_NOISE = (
    "название фильма",
    "название сериала",
    "название мультика",
    "как называется",
    "что за фильм",
    "что за сериал",
    "что за мультик",
    "какой фильм",
    "какой сериал",
    "какой мультик",
    "какой кинчик",
    "какой кенчик",
    "откуда этот отрывок",
    "что за хуйня",
    "че за хуйня",
    "шо за хуйня",
)

_MEDIA_NOISE_REGEX = [
    r"\bв главной роли\b",
    r"\bглавная роль\b",
    r"\bактер(ы|а)?\b",
    r"\bактриса\b",
    r"\bкто играет\b",
    r"\bнужно название\b",
    r"\bподскажи\b",
    r"\bпожалуйста\b",
]

def _media_clean_user_query(q: str) -> str:
    q0 = (q or "").strip()
    if not q0:
        return ""

    ql = q0.lower().strip()

    # remove leading canned phrases
    for p in _MEDIA_LEADING_NOISE:
        ql = ql.replace(p, " ")

    # remove other noise patterns
    for pat in _MEDIA_NOISE_REGEX:
        ql = re.sub(pat, " ", ql, flags=re.IGNORECASE)

    # normalize punctuation
    ql = re.sub(r"[“”\"'`]", " ", ql)
    ql = re.sub(r"[,.;:!?()\{\}<>/\\\\|_+=~\-]+", " ", ql)
    ql = re.sub(r"\s{2,}", " ", ql).strip()

    # fallback to original if we over-cleaned
    return ql if ql else q0

def _is_bad_media_query(q: str) -> bool:
    ql = (q or "").lower().strip()
    if not ql:
        return True

    # слишком короткий мусор
    if len(ql) < 3:
        return True

    words = ql.split()

    # ✅ одно слово — НЕ всегда мусор: допускаем короткие/брендовые тайтлы
    # но режем "news", "sport", "trailer", "subscribe" и т.п.
    if len(words) == 1:
        w = words[0]
        if w in GENERIC_TITLE_WORDS:
            return True
        # цифро-мусор / слишком мало букв
        letters = sum(ch.isalpha() for ch in w)
        digits = sum(ch.isdigit() for ch in w)
        if letters < 3:
            return True
        if digits > 0 and letters < 4:
            return True
        # стоп-слова
        for sw in BAD_MEDIA_QUERY_WORDS:
            if sw in w:
                return True
        return False

    # содержит стоп-слова
    for sw in BAD_MEDIA_QUERY_WORDS:
        if sw in ql:
            return True

    # слишком много цифр
    if sum(c.isdigit() for c in ql) > len(ql) * 0.4:
        return True

    return False


# === SOCIAL FILTER ===
_BAD_WORDS = ("instagram", "tiktok", "facebook", "vk.com", "@", "profile")

def _is_social_garbage(title: str) -> bool:
    tl = title.lower()
    return any(w in tl for w in _BAD_WORDS)
# === END FILTER ===
