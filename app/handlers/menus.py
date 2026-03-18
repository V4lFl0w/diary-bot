from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.handlers.assistant import _media_inline_kb
from app.keyboards import (
    get_journal_menu_kb,
    get_main_kb,
    get_media_menu_kb,
    get_premium_menu_kb,
    get_settings_menu_kb,
    is_back_btn,
    is_root_journal_btn,
    is_root_media_btn,
    is_root_premium_btn,
    is_root_proactive_btn,
    is_root_settings_btn,
    is_about_btn,
    is_root_profile_btn,
)
from app.models.user import User
from app.services.analytics_helpers import log_ui
from app.services.assistant import run_assistant

# 🔥 Импортируем НОВЫЕ функции лимитов (штуки)
from app.services.assistant import (
    _usage_last_24h,
    _quota_limits,
    _assistant_plan,
    _next_slot_eta_24h,
    _format_eta_short,
)
from app.services.daily_limits import get_daily_reset_eta_text, get_daily_limit, get_daily_used

router = Router(name="menus")


async def _get_user(session: AsyncSession, tg_id: int) -> User:
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user is None:
        user = User(tg_id=tg_id)
        session.add(user)
        await session.commit()
    return user


def _user_lang(user: User, tg_lang: Optional[str]) -> str:
    loc = (getattr(user, "locale", None) or getattr(user, "lang", None) or tg_lang or "ru").lower()
    if loc.startswith(("ua", "uk")):
        return "uk"
    if loc.startswith("en"):
        return "en"
    return "ru"


def _is_premium_user(user: User) -> bool:
    if not user:
        return False
    if bool(getattr(user, "has_premium", False) or getattr(user, "is_premium", False)):
        return True
    pu = getattr(user, "premium_until", None)
    if pu:
        try:
            now = datetime.now(timezone.utc)
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)
            return pu > now
        except Exception:
            return False
    return False


def _is_admin_user(user: User, tg_id: int) -> bool:
    if user and bool(getattr(user, "is_admin", False)):
        return True
    try:
        from app.handlers.admin import is_admin

        return bool(is_admin(tg_id, user))
    except Exception:
        return False


async def _log(
    session: AsyncSession,
    user: User,
    tg_lang: Optional[str],
    event: str,
    source: str,
) -> None:
    await log_ui(
        session,
        user=user,
        user_id=(user.id if user else None),
        event=event,
        source=source,
        tg_lang=tg_lang,
    )
    await session.commit()


@router.message(F.text.func(is_root_journal_btn))
async def open_journal_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "open_journal_menu", "menu")
    txt = {"ru": "📓 Журнал", "uk": "📓 Щоденник", "en": "📓 Journal"}.get(lang, "📓 Журнал")
    await m.answer(txt, reply_markup=get_journal_menu_kb(lang))


@router.message(F.text.func(is_root_media_btn))
async def open_media_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "open_media_menu", "menu")
    txt = {
        "ru": "🧘 Медиа\n\nВыберите раздел ниже 👇",
        "uk": "🧘 Медіа\n\nОберіть розділ нижче 👇",
        "en": "🧘 Media\n\nChoose a section below 👇",
    }.get(lang, "🧘 Медиа\n\nВыберите раздел ниже 👇")
    await m.answer(txt, reply_markup=get_media_menu_kb(lang))


@router.message(F.text.func(is_root_settings_btn))
async def open_settings_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "open_settings_menu", "menu")
    txt = {"ru": "⚙️ Настройки", "uk": "⚙️ Налаштування", "en": "⚙️ Settings"}.get(lang, "⚙️ Настройки")
    await m.answer(txt, reply_markup=get_settings_menu_kb(lang))


@router.message(F.text.func(is_about_btn))
async def open_about_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    await m.answer(
        "<b>Diary-Bot</b> — твой умный помощник.\n\n"
        "🎬 <i>This product uses the TMDB API but is not endorsed or certified by TMDB.</i>",
        parse_mode="HTML",
    )


@router.message(F.text.func(is_root_proactive_btn))
async def open_proactive_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "open_proactive_menu", "menu")

    from app.handlers.proactive import show_proactive_screen

    await show_proactive_screen(m, session, lang)


