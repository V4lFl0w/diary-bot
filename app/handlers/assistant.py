from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.keyboards import (
    get_main_kb,

    # root
    is_root_assistant_btn,
    is_root_journal_btn, is_root_reminders_btn, is_root_calories_btn, is_root_stats_btn,
    is_root_media_btn, is_root_premium_btn, is_root_settings_btn, is_root_proactive_btn,
    is_report_bug_btn, is_admin_btn,

    # journal submenu
    is_journal_today_btn, is_journal_week_btn, is_journal_history_btn,
    is_journal_search_btn, is_journal_range_btn,
    # ⚠️ ВАЖНО: “сам журнал / запись”
    # если у тебя есть новый matcher:
    # is_journal_add_btn,
    # а если нет — используем legacy is_journal_btn
    is_journal_btn,

    # media submenu
    is_meditation_btn, is_music_btn,

    # premium submenu
    is_premium_info_btn, is_premium_card_btn, is_premium_stars_btn,

    # settings submenu
    is_language_btn, is_privacy_btn,
    is_data_privacy_btn,

    # shared
    is_back_btn,
)

from app.models.user import User
from app.services.assistant import run_assistant
from app.bot import bot
import io

# admin check (best-effort)
try:
    from app.handlers.admin import is_admin_tg  # type: ignore
except Exception:  # pragma: no cover
    def is_admin_tg(_: int) -> bool:
        return False


router = Router(name="assistant")


class AssistantFSM(StatesGroup):
    waiting_question = State()


def _normalize_lang(code: Optional[str]) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    return "ru"


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    res = await session.execute(select(User).where(User.tg_id == tg_id))
    return res.scalar_one_or_none()


def _detect_lang(user: Optional[User], m: Message) -> str:
    return _normalize_lang(
        (getattr(user, "locale", None) if user else None)
        or (getattr(user, "lang", None) if user else None)
        or (getattr(getattr(m, "from_user", None), "language_code", None))
        or "ru"
    )


def _has_premium(user: Optional[User]) -> bool:
    if not user:
        return False

    # 1) legacy-flag
    if bool(getattr(user, "is_premium", False)):
        return True

    # 2) premium_until
    pu = getattr(user, "premium_until", None)
    if pu:
        try:
            now = datetime.now(timezone.utc)
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)
            return pu > now
        except Exception:
            return False

    # 3) fallback на случай старых полей
    return bool(getattr(user, "has_premium", False))


def _is_menu_click(text: str) -> bool:
    return any(fn(text) for fn in (
        # root
        is_root_journal_btn, is_root_reminders_btn, is_root_calories_btn, is_root_stats_btn,
        is_root_assistant_btn, is_root_media_btn, is_root_premium_btn, is_root_settings_btn, is_root_proactive_btn, is_root_proactive_btn,
        is_report_bug_btn, is_admin_btn,

        # journal submenu
        is_journal_btn,              # ✅ “Запись / сам журнал”
        is_journal_today_btn, is_journal_week_btn, is_journal_history_btn,
        is_journal_search_btn, is_journal_range_btn,

        # media submenu
        is_meditation_btn, is_music_btn,

        # premium submenu
        is_premium_info_btn, is_premium_card_btn, is_premium_stars_btn,

        # settings submenu
        is_language_btn, is_privacy_btn, is_data_privacy_btn,

        # shared
        is_back_btn,
    ))


# =============== ENTRY ===============

@router.message(F.text.func(is_root_assistant_btn))
async def assistant_entry(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)
    is_admin = is_admin_tg(m.from_user.id)

    # Free -> апсейл (и показываем меню как есть)
    if not _has_premium(user):
        await state.clear()
        await m.answer(
            "🤖 Помощник доступен только в Premium.\n\n"
            "Он помогает:\n"
            "• структурировать мысли\n"
            "• сделать план на завтра/неделю\n"
            "• успокоить шум в голове и перейти к действиям\n\n"
            "Открой 💎 Премиум в меню, чтобы включить.",
            reply_markup=get_main_kb(lang, is_premium=False, is_admin=is_admin),
        )
        return

    await state.set_state(AssistantFSM.waiting_question)
    await m.answer(
        "🤖 Помощник включён.\n\n"
        "Напиши одним сообщением:\n"
        "— что у тебя в голове / что надо решить\n"
        "— и если хочешь: какой результат нужен завтра\n\n"
        "Чтобы выйти — напиши «стоп» или /cancel."
    )


# =============== EXIT ===============

@router.message(AssistantFSM.waiting_question, F.text.casefold().in_(("стоп", "stop", "/cancel")))
async def assistant_exit(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)
    is_admin = is_admin_tg(m.from_user.id)

    await state.clear()
    await m.answer(
        "Ок, режим помощника выключен.",
        reply_markup=get_main_kb(
            lang,
            is_premium=_has_premium(user),
            is_admin=is_admin,
        ),
    )



@router.message(AssistantFSM.waiting_question, F.photo)
async def assistant_photo(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)

    if not _has_premium(user):
        await state.clear()
        await m.answer(
            "Assistant is Premium-only. Open Premium in menu.",
            reply_markup=get_main_kb(lang, is_premium=False, is_admin=is_admin_tg(m.from_user.id)),
        )
        return

    from app.services.assistant import _assistant_plan, run_assistant_vision
    plan = _assistant_plan(user)
    if plan != "pro":
        await m.answer("Photo search is available in PRO plan.")
        return

    # ✅ берём не самый огромный размер (дешевле, почти без потери качества)
    ph = m.photo[-2] if len(m.photo) >= 2 else m.photo[-1]

    buf = io.BytesIO()
    await bot.download(ph, destination=buf)
    img_bytes = buf.getvalue()

    caption = (m.caption or "").strip()
    reply = await run_assistant_vision(user, img_bytes, caption, lang, session=session)
    await m.answer(reply)


# =============== DIALOG (ВАЖНО: НЕ ЖРЁМ МЕНЮ) ===============

@router.message(
    AssistantFSM.waiting_question,
    F.text
    & ~F.text.func(_is_menu_click)  # ✅ меню-клики пропускаем другим хендлерам
    & ~F.text.startswith("/")       # ✅ команды не трогаем (кроме exit-хендлера выше)
)
async def assistant_dialog(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)

    if not _has_premium(user):
        await state.clear()
        await m.answer(
            "🤖 Помощник доступен только в Premium.\nОткрой 💎 Премиум в меню.",
            reply_markup=get_main_kb(lang, is_premium=False, is_admin=is_admin_tg(m.from_user.id)),
        )
        return

    text = (m.text or "").strip()
    if not text:
        return

    reply = await run_assistant(user, text, lang, session=session)
    await m.answer(reply)