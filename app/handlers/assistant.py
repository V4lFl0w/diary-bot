from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import bot
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

# admin check (best-effort)
try:
    from app.handlers.admin import is_admin_tg  # type: ignore
except Exception:  # pragma: no cover
    def is_admin_tg(_: int) -> bool:
        return False


router = Router(name="assistant")


# ===== media poster extraction (optional) =====

_POSTER_RE = re.compile(r"(?m)^\s*🖼\s+(https?://\S+)\s*$")
_MEDIA_KNOBS_LINE = "\nКнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить"

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
        "фильм", "сериал", "кино", "мульт", "мультик",
        "кадр", "откуда кадр", "по кадру",
        "как называется", "что за фильм", "что за сериал", "что за мультик",
        "название фильма", "название сериала",
        "в главной роли", "главную роль играет", "с актёром", "с актером",
        "про фильм где", "про сериал где",
        "season", "episode", "movie", "series", "tv",
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
    return any(fn(text) for fn in (
        # root
        is_root_journal_btn, is_root_reminders_btn, is_root_calories_btn, is_root_stats_btn,
        is_root_assistant_btn, is_root_media_btn, is_root_premium_btn, is_root_settings_btn, is_root_proactive_btn,
        is_report_bug_btn, is_admin_btn,

        # journal submenu
        is_journal_btn,
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

@router.message(F.text.func(is_root_assistant_btn))
async def assistant_entry(m: Message, state: FSMContext, session: AsyncSession) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)
    is_admin = is_admin_tg(m.from_user.id)

    if not _has_premium(user):
        await state.clear()
        await m.answer(
            "🤖 Помощник — это твой **умный режим** в дневнике.\n\n"
            "Что он делает:\n"
            "• 🧠 раскладывает мысли по полочкам\n"
            "• 🎯 помогает найти фильм, идею, решение\n"
            "• 🌊 снижает шум в голове и многое другое\n\n"
            "💎 Доступен в Premium. Нажми **Премиум** в меню ниже.",
            reply_markup=get_main_kb(lang, is_premium=False, is_admin=is_admin),
            parse_mode="Markdown",
        )
        return

    await state.set_state(AssistantFSM.waiting_question)
    await m.answer(
        "🤖 Режим помощника включён.\n"
        "Можешь писать текст или отправить фото.\n\n"
        "Чтобы выйти — напиши «стоп» или /cancel.",
        reply_markup=get_main_kb(lang, is_premium=True, is_admin=is_admin),
    )


# =============== EXIT ===============


@router.message(AssistantFSM.waiting_question, F.text.casefold().in_(("стоп", "stop", "/cancel")))
async def assistant_exit(m: Message, state: FSMContext, session: AsyncSession) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)
    is_admin = is_admin_tg(m.from_user.id)

    await state.clear()
    await m.answer(
        "Ок, режим помощника выключен.",
        reply_markup=get_main_kb(lang, is_premium=_has_premium(user), is_admin=is_admin),
    )


@router.message(AssistantFSM.waiting_question, F.text.func(_is_menu_click))
async def assistant_menu_exit(m: Message, state: FSMContext) -> None:
    await state.clear()


# =============== PHOTO (PRO) ===============

@router.message(AssistantFSM.waiting_question, F.photo)
async def assistant_photo(m: Message, state: FSMContext, session: AsyncSession) -> None:
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    lang = _detect_lang(user, m)
    caption = (m.caption or "").strip()

    # save last media context for buttons
    try:
        q = caption or "<photo>"
        await state.update_data(_media_last_query=q, _media_last_lang=lang)
    except Exception:
        pass

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

    photos = m.photo or []
    if not photos:
        await m.answer("Не удалось получить фото. Попробуй отправить ещё раз.")
        return

    ph = photos[-2] if len(photos) >= 2 else photos[-1]

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

    if isinstance(reply, str) and "Кнопки:" in reply:
        clean = reply.replace(_MEDIA_KNOBS_LINE, "")
        poster_url, clean2 = _extract_poster_url(clean)
        if poster_url:
            await m.answer_photo(poster_url, caption=clean2, reply_markup=_media_inline_kb(), parse_mode=None)
        else:
            await m.answer(clean, reply_markup=_media_inline_kb(), parse_mode=None)
    else:
        await m.answer(str(reply))

