from __future__ import annotations

# ruff: noqa: E402
# pyright: reportOptionalSubscript=false

import os
import os as _os
import time as _time
import contextvars as _contextvars
import uuid as _uuid
import asyncio
import asyncio as _asyncio
import io
import re
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select, func, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

import httpx
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- Imports from app ---
from app.bot import bot
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
from app.services.media.safety import _scrub_media_items
from app.services.media.session import _MEDIA_SESSIONS, _media_get, _media_set, _media_uid
from app.services.media.vision_parse import (
    _extract_media_json_from_model_text,
    _extract_search_query_from_text,
    _extract_title_like_from_model_text,
)

from app.keyboards import (
    get_main_kb,
    is_admin_btn,
    is_back_btn,
    is_data_privacy_btn,
    is_journal_btn,
    is_journal_history_btn,
    is_journal_range_btn,
    is_journal_search_btn,
    is_journal_today_btn,
    is_journal_week_btn,
    is_language_btn,
    is_meditation_btn,
    is_music_btn,
    is_premium_card_btn,
    is_premium_info_btn,
    is_premium_stars_btn,
    is_privacy_btn,
    is_report_bug_btn,
    is_root_assistant_btn,
    is_root_calories_btn,
    is_root_journal_btn,
    is_root_media_btn,
    is_root_premium_btn,
    is_root_proactive_btn,
    is_root_reminders_btn,
    is_root_stats_btn,
)

# admin check (best-effort)
try:
    from app.handlers.admin import is_admin_tg  # type: ignore
except Exception:  # pragma: no cover
    def is_admin_tg(tg_id: int, /) -> bool:
        return False

try:
    from openai import AsyncOpenAI
except ModuleNotFoundError:
    AsyncOpenAI = None

router = Router(name="assistant")

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
    if not _TRACE_ON: return
    try: logger.info("[trace] %s | %s | %s", _atrace_id(), stage, kv)
    except Exception: pass

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
    try: _trace_id_var.set(tid)
    except Exception: pass

def _dbg_media(logger, tag: str, **kv):
    try: logger.info("[media][dbg] %s | %s", tag, kv)
    except Exception: pass

# --- FlowPatch: media query clean + refinement detection (assistant) ---
_TMDB_STOPWORDS = {"photo", "<photo>", "уточнение", "уточнение:", "уточни", "дай", "другие", "варианты", "жанр", "страна", "год", "серия", "эпизод", "сезон", "film", "movie", "series", "tv", "what", "is", "the", "a", "an", "drama", "romance", "prison", "fence"}
_LENS_BLOCKLIST = {"movie reviews", "full episode", "youtube", "tiktok", "instagram", "video", "clip", "scene", "4k", "1080p", "hd", "watch online", "trailer", "official trailer", "teaser", "review"}

