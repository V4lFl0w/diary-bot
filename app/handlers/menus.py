from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.handlers.assistant import _media_inline_kb
from app.keyboards import (
    get_journal_menu_kb,
    get_main_kb,
    get_media_menu_kb,
    get_premium_menu_kb,
    get_settings_menu_kb,
    is_back_btn,
    is_root_journal_btn,
    is_root_media_btn,
    is_root_premium_btn,
    is_root_proactive_btn,
    is_root_settings_btn,
)
from app.models.user import User
from app.services.analytics_helpers import log_ui
from app.services.assistant import run_assistant

router = Router(name="menus")


async def _get_user(session: AsyncSession, tg_id: int) -> User:
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user is None:
        user = User(tg_id=tg_id)
        session.add(user)
        await session.commit()
    return user


def _user_lang(user: User, tg_lang: Optional[str]) -> str:
    loc = (getattr(user, "locale", None) or getattr(user, "lang", None) or tg_lang or "ru").lower()
    if loc.startswith(("ua", "uk")):
        return "uk"
    if loc.startswith("en"):
        return "en"
    return "ru"


def _is_premium_user(user: User) -> bool:
    if not user:
        return False
    if bool(getattr(user, "has_premium", False) or getattr(user, "is_premium", False)):
        return True
    pu = getattr(user, "premium_until", None)
    if pu:
        try:
            now = datetime.now(timezone.utc)
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)
            return pu > now
        except Exception:
            return False
    return False


def _is_admin_user(user: User, tg_id: int) -> bool:
    if user and bool(getattr(user, "is_admin", False)):
        return True
    try:
        from app.handlers.admin import is_admin

        return bool(is_admin(tg_id, user))
    except Exception:
        return False


async def _log(
    session: AsyncSession,
    user: User,
    tg_lang: Optional[str],
    event: str,
    source: str,
) -> None:
    await log_ui(
        session,
        user=user,
        user_id=(user.id if user else None),
        event=event,
        source=source,
        tg_lang=tg_lang,
    )
    await session.commit()


@router.message(F.text.func(is_root_journal_btn))
async def open_journal_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "open_journal_menu", "menu")
    await m.answer("📓 Журнал", reply_markup=get_journal_menu_kb(lang))


@router.message(F.text.func(is_root_media_btn))
async def open_media_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "open_media_menu", "menu")
    await m.answer("🧘 Медиа", reply_markup=get_media_menu_kb(lang))


@router.message(F.text.func(is_root_settings_btn))
async def open_settings_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "open_settings_menu", "menu")
    await m.answer("⚙️ Настройки", reply_markup=get_settings_menu_kb(lang))


@router.message(F.text.func(is_root_proactive_btn))
async def open_proactive_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "open_proactive_menu", "menu")

    from app.handlers.proactive import show_proactive_screen

    await show_proactive_screen(m, session, lang)


@router.message(F.text.func(is_root_premium_btn))
async def open_premium_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)
    is_premium = _is_premium_user(user)

    await _log(session, user, tg_lang, "premium_click", "menu")

    from aiogram.types import ReplyKeyboardRemove

    await m.answer("💎 Премиум", reply_markup=ReplyKeyboardRemove())
    await m.answer("💎 Премиум", reply_markup=get_premium_menu_kb(lang, is_premium=is_premium))


@router.callback_query(F.data == "menu:home")
async def cb_back_to_main(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()

    user = await _get_user(session, call.from_user.id)
    lang = _user_lang(user, getattr(call.from_user, "language_code", None))

    await call.message.answer(
        "🏠 Главное меню",
        reply_markup=get_main_kb(
            lang,
            is_premium=_is_premium_user(user),
            is_admin=_is_admin_user(user, call.from_user.id),
        ),
    )

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await call.answer()


@router.message(F.text.func(is_back_btn))
async def back_to_main(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "back_to_main", "button")

    await m.answer(
        "🏠 Главное меню",
        reply_markup=get_main_kb(
            lang,
            is_premium=_is_premium_user(user),
            is_admin=_is_admin_user(user, m.from_user.id),
        ),
    )


@router.message(F.text & ~F.text.startswith("/"))
async def media_mode_text_router(message: Message, session: AsyncSession, state: FSMContext):
    # ✅ если в любом FSM (журнал/калории/ассистент/и т.д.) — НЕ перехватываем, пропускаем дальше
    st = None
    try:
        st = await state.get_state()
    except Exception:
        st = None

    if st:
        raise SkipHandler()

    if not getattr(message, "from_user", None):
        raise SkipHandler()

    # Если включен assistant_mode == 'media', то любой текст уходит в run_assistant.
    # Если media mode не активен — пропускаем дальше.
    user = await session.scalar(select(User).where(User.tg_id == message.from_user.id))
    if not user or getattr(user, "assistant_mode", None) != "media":
        raise SkipHandler()

    try:
        now = datetime.now(timezone.utc)
    except Exception:
        now = datetime.utcnow().replace(tzinfo=timezone.utc)

    until = getattr(user, "assistant_mode_until", None)
    if until is not None and until <= now:
        raise SkipHandler()

    lang = getattr(user, "lang", None) or "ru"
    text = (message.text or "").strip()
    if not text:
        raise SkipHandler()

    reply = await run_assistant(user, text, lang, session=session)
    if reply:
        clean = reply.replace("\nКнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить", "")
        await message.answer(clean, reply_markup=_media_inline_kb(), parse_mode=None)