@router.message(
    AssistantFSM.waiting_question,
    F.text
    & ~F.photo
    & ~F.text.func(_is_menu_click)
    & ~F.text.startswith("/")
)
@router.message(
    AssistantFSM.waiting_question,
    F.text
    & ~F.photo
    & ~F.text.func(_is_menu_click)
    & ~F.text.startswith("/")
)
async def assistant_dialog(m: Message, state: FSMContext, session: AsyncSession) -> None:
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
    if not text or _is_noise_msg(text):
        return

    # if we are waiting for a clarification, merge it with last query
    try:
        data = await state.get_data()
    except Exception:
        data = {}

    if data.get("_media_waiting_hint"):
        last_q = (data.get("_media_last_query") or "").strip()
        if last_q:
            text = f"{last_q}\n\nУточнение: {text}"
        try:
            await state.update_data(_media_waiting_hint=False)
        except Exception:
            pass

    # save last query for media buttons
    try:
        await state.update_data(_media_last_query=text, _media_last_lang=lang)
    except Exception:
        pass

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

    if isinstance(reply, str) and "Кнопки:" in reply:
        clean = reply.replace(_MEDIA_KNOBS_LINE, "")
        poster_url, clean2 = _extract_poster_url(clean)
        if poster_url:
            await m.answer_photo(poster_url, caption=clean2, reply_markup=_media_inline_kb(), parse_mode=None)
        else:
            await m.answer(clean, reply_markup=_media_inline_kb(), parse_mode=None)
    else:
        await m.answer(str(reply))

@router.callback_query(F.data == "media:ok")
async def media_ok(call: CallbackQuery, state: FSMContext) -> None:
    # user confirmed the result
    try:
        if call.message:
            await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.answer("✅ Ок, принято.")


@router.callback_query(F.data == "media:alts")
async def media_alts(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    # ask assistant for alternative variants based on last query
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

    # typing loop (optional)
    typing_task = asyncio.create_task(_typing_loop(call.message.chat.id, interval=4.0)) if call.message else None
    try:
        prompt = f"{last_q}\n\nДай другие варианты. 3–5 штук. Коротко."
        reply = await run_assistant(user, prompt, lang, session=session)
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

    if isinstance(reply, str) and "Кнопки:" in reply:
        clean = reply.replace(_MEDIA_KNOBS_LINE, "")
        poster_url, clean2 = _extract_poster_url(clean)
        try:
            await state.update_data(_media_last_query=prompt, _media_last_lang=lang)
        except Exception:
            pass

        if poster_url:
            await call.message.answer_photo(poster_url, caption=clean2, reply_markup=_media_inline_kb(), parse_mode=None)
        else:
            await call.message.answer(clean, reply_markup=_media_inline_kb(), parse_mode=None)
    else:
        await call.message.answer(str(reply))

    await call.answer()


@router.callback_query(F.data == "media:hint")
async def media_hint(call: CallbackQuery, state: FSMContext) -> None:
    # ask user for clarification; next text message will be merged with last query
    try:
        await state.update_data(_media_waiting_hint=True)
    except Exception:
        pass

    if call.message:
        await call.message.answer(
            "🧩 Ок, уточни одним сообщением:\n"
            "• актёр/актриса?\n"
            "• примерный год?\n"
            "• страна/жанр?\n"
            "• что происходило в сцене?\n"
        )
    await call.answer()


# --- FALLBACK PHOTO HANDLER (если FSM почему-то не активен) ---
@router.message(F.photo)
async def assistant_photo_fallback(m: Message, state: FSMContext, session: AsyncSession) -> None:
    st = await state.get_state()
    if st != AssistantFSM.waiting_question.state:
        return
    await assistant_photo(m, state, session)