def _tmdb_clean_user_text(text: str) -> str:
    if not text: return ""
    t = text.strip()
    t = t.replace("<photo>", " ").replace("photo", " ")
    t = re.sub(r"(?i)\bуточнение\s*:\s*", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 140: t = t[:140].rsplit(" ", 1)[0].strip()
    return t

def _tmdb_is_refinement(text: str) -> bool:
    if not text: return False
    t = text.lower().strip()
    if any(k in t for k in ("уточнение", "уточни", "дай другие", "другие варианты", "коротко")): return True
    if re.search(r"\b(19\d{2}|20\d{2})\b", t): return True
    parts = t.split()
    if 1 <= len(parts) <= 2 and len(t) <= 18: return True
    hint_words = ("год", "акт", "актер", "актёр", "страна", "язык", "серия", "эпизод", "сезон", "сша", "америка", "usa", "us", "uk", "нетфликс", "netflix", "hbo", "amazon", "комедия", "драма", "боевик", "триллер", "ужасы", "мелодрама")
    return any(w in t for w in hint_words)

def _is_garbage_query(q: str) -> bool:
    if not q: return True
    q_lower = q.strip().lower()
    if len(q_lower) < 3: return True
    for word in q_lower.split():
        if len(word) > 6 and any(c.isdigit() for c in word) and any(c.isalpha() for c in word): return True
    for block in _LENS_BLOCKLIST:
        if block in q_lower: return True
    return False

def _smart_clean_lens_candidate(text: str) -> str:
    if not text: return ""
    text_clean = re.sub(r"\(@[^)]+\)", "", text)
    quotes = re.findall(r"«([^»\n]+)(?:»|$)", text_clean) or re.findall(r'"([^"\n]+)(?:"|$)', text_clean)
    if quotes:
        longest = max(quotes, key=len)
        cleaned = re.sub(r"[\.…]+$", "", longest).strip()
        if len(cleaned) > 2 and not _is_garbage_query(cleaned): return cleaned
    anchors = ["сериал", "фильм", "movie", "film", "сцена из", "scene from", "watch"]
    lower = text_clean.lower()
    for anchor in anchors:
        if f" {anchor} " in f" {lower} ":
            match = re.search(r"(?i)\b" + re.escape(anchor) + r"\b", text_clean)
            if match:
                candidate = text_clean[match.end() :].strip()
                candidate = re.sub(r"\b(19|20)\d{2}\b.*", "", candidate)
                candidate = re.sub(r"^[^a-zA-Zа-яА-Я0-9]+", "", candidate)
                if len(candidate) > 2 and not _is_garbage_query(candidate): return candidate.strip()
    candidate = text_clean
    candidate = re.sub(r"(?i)\b(сериал|фильм|кино|movie|film|scene from|сцена из)\b", "", candidate)
    candidate = re.sub(r"[\.…]+$", "", candidate)
    candidate = re.sub(r"[^\w\s\-\.,:!?'()]+", " ", candidate, flags=re.UNICODE)
    if ":" in candidate and len(candidate.split()) > 5:
        parts = candidate.split(":")
        if len(parts[0].strip()) > 3: candidate = parts[0]
    return re.sub(r"\s+", " ", candidate).strip()

try:
    from app.services.media_text import is_generic_media_caption as _is_generic_media_caption
except Exception:
    def _is_generic_media_caption(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t: return True
        t = re.sub(r"\s+", " ", t).strip()
        return t in {"откуда кадр", "откуда кадр?", "что за фильм", "что за фильм?", "что за сериал", "что за сериал?", "что за мультик", "что за мультик?", "как называется", "как называется?"}

ANTI_HALLUCINATION_PREFIX: str = ""

_VISION_IMG_CACHE: dict[str, tuple[float, str]] = {}
_VISION_IMG_CACHE_TTL_SEC = 30 * 60

def _vision_cache_get(key: str) -> str | None:
    try:
        v = _VISION_IMG_CACHE.get(key)
        if not v: return None
        ts, reply = v
        if (_time.time() - ts) > _VISION_IMG_CACHE_TTL_SEC:
            _VISION_IMG_CACHE.pop(key, None)
            return None
        return reply
    except Exception: return None

def _vision_cache_set(key: str, reply: str) -> None:
    try:
        if key and reply: _VISION_IMG_CACHE[key] = (_time.time(), reply)
    except Exception: pass

try:
    from app.services.media_search import tmdb_search_multi
except Exception:
    async def tmdb_search_multi(*args: Any, **kwargs: Any) -> list[dict]: return []

try:
    from app.services.media_web_pipeline import web_to_tmdb_candidates
except Exception:
    async def web_to_tmdb_candidates(*args: Any, **kwargs: Any) -> tuple[list[str], str]: return ([], "web_stub")

try:
    from app.services.media_web_pipeline import image_bytes_to_tmdb_candidates
except Exception:
    async def image_bytes_to_tmdb_candidates(*args: Any, **kwargs: Any) -> tuple[list[str], str]: return ([], "lens_stub")

try:
    from app.services.media_id import trace_moe_identify
except Exception:
    async def trace_moe_identify(*args: Any, **kwargs: Any) -> Optional[dict]: return None

def _media_confident(item: dict) -> bool:
    try:
        pop = float(item.get("popularity") or 0)
        va = float(item.get("vote_average") or 0)
    except Exception: return False
    return (pop >= 25 and va >= 6.8) or (pop >= 60) or (va >= 7.6)

def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v else default

def _pick_model() -> str:
    return _env("ASSISTANT_MODEL", "gpt-4o-mini")

def _user_name(user: Optional[User]) -> str:
    for attr in ("first_name", "name", "username"):
        v = getattr(user, attr, None)
        if v: return str(v)
    return "друг"

def _user_tz(user: Optional[User]) -> ZoneInfo:
    tz_name = getattr(user, "tz", None) or "UTC"
    try: return ZoneInfo(str(tz_name))
    except Exception: return ZoneInfo("UTC")


# ===========================
# === Quotas / Daily limits ==
# ===========================

def _assistant_plan(user: Optional[User]) -> str:
    if not user: return "free"
    now = datetime.now(timezone.utc)
    pu = getattr(user, "premium_until", None)
    is_prem = bool(getattr(user, "is_premium", False))
    db_plan = str(getattr(user, "premium_plan", "")).strip().lower()
    if pu is not None and pu.tzinfo is None:
        pu = pu.replace(tzinfo=timezone.utc)
    if not is_prem and (pu is None or pu <= now): return "free"
    if db_plan in ["pro", "max", "pro_max"]: return db_plan
    return "basic"

def _quota_limits(plan: str, feature: str) -> int:
    """Жесткие дневные лимиты (в штуках запросов)."""
    plan_n = (plan or "free").strip().lower()
    feat = (feature or "assistant").strip().lower()

    if plan_n == "free": return 0

    if plan_n == "basic":
        if feat == "assistant": return 50
        return 0

    if plan_n == "pro":
        if feat == "assistant": return 200
        if feat == "vision": return 10
        if feat == "assistant_web": return 5
        return 0

    if plan_n in ["max", "pro_max"]:
        if feat == "assistant": return 500
        if feat == "vision": return 25
        if feat == "assistant_web": return 15
        return 0

    return 0

async def _usage_last_24h(session: Any, user_id: int, feature: str) -> int:
    """Считает КОЛИЧЕСТВО ЗАПРОСОВ (строк в БД) за 24 часа."""
    if not session or not user_id: return 0
    try:
        from app.models.llm_usage import LLMUsage
        since = datetime.utcnow() - timedelta(hours=24)
        q = select(func.count(LLMUsage.id)).where(
            LLMUsage.user_id == user_id,
            LLMUsage.feature == feature,
            LLMUsage.created_at >= since,
        )
        res = await session.execute(q)
        v = res.scalar_one() if res is not None else 0
        return int(v or 0)
    except Exception as e:
        import logging
        logging.error(f"Error checking usage: {e}")
        return 0

def _quota_msg_ru(feature: str, used: int, limit: int) -> str:
    feat = "🌐 Web" if (feature == "assistant_web") else ("📷 Фото" if feature == "vision" else "🤖 Ассистент")
    return (
        f"⛔️ Лимит на сегодня по режиму {feat} исчерпан.\n"
        f"Использовано: {used} из {limit} запросов за последние 24 часа.\n\n"
        "Попробуй завтра или обнови тариф."
    )

def _soft_quota_web_ru(plan: str) -> str:
    p = (plan or "basic").strip().lower()
    plan_label = "Basic" if p == "basic" else ("Pro" if p == "pro" else ("Max" if p in {"pro_max", "max"} else p))
    return (
        f"⚠️ Лимит на Web-поиск по тарифу {plan_label} исчерпан.\n\n"
        "Ты использовал лимит текущего тарифа.\n"
        "Следующее обновление через 24 часа.\n\n"
        "Или можешь увеличить лимит прямо сейчас:"
    )

async def _enforce_quota(*, session: Any, user: Optional[User], plan: str, feature: str) -> Optional[str]:
    if not user or not getattr(user, "id", None): return None
    if feature == "assistant_web" and plan not in ["pro", "max", "pro_max"]:
        return "🌐 Web-разбор доступен только в PRO. Открой Premium и выбери PRO-тариф."
    usage_feature = "assistant_web" if feature == "assistant_web" else ("vision" if feature == "vision" else "assistant")
    limit = _quota_limits(plan, usage_feature)
    if limit <= 0:
        if usage_feature == "vision": return "📷 Поиск по фото доступен только в PRO."
        return "Функция недоступна на твоем тарифе."
    used = await _usage_last_24h(session, int(user.id), usage_feature)
    if used >= limit: return _quota_msg_ru(feature, used, limit)
    return None

def _now_str_user(user: Optional[User]) -> str:
    tz = _user_tz(user)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")

def _is_media_query(text: str) -> bool:
    t = (text or "").lower()
    keys = ("фильм", "сериал", "кино", "мульт", "мультик", "лента", "кадр", "по кадру", "по этому кадру", "season", "episode", "movie", "tv", "series", "актёр", "актер", "режисс", "персонаж", "как называется", "что за фильм", "что за сериал", "что за мультик")
    return any(k in t for k in keys)

def _is_noise(text: str) -> bool:
    s = (text or "").strip()
    if not s: return True
    letters = sum(ch.isalpha() for ch in s)
    if letters == 0: return True
    if len(s) <= 3: return True
    tokens = re.findall(r"[A-Za-zА-Яа-яЁёІіЇїЄє]+", s.lower())
    if tokens:
        most = max(tokens.count(x) for x in set(tokens))
        if most / max(1, len(tokens)) >= 0.6 and len(tokens) >= 4: return True
        if len(tokens) >= 4:
            uniq = set(tokens)
            if len(uniq) <= 2 and all(tokens.count(t) >= 2 for t in uniq): return True
    if "_" in s and " " not in s and len(s) <= 20: return True
    return False

def _as_user_ts(user: Optional[User], ts: Any) -> str:
    if ts is None: return "?"
    try:
        tz = _user_tz(user)
        if getattr(ts, "tzinfo", None) is None: ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    except Exception:
        try: return ts.strftime("%Y-%m-%d %H:%M")
        except Exception: return "?"

async def _fetch_recent_journal(session: Any, user: Optional[User], *, limit: int = 30, take: int = 5) -> list[tuple[str, str]]:
    if not session or not user: return []
    q = select(JournalEntry.created_at, JournalEntry.text).where(JournalEntry.user_id == user.id).order_by(desc(JournalEntry.created_at)).limit(limit)
    res = await session.execute(q)
    rows = res.all()
    out: list[tuple[str, str]] = []
    for created_at, text in rows:
        txt = (text or "").strip()
        if _is_noise(txt): continue
        created_str = _as_user_ts(user, created_at)
        out.append((created_str, txt[:700]))
        if len(out) >= take: break
    return out

async def build_context(session: Any, user: Optional[User], lang: str, plan: str) -> str:
    parts: list[str] = []
    parts.append(f"Time now: {_now_str_user(user)}")
    if user:
        parts.append(f"User: id={getattr(user, 'id', None)}, tg_id={getattr(user, 'tg_id', None)}, name={_user_name(user)}, tz={getattr(user, 'tz', None)}")
        last_used = getattr(user, "assistant_last_used_at", None)
        if last_used: parts.append(f"Assistant last used at: {last_used}")
        profile = getattr(user, "assistant_profile_json", None)
        if profile:
            parts.append("Assistant profile (long-term):")
            parts.append(str(profile)[:2000])
    take = 0 if plan in {"free", "basic"} else 5
    recent = await _fetch_recent_journal(session, user, limit=30, take=take)
    if recent:
        parts.append("Recent journal entries:")
        for ts, txt in recent: parts.append(f"- [{ts}] {txt}")
    return "\n".join(parts)

def _instructions(lang: str, plan: str) -> str:
    base_map = {
        "ru": "Ты — личный помощник в Telegram. Пиши по-русски.\nНе оценивай настроение и не делай психоанализ.\nЕсли данных не хватает — задай 1 уточняющий вопрос.\n",
        "uk": "Ти — особистий помічник у Telegram. Пиши українською.\nНе оцінюй настрій і не роби психоаналіз.\nЯкщо бракує даних — постав 1 уточнювальне питання.\n",
        "en": "You are a personal Telegram assistant. Reply in English.\nDo not psychoanalyze mood.\nIf info is missing — ask 1 clarifying question.\n",
    }
    base = base_map.get(lang, base_map["en"])
    style = "Правила ответа:\n- Не используй шаблоны 'Суть/План/Шаги' и нумерацию, если не просят.\n- Без психоанализа и диагнозов.\n- Коротко и по делу.\n"
    tricks = "\n\nСЕКРЕТНЫЕ НАВЫКИ (Применяй только если это в тему):\n1. Формула Карвонена: Если юзер спрашивает про бег, пульс, кардио или похудение, рассчитай ему пульсовые зоны по формуле Карвонена. Запроси возраст и пульс покоя, если их нет.\n2. Музыкатерапия: Если юзер пишет, что он выгорел, устал или в депрессии, помимо слов поддержки, посоветуй ему послушать конкретный Lo-Fi/Ambient трек или классику (напиши название и автора) и порекомендуй включить раздел 'Медитация' в боте."
    
    if plan == "basic": return base + style + "Режим BASIC:\n- 2–6 предложений.\n- Без планов и стратегий без запроса.\n- Журнал не использовать как память.\n" + tricks
    return base + style + "Режим PRO:\n- Можно использовать последние записи журнала как контекст.\n- Можно предлагать чеклисты и структуру.\n- Можно задать до 2 уточняющих вопросов.\n- Стиль: умный близкий помощник.\n" + tricks

async def run_assistant(user: Optional[User], text: str, lang: str, *, session: Any = None, has_media: bool = False) -> str:
    if AsyncOpenAI is None: return "🤖 Ассистент временно недоступен (сервер без openai).\nПопробуй позже или напиши в поддержку."
    api_key = _env("OPENAI_API_KEY")
    if not api_key: return "❌ OPENAI_API_KEY missing."

    client = AsyncOpenAI(api_key=api_key)
    model = _pick_model()
    plan = _assistant_plan(user)

    if session and user:
        qmsg = await _enforce_quota(session=session, user=user, plan=plan, feature="assistant")
        if qmsg: return qmsg
        
    query = ""
    prev_q = ""
    items = []
    raw = (text or "").strip()
    web_mode = False
    
    t0 = (text or "").strip()
    if t0 and (t0.lower().startswith("web:") or ("http://" in t0) or ("https://" in t0)):
        web_mode = True
        if session and user:
            qmsg = await _enforce_quota(session=session, user=user, plan=plan, feature="assistant_web")
            if qmsg: return qmsg

        q_or_url = t0[4:].strip() if t0.lower().startswith("web:") else t0
        url = extract_first_url(q_or_url) or extract_first_url(t0)
        try:
            if url:
                title, page_text = await fetch_page_text(url, max_chars=12000)
                if page_text:
                    text = f"Ты — аналитик. Разбери веб-страницу.\nДай:\n1) 6–10 буллетов по сути\n2) 2 короткие цитаты (1 строка каждая)\n3) 1 практический вывод\n\nURL: {url}\nTITLE: {title}\n\nTEXT:\n{page_text}\n"
                    raw = (text or "").strip()
            else:
                results = await serpapi_search(session, cast(User, user), q_or_url, count=5)
                if results and isinstance(results, list) and isinstance(results[0], dict) and results[0].get('quota_exceeded'):
                    plan = (results[0].get('plan') or getattr(user, 'premium_plan', None) or 'basic')
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
                    text = f"Ты — аналитик. Суммируй результаты поиска и выдержки страниц.\nДай:\n1) 8–12 буллетов\n2) Отметь противоречия (если есть)\n3) Источники указывай как [1], [2]\n\nQUERY: {q_or_url}\n\n" + "\n\n".join(parts)
                    raw = (text or "").strip()
        except Exception: pass

    if web_mode:
        ctx = await build_context(session, user, lang, plan)
        prev_id = getattr(user, "assistant_prev_response_id", None) if user else None
        if user:
            last_used = getattr(user, "assistant_last_used_at", None)
            if last_used and (datetime.now(timezone.utc) - last_used) > timedelta(hours=24): prev_id = None
        prompt = f"Context:\n{ctx}\n\nUser message:\n" + (text or "") + "\n"

        try:
            resp = await client.responses.create(
                previous_response_id=prev_id,
                model=model,
                instructions=_instructions(lang, plan),
                input=prompt,
                max_output_tokens=(260 if plan == "basic" else 650),
            )
        except Exception as e: return f"⚠️ API Error: {str(e)}"

        if session:
            try:
                import logging
                total_tokens = getattr(resp.usage, "total_token_count", 1000) if hasattr(resp, "usage") else 1000
                await session.execute(
                    sql_text("INSERT INTO llm_usage (user_id, feature, model, plan, input_tokens, output_tokens, total_tokens, cost_usd_micros, meta, created_at) VALUES (:u, 'assistant_web', :m, :p, 0, 0, :t, 0, '{}'::json, :ts)"),
                    {"u": user.id, "m": model, "p": plan, "t": total_tokens, "ts": datetime.utcnow()}
                )
                await session.commit()
            except Exception as e:
                import logging
                logging.error(f"Failed to log tokens for web: {e}")

        out_text = (getattr(resp, "output_text", None) or "").strip()
        resp_id = getattr(resp, "id", None)
        if session and user and resp_id:
            changed = False
            if user.assistant_prev_response_id != str(resp_id):
                user.assistant_prev_response_id = str(resp_id)
                changed = True
            user.assistant_last_used_at = datetime.now(timezone.utc)
            changed = True
            if changed: await session.commit()

        if out_text: return out_text
        try: return str(getattr(resp, "output", "")).strip() or "⚠️ Empty response."
        except Exception: return "⚠️ Не смог прочитать ответ модели."

    now = datetime.now(timezone.utc)
    kind_marker = _extract_media_kind_marker(text)
    if kind_marker: return MEDIA_VIDEO_STUB_REPLY_RU

    uid = _media_uid(user)
    st = _media_get(uid)

    sticky_media_db = False
    if user:
        mode = getattr(user, "assistant_mode", None)
        until = getattr(user, "assistant_mode_until", None)
        if mode == "media" and until and until > now: sticky_media_db = True

    is_nav = False
    if text:
        t_low = text.lower().strip()
        if any(k in t_low for k in ("другие", "варианты", "еще", "ещё")) or re.fullmatch(r"\d{1,2}", t_low):
            is_nav = True

    if is_nav:
        is_intent_media = True
        intent = Intent.MEDIA_TEXT
    else:
        intent_res = detect_intent((text or "").strip() if text else None, has_media=bool(has_media))
        intent = getattr(intent_res, "intent", None) or intent_res
        is_intent_media = intent in (Intent.MEDIA_IMAGE, Intent.MEDIA_TEXT)

    if is_intent_media and (not has_media) and (not sticky_media_db) and (not st) and (not _is_media_query(text or "")):
        is_intent_media = False
        intent = None

    if not is_intent_media and not is_nav:
        if uid: _MEDIA_SESSIONS.pop(uid, None)
        if user is not None:
            try:
                setattr(user, "assistant_mode", None)
                setattr(user, "assistant_mode_until", now - timedelta(seconds=1))
                if session: await session.commit()
            except Exception: pass

    is_media = bool(has_media) or bool(is_intent_media) or (is_nav and bool(st))

    if is_media:
        _d("media.enter", is_media=is_media, sticky_media_db=sticky_media_db, has_st=bool(st), uid=uid)
        raw_text = (text or "").strip()

        if st and ("другие" in raw_text.lower() or "варианты" in raw_text.lower()):
            opts = st.get("items") or []
            prev_q = st.get("query") or "Результаты поиска"
            if len(opts) > 3:
                rotated_opts = opts[3:] + opts[:3]
                _media_set(uid, prev_q, rotated_opts)
                return _format_media_ranked(prev_q, rotated_opts, year_hint=_parse_media_hints(prev_q).get("year"), lang=lang, source="cache") + "\n\n(Показаны следующие варианты 🔄)"
            else: return "📭 Больше вариантов нет. Попробуй уточнить запрос (год, актер) или скинь другой кадр."

        if st and _looks_like_choice(raw_text):
            idx = int(raw_text) - 1
            opts = st.get("items") or []
            if 0 <= idx < len(opts):
                picked = opts[idx]
                return _format_media_pick(picked) + "\n\nХочешь — напиши другое название/описание, я поищу ещё."

        if st and _is_asking_for_title(raw_text):
            opts = st.get("items") or []
            if not opts: return MEDIA_NOT_FOUND_REPLY_RU
            return build_media_context(opts) + "\n\nКнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить"

        raw = raw_text
        if st and re.search(r"(?i)\b(не\s*то|не\s*подходит|ничего\s*не|такого\s*фильма|не\s*существует)\b", raw):
            return MEDIA_NOT_FOUND_REPLY_RU

        raw = _normalize_tmdb_query(raw)
        prev_q = ((st.get("query") if st else "") or "").strip()

        if st and prev_q and raw and (len(raw) <= 140):
            if _tmdb_is_refinement(raw) or len(raw.split()) <= 2:
                if _looks_like_year_or_hint(raw): query = f"{prev_q} {raw}"
                else: query = prev_q
            else: query = _tmdb_sanitize_query(_clean_media_search_query(raw))
        else: query = _tmdb_sanitize_query(_clean_media_search_query(raw))

        try:
            raw_clean = _tmdb_clean_user_text(raw or "")
            prev_clean = _tmdb_clean_user_text(prev_q or "")
            if raw_clean: raw = raw_clean
            if prev_clean: prev_q = prev_clean
            if raw_clean and _tmdb_is_refinement(raw_clean): query = _tmdb_sanitize_query(_normalize_tmdb_query(raw_clean))
            else: query = _tmdb_sanitize_query(_normalize_tmdb_query(_tmdb_clean_user_text(query or "")))
        except Exception: pass

        try:
            prev_q_n = (prev_q or "").strip()
            q_n = (query or "").strip()
            raw_n = (raw or "").strip() if "raw" in locals() else (raw_text or "").strip()
            raw_titleish = tmdb_query_compact(raw_n) if raw_n else ""
            if raw_titleish and not is_bad_tmdb_query(raw_titleish):
                if (not q_n) or is_bad_tmdb_query(q_n) or _is_bad_tmdb_candidate(q_n) or (not _mf_is_worthy_tmdb(q_n)):
                    query = raw_titleish
                    q_n = raw_titleish
            if prev_q_n and (not q_n or is_bad_tmdb_query(q_n) or _is_bad_tmdb_candidate(q_n) or (not _mf_is_worthy_tmdb(q_n))):
                query = prev_q_n
                q_n = prev_q_n
            if prev_q_n and q_n:
                if _mf_is_worthy_tmdb(prev_q_n) and not _mf_is_worthy_tmdb(q_n): query = prev_q_n
            if prev_q_n and q_n and (" " not in q_n) and len(q_n) <= 10:
                if _is_bad_tmdb_candidate(q_n) or (not _mf_is_worthy_tmdb(q_n)): query = prev_q_n
        except Exception: pass

        if is_media:
            if len(query) < 2 and ("фильм" in (raw or "").lower() or "что за" in (raw or "").lower()):
                if user is not None:
                    setattr(user, "assistant_mode", "media")
                    setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
                    if session: await session.commit()
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
                    from app.services.media_search import tmdb_discover_with_people, tmdb_search_person
                    for actor in hints["cast"]:
                        pid = await tmdb_search_person(actor)
                        if pid:
                            items = await tmdb_discover_with_people(pid, year=hints.get("year"), kind=hints.get("kind"))
                            if items: break
            except Exception: items = []

            try:
                if items and raw and _looks_like_freeform_media_query(raw): items = []
            except Exception: pass

            if not items and query and len(query) > 3:
                query = _normalize_tmdb_query(query)

                async def _try_cands(cands: list[str]) -> list[dict]:
                    out: list[dict] = []
                    for c in (cands or [])[:15]:
                        if _is_bad_media_query(c): continue
                        c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
                        if not _good_tmdb_cand(c): continue
                        out = await _tmdb_best_effort(c, limit=5)
                        if out: return out
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
                except Exception: items = []

                if (not items) and (os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")):
                    try:
                        cands, tag = await web_to_tmdb_candidates(query, use_serpapi=True, session=session, user=user)
                        items = await _try_cands(cands)
                    except Exception: pass

            if user is not None:
                setattr(user, "assistant_mode", "media")
                setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
                if session: await session.commit()

            if not items:
                if uid: _media_set(uid, query, [])
                return MEDIA_NOT_FOUND_REPLY_RU

            items = _scrub_media_items(items)
            if uid: _media_set(uid, query, items)
            return _format_media_ranked(query, items, year_hint=_parse_media_hints(query).get("year"), lang=lang, source="tmdb")

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
    except Exception as e: return f"⚠️ API Error: {str(e)}"

    if session:
        # Для текстового ассистента списываем токены
        try:
            import logging
            total_tokens = getattr(resp.usage, "total_token_count", 500) if hasattr(resp, "usage") else 500
            await session.execute(
                sql_text("INSERT INTO llm_usage (user_id, feature, model, plan, input_tokens, output_tokens, total_tokens, cost_usd_micros, meta, created_at) VALUES (:u, 'assistant', :m, :p, 0, 0, :t, 0, '{}'::json, :ts)"),
                {"u": user.id, "m": model, "p": plan, "t": total_tokens, "ts": datetime.utcnow()}
            )
            await session.commit()
        except Exception as e:
            import logging
            logging.error(f"Failed to log tokens for text assistant: {e}")

    out_text = (getattr(resp, "output_text", None) or "").strip()
    resp_id = getattr(resp, "id", None)
    if session and user and resp_id:
        changed = False
        if user.assistant_prev_response_id != str(resp_id):
            user.assistant_prev_response_id = str(resp_id)
            changed = True
        user.assistant_last_used_at = datetime.now(timezone.utc)
        changed = True
        if changed: await session.commit()

    if out_text: return out_text
    try: return str(getattr(resp, "output", "")).strip() or "⚠️ Empty response."
    except Exception: return "⚠️ Не смог прочитать ответ модели."


async def run_assistant_vision(
    user: Optional[User],
    image_bytes: bytes,
    caption: str,
    lang: str,
    *,
    session: Any = None,
) -> str:
    if AsyncOpenAI is None: return "🤖 Vision временно недоступен (сервер без openai)."
    api_key = _env("OPENAI_API_KEY")
    if not api_key: return "❌ OPENAI_API_KEY missing."

    plan = _assistant_plan(user)
    if plan not in ["pro", "max", "pro_max"]: return "Фото доступно только в PRO (обнови тариф)."

    if session and user:
        qmsg = await _enforce_quota(session=session, user=user, plan=plan, feature="vision")
        if qmsg: return qmsg
        
    client = AsyncOpenAI(api_key=api_key)
    now = datetime.now(timezone.utc)

    img_key = ""
    try:
        img_key = hashlib.sha256(image_bytes).hexdigest()
        cached = _vision_cache_get(img_key)
        if cached: return cached
    except Exception: pass

    async def _task_lens():
        try:
            cands, tag = await image_bytes_to_tmdb_candidates(image_bytes, ext="jpg", use_serpapi_lens=True, hl=("ru" if (lang or "ru") == "ru" else "en"), prefix="frames")
            return cands or []
        except Exception: return []

    async def _task_vision_model():
        prompt_text = (caption or "").strip() or "Identify the movie/series frame. Return JSON with actors, title hints, keywords."
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{b64}"
        instr = (ANTI_HALLUCINATION_PREFIX + "Ты видишь изображение. Если это кадр из фильма/сериала/аниме — определи источник.\nВерни СТРОГО JSON:\n" + '{"actors":["..."],"title_hints":["..."],"keywords":["..."]}\n' + "- title_hints: название, если уверен\n- keywords: 3-5 слов о сцене (визуальное описание)\nПОТОМ добавь текст: SEARCH_QUERY: <лучший поисковый запрос>")
        try:
            resp = await client.responses.create(
                model=_env("ASSISTANT_VISION_MODEL", "gpt-4.1-mini"),
                instructions=instr,
                input=cast(Any, [{"role": "user", "content": [{"type": "input_text", "text": prompt_text}, {"type": "input_image", "image_url": data_url}]}]),
                max_output_tokens=450,
            )
            if session:
                try:
                    import logging
                    await session.execute(
                        sql_text("INSERT INTO llm_usage (user_id, feature, model, plan, input_tokens, output_tokens, total_tokens, cost_usd_micros, meta, created_at) VALUES (:u, 'vision', :m, :p, 0, 0, 800, 0, '{}'::json, :ts)"),
                        {"u": user.id, "m": "gpt-4o-mini", "p": plan, "ts": datetime.utcnow()}
                    )
                    await session.commit()
                except Exception as e:
                    import logging
                    logging.error(f"Failed to log tokens for vision assistant: {e}")
                    
            return getattr(resp, "output_text", None) or ""
        except Exception: return ""

    _d("vision.start_parallel")
    lens_cands_future = _asyncio.create_task(_task_lens())
    vision_text_future = _asyncio.create_task(_task_vision_model())

    vision_text = await vision_text_future
    lens_cands = await lens_cands_future

    if not vision_text and not lens_cands: return MEDIA_NOT_FOUND_REPLY_RU

    query_a_title = ""
    query_b_desc = ""

    mj = _extract_media_json_from_model_text(vision_text)
    explicit_q = _extract_search_query_from_text(vision_text)
    json_titles = mj.get("title_hints") or []

    if explicit_q and len(explicit_q) < 50: query_a_title = explicit_q
    elif json_titles: query_a_title = json_titles[0]
    else: query_a_title = _extract_title_like_from_model_text(vision_text)

    actors = mj.get("actors") or []
    keywords = mj.get("keywords") or []
    parts_b = []
    if actors: parts_b.extend(actors[:2])
    if keywords: parts_b.extend(keywords[:3])
    if parts_b: query_b_desc = " ".join(parts_b)

    _d("vision.parsed", query_a=query_a_title, query_b=query_b_desc, lens_cands=(lens_cands or [])[:5])

    async def _safe_search(q: str, limit: int = 5) -> list[dict]:
        if not q or len(q) < 2: return []
        q = _tmdb_sanitize_query(_normalize_tmdb_query(q))
        if _is_bad_media_query(q) or _is_garbage_query(q): return []
        try:
            res = await _tmdb_best_effort(q, limit=limit)
            return _scrub_media_items(res)
        except Exception: return []

    tasks = []
    if query_a_title: tasks.append(_safe_search(query_a_title, limit=5))
    else: tasks.append(_asyncio.sleep(0, result=[]))
    if query_b_desc: tasks.append(_safe_search(query_b_desc, limit=5))
    else: tasks.append(_asyncio.sleep(0, result=[]))

    lens_queries = []
    if lens_cands:
        for lc in lens_cands:
            cleaned = _smart_clean_lens_candidate(lc)
            if cleaned and cleaned not in lens_queries and not _is_garbage_query(cleaned): lens_queries.append(cleaned)
        lens_queries = lens_queries[:3]

    lens_search_tasks = [_safe_search(lq, limit=3) for lq in lens_queries]
    all_tmdb_futures = tasks + lens_search_tasks
    raw_results = await _asyncio.gather(*all_tmdb_futures)

    final_items = []
    seen_ids = set()

    title_items = raw_results[0] if len(raw_results) > 0 else []
    desc_items = raw_results[1] if len(raw_results) > 1 else []
    lens_items_flat = []
    if len(raw_results) > 2:
        for sublist in raw_results[2:]:
            if sublist: lens_items_flat.extend(sublist)

    all_sourced = lens_items_flat + title_items + desc_items

    for item in all_sourced:
        mid = item.get("id")
        if not mid or mid in seen_ids: continue
        seen_ids.add(mid)
        final_items.append(item)

    if not final_items:
        if vision_text: return vision_text
        return MEDIA_NOT_FOUND_REPLY_RU

    best_query = query_a_title or (lens_queries[0] if lens_queries else "Image Search")

    if user is not None:
        setattr(user, "assistant_mode", "media")
        setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
        if session: await session.commit()

    uid = _media_uid(user)
    if uid: _media_set(uid, best_query, final_items)

    reply = _format_media_ranked(best_query, final_items, year_hint=None, lang=lang, source="tmdb")
    if img_key: _vision_cache_set(img_key, reply)

    return reply

# ===== upgrade marker -> inline button (web quota softback) =====

_UPGRADE_MARKER = "[Upgrade to Pro]"

def _normalize_lang(code: Optional[str]) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    return "ru"

def _tr(lang: str, ru: str, uk: str, en: str) -> str:
    loc = _normalize_lang(lang)
    if loc == "uk":
        return uk
    if loc == "en":
        return en
    return ru

def _strip_upgrade_marker(text: str) -> tuple[str, bool]:
    if not isinstance(text, str):
        return str(text), False
    if _UPGRADE_MARKER not in text:
        return text, False
    t = text.replace(_UPGRADE_MARKER, "")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t, True

def _upgrade_to_pro_inline_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Upgrade to Pro", callback_data="open_premium")
    kb.adjust(1)
    return kb.as_markup()

def _assistant_tools_kb(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=_tr(lang, "🌐 Искать в Web", "🌐 Шукати в Web", "🌐 Search Web"), callback_data="assistant:web"),
        InlineKeyboardButton(text=_tr(lang, "🎬 Найти по кадру", "🎬 Знайти за кадром", "🎬 Find by frame"), callback_data="assistant:media"),
        width=2,
    )
    kb.row(
        InlineKeyboardButton(text=_tr(lang, "💬 Спросить ИИ", "💬 Запитати ШІ", "💬 Ask AI"), callback_data="assistant:ask"),
        InlineKeyboardButton(text=_tr(lang, "📚 Искать в базе", "📚 Шукати в базі", "📚 Search in KB"), callback_data="assistant:kb"),
        width=2,
    )
    kb.row(
        InlineKeyboardButton(text=_tr(lang, "⛔️ Стоп", "⛔️ Стоп", "⛔️ Stop"), callback_data="assistant:stop"),
        width=1,
    )
    return kb.as_markup()

async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    res = await session.execute(select(User).where(User.tg_id == tg_id))
    return res.scalar_one_or_none()

def _detect_lang(user: Optional[User], obj: Message | CallbackQuery | None = None) -> str:
    tg_lang = None
    if isinstance(obj, Message) and obj.from_user:
        tg_lang = obj.from_user.language_code
    elif isinstance(obj, CallbackQuery) and obj.from_user:
        tg_lang = obj.from_user.language_code

    return _normalize_lang(
        (getattr(user, "locale", None) if user else None)
        or (getattr(user, "lang", None) if user else None)
        or tg_lang
        or "ru"
    )

@router.callback_query(F.data == "assistant:stop")
async def assistant_stop_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await cb.answer()
    except Exception:
        pass

    if not cb.from_user:
        return

    user = await _get_user(session, cb.from_user.id)
    lang = _detect_lang(user, cb)
    is_admin = is_admin_tg(cb.from_user.id)

    await state.clear()

    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return

    msg = _tr(lang, "Ок, режим помощника выключен.", "Ок, режим помічника вимкнено.", "Ok, assistant mode off.")
    await m.answer(msg, reply_markup=get_main_kb(lang, is_premium=_has_premium(user), is_admin=is_admin))

@router.callback_query(F.data == "assistant:web")
async def assistant_web_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await cb.answer()
    except Exception:
        pass

    try:
        await state.update_data(_assistant_mode="web")
    except Exception:
        pass

    user = await _get_user(session, cb.from_user.id)
    lang = _detect_lang(user, cb)

    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return

    msg = _tr(
        lang,
        "🌐 Web-режим. Пришли ссылку (https://...) или напиши `web: <запрос>`.",
        "🌐 Web-режим. Надішли посилання (https://...) або напиши `web: <запит>`.",
        "🌐 Web mode. Send a link (https://...) or type `web: <query>`."
    )
    await m.answer(msg, parse_mode="Markdown", reply_markup=_assistant_tools_kb(lang))

@router.callback_query(F.data == "assistant:media")
async def assistant_media_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await cb.answer()
    except Exception:
        pass
    try:
        await state.update_data(_assistant_mode="media")
    except Exception:
        pass

    user = await _get_user(session, cb.from_user.id)
    lang = _detect_lang(user, cb)

    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return

    msg = _tr(
        lang,
        "🎬 Режим кадра/фото. Пришли скрин/фото или опиши сцену (год/актёр если знаешь).",
        "🎬 Режим кадру/фото. Надішли скрін/фото або опиши сцену (рік/актор якщо знаєш).",
        "🎬 Frame/Photo mode. Send a screenshot/photo or describe the scene (year/actor if known)."
    )
    await m.answer(msg, reply_markup=_assistant_tools_kb(lang))

@router.callback_query(F.data == "assistant:ask")
async def assistant_ask_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await cb.answer()
    except Exception:
        pass
    try:
        await state.update_data(_assistant_mode="ask")
    except Exception:
        pass

    user = await _get_user(session, cb.from_user.id)
    lang = _detect_lang(user, cb)

    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return

    msg = _tr(
        lang,
        "❓ Режим вопроса. Напиши, что нужно решить (1–2 предложения).",
        "❓ Режим питання. Напиши, що треба вирішити (1–2 речення).",
        "❓ Question mode. Write what you want to solve (1-2 sentences)."
    )
    await m.answer(msg, reply_markup=_assistant_tools_kb(lang))

@router.callback_query(F.data == "assistant:kb")
async def assistant_kb_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await cb.answer()
    except Exception:
        pass
    try:
        await state.update_data(_assistant_mode="kb")
    except Exception:
        pass

    user = await _get_user(session, cb.from_user.id)
    lang = _detect_lang(user, cb)

    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return

    msg = _tr(
        lang,
        "📚 База знаний.\n\n• чтобы добавить введи: `kb+: <текст>`\n• чтобы спросить: `kb?: <вопрос>`\n",
        "📚 База знань.\n\n• щоб додати введи: `kb+: <текст>`\n• щоб запитати: `kb?: <питання>`\n",
        "📚 Knowledge base.\n\n• to add enter: `kb+: <text>`\n• to ask: `kb?: <question>`\n"
    )
    await m.answer(msg, reply_markup=_assistant_tools_kb(lang), parse_mode="Markdown")

@router.callback_query(F.data == "media:noop")
async def _assistant_passthrough_menu_callbacks(cb: CallbackQuery, state: FSMContext):
    st = await state.get_state()
    if not st:
        await cb.answer()
        return
    if not st.startswith("AssistantFSM"):
        return

    data = (cb.data or "").strip()

    try:
        if is_root_assistant_btn(data):
            return
    except Exception:
        pass

    if data.startswith(("assistant_", "assistant:", "assistant_pick:", "media:")):
        return

    await state.clear()
    raise SkipHandler

@router.callback_query(F.data.startswith("media:"))
async def _media_callback_fallback(cb: CallbackQuery, state: FSMContext) -> None:
    data = (cb.data or "").strip()
    known = {"media:noop", "media:pick", "media:nav:next", "media:refine"}
    if data in known:
        raise SkipHandler

    try:
        await cb.answer("Кнопка устарела. Нажми 🔁 Другие варианты или отправь запрос заново.", show_alert=False)
    except Exception:
        try:
            await cb.answer()
        except Exception:
            pass

_POSTER_RE = re.compile(r"(?m)^\s*🖼\s+(https?://\S+)\s*$")
_MEDIA_KNOBS_LINE = "\nКнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить"
_MEDIA_KNOBS_LINE2 = (
    "\n\n👉 Нажми кнопку: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить.\nЕсли кнопок нет — ответь цифрой."
)

def _strip_media_knobs(text: str) -> str:
    if not isinstance(text, str):
        return str(text)
    t = text
    t = t.replace(_MEDIA_KNOBS_LINE, "")
    t = t.replace(_MEDIA_KNOBS_LINE2, "")
    return t.strip()

def _needs_media_kb(text: str) -> bool:
    if not isinstance(text, str):
        return False
    t = text
    return (
        "Кнопки:" in t
        or "Нажми кнопку" in t
        or ("✅ Это оно" in t and "🔁" in t and "🧩" in t)
        or "Если кнопок нет" in t
    )

def _extract_poster_url(text: str) -> tuple[Optional[str], str]:
    if not text:
        return None, text
    m = _POSTER_RE.search(text)
    if not m:
        return None, text
    url = (m.group(1) or "").strip()
    cleaned = _POSTER_RE.sub("", text).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return (url or None), cleaned

def _media_inline_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text={"ru": "✅ Это оно", "uk": "✅ Це воно", "en": "✅ This is it"}.get(lang, "✅ Это оно"), callback_data="media:pick")
    kb.button(text={"ru": "🔁 Другие варианты", "uk": "🔁 Інші варіанти", "en": "🔁 Other options"}.get(lang, "🔁 Другие варианты"), callback_data="media:nav:next")
    kb.button(text={"ru": "🧩 Уточнить", "uk": "🧩 Уточнити", "en": "🧩 Refine"}.get(lang, "🧩 Уточнить"), callback_data="media:refine")
    kb.adjust(2, 1)
    return kb.as_markup()

def _open_premium_inline_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text={"ru": "💎 Открыть Premium", "uk": "💎 Відкрити Premium", "en": "💎 Open Premium"}.get(lang, "💎 Открыть Premium"), callback_data="open_premium")
    kb.adjust(1)
    return kb.as_markup()