@router.message(F.text.func(is_root_premium_btn))
async def open_premium_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)
    is_premium = _is_premium_user(user)

    await _log(session, user, tg_lang, "premium_click", "menu")

    from aiogram.types import ReplyKeyboardRemove

    btn_txt = {"ru": "💎 Премиум", "uk": "💎 Преміум", "en": "💎 Premium"}.get(lang, "💎 Премиум")
    await m.answer(btn_txt, reply_markup=ReplyKeyboardRemove())
    await m.answer(btn_txt, reply_markup=get_premium_menu_kb(lang, is_premium=is_premium, tg_id=m.from_user.id))


@router.message(F.text.func(is_root_profile_btn))
async def open_profile_menu(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "open_profile_menu", "menu")

    def tr(ru: str, uk: str, en: str) -> str:
        if lang == "uk":
            return uk
        if lang == "en":
            return en
        return ru

    is_prem = _is_premium_user(user)
    plan = _assistant_plan(user)

    if plan in ["free", "basic"] and not is_prem:
        plan_name = tr("Базовый", "Базовий", "Basic")
    else:
        plan_name = str(plan).upper()

    if is_prem and plan in ["free", "basic"]:
        plan_name = "PREMIUM"

    status_icon = "💎" if is_prem else "🆓"

    pu = getattr(user, "premium_until", None)
    until_text = ""
    if pu and is_prem:
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        until_text = tr(
            f" (до {pu.strftime('%d.%m.%Y')})",
            f" (до {pu.strftime('%d.%m.%Y')})",
            f" (until {pu.strftime('%d.%m.%Y')})",
        )

    # ---------- DAILY LIMITS ----------
    eta_text = get_daily_reset_eta_text(user, lang)

    calories_used = await get_daily_used(session, user.id, "calories_text_daily", user)
    calories_total = get_daily_limit(user, "calories_text_daily")

    journal_used = await get_daily_used(session, user.id, "journal_entries_daily", user)
    journal_total = get_daily_limit(user, "journal_entries_daily")

    reminders_used = await get_daily_used(session, user.id, "reminders_daily", user)
    reminders_total = get_daily_limit(user, "reminders_daily")

    # ---------- AI LIMITS / 24H ----------
    ast_used = await _usage_last_24h(session, user.id, "assistant")
    ast_total = _quota_limits(plan, "assistant")

    vis_used = await _usage_last_24h(session, user.id, "vision")
    vis_total = _quota_limits(plan, "vision")

    web_used = await _usage_last_24h(session, user.id, "assistant_web")
    web_total = _quota_limits(plan, "assistant_web")

    async def slot_line(feature: str, used: int, total: int, kind: str) -> str:
        if kind == "text":
            return tr(
                "⏳ Следующий слот текста: ",
                "⏳ Наступний слот тексту: ",
                "⏳ Next text slot: ",
            ) + (
                tr("недоступно на текущем тарифе", "недоступно на поточному тарифі", "unavailable on current plan")
                if total <= 0
                else tr("уже доступен", "вже доступний", "already available")
                if used < total
                else _format_eta_short(await _next_slot_eta_24h(session, int(user.id), feature), lang)
            )

        if kind == "photo":
            return tr(
                "⏳ Следующий слот фото: ",
                "⏳ Наступний слот фото: ",
                "⏳ Next photo slot: ",
            ) + (
                tr("недоступно на текущем тарифе", "недоступно на поточному тарифі", "unavailable on current plan")
                if total <= 0
                else tr("уже доступен", "вже доступний", "already available")
                if used < total
                else _format_eta_short(await _next_slot_eta_24h(session, int(user.id), feature), lang)
            )

        return tr(
            "⏳ Следующий слот Web: ",
            "⏳ Наступний слот Web: ",
            "⏳ Next Web slot: ",
        ) + (
            tr("недоступно на текущем тарифе", "недоступно на поточному тарифі", "unavailable on current plan")
            if total <= 0
            else tr("уже доступен", "вже доступний", "already available")
            if used < total
            else _format_eta_short(await _next_slot_eta_24h(session, int(user.id), feature), lang)
        )

    text_slot_line = await slot_line("assistant", ast_used, ast_total, "text")
    photo_slot_line = await slot_line("vision", vis_used, vis_total, "photo")
    web_slot_line = await slot_line("assistant_web", web_used, web_total, "web")

    lines = [
        tr("👤 <b>Твой профиль</b>", "👤 <b>Твій профіль</b>", "👤 <b>Your profile</b>"),
        f"ID: <code>{m.from_user.id}</code>",
        tr("Тариф", "Тариф", "Plan") + f": {status_icon} <b>{plan_name}</b>{until_text}",
        "",
        tr(
            f"🔄 Дневные лимиты обновятся через: {eta_text}",
            f"🔄 Денний ліміт оновиться через: {eta_text}",
            f"🔄 Daily limits reset in: {eta_text}",
        ),
        f"🔥 {tr('Калории', 'Калорії', 'Calories')}: {calories_used}/{calories_total}",
        f"📒 {tr('Дневник', 'Щоденник', 'Journal')}: {journal_used}/{journal_total}",
        f"⏰ {tr('Напоминания', 'Нагадування', 'Reminders')}: {reminders_used}/{reminders_total}",
        "",
        tr(
            "🤖 <b>ИИ за последние 24 часа:</b>",
            "🤖 <b>ШІ за останні 24 години:</b>",
            "🤖 <b>AI in the last 24 hours:</b>",
        ),
        f"💬 {tr('Текст', 'Текст', 'Text')}: {ast_used}/{ast_total}",
        text_slot_line,
        f"📸 {tr('Фото', 'Фото', 'Photo')}: {vis_used}/{vis_total}",
        photo_slot_line,
        f"🌐 Web: {web_used}/{web_total}",
        web_slot_line,
    ]

    await m.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data == "menu:home")
