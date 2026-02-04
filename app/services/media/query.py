from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional
import re as _re_tmdb_clean

from app.services.media_text import SXXEYY_RE as _SXXEYY_RE
from app.services.media_text import YEAR_RE as _YEAR_RE

# -----------------------------
# 1) базовые нормализации
# -----------------------------

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[“”\"'`]+")
_TRASH_RE = re.compile(r"[^\w\s\-]", flags=re.UNICODE)
_TMDB_YEAR_RE_LEGACY = re.compile(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)")

# описательные слова, которые НЕ должны доминировать в query
_MEDIA_LEADING_NOISE = (
    "название фильма",
    "название сериала",
    "название мультика",
    "как называется",
    "что за фильм",
    "что за сериал",
    "что за мультик",
    "откуда кадр",
    "по кадру",
    "помоги найти",
    "подскажи",
    "пожалуйста",
)

_MEDIA_NOISE_REGEX = [
    r"\bв главной роли\b",
    r"\bглавная роль\b",
    r"\bактер(ы|а)?\b",
    r"\bактриса\b",
    r"\bкто играет\b",
    r"\bчто происходит\b",
    r"\bв какой серии\b",
    r"\bв каком сезоне\b",
]

# слова, которые часто встречаются в “заголовках” и не помогают идентификации
GENERIC_TITLE_WORDS = {
    "movie",
    "film",
    "series",
    "tv",
    "show",
    "episode",
    "season",
    "trailer",
    "фильм",
    "кино",
    "сериал",
    "мульт",
    "мультик",
    "аниме",
    "кадр",
    "сцена",
    "эпизод",
    "серия",
    "сезон",
    "название",
    "как",
    "называется",
    "что",
    "за",
    "откуда",
}

# --- tmdb candidate stoplist (json keys + generic descriptors) ---
_BAD_TMDB_CANDIDATES = {
    # json keys / system labels
    "actors",
    "actor",
    "actress",
    "cast",
    "characters",
    "character",
    "dialogue",
    "dialog",
    "keywords",
    "title_hints",
    "search_query",
    "query",
    "prompt",
    "description",
    "summary",
    "scene",
    "plot",
    "synopsis",
    "genre",
    "genres",
    # common useless descriptors from lens/vision
    "male characters",
    "female characters",
    "dark scene",
    "dark",
    "a dark scene",
    "a scene",
    "movie scene",
    "tv show",
    "tv series",
}


def _is_bad_tmdb_candidate(q: str) -> bool:
    q = _norm(q)
    if not q:
        return True

    low = q.lower().strip()

    # 1) прямые стоп-слова
    if low in _BAD_TMDB_CANDIDATES:
        return True

    # 2) если это по сути "общие слова" (все токены — из GENERIC_TITLE_WORDS)
    toks = [t for t in re.split(r"\s+", low) if t]
    if toks and all(t in GENERIC_TITLE_WORDS for t in toks):
        return True

    # 3) короткий мусор
    if len(low) <= 2:
        return True

    return False


@dataclass(frozen=True)
class MediaHints:
    year: Optional[str] = None
    s: Optional[int] = None
    e: Optional[int] = None


def _norm(s: str) -> str:
    s = (s or "").strip()
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _extract_hints(text: str) -> MediaHints:
    t = (text or "").strip()
    year = None
    m = _YEAR_RE.search(t)
    if m:
        year = m.group(1)

    s = e = None
    m2 = _SXXEYY_RE.search(t)
    if m2:
        try:
            s = int(m2.group(1))
            e = int(m2.group(2))
        except Exception:
            s = e = None

    return MediaHints(year=year, s=s, e=e)


