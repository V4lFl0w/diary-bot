# app/handlers/media_nav.py
from __future__ import annotations

from typing import Any, Optional

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

router = Router(name="media_nav")

# ВАЖНО:
# Это простое in-memory хранилище. На прод лучше Redis/DB.
_MEDIA_CACHE: dict[int, list[dict[str, Any]]] = {}  # user_id -> [cand...]
_MEDIA_IDX: dict[int, int] = {}  # user_id -> current idx


def _kb(idx: int, total: int) -> InlineKeyboardMarkup:
    prev_btn = InlineKeyboardButton(text="⬅️", callback_data="media:nav:prev")
    next_btn = InlineKeyboardButton(text="➡️", callback_data="media:nav:next")
    ok_btn = InlineKeyboardButton(text="✅ Это он", callback_data="media:pick")
    refine_btn = InlineKeyboardButton(text="🔍 Уточнить", callback_data="media:refine")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [prev_btn, InlineKeyboardButton(text=f"{idx + 1}/{total}", callback_data="media:noop"), next_btn],
            [ok_btn],
            [refine_btn],
        ]
    )


def _format_caption(c: dict[str, Any]) -> str:
    # ожидаем минимум:
    # title/name, year, overview, rating
    title = c.get("title") or c.get("name") or "Без названия"
    year = c.get("year") or c.get("release_year") or ""
    rating = c.get("rating") or c.get("vote_average") or ""
    overview = (c.get("overview") or "").strip()

    head = f"🎬 {title}"
    if year:
        head += f" ({year})"
    if rating != "":
        head += f"\n⭐ {rating}"

    if overview:
        if len(overview) > 500:
            overview = overview[:500].rstrip() + "…"
        head += "\n\n" + overview
    return head


def _poster_url(c: dict[str, Any]) -> Optional[str]:
    # подстрой под себя:
    return c.get("poster_url") or c.get("poster") or c.get("posterPath") or None


async def show_media_carousel(
    *,
    bot,  # aiogram Bot
    user_id: int,
    chat_id: int,
    cands: list[dict[str, Any]],
) -> None:
    cands = cands[:3]
    _MEDIA_CACHE[user_id] = cands
    _MEDIA_IDX[user_id] = 0

    c0 = cands[0]
    poster = _poster_url(c0)
    cap = _format_caption(c0)
    kb = _kb(0, len(cands))

    if poster:
        await bot.send_photo(chat_id=chat_id, photo=poster, caption=cap, reply_markup=kb)
    else:
        await bot.send_message(chat_id=chat_id, text=cap, reply_markup=kb)


@router.callback_query(F.data.startswith("media:"))
async def on_media_nav(cb: CallbackQuery, session: AsyncSession) -> None:
    uid = cb.from_user.id if cb.from_user else 0
    data = (cb.data or "").strip()

    if data == "media:noop":
        await cb.answer()
        return

    cands = _MEDIA_CACHE.get(uid) or []
    if not cands:
        await cb.answer("Нет вариантов (кэш пуст). Скинь кадр ещё раз.", show_alert=True)
        return

    idx = _MEDIA_IDX.get(uid, 0)

    if data == "media:nav:next":
        idx = (idx + 1) % len(cands)
        _MEDIA_IDX[uid] = idx
    elif data == "media:nav:prev":
        idx = (idx - 1) % len(cands)
        _MEDIA_IDX[uid] = idx
    elif data == "media:pick":
        # TODO: вот тут ты делаешь "подтвердить" → сохранить, прикрепить к записи и т.д.
        await cb.answer("✅ Ок, зафиксировал вариант.", show_alert=False)
        return
    elif data == "media:refine":
        # TODO: вот тут ты просишь уточнение у пользователя (сообщением)
        await cb.message.answer("Ок, уточни: актёры / год / жанр / что происходит в сцене?")
        await cb.answer()
        return
    else:
        await cb.answer()
        return

    c = cands[idx]
    cap = _format_caption(c)
    kb = _kb(idx, len(cands))
    poster = _poster_url(c)

    try:
        if poster:
            # редактируем подпись + клаву (фото в телеге менять нельзя без resend — оставляем постер тем же)
            await cb.message.edit_caption(caption=cap, reply_markup=kb)
        else:
            await cb.message.edit_text(text=cap, reply_markup=kb)
    except Exception:
        # если не смогли редактировать — просто шлём новое
        if poster:
            await cb.message.answer_photo(photo=poster, caption=cap, reply_markup=kb)
        else:
            await cb.message.answer(text=cap, reply_markup=kb)

    await cb.answer()
