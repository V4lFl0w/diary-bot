from __future__ import annotations

import os
import re
from typing import Optional
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.keyboards import get_main_kb
from app.utils.aiogram_guards import cb_reply

router = Router()


def _admin_ids():
    # settings.admin_ids может быть списком/строкой; также поддержим переменную окружения ADMINS="id1,id2"
    val = getattr(settings, "admin_ids", None)
    if isinstance(val, (list, tuple, set)):
        ids = {int(x) for x in val if str(x).isdigit()}
    elif isinstance(val, str):
        ids = {int(x) for x in re.findall(r"\d+", val)}
    else:
        ids = set()
    env = os.getenv("ADMINS", "")
    ids |= {int(x) for x in re.findall(r"\d+", env)}
    return ids


async def _reset_premium(session: AsyncSession, tg_id: int) -> int:
    q = sql_text("UPDATE users SET is_premium=0, premium_until=NULL WHERE tg_id=:tg")
    res = await session.execute(q, {"tg": tg_id})
    await session.commit()
    return res.rowcount or 0


def _loc(lang: Optional[str]) -> str:
    l = (lang or getattr(settings, "default_locale", "ru"))[:2].lower()
    return "uk" if l == "ua" else (l if l in {"ru", "uk", "en"} else "ru")


_MSG = {
    "ru": {
        "done_self": "Готово ✅ Премиум для твоего аккаунта сброшен.",
        "done_other": "Готово ✅ Премиум сброшен для tg_id={tg}.",
        "no_change": "Нечего сбрасывать — премиум уже выключен.",
        "forbidden": "Недостаточно прав для сброса у другого пользователя.",
        "usage": "Использование:\n/premium_reset — сбросить у себя\n/premium_reset 123456789 — (только админ) сбросить у tg_id",
    },
    "uk": {
        "done_self": "Готово ✅ Преміум для твого акаунта скинуто.",
        "done_other": "Готово ✅ Преміум скинуто для tg_id={tg}.",
        "no_change": "Нічого скидати — преміум вже вимкнено.",
        "forbidden": "Недостатньо прав для скидання в іншого користувача.",
        "usage": "Використання:\n/premium_reset — скинути у себе\n/premium_reset 123456789 — (лише адмін) скинути у tg_id",
    },
    "en": {
        "done_self": "Done ✅ Premium for your account has been reset.",
        "done_other": "Done ✅ Premium has been reset for tg_id={tg}.",
        "no_change": "Nothing to reset — premium is already off.",
        "forbidden": "Not allowed to reset for another user.",
        "usage": "Usage:\n/premium_reset — reset for yourself\n/premium_reset 123456789 — (admin only) reset for tg_id",
    },
}


@router.message(Command("premium_reset"))
async def premium_reset(m: Message, session: AsyncSession, lang: Optional[str] = None):
    loc = _loc(lang)
    admins = _admin_ids()
    args = (m.text or "").split()[1:]

    target_tg = m.from_user.id
    if args:
        # админ может передать чужой tg_id
        m_id = re.search(r"\d{5,}", " ".join(args))
        if m_id:
            if m.from_user.id not in admins:
                await m.answer(_MSG[loc]["forbidden"])
                return
            target_tg = int(m_id.group(0))

    changed = await _reset_premium(session, target_tg)
    if changed:
        if target_tg == m.from_user.id:
            await m.answer(_MSG[loc]["done_self"], reply_markup=get_main_kb(loc))
        else:
            await m.answer(
                _MSG[loc]["done_other"].format(tg=target_tg),
                reply_markup=get_main_kb(loc),
            )
    else:
        await m.answer(_MSG[loc]["no_change"], reply_markup=get_main_kb(loc))


# На будущее: если добавишь инлайн-кнопку "premium:reset", этот хендлер тоже отработает
@router.callback_query(F.data == "premium:reset")
async def premium_reset_cb(
    c: CallbackQuery, session: AsyncSession, lang: Optional[str] = None
):
    loc = _loc(lang)
    await c.answer()
    changed = await _reset_premium(session, c.from_user.id)
    await cb_reply(
        c,
        _MSG[loc]["done_self"] if changed else _MSG[loc]["no_change"],
        reply_markup=get_main_kb(loc),
    )
