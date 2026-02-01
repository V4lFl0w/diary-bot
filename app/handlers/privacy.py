from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, cast
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from app.utils.aiogram_guards import cb_reply, is_message
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards import get_main_kb, is_privacy_btn
from app.models.user import User

# ✅ единая логика админа
try:
    from app.handlers.admin import is_admin_tg
except Exception:

    def is_admin_tg(tg_id: int, /) -> bool:
        return False


router = Router(name="privacy")
log = logging.getLogger(__name__)

CB_PRIVACY_OPEN = "privacy:open"
CB_AGREE = "privacy:agree"
CB_DISAGREE = "privacy:disagree"

SUPPORTED = {"ru", "uk", "en"}


# -------------------- texts --------------------

SOFT_INTRO: Dict[str, str] = {
    "ru": (
        "🔒 Перед стартом — короткая политика безопасности.\n"
        "Без неё нельзя пользоваться журналом и личными функциями.\n\n"
        "Это займёт 10 секунд."
    ),
    "uk": (
        "🔒 Перед стартом — коротка політика безпеки.\n"
        "Без неї не можна користуватись журналом і особистими функціями.\n\n"
        "Це займе 10 секунд."
    ),
    "en": (
        "🔒 Before you start — a short safety policy.\n"
        "Without it you can’t use the journal and personal features.\n\n"
        "Takes 10 seconds."
    ),
}

POLICY_TXT: Dict[str, str] = {
    "ru": (
        "🛡️ *Политика безопасности*\n\n"
        "Этот бот — дневник-ассистент, не терапия и не медуслуга.\n\n"
        "*Что мы НЕ делаем:*\n"
        "• не запрашиваем паспорт/банковские данные\n"
        "• не публикуем твои записи\n"
        "• не продаём данные\n\n"
        "*Как ты защищён:*\n"
        "• сохраняем только то, что нужно для работы функций\n"
        "• ты можешь удалить данные командой /delete\\_data\n\n"
        "Нажимая *Agree*, ты принимаешь политику и можешь пользоваться функциями."
    ),
    "uk": (
        "🛡️ *Політика безпеки*\n\n"
        "Цей бот — щоденник-асистент, не терапія і не медична послуга.\n\n"
        "*Що ми НЕ робимо:*\n"
        "• не просимо паспорт/банківські дані\n"
        "• не публікуємо твої записи\n"
        "• не продаємо дані\n\n"
        "*Як ти захищений:*\n"
        "• зберігаємо лише те, що потрібно для роботи функцій\n"
        "• ти можеш видалити дані командою /delete\\_data\n\n"
        "Натискаючи *Agree*, ти приймаєш політику і можеш користуватись функціями."
    ),
    "en": (
        "🛡️ *Safety Policy*\n\n"
        "This bot is a journal assistant, not therapy or medical care.\n\n"
        "*What we DO NOT do:*\n"
        "• we don’t ask for passport/banking data\n"
        "• we don’t publish your entries\n"
        "• we don’t sell data\n\n"
        "*How you are protected:*\n"
        "• we store only what’s needed for features\n"
        "• you can delete your data with /delete\\_data\n\n"
        "By pressing *Agree*, you accept the policy and can use the features."
    ),
}

OK_TXT = {
    "ru": "Спасибо! Политика принята ✅",
    "uk": "Дякую! Політика прийнята ✅",
    "en": "Thanks! Policy accepted ✅",
}
NO_TXT = {
    "ru": "Без принятия политики личные функции недоступны 🔒",
    "uk": "Без прийняття політики особисті функції недоступні 🔒",
    "en": "Without accepting the policy, personal features are locked 🔒",
}


# -------------------- lang --------------------


def _norm_lang(code: str | None) -> str:
    lang = (code or "ru").strip().lower()
    if lang.startswith(("ua", "uk")):
        return "uk"
    if lang.startswith("en"):
        return "en"
    if lang.startswith("ru"):
        return "ru"
    return "ru"


# -------------------- schema guard --------------------


async def _ensure_cols(session: AsyncSession) -> None:
    """
    Временный safe-guard на SQLite.
    На PostgreSQL эти ALTER могут падать — подавляем.
    Лучше потом заменить Alembic-миграцией.
    """
    stmts = [
        "ALTER TABLE users ADD COLUMN policy_accepted INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN consent_accepted_at TIMESTAMP NULL",
    ]
    for s in stmts:
        with contextlib.suppress(Exception):
            await session.execute(sql_text(s))

    with contextlib.suppress(Exception):
        await session.commit()