def clean_user_text_for_media(text: str) -> str:
    """
    Убираем “разговорный мусор”, оставляя смысловые слова.
    Это НЕ финальный tmdb query, это подготовка.
    """
    q0 = _norm(text)
    if not q0:
        return ""

    ql = q0.lower()

    # remove canned phrases
    for p in _MEDIA_LEADING_NOISE:
        ql = ql.replace(p, " ")

    # remove noise patterns
    for pat in _MEDIA_NOISE_REGEX:
        ql = re.sub(pat, " ", ql, flags=re.IGNORECASE)

    # keep letters/digits/- and spaces
    ql = _TRASH_RE.sub(" ", ql)
    ql = _WS_RE.sub(" ", ql).strip()

    return ql if ql else q0


def tmdb_query_compact(text: str, *, max_len: int = 60) -> str:
    """
    Делает короткий “title-ish” запрос для TMDB.
    НЕ пытается превращать описание сцены в тайтл — просто сжимает.
    """
    q = _norm(text)
    if not q:
        return ""

    q = re.sub(r"(?im)^\s*SEARCH_QUERY:\s*", "", q).strip()

    # убрать кавычки/мусор
    q = _PUNCT_RE.sub("", q)
    q = _WS_RE.sub(" ", q).strip()

    # убрать префиксы
    q = re.sub(r"^(что за|как называется)\s+", "", q, flags=re.I).strip()
    q = re.sub(r"^(фильм|сериал|мульт(ик)?|кино)\s+", "", q, flags=re.I).strip()

    # hard truncate
    if len(q) > max_len:
        q = q[:max_len].rsplit(" ", 1)[0].strip()

    return q


def is_bad_tmdb_query(q: str) -> bool:
    """
    Отсекаем явный мусор, но НЕ режем короткие тайтлы.
    """
    q = _norm(q)
    if not q:
        return True
    if len(q) < 3:
        return True

    # если 1 слово — допустимо, если там есть буквы
    words = q.split()
    if len(words) == 1:
        w = words[0]
        letters = sum(ch.isalpha() for ch in w)
        if letters < 3:
            return True
        return False

    # слишком много слов => обычно описание сцены, не тайтл
    if q.count(" ") >= 7:
        return True

    return False


# -----------------------------
# 2) сильное сравнение (без rapidfuzz)
# -----------------------------


def _token_set(s: str) -> set[str]:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w\s\-]", " ", s, flags=re.UNICODE)
    s = _WS_RE.sub(" ", s).strip()
    return {w for w in s.split() if len(w) >= 3}


def is_strong_title_match(query: str, title: str) -> bool:
    """
    Работает и для тайтлов (Kung Fu Panda) и для имён (Makarov Kuznetsova),
    но НЕ считает длинное описание сцены как “точное совпадение”.
    """
    q = _norm(query)
    t = _norm(title)
    if not q or not t:
        return False

    ql = q.lower()
    tl = t.lower()

    # “in” проверяем только на коротких запросах
    if len(ql) <= 35 and (ql == tl or ql in tl or tl in ql):
        return True

    tq = _token_set(ql)
    tt = _token_set(tl)
    if not tq or not tt:
        return False

    inter = len(tq & tt)

    # Для коротких сущностей (2 слова фамилия/имя) — требуем 2 токена
    if len(tt) <= 3:
        return inter >= 2

    # для тайтлов — 2+ токена и заметная доля совпадений
    j = inter / max(1, len(tq | tt))
    return inter >= 2 and j >= 0.35


def reorder_items_by_strong_hit(
    items: list[dict],
    *,
    strong_queries: Iterable[str],
) -> list[dict]:
    """
    Если в TMDB items уже есть “правильный” тайтл/имя — поднимаем его наверх,
    чтобы дальше форматтер/threshold не “затирал” и не писал “уточните”.
    """
    if not items:
        return items

    sq = [tmdb_query_compact(x) for x in (strong_queries or [])]
    sq = [x for x in sq if x and not is_bad_tmdb_query(x)]
    if not sq:
        return items

    strong = []
    rest = []

    for it in items:
        title = (it.get("title") or it.get("name") or "").strip()
        orig = (it.get("original_title") or it.get("original_name") or "").strip()
        any_hit = False
        for q in sq:
            if is_strong_title_match(q, title) or (orig and is_strong_title_match(q, orig)):
                any_hit = True
                break
        (strong if any_hit else rest).append(it)

    return strong + rest


