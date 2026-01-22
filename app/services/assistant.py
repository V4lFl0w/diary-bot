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
    # default
    plan = "basic"

    if not user:
        return plan

    # 0) tier from user.premium_plan (highest priority)
    v0 = str(getattr(user, 'premium_plan', '') or '').strip().lower()
    if v0 in {'basic', 'pro'}:
        return v0

    # 1) пробуем из assistant_profile_json
    prof = getattr(user, "assistant_profile_json", None)
    if isinstance(prof, str) and prof.strip():
        try:
            prof = json.loads(prof)
        except Exception:
            prof = None
    if isinstance(prof, dict):
        v = str(prof.get("plan") or "").strip().lower()
        if v in {"basic", "pro"}:
            return v

    # 2) fallback: если у тебя есть только is_premium — пусть премиум = pro
    try:
        if bool(getattr(user, "is_premium", False)):
            return "pro"
    except Exception:
        pass

    return plan


def _now_str_user(user: Optional[User]) -> str:
    tz = _user_tz(user)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")


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

    ctx = await build_context(session, user, lang, plan)

    prev_id = getattr(user, "assistant_prev_response_id", None) if user else None

    # если не использовался > 24 часов — начинаем новую ветку
    if user:
        last_used = getattr(user, "assistant_last_used_at", None)
        if last_used and (datetime.now(timezone.utc) - last_used) > timedelta(hours=24):
            prev_id = None

    prompt = (
        f"Context:\n{ctx}\n\n"
        f"User message:\n{text}\n"
    )

    resp = await client.responses.create(
        previous_response_id=prev_id,
        model=model,
        instructions=_instructions(lang, plan),
        input=prompt,                 # <-- важно: строкой
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

    out = getattr(resp, "output_text", None)
    out_text = (out or "").strip()

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

    # fallback
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
        return {"ru": "Фото доступно только в PRO.", "uk": "Фото доступне лише в PRO.", "en": "Photos are PRO-only."}.get(lang, "PRO-only.")

    client = AsyncOpenAI(api_key=api_key)

    prompt_text = (caption or "").strip()
    if not prompt_text:
        prompt_text = {
            "ru": "Определи, что на фото, и дай краткий полезный вывод.",
            "uk": "Визнач, що на фото, і дай короткий корисний висновок.",
            "en": "Identify what’s in the photo and give a short helpful takeaway.",
        }.get(lang, "Identify the image and give a short helpful takeaway.")

    # ✅ авто-усиление модели только для “сложных” задач (скрины/текст/ошибки)
    hard_keywords = (
        "текст", "что написано", "прочитай", "скрин", "скриншот",
        "ошибка", "error", "traceback", "лог", "qr", "кьюар",
        "инструкция", "меню", "чек", "рецепт", "состав"
    )
    is_hard = any(k in prompt_text.lower() for k in hard_keywords)

    model_default = _env("ASSISTANT_VISION_MODEL", _pick_model())
    model_hard = _env("ASSISTANT_VISION_MODEL_HARD", model_default)
    model = model_hard if is_hard else model_default

    # ✅ data-url
    import base64
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"  # для F.photo почти всегда jpeg

    instr = ANTI_HALLUCINATION_PREFIX + _instructions(lang, plan) + "\n" + (
        "Ты видишь изображение. Отвечай по делу. "
        "Если есть неопределенность — скажи об этом. "
        "Не выдумывай детали."
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
        # ✅ понятный фолбэк для юзера вместо падения
        return {
            "ru": f"⚠️ Не смог обработать фото ({type(e).__name__}). Попробуй отправить фото меньшего размера или сжать скрин.",
            "uk": f"⚠️ Не зміг обробити фото ({type(e).__name__}). Спробуй надіслати менше фото або стиснути скрін.",
            "en": f"⚠️ I couldn’t process the photo ({type(e).__name__}). Try sending a smaller image or compressing the screenshot.",
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
    # 🎞️ Anime / cartoon frame detection via trace.moe
    if any(k in out_text.lower() for k in ("аниме", "anime", "мульт", "cartoon")):
        result = await trace_moe_identify(image_bytes)
        if result and result["similarity"] >= 0.9:
            return (
                f"🎬 Это кадр из аниме.\n\n"
                f"Название: {result['title']}\n"
                f"Серия: {result['episode']}\n"
                f"Совпадение: {result['similarity']:.1%}"
            )
        elif result:
            return (
                "🎬 Похоже на аниме, но не уверен.\n\n"
                f"Возможный источник: {result['title']}\n"
                f"Совпадение: {result['similarity']:.1%}"
            )
    if out_text:
        return out_text

    try:
        return str(getattr(resp, "output", "")).strip() or "⚠️ Empty response."
    except Exception:
        return "⚠️ Не смог прочитать ответ vision."
