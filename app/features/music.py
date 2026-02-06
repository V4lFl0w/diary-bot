from __future__ import annotations
from aiogram.types import WebAppInfo


from app.services.music_search import search_tracks
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import os
import re
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.user_track import UserTrack
from app.utils.aiogram_guards import cb_edit, cb_reply

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
PLAYLIST_LIMIT = 50
WEBAPP_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("PUBLIC_URL") or os.getenv("WEBAPP_BASE_URL") or "").rstrip("/")
def _is_http_url(url: str) -> bool:
    u = (url or '').strip().lower()
    return u.startswith('http://') or u.startswith('https://')


def _is_https_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("https://")

WEBAPP_MUSIC_URL = f"{WEBAPP_BASE_URL}/webapp/music/index.html"
MY_LIST_LIMIT = 10

TXT: dict[str, dict[str, str]] = {
    "menu": {
        "ru": "Выбери плейлист:",
        "uk": "Оберіть плейлист:",
        "en": "Choose a playlist:",
    },
    "focus_btn": {"ru": "Фокус", "uk": "Фокус", "en": "Focus"},
    "sleep_btn": {"ru": "Сон", "uk": "Сон", "en": "Sleep"},
    "my_btn": {"ru": "Мой плейлист", "uk": "Мій плейлист", "en": "My playlist"},
    "add_btn": {"ru": "Добавить трек", "uk": "Додати трек", "en": "Add a track"},
    "search_btn": {"ru": "🔎 Поиск", "uk": "🔎 Пошук", "en": "🔎 Search"},
    "link_btn": {"ru": "➕ По ссылке", "uk": "➕ За посиланням", "en": "➕ By link"},
    "link_hint": {"ru": "Пришли прямую HTTPS-ссылку на аудио (mp3/ogg/m4a).", "uk": "Надішли пряму HTTPS-лінку на аудіо (mp3/ogg/m4a).", "en": "Send a direct HTTPS link to audio (mp3/ogg/m4a)."},
    "bad_url": {"ru": "Нужна прямая HTTPS-ссылка на файл (mp3/ogg/m4a).", "uk": "Потрібне пряме HTTPS-посилання на файл (mp3/ogg/m4a).", "en": "Need a direct HTTPS file link (mp3/ogg/m4a)."},
    "link_saved": {"ru": "Сохранил ссылку в плейлист ✅", "uk": "Зберіг посилання у плейлист ✅", "en": "Saved link to playlist ✅"},
    "search_hint": {
        "ru": "Напиши название трека или артиста.",
        "uk": "Напиши назву треку або артиста.",
        "en": "Type a song name or an artist.",
    },
    "search_results": {
        "ru": "Результаты поиска:",
        "uk": "Результати пошуку:",
        "en": "Search results:",
    },
    "saved_external": {
        "ru": "Сохранил в плейлист (превью/ссылка) ✅",
        "uk": "Зберіг у плейлист (превʼю/посилання) ✅",
        "en": "Saved to playlist (preview/link) ✅",
    },
    "open_focus": {
        "ru": "Открыть Focus ▶️",
        "uk": "Відкрити Focus ▶️",
        "en": "Open Focus ▶️",
    },
    "open_sleep": {
        "ru": "Открыть Sleep ▶️",
        "uk": "Відкрити Sleep ▶️",
        "en": "Open Sleep ▶️",
    },
    "back": {"ru": "⬅️ Назад", "uk": "⬅️ Назад", "en": "⬅️ Back"},
    "send_audio_hint": {
        "ru": "Пришли мне аудио-файл(ы) — добавлю в твой плейлист.",
        "uk": "Надішли аудіофайл(и) — додам у твій плейлист.",
        "en": "Send me audio file(s) — I will add them to your playlist.",
    },
    "saved": {
        "ru": "Сохранил в твой плейлист ✅",
        "uk": "Зберіг у твій плейлист ✅",
        "en": "Saved to your playlist ✅",
    },
    "empty": {"ru": "Пока пусто.", "uk": "Поки порожньо.", "en": "No tracks yet."},
    "your_tracks": {"ru": "Твои треки:", "uk": "Твої треки:", "en": "Your tracks:"},
    "too_many": {
        "ru": "Пока максимум 50 треков в плейлисте.",
        "uk": "Поки максимум 50 треків у плейлисті.",
        "en": "For now the playlist limit is 50 tracks.",
    },
    "need_start": {"ru": "Нажми /start", "uk": "Натисни /start", "en": "Type /start"},
}


