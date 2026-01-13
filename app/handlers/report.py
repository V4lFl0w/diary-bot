from __future__ import annotations

import contextlib
import os
from typing import Dict, Optional, Set

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ForceReply
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.bug_report import BugReport
from app.keyboards import get_main_kb, is_report_btn
try:
    from app.handlers.admin import is_admin_tg
except Exception:
    def is_admin_tg(_: int) -> bool:
        return False
from app.config import settings

router = Router(name="report")


class ReportFSM(StatesGroup):
    waiting_text = State()


TEXTS: Dict[str, Dict[str, str]] = {
    "ru": {
        "ask": (
            "Опиши, что не работает/что улучшить. Можно приложить скрин/файл.\n"
            "Отправь сообщением ниже 👇"
        ),
        "saved": "Спасибо! Репорт сохранён. Мы посмотрим и ответим.",
        "start_first": "Нажми /start — и повтори репорт.",
        "empty": "Нужно написать пару слов к репорту. Попробуй ещё раз 👇",
        "cancelled": "Ок, отменил.",
    },
    "uk": {
        "ask": (
            "Опишіть, що не працює/що покращити. Можна додати скрін/файл.\n"
            "Надішліть повідомлення нижче 👇"
        ),
        "saved": "Дякуємо! Репорт збережено. Переглянемо і відповімо.",
        "start_first": "Натисніть /start — і повторіть репорт.",
        "empty": "Потрібно написати кілька слів до репорту. Спробуйте ще раз 👇",
        "cancelled": "Ок, скасовано.",
    },
    "en": {
        "ask": (
            "Describe what’s broken / what to improve. You may attach a screenshot/file.\n"
            "Send your message below 👇"
        ),
        "saved": "Thanks! Bug report saved. We’ll review and reply.",
        "start_first": "Please press /start and send the report again.",
        "empty": "Please add a few words to the report. Try again 👇",
        "cancelled": "Ok, cancelled.",
    },
}


def _normalize_lang(code: Optional[str]) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    return "ru"


def _t(lang: str, key: str) -> str:
    loc = _normalize_lang(lang)
    pack = TEXTS.get(loc) or TEXTS["ru"]
    return pack.get(key, TEXTS["ru"].get(key, key))


def _user_lang(user: User | None, fallback: Optional[str]) -> str:
    return _normalize_lang(
        (getattr(user, "locale", None)
         or getattr(user, "lang", None)
         or fallback
         or getattr(settings, "default_locale", None)
         or "ru")
    )


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (
        await session.execute(select(User).where(User.tg_id == tg_id))
    ).scalar_one_or_none()


def _collect_admin_ids() -> Set[int]:
    ids: Set[int] = set()

    # settings.bot_admin_tg_id
    with contextlib.suppress(Exception):
        if getattr(settings, "bot_admin_tg_id", None):
            ids.add(int(settings.bot_admin_tg_id))

    # settings.admin_ids (если используешь)
    raw_settings = str(getattr(settings, "admin_ids", "") or "").strip()
    if raw_settings:
        for part in raw_settings.replace(";", ",").split(","):
            part = part.strip()
            if part and part.lstrip("+-").isdigit():
                with contextlib.suppress(Exception):
                    ids.add(int(part))

    # ENV ADMIN_IDS
    raw_env = str(os.getenv("ADMIN_IDS", "") or "").strip()
    if raw_env:
        for part in raw_env.replace(";", ",").split(","):
            part = part.strip()
            if part and part.lstrip("+-").isdigit():
                with contextlib.suppress(Exception):
                    ids.add(int(part))

    return ids


# --- триггеры на кнопку репорта ---
report_triggers = F.text.func(lambda s: bool(s) and is_report_btn(s))


@router.message(Command("report"))
@router.message(Command("bug"))
@router.message(Command("issue"))
@router.message(report_triggers)
async def ask_report(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)
    is_premium = bool(getattr(user, "is_premium", False)) if user else False
    is_admin = is_admin_tg(m.from_user.id)

    await state.set_state(ReportFSM.waiting_text)
    await m.answer(_t(loc, "ask"), reply_markup=ForceReply(selective=True))


content_any = (
    F.text
    | F.caption
    | F.photo
    | F.document
    | F.video
    | F.animation
    | F.voice
    | F.audio
)


# ✅ ВАЖНО: cancel должен быть выше save_report
@router.message(ReportFSM.waiting_text, Command("cancel"))
async def cancel_report(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)
    is_premium = bool(getattr(user, "is_premium", False)) if user else False
    is_admin = is_admin_tg(m.from_user.id)

    await state.clear()
    await m.answer(_t(loc, "cancelled"), reply_markup=get_main_kb(loc, is_premium=is_premium, is_admin=is_admin))


@router.message(ReportFSM.waiting_text, content_any)
async def save_report(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)
    is_premium = bool(getattr(user, "is_premium", False)) if user else False
    is_admin = is_admin_tg(m.from_user.id)

    if not user:
        await state.clear()
        await m.answer(_t(loc, "start_first"), reply_markup=get_main_kb(loc, is_premium=is_premium, is_admin=is_admin))
        return

    text = (m.text or m.caption or "").strip()
    if not text:
        await m.answer(_t(loc, "empty"), reply_markup=ForceReply(selective=True))
        return

    br = BugReport(user_id=user.id, text=text, status="new")
    session.add(br)

    try:
        await session.commit()
    except Exception:
        await session.rollback()

    # уведомление админам
    admin_ids = _collect_admin_ids()
    if admin_ids:
        uname = f"@{m.from_user.username}" if m.from_user.username else str(m.from_user.id)
        preview = (text[:800] + "…") if len(text) > 800 else text

        for admin_id in admin_ids:
            with contextlib.suppress(Exception):
                await m.bot.send_message(
                    admin_id,
                    f"🐞 Bug report from {uname}\n\n{preview}",
                )
            with contextlib.suppress(Exception):
                await m.bot.copy_message(
                    chat_id=admin_id,
                    from_chat_id=m.chat.id,
                    message_id=m.message_id,
                )

    await state.clear()
    await m.answer(_t(loc, "saved"), reply_markup=get_main_kb(loc, is_premium=is_premium, is_admin=is_admin))


__all__ = ["router"]