class AssistantFSM(StatesGroup):
    waiting_question = State()

async def _typing_loop(chat_id: int, *, interval: float = 4.0) -> None:
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return

def _has_premium(user: Optional[User]) -> bool:
    if not user:
        return False

    now = datetime.now(timezone.utc)

    pu = getattr(user, "premium_until", None)
    if pu is not None:
        try:
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)
            return pu > now
        except Exception:
            return False

    if bool(getattr(user, "is_premium", False)):
        return True

    return bool(getattr(user, "has_premium", False))

def _looks_like_media_text(text: str) -> bool:
    t = (text or "").lower()
    keys = (
        "фильм", "сериал", "кино", "мульт", "мультик", "кадр", "откуда кадр", "по кадру",
        "как называется", "что за фильм", "что за сериал", "что за мультик", "название фильма",
        "название сериала", "в главной роли", "главную роль играет", "с актёром", "с актером",
        "про фильм где", "про сериал где", "season", "episode", "movie", "series", "tv",
        "актёр", "актер", "актриса", "режиссер", "режиссёр",
    )
    return any(k in t for k in keys)

def _is_noise_msg(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) <= 2:
        return True
    if " " not in t and len(t) <= 3:
        return True
    return False

def _is_menu_click(text: str) -> bool:
    return any(
        fn(text)
        for fn in (
            is_root_journal_btn, is_root_reminders_btn, is_root_calories_btn, is_root_stats_btn,
            is_root_assistant_btn, is_root_media_btn, is_root_premium_btn, is_root_proactive_btn,
            is_report_bug_btn, is_admin_btn, is_journal_btn, is_journal_today_btn, is_journal_week_btn,
            is_journal_history_btn, is_journal_search_btn, is_journal_range_btn, is_meditation_btn,
            is_music_btn, is_premium_info_btn, is_premium_card_btn, is_premium_stars_btn,
            is_language_btn, is_privacy_btn, is_data_privacy_btn, is_back_btn,
        )
    )

