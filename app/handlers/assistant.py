from __future__ import annotations

import asyncio

from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
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

def _media_inline_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Это оно", callback_data="media:ok")
    kb.button(text="🔁 Другие варианты", callback_data="media:alts")
    kb.button(text="🧩 Уточнить", callback_data="media:hint")
    kb.adjust(2, 1)
    return kb.as_markup()



class AssistantFSM(StatesGroup):
    waiting_question = State()

async def _typing_loop(chat_id: int, *, interval: float = 4.0) -> None:
    """
    Keep Telegram 'typing…' status alive while we do long operations.
    Call as a background task, cancel when done.
    """
    try:
        while True:
            try:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return



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

    now = datetime.now(timezone.utc)

    # 1) Если есть premium_until — он главный
    pu = getattr(user, "premium_until", None)
    if pu is not None:
        try:
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)
            return pu > now
        except Exception:
            return False

    # 2) Если premium_until нет, но is_premium=True — lifetime / ручной премиум
    if bool(getattr(user, "is_premium", False)):
        return True

    # 3) legacy fallback
    return bool(getattr(user, "has_premium", False))



def _looks_like_media_text(text: str) -> bool:
    t = (text or "").lower()
    keys = (
        "фильм", "сериал", "кино", "мульт", "мультик",
        "кадр", "откуда кадр", "по кадру",
        "как называется", "что за фильм", "что за сериал", "что за мультик",
        "season", "episode", "movie", "series", "tv",
        "актёр", "актер", "актриса", "режиссер", "режиссёр",
    )
    return any(k in t for k in keys)

def _is_noise_msg(text: str) -> bool:
    t = (text or "").strip()
    # пусто/очень коротко — почти всегда мусор
    if not t or len(t) <= 2:
        return True
    # одно слово до 3 букв — мусор
    if " " not in t and len(t) <= 3:
        return True
    return False

def _is_menu_click(text: str) -> bool:
    return any(fn(text) for fn in (
        # root
        is_root_journal_btn, is_root_reminders_btn, is_root_calories_btn, is_root_stats_btn,
        is_root_assistant_btn, is_root_media_btn, is_root_premium_btn, is_root_settings_btn, is_root_proactive_btn,
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




@router.message(AssistantFSM.waiting_question, F.text.func(_is_menu_click))
async def assistant_menu_exit(
    m: Message,
    state: FSMContext,
) -> None:
    # Любой клик по меню/кнопкам — выходим из режима ассистента,
    # чтобы FSM не перехватывал дальнейшие сообщения.
    await state.clear()


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

    # ✅ instant feedback + typing loop (refresh ~every 4s)
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

    # media inline buttons
    if isinstance(reply, str) and "Кнопки:" in reply:
        clean = reply.replace("\nКнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить", "")
        await m.answer(clean, reply_markup=_media_inline_kb())
    else:
        await m.answer(reply)


async def _ack_media_search_once(m, state) -> None:
    """
    Prevent duplicate 'Окей...' when photo+text (or multiple handlers) fire.
    Stores a small flag in FSM data; reset it after sending final reply.
    """
    try:
        data = await state.get_data()
        if data.get("_media_ack_sent"):
            return
        await state.update_data(_media_ack_sent=True)
    except Exception:
        # если FSM недоступен — всё равно просто ответим один раз
        pass

    # ✅ одноразовый ack (без рекурсии)
    try:
        await m.answer("Окей, щас гляну и найду. ⏳")
    except Exception:
        pass


async def _reset_media_ack(state) -> None:
    try:
        await state.update_data(_media_ack_sent=False)
    except Exception:
        pass



# =============== DIALOG (ВАЖНО: НЕ ЖРЁМ МЕНЮ) ===============

@router.message(
    AssistantFSM.waiting_question,
    F.text
    & ~F.photo
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

    if _is_noise_msg(text):
        return

    if not text:
        return

    # ✅ Media queries can be slow (TMDb/Web/LLM). Give instant feedback + typing loop.
    is_media_like = _looks_like_media_text(text)
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
        reply = await run_assistant(user, text, lang, session=session)
    finally:

        await _reset_media_ack(state)
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except Exception:
                pass

    # media inline buttons
    if isinstance(reply, str) and "Кнопки:" in reply:
        clean = reply.replace("\nКнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить", "")
        await m.answer(clean, reply_markup=_media_inline_kb())
    else:
        await m.answer(reply)