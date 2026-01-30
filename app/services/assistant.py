from __future__ import annotations

# app/services/assistant.py
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select

from app.models.journal import JournalEntry
from app.models.user import User
from app.services.intent_router import Intent, detect_intent
from app.services.media.formatting import (
    MEDIA_NOT_FOUND_REPLY_RU,
    MEDIA_VIDEO_STUB_REPLY_RU,
    _format_media_pick,
    _format_media_ranked,
    build_media_context,
)
from app.services.media.lens import (
    _lens_bad_candidate,
    _pick_best_lens_candidates,
)

# --- Optional OpenAI import (server may not have it) ---
# --- Anti-hallucination prefix (local-only; do not import) ---
# --- media helpers split (auto) ---
from app.services.media.logging import _d
from app.services.media.pipeline_tmdb import _tmdb_best_effort
from app.services.media.query import (
    _clean_media_search_query,
    _clean_tmdb_query,
    _extract_media_kind_marker,
    _good_tmdb_cand,
    _is_asking_for_title,
    _is_bad_media_query,
    _is_generic_media_caption,
    _looks_like_freeform_media_query,
    _normalize_tmdb_query,
    _parse_media_hints,
    _tmdb_sanitize_query,
)
from app.services.media.safety import (
    _scrub_media_items,
)
from app.services.media.session import (
    _MEDIA_SESSIONS,
    _looks_like_choice,
    _looks_like_year_or_hint,
    _media_get,
    _media_set,
    _media_uid,
)
from app.services.media.vision_parse import (
    _build_tmdb_queries_from_media_json,
    _extract_media_json_from_model_text,
    _extract_search_query_from_text,
    _extract_title_like_from_model_text,
)

ANTI_HALLUCINATION_PREFIX: str = ""

try:
    from openai import AsyncOpenAI
except ModuleNotFoundError:
    AsyncOpenAI = None  # type: ignore

# --- Models (imported at top) ---

# --- Project-level constants (fallbacks) ---
# Used by _is_generic_media_caption
# _GENERIC_MEDIA_CAPTIONS moved to app/services/media/query.py
# --- restored media helpers (from assistant.py.bak2) ---




# --- restored helpers (from assistant.py.bak2) ---


# --- safety: scrub explicit overviews (TMDb sometimes returns NSFW text even with include_adult=false) ---









# --- Services imports (try real, otherwise safe stubs) ---
try:
    from app.services.media_search import tmdb_search_multi  # expected existing
except Exception:  # pragma: no cover

    async def tmdb_search_multi(*args: Any, **kwargs: Any) -> list[dict]:
        return []


try:
    from app.services.media_web_pipeline import (
        web_to_tmdb_candidates,  # expected existing
    )
except Exception:  # pragma: no cover

    async def web_to_tmdb_candidates(
        *args: Any, **kwargs: Any
    ) -> tuple[list[str], str]:
        return ([], "web_stub")


try:
    from app.services.media_web_pipeline import (
        image_bytes_to_tmdb_candidates,
    )  # expected existing
except Exception:  # pragma: no cover

    async def image_bytes_to_tmdb_candidates(
        *args: Any, **kwargs: Any
    ) -> tuple[list[str], str]:
        return ([], "lens_stub")


try:
    from app.services.media_id import trace_moe_identify  # expected existing
except Exception:  # pragma: no cover

    async def trace_moe_identify(*args: Any, **kwargs: Any) -> Optional[dict]:
        return None


try:
    from app.services.llm_usage import log_llm_usage  # expected existing
except Exception:  # pragma: no cover

    async def log_llm_usage(*args: Any, **kwargs: Any) -> None:
        return None


# --- Optional project prompts (safe fallback for workers/tests) ---

# --- TMDB query sanitizer: TMDB hates long "scene description" queries ---






























def _media_confident(item: dict) -> bool:
    """Conservative confidence heuristic for Vision results."""
    try:
        pop = float(item.get("popularity") or 0)
        va = float(item.get("vote_average") or 0)
    except Exception:
        return False
    return (pop >= 25 and va >= 6.8) or (pop >= 60) or (va >= 7.6)

















# --- BAD OCR / GENERIC QUERY FILTER FOR MEDIA SEARCH ---


# --- media query cleaning: turn human phrasing into search-friendly query ---







