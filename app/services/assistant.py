# app/services/assistant.py
from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta, time as dtime
from typing import Optional, Any

from zoneinfo import ZoneInfo
from sqlalchemy import select, desc
from openai import AsyncOpenAI

from app.models.user import User
from app.models.journal import JournalEntry


MENU_NOISE = {
    "📊 Статистика", "🧾 Сегодня", "📓 Журнал", "🏠 Главное меню",
    "💎 Премиум", "⚙️ Настройки", "🧘 Медиа",
}


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


async def build_context(session: Any, user: Optional[User], lang: str) -> str:
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

    recent = await _fetch_recent_journal(session, user, limit=30, take=5)
    if recent:
        parts.append("Recent journal entries:")
        for ts, txt in recent:
            parts.append(f"- [{ts}] {txt}")

    return "\n".join(parts)


def _instructions(lang: str) -> str:
    if lang == "uk":
        return (
            "Ти — приватний помічник у щоденнику. Дуже практичний.\n"
            "Формат: 1) Суть 2) План (3 кроки) 3) Один маленький наступний крок.\n"
            "Якщо про завтра — блоки (ранок/день/вечір) + 1 пріоритет.\n"
            "Якщо мало даних — постав 1–2 уточнення.\n"
            "Без моралі і без води.\n"
            "Якщо питають погоду/курси/факти, які ти не можеш перевірити — "
            "дай 2 сценарії (якщо X / якщо Y) і що зробити прямо зараз.\n"
        )
    if lang == "en":
        return (
            "You are a private diary assistant. Very practical.\n"
            "Format: 1) Summary 2) 3-step plan 3) One tiny next action.\n"
            "If asked for tomorrow — morning/afternoon/evening + 1 priority.\n"
            "If missing info — ask 1–2 clarifying questions.\n"
            "No fluff.\n"
            "If asked about weather/exchange rates/facts you can't verify — "
            "give 2 scenarios (if X / if Y) and what to do right now.\n"
        )
    return (
        "Ты — приватный помощник дневника. Максимально практичный.\n"
        "Формат: 1) Суть 2) План (3 шага) 3) Один маленький следующий шаг.\n"
        "Если про завтра — блоки (утро/день/вечер) + 1 приоритет.\n"
        "Если не хватает вводных — задай 1–2 уточняющих вопроса.\n"
        "Без морали и без воды.\n"
        "Если спрашивают погоду/курсы/факты, которые ты не можешь проверить — "
        "дай 2 сценария (если X / если Y) и что сделать прямо сейчас.\n"
    )


async def run_assistant(
    user: Optional[User],
    text: str,
    lang: str,
    *,
    session: Any = None,
) -> str:
    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        return {
            "uk": "❌ Не задано OPENAI_API_KEY. Додай ключ у .env / змінні середовища.",
            "en": "❌ OPENAI_API_KEY is missing. Add it to env/.env.",
            "ru": "❌ Не задан OPENAI_API_KEY. Добавь ключ в .env / переменные окружения.",
        }.get(lang, "❌ OPENAI_API_KEY missing.")

    client = AsyncOpenAI(api_key=api_key)
    model = _pick_model()

    ctx = await build_context(session, user, lang)

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
        instructions=_instructions(lang),
        input=prompt,                 # <-- важно: строкой
        max_output_tokens=450,
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