async def _ack_media_search_once(m: Message, state: FSMContext) -> None:
    try:
        data = await state.get_data()
        if data.get("_media_ack_sent"):
            return
        await state.update_data(_media_ack_sent=True)
    except Exception:
        pass

    try:
        await m.answer("Окей, щас гляну и найду. ⏳")
    except Exception:
        pass

async def _reset_media_ack(state: FSMContext) -> None:
    try:
        await state.update_data(_media_ack_sent=False)
    except Exception:
        pass

# =============== ENTRY ===============

@router.message(AssistantFSM.waiting_question, F.text)
async def _assistant_text_in_waiting_question(m: Message, state: FSMContext, session: AsyncSession):
    text = (m.text or "").strip()
    if not text:
        return

    if text.casefold() in ("стоп", "stop", "/cancel"):
        raise SkipHandler

    if _is_menu_click(text):
        await state.clear()
        raise SkipHandler

    return await assistant_dialog(m, state, session)

@router.message(F.text.func(is_root_assistant_btn))
async def assistant_entry(m: Message, state: FSMContext, session: AsyncSession) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)
    is_admin = is_admin_tg(m.from_user.id)

    if not _has_premium(user):
        await state.clear()
        msg = _tr(
            lang,
            "🤖 Помощник — это твой **умный режим** в дневнике.\n\nЧто он делает:\n• 🧠 раскладывает мысли по полочкам\n• 🎯 помогает найти фильм, идею, решение\n• 📚 анализирует документы и пополняет базу знаний\n• 🌊 снижает шум в голове и многое другое\n\n💎 Доступен в Premium. Нажми кнопку ниже 👇",
            "🤖 Помічник — це твій **розумний режим** у щоденнику.\n\nЩо він робить:\n• 🧠 розкладає думки по поличках\n• 🎯 допомагає знайти фільм, ідею, рішення\n• 📚 аналізує документи та поповнює базу знань\n• 🌊 знижує шум у голові та багато іншого\n\n💎 Доступний у Premium. Натисни кнопку нижче 👇",
            "🤖 Assistant is your **smart mode** in the journal.\n\nWhat it does:\n• 🧠 organizes your thoughts\n• 🎯 helps find a movie, idea, or solution\n• 📚 analyzes documents and builds a knowledge base\n• 🌊 reduces mental noise and much more\n\n💎 Available in Premium. Tap the button below 👇"
        )
        await m.answer(msg, reply_markup=_open_premium_inline_kb(lang), parse_mode="Markdown")
        return

    await state.set_state(AssistantFSM.waiting_question)
    msg = _tr(
        lang,
        "🤖 Режим помощника включён.\nМожешь писать текст или отправить фото.\n\nЧтобы выйти — напиши «стоп» или /cancel.",
        "🤖 Режим помічника увімкнено.\nМожеш писати текст або надіслати фото.\n\nЩоб вийти — напиши «стоп» або /cancel.",
        "🤖 Assistant mode is on.\nYou can send text or photos.\n\nTo exit, type 'stop' or /cancel."
    )
    await m.answer(msg, reply_markup=get_main_kb(lang, is_premium=True, is_admin=is_admin))