# --- media session cache (in-memory, no DB migrations) ---























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


async def build_context(
    session: Any, user: Optional[User], lang: str, plan: str
) -> str:
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

    if plan == "basic":
        return (
            base
            + style
            + (
                "Режим BASIC:\n"
                "- 2–6 предложений.\n"
                "- Без планов и стратегий без запроса.\n"
                "- Журнал не использовать как память.\n"
            )
        )

    return (
        base
        + style
        + (
            "Режим PRO:\n"
            "- Можно использовать последние записи журнала как контекст.\n"
            "- Можно предлагать чеклисты и структуру.\n"
            "- Можно задать до 2 уточняющих вопросов.\n"
            "- Стиль: умный близкий помощник.\n"
        )
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
        return {
            "uk": "❌ Не задано OPENAI_API_KEY. Додай ключ у .env / змінні середовища.",
            "en": "❌ OPENAI_API_KEY is missing. Add it to env/.env.",
            "ru": "❌ Не задан OPENAI_API_KEY. Добавь ключ в .env / переменные окружения.",
        }.get(lang, "❌ OPENAI_API_KEY missing.")

    client = AsyncOpenAI(api_key=api_key)
    model = _pick_model()
    plan = _assistant_plan(user)

    kind_marker = _extract_media_kind_marker(text)
    if kind_marker:
        return MEDIA_VIDEO_STUB_REPLY_RU

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

    # --- INTENT gate (prevents media context from leaking into other topics) ---
    intent_res = detect_intent((text or '').strip() if text else None, has_media=bool(has_media))
    intent = getattr(intent_res, 'intent', None) or intent_res
    is_intent_media = intent in (Intent.MEDIA_IMAGE, Intent.MEDIA_TEXT)

    # If user message is NOT media-related, we must drop sticky media (DB + memory)
    if not is_intent_media:
        if uid:
            try:
                _MEDIA_SESSIONS.pop(uid, None)
            except Exception:
                pass
        if user is not None:
            try:
                mode = getattr(user, 'assistant_mode', None)
                if mode == 'media':
                    setattr(user, 'assistant_mode', None)
                    setattr(user, 'assistant_mode_until', now - timedelta(seconds=1))
                    if session:
                        await session.commit()
            except Exception:
                pass    # IMPORTANT: media mode should trigger ONLY for media intents (or real media message)
    # st/sticky are allowed to keep follow-ups ONLY when current intent is media.
    is_media = bool(has_media) or bool(is_intent_media) or (sticky_media_db and bool(is_intent_media)) or (bool(st) and bool(is_intent_media))


    if is_media:
        _d(
            "media.enter",
            is_media=is_media,
            sticky_media_db=sticky_media_db,
            has_st=bool(st),
            uid=uid,
        )  # DBG_MEDIA_RUN_ASSISTANT_V1
        raw_text = (text or "").strip()

        # 1) User picked an option number: "1", "2", ...
        if st and _looks_like_choice(raw_text):
            idx = int(raw_text) - 1
            opts = st.get("items") or []
            if 0 <= idx < len(opts):
                picked = opts[idx]
                return (
                    _format_media_pick(picked)
                    + "\n\nХочешь — напиши другое название/описание, я поищу ещё."
                )

        # 1.5) "Как называется/какое название" — это не новый поиск, показываем варианты
        if st and _is_asking_for_title(raw_text):
            opts = st.get("items") or []
            if not opts:
                return MEDIA_NOT_FOUND_REPLY_RU
            return build_media_context(opts) + "\n\nВыбери номер варианта."
        # 2) Build query (new query vs follow-up hint)# 2) Merge уточнение with previous query
        # 2) Build query (new query vs follow-up hint)
        raw = raw_text
        prev_q = ((st.get("query") if st else "") or "").strip()

        # не даём "ядовитым" фразам портить поисковую строку
        if st and re.search(
            r"(?i)\b(не\s*то|не\s*подходит|ничего\s*не|такого\s*фильма|не\s*существует)\b",
            raw,
        ):
            return MEDIA_NOT_FOUND_REPLY_RU

        # короткое уточнение (год/актёр/страна/язык/серия/эпизод) — добавляем к прошлому запросу
        raw = _normalize_tmdb_query(raw)
        if st and prev_q and _looks_like_year_or_hint(raw) and len(raw) <= 60:
            query = _tmdb_sanitize_query(_normalize_tmdb_query(f"{prev_q} {raw}"))
        else:
            query = _tmdb_sanitize_query(_clean_media_search_query(raw))
        _d("media.built_query", prev_q=prev_q, raw=raw, query=query)

        # 3) Too generic → ask 1 detail
        if len(query) < 6 and ("фильм" in query.lower() or "что за" in query.lower()):
            # keep media mode alive for follow-ups even without DB session
            if user is not None:
                setattr(user, "assistant_mode", "media")
                setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
                if session:
                    await session.commit()
            return MEDIA_NOT_FOUND_REPLY_RU

        # 4) Best-effort TMDb search (ru first, fallback en, year filter, dedupe, sort)
        cleaned = _normalize_tmdb_query(query)
        query = _tmdb_sanitize_query(_normalize_tmdb_query(cleaned or query))

        try:
            items = []

            # 🔹 First try direct search by model/caption query
            items = await _tmdb_best_effort(query, limit=5)
            items = _scrub_media_items(items)
            _d(
                "media.tmdb.primary",
                q=query,
                n=len(items or []),
                top=((items or [{}])[0].get("title") or (items or [{}])[0].get("name"))
                if items
                else None,
            )

            # 🔹 If nothing found — use parsed hints
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
                        items = await tmdb_discover_with_people(
                            pid,
                            year=hints.get("year"),
                            kind=hints.get("kind"),
                        )
                        if items:
                            break

        except Exception:
            items = []

        # If user sent a long free-form scene description, TMDb guesses are often noisy.
        # In that case, force WEB pipeline to extract the real title.
        try:
            if items and raw and _looks_like_freeform_media_query(raw):
                items = []
        except Exception:
            pass

        # --- WEB fallback (cheap -> expensive) ---
        # порядок:
        # 1) wiki/brave (без SerpAPI)
        # 2) SerpAPI только если есть ключ
        if not items and query:
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
                # --- media refinement guard ---
                # If user sends non-digit while media session is active, treat it as query refinement.
                if is_intent_media and (st or sticky_media_db) and text:
                    t = text.strip()
                    if t and (not re.fullmatch(r"\d+", t)) and (not t.startswith("/")):
                        query = t
                        items = []
                # --- end guard ---
                cands, tag = await web_to_tmdb_candidates(query, use_serpapi=False)
                _d(
                    "media.web.cands",
                    use_serpapi=False,
                    tag=tag,
                    n=len(cands or []),
                    sample=(cands or [])[:5],
                )
                items = await _try_cands(cands)
            except Exception:
                items = []

            # SerpAPI — только если всё ещё пусто и реально есть ключ
            if (not items) and (
                os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY")
            ):
                try:
                    cands, tag = await web_to_tmdb_candidates(query, use_serpapi=True)
                    _d(
                        "media.web.cands_serp",
                        use_serpapi=True,
                        tag=tag,
                        n=len(cands or []),
                        sample=(cands or [])[:5],
                    )
                    items = await _try_cands(cands)
                except Exception:
                    pass

        # keep sticky media mode (DB if possible)
        if user is not None:
            setattr(user, "assistant_mode", "media")
            setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
            if session:
                await session.commit()

        if not items:
            # keep last query in memory so next hint still treated as media
            if uid:
                _media_set(uid, query, [])
            return MEDIA_NOT_FOUND_REPLY_RU

        items = _scrub_media_items(items)
        if uid:
            _media_set(uid, query, items)
        return _format_media_ranked(query, items, year_hint=_parse_media_hints(query).get('year'), lang=lang, source='tmdb')

    # ---- Normal assistant (non-media) ----
    ctx = await build_context(session, user, lang, plan)

    prev_id = getattr(user, "assistant_prev_response_id", None) if user else None
    if user:
        last_used = getattr(user, "assistant_last_used_at", None)
        if last_used and (datetime.now(timezone.utc) - last_used) > timedelta(hours=24):
            prev_id = None

    prompt = f"Context:\n{ctx}\n\nUser message:\n" + (text or "") + "\n"

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
    }.get(
        lang,
        "Identify the image and, if it's a movie/series/cartoon frame, try to identify the source.",
    )

    hard_keywords = (
        "текст",
        "что написано",
        "прочитай",
        "скрин",
        "скриншот",
        "ошибка",
        "error",
        "traceback",
        "лог",
        "qr",
        "кьюар",
        "инструкция",
        "меню",
        "чек",
        "рецепт",
        "состав",
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
            "Если это кадр из фильма/сериала/мульта/аниме — помоги найти источник.\n"
            "Если не уверен — так и скажи. Не выдумывай детали.\n\n"
            "ВАЖНО: игнорируй хэштеги (#...), никнеймы (@...), эмодзи, UI-кнопки (Subscribe/Like/Share),\n"
            "названия каналов, музыку/название трека, лайки/просмотры и декоративный текст.\n\n"
            "Сначала верни СТРОГО JSON (без пояснений):\n"
            '{"actors":["..."],"title_hints":["..."],"keywords":["..."]}\n'
            "- actors: имена актёров/актрис (желательно латиницей), минимум 2 если распознаны\n"
            "- title_hints: возможное название/видимый тайтл (если есть)\n"
            "- keywords: 2–5 коротких слов про сцену/жанр (EN или RU)\n\n"
            "ПОТОМ (на новой строке) добавь:\n"
            "SEARCH_QUERY: <короткий запрос, максимум 6–8 слов>\n"
        )
    )

    try:
        resp = await client.responses.create(
            model=model,
            instructions=instr,
            input=cast(Any, [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt_text},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ]),
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

    # --- Lens (bytes -> Spaces -> Google Lens -> candidates -> TMDb) ---
    # NOTE: works only if image_bytes is a real JPG/PNG; dummy bytes will produce empty cands.
    try:
        lens_cands, lens_tag = await image_bytes_to_tmdb_candidates(
            image_bytes,
            ext="jpg",
            use_serpapi_lens=True,
            hl=("ru" if (lang or "ru") == "ru" else "en"),
            prefix="frames",
        )
    except Exception:
        lens_cands, lens_tag = [], "lens_fail"
    _d(
        "vision.lens", lens_tag=lens_tag, lens_cands=(lens_cands or [])[:8]
    )  # DBG_VISION_LENS_V2
    best_lens_fallback: list[str] = []

    if lens_cands:
        try:
            items = []
            used_cand = ""

            ordered = _pick_best_lens_candidates(lens_cands, limit=12)
            ordered = (ordered or [])[:5]  # hard cap: 3–5 clean candidates

            best_lens_fallback = ordered[:8]
            _d("vision.lens.pick", ordered=ordered[:10])

            for cand in ordered:
                cand0 = cand

                # hard drop obvious junk BEFORE touching TMDb
                if _lens_bad_candidate(cand0):
                    continue

                cand0 = _normalize_tmdb_query(_clean_tmdb_query(cand0))
                if not cand0 or len(cand0) < 3:
                    continue
                if _is_bad_media_query(cand0):
                    continue

                cand0 = _tmdb_sanitize_query(_normalize_tmdb_query(cand0))
                if not _good_tmdb_cand(cand0):
                    continue
                items = await _tmdb_best_effort(cand0, limit=5)
                if items:
                    used_cand = cand0
                    break

        except Exception:
            items = []
            used_cand = ""

        if items:
            if user is not None:
                setattr(user, "assistant_mode", "media")
                setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
                if session:
                    await session.commit()

            uid = _media_uid(user)
            if uid and used_cand:
                _media_set(uid, used_cand, items)

            return _format_media_ranked(used_cand, items, year_hint=_parse_media_hints(used_cand).get('year'), lang=lang, source='tmdb')

    # Vision → TMDb candidates (robust)

    # Vision → TMDb candidates (robust)
    caption_str = (caption or "").strip()
    _d(
        "vision.model_out",
        caption=caption_str[:120],
        out_text=(out_text or "")[:250],
        is_generic_caption=_is_generic_media_caption(caption_str),
    )  # DBG_VISION_MODEL_OUT_V2
    # Prefer explicit SEARCH_QUERY from model, then title extracted from the explanation.
    search_q = _normalize_tmdb_query(_extract_search_query_from_text(out_text))
    title_from_text = _normalize_tmdb_query(
        _extract_title_like_from_model_text(out_text)
    )
    _d(
        "vision.extract", search_q=search_q, title_from_text=title_from_text
    )  # DBG_VISION_EXTRACT_V2

    # CAND_LIST_JSON_PRIORITY_V1
    try:
        mj = _extract_media_json_from_model_text(out_text)
        json_queries = _build_tmdb_queries_from_media_json(mj)
        _d("vision.json", json_queries=(json_queries or [])[:10])
    except Exception as e:
        _d("vision.json.fail", err=type(e).__name__, msg=str(e)[:200])
        json_queries = []

    # Build candidate list in priority order (JSON -> model text)
    # Build candidate list in priority order:
    # 1) Vision JSON (actors/title_hints/keywords)
    # 2) Model SEARCH_QUERY / title extracted from text
    # 3) Caption (only if not generic)
    # 4) Lens fallback (only after Vision sources)
    cand_list: list[str] = []

    for c in (json_queries or []):
        c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
        if c and _good_tmdb_cand(c) and c not in cand_list:
            cand_list.append(c)

    for c in (search_q, title_from_text):
        c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
        if c and _good_tmdb_cand(c) and c not in cand_list:
            cand_list.append(c)

    # Caption is used ONLY if it is not a generic phrase like "Откуда кадр?"
    if caption_str and (not _is_generic_media_caption(caption_str)):
        c = _tmdb_sanitize_query(_normalize_tmdb_query(caption_str))
        if c and _good_tmdb_cand(c) and c not in cand_list:
            cand_list.append(c)

    # Lens fallback goes LAST (weak source)
    for c in (best_lens_fallback or [])[:8]:
        c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
        if c and _good_tmdb_cand(c) and c not in cand_list:
            cand_list.append(c)

    _d("vision.cand_list", cand_list=cand_list[:15])  # DBG_VISION_CAND_LIST_V3

    if not cand_list:
        return MEDIA_NOT_FOUND_REPLY_RU

    items: list[dict] = []
    used_query = ""

    # Try TMDb for each candidate
    for q in cand_list:
        if not _good_tmdb_cand(q):
            continue
        _d("vision.tmdb.try", q=q)  # DBG_VISION_TMBD_TRY_V2

        try:
            q = _normalize_tmdb_query(q)
            items = await _tmdb_best_effort(q, limit=5)
            items = [i for i in items if not i.get("adult")]
        except Exception:
            items = []
        if items:
            used_query = q
            try:
                top = items[0]
                _d(
                    "vision.tmdb.hit",
                    used_query=used_query,
                    top_title=(top.get("title") or top.get("name")),
                    top_year=top.get("year"),
                    top_type=top.get("media_type"),
                )
            except Exception:
                pass
            break

    # WEB fallback (only if still empty)
    if (not items) and cand_list:
        try:
            q0 = cand_list[0]
            use_serp = bool(os.getenv("SERPAPI_API_KEY") or os.getenv("SERPAPI_KEY"))
            cands, tag = await web_to_tmdb_candidates(q0, use_serpapi=use_serp)

            for c in cands:
                if _is_bad_media_query(c):
                    continue
                c = _tmdb_sanitize_query(_normalize_tmdb_query(c))
                c = _normalize_tmdb_query(c)
                if not _good_tmdb_cand(c):
                    continue
                items = await _tmdb_best_effort(c, limit=5)
                items = [i for i in items if not i.get("adult")]
                if items:
                    used_query = c
                    break
        except Exception:
            items = []

    if items:
        if user is not None:
            setattr(user, "assistant_mode", "media")
            setattr(user, "assistant_mode_until", now + timedelta(minutes=10))
            if session:
                await session.commit()

        uid = _media_uid(user)
        if uid:
            _media_set(uid, used_query or (cand_list[0] if cand_list else ""), items)

        # Default: return title directly if confident (or single result)
        top = items[0]

        return _format_media_ranked(used_query or (cand_list[0] if cand_list else ''), items, year_hint=_parse_media_hints(used_query or (cand_list[0] if cand_list else '')).get('year'), lang=lang, source='tmdb')

    # --- Failsafe: Vision must always return text ---
    final_text = (out_text or "").strip()
    if final_text:
        return final_text
    return MEDIA_NOT_FOUND_REPLY_RU
