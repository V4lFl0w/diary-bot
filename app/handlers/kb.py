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


def _normalize_lang(code: str | None) -> str:
    s = (code or "ru").strip().lower()
    if s.startswith(("ua", "uk")): return "uk"
    if s.startswith("en"): return "en"
    return "ru"


def _tr(lang: str, ru: str, uk: str, en: str) -> str:
    loc = _normalize_lang(lang)
    if loc == "uk": return uk
    if loc == "en": return en
    return ru


def _kb_menu_kb(lang: str):
    kb = InlineKeyboardBuilder()
    kb.button(text=_tr(lang, "📚 Добавить", "📚 Додати", "📚 Add"), callback_data="kb:add")
    kb.button(text=_tr(lang, "🔎 Спросить", "🔎 Запитати", "🔎 Ask"), callback_data="kb:ask")
    kb.adjust(2)
    return kb.as_markup()


def _gate_ok(message_or_query) -> bool:
    if require_feature_v2 is None:
        return True
    return True


@router.message(Command("kb"))
async def kb_cmd(message: Message, session: AsyncSession, user: User, state: FSMContext):
    lang = _normalize_lang(getattr(user, "lang", None) or getattr(message.from_user, "language_code", None))
    if require_feature_v2 is not None:
        ok = await require_feature_v2(message, session=session, user=user, feature="kb_v1")
        if not ok:
            return
    await state.clear()
    msg = _tr(lang, "📚 KB v1\nВыбери действие:", "📚 KB v1\nОбери дію:", "📚 KB v1\nChoose action:")
    await message.answer(msg, reply_markup=_kb_menu_kb(lang))


@router.callback_query(F.data == "kb:add")
async def kb_add_cb(call: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    lang = _normalize_lang(getattr(user, "lang", None) or getattr(call.from_user, "language_code", None))
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
    txt = _tr(lang, "Ок. Скинь текст/факт, который добавить в KB (одним сообщением).", "Ок. Надішли текст/факт для збереження у KB.", "Ok. Send text/fact to save in KB (one message).")
    await call.message.answer(txt)
    await call.answer()


@router.callback_query(F.data == "kb:ask")
async def kb_ask_cb(call: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    lang = _normalize_lang(getattr(user, "lang", None) or getattr(call.from_user, "language_code", None))
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
    txt = _tr(lang, "Ок. Напиши вопрос — я найду релевантные записи из KB.", "Ок. Напиши питання — я знайду релевантні записи з KB.", "Ok. Ask a question and I'll find relevant KB notes.")
    await call.message.answer(txt)
    await call.answer()


@router.message(KBStates.waiting_add_text)
async def kb_add_text(message: Message, session: AsyncSession, user: User, state: FSMContext):
    lang = _normalize_lang(getattr(user, "lang", None) or getattr(message.from_user, "language_code", None))
    txt = (message.text or "").strip()
    if not txt:
        await message.answer(_tr(lang, "Скинь текстом, пожалуйста 🙂", "Надішли текстом, будь ласка 🙂", "Send as text, please 🙂"))
        return
    item = await kb_add(session, user_id=int(user.id), content=txt)
    await state.clear()
    await message.answer(_tr(lang, f"✅ Добавлено в KB (id={item.id}).", f"✅ Додано до KB (id={item.id}).", f"✅ Added to KB (id={item.id})."), reply_markup=_kb_menu_kb(lang))


@router.message(KBStates.waiting_ask_text)
async def kb_ask_text(message: Message, session: AsyncSession, user: User, state: FSMContext):
    lang = _normalize_lang(getattr(user, "lang", None) or getattr(message.from_user, "language_code", None))
    q = (message.text or "").strip()
    if not q:
        await message.answer(_tr(lang, "Напиши вопрос текстом 🙂", "Напиши питання текстом 🙂", "Write your question as text 🙂"))
        return
    hits = await kb_search(session, user_id=int(user.id), q=q, limit=5)
    await state.clear()

    if not hits:
        await message.answer(_tr(lang, "Ничего не нашёл в KB по этому запросу.", "Нічого не знайдено у KB.", "Nothing found in KB for this query."), reply_markup=_kb_menu_kb(lang))
        return

    top = _tr(lang, "🔎 Нашёл в KB:", "🔎 Знайдено у KB:", "🔎 Found in KB:")
    lines = [top]
    for i, h in enumerate(hits, 1):
        title = f" — {h['title']}" if h.get("title") else ""
        lines.append(f"{i}) id={h['id']}{title}\n{h['content']}")
    await message.answer("\n\n".join(lines), reply_markup=_kb_menu_kb(lang))