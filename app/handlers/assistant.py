from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import bot
from app.keyboards import (
    get_main_kb,
    is_admin_btn,
    # shared
    is_back_btn,
    is_data_privacy_btn,
    is_journal_btn,
    is_journal_history_btn,
    is_journal_range_btn,
    is_journal_search_btn,
    # journal submenu
    is_journal_today_btn,
    is_journal_week_btn,
    # settings submenu
    is_language_btn,
    # media submenu
    is_meditation_btn,
    is_music_btn,
    is_premium_card_btn,
    # premium submenu
    is_premium_info_btn,
    is_premium_stars_btn,
    is_privacy_btn,
    is_report_bug_btn,
    # root
    is_root_assistant_btn,
    is_root_calories_btn,
    is_root_journal_btn,
    is_root_media_btn,
    is_root_premium_btn,
    is_root_proactive_btn,
    is_root_reminders_btn,
    is_root_settings_btn,
    is_root_stats_btn,
)
from app.models.user import User
from app.services.assistant import run_assistant

# admin check (best-effort)
try:
    from app.handlers.admin import is_admin_tg  # type: ignore
except Exception:  # pragma: no cover

    def is_admin_tg(tg_id: int, /) -> bool:
        return False


router = Router(name="assistant")



# ===== upgrade marker -> inline button (web quota softback) =====

_UPGRADE_MARKER = "[Upgrade to Pro]"

def _strip_upgrade_marker(text: str) -> tuple[str, bool]:
    if not isinstance(text, str):
        return str(text), False
    if _UPGRADE_MARKER not in text:
        return text, False
    t = text.replace(_UPGRADE_MARKER, "")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t, True

def _upgrade_to_pro_inline_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Upgrade to Pro", callback_data="open_premium")
    kb.adjust(1)
    return kb.as_markup()

def _assistant_tools_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🌐 Web", callback_data="assistant:web"),
        InlineKeyboardButton(text="🎬 Кадр/фото", callback_data="assistant:media"),
        width=2,
    )
    kb.row(
        InlineKeyboardButton(text="❓ Спросить", callback_data="assistant:ask"),
        InlineKeyboardButton(text="📚 База знаний", callback_data="assistant:kb"),
        width=2,
    )
    kb.row(
        InlineKeyboardButton(text="⛔️ Стоп", callback_data="assistant:stop"),
        width=1,
    )
    return kb.as_markup()


@router.callback_query(F.data == "assistant:stop")
async def assistant_stop_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await cb.answer()
    except Exception:
        pass

    if not cb.from_user:
        return

    user = await _get_user(session, cb.from_user.id)
    lang = _normalize_lang(
        (getattr(user, "locale", None) if user else None)
        or (getattr(user, "lang", None) if user else None)
        or getattr(cb.from_user, "language_code", None)
        or "ru"
    )
    is_admin = is_admin_tg(cb.from_user.id)

    await state.clear()

    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return

    await m.answer(
        "Ок, режим помощника выключен.",
        reply_markup=get_main_kb(lang, is_premium=_has_premium(user), is_admin=is_admin),
    )