async def cb_back_to_main(call: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()

    user = await _get_user(session, call.from_user.id)
    lang = _user_lang(user, getattr(call.from_user, "language_code", None))

    txt = {"ru": "🏠 Главное меню", "uk": "🏠 Головне меню", "en": "🏠 Main menu"}.get(lang, "🏠 Главное меню")
    await call.message.answer(
        txt,
        reply_markup=get_main_kb(
            lang,
            is_premium=_is_premium_user(user),
            is_admin=_is_admin_user(user, call.from_user.id),
        ),
    )

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await call.answer()


@router.message(F.text.func(is_back_btn))
async def back_to_main(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    tg_lang = getattr(m.from_user, "language_code", None)
    lang = _user_lang(user, tg_lang)

    await _log(session, user, tg_lang, "back_to_main", "button")

    txt = {"ru": "🏠 Главное меню", "uk": "🏠 Головне меню", "en": "🏠 Main menu"}.get(lang, "🏠 Главное меню")
    await m.answer(
        txt,
        reply_markup=get_main_kb(
            lang,
            is_premium=_is_premium_user(user),
            is_admin=_is_admin_user(user, m.from_user.id),
        ),
    )


@router.message(F.text & ~F.text.startswith("/"))
async def media_mode_text_router(message: Message, session: AsyncSession, state: FSMContext):
    st = None
    try:
        st = await state.get_state()
    except Exception:
        st = None

    if st:
        raise SkipHandler()

    if not getattr(message, "from_user", None):
        raise SkipHandler()

    user = await session.scalar(select(User).where(User.tg_id == message.from_user.id))
    if not user or getattr(user, "assistant_mode", None) != "media":
        raise SkipHandler()

    try:
        now = datetime.now(timezone.utc)
    except Exception:
        now = datetime.utcnow().replace(tzinfo=timezone.utc)

    until = getattr(user, "assistant_mode_until", None)
    if until is not None and until <= now:
        raise SkipHandler()

    lang = getattr(user, "lang", None) or "ru"
    text = (message.text or "").strip()
    if not text:
        raise SkipHandler()

    reply = await run_assistant(user, text, lang, session=session)
    if reply:
        clean = reply.replace("\nКнопки: ✅ Это оно / 🔁 Другие варианты / 🧩 Уточнить", "")
        await message.answer(clean, reply_markup=_media_inline_kb(lang), parse_mode=None)


__all__ = ["router"]
