from __future__ import annotations

from typing import cast
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.kb import kb_add, kb_search

try:
    from app.services.features_v2 import require_feature_v2
except Exception:
    require_feature_v2 = None  # type: ignore


router = Router(name="kb")


class KBStates(StatesGroup):
    waiting_add_text = State()
    waiting_ask_text = State()


def _kb_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📚 Добавить", callback_data="kb:add")
    kb.button(text="🔎 Спросить", callback_data="kb:ask")
    kb.adjust(2)
    return kb.as_markup()


def _gate_ok(message_or_query) -> bool:
    # if require_feature_v2 exists, enforce kb_v1
    if require_feature_v2 is None:
        return True
    return True


@router.message(Command("kb"))
async def kb_cmd(message: Message, session: AsyncSession, user: User, state: FSMContext):
    if require_feature_v2 is not None:
        ok = await require_feature_v2(message, session=session, user=user, feature="kb_v1")
        if not ok:
            return
    await state.clear()
    await message.answer("📚 KB v1\nВыбери действие:", reply_markup=_kb_menu_kb())


@router.callback_query(F.data == "kb:add")
async def kb_add_cb(call: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    msg0 = call.message
    if msg0 is None:
        await call.answer()
        return
    msg = cast(Message, msg0)
    if require_feature_v2 is not None:
        ok = await require_feature_v2(msg, session=session, user=user, feature="kb_v1")
        if not ok:
            return
    await state.set_state(KBStates.waiting_add_text)
    await call.message.answer("Ок. Скинь текст/факт, который добавить в KB (одним сообщением).")
    await call.answer()


@router.callback_query(F.data == "kb:ask")
async def kb_ask_cb(call: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    msg0 = call.message
    if msg0 is None:
        await call.answer()
        return
    msg = cast(Message, msg0)
    if require_feature_v2 is not None:
        ok = await require_feature_v2(msg, session=session, user=user, feature="kb_v1")
        if not ok:
            return
    await state.set_state(KBStates.waiting_ask_text)
    await call.message.answer("Ок. Напиши вопрос — я найду релевантные записи из KB.")
    await call.answer()


@router.message(KBStates.waiting_add_text)
async def kb_add_text(message: Message, session: AsyncSession, user: User, state: FSMContext):
    txt = (message.text or "").strip()
    if not txt:
        await message.answer("Скинь текстом, пожалуйста 🙂")
        return
    item = await kb_add(session, user_id=int(user.id), content=txt)
    await state.clear()
    await message.answer(f"✅ Добавлено в KB (id={item.id}).", reply_markup=_kb_menu_kb())


@router.message(KBStates.waiting_ask_text)
async def kb_ask_text(message: Message, session: AsyncSession, user: User, state: FSMContext):
    q = (message.text or "").strip()
    if not q:
        await message.answer("Напиши вопрос текстом 🙂")
        return
    hits = await kb_search(session, user_id=int(user.id), q=q, limit=5)
    await state.clear()

    if not hits:
        await message.answer("Ничего не нашёл в KB по этому запросу.", reply_markup=_kb_menu_kb())
        return

    lines = ["🔎 Нашёл в KB:"]
    for i, h in enumerate(hits, 1):
        title = f" — {h['title']}" if h.get("title") else ""
        lines.append(f"{i}) id={h['id']}{title}\n{h['content']}")
    await message.answer("\n\n".join(lines), reply_markup=_kb_menu_kb())