def _normalize_lang(code: str | None) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    return "ru"


def _tr(lang: str | None, key: str) -> str:
    l = _normalize_lang(lang)
    return TXT.get(key, {}).get(l, TXT.get(key, {}).get("ru", key))


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def _user_lang(user: User | None, tg_lang: str | None) -> str:
    raw = (
        (getattr(user, "locale", None) if user else None)
        or (getattr(user, "lang", None) if user else None)
        or tg_lang
        or "ru"
    )
    l = _normalize_lang(str(raw))
    return l if l in SUPPORTED else "ru"


def _urls() -> tuple[str, str]:
    try:
        from app.config import settings as cfg
    except Exception:
        cfg = None

    focus = (
        getattr(cfg, "music_focus_url", None)
        or os.getenv("MUSIC_FOCUS_URL")
        or "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    )
    sleep = (
        getattr(cfg, "music_sleep_url", None)
        or os.getenv("MUSIC_SLEEP_URL")
        or "https://www.youtube.com/watch?v=5qap5aO4i9A"
    )
    return str(focus), str(sleep)


def _menu_kb(l: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=_tr(l, "focus_btn"), callback_data="music:focus"),
                InlineKeyboardButton(text=_tr(l, "sleep_btn"), callback_data="music:sleep"),
            ],
            [
                InlineKeyboardButton(text=_tr(l, "my_btn"), callback_data="music:my"),
                InlineKeyboardButton(text=_tr(l, "add_btn"), callback_data="music:add"),
            ],
            [
                *( [InlineKeyboardButton(text="🎧 Открыть плеер", web_app=WebAppInfo(url=WEBAPP_MUSIC_URL))] if _is_https_url(WEBAPP_MUSIC_URL) else [] ),
                InlineKeyboardButton(text=_tr(l, "link_btn"), callback_data="music:link"),
                InlineKeyboardButton(text=_tr(l, "search_btn"), callback_data="music:search"),
            ],
        ]
    )


def _open_kb(l: str, kind: str) -> InlineKeyboardMarkup:
    focus, sleep = _urls()
    url = focus if kind == "focus" else sleep
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_tr(l, f"open_{kind}"), url=url)],
            [InlineKeyboardButton(text=_tr(l, "back"), callback_data="music:back")],
        ]
    )