# =============== EXIT ===============

@router.callback_query(F.data.func(is_root_assistant_btn))
async def assistant_entry_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await cb.answer()
    except Exception:
        pass

    if not cb.from_user:
        return

    m_any = cb.message
    m: Message | None = m_any if isinstance(m_any, Message) else None

    user = await _get_user(session, cb.from_user.id)

    if m is not None:
        lang = _detect_lang(user, m)
    else:
        lang = _normalize_lang(getattr(cb.from_user, "language_code", None) or "ru")

    is_admin = is_admin_tg(cb.from_user.id)

    if m is None:
        return

    if not _has_premium(user):
        await state.clear()
        msg = _tr(
            lang,
            "🤖 Помощник — это твой **умный режим** в дневнике.\n\nЧто он делает:\n• 🧠 раскладывает мысли по полочкам\n• 🎯 помогает найти фильм, идею, решение\n• 📚 анализирует документы и пополняет базу знаний\n• 🌊 снижает шум в голове и многое другое\n\n💎 Доступен в Premium. Нажми кнопку ниже 👇",
            "🤖 Помічник — це твій **розумний режим** у щоденнику.\n\nЩо він робить:\n• 🧠 розкладає думки по поличках\n• 🎯 допомагає знайти фільм, ідею, рішення\n• 📚 аналізує документи та поповнює базу знань\n• 🌊 знижує шум у голові та багато іншого\n\n💎 Доступний у Premium. Натисни кнопку нижче 👇",
            "🤖 Assistant is your **smart mode** in the journal.\n\nWhat it does:\n• 🧠 organizes your thoughts\n• 🎯 helps find a movie, idea, or solution\n• 📚 analyzes documents and builds a knowledge base\n• 🌊 reduces mental noise and much more\n\n💎 Available in Premium. Tap the button below 👇"
        )
        await m.answer(msg, reply_markup=_open_premium_inline_kb(lang), parse_mode="Markdown")
        return

    await state.set_state(AssistantFSM.waiting_question)
    msg = _tr(
        lang,
        "🤖 Режим помощника включён.\nМожешь писать текст или отправить фото.\n\nЧтобы выйти — напиши «стоп» или /cancel.",
        "🤖 Режим помічника увімкнено.\nМожеш писати текст або надіслати фото.\n\nЩоб вийти — напиши «стоп» або /cancel.",
        "🤖 Assistant mode is on.\nYou can send text or photos.\n\nTo exit, type 'stop' or /cancel."
    )
    await m.answer(msg, reply_markup=get_main_kb(lang, is_premium=True, is_admin=is_admin))