# -------------------- db helpers --------------------


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


async def _get_or_create_user(session: AsyncSession, tg_id: int, lang: str) -> User:
    """
    Критично:
    если юзера нет в базе, запись флагов не сработает.
    Поэтому создаём запись при принятии политики.
    """
    user = await _get_user(session, tg_id)
    if user:
        # мягко обновим локаль, если пусто
        if not getattr(user, "locale", None):
            with contextlib.suppress(Exception):
                user.locale = lang  # type: ignore[attr-defined]
        if not getattr(user, "lang", None):
            with contextlib.suppress(Exception):
                user.lang = lang  # type: ignore[attr-defined]
        return user

    user = User(tg_id=tg_id, locale=lang, lang=lang)
    session.add(user)
    await session.flush()
    return user


async def _fetch_lang(session: AsyncSession, tg_id: int, tg_lang: str | None) -> str:
    """
    Берём язык из модели, если есть.
    Иначе — Telegram language_code.
    """
    await _ensure_cols(session)

    user = await _get_user(session, tg_id)
    if user:
        raw = getattr(user, "locale", None) or getattr(user, "lang", None) or tg_lang
        return _norm_lang(str(raw))

    return _norm_lang(tg_lang)


def _premium_active(user: Optional[User]) -> bool:
    if not user:
        return False

    # 1) прямой флаг
    with contextlib.suppress(Exception):
        if bool(getattr(user, "is_premium", False)):
            return True

    # 2) premium_until > now
    pu = getattr(user, "premium_until", None)
    if pu:
        try:
            now = datetime.now(timezone.utc)
            if getattr(pu, "tzinfo", None) is None:
                pu = pu.replace(tzinfo=timezone.utc)
            return pu > now
        except Exception:
            return False

    return False


async def _fetch_flags(session: AsyncSession, tg_id: int) -> Tuple[bool, bool]:
    """
    Возвращаем:
    is_admin — строго из единой логики
    is_premium — по модели
    """
    user = await _get_user(session, tg_id)
    is_admin = is_admin_tg(tg_id) or bool(getattr(user, "is_admin", False) if user else False)
    is_premium = _premium_active(user)
    return is_admin, is_premium


# -------------------- keyboards --------------------


def _soft_kb(lang: str) -> InlineKeyboardMarkup:
    label = {
        "ru": "Продолжить",
        "uk": "Продовжити",
        "en": "Continue",
    }.get(lang, "Continue")

    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=CB_PRIVACY_OPEN)]])


def _policy_kb(lang: str) -> InlineKeyboardMarkup:
    agree = "Agree"
    disagree = "Disagree"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=agree, callback_data=CB_AGREE),
                InlineKeyboardButton(text=disagree, callback_data=CB_DISAGREE),
            ]
        ]
    )


# -------------------- публичные show-функции --------------------