def _numbers_kb(l: str, items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    row: list[InlineKeyboardButton] = []
    kb: list[list[InlineKeyboardButton]] = []

    for idx, (iid, _title) in enumerate(items, start=1):
        row.append(InlineKeyboardButton(text=str(idx), callback_data=f"music:play/{iid}"))
        if len(row) == 5:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([InlineKeyboardButton(text=_tr(l, "back"), callback_data="music:back")])
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
    kb.append([InlineKeyboardButton(text=_tr(l, "back"), callback_data="music:back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def _save_track(session: AsyncSession, user: User, title: str, file_id: str) -> None:
    existing = await session.scalar(
        select(UserTrack).where(UserTrack.user_id == user.id, UserTrack.file_id == file_id).limit(1)
    )
    if existing:
        if not existing.title and title:
            existing.title = title
            await session.commit()
        return

    total = await session.scalar(select(func.count()).select_from(UserTrack).where(UserTrack.user_id == user.id))
    if (total or 0) >= PLAYLIST_LIMIT:
        raise ValueError("limit")

    session.add(
        UserTrack(
            user_id=user.id,
            tg_id=user.tg_id,
            title=title or None,
            file_id=file_id,
        )
    )
    await session.commit()


async def _list_tracks(session: AsyncSession, user: User, limit: int = MY_LIST_LIMIT) -> list[tuple[int, str]]:
    rows = (
        (
            await session.execute(
                select(UserTrack).where(UserTrack.user_id == user.id).order_by(UserTrack.id.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [(t.id, (t.title or "Track")) for t in rows]


async def _get_track(session: AsyncSession, user: User, track_id: int) -> Optional[UserTrack]:
    return (
        await session.execute(select(UserTrack).where(UserTrack.user_id == user.id, UserTrack.id == track_id).limit(1))
    ).scalar_one_or_none()


@router.message(Command("music"))
@router.message(F.text.func(is_music_btn))
@router.message(F.text.in_({"🎵 Music", "🎵 Музика", "🎵 Музыка", "music", "музыка", "музика"}))
async def cmd_music(m: Message, session: AsyncSession) -> None:
    user = await _get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    if not user:
        await m.answer("Нажми /start")
        return
    await m.answer(_tr(l, "menu"), reply_markup=_menu_kb(l))


@router.callback_query(F.data == "music:search")
async def on_music_search_btn(c: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        await c.answer()
    except TelegramBadRequest:
        pass

    user = await _get_user(session, c.from_user.id)
    l = _user_lang(user, getattr(c.from_user, "language_code", None))
    if not user:
        await cb_reply(c, "Нажми /start")
        return

    await state.set_state(MusicStates.waiting_search)
    await cb_reply(c, _tr(l, "search_hint"))


@router.callback_query(F.data.startswith("music:"))
async def on_music_choice(c: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = c.data or ""
    kind = data.split(":", 1)[1] if ":" in data else ""

    try:
        await c.answer()
    except TelegramBadRequest:
        pass

    user = await _get_user(session, c.from_user.id)
    l = _user_lang(user, getattr(c.from_user, "language_code", None))

    if kind in {"back", ""}:
        await cb_edit(c, _tr(l, "menu"), reply_markup=_menu_kb(l))
        return

    if kind in {"focus", "sleep"}:
        await cb_edit(c, _tr(l, "menu"), reply_markup=_open_kb(l, kind))
        return

    
    if kind == "link":
        if not user:
            await cb_reply(c, _tr(l, "need_start"))
            return
        await state.set_state(MusicStates.waiting_link)
        await cb_reply(c, _tr(l, "link_hint"))
        return

    if kind == "add":
        await cb_reply(c, _tr(l, "send_audio_hint"))
        return

    if kind == "my":
        if not user:
            await cb_reply(c, _tr(l, "need_start"))
            return
        rows = await _list_tracks(session, user, limit=MY_LIST_LIMIT)
        if not rows:
            await cb_edit(c, f"{_tr(l, 'empty')} {_tr(l, 'send_audio_hint')}")
        else:
            lines = [f"{i}) {title}" for i, (_id, title) in enumerate(rows, start=1)]
            await cb_edit(c, _tr(l, "your_tracks") + "\n" + "\n".join(lines), reply_markup=_numbers_kb(l, rows))
        return
    
    # pick from search results: music:s/<n>
    if kind.startswith("s/"):
        if not user:
            await cb_reply(c, _tr(l, "need_start"))
            return

        sid = kind.split("/", 1)[1]
        try:
            idx = int(sid) - 1
        except Exception:
            return

        data = await state.get_data()
        packed = data.get("music_search_results") or []
        if not isinstance(packed, list) or idx < 0 or idx >= len(packed):
            await cb_reply(c, "⚠️ Результаты поиска устарели. Нажми «Поиск» ещё раз.")
            return

        item = packed[idx] or {}
        title = str(item.get("title") or "Track").strip()
        artist = str(item.get("artist") or "").strip()
        preview = str(item.get("preview_url") or "").strip()
        url = str(item.get("url") or "").strip()

        caption = title
        if artist:
            caption += f" — {artist}"

        # 1) Проиграть превью (если есть)
        if preview:
            chat_id = int(getattr(getattr(c, "from_user", None), "id", 0) or 0)
            if chat_id:
                await c.bot.send_audio(chat_id=chat_id, audio=preview, caption=caption)
        elif url:
            await cb_reply(c, f"🎧 Превью нет, но вот ссылка:\n{url}")
        else:
            await cb_reply(c, "⚠️ Нет ни превью, ни ссылки.")
            return

        # 2) Сохранить в плейлист: сохраняем ТОЛЬКО превью (оно реально воспроизводится)
        if preview:
            try:
                await _save_track(session, user, caption, preview)
                await cb_reply(c, _tr(l, "saved"))
            except ValueError:
                await cb_reply(c, _tr(l, "too_many"))

        return

    if kind.startswith("play/"):
        if not user:
            return

        sid = kind.split("/", 1)[1]
        try:
            track_id = int(sid)
        except Exception:
            return

        track = await _get_track(session, user, track_id)
        if not track:
            return

        # callback может быть без доступного message -> отправляем через bot
        chat_id = int(getattr(getattr(c, "from_user", None), "id", 0) or 0)
        if not chat_id:
            return

        audio_src = (track.file_id or "").strip()
        try:
            # if it is a URL, Telegram will fetch it; if it is file_id, Telegram will reuse it
            await c.bot.send_audio(chat_id=chat_id, audio=audio_src, caption=track.title or None)
        except TelegramBadRequest:
            # fallback: show link instead of crashing
            if _is_http_url(audio_src):
                await c.bot.send_message(chat_id=chat_id, text=f"🎧 Не удалось отправить как аудио, вот ссылка:\n{audio_src}")
            else:
                raise
        return


@router.message(F.audio)
async def on_audio_inbox(m: Message, session: AsyncSession) -> None:
    user = await _get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))

    if not user:
        await m.answer(_tr(l, "need_start"))
        return

    title = (
        getattr(m.audio, "title", None)
        or getattr(m.audio, "file_name", None)
        or getattr(m.audio, "performer", None)
        or "Track"
    )

    try:
        await _save_track(session, user, title, m.audio.file_id)
    except ValueError:
        await m.answer(_tr(l, "too_many"))
        return

    await m.answer(_tr(l, "saved"))


@router.message(F.document.mime_type.startswith("audio/"))
async def on_audio_document(m: Message, session: AsyncSession) -> None:
    user = await _get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))

    if not user:
        await m.answer(_tr(l, "need_start"))
        return

    title = getattr(m.document, "file_name", None) or "Track"

    try:
        await _save_track(session, user, title, m.document.file_id)
    except ValueError:
        await m.answer(_tr(l, "too_many"))
        return

    await m.answer(_tr(l, "saved"))



@router.message(MusicStates.waiting_link, F.text)
async def on_music_link(m: Message, state: FSMContext, session: AsyncSession) -> None:
    user = await _get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    if not user:
        await m.answer(_tr(l, "need_start"))
        return

    url = (m.text or "").strip()
    if not (url.startswith("https://") and re.search(r"\.(mp3|ogg|m4a|aac|wav)(\?|$)", url, re.IGNORECASE)):
        await m.answer(_tr(l, "bad_url"))
        return

    title = url.split("/")[-1].split("?")[0] or "Audio link"
    try:
        await _save_track(session, user, title, url)
    except ValueError:
        await m.answer(_tr(l, "too_many"))
        return

    await state.set_state(None)
    await m.answer(_tr(l, "link_saved"))


@router.message(MusicStates.waiting_search, F.text)
async def on_music_search_query(m: Message, state: FSMContext, session: AsyncSession) -> None:
    user = await _get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    if not user:
        await m.answer("Нажми /start")
        return

    q = (m.text or "").strip()
    if not q:
        await m.answer(_tr(l, "search_hint"))
        return

    results = await search_tracks(q, limit=10)
    if not results:
        await m.answer(_tr(l, "empty"))
        return

    packed = [
        {
            "title": r.title,
            "artist": r.artist,
            "url": r.url,
            "preview_url": r.preview_url,
            "source": r.source,
        }
        for r in results
    ]
    await state.update_data(music_search_results=packed)
    await state.set_state(None)

    lines = []
    for i, r in enumerate(results, start=1):
        line = f"{i}) {r.title}"
        if r.artist:
            line += f" — {r.artist}"
        lines.append(line)

    await m.answer(
        _tr(l, "search_results") + "\n" + "\n".join(lines),
        reply_markup=_search_numbers_kb(l, len(results)),
    )


__all__ = ["router"]