@router.message(AssistantFSM.waiting_question, F.text.casefold().in_(("стоп", "stop", "/cancel")))
async def assistant_exit(m: Message, state: FSMContext, session: AsyncSession) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)
    is_admin = is_admin_tg(m.from_user.id)

    await state.clear()
    msg = _tr(lang, "Ок, режим помощника выключен.", "Ок, режим помічника вимкнено.", "Ok, assistant mode off.")
    await m.answer(msg, reply_markup=get_main_kb(lang, is_premium=_has_premium(user), is_admin=is_admin))

@router.message(AssistantFSM.waiting_question, F.text.func(_is_menu_click))
async def assistant_menu_exit(m: Message, state: FSMContext) -> None:
    await state.clear()
    raise SkipHandler()

# =============== PHOTO (PRO) ===============

@router.message(AssistantFSM.waiting_question, F.photo)
async def assistant_photo(m: Message, state: FSMContext, session: AsyncSession) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)
    caption = (m.caption or "").strip()

    try:
        q = caption or "<photo>"
        await state.update_data(_media_last_query=q, _media_last_lang=lang)
    except Exception:
        pass

    if not _has_premium(user):
        await state.clear()
        await m.answer(
            "🤖 Помощник доступен только в Premium.\nОткрой 💎 Премиум в меню.",
            reply_markup=_open_premium_inline_kb(lang),
        )
        return

    from app.services.assistant import _assistant_plan, run_assistant_vision

    plan = _assistant_plan(user)
    if plan not in ["pro", "max", "pro_max"]:
        await m.answer("Photo search is available in PRO plan.")
        return

    photos = m.photo or []
    if not photos:
        await m.answer("Не удалось получить фото. Попробуй отправить ещё раз.")
        return

    ph = photos[-2] if len(photos) >= 2 else photos[-1]

    try:
        await state.update_data(
            _media_last_photo_file_id=getattr(ph, "file_id", None),
            _media_waiting_photo_desc=(not bool(caption)),
        )
    except Exception:
        pass

    if not caption:
        try:
            data = await state.get_data()
            last_text = (data.get("_media_last_query") or "").strip()
            if last_text and last_text != "<photo>":
                caption = last_text
                await state.update_data(_media_waiting_photo_desc=False)
        except Exception:
            pass

    buf = io.BytesIO()
    await bot.download(ph, destination=buf)
    img_bytes = buf.getvalue()

    await _ack_media_search_once(m, state)
    typing_task = asyncio.create_task(_typing_loop(m.chat.id, interval=4.0))
    try:
        reply = await run_assistant_vision(user, img_bytes, caption, lang, session=session)
    finally:
        await _reset_media_ack(state)
        typing_task.cancel()
        try:
            await typing_task
        except Exception:
            pass

    if isinstance(reply, str) and _needs_media_kb(reply):
        clean = _strip_media_knobs(reply)
        poster_url, clean2 = _extract_poster_url(clean)
        if poster_url:
            await m.answer_photo(
                photo=poster_url,
                caption=clean2,
                reply_markup=_media_inline_kb(lang),
                parse_mode=None,
            )
        else:
            await m.answer(clean2, reply_markup=_media_inline_kb(lang), parse_mode=None)
    else:
        await m.answer(str(reply))

