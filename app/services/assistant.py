# app/services/assistant.py
from __future__ import annotations

import os
import json
import re
from datetime import datetime, timezone, timedelta, time as dtime
from typing import Optional, Any

from zoneinfo import ZoneInfo
from sqlalchemy import select, desc
try:
    from openai import AsyncOpenAI
except ModuleNotFoundError:
    AsyncOpenAI = None  # type: ignore

from app.models.user import User
from app.models.journal import JournalEntry
from app.services.llm_usage import log_llm_usage
from app.services.media_id import trace_moe_identify
from app.services.media_search import tmdb_search_multi, build_media_context


MENU_NOISE = {
    "📊 Статистика", "🧾 Сегодня", "📓 Журнал", "🏠 Главное меню",
    "💎 Премиум", "⚙️ Настройки", "🧘 Медиа",
}

ANTI_HALLUCINATION_PREFIX = (
    "ВАЖНО:\n"
    "- Если ты НЕ УВЕРЕН(а) — прямо скажи: 'не уверен(а)'.\n"
    "- НЕ угадывай названия фильмов/мультфильмов/людей/мест.\n"
    "- Не придумывай детали, которых не видно.\n"
    "- Если нужно — задай 1 уточняющий вопрос.\n\n"
)

MEDIA_NOT_FOUND_REPLY_RU = (
    "Не нашёл точного совпадения в базе. Дай 1 деталь, и я попробую ещё раз: "
    "год / актёр / страна / язык / что происходит в сцене (1–2 факта)."
)






def _is_asking_for_title(text: str) -> bool:
    t = (text or "").strip().lower()
    pats = (
        "какое название", "как называется", "название фильма", "название у фильма",
        "как называется фильм", "как называется этот фильм", "что за название",
    )
    return any(x in t for x in pats)

def _is_affirmation(text: str) -> bool:
    t = (text or "").strip().lower()
    return bool(re.match(r"^(да|ага|угу)\b", t)) or t.startswith("это ") or t.startswith("да,") or t.startswith("да ")

def _extract_search_query_from_text(s: str) -> str:
    s = s or ""
    m = re.search(r"(?im)^\s*SEARCH_QUERY:\s*(.*)\s*$", s)
    if m:
        return (m.group(1) or "").strip()
    return ""


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
    return q

# --- media session cache (in-memory, no DB migrations) ---
from time import time as _time_now

_MEDIA_TTL_SEC = 10 * 60
_MEDIA_SESSIONS: dict[str, dict] = {}

def _media_uid(user: Any) -> str:
    # prefer tg_id, fallback to db id
    if not user:
        return ""
    v = getattr(user, "tg_id", None) or getattr(user, "id", None)
    return str(v) if v is not None else ""

def _media_get(uid: str) -> Optional[dict]:
    if not uid:
        return None
    s = _MEDIA_SESSIONS.get(uid)
    if not s:
        return None
    if (_time_now() - float(s.get("ts", 0))) > _MEDIA_TTL_SEC:
        _MEDIA_SESSIONS.pop(uid, None)
        return None
    return s

def _media_set(uid: str, query: str, items: list[dict]) -> None:
    if not uid:
        return
    q = _normalize_tmdb_query(query)
    _MEDIA_SESSIONS[uid] = {"query": q, "items": items or [], "ts": _time_now()}

def _looks_like_choice(text: str) -> bool:
    t = (text or "").strip()
    return bool(re.fullmatch(r"\d{1,2}", t))

def _looks_like_year_or_hint(text: str) -> bool:
    t = (text or "").strip().lower()
    if re.search(r"\b(19\d{2}|20\d{2})\b", t):
        return True
    # короткие уточнения: актёр/страна/язык/год/серия/эпизод
    hint_words = ("год", "акт", "актер", "актёр", "страна", "язык", "серия", "эпизод", "сезон")
    return any(w in t for w in hint_words) or (len(t) <= 30 and " " in t)


def _extract_year(text: str) -> Optional[str]:
    m = re.search(r"\b(19\d{2}|20\d{2})\b", (text or ""))
    return m.group(1) if m else None


def _parse_media_hints(text: str) -> dict:
    t = (text or "").lower()

    year = None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", t)
    if m:
        year = m.group(1)

    kind = None
    if "сериал" in t:
        kind = "tv"
    elif "фильм" in t or "кино" in t:
        kind = "movie"

    cast = re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", text)

    keywords = re.sub(r"[^a-zA-Zа-яА-Я0-9 ]", " ", text)
    keywords = " ".join(w for w in keywords.split() if len(w) > 3)[:80]

    return {"year": year, "kind": kind, "cast": cast[:2], "keywords": keywords.strip()}