def build_tmdb_queries(
    user_text: str,
    *,
    extra_candidates: list[str] | None = None,
    max_queries: int = 8,
) -> tuple[list[str], MediaHints]:
    """
    Главная функция:
    - user_text (описание сцены/что угодно)
    - extra_candidates (то, что пришло из Lens/Wiki/Serp)
    Возвращает список коротких tmdb-queries (самые перспективные первыми)
    + хинты (год, s/e)
    """
    raw = _norm(user_text)
    hints = _extract_hints(raw)

    # 1) сырая чистка текста (для генерации коротких)
    cleaned = clean_user_text_for_media(raw)

    # 2) компактный вариант
    compact = tmdb_query_compact(cleaned)

    # 3) список “внешних” кандидатов (Lens/Wiki/Serp)
    extras = [tmdb_query_compact(x) for x in (extra_candidates or [])]
    extras = [x for x in extras if x and not is_bad_tmdb_query(x) and not _is_bad_tmdb_candidate(x)]

    # 4) если есть SxxEyy и есть базовый компакт — делаем уточнённые
    out: list[str] = []
    if hints.s and hints.e and compact:
        out.append(f"{compact} S{hints.s}E{hints.e}")
        out.append(f"{compact} season {hints.s} episode {hints.e}")
        out.append(f"{compact} episode {hints.e}")

    # 5) потом внешние кандидаты (они часто самые “точные”)
    out.extend(extras)

    # 6) потом компакт от текста
    if compact:
        out.append(compact)

    # 7) если есть год и запрос короткий — добавим год (но не раздуваем)
    if hints.year:
        with_year = []
        for q in out:
            if hints.year in q:
                continue
            if len(q) <= 40:
                with_year.append(f"{q} {hints.year}")
        out = with_year + out

    # 8) дедуп + лимит
    seen = set()
    final: list[str] = []
    for q in out:
        q2 = _norm(q)
        if not q2 or is_bad_tmdb_query(q2) or _is_bad_tmdb_candidate(q2):
            continue
        k = q2.lower()
        if k in seen:
            continue
        seen.add(k)
        final.append(q2)
        if len(final) >= max_queries:
            break

    if not final:
        return ([], hints)
    return (final, hints)


# ================= LEGACY TMDB COMPAT HELPERS =================