@router.message(
    AssistantFSM.waiting_question,
    F.text & ~F.photo & ~F.text.func(_is_menu_click) & ~F.text.startswith("/"),
)
@router.message(StateFilter(None))
async def _assistant_media_fallback_message(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        st = await state.get_state()
        if st and st.startswith("AssistantFSM"):
            raise SkipHandler
    except Exception:
        pass

    if not message.from_user:
        raise SkipHandler

    text = (message.text or message.caption or "").strip()

    try:
        if text and (_is_menu_click(text) or _is_noise_msg(text)):
            raise SkipHandler
    except Exception:
        pass

    has_photo = bool(getattr(message, "photo", None))
    has_doc = bool(getattr(message, "document", None))
    has_img_doc = False
    if has_doc:
        try:
            mime = (message.document.mime_type or "").lower()
            has_img_doc = mime.startswith("image/")
        except Exception:
            has_img_doc = False

    if not (_looks_like_media_text(text) or has_photo or has_img_doc):
        raise SkipHandler

    user = await _get_user(session, message.from_user.id)
    lang = _detect_lang(user, message)

    try:
        effective_text = text
        data = await state.get_data()
        mode = (data.get("_assistant_mode") or "").strip().lower()
        if mode == "web":
            effective_text = f"web: {text}"
        elif mode == "ask":
            effective_text = text
        elif mode == "kb":
            effective_text = text

        reply = await run_assistant(user, effective_text, lang, session=session)
        if isinstance(reply, str):
            clean_q, need_btn = _strip_upgrade_marker(reply)
            if need_btn:
                await message.answer(clean_q, reply_markup=_upgrade_to_pro_inline_kb(lang), parse_mode=None)
                return
    except Exception:
        try:
            await message.answer(
                "Понял. Давай так: пришли 1 кадр (скрин) или опиши сцену 1–2 фактами + год/актёр, если знаешь."
            )
        except Exception:
            pass
        return

    if isinstance(reply, str) and _needs_media_kb(reply):
        clean = _strip_media_knobs(reply)
        poster_url, clean2 = _extract_poster_url(clean)
        if poster_url:
            await message.answer_photo(
                photo=poster_url,
                caption=clean2,
                reply_markup=_media_inline_kb(lang),
                parse_mode=None,
            )
        else:
            await message.answer(clean2, reply_markup=_media_inline_kb(lang), parse_mode=None)
        return

    await message.answer(str(reply))

async def assistant_dialog(m: Message, state: FSMContext, session: AsyncSession) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)

    if not _has_premium(user):
        await state.clear()
        await m.answer(
            "🤖 Помощник доступен только в Premium.\nОткрой 💎 Премиум в меню.",
            reply_markup=_open_premium_inline_kb(lang),
        )
        return

    text = (m.text or "").strip()
    if not text or _is_noise_msg(text):
        return

    try:
        data = await state.get_data()
    except Exception:
        data = {}

    mode = (data.get("_assistant_mode") or "").strip().lower()

    if data.get("_media_waiting_hint"):
        last_q = (data.get("_media_last_query") or "").strip()
        if last_q:
            text = f"{last_q}\n\nУточнение: {text}"
        try:
            await state.update_data(_media_waiting_hint=False)
        except Exception:
            pass

    effective_text = text
    if mode == "web":
        if not effective_text.lower().startswith("web:"):
            effective_text = f"web: {effective_text}"

    try:
        await state.update_data(_media_last_query=text, _media_last_lang=lang)
    except Exception:
        pass

    is_media_like = _looks_like_media_text(text)
    if mode == "web":
        is_media_like = False
    if user:
        now_utc = datetime.now(timezone.utc)
        mode = getattr(user, "assistant_mode", None)
        until = getattr(user, "assistant_mode_until", None)
        if mode == "media" and until and until > now_utc:
            is_media_like = True

    typing_task = None
    if is_media_like:
        await _ack_media_search_once(m, state)
        typing_task = asyncio.create_task(_typing_loop(m.chat.id, interval=4.0))

    try:
        reply = await run_assistant(user, effective_text, lang, session=session)
        if isinstance(reply, str):
            clean_q, need_btn = _strip_upgrade_marker(reply)
            if need_btn:
                await m.answer(clean_q, reply_markup=_upgrade_to_pro_inline_kb(lang), parse_mode=None)
                return
    finally:
        await _reset_media_ack(state)
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except Exception:
                pass

    try:
        now_utc = datetime.now(timezone.utc)
        mode = getattr(user, "assistant_mode", None) if user else None
        until = getattr(user, "assistant_mode_until", None) if user else None
        sticky_media = bool(mode == "media" and until and until > now_utc)
    except Exception:
        sticky_media = False

    if sticky_media and isinstance(reply, str):
        try:
            await state.update_data(_media_last_query=text, _media_last_lang=lang)
        except Exception:
            pass
        poster_url, clean2 = _extract_poster_url(reply)
        if poster_url:
            await m.answer_photo(
                poster_url,
                caption=clean2,
                reply_markup=_media_inline_kb(lang),
                parse_mode=None,
            )
        else:
            await m.answer(reply, reply_markup=_media_inline_kb(lang), parse_mode=None)
        return

    if isinstance(reply, str) and "Кнопки:" in reply:
        clean = reply.replace(_MEDIA_KNOBS_LINE, "")
        poster_url, clean2 = _extract_poster_url(clean)
        if poster_url:
            await m.answer_photo(
                poster_url,
                caption=clean2,
                reply_markup=_media_inline_kb(lang),
                parse_mode=None,
            )
        else:
            await m.answer(clean, reply_markup=_media_inline_kb(lang), parse_mode=None)
    else:
        await m.answer(str(reply), reply_markup=_assistant_tools_kb(lang))