def _dedupe_media(items: list[dict]) -> list[dict]:
    seen = set()
    out: list[dict] = []
    for it in items or []:
        key = (
            it.get("media_type"),
            it.get("id"),
            ((it.get("title") or "") + "|" + (it.get("name") or "")).lower(),
            it.get("year"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _sort_media(items: list[dict]) -> list[dict]:
    def score(it: dict) -> float:
        try:
            return float(it.get("popularity") or 0) * 0.8 + float(it.get("vote_average") or 0) * 2.0
        except Exception:
            return 0.0

    return sorted(items or [], key=score, reverse=True)


async def _tmdb_best_effort(query: str, *, limit: int = 5) -> list[dict]:
    """
    Best-effort TMDb retrieval:
    - ru-RU first
    - fallback to en-US (TMDb часто богаче на EN)
    - dedupe + soft year filter + sort by usefulness
    """
    q = _normalize_tmdb_query(query)
    if not q:
        return []

    year = _extract_year(q)

    items: list[dict] = []
    try:
        items_ru = await tmdb_search_multi(q, lang="ru-RU", limit=limit)
    except Exception:
        items_ru = []

    if items_ru and isinstance(items_ru[0], dict) and items_ru[0].get("_error"):
        items_ru = []

    items_en: list[dict] = []
    if not items_ru:
        try:
            items_en = await tmdb_search_multi(q, lang="en-US", limit=limit)
        except Exception:
            items_en = []

        if items_en and isinstance(items_en[0], dict) and items_en[0].get("_error"):
            items_en = []

    items = _dedupe_media((items_ru or []) + (items_en or []))

    if year:
        filtered = [it for it in items if str(it.get("year") or "") == year]
        if filtered:
            items = filtered

    return _sort_media(items)[:limit]


def _format_one_media(item: dict) -> str:
    # items come from tmdb_search_multi(): title/year/media_type/overview/vote_average
    title = (item.get("title") or item.get("name") or "Без названия").strip()
    year = (item.get("year") or "").strip()
    overview = (item.get("overview") or "").strip()
    rating = item.get("vote_average", None)
    kind = (item.get("media_type") or "").strip()
    kind_ru = "сериал" if kind == "tv" else "фильм" if kind == "movie" else kind or "медиа"

    line = f"🎬 {title}"
    if year:
        line += f" ({year})"
    line += f" — {kind_ru}"

    if rating is not None:
        try:
            line += f" • ⭐ {float(rating):.1f}"
        except Exception:
            pass

    if overview:
        line += f"\n\n{overview[:700]}"
    return line

def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v else default


def _pick_model() -> str:
    return _env("ASSISTANT_MODEL", "gpt-4.1-mini")

def _user_name(user: Optional[User]) -> str:
    for attr in ("first_name", "name", "username"):
        v = getattr(user, attr, None)
        if v:
            return str(v)
    return "друг"


def _user_tz(user: Optional[User]) -> ZoneInfo:
    tz_name = getattr(user, "tz", None) or "UTC"
    try:
        return ZoneInfo(str(tz_name))
    except Exception:
        return ZoneInfo("UTC")

def _assistant_plan(user: Optional[User]) -> str:
    if not user:
        return "free"

    now = datetime.now(timezone.utc)

    # если premium_until есть и он истёк → FREE
    pu = getattr(user, "premium_until", None)
    if pu is not None:
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        if pu <= now:
            return "free"

    # если premium_until нет и is_premium=False → FREE
    if pu is None and not bool(getattr(user, "is_premium", False)):
        return "free"

    # премиум есть → читаем тариф
    plan = str(getattr(user, "premium_plan", "") or "").strip().lower()
    if plan in {"basic", "pro"}:
        return plan

    # дефолтный тариф премиума
    return "basic"


def _now_str_user(user: Optional[User]) -> str:
    tz = _user_tz(user)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")

def _is_media_query(text: str) -> bool:
    t = (text or "").lower()
    # ключевые слова + типичные запросы на поиск названия
    keys = (
        "фильм", "сериал", "кино", "мульт", "мультик",
        "лента", "кадр", "по кадру", "по этому кадру",
        "season", "episode", "movie", "tv", "series",
        "актёр", "актер", "режисс", "персонаж",
        "как называется", "что за фильм", "что за сериал", "что за мультик"
    )
    return any(k in t for k in keys)

def _is_noise(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True

    if s in MENU_NOISE:
        return True

    letters = sum(ch.isalpha() for ch in s)
    if letters == 0:
        return True

    # суперкороткое почти всегда мусор (но 1-2 слова иногда важны)
    if len(s) <= 3:
        return True

    tokens = re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄє]+", s.lower())
    if tokens:
        most = max(tokens.count(x) for x in set(tokens))
        if most / max(1, len(tokens)) >= 0.6 and len(tokens) >= 4:
            return True

        if len(tokens) >= 4:
            uniq = set(tokens)
            if len(uniq) <= 2 and all(tokens.count(t) >= 2 for t in uniq):
                return True

    # ник/идентификатор без пробелов с подчёркиванием (Pisya_Popa)
    if "_" in s and " " not in s and len(s) <= 20:
        return True

    return False


def meaning_score(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0

    letters = sum(ch.isalpha() for ch in s)
    if letters == 0:
        return 0.0

    tokens = re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄє]+", s.lower())
    w = len(tokens)

    score = 0.0

    if w >= 8:
        score += 0.45
    elif w >= 5:
        score += 0.30
    elif w >= 3:
        score += 0.15
    else:
        score -= 0.10

    ratio = letters / max(1, len(s))
    if ratio >= 0.55:
        score += 0.20
    elif ratio >= 0.35:
        score += 0.10
    else:
        score -= 0.15

    if tokens:
        most = max(tokens.count(x) for x in set(tokens))
        rep = most / max(1, len(tokens))
        if rep >= 0.6 and len(tokens) >= 4:
            score -= 0.35
        elif rep >= 0.4 and len(tokens) >= 5:
            score -= 0.15

    if any(x in s.lower() for x in ("bot_tg", "test", "asdf", "qwerty")):
        score -= 0.35

    return max(0.0, min(1.0, score))

def _as_user_ts(user: Optional[User], ts: Any) -> str:
    """
    created_at из sqlite может быть naive.
    Считаем naive как UTC (это самый безопасный дефолт для серверного времени),
    потом переводим в tz юзера.
    """
    if ts is None:
        return "?"
    try:
        tz = _user_tz(user)
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    except Exception:
        try:
            return ts.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "?"


async def _fetch_recent_journal(
    session: Any,
    user: Optional[User],
    *,
    limit: int = 30,
    take: int = 5,
) -> list[tuple[str, str]]:
    if not session or not user:
        return []

    q = (
        select(JournalEntry.created_at, JournalEntry.text)
        .where(JournalEntry.user_id == user.id)
        .order_by(desc(JournalEntry.created_at))
        .limit(limit)
    )
    res = await session.execute(q)
    rows = res.all()

    out: list[tuple[str, str]] = []

    for created_at, text in rows:
        txt = (text or "").strip()
        if _is_noise(txt):
            continue

        created_str = _as_user_ts(user, created_at)
        out.append((created_str, txt[:700]))
        if len(out) >= take:
            break

    return out


async def build_context(session: Any, user: Optional[User], lang: str, plan: str) -> str:
    parts: list[str] = []
    parts.append(f"Time now: {_now_str_user(user)}")

    if user:
        parts.append(
            "User: "
            f"id={getattr(user,'id',None)}, "
            f"tg_id={getattr(user,'tg_id',None)}, "
            f"name={_user_name(user)}, "
            f"tz={getattr(user,'tz',None)}"
        )

        last_used = getattr(user, "assistant_last_used_at", None)
        if last_used:
            parts.append(f"Assistant last used at: {last_used}")

        profile = getattr(user, "assistant_profile_json", None)
        if profile:
            parts.append("Assistant profile (long-term):")
            parts.append(str(profile)[:2000])

    take = 0 if plan == "basic" else 5

    recent = await _fetch_recent_journal(session, user, limit=30, take=take)
    if recent:
        parts.append("Recent journal entries:")
        for ts, txt in recent:
            parts.append(f"- [{ts}] {txt}")

    return "\n".join(parts)

def _instructions(lang: str, plan: str) -> str:
    base_map = {
        "ru": (
            "Ты — личный помощник в Telegram. Пиши по-русски.\n"
            "Не оценивай настроение и не делай психоанализ.\n"
            "Если данных не хватает — задай 1 уточняющий вопрос.\n"
        ),
        "uk": (
            "Ти — особистий помічник у Telegram. Пиши українською.\n"
            "Не оцінюй настрій і не роби психоаналіз.\n"
            "Якщо бракує даних — постав 1 уточнювальне питання.\n"
        ),
        "en": (
            "You are a personal Telegram assistant. Reply in English.\n"
            "Do not psychoanalyze mood.\n"
            "If info is missing — ask 1 clarifying question.\n"
        ),
    }

    base = base_map.get(lang, base_map["en"])

    style = (
        "Правила ответа:\n"
        "- Не используй шаблоны 'Суть/План/Шаги' и нумерацию, если не просят.\n"
        "- Без психоанализа и диагнозов.\n"
        "- Коротко и по делу.\n"
    )

    if plan == "basic":
        return base + style + (
            "Режим BASIC:\n"
            "- 2–6 предложений.\n"
            "- Без планов и стратегий без запроса.\n"
            "- Журнал не использовать как память.\n"
        )

    return base + style + (
        "Режим PRO:\n"
        "- Можно использовать последние записи журнала как контекст.\n"
        "- Можно предлагать чеклисты и структуру.\n"
        "- Можно задать до 2 уточняющих вопросов.\n"
        "- Стиль: умный близкий помощник.\n"
    )


async def run_assistant(
    user: Optional[User],
    text: str,
    lang: str,
    *,
    session: Any = None,
) -> str:
    if AsyncOpenAI is None:
        return "🤖 Ассистент временно недоступен (сервер без openai).\nПопробуй позже или напиши в поддержку."

    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return {
            "uk": "❌ Не задано OPENAI_API_KEY. Додай ключ у .env / змінні середовища.",
            "en": "❌ OPENAI_API_KEY is missing. Add it to env/.env.",
            "ru": "❌ Не задан OPENAI_API_KEY. Добавь ключ в .env / переменные окружения.",
        }.get(lang, "❌ OPENAI_API_KEY missing.")

    client = AsyncOpenAI(api_key=api_key)
    model = _pick_model()
    plan = _assistant_plan(user)

    now = datetime.now(timezone.utc)

    # --- MEDIA state (DB + in-memory fallback) ---
    uid = _media_uid(user)
    st = _media_get(uid)  # in-memory session, survives even if session=None

    sticky_media_db = False
    if user:
        mode = getattr(user, "assistant_mode", None)
        until = getattr(user, "assistant_mode_until", None)
        if mode == "media" and until and until > now:
            sticky_media_db = True

    # IMPORTANT: if we have in-memory session => treat as media follow-up
    is_media = _is_media_query(text) or sticky_media_db or bool(st)

    if is_media:
        # 1) User picked an option number
        if st and _looks_like_choice(text):
            idx = int(text.strip()) - 1
            opts = st.get("items") or []
            if 0 <= idx < len(opts):
                return _format_one_media(opts[idx])

        # 1.5) "Как называется/какое название" — это не новый поиск, показываем варианты
        if st and _is_asking_for_title(text):
            return build_media_context(st.get("items") or []) + "\n\nВыбери номер варианта."
        # 2) Build query (new query vs follow-up hint)# 2) Merge уточнение with previous query
        # 2) Build query (new query vs follow-up hint)
        raw = (text or "").strip()
        prev_q = ((st.get("query") if st else "") or "").strip()

        # не даём "ядовитым" фразам портить поисковую строку
        if st and re.search(r"(?i)\b(не\s*то|не\s*подходит|ничего\s*не|такого\s*фильма|не\s*существует)\b", raw):
            return MEDIA_NOT_FOUND_REPLY_RU

        # короткое уточнение (год/актёр/страна/язык/серия/эпизод) — добавляем к прошлому запросу
        if st and prev_q and _looks_like_year_or_hint(raw) and len(raw) <= 60:
            query = _normalize_tmdb_query(f"{prev_q} {raw}")
        else:
            query = _normalize_tmdb_query(raw)


        # 3) Too generic → ask 1 detail
        if len(query) < 6 and ("фильм" in query.lower() or "что за" in query.lower()):
            # keep media mode alive for follow-ups even without DB session
            if user is not None:
                user.assistant_mode = "media"
                user.assistant_mode_until = now + timedelta(minutes=10)
                if session:
                    await session.commit()
            return MEDIA_NOT_FOUND_REPLY_RU

        # 4) Best-effort TMDb search (ru first, fallback en, year filter, dedupe, sort)
        query = _normalize_tmdb_query(query)
        try:
            print(f"[media] prev_q={prev_q!r} raw={raw!r} -> query={query!r}")
        except Exception:
            pass

        try:
            items = []

            # 🔹 First try direct search by model/caption query
            items = await _tmdb_best_effort(query, limit=5)

            # 🔹 If nothing found — use parsed hints
            hints = _parse_media_hints(query)
            if hints.get("keywords"):
                items = await _tmdb_best_effort(hints["keywords"], limit=5)

            if not items and hints.get("cast"):
                from app.services.media_search import tmdb_search_person, tmdb_discover_with_people
                for actor in hints["cast"]:
                    pid = await tmdb_search_person(actor)
                    if pid:
                        items = await tmdb_discover_with_people(
                            pid,
                            year=hints.get("year"),
                            kind=hints.get("kind"),
                        )
                        if items:
                            break

        except Exception:
            items = []

        # keep sticky media mode (DB if possible)
        if user is not None:
            user.assistant_mode = "media"
            user.assistant_mode_until = now + timedelta(minutes=10)
            if session:
                await session.commit()

        if not items:
            # keep last query in memory so next hint still treated as media
            if uid:
                _media_set(uid, query, [])
            return MEDIA_NOT_FOUND_REPLY_RU

        _media_set(uid, query, items)
        return build_media_context(items) + "\n\nВыбери номер варианта."

    # ---- Normal assistant (non-media) ----
    ctx = await build_context(session, user, lang, plan)

    prev_id = getattr(user, "assistant_prev_response_id", None) if user else None
    if user:
        last_used = getattr(user, "assistant_last_used_at", None)
        if last_used and (datetime.now(timezone.utc) - last_used) > timedelta(hours=24):
            prev_id = None

    prompt = (
        f"Context:\n{ctx}\n\n"
        "User message:\n" + (text or "") + "\n"
    )

    resp = await client.responses.create(
        previous_response_id=prev_id,
        model=model,
        instructions=_instructions(lang, plan),
        input=prompt,
        max_output_tokens=(260 if plan == "basic" else 650),
    )

    if session:
        await log_llm_usage(
            session,
            user_id=getattr(user, "id", None) if user else None,
            feature="assistant",
            model=model,
            plan=plan,
            resp=resp,
            meta={"lang": lang},
        )

    out_text = (getattr(resp, "output_text", None) or "").strip()

    resp_id = getattr(resp, "id", None)
    if session and user and resp_id:
        changed = False
        if user.assistant_prev_response_id != str(resp_id):
            user.assistant_prev_response_id = str(resp_id)
            changed = True
        user.assistant_last_used_at = datetime.now(timezone.utc)
        changed = True

        if changed:
            await session.commit()

    if out_text:
        return out_text

    try:
        return str(getattr(resp, "output", "")).strip() or "⚠️ Empty response."
    except Exception:
        return "⚠️ Не смог прочитать ответ модели."

async def run_assistant_vision(
    user: Optional[User],
    image_bytes: bytes,
    caption: str,
    lang: str,
    *,
    session: Any = None,
) -> str:
    if AsyncOpenAI is None:
        return "🤖 Vision временно недоступен (сервер без openai)."

    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return {
            "uk": "❌ Не задано OPENAI_API_KEY.",
            "en": "❌ OPENAI_API_KEY is missing.",
            "ru": "❌ Не задан OPENAI_API_KEY.",
        }.get(lang, "❌ OPENAI_API_KEY missing.")

    plan = _assistant_plan(user)
    if plan != "pro":
        return {
            "ru": "Фото доступно только в PRO.",
            "uk": "Фото доступне лише в PRO.",
            "en": "Photos are PRO-only.",
        }.get(lang, "PRO-only.")

    client = AsyncOpenAI(api_key=api_key)

    prompt_text = (caption or "").strip() or {
        "ru": "Определи, что на фото. Если это кадр из фильма/сериала/мульта — попробуй определить источник.",
        "uk": "Визнач, що на фото. Якщо це кадр з фільму/серіалу/мультфільму — спробуй визначити джерело.",
        "en": "Identify what’s in the image. If it’s a movie/series/cartoon frame, try to identify the source.",
    }.get(lang, "Identify the image and, if it's a movie/series/cartoon frame, try to identify the source.")

    hard_keywords = (
        "текст", "что написано", "прочитай", "скрин", "скриншот",
        "ошибка", "error", "traceback", "лог", "qr", "кьюар",
        "инструкция", "меню", "чек", "рецепт", "состав"
    )
    is_hard = any(k in prompt_text.lower() for k in hard_keywords)

    model_default = _env("ASSISTANT_VISION_MODEL", _pick_model())
    model_hard = _env("ASSISTANT_VISION_MODEL_HARD", model_default)
    model = model_hard if is_hard else model_default

    import base64
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"

    now = datetime.now(timezone.utc)

    instr = (
        ANTI_HALLUCINATION_PREFIX
        + _instructions(lang, plan)
        + "\n"
        + (
            "Ты видишь изображение.\n"
            "Если это кадр из фильма/сериала/мульта/аниме — попробуй определить источник.\n"
            "Если не уверен — так и скажи. Не выдумывай детали.\n\n"
            "В конце добавь строку:\n"
            "SEARCH_QUERY: <короткий запрос для поиска (название/персонаж/год/ключевые слова)>\n"
            "Если не можешь — напиши:\n"
            "SEARCH_QUERY:\n"
        )
    )

    try:
        resp = await client.responses.create(
            model=model,
            instructions=instr,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt_text},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
            max_output_tokens=450,
        )
    except Exception as e:
        return {
            "ru": f"⚠️ Не смог обработать фото ({type(e).__name__}). Попробуй отправить фото меньшего размера или сжать скрин.",
            "uk": f"⚠️ Не зміг обробити фото ({type(e).__name__}). Спробуй надіслати менше фото або стиснути скрін.",
            "en": f"⚠️ I couldn’t process the photo ({type(e).__name__}). Try sending a smaller image or compress the screenshot.",
        }.get(lang, f"⚠️ Vision error: {type(e).__name__}")

    if session:
        await log_llm_usage(
            session,
            user_id=getattr(user, "id", None) if user else None,
            feature="vision",
            model=model,
            plan=plan,
            resp=resp,
            meta={"lang": lang},
        )

    out_text = (getattr(resp, "output_text", None) or "").strip()
    out_text = str(out_text)

    # trace.moe (anime) — только если модель явно сказала "аниме"
    if any(k in out_text.lower() for k in ("аниме", "anime")):
        try:
            result = await trace_moe_identify(image_bytes)
        except Exception:
            result = None

        if result:
            sim = float(result.get("similarity", 0) or 0)
            if sim >= 0.9:
                return (
                    "🎬 Это кадр из аниме.\n\n"
                    f"Название: {result.get('title')}\n"
                    f"Серия: {result.get('episode')}\n"
                    f"Совпадение: {sim:.1%}"
                )
    # иначе — не ломаем основной поток, просто идём дальше (TMDb)
    # Vision → TMDb candidates
    caption_str = (caption or "").strip()
    search_q = _normalize_tmdb_query(_extract_search_query_from_text(out_text))
    tmdb_q = search_q or _normalize_tmdb_query(caption_str)

    if tmdb_q:
        try:
            items = []

            # 🔹 First try direct search by model/caption query
            items = await _tmdb_best_effort(tmdb_q, limit=5)

            # 🔹 If nothing found — use parsed hints
            hints = _parse_media_hints(tmdb_q)
            if hints.get("keywords"):
                items = await _tmdb_best_effort(hints["keywords"], limit=5)

            if not items and hints.get("cast"):
                from app.services.media_search import tmdb_search_person, tmdb_discover_with_people
                for actor in hints["cast"]:
                    pid = await tmdb_search_person(actor)
                    if pid:
                        items = await tmdb_discover_with_people(
                            pid,
                            year=hints.get("year"),
                            kind=hints.get("kind"),
                        )
                        if items:
                            break

        except Exception:
            items = []

        if items:
            if user is not None:
                user.assistant_mode = "media"
                user.assistant_mode_until = now + timedelta(minutes=10)
                if session:
                    await session.commit()

            uid = _media_uid(user)
            if uid:
                _media_set(uid, tmdb_q, items)

            return build_media_context(items) + "Выбери номер варианта."

        return MEDIA_NOT_FOUND_REPLY_RU
    
    # --- Failsafe: Vision must always return text ---
    final_text = (out_text or "").strip()

    if not final_text:
        final_text = (
            "Я не смог уверенно определить источник по изображению. "
            "Попробуй описать сцену словами или добавить деталь "
            "(актёр, год, что происходит)."
        )

    return final_text