from __future__ import annotations

# ruff: noqa: E402
# pyright: reportOptionalSubscript=false

import os
import os as _os
import time as _time
import contextvars as _contextvars
import uuid as _uuid
import asyncio as _asyncio
import re
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select, func

# --- Imports from app ---
from app.models.journal import JournalEntry
from app.models.user import User
from app.services.intent_router import Intent, detect_intent
from app.services.web_search import serpapi_search
from app.services.web_reader import extract_first_url, fetch_page_text
from app.services.media.formatting import (
    MEDIA_NOT_FOUND_REPLY_RU,
    MEDIA_VIDEO_STUB_REPLY_RU,
    _format_media_pick,
    _format_media_ranked,
    build_media_context,
)
from app.services.media.logging import _d
from app.services.media.pipeline_tmdb import _tmdb_best_effort
from app.services.media.query import (
    _clean_media_search_query,
    _extract_media_kind_marker,
    _good_tmdb_cand,
    _is_asking_for_title,
    _is_bad_media_query,
    _looks_like_freeform_media_query,
    _normalize_tmdb_query,
    _parse_media_hints,
    _tmdb_sanitize_query,
    _looks_like_choice,
    _looks_like_year_or_hint,
    is_bad_tmdb_query,
    tmdb_query_compact,
    _is_bad_tmdb_candidate,
    _mf_is_worthy_tmdb,
)
from app.services.media.safety import (
    _scrub_media_items,
)
from app.services.media.session import (
    _MEDIA_SESSIONS,
    _media_get,
    _media_set,
    _media_uid,
)

from app.services.media.vision_parse import (
    _extract_media_json_from_model_text,
    _extract_search_query_from_text,
    _extract_title_like_from_model_text,
)


# --- Logging & Tracing Wrappers ---
async def _send_dbg(logger, kind: str, fn, *args, **kwargs):
    if _TRACE_ON:
        txt = None
        try:
            if "text" in kwargs and isinstance(kwargs.get("text"), str):
                txt = kwargs.get("text")[:180]
        except Exception:
            pass
        _atrace(
            logger,
            f"tg.{kind}.send",
            has_markup=bool(kwargs.get("reply_markup") or kwargs.get("markup")),
            text=txt,
        )
    return await fn(*args, **kwargs)


_TRACE_ON = _os.getenv("TRACE_ASSISTANT", "0") == "1"
_trace_id_var: _contextvars.ContextVar[str] = _contextvars.ContextVar("atrace_id", default="")


def _atrace_id() -> str:
    return _trace_id_var.get() or "-"


def _atrace_new(prefix: str = "a") -> str:
    return f"{prefix}{_uuid.uuid4().hex[:10]}"


def _atrace(logger, stage: str, **kv):
    if not _TRACE_ON:
        return
    try:
        logger.info("[trace] %s | %s | %s", _atrace_id(), stage, kv)

    except Exception:
        pass


class _ASpan:
    def __init__(self, logger, stage: str, **kv):
        self.logger = logger
        self.stage = stage
        self.kv = kv
        self.t0 = None

    def __enter__(self):
        self.t0 = _time.time()
        _atrace(self.logger, self.stage + ".in", **self.kv)
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = int((_time.time() - (self.t0 or _time.time())) * 1000)
        if exc is not None:
            _atrace(self.logger, self.stage + ".err", ms=dt, err=str(exc))
            return False
        _atrace(self.logger, self.stage + ".out", ms=dt)
        return False


def _atrace_set(tid: str):
    try:
        _trace_id_var.set(tid)

    except Exception:
        pass


def _dbg_media(logger, tag: str, **kv):
    try:
        logger.info("[media][dbg] %s | %s", tag, kv)

    except Exception:
        pass


# --- FlowPatch: media query clean + refinement detection (assistant) ---
_TMDB_STOPWORDS = {
    "photo",
    "<photo>",
    "уточнение",
    "уточнение:",
    "уточни",
    "дай",
    "другие",
    "варианты",
    "жанр",
    "страна",
    "год",
    "серия",
    "эпизод",
    "сезон",
    "film",
    "movie",
    "series",
    "tv",
    "what",
    "is",
    "the",
    "a",
    "an",
    "drama",
    "romance",
    "prison",
    "fence",
}

_LENS_BLOCKLIST = {
    "movie reviews",
    "full episode",
    "youtube",
    "tiktok",
    "instagram",
    "video",
    "clip",
    "scene",
    "4k",
    "1080p",
    "hd",
    "watch online",
    "trailer",
    "official trailer",
    "teaser",
    "review",
}