@router.callback_query(F.data == "media:pick")
async def media_ok(call: CallbackQuery, state: FSMContext) -> None:
    try:
        if call.message:
            await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.answer("✅ Ок, принято.")

@router.callback_query(F.data == "media:nav:next")
async def media_alts(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        data = await state.get_data()
    except Exception:
        data = {}

    last_q = (data.get("_media_last_query") or "").strip()
    lang = (data.get("_media_last_lang") or "ru").strip()

    if not last_q:
        await call.answer("Нет контекста. Напиши запрос ещё раз 🙏", show_alert=False)
        return

    user = await session.scalar(select(User).where(User.tg_id == call.from_user.id))
    if not user:
        await call.answer("Юзер не найден.", show_alert=False)
        return

    typing_task = asyncio.create_task(_typing_loop(call.message.chat.id, interval=4.0)) if call.message else None
    try:
        reply = await run_assistant(user, f"{last_q}\n\nДругие варианты", lang, session=session)
        if isinstance(reply, str):
            clean_q, need_btn = _strip_upgrade_marker(reply)
            if need_btn and call.message:
                await call.message.answer(clean_q, reply_markup=_upgrade_to_pro_inline_kb(lang), parse_mode=None)
                await call.answer()
                return
    finally:
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except Exception:
                pass

    if not call.message:
        await call.answer()
        return

    if isinstance(reply, str) and _needs_media_kb(reply):
        clean = _strip_media_knobs(reply)
        poster_url, clean2 = _extract_poster_url(clean)
        try:
            await state.update_data(_media_last_query=last_q, _media_last_lang=lang)
        except Exception:
            pass

        if poster_url:
            await call.message.answer_photo(
                poster_url,
                caption=clean2,
                reply_markup=_media_inline_kb(lang),
                parse_mode=None,
            )
        else:
            await call.message.answer(clean, reply_markup=_media_inline_kb(lang), parse_mode=None)
    else:
        await call.message.answer(str(reply))

    await call.answer()

@router.callback_query(F.data == "media:refine")
async def media_hint(call: CallbackQuery, state: FSMContext) -> None:
    try:
        await state.update_data(_media_waiting_hint=True)
        data = await state.get_data()
        lang = data.get("_media_last_lang", "ru")
    except Exception:
        lang = "ru"

    if call.message:
        msg = _tr(
            lang,
            "🧩 Ок, уточни одним сообщением:\n• актёр/актриса?\n• примерный год?\n• страна/жанр?\n• что происходило в сцене?\n",
            "🧩 Ок, уточни одним повідомленням:\n• актор/актриса?\n• приблизний рік?\n• країна/жанр?\n• що відбувалося у сцені?\n",
            "🧩 Ok, clarify in one message:\n• actor/actress?\n• approximate year?\n• country/genre?\n• what happened in the scene?\n"
        )
        await call.message.answer(msg)
    await call.answer()

@router.message(F.photo)
async def assistant_photo_fallback(m: Message, state: FSMContext, session: AsyncSession) -> None:
    st = await state.get_state()
    if st != AssistantFSM.waiting_question.state:
        try:
            user = await session.scalar(select(User).where(User.tg_id == m.from_user.id))
            now_utc = datetime.now(timezone.utc)
            mode = getattr(user, "assistant_mode", None) if user else None
            until = getattr(user, "assistant_mode_until", None) if user else None
            sticky_media = bool(mode == "media" and until and until > now_utc)
        except Exception:
            sticky_media = False
        if not sticky_media:
            raise SkipHandler
    await assistant_photo(m, state, session)

__all__ = ["router"]