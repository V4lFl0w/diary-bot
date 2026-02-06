from __future__ import annotations

import re
import json
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.music_search import search_tracks
from app.utils.aiogram_guards import cb_edit, cb_reply

from app.music.i18n import tr, normalize
from app.music.urls import WEBAPP_MUSIC_URL, get_focus_sleep
from app.music.repo import get_user, save_track, list_tracks, get_track
from app.music.audio import send_audio_safe

try:
    from app.keyboards import is_music_btn
except Exception:

    def is_music_btn(text: str, /) -> bool:  # type: ignore
        return False


router = Router(name="music")


class MusicStates(StatesGroup):
    waiting_search = State()
    waiting_link = State()


SUPPORTED = {"ru", "uk", "en"}
MY_LIST_LIMIT = 10


def _is_https_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("https://")


def _user_lang(user: Any, tg_lang: str | None) -> str:
    raw = None
    if user:
        raw = getattr(user, "locale", None) or getattr(user, "lang", None)
    raw = raw or tg_lang or "ru"
    l = normalize(str(raw))
    return l if l in SUPPORTED else "ru"


def _menu_kb(l: str) -> InlineKeyboardMarkup:
    webapp_btns: list[InlineKeyboardButton] = []
    if _is_https_url(WEBAPP_MUSIC_URL):
        webapp_btns.append(InlineKeyboardButton(text="🎧 Открыть плеер", web_app=WebAppInfo(url=WEBAPP_MUSIC_URL)))

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=tr(l, "focus_btn"), callback_data="music:focus"),
                InlineKeyboardButton(text=tr(l, "sleep_btn"), callback_data="music:sleep"),
            ],
            [
                InlineKeyboardButton(text=tr(l, "my_btn"), callback_data="music:my"),
                InlineKeyboardButton(text=tr(l, "add_btn"), callback_data="music:add"),
            ],
            [
                *webapp_btns,
                InlineKeyboardButton(text=tr(l, "link_btn"), callback_data="music:link"),
                InlineKeyboardButton(text=tr(l, "search_btn"), callback_data="music:search"),
            ],
        ]
    )


def _open_kb(l: str, kind: str) -> InlineKeyboardMarkup:
    focus, sleep = get_focus_sleep()
    url = focus if kind == "focus" else sleep
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(tr(l, "open_focus") if kind == "focus" else tr(l, "open_sleep")),
                    url=url,
                )
            ],
            [InlineKeyboardButton(text=tr(l, "back"), callback_data="music:back")],
        ]
    )


def _numbers_kb(l: str, items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for idx, (iid, _title) in enumerate(items, start=1):
        row.append(InlineKeyboardButton(text=str(idx), callback_data=f"music:play/{iid}"))
        if len(row) == 5:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text=tr(l, "back"), callback_data="music:back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _search_numbers_kb(l: str, n: int) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i in range(1, n + 1):
        row.append(InlineKeyboardButton(text=str(i), callback_data=f"music:s/{i}"))
        if len(row) == 5:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton(text=tr(l, "back"), callback_data="music:back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("music"))
@router.message(F.text.func(is_music_btn))
@router.message(F.text.in_({"🎵 Music", "🎵 Музика", "🎵 Музыка", "music", "музыка", "музика"}))
async def cmd_music(m: Message, session: AsyncSession) -> None:
    user = await get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    if not user:
        await m.answer(tr(l, "need_start"))
        return
    await m.answer(tr(l, "menu"), reply_markup=_menu_kb(l))


@router.callback_query(F.data == "music:search")
async def on_music_search_btn(c: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await c.answer()
    except TelegramBadRequest:
        pass

    user = await get_user(session, c.from_user.id)
    l = _user_lang(user, getattr(c.from_user, "language_code", None))
    if not user:
        await cb_reply(c, tr(l, "need_start"))
        return

    await state.set_state(MusicStates.waiting_search)
    await cb_reply(c, tr(l, "search_hint"))


@router.callback_query(F.data.startswith("music:"))
async def on_music_choice(c: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = c.data or ""
    kind = data.split(":", 1)[1] if ":" in data else ""

    try:
        await c.answer()
    except TelegramBadRequest:
        pass

    user = await get_user(session, c.from_user.id)
    l = _user_lang(user, getattr(c.from_user, "language_code", None))

    if kind in {"back", ""}:
        await cb_edit(c, tr(l, "menu"), reply_markup=_menu_kb(l))
        return

    if kind in {"focus", "sleep"}:
        await cb_edit(c, tr(l, "menu"), reply_markup=_open_kb(l, kind))
        return

    if kind == "link":
        if not user:
            await cb_reply(c, tr(l, "need_start"))
            return
        await state.set_state(MusicStates.waiting_link)
        await cb_reply(c, tr(l, "link_hint"))
        return

    if kind == "add":
        await cb_reply(c, tr(l, "send_audio_hint"))
        return

    if kind == "my":
        if not user:
            await cb_reply(c, tr(l, "need_start"))
            return

        rows = await list_tracks(session, user, limit=MY_LIST_LIMIT)
        if not rows:
            await cb_edit(c, f"{tr(l, 'empty')} {tr(l, 'send_audio_hint')}")
            return

        lines = [f"{i}) {title}" for i, (_id, title) in enumerate(rows, start=1)]
        await cb_edit(
            c,
            tr(l, "your_tracks") + "\n" + "\n".join(lines),
            reply_markup=_numbers_kb(l, rows),
        )
        return

    # pick from search results: music:s/<n>
    if kind.startswith("s/"):
        if not user:
            await cb_reply(c, tr(l, "need_start"))
            return

        sid = kind.split("/", 1)[1]
        try:
            idx = int(sid) - 1
        except Exception:
            return

        st = await state.get_data()
        packed = st.get("music_search_results") or []
        if not isinstance(packed, list) or idx < 0 or idx >= len(packed):
            await cb_reply(c, "⚠️ Результаты поиска устарели. Нажми «Поиск» ещё раз.")
            return

        item = packed[idx] or {}
        title = str(item.get("title") or "Track").strip()
        artist = str(item.get("artist") or "").strip()
        audio = str(item.get("audio_url") or "").strip()
        url = str(item.get("url") or "").strip()

        caption = title + (f" — {artist}" if artist else "")

        if audio:
            chat_id = int(getattr(getattr(c, "from_user", None), "id", 0) or 0)
            if chat_id:
                await send_audio_safe(c.bot, chat_id=chat_id, audio_src=audio, caption=caption)
                try:
                    await save_track(session, user, caption, audio)
                    await cb_reply(c, tr(l, "saved"))
                except ValueError:
                    await cb_reply(c, tr(l, "too_many"))
            return

        if url:
            await cb_reply(c, f"🎧 Не нашёл прямой full-аудио файл, вот ссылка:\n{url}")
        else:
            await cb_reply(c, "⚠️ Нет ни full-аудио, ни ссылки.")
        return

    if kind.startswith("play/"):
        if not user:
            return

        sid = kind.split("/", 1)[1]
        try:
            track_id = int(sid)
        except Exception:
            return

        track = await get_track(session, user, track_id)
        if not track:
            return

        chat_id = int(getattr(getattr(c, "from_user", None), "id", 0) or 0)
        if not chat_id:
            return

        audio_src = (getattr(track, "file_id", None) or "").strip()
        if not audio_src:
            return

        await send_audio_safe(
            c.bot, chat_id=chat_id, audio_src=audio_src, caption=(getattr(track, "title", None) or None)
        )
        return


@router.message(F.audio)
async def on_audio_inbox(m: Message, session: AsyncSession) -> None:
    user = await get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    if not user:
        await m.answer(tr(l, "need_start"))
        return

    title = (
        getattr(m.audio, "title", None)
        or getattr(m.audio, "file_name", None)
        or getattr(m.audio, "performer", None)
        or "Track"
    )
    try:
        await save_track(session, user, title, m.audio.file_id)
    except ValueError:
        await m.answer(tr(l, "too_many"))
        return
    await m.answer(tr(l, "saved"))


@router.message(F.document.mime_type.startswith("audio/"))
async def on_audio_document(m: Message, session: AsyncSession) -> None:
    user = await get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    if not user:
        await m.answer(tr(l, "need_start"))
        return

    title = getattr(m.document, "file_name", None) or "Track"
    try:
        await save_track(session, user, title, m.document.file_id)
    except ValueError:
        await m.answer(tr(l, "too_many"))
        return
    await m.answer(tr(l, "saved"))


@router.message(MusicStates.waiting_link, F.text)
async def on_music_link(m: Message, state: FSMContext, session: AsyncSession) -> None:
    user = await get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    if not user:
        await m.answer(tr(l, "need_start"))
        return

    url = (m.text or "").strip()

    # 1) Если это НЕ прямая ссылка на аудио-файл — не бесим юзера.
    # Переводим в поиск (страницы типа YouTube/sefon/и т.д.)
    is_direct_audio = bool(
        url.startswith("https://") and re.search(r"\.(mp3|ogg|m4a|aac|wav)(\?|$)", url, re.IGNORECASE)
    )
    if not is_direct_audio:
        await state.set_state(MusicStates.waiting_search)
        await m.answer("Это ссылка на страницу, а не на аудио-файл. Напиши: Артист — Трек")
        return

    # 2) Прямая ссылка на файл — сохраняем
    title = url.split("/")[-1].split("?")[0] or "Audio link"
    try:
        await save_track(session, user, title, url)
    except ValueError:
        await m.answer(tr(l, "too_many"))
        return

    await state.set_state(None)
    await m.answer(tr(l, "link_saved"))


@router.message(MusicStates.waiting_search, F.text)
async def on_music_search_query(m: Message, state: FSMContext, session: AsyncSession) -> None:
    user = await get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    if not user:
        await m.answer(tr(l, "need_start"))
        return

    q = (m.text or "").strip()
    if not q:
        await m.answer(tr(l, "search_hint"))
        return

    results = await search_tracks(q, limit=10)
    if not results:
        hint = tr(l, "empty")
        await m.answer(hint)
        return

    packed = [
        {"title": r.title, "artist": r.artist, "url": r.url, "audio_url": r.audio_url, "source": r.source}
        for r in results
    ]
    await state.update_data(music_search_results=packed)
    await state.set_state(None)

    lines: list[str] = []
    for i, r in enumerate(results, start=1):
        line = f"{i}) {r.title}"
        if r.artist:
            line += f" — {r.artist}"
        lines.append(line)

    await m.answer(tr(l, "search_results") + "\n" + "\n".join(lines), reply_markup=_search_numbers_kb(l, len(results)))


@router.message(F.web_app_data)
async def on_music_webapp_data(m: Message, session: AsyncSession) -> None:
    user = await get_user(session, m.from_user.id)
    if not user:
        return

    raw = getattr(getattr(m, "web_app_data", None), "data", None) or ""
    try:
        payload = json.loads(raw)
    except Exception:
        return

    if not isinstance(payload, dict):
        return
    if payload.get("action") != "play":
        return

    raw_id = payload.get("id")
    if raw_id is None:
        return
    try:
        track_id = int(raw_id)
    except (TypeError, ValueError):
        return

    track = await get_track(session, user, track_id)
    if not track:
        return

    audio_src = (getattr(track, "file_id", None) or "").strip()
    if not audio_src:
        return

    await send_audio_safe(
        m.bot, chat_id=m.chat.id, audio_src=audio_src, caption=(getattr(track, "title", None) or None)
    )


__all__ = ["router"]
