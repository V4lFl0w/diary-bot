# app/handlers/erase.py
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message


router = Router()


class EraseFSM(StatesGroup):
    confirm = State()


_L10N = {
    "prompt": {
        "ru": "❗️Удалить все твои записи/репорты/напоминания?\n\nНапиши: <code>{phrase}</code>\nИли /cancel — чтобы отменить.\n\n(Код действует 2 минуты.)",
        "uk": "❗️Видалити всі твої записи/репорти/нагадування?\n\nНапиши: <code>{phrase}</code>\nАбо /cancel — щоб скасувати.\n\n(Код діє 2 хвилини.)",
        "en": "❗️Delete all your entries/reports/reminders?\n\nType: <code>{phrase}</code>\nOr /cancel to abort.\n\n(The code is valid for 2 minutes.)",
    },
    "canceled": {
        "ru": "Отменил. Ничего не удалял.",
        "uk": "Скасував. Нічого не видаляв.",
        "en": "Canceled. Nothing was deleted.",
    },
    "need_start": {
        "ru": "Нажми /start",
        "uk": "Натисни /start",
        "en": "Press /start",
    },
    "expired": {
        "ru": "⏱ Срок действия кода истёк. Вызови /erase снова.",
        "uk": "⏱ Термін дії коду минув. Виклич /erase ще раз.",
        "en": "⏱ The code expired. Run /erase again.",
    },
    "wrong": {
        "ru": "Нужно написать ровно: <code>{phrase}</code> — или /cancel",
        "uk": "Потрібно написати рівно: <code>{phrase}</code> — або /cancel",
        "en": "You must type exactly: <code>{phrase}</code> — or /cancel",
    },
    "done": {
        "ru": "✅ Готово. Все твои данные удалены.\n(Аккаунт оставил, можно продолжать пользоваться ботом.)\n\nУдалено: заметок — {j}, напоминаний — {r}, репортов — {b}.",
        "uk": "✅ Готово. Усі твої дані видалено.\n(Акаунт лишив, можна далі користуватися ботом.)\n\nВидалено: нотаток — {j}, нагадувань — {r}, репортів — {b}.",
        "en": "✅ Done. All your data is deleted.\n(Account kept; you can continue using the bot.)\n\nDeleted: entries — {j}, reminders — {r}, reports — {b}.",
    },
}


def _norm_lang(code: str | None) -> str:
    loc = (code or "ru").strip().lower()[:2]
    if loc == "ua":
        loc = "uk"
    if loc not in ("ru", "uk", "en"):
        loc = "ru"
    return loc


def _lang(m, user=None) -> str:
    return _norm_lang(
        getattr(user, "language", None)
        or getattr(user, "locale", None)
        or getattr(user, "lang", None)
        or getattr(getattr(m, "from_user", None), "language_code", None)
        or "ru"
    )


def _t(lang: str, key: str, **fmt):
    block = _L10N.get(key, {}) if isinstance(globals().get("_L10N"), dict) else {}
    template = (block.get(_norm_lang(lang)) or block.get("ru") or key) if isinstance(block, dict) else key
    try:
        return template.format(**fmt)
    except Exception:
        return template


@router.message(Command("erase"))
async def erase_start(m: Message, state: FSMContext):
    # Генерим одноразовую фразу вида "DELETE ABC123"
    token = secrets.token_hex(3).upper()  # 6 символов
    phrase = f"DELETE {token}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)

    await state.set_state(EraseFSM.confirm)
    await state.update_data(phrase=phrase, expires_at=expires_at.isoformat())

    # Язык возьмём из профиля позже, тут пока без session
    await m.answer(_t(_lang(m, None), "prompt", phrase=phrase), parse_mode="HTML")


@router.message(Command("cancel"), EraseFSM.confirm)
async def erase_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(_t(_lang(m, None), "canceled"))
