from __future__ import annotations

import os
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.models.user import User
from app.models.user_track import UserTrack
from sqlalchemy import func

try:
    from app.keyboards import is_music_btn
except Exception:
    def is_music_btn(_text: str) -> bool:  # type: ignore
        return False


router = Router(name="music")

SUPPORTED = {"ru", "uk", "en"}

TXT = {
    "menu": {
        "ru": "Выбери плейлист:",
        "uk": "Оберіть плейлист:",
        "en": "Choose a playlist:",
    },
    "focus_btn": {"ru": "Фокус", "uk": "Фокус", "en": "Focus"},
    "sleep_btn": {"ru": "Сон", "uk": "Сон", "en": "Sleep"},
    "my_btn":    {"ru": "Мой плейлист", "uk": "Мій плейлист", "en": "My playlist"},
    "add_btn":   {"ru": "Добавить трек", "uk": "Додати трек", "en": "Add a track"},
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
    "back": {
        "ru": "⬅️ Назад",
        "uk": "⬅️ Назад",
        "en": "⬅️ Back",
    },
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
    "empty": {
        "ru": "Пока пусто. ",
        "uk": "Поки порожньо. ",
        "en": "No tracks yet. ",
    },
    "your_tracks": {
        "ru": "Твои треки:",
        "uk": "Твої треки:",
        "en": "Your tracks:",
    },
    "too_many": {
        "ru": "Пока максимум 50 треков в плейлисте.",
        "uk": "Поки максимум 50 треків у плейлисті.",
        "en": "For now the playlist limit is 50 tracks.",
    }
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


def _tr(l: str, key: str) -> str:
    l = _normalize_lang(l)
    return TXT[key].get(l, TXT[key]["ru"])


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(
        select(User).where(User.tg_id == tg_id)
    )).scalar_one_or_none()


def _user_lang(user: Optional[User], tg_lang: Optional[str]) -> str:
    raw = (
        getattr(user, "locale", None)
        or getattr(user, "lang", None)
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
        row.append(
            InlineKeyboardButton(
                text=str(idx),
                callback_data=f"music:play/{iid}",
            )
        )
        if len(row) == 5:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    kb.append([InlineKeyboardButton(text=_tr(l, "back"), callback_data="music:back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def _save_track(session: AsyncSession, user: User, title: str, file_id: str) -> None:
    existing = await session.scalar(
        select(UserTrack)
        .where(UserTrack.user_id == user.id, UserTrack.file_id == file_id)
        .limit(1)
    )

    if existing:
        if not existing.title and title:
            existing.title = title
            await session.commit()
        return

    total = await session.scalar(
        select(func.count()).select_from(UserTrack).where(UserTrack.user_id == user.id)
    )

    if (total or 0) >= 50:
        raise ValueError("limit")

    session.add(
        UserTrack(
            user_id=user.id,
            tg_id=user.tg_id,          # ✅ ВОТ ЭТО ОБЯЗАТЕЛЬНО
            title=title or None,
            file_id=file_id
        )
    )
    await session.commit()


async def _list_tracks(session: AsyncSession, user: User, limit: int = 10) -> list[tuple[int, str]]:
    rows = (await session.execute(
        select(UserTrack)
        .where(UserTrack.user_id == user.id)
        .order_by(UserTrack.id.desc())
        .limit(limit)
    )).scalars().all()
    return [(t.id, (t.title or "Track")) for t in rows]


async def _get_track(session: AsyncSession, user: User, track_id: int) -> Optional[UserTrack]:
    return (await session.execute(
        select(UserTrack)
        .where(UserTrack.user_id == user.id)
        .where(UserTrack.id == track_id)
        .limit(1)
    )).scalar_one_or_none()


@router.message(Command("music"))
@router.message(F.text.func(is_music_btn))
@router.message(
    F.text.in_(
        {
            "🎵 Music",
            "🎵 Музика",
            "🎵 Музыка",
            "music",
            "музыка",
            "музика",
        }
    )
)
async def cmd_music(m: Message, session: AsyncSession) -> None:
    user = await _get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    await m.answer(_tr(l, "menu"), reply_markup=_menu_kb(l))


@router.callback_query(F.data.startswith("music:"))
async def on_music_choice(c: CallbackQuery, session: AsyncSession) -> None:
    kind = (c.data or "").split(":", 1)[1] if ":" in (c.data or "") else ""
    try:
        await c.answer()
    except TelegramBadRequest:
        pass

    user = await _get_user(session, c.from_user.id)
    l = _user_lang(user, getattr(c.from_user, "language_code", None))

    if kind == "back":
        await c.message.answer(_tr(l, "menu"), reply_markup=_menu_kb(l))
        return

    if kind in {"focus", "sleep"}:
        await c.message.answer(_tr(l, "menu"), reply_markup=_open_kb(l, kind))
        return

    if kind == "add":
        await c.message.answer(_tr(l, "send_audio_hint"))
        return

    if kind == "my":
        if not user:
            await c.message.answer("Нажми /start")
            return

        rows = await _list_tracks(session, user, limit=10)
        if not rows:
            await c.message.answer(_tr(l, "empty") + _tr(l, "send_audio_hint"))
        else:
            await c.message.answer(
                _tr(l, "your_tracks"),
                reply_markup=_numbers_kb(l, rows),
            )
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
        if track:
            await c.message.answer_audio(audio=track.file_id, caption=track.title or None)
        return


@router.message(F.audio)
async def on_audio_inbox(m: Message, session: AsyncSession) -> None:
    user = await _get_user(session, m.from_user.id)
    l = _user_lang(user, getattr(m.from_user, "language_code", None))
    if not user:
        await m.answer("Нажми /start")
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
        await m.answer("Нажми /start")
        return

    title = getattr(m.document, "file_name", None) or "Track"

    try:
        await _save_track(session, user, title, m.document.file_id)
    except ValueError:
        await m.answer(_tr(l, "too_many"))
        return

    await m.answer(_tr(l, "saved"))


__all__ = ["router"]
