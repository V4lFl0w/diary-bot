from __future__ import annotations

import os
from typing import Optional
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession

# кнопка из главного меню (если есть)
try:
    from app.keyboards import is_meditation_btn
except Exception:  # pragma: no cover

    def is_meditation_btn(text: str, /) -> bool:  # type: ignore
        return False


router = Router(name="meditation")


SUPPORTED_LANGS = {"ru", "uk", "en"}

PITCH = {
    "ru": (
        "🧘 <b>Медитация за 1 клик</b>\n"
        "Чтобы мозг перестал шуметь — и ты реально выдохнул.\n\n"
        "✨ В mini-app тебя ждёт:\n"
        "• красивый таймер + мягкий звук в конце\n"
        "• без лишних кнопок и «настроек ради настроек»\n"
        "• удобно: открыл → запустил → сделал паузу\n\n"
        "💡 <i>Сделай одну короткую сессию прямо сейчас — и сравни состояние «до/после».</i>\n\n"
        "👇 Жми кнопку и стартуй."
    ),
    "uk": (
        "🧘 <b>Медитація в 1 клік</b>\n"
        "Щоб голова перестала шуміти — і ти реально видихнув.\n\n"
        "✨ У mini-app на тебе чекає:\n"
        "• гарний таймер + м’який звук в кінці\n"
        "• без зайвих кнопок і «налаштувань заради налаштувань»\n"
        "• зручно: відкрив → запустив → пауза коли треба\n\n"
        "💡 <i>Зроби коротку сесію просто зараз — і відчуй різницю «до/після».</i>\n\n"
        "👇 Тисни кнопку і стартуй."
    ),
    "en": (
        "🧘 <b>Meditation in 1 tap</b>\n"
        "Quiet the noise — and actually exhale.\n\n"
        "✨ Inside the mini-app:\n"
        "• a clean timer + a gentle end sound\n"
        "• no clutter, no pointless settings\n"
        "• simple: open → start → pause anytime\n\n"
        "💡 <i>Do one short session now and compare how you feel before vs after.</i>\n\n"
        "👇 Tap the button to begin."
    ),
}

BTN = {
    "ru": "Открыть медитацию",
    "uk": "Відкрити медитацію",
    "en": "Open meditation",
}


def _normalize_lang(code: Optional[str]) -> str:
    raw = (code or "ru").strip().lower()
    if raw.startswith(("ua", "uk")):
        return "uk"
    if raw.startswith("en"):
        return "en"
    return "ru"


def _lang_from_message(m: Message) -> str:
    tg = getattr(m, "from_user", None)
    code = getattr(tg, "language_code", None) if tg else None
    l = _normalize_lang(code)
    return l if l in SUPPORTED_LANGS else "ru"


def _webapp_url() -> str | None:
    base = (os.getenv("PUBLIC_BASE_URL") or "").strip()
    if not base:
        return None
    base = base[:-1] if base.endswith("/") else base
    # точный путь оставляю как у тебя было
    return f"{base}/static/mini/meditation/index.html"


def _open_kb(l: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN.get(l, BTN["ru"]), web_app=WebAppInfo(url=url))]]
    )


@router.message(Command("meditation"))
@router.message(F.text.func(is_meditation_btn))
async def cmd_meditation(m: Message, session: AsyncSession, state: FSMContext) -> None:
    # В ЧАТЕ НЕТ МЕНЮ. Только pitch + кнопка открыть mini-app.
    try:
        await state.clear()
    except Exception:
        pass

    l = _lang_from_message(m)
    url = _webapp_url()

    if not url:
        await m.answer(PITCH.get(l, PITCH["ru"]), parse_mode="HTML")
        return

    await m.answer(PITCH.get(l, PITCH["ru"]), reply_markup=_open_kb(l, url), parse_mode="HTML")


__all__ = ["router"]


import json

from aiogram import F


def _bell_ids():
    start_id = (os.getenv("MEDIT_BELL_START_FILE_ID") or "").strip()
    end_id = (os.getenv("MEDIT_BELL_END_FILE_ID") or "").strip()
    return start_id, end_id


@router.message(F.web_app_data)
async def meditation_webapp_bells(m: Message) -> None:
    raw = getattr(getattr(m, "web_app_data", None), "data", None)
    if not raw:
        return

    try:
        payload = json.loads(raw)
    except Exception:
        return

    event = (payload.get("event") or payload.get("type") or "").strip()
    start_id, end_id = _bell_ids()

    async def _play(fid: str):
        if not fid:
            return
        try:
            await m.answer_voice(voice=fid)
        except Exception:
            try:
                await m.answer_audio(audio=fid)
            except Exception:
                pass

    if event == "med2_start":
        await _play(start_id)
    elif event == "med2_finish":
        await _play(end_id)