async def privacy_soft_show(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    tg_lang = lang or getattr(m.from_user, "language_code", None)
    lang = await _fetch_lang(session, m.from_user.id, tg_lang)
    await m.answer(SOFT_INTRO.get(lang, SOFT_INTRO["ru"]), reply_markup=_soft_kb(lang))


async def privacy_show(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    tg_lang = lang or getattr(m.from_user, "language_code", None)
    lang = await _fetch_lang(session, m.from_user.id, tg_lang)
    text = POLICY_TXT.get(lang, POLICY_TXT["ru"])
    kb = _policy_kb(lang)

    await m.answer(
        text,
        reply_markup=kb,
        parse_mode="Markdown",
    )


# -------------------- commands --------------------


@router.message(Command("privacy"))
@router.message(Command("policy"))
@router.message(F.text.func(is_privacy_btn))
async def privacy_cmd(m: Message, session: AsyncSession) -> None:
    await privacy_show(m, session)


# -------------------- callbacks --------------------


@router.callback_query(F.data == CB_PRIVACY_OPEN)
async def privacy_open_cb(c: CallbackQuery, session: AsyncSession) -> None:
    if not c.message:
        return

    lang = await _fetch_lang(session, c.from_user.id, getattr(c.from_user, "language_code", None))

    if is_message(c.message):
        await privacy_show(cast(Message, c.message), session, lang=lang)
    else:
        await c.bot.send_message(
            c.from_user.id,
            POLICY_TXT.get(lang, POLICY_TXT["ru"]),
            reply_markup=_policy_kb(lang),
            parse_mode="Markdown",
        )

    with contextlib.suppress(Exception):
        await c.answer()


@router.callback_query(F.data == CB_AGREE)
async def privacy_agree(c: CallbackQuery, session: AsyncSession) -> None:
    if not c.message:
        return

    await _ensure_cols(session)
    lang = await _fetch_lang(session, c.from_user.id, getattr(c.from_user, "language_code", None))

    try:
        user = await _get_or_create_user(session, c.from_user.id, lang)

        with contextlib.suppress(Exception):
            user.policy_accepted = True  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            user.consent_accepted_at = datetime.now(timezone.utc)  # type: ignore[attr-defined]

        session.add(user)
        await session.commit()

    except Exception:
        await session.rollback()
        log.exception("policy accept update failed")

    is_admin, is_premium = await _fetch_flags(session, c.from_user.id)

    kb = get_main_kb(lang, is_premium=is_premium, is_admin=is_admin)

    await cb_reply(
        c,
        OK_TXT.get(lang, OK_TXT["ru"]),
        reply_markup=kb,
    )

    with contextlib.suppress(Exception):
        await c.answer()


@router.callback_query(F.data == CB_DISAGREE)
async def privacy_disagree(c: CallbackQuery, session: AsyncSession) -> None:
    if not c.message:
        return

    await _ensure_cols(session)
    lang = await _fetch_lang(session, c.from_user.id, getattr(c.from_user, "language_code", None))

    try:
        user = await _get_or_create_user(session, c.from_user.id, lang)

        with contextlib.suppress(Exception):
            user.policy_accepted = False  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            user.consent_accepted_at = None  # type: ignore[attr-defined]

        session.add(user)
        await session.commit()
    except Exception:
        await session.rollback()
        log.exception("policy decline update failed")

    # после отказа НЕ показываем главное меню,
    # чтобы не вводить в заблуждение — только мягкий возврат
    await cb_reply(
        c,
        NO_TXT.get(lang, NO_TXT["ru"]),
        reply_markup=_soft_kb(lang),
    )

    with contextlib.suppress(Exception):
        await c.answer()


# -------------------- delete data --------------------


@router.message(Command("delete_data"))
async def delete_data_cmd(m: Message, session: AsyncSession) -> None:
    await _ensure_cols(session)

    tg_id = m.from_user.id
    lang = _norm_lang(getattr(m.from_user, "language_code", None))

    user = await _get_user(session, tg_id)
    user_db_id = getattr(user, "id", None) if user else None

    # best-effort удаления
    with contextlib.suppress(Exception):
        await session.execute(
            sql_text("DELETE FROM journal_entries WHERE tg_id=:tg"),
            {"tg": tg_id},
        )

    with contextlib.suppress(Exception):
        await session.execute(
            sql_text("DELETE FROM reminders WHERE tg_id=:tg"),
            {"tg": tg_id},
        )

    if user_db_id:
        with contextlib.suppress(Exception):
            await session.execute(
                sql_text("DELETE FROM user_tracks WHERE user_id=:uid"),
                {"uid": user_db_id},
            )

        with contextlib.suppress(Exception):
            await session.execute(
                sql_text("DELETE FROM analytics_events WHERE user_id=:uid"),
                {"uid": user_db_id},
            )

    if user:
        with contextlib.suppress(Exception):
            user.policy_accepted = False  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            user.consent_accepted_at = None  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            user.is_premium = False  # type: ignore[attr-defined]
        with contextlib.suppress(Exception):
            user.premium_until = None  # type: ignore[attr-defined]

        session.add(user)

    with contextlib.suppress(Exception):
        await session.commit()

    await m.answer(
        {
            "ru": "Готово. Твои данные удалены ✅\nЕсли захочешь вернуться — просто начни заново с /start.",
            "uk": "Готово. Твої дані видалено ✅\nЯкщо захочеш повернутись — почни з /start.",
            "en": "Done. Your data has been deleted ✅\nIf you want to return — start again with /start.",
        }.get(lang, "Done ✅")
    )


__all__ = ["router", "privacy_show", "privacy_soft_show"]