def _tmdb_clean_user_text(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    t = t.replace("<photo>", " ").replace("photo", " ")
    t = re.sub(r"(?i)\bуточнение\s*:\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 140:
        t = t[:140].rsplit(" ", 1)[0].strip()
    return t


def _tmdb_is_refinement(text: str) -> bool:
    if not text:
        return False
    t = text.lower().strip()
    if any(k in t for k in ("уточнение", "уточни", "дай другие", "другие варианты", "коротко")):
        return True
    if re.search(r"\b(19\d{2}|20\d{2})\b", t):
        return True
    parts = t.split()
    if 1 <= len(parts) <= 2 and len(t) <= 18:
        return True
    hint_words = (
        "год",
        "акт",
        "актер",
        "актёр",
        "страна",
        "язык",
        "серия",
        "эпизод",
        "сезон",
        "сша",
        "америка",
        "usa",
        "us",
        "uk",
        "нетфликс",
        "netflix",
        "hbo",
        "amazon",
        "комедия",
        "драма",
        "боевик",
        "триллер",
        "ужасы",
        "мелодрама",
    )
    return any(w in t for w in hint_words)


def _is_garbage_query(q: str) -> bool:
    """Фильтр для мусорных запросов от Lens."""
    if not q:
        return True
    q_lower = q.strip().lower()

    if len(q_lower) < 3:
        return True

    # Хэши и имена файлов
    for word in q_lower.split():
        if len(word) > 6 and any(c.isdigit() for c in word) and any(c.isalpha() for c in word):
            return True

    # Блок-лист
    for block in _LENS_BLOCKLIST:
        if block in q_lower:
            return True

    return False


def _smart_clean_lens_candidate(text: str) -> str:
    """Очистка мусора от Lens (Малькольм в центре внимания, 2000 год...)"""
    if not text:
        return ""

    # 0. Удаляем юзернеймы
    text_clean = re.sub(r"\(@[^)]+\)", "", text)

    # 1. Приоритет: Текст в кавычках
    quotes = re.findall(r"«([^»\n]+)(?:»|$)", text_clean) or re.findall(r'"([^"\n]+)(?:"|$)', text_clean)
    if quotes:
        longest = max(quotes, key=len)
        cleaned = re.sub(r"[\.…]+$", "", longest).strip()
        if len(cleaned) > 2 and not _is_garbage_query(cleaned):
            return cleaned

    # 2. Поиск по якорям (Сериал, Фильм)
    anchors = ["сериал", "фильм", "movie", "film", "сцена из", "scene from", "watch"]
    lower = text_clean.lower()
    for anchor in anchors:
        if f" {anchor} " in f" {lower} ":
            match = re.search(r"(?i)\b" + re.escape(anchor) + r"\b", text_clean)
            if match:
                # Берем то, что ПОСЛЕ якоря: "Сериал Малкольм в центре внимания" -> "Малкольм..."
                candidate = text_clean[match.end() :].strip()
                # Или то, что ДО якоря, если после - мусор?
                # Обычно Lens пишет: "Со смыслом... Сериал Малкольм..."
                # Попробуем взять то, что выглядит как название

                # Чистим результат от "2000 год", "смотреть онлайн"
                candidate = re.sub(r"\b(19|20)\d{2}\b.*", "", candidate)  # Отрезаем год и все что после
                candidate = re.sub(r"^[^a-zA-Zа-яА-Я0-9]+", "", candidate)
                if len(candidate) > 2 and not _is_garbage_query(candidate):
                    return candidate.strip()

    # 3. Fallback очистка
    candidate = text_clean
    candidate = re.sub(r"(?i)\b(сериал|фильм|кино|movie|film|scene from|сцена из)\b", "", candidate)
    candidate = re.sub(r"[\.…]+$", "", candidate)
    # Удаляем эмодзи
    candidate = re.sub(r"[^\w\s\-\.,:!?'()]+", " ", candidate, flags=re.UNICODE)

    if ":" in candidate and len(candidate.split()) > 5:
        parts = candidate.split(":")
        if len(parts[0].strip()) > 3:
            candidate = parts[0]

    return re.sub(r"\s+", " ", candidate).strip()


# --- External Services Stubs/Imports ---

try:
    from app.services.media_text import (
        is_generic_media_caption as _is_generic_media_caption,
    )
except Exception:

    def _is_generic_media_caption(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return True
        t = re.sub(r"\s+", " ", t).strip()
        return t in {
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
        }


ANTI_HALLUCINATION_PREFIX: str = ""

try:
    from openai import AsyncOpenAI
except ModuleNotFoundError:
    AsyncOpenAI = None

# --- Vision Cache ---
_VISION_IMG_CACHE: dict[str, tuple[float, str]] = {}
_VISION_IMG_CACHE_TTL_SEC = 30 * 60  # 30 minutes


def _vision_cache_get(key: str) -> str | None:
    try:
        v = _VISION_IMG_CACHE.get(key)
        if not v:
            return None
        ts, reply = v
        if (_time.time() - ts) > _VISION_IMG_CACHE_TTL_SEC:
            _VISION_IMG_CACHE.pop(key, None)
            return None
        return reply
    except Exception:
        return None


def _vision_cache_set(key: str, reply: str) -> None:
    try:
        if key and reply:
            _VISION_IMG_CACHE[key] = (_time.time(), reply)

    except Exception:
        pass


# --- Service fallbacks ---
try:
    from app.services.media_search import tmdb_search_multi
except Exception:

    async def tmdb_search_multi(*args: Any, **kwargs: Any) -> list[dict]:
        return []


try:
    from app.services.media_web_pipeline import web_to_tmdb_candidates
except Exception:

    async def web_to_tmdb_candidates(*args: Any, **kwargs: Any) -> tuple[list[str], str]:
        return ([], "web_stub")


try:
    from app.services.media_web_pipeline import image_bytes_to_tmdb_candidates
except Exception:

    async def image_bytes_to_tmdb_candidates(*args: Any, **kwargs: Any) -> tuple[list[str], str]:
        return ([], "lens_stub")


try:
    from app.services.media_id import trace_moe_identify
except Exception:

    async def trace_moe_identify(*args: Any, **kwargs: Any) -> Optional[dict]:
        return None


try:
    from app.services.llm_usage import log_llm_usage
except Exception:

    async def log_llm_usage(*args: Any, **kwargs: Any) -> None:
        return None


def _media_confident(item: dict) -> bool:
    try:
        pop = float(item.get("popularity") or 0)
        va = float(item.get("vote_average") or 0)
    except Exception:
        return False
    return (pop >= 25 and va >= 6.8) or (pop >= 60) or (va >= 7.6)


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


# ===========================
# === Quotas / Daily limits ==
# ===========================


def _env_int(name: str, default: int) -> int:
    try:
        v = os.getenv(name)
        return int(v) if v is not None and str(v).strip() != "" else int(default)
    except Exception:
        return int(default)


def _quota_limits_tokens(plan: str, feature: str) -> int:
    """
    Daily token limits (sum of total_tokens in llm_usage for last 24h).
    Can be overridden via env:
      - ASSISTANT_DAILY_TOKENS_BASIC / PRO
      - VISION_DAILY_TOKENS_BASIC / PRO
    """
    plan_n = (plan or "basic").strip().lower()
    feat = (feature or "assistant").strip().lower()

    if feat == "vision":
        if plan_n == "pro":
            return _env_int("VISION_DAILY_TOKENS_PRO", 120_000)
        return _env_int("VISION_DAILY_TOKENS_BASIC", 0)  # basic: no photo by design
    else:
        if plan_n == "pro":
            return _env_int("ASSISTANT_DAILY_TOKENS_PRO", 220_000)
        if plan_n == "basic":
            return _env_int("ASSISTANT_DAILY_TOKENS_BASIC", 35_000)
        # Поднимаем лимит для беты, чтобы хватало на 15-20 диалогов
        return _env_int("ASSISTANT_DAILY_TOKENS_FREE", 45_000)


async def _usage_tokens_last_24h(session: Any, user_id: int, feature: str) -> int:
    if not session or not user_id:
        return 0
    try:
        from app.models.llm_usage import LLMUsage  # local import to avoid circulars

        since = datetime.utcnow() - timedelta(hours=24)
        q = select(func.coalesce(func.sum(LLMUsage.total_tokens), 0)).where(
            LLMUsage.user_id == user_id,
            LLMUsage.feature == feature,
            LLMUsage.created_at >= since,
        )
        res = await session.execute(q)
        v = res.scalar_one() if res is not None else 0
        return int(v or 0)
    except Exception:
        return 0


def _quota_msg_ru(feature: str, used: int, limit: int) -> str:
    feat = "🌐 Web" if (feature == "assistant_web") else ("📷 Фото" if feature == "vision" else "🤖 Ассистент")
    return (
        f"⛔️ Лимит на сегодня по режиму {feat} исчерпан.\n"
        f"Использовано: {used:,} токенов из {limit:,} за последние 24 часа.\n\n"
        "Попробуй завтра или обнови тариф."
    )


def _soft_quota_web_ru(plan: str) -> str:
    # UX: мягко, без злости, с понятным CTA
    # NOTE: "3 дня" — твой продуктовый цикл
    p = (plan or "basic").strip().lower()
    plan_label = "Basic" if p == "basic" else ("Pro" if p == "pro" else ("Max" if p in {"pro_max", "max"} else p))
    return (
        f"⚠️ Лимит на Web-поиск по тарифу {plan_label} исчерпан.\n\n"
        "Ты использовал лимит текущего тарифа.\n"
        "Следующее обновление через 3 дня.\n\n"
        "Или можешь увеличить лимит прямо сейчас:"
    )


async def _enforce_quota(
    *,
    session: Any,
    user: Optional[User],
    plan: str,
    feature: str,
) -> Optional[str]:
    if not user or not getattr(user, "id", None):
        return None

    # web is PRO-only
    if feature == "assistant_web" and plan != "pro":
        return "🌐 Web-разбор доступен только в PRO. Открой Premium и выбери PRO-тариф."

    # map feature -> llm_usage.feature values
    usage_feature = "assistant" if feature in {"assistant", "assistant_web"} else "vision"

    limit = _quota_limits_tokens(plan, usage_feature)
    if limit <= 0:
        # basic vision disabled, etc.
        if usage_feature == "vision":
            return "📷 Поиск по фото доступен только в PRO."
        return None

    used = await _usage_tokens_last_24h(session, int(user.id), usage_feature)
    if used >= limit:
        return _quota_msg_ru(feature, used, limit)

    return None


def _assistant_plan(user: Optional[User]) -> str:
    if not user:
        return "free"

    now = datetime.now(timezone.utc)
    pu = getattr(user, "premium_until", None)
    is_prem = bool(getattr(user, "is_premium", False))
    plan = str(getattr(user, "premium_plan", "") or "").strip().lower()

    # Если дата истекла, но админ повесил галочку is_premium — не сбрасываем!
    if pu is not None:
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        if pu <= now and not is_prem:
            return "free"

    # Если даты нет и галочки нет
    if pu is None and not is_prem:
        return "free"

    # Распознаем все твои тарифы
    if plan in {"basic", "pro", "max", "pro_max"}:
        return plan

    return "basic"


def _now_str_user(user: Optional[User]) -> str:
    tz = _user_tz(user)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")


def _is_media_query(text: str) -> bool:
    t = (text or "").lower()
    keys = (
        "фильм",
        "сериал",
        "кино",
        "мульт",
        "мультик",
        "лента",
        "кадр",
        "по кадру",
        "по этому кадру",
        "season",
        "episode",
        "movie",
        "tv",
        "series",
        "актёр",
        "актер",
        "режисс",
        "персонаж",
        "как называется",
        "что за фильм",
        "что за сериал",
        "что за мультик",
    )
    return any(k in t for k in keys)


def _is_noise(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return True
    letters = sum(ch.isalpha() for ch in s)
    if letters == 0:
        return True
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
    if "_" in s and " " not in s and len(s) <= 20:
        return True
    return False


def _as_user_ts(user: Optional[User], ts: Any) -> str:
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
    session: Any, user: Optional[User], *, limit: int = 30, take: int = 5
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
            f"id={getattr(user, 'id', None)}, "
            f"tg_id={getattr(user, 'tg_id', None)}, "
            f"name={_user_name(user)}, "
            f"tz={getattr(user, 'tz', None)}"
        )
        last_used = getattr(user, "assistant_last_used_at", None)
        if last_used:
            parts.append(f"Assistant last used at: {last_used}")
        profile = getattr(user, "assistant_profile_json", None)
        if profile:
            parts.append("Assistant profile (long-term):")
            parts.append(str(profile)[:2000])

    take = 0 if plan in {"free", "basic"} else 5
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
    tricks = (
        "\n\nСЕКРЕТНЫЕ НАВЫКИ (Применяй только если это в тему):\n"
        "1. Формула Карвонена: Если юзер спрашивает про бег, пульс, кардио или похудение, рассчитай ему пульсовые зоны по формуле Карвонена. Запроси возраст и пульс покоя, если их нет.\n"
        "2. Музыкатерапия: Если юзер пишет, что он выгорел, устал или в депрессии, помимо слов поддержки, посоветуй ему послушать конкретный Lo-Fi/Ambient трек или классику (напиши название и автора) и порекомендуй включить раздел 'Медитация' в боте."
    )

    if plan == "basic":
        return (
            base
            + style
            + "Режим BASIC:\n- 2–6 предложений.\n- Без планов и стратегий без запроса.\n- Журнал не использовать как память.\n"
            + tricks
        )
    return (
        base
        + style
        + "Режим PRO:\n- Можно использовать последние записи журнала как контекст.\n- Можно предлагать чеклисты и структуру.\n- Можно задать до 2 уточняющих вопросов.\n- Стиль: умный близкий помощник.\n"
        + tricks
    )


async def run_assistant(
    user: Optional[User],
    text: str,
    lang: str,
    *,
    session: Any = None,
    has_media: bool = False,
) -> str:
    if AsyncOpenAI is None:
        return "🤖 Ассистент временно недоступен (сервер без openai).\nПопробуй позже или напиши в поддержку."

    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return "❌ OPENAI_API_KEY missing."

    client = AsyncOpenAI(api_key=api_key)
    model = _pick_model()
    plan = _assistant_plan(user)

    # quota guard (before any external calls)
    if session and user:
        qmsg = await _enforce_quota(session=session, user=user, plan=plan, feature="assistant")
        if qmsg:
            return qmsg
    query = ""
    prev_q = ""
    items = []
    raw = (text or "").strip()

    web_mode = False  # web-mode: skip media/TMDB
    # --- WEB MODE (PRO/MAX): url or "web:" prefix -> build analysis prompt and continue normal assistant flow ---
    t0 = (text or "").strip()
    if t0 and (t0.lower().startswith("web:") or ("http://" in t0) or ("https://" in t0)):
        web_mode = True
        # quota guard for web mode
        if session and user:
            qmsg = await _enforce_quota(session=session, user=user, plan=plan, feature="assistant_web")
            if qmsg:
                return qmsg

        q_or_url = t0[4:].strip() if t0.lower().startswith("web:") else t0
        url = extract_first_url(q_or_url) or extract_first_url(t0)
        try:
            if url:
                title, page_text = await fetch_page_text(url, max_chars=12000)
                if page_text:
                    text = (
                        "Ты — аналитик. Разбери веб-страницу.\n"
                        "Дай:\n"
                        "1) 6–10 буллетов по сути\n"
                        "2) 2 короткие цитаты (1 строка каждая)\n"
                        "3) 1 практический вывод\n\n"
                        f"URL: {url}\n"
                        f"TITLE: {title}\n\n"
                        f"TEXT:\n{page_text}\n"
                    )
                    raw = (text or "").strip()
            else:
                results = await serpapi_search(session, cast(User, user), q_or_url, count=5)
                # quota softback from serpapi gateway
                if results and isinstance(results, list) and isinstance(results[0], dict) and results[0].get('quota_exceeded'):
                    plan = (results[0].get('plan') or getattr(user, 'premium_plan', None) or 'basic')
                    # IMPORTANT: handler layer should attach the button; we return text marker here.
                    return _soft_quota_web_ru(str(plan)) + "\n\n[Upgrade to Pro]"
                if results:
                    parts = []
                    for i, it in enumerate(results[:2], 1):
                        u = (it.get("url") or "").strip()
                        t = (it.get("title") or "").strip()
                        sn = (it.get("snippet") or "").strip()
                        pt = ""
                        if u:
                            _ttl, _txt = await fetch_page_text(u, max_chars=8000)
                            pt = (_txt or "")[:6000]
                        parts.append(f"[{i}] {t}\nURL: {u}\nSNIPPET: {sn}\nTEXT_EXCERPT:\n{pt}\n")
                    text = (
                        "Ты — аналитик. Суммируй результаты поиска и выдержки страниц.\n"
                        "Дай:\n"
                        "1) 8–12 буллетов\n"
                        "2) Отметь противоречия (если есть)\n"
                        "3) Источники указывай как [1], [2]\n\n"
                        f"QUERY: {q_or_url}\n\n" + "\n\n".join(parts)
                    )
                    raw = (text or "").strip()
        except Exception:
            pass
    # --- WEB SHORT-CIRCUIT: never route web-mode content into media/TMDB ---
    if web_mode:
        ctx = await build_context(session, user, lang, plan)
        prev_id = getattr(user, "assistant_prev_response_id", None) if user else None
        if user:
            last_used = getattr(user, "assistant_last_used_at", None)
            if last_used and (datetime.now(timezone.utc) - last_used) > timedelta(hours=24):
                prev_id = None
        prompt = f"Context:\n{ctx}\n\nUser message:\n" + (text or "") + "\n"

        try:
            resp = await client.responses.create(
                previous_response_id=prev_id,
                model=model,
                instructions=_instructions(lang, plan),
                input=prompt,
                max_output_tokens=(260 if plan == "basic" else 650),
            )
        except Exception as e:
            return f"⚠️ API Error: {str(e)}"

        if session:
            await log_llm_usage(
                session,
                user_id=getattr(user, "id", None) if user else None,
                feature="assistant_web",
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
    now = datetime.now(timezone.utc)
    kind_marker = _extract_media_kind_marker(text)
    if kind_marker:
        return MEDIA_VIDEO_STUB_REPLY_RU

    # --- MEDIA state (DB + in-memory fallback) ---
    uid = _media_uid(user)
    st = _media_get(uid)

    sticky_media_db = False
    if user:
        mode = getattr(user, "assistant_mode", None)
        until = getattr(user, "assistant_mode_until", None)
        if mode == "media" and until and until > now:
            sticky_media_db = True

    # 1. ПЕРВООЧЕРЕДНАЯ ПРОВЕРКА НАВИГАЦИИ (кнопки или цифры)
    is_nav = False
    if text:
        t_low = text.lower().strip()
        if any(k in t_low for k in ("другие", "варианты", "еще", "ещё")) or re.fullmatch(r"\d{1,2}", t_low):
            is_nav = True

    # 2. ОПРЕДЕЛЕНИЕ ИНТЕНТА
    if is_nav:
        # Если это навигация, принудительно ставим медиа-интент, чтобы не сбросить сессию
        is_intent_media = True
        intent = Intent.MEDIA_TEXT
    else:
        intent_res = detect_intent((text or "").strip() if text else None, has_media=bool(has_media))
        intent = getattr(intent_res, "intent", None) or intent_res
        is_intent_media = intent in (Intent.MEDIA_IMAGE, Intent.MEDIA_TEXT)

    # Guard: intent_router can be noisy. If there is no media, no sticky mode and the text doesn't look like a media query,
    # do NOT enter media pipeline.
    if is_intent_media and (not has_media) and (not sticky_media_db) and (not st) and (not _is_media_query(text or "")):
        is_intent_media = False
        intent = None
    # 3. СБРОС СЕССИИ (только если это не медиа и не навигация)
    if not is_intent_media and not is_nav:
        if uid:
            _MEDIA_SESSIONS.pop(uid, None)
        if user is not None:
            try:
                setattr(user, "assistant_mode", None)
                setattr(user, "assistant_mode_until", now - timedelta(seconds=1))
                if session:
                    await session.commit()

            except Exception:
                pass
    # 4. ВХОД В МЕДИА-ЛОГИКУ
    is_media = bool(has_media) or bool(is_intent_media) or (is_nav and bool(st))

    if is_media:
        _d(
            "media.enter",
            is_media=is_media,
            sticky_media_db=sticky_media_db,
            has_st=bool(st),
            uid=uid,
        )
        raw_text = (text or "").strip()

        # ---------------------------------------------------------------------
        # ОБРАБОТКА КНОПКИ "ДРУГИЕ ВАРИАНТЫ" (FIXED)
        # ---------------------------------------------------------------------
        # Теперь бот не идет в поиск/ИИ, а достает из памяти следующие результаты.
        if st and ("другие" in raw_text.lower() or "варианты" in raw_text.lower()):
            opts = st.get("items") or []
            prev_q = st.get("query") or "Результаты поиска"

            # Ротация: берем текущие 3, кидаем в конец, показываем следующие 3
            if len(opts) > 3:
                rotated_opts = opts[3:] + opts[:3]
                _media_set(uid, prev_q, rotated_opts)
                return (
                    _format_media_ranked(
                        prev_q,
                        rotated_opts,
                        year_hint=_parse_media_hints(prev_q).get("year"),
                        lang=lang,
                        source="cache",
                    )
                    + "\n\n(Показаны следующие варианты 🔄)"
                )
            else:
                return "📭 Больше вариантов нет. Попробуй уточнить запрос (год, актер) или скинь другой кадр."

        # 1) Choice by number
        if st and _looks_like_choice(raw_text):
            idx = int(raw_text) - 1
            opts = st.get("items") or []
            if 0 <= idx < len(opts):
                picked = opts[idx]
                return _format_media_pick(picked) + "\n\nХочешь — напиши другое название/описание, я поищу ещё."

        # 1.5) Asking for title again
        if st and _is_asking_for_title(raw_text):
            opts = st.get("items") or []
            if not opts:
                return MEDIA_NOT_FOUND_REPLY_RU
            return build_media_context(opts) + "\n\nКнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить"

        # 2) Build query
        raw = raw_text
        if st and re.search(r"(?i)\b(не\s*то|не\s*подходит|ничего\s*не|такого\s*фильма|не\s*существует)\b", raw):
            return MEDIA_NOT_FOUND_REPLY_RU

        # Normalization
        raw = _normalize_tmdb_query(raw)

        # Если это просто уточнение (год, актер), клеим к прошлому запросу
        # Если новый запрос — ищем заново.
        prev_q = ((st.get("query") if st else "") or "").strip()

        if st and prev_q and raw and (len(raw) <= 140):
            if _tmdb_is_refinement(raw) or len(raw.split()) <= 2:
                if _looks_like_year_or_hint(raw):
                    query = f"{prev_q} {raw}"
                else:
                    query = prev_q  # Если мусор, оставляем старый
            else:
                query = _tmdb_sanitize_query(_clean_media_search_query(raw))
        else:
            query = _tmdb_sanitize_query(_clean_media_search_query(raw))

        try:
            raw_clean = _tmdb_clean_user_text(raw or "")
            prev_clean = _tmdb_clean_user_text(prev_q or "")
            if raw_clean:
                raw = raw_clean
            if prev_clean:
                prev_q = prev_clean
            if raw_clean and _tmdb_is_refinement(raw_clean):
                query = _tmdb_sanitize_query(_normalize_tmdb_query(raw_clean))
            else:
                query = _tmdb_sanitize_query(_normalize_tmdb_query(_tmdb_clean_user_text(query or "")))

        except Exception:
            pass
        # Stabilize query logic...
        try:
            prev_q_n = (prev_q or "").strip()
            q_n = (query or "").strip()
            raw_n = (raw or "").strip() if "raw" in locals() else (raw_text or "").strip()
            raw_titleish = tmdb_query_compact(raw_n) if raw_n else ""
            if raw_titleish and not is_bad_tmdb_query(raw_titleish):
                if (not q_n) or is_bad_tmdb_query(q_n) or _is_bad_tmdb_candidate(q_n) or (not _mf_is_worthy_tmdb(q_n)):
                    query = raw_titleish
                    q_n = raw_titleish
            if prev_q_n and (
                not q_n or is_bad_tmdb_query(q_n) or _is_bad_tmdb_candidate(q_n) or (not _mf_is_worthy_tmdb(q_n))
            ):
                query = prev_q_n
                q_n = prev_q_n
            if prev_q_n and q_n:
                if _mf_is_worthy_tmdb(prev_q_n) and not _mf_is_worthy_tmdb(q_n):
                    query = prev_q_n
            if prev_q_n and q_n and (" " not in q_n) and len(q_n) <= 10:
                if _is_bad_tmdb_candidate(q_n) or (not _mf_is_worthy_tmdb(q_n)):
                    query = prev_q_n

        except Exception:
            pass
        if is_media:
            if len(query) < 2 and ("фильм" in (raw or "").lower() or "что за" in (raw or "").lower()):
                if user is not None:
                    setattr(user, "assistant_mode", "media")
                    setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
                    if session:
                        await session.commit()
                return MEDIA_NOT_FOUND_REPLY_RU

            cleaned = _normalize_tmdb_query(query)
            query = _tmdb_sanitize_query(_normalize_tmdb_query(cleaned or query))

            try:
                items = []
                items = await _tmdb_best_effort(query, limit=5)
                items = _scrub_media_items(items)
                hints = _parse_media_hints(query)
                if (not items) and hints.get("keywords"):
                    items = await _tmdb_best_effort(hints["keywords"], limit=5)

                if not items and hints.get("cast"):
                    from app.services.media_search import (
                        tmdb_discover_with_people,
                        tmdb_search_person,
                    )

                    for actor in hints["cast"]:
                        pid = await tmdb_search_person(actor)
                        if pid:
                            items = await tmdb_discover_with_people(pid, year=hints.get("year"), kind=hints.get("kind"))
                            if items:
                                break
            except Exception:
                items = []

            try:
                if items and raw and _looks_like_freeform_media_query(raw):
                    items = []

            except Exception:
                pass
            if not items and query and len(query) > 3:
                query = _normalize_tmdb_query(query)

                async def _try_cands(cands: list[str]) -> list[dict]:
                    out: list[dict] = []
                    for c in (cands or [])[:15]:
                        if _is_bad_media_query(c):
                            continue
                        c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
                        if not _good_tmdb_cand(c):
                            continue
                        out = await _tmdb_best_effort(c, limit=5)
                        if out:
                            return out
                    return out

                try:
                    if is_intent_media and (st or sticky_media_db) and text:
                        t = text.strip()
                        if t and (not re.fullmatch(r"\d+", t)) and (not t.startswith("/")):
                            if not _tmdb_is_refinement(t):
                                query = t
                                items = []
                    cands, tag = await web_to_tmdb_candidates(query, use_serpapi=False, session=session, user=user)
                    items = await _try_cands(cands)
                except Exception:
                    items = []

                if (not items) and (os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")):
                    try:
                        cands, tag = await web_to_tmdb_candidates(query, use_serpapi=True, session=session, user=user)
                        items = await _try_cands(cands)

                    except Exception:
                        pass
            if user is not None:
                setattr(user, "assistant_mode", "media")
                setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
                if session:
                    await session.commit()

            if not items:
                if uid:
                    _media_set(uid, query, [])
                return MEDIA_NOT_FOUND_REPLY_RU

            items = _scrub_media_items(items)
            if uid:
                _media_set(uid, query, items)
            return _format_media_ranked(
                query, items, year_hint=_parse_media_hints(query).get("year"), lang=lang, source="tmdb"
            )

    # ---- Normal assistant (non-media) ----
    ctx = await build_context(session, user, lang, plan)
    prev_id = getattr(user, "assistant_prev_response_id", None) if user else None
    if user:
        last_used = getattr(user, "assistant_last_used_at", None)
        if last_used and (datetime.now(timezone.utc) - last_used) > timedelta(hours=24):
            prev_id = None
    prompt = f"Context:\n{ctx}\n\nUser message:\n" + (text or "") + "\n"

    try:
        resp = await client.responses.create(
            previous_response_id=prev_id,
            model=model,
            instructions=_instructions(lang, plan),
            input=prompt,
            max_output_tokens=(260 if plan == "basic" else 650),
        )
    except Exception as e:
        return f"⚠️ API Error: {str(e)}"

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


# =================================================================================================
# === AUTO-PATCH SOLUTION: Aggressive Multi-Search (3 Strikes) for Vision ===
# =================================================================================================


async def run_assistant_vision(
    user: Optional[User],
    image_bytes: bytes,
    caption: str,
    lang: str,
    *,
    session: Any = None,
) -> str:
    """
    Реализация алгоритма '3 удара':
    1. Запускаем Vision Model (OpenAI) и Google Lens ПАРАЛЛЕЛЬНО.
    2. Извлекаем из Vision:
       - Query A: Предполагаемое название (если есть).
       - Query B: Описание сцены (keywords + actors).
    3. Извлекаем из Lens:
       - Query C: Кандидаты из поиска по картинке.
    4. Запускаем поиск в TMDb для A, B и C ОДНОВРЕМЕННО.
    5. Сливаем результаты, удаляем дубли, выдаем ТОП.
    """
    if AsyncOpenAI is None:
        return "🤖 Vision временно недоступен (сервер без openai)."

    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return "❌ OPENAI_API_KEY missing."

    plan = _assistant_plan(user)
    if plan != "pro":
        return "Фото доступно только в PRO (обнови тариф)."

    # quota guard for vision
    if session and user:
        qmsg = await _enforce_quota(session=session, user=user, plan=plan, feature="vision")
        if qmsg:
            return qmsg
    client = AsyncOpenAI(api_key=api_key)
    now = datetime.now(timezone.utc)

    # 1. Проверка кеша (по хэшу картинки)
    img_key = ""
    try:
        img_key = hashlib.sha256(image_bytes).hexdigest()
        cached = _vision_cache_get(img_key)
        if cached:
            return cached

    except Exception:
        pass
    # --- Подготовка задач (Запуск параллельно Lens и Vision) ---

    # Задача 1: Google Lens (фон)
    async def _task_lens():
        try:
            cands, tag = await image_bytes_to_tmdb_candidates(
                image_bytes,
                ext="jpg",
                use_serpapi_lens=True,
                hl=("ru" if (lang or "ru") == "ru" else "en"),
                prefix="frames",
            )
            return cands or []
        except Exception:
            return []

    # Задача 2: OpenAI Vision Model
    async def _task_vision_model():
        prompt_text = (
            caption or ""
        ).strip() or "Identify the movie/series frame. Return JSON with actors, title hints, keywords."
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{b64}"

        instr = (
            ANTI_HALLUCINATION_PREFIX
            + "Ты видишь изображение. Если это кадр из фильма/сериала/аниме — определи источник.\n"
            "Верни СТРОГО JSON:\n"
            '{"actors":["..."],"title_hints":["..."],"keywords":["..."]}\n'
            "- title_hints: название, если уверен\n"
            "- keywords: 3-5 слов о сцене (визуальное описание)\n"
            "ПОТОМ добавь текст: SEARCH_QUERY: <лучший поисковый запрос>"
        )

        try:
            resp = await client.responses.create(
                model=_env("ASSISTANT_VISION_MODEL", "gpt-4.1-mini"),
                instructions=instr,
                input=cast(
                    Any,
                    [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt_text},
                                {"type": "input_image", "image_url": data_url},
                            ],
                        }
                    ],
                ),
                max_output_tokens=450,
            )
            if session:
                await log_llm_usage(
                    session,
                    user_id=getattr(user, "id", None) if user else None,
                    feature="vision",
                    model="gpt-4o",
                    plan=plan,
                    resp=resp,
                    meta={"lang": lang},
                )
            return getattr(resp, "output_text", None) or ""
        except Exception:
            return ""

    # Запускаем Lens и Vision одновременно
    _d("vision.start_parallel")
    lens_cands_future = _asyncio.create_task(_task_lens())
    vision_text_future = _asyncio.create_task(_task_vision_model())

    # Ждем результатов
    vision_text = await vision_text_future
    lens_cands = await lens_cands_future

    if not vision_text and not lens_cands:
        return MEDIA_NOT_FOUND_REPLY_RU

    # --- Парсинг результатов Vision (Разделение на Query A и Query B) ---
    query_a_title = ""
    query_b_desc = ""

    # Извлекаем JSON и текстовые подсказки
    mj = _extract_media_json_from_model_text(vision_text)

    # Формируем Query A (Название)
    # Приоритет: explicit SEARCH_QUERY > JSON title > extracted title
    explicit_q = _extract_search_query_from_text(vision_text)
    json_titles = mj.get("title_hints") or []

    if explicit_q and len(explicit_q) < 50:
        query_a_title = explicit_q
    elif json_titles:
        query_a_title = json_titles[0]
    else:
        query_a_title = _extract_title_like_from_model_text(vision_text)

    # Формируем Query B (Описание)
    # Актеры + ключевые слова
    actors = mj.get("actors") or []
    keywords = mj.get("keywords") or []

    # Если название пустое, но есть актеры - это хороший B-запрос
    parts_b = []
    if actors:
        parts_b.extend(actors[:2])
    if keywords:
        parts_b.extend(keywords[:3])

    if parts_b:
        query_b_desc = " ".join(parts_b)

    # FIX: Логируем реальные кандидаты от Lens, а не count
    _d(
        "vision.parsed",
        query_a=query_a_title,
        query_b=query_b_desc,
        lens_cands=(lens_cands or [])[:5],
    )

    # --- 3 УДАРА (Параллельный поиск в TMDb) ---

    async def _safe_search(q: str, limit: int = 5) -> list[dict]:
        if not q or len(q) < 2:
            return []
        q = _tmdb_sanitize_query(_normalize_tmdb_query(q))
        if _is_bad_media_query(q):
            return []
        # FIX: фильтр от мусорных запросов (hash, filenames)
        if _is_garbage_query(q):
            return []
        try:
            res = await _tmdb_best_effort(q, limit=limit)
            return _scrub_media_items(res)
        except Exception:
            return []

    tasks = []

    # 1. Запрос А (Название)
    if query_a_title:
        tasks.append(_safe_search(query_a_title, limit=5))
    else:
        tasks.append(_asyncio.sleep(0, result=[]))  # заглушка

    # 2. Запрос Б (Описание)
    if query_b_desc:
        tasks.append(_safe_search(query_b_desc, limit=5))
    else:
        tasks.append(_asyncio.sleep(0, result=[]))

    # 3. Запрос В (Lens - берем топ-3 кандидата и ищем)
    # FIX: "Умная" очистка кандидата (выдираем название из кавычек)
    lens_queries = []
    if lens_cands:
        for lc in lens_cands:
            cleaned = _smart_clean_lens_candidate(lc)
            if cleaned and cleaned not in lens_queries and not _is_garbage_query(cleaned):
                lens_queries.append(cleaned)
        lens_queries = lens_queries[:3]

    lens_search_tasks = [_safe_search(lq, limit=3) for lq in lens_queries]

    # Собираем все TMDb задачи
    all_tmdb_futures = tasks + lens_search_tasks

    # ЗАПУСК ВСЕХ ПОИСКОВ
    raw_results = await _asyncio.gather(*all_tmdb_futures)

    # --- МИКСЕР (Сборка и дедупликация) ---
    final_items = []
    seen_ids = set()

    # Порядок приоритета при слиянии:
    # 1. Lens Results (они часто точнее для мемов)
    # 2. Title Results (Query A)
    # 3. Desc Results (Query B)

    # raw_results[0] = Title items
    # raw_results[1] = Desc items
    # raw_results[2:] = Lens items (list of lists)

    title_items = raw_results[0] if len(raw_results) > 0 else []
    desc_items = raw_results[1] if len(raw_results) > 1 else []

    # Flatten Lens items
    lens_items_flat = []
    if len(raw_results) > 2:
        for sublist in raw_results[2:]:
            if sublist:
                lens_items_flat.extend(sublist)

    # Слияние: Lens -> Title -> Desc
    all_sourced = lens_items_flat + title_items + desc_items

    for item in all_sourced:
        mid = item.get("id")
        if not mid:
            continue
        if mid in seen_ids:
            continue

        # Фильтр уверенности (по желанию)
        # if not _media_confident(item): continue

        seen_ids.add(mid)
        final_items.append(item)

    if not final_items:
        # Fallback: просто вернем текст модели, если ничего не нашли
        if vision_text:
            return vision_text
        return MEDIA_NOT_FOUND_REPLY_RU

    # --- СОХРАНЕНИЕ И ОТВЕТ ---

    # Сохраняем ВСЕ найденные варианты, чтобы кнопка "Другие варианты" работала
    # Используем лучший запрос как заголовок (либо A, либо первый из Lens)
    best_query = query_a_title or (lens_queries[0] if lens_queries else "Image Search")

    if user is not None:
        setattr(user, "assistant_mode", "media")
        setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
        if session:
            await session.commit()

    uid = _media_uid(user)
    if uid:
        _media_set(uid, best_query, final_items)

    # Формируем ответ (берем ТОП-3 из общей кучи)
    reply = _format_media_ranked(
        best_query,
        final_items,  # форматтер сам обрежет до лимита
        year_hint=None,
        lang=lang,
        source="tmdb",
    )

    if img_key:
        _vision_cache_set(img_key, reply)

    return reply
