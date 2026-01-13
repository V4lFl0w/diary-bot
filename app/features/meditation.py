from __future__ import annotations

from typing import Optional, Dict

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User

# кнопка из главного меню (если есть)
try:
    from app.keyboards import is_meditation_btn
except Exception:  # pragma: no cover
    def is_meditation_btn(_text: str) -> bool:  # type: ignore
        return False

# settings optional
try:
    from app.config import settings  # type: ignore
except Exception:  # pragma: no cover
    settings = None  # type: ignore


router = Router(name="meditation")


# -------------------- i18n --------------------

TXT = {
    "menu": {
        "ru": "Выбери режим медитации:",
        "uk": "Вибери режим медитації:",
        "en": "Choose meditation mode:",
    },
    "focus": {"ru": "Фокус", "uk": "Фокус", "en": "Focus"},
    "calm": {"ru": "Спокойствие", "uk": "Спокій", "en": "Calm"},
    "sleep": {"ru": "Сон", "uk": "Сон", "en": "Sleep"},
    "open": {"ru": "Открыть ▶️", "uk": "Відкрити ▶️", "en": "Open ▶️"},
    "back": {"ru": "⬅️ Назад", "uk": "⬅️ Назад", "en": "⬅️ Back"},
    "dur_title": {"ru": "Продолжительность:", "uk": "Тривалість:", "en": "Duration:"},
    "d5": {"ru": "5 мин", "uk": "5 хв", "en": "5 min"},
    "d10": {"ru": "10 мин", "uk": "10 хв", "en": "10 min"},
    "d15": {"ru": "15 мин", "uk": "15 хв", "en": "15 min"},
    "started": {
        "ru": "Старт. Сессия {dur} в режиме «{mode}». Трек ниже.",
        "uk": "Старт. Сесія {dur} у режимі «{mode}». Трек нижче.",
        "en": "Started. {dur} session in “{mode}” mode. Track below.",
    },
}


SUPPORTED_LANGS = {"ru", "uk", "en"}
MODES = ("focus", "calm", "sleep")
DURATIONS = {"d5": 5, "d10": 10, "d15": 15}

CB_PREFIX = "med"
CB_NOOP = "med:noop"
CB_BACK = "med:back"


# -------------------- helpers --------------------

def _normalize_lang(code: Optional[str]) -> str:
    raw = (code or "ru").strip().lower()

    if raw.startswith(("ua", "uk")):
        return "uk"
    if raw.startswith("en"):
        return "en"
    if raw.startswith("ru"):
        return "ru"
    return "ru"


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(
        select(User).where(User.tg_id == tg_id)
    )).scalar_one_or_none()


async def _lang(obj: Message | CallbackQuery, session: AsyncSession) -> str:
    tg_user = getattr(obj, "from_user", None)
    tg_id = getattr(tg_user, "id", None)
    tg_lang = getattr(tg_user, "language_code", None)

    user: Optional[User] = None
    if tg_id:
        try:
            user = await _get_user(session, tg_id)
        except Exception:
            user = None

    code = (
        getattr(user, "locale", None)
        or getattr(user, "lang", None)
        or tg_lang
        or "ru"
    )

    l = _normalize_lang(code)
    return l if l in SUPPORTED_LANGS else "ru"


def _mode_title(l: str, mode: str) -> str:
    return TXT.get(mode, {}).get(l, mode)


def _dur_str(l: str, mins: int) -> str:
    if l == "uk":
        return f"{mins} хв"
    if l == "en":
        return f"{mins} min"
    return f"{mins} мин"


def _urls() -> Dict[str, str]:
    """
    Канон:
    - можно переопределить через settings (если захочешь)
      settings.meditation_urls = {"focus": "...", "calm": "...", "sleep": "..."}
    - иначе берём безопасные дефолты
    """
    default = {
        "focus": "https://www.youtube.com/watch?v=jfKfPfyJRdk",
        "calm": "https://www.youtube.com/watch?v=5qap5aO4i9A",
        "sleep": "https://www.youtube.com/watch?v=lTRiuFIWV54",
    }

    if settings and hasattr(settings, "meditation_urls"):
        try:
            cfg = getattr(settings, "meditation_urls") or {}
            if isinstance(cfg, dict):
                merged = dict(default)
                for k in ("focus", "calm", "sleep"):
                    v = cfg.get(k)
                    if isinstance(v, str) and v.strip():
                        merged[k] = v.strip()
                return merged
        except Exception:
            pass

    return default


# -------------------- keyboards --------------------

def _menu_kb(l: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=TXT["focus"][l], callback_data="med:mode:focus"),
                InlineKeyboardButton(text=TXT["calm"][l], callback_data="med:mode:calm"),
                InlineKeyboardButton(text=TXT["sleep"][l], callback_data="med:mode:sleep"),
            ]
        ]
    )


def _dur_kb(l: str, mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=TXT["dur_title"][l], callback_data=CB_NOOP),
            ],
            [
                InlineKeyboardButton(text=TXT["d5"][l], callback_data=f"med:dur:{mode}:d5"),
                InlineKeyboardButton(text=TXT["d10"][l], callback_data=f"med:dur:{mode}:d10"),
                InlineKeyboardButton(text=TXT["d15"][l], callback_data=f"med:dur:{mode}:d15"),
            ],
            [
                InlineKeyboardButton(text=TXT["back"][l], callback_data=CB_BACK),
            ],
        ]
    )


def _open_kb(l: str, mode: str) -> InlineKeyboardMarkup:
    url = _urls().get(mode)
    if not url:
        url = _urls()["calm"]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=TXT["open"][l], url=url),
            ]
        ]
    )


# -------------------- entrypoints --------------------

@router.message(Command("meditation"))
@router.message(F.text.func(is_meditation_btn))
async def cmd_meditation(m: Message, session: AsyncSession) -> None:
    l = await _lang(m, session)
    await m.answer(TXT["menu"][l], reply_markup=_menu_kb(l))


# -------------------- callbacks --------------------

@router.callback_query(F.data == CB_NOOP)
async def med_noop(c: CallbackQuery) -> None:
    # просто убираем "крутилку"
    await c.answer()


@router.callback_query(F.data == CB_BACK)
async def med_back(c: CallbackQuery, session: AsyncSession) -> None:
    l = await _lang(c, session)
    await c.answer()
    await c.message.answer(TXT["menu"][l], reply_markup=_menu_kb(l))


@router.callback_query(F.data.startswith("med:mode:"))
async def med_choose_mode(c: CallbackQuery, session: AsyncSession) -> None:
    l = await _lang(c, session)
    parts = (c.data or "").split(":")
    mode = parts[2] if len(parts) >= 3 else ""

    if mode not in MODES:
        await c.answer()
        return

    await c.answer()
    await c.message.answer(
        f"{TXT['dur_title'][l]} {_mode_title(l, mode)}",
        reply_markup=_dur_kb(l, mode),
    )


@router.callback_query(F.data.startswith("med:dur:"))
async def med_choose_duration(c: CallbackQuery, session: AsyncSession) -> None:
    l = await _lang(c, session)
    parts = (c.data or "").split(":")

    if len(parts) < 4:
        await c.answer()
        return

    mode, dkey = parts[2], parts[3]
    if mode not in MODES:
        await c.answer()
        return

    mins = DURATIONS.get(dkey, 5)

    await c.answer()

    await c.message.answer(
        TXT["started"][l].format(
            dur=_dur_str(l, mins),
            mode=_mode_title(l, mode),
        )
    )

    await c.message.answer(
        TXT["open"][l],
        reply_markup=_open_kb(l, mode),
    )


__all__ = ["router"]