@router.callback_query(F.data == "assistant:web")
async def assistant_web_cb(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        await cb.answer()
    except Exception:
        pass

    try:
        await state.update_data(_assistant_mode="web")
    except Exception:
        pass

    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return

    await m.answer(
        "🌐 Web-режим. Пришли ссылку (https://...) или напиши `web: <запрос>`.",
        parse_mode="Markdown",
        reply_markup=_assistant_tools_kb(),
    )


@router.callback_query(F.data == "assistant:media")
async def assistant_media_cb(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        await cb.answer()
    except Exception:
        pass
    try:
        await state.update_data(_assistant_mode="media")
    except Exception:
        pass

    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return

    await m.answer(
        "🎬 Режим кадра/фото. Пришли скрин/фото или опиши сцену (год/актёр если знаешь).",
        reply_markup=_assistant_tools_kb(),
    )


@router.callback_query(F.data == "assistant:ask")
async def assistant_ask_cb(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        await cb.answer()
    except Exception:
        pass
    try:
        await state.update_data(_assistant_mode="ask")
    except Exception:
        pass
    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return
    await m.answer("❓ Режим вопроса. Напиши, что нужно решить (1–2 предложения).", reply_markup=_assistant_tools_kb())


@router.callback_query(F.data == "assistant:kb")
async def assistant_kb_cb(cb: CallbackQuery, state: FSMContext) -> None:
    try:
        await cb.answer()
    except Exception:
        pass
    try:
        await state.update_data(_assistant_mode="kb")
    except Exception:
        pass

    m_any = cb.message
    m = m_any if isinstance(m_any, Message) else None
    if m is None:
        return

    await m.answer(
        "📚 База знаний.\n\n• чтобы добавить: `kb+: <текст>`\n• чтобы спросить: `kb?: <вопрос>`\n",
        reply_markup=_assistant_tools_kb(),
        parse_mode="Markdown",
    )


@router.callback_query(F.data == "media:noop")
async def _assistant_passthrough_menu_callbacks(cb: CallbackQuery, state: FSMContext):
    st = await state.get_state()
    if not st:
        await cb.answer()
        return
    # only when we're inside assistant FSM
    if not st.startswith("AssistantFSM"):
        return

    data = (cb.data or "").strip()

    # if it's assistant root button (inline) — let assistant handlers work
    try:
        if is_root_assistant_btn(data):
            return
    except Exception:
        pass

    # allow assistant's own callbacks to be handled by assistant handlers
    if data.startswith(("assistant_", "assistant:", "assistant_pick:", "media:")):
        return

    # everything else (Menu/Journal/Settings/Media/etc) must pass through
    await state.clear()
    raise SkipHandler


@router.callback_query(F.data.startswith("media:"))
async def _media_callback_fallback(cb: CallbackQuery, state: FSMContext) -> None:
    """
    Safety net:
    - lets real media handlers handle known callbacks
    - catches stale/unknown media:* callbacks so updates are not 'unhandled'
    """
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


# ===== media poster extraction (optional) =====

_POSTER_RE = re.compile(r"(?m)^\s*🖼\s+(https?://\S+)\s*$")
_MEDIA_KNOBS_LINE = "\nКнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить"

# service может возвращать не "Кнопки:", а "👉 Нажми кнопку..."
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
    # триггеры, которые точно означают "должны быть кнопки"
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


def _media_inline_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Это оно", callback_data="media:pick")
    kb.button(text="🔁 Другие варианты", callback_data="media:nav:next")
    kb.button(text="🧩 Уточнить", callback_data="media:refine")
    kb.adjust(2, 1)
    return kb.as_markup()


def _open_premium_inline_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Открыть Premium", callback_data="open_premium")
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
        "фильм",
        "сериал",
        "кино",
        "мульт",
        "мультик",
        "кадр",
        "откуда кадр",
        "по кадру",
        "как называется",
        "что за фильм",
        "что за сериал",
        "что за мультик",
        "название фильма",
        "название сериала",
        "в главной роли",
        "главную роль играет",
        "с актёром",
        "с актером",
        "про фильм где",
        "про сериал где",
        "season",
        "episode",
        "movie",
        "series",
        "tv",
        "актёр",
        "актер",
        "актриса",
        "режиссер",
        "режиссёр",
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
            # root
            is_root_journal_btn,
            is_root_reminders_btn,
            is_root_calories_btn,
            is_root_stats_btn,
            is_root_assistant_btn,
            is_root_media_btn,
            is_root_premium_btn,
            is_root_settings_btn,
            is_root_proactive_btn,
            is_report_bug_btn,
            is_admin_btn,
            # journal submenu
            is_journal_btn,
            is_journal_today_btn,
            is_journal_week_btn,
            is_journal_history_btn,
            is_journal_search_btn,
            is_journal_range_btn,
            # media submenu
            is_meditation_btn,
            is_music_btn,
            # premium submenu
            is_premium_info_btn,
            is_premium_card_btn,
            is_premium_stars_btn,
            # settings submenu
            is_language_btn,
            is_privacy_btn,
            is_data_privacy_btn,
            # shared
            is_back_btn,
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

    # 1) дай сработать штатному выходу
    if text.casefold() in ("стоп", "stop", "/cancel"):
        raise SkipHandler

    # 2) если это кнопка меню — выходим из ассистента и отдаём управление меню-роутерам
    if _is_menu_click(text):
        await state.clear()
        raise SkipHandler

    # 3) обычный текст — ассистент
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
        await m.answer(
            "🤖 Помощник — это твой **умный режим** в дневнике.\n\n"
            "Что он делает:\n"
            "• 🧠 раскладывает мысли по полочкам\n"
            "• 🎯 помогает найти фильм, идею, решение\n"
            "• 🌊 снижает шум в голове и многое другое\n\n"
            "💎 Доступен в Premium. Нажми кнопку ниже 👇",
            reply_markup=_open_premium_inline_kb(),
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


@router.callback_query(F.data.func(is_root_assistant_btn))
async def assistant_entry_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Entry to assistant via INLINE кнопки (callback_data)."""
    try:
        await cb.answer()
    except Exception:
        pass

    if not cb.from_user:
        return

    m_any = cb.message
    m: Message | None = m_any if isinstance(m_any, Message) else None

    user = await _get_user(session, cb.from_user.id)

    # Message может быть InaccessibleMessage (pyright ругается). Тогда берём язык из from_user.
    if m is not None:
        lang = _detect_lang(user, m)
    else:
        lang = _normalize_lang(getattr(cb.from_user, "language_code", None) or "ru")

    is_admin = is_admin_tg(cb.from_user.id)

    # если сообщения нет/оно недоступно — отвечать некуда, выходим тихо
    if m is None:
        return

    if not _has_premium(user):
        await state.clear()
        await m.answer(
            "🤖 Помощник — это твой **умный режим** в дневнике."
            "Что он делает:"
            "• 🧠 раскладывает мысли по полочкам"
            "• 🎯 помогает найти фильм, идею, решение"
            "• 🌊 снижает шум в голове и многое другое"
            "💎 Доступен в Premium. Нажми кнопку ниже 👇",
            reply_markup=_open_premium_inline_kb(),
            parse_mode="Markdown",
        )
        return

    await state.set_state(AssistantFSM.waiting_question)
    await m.answer(
        "🤖 Режим помощника включён.Можешь писать текст или отправить фото.Чтобы выйти — напиши «стоп» или /cancel.",
        reply_markup=get_main_kb(lang, is_premium=True, is_admin=is_admin),
    )


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

    raise SkipHandler()


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
            reply_markup=_open_premium_inline_kb(),
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
    # --- remember last photo for "photo -> description" flow
    try:
        await state.update_data(
            _media_last_photo_file_id=getattr(ph, "file_id", None),
            _media_waiting_photo_desc=(not bool(caption)),
        )
    except Exception:
        pass

    # --- text -> photo (no caption) flow: reuse last text as caption
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
        poster_url, clean = _extract_poster_url(clean)
        if poster_url:
            await m.answer_photo(
                photo=poster_url,
                caption=clean,
                reply_markup=_media_inline_kb(),
                parse_mode=None,
            )
        else:
            await m.answer(clean, reply_markup=_media_inline_kb(), parse_mode=None)
    else:
        await m.answer(str(reply))


@router.message(
    AssistantFSM.waiting_question,
    F.text & ~F.photo & ~F.text.func(_is_menu_click) & ~F.text.startswith("/"),
)
@router.message(StateFilter(None))
async def _assistant_media_fallback_message(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """
    Safety net so media / assistant messages are not 'unhandled' when FSM state is empty.
    Does NOT interfere with normal AssistantFSM flow.
    """
    # if we're already inside assistant FSM, let existing handlers work
    try:
        st = await state.get_state()
        if st and st.startswith("AssistantFSM"):
            raise SkipHandler
    except Exception:
        pass

    if not message.from_user:
        raise SkipHandler

    text = (message.text or message.caption or "").strip()

    # ignore obvious menu clicks/noise
    try:
        if text and (_is_menu_click(text) or _is_noise_msg(text)):
            raise SkipHandler
    except Exception:
        pass

    # media-like text OR photo/document-image triggers assistant
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
            # force web pipeline (no TMDB)
            effective_text = f"web: {text}"
        elif mode == "ask":
            effective_text = text
        elif mode == "kb":
            effective_text = text

        reply = await run_assistant(user, effective_text, lang, session=session)
        # --- Web quota softback: show Upgrade button if marker is present ---
        if isinstance(reply, str):
            clean_q, need_btn = _strip_upgrade_marker(reply)
            if need_btn:
                await message.answer(clean_q, reply_markup=_upgrade_to_pro_inline_kb(), parse_mode=None)
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
                reply_markup=_media_inline_kb(),
                parse_mode=None,
            )
        else:
            await message.answer(clean2, reply_markup=_media_inline_kb(), parse_mode=None)
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
            reply_markup=_open_premium_inline_kb(),
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

    mode = (data.get("_assistant_mode") or "").strip().lower()

    if data.get("_media_waiting_hint"):
        last_q = (data.get("_media_last_query") or "").strip()
        if last_q:
            text = f"{last_q}\n\nУточнение: {text}"
        try:
            await state.update_data(_media_waiting_hint=False)
        except Exception:
            pass

    # route by mode (AFTER hint merge)
    effective_text = text
    if mode == "web":
        # force web pipeline (skip TMDB/media)
        if not effective_text.lower().startswith("web:"):
            effective_text = f"web: {effective_text}"

    # save last query for media buttons
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
        # --- Web quota softback: show Upgrade button if marker is present ---
        if isinstance(reply, str):
            clean_q, need_btn = _strip_upgrade_marker(reply)
            if need_btn:
                await m.answer(clean_q, reply_markup=_upgrade_to_pro_inline_kb(), parse_mode=None)
                return
    finally:
        await _reset_media_ack(state)
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except Exception:
                pass

    # --- FORCE INLINE BUTTONS IN STICKY MEDIA MODE (robust) ---
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
                reply_markup=_media_inline_kb(),
                parse_mode=None,
            )
        else:
            await m.answer(reply, reply_markup=_media_inline_kb(), parse_mode=None)
        return
    if isinstance(reply, str) and "Кнопки:" in reply:
        clean = reply.replace(_MEDIA_KNOBS_LINE, "")
        poster_url, clean2 = _extract_poster_url(clean)
        if poster_url:
            await m.answer_photo(
                poster_url,
                caption=clean2,
                reply_markup=_media_inline_kb(),
                parse_mode=None,
            )
        else:
            await m.answer(clean, reply_markup=_media_inline_kb(), parse_mode=None)
    else:
        await m.answer(str(reply), reply_markup=_assistant_tools_kb())


@router.callback_query(F.data == "media:pick")
async def media_ok(call: CallbackQuery, state: FSMContext) -> None:
    # user confirmed the result
    try:
        if call.message:
            await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.answer("✅ Ок, принято.")


@router.callback_query(F.data == "media:nav:next")
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
        reply = await run_assistant(user, f"{last_q}\n\nДругие варианты", lang, session=session)
        # --- Web quota softback: show Upgrade button if marker is present ---
        if isinstance(reply, str):
            clean_q, need_btn = _strip_upgrade_marker(reply)
            if need_btn and call.message:
                await call.message.answer(clean_q, reply_markup=_upgrade_to_pro_inline_kb(), parse_mode=None)
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
                reply_markup=_media_inline_kb(),
                parse_mode=None,
            )
        else:
            await call.message.answer(clean, reply_markup=_media_inline_kb(), parse_mode=None)
    else:
        await call.message.answer(str(reply))

    await call.answer()


@router.callback_query(F.data == "media:refine")
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
        # allow photo handling in sticky media mode even if FSM is not active
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