def _normalize_tmdb_query(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = t.replace("\u00a0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _clean_tmdb_query_legacy(text: str) -> str:
    t = _normalize_tmdb_query(text)
    if not t:
        return ""
    t = re.sub(r"[^\w\s\-':.,&()]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _tmdb_sanitize_query(q: str) -> str:
    t = _clean_tmdb_query_legacy(q)
    if not t:
        return ""
    if len(t) > 120:
        t = t[:120].rsplit(" ", 1)[0] or t[:120]
    return t


def _parse_media_hints(q: str) -> dict:
    t = q or ""
    m = _TMDB_YEAR_RE_LEGACY.search(t)
    if not m:
        return {"year": None}
    try:
        year = int(m.group(1))
    except Exception:
        return {"year": None}
    if year < 1880 or year > 2100:
        return {"year": None}
    return {"year": year}


# ===============================================================

# --- tmdb query cleanup v3 (remove chatter, keep year, drop "где") ---

_TMDB_LEADING_TRASH = _re_tmdb_clean.compile(
    r"""(?ix)
    ^
    (?:
        как\s+называется(?:\s+фильм|\s+сериал|\s+мультик)?|
        что\s+за(?:\s+фильм|\s+сериал|\s+мультик)?|
        помоги\s+найти(?:\s+фильм|\s+сериал|\s+мультик)?|
        ищу(?:\s+фильм|\s+сериал|\s+мультик)?|
        подскажи(?:\s+фильм|\s+сериал|\s+мультик)?|
        откуда\s+кадр|
        название(?:\s+фильма|\s+сериала)?|
        фильм\s+где|
        сериал\s+где|
        мультик\s+где
    )
    \b[\s,:-]*
    """
)

_TMDB_MIDDLE_TRASH = _re_tmdb_clean.compile(
    r"""(?ix)
    \b(?:
        где|в\s+котором|там\s+где|про\s+то\s+как|когда|который|вроде|типа
    )\b
    """
)

_TMDB_YEAR_RE = _re_tmdb_clean.compile(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)")


def _clean_tmdb_query(text: str) -> str:
    """
    Clean user chatter for TMDB search.
    - aggressively removes "как называется/что за/фильм где..."
    - drops leftover lone "где"
    - keeps year hint if it was present
    """
    t = (text or "").strip()
    if not t:
        return ""

    # extract year early (so we can keep it even if we strip everything else)
    years = _TMDB_YEAR_RE.findall(t)
    year = int(years[-1]) if years else None

    t = t.replace("…", " ").replace("...", " ").strip()
    t = _re_tmdb_clean.sub(r"\s+", " ", t).strip()

    # remove leading trash phrases
    t2 = _TMDB_LEADING_TRASH.sub("", t).strip()
    if t2:
        t = t2

    # drop leading "где" if it remains after trash removal
    t = _re_tmdb_clean.sub(r"(?i)^\s*где\b[\s,:-]*", "", t).strip()

    # remove weak filler words inside (only for longer strings)
    if len(t) >= 18:
        t = _TMDB_MIDDLE_TRASH.sub(" ", t)
        t = _re_tmdb_clean.sub(r"\s+", " ", t).strip()

    # strip quotes
    t = t.strip("“”\"'` ").strip()

    # if we ended up with nothing meaningful — keep year (for hints), else return cleaned
    if not t:
        return str(year) if year else ""
    if year and _re_tmdb_clean.fullmatch(r"(?:18|19|20)\d{2}", t):
        return str(year)

    return t


# -----------------------------
# 3) совместимость с assistant.py (ожидаемые имена)
# -----------------------------


def _clean_media_search_query(text: str) -> str:
    # ассистант дальше сам ещё нормализует/санитизирует
    return clean_user_text_for_media(text)


def _is_bad_media_query(q: str) -> bool:
    return is_bad_tmdb_query(q)


def _good_tmdb_cand(q: str) -> bool:
    # “хороший кандидат” = не мусор + не стоп-слово/общая болтовня
    q = _norm(q)
    if not q:
        return False
    if is_bad_tmdb_query(q):
        return False
    if _is_bad_tmdb_candidate(q):
        return False
    return True


_KIND_MARK_RE = re.compile(r"(?i)\b(video|видео|clip|клип|тизер|trailer|трейлер)\b")


def _extract_media_kind_marker(text: str) -> str | None:
    # если юзер явно говорит про видео/клип/трейлер — ассистант у тебя возвращает VIDEO_STUB
    t = (text or "").strip()
    if not t:
        return None
    return "video" if _KIND_MARK_RE.search(t) else None


def _looks_like_choice(text: str) -> bool:
    # "1", "2", "3"...
    t = (text or "").strip()
    return bool(re.fullmatch(r"\d{1,2}", t))


_ASKING_TITLE_RE = re.compile(r"(?i)\b(как называется|что за фильм|что за сериал|что за мультик|откуда кадр)\b")


def _is_asking_for_title(text: str) -> bool:
    t = (text or "").strip()
    return bool(t and _ASKING_TITLE_RE.search(t))


def _looks_like_freeform_media_query(text: str) -> bool:
    # длинное описание сцены/сюжета, а не тайтл
    t = (text or "").strip()
    if not t:
        return False
    if len(t) >= 90:
        return True
    # много слов -> чаще “описание сцены”
    if t.count(" ") >= 10:
        return True
    return False
