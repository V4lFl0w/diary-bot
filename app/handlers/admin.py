# app/handlers/admin.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Tuple, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select, update
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.llm_usage import LLMUsage
from app.models.subscription import Subscription
from app.models.user import User
from app.services.admin_audit import log_admin_action
from app.services.subscriptions import (
    get_current_subscription,
    sync_user_premium_flags,
    utcnow,
)  

from app.utils.aiogram_guards import cb_reply

try:
    from app.models.event import AnalyticsEvent
except Exception: 
    AnalyticsEvent = None  # type: ignore

router = Router(name="admin")

SUPPORTED = {"ru", "uk", "en"}

TXT: Dict[str, Dict[str, str]] = {
    "title": {
        "ru": "🛡 Админ-панель",
        "uk": "🛡 Адмін-панель",
        "en": "🛡 Admin panel",
    },
    "list": {
        "ru": (
            "• Premium 24h себе\n"
            "• Premium пользователю по TG ID\n"
            "• Reset Premium по TG ID\n"
            "• Analytics (7d) — топ действий + active users\n"
            "• Users (7d active) — список активных\n"
            "• Find user — карточка по TG ID\n"
            "• Ban/Unban — по TG ID (если поле бана есть в модели)"
        ),
        "uk": (
            "• Premium 24h собі\n"
            "• Premium користувачу за TG ID\n"
            "• Reset Premium за TG ID\n"
            "• Analytics (7d) — топ дій + active users\n"
            "• Users (7d active) — список активних\n"
            "• Find user — картка за TG ID\n"
            "• Ban/Unban — за TG ID (якщо поле бану є в моделі)"
        ),
        "en": (
            "• Premium 24h for me\n"
            "• Premium to user by TG ID\n"
            "• Reset Premium by TG ID\n"
            "• Analytics (7d) — top actions + active users\n"
            "• Users (7d active) — active list\n"
            "• Find user — card by TG ID\n"
            "• Ban/Unban — by TG ID (if ban field exists in model)"
        ),
    },
    "btn_self": {
        "ru": "⭐ Выдать Premium себе (24h)",
        "uk": "⭐ Видати Premium собі (24h)",
        "en": "⭐ Give me Premium (24h)",
    },
    "btn_give": {
        "ru": "🎁 Выдать Premium по TG ID",
        "uk": "🎁 Видати Premium за TG ID",
        "en": "🎁 Give Premium by TG ID",
    },
    "btn_reset": {
        "ru": "🧹 Reset Premium по TG ID",
        "uk": "🧹 Reset Premium за TG ID",
        "en": "🧹 Reset Premium by TG ID",
    },
    "btn_analytics": {
        "ru": "📊 Analytics (7d)",
        "uk": "📊 Analytics (7d)",
        "en": "📊 Analytics (7d)",
    },
    "btn_users": {
        "ru": "👥 Users (7d active)",
        "uk": "👥 Users (7d active)",
        "en": "👥 Users (7d active)",
    },
    "btn_users_all": {
        "ru": "👥 Users (ALL)",
        "uk": "👥 Users (ALL)",
        "en": "👥 Users (ALL)",
    },
    "btn_find_user": {
        "ru": "🔎 Найти юзера по TG ID",
        "uk": "🔎 Знайти юзера за TG ID",
        "en": "🔎 Find user by TG ID",
    },
    "btn_ban": {
        "ru": "⛔️ Забанить по TG ID",
        "uk": "⛔️ Забанити за TG ID",
        "en": "⛔️ Ban by TG ID",
    },
    "btn_unban": {
        "ru": "✅ Разбанить по TG ID",
        "uk": "✅ Розбанити за TG ID",
        "en": "✅ Unban by TG ID",
    },
    "ask_id_give": {
        "ru": "Введи Telegram ID (tg_id) пользователя, кому выдать Premium:",
        "uk": "Введи Telegram ID (tg_id) користувача, кому видати Premium:",
        "en": "Send Telegram ID (tg_id) to grant Premium:",
    },
    "ask_id_reset": {
        "ru": "Введи Telegram ID (tg_id) пользователя, кому сбросить Premium:",
        "uk": "Введи Telegram ID (tg_id) користувача, кому скинути Premium:",
        "en": "Send Telegram ID (tg_id) to reset Premium:",
    },
    "ask_id_find": {
        "ru": "Введи Telegram ID (tg_id), чтобы показать карточку пользователя:",
        "uk": "Введи Telegram ID (tg_id), щоб показати картку користувача:",
        "en": "Send Telegram ID (tg_id) to show user card:",
    },
    "ask_id_ban": {
        "ru": "Введи Telegram ID (tg_id), чтобы забанить пользователя:",
        "uk": "Введи Telegram ID (tg_id), щоб забанити користувача:",
        "en": "Send Telegram ID (tg_id) to ban user:",
    },
    "ask_id_unban": {
        "ru": "Введи Telegram ID (tg_id), чтобы разбанить пользователя:",
        "uk": "Введи Telegram ID (tg_id), щоб розбанити користувача:",
        "en": "Send Telegram ID (tg_id) to unban user:",
    },
    "bad_id": {
        "ru": "Не похоже на ID. Пришли число.",
        "uk": "Це не схоже на ID. Надішли число.",
        "en": "That doesn't look like an ID. Send a number.",
    },
    "not_admin": {
        "ru": "Недоступно.",
        "uk": "Недоступно.",
        "en": "Not available.",
    },
    "done_self": {
        "ru": "Готово ✅ Premium активен на 24h.",
        "uk": "Готово ✅ Premium активний на 24h.",
        "en": "Done ✅ Premium is active for 24h.",
    },
    "done_user": {
        "ru": "Готово ✅ Premium выдан пользователю.",
        "uk": "Готово ✅ Premium видано користувачу.",
        "en": "Done ✅ Premium granted to the user.",
    },
    "done_reset": {
        "ru": "Готово ✅ Premium сброшен.",
        "uk": "Готово ✅ Premium скинуто.",
        "en": "Done ✅ Premium reset.",
    },
    "user_not_found": {
        "ru": "Пользователь не найден в базе. Пусть нажмёт /start.",
        "uk": "Користувача не знайдено в базі. Нехай натисне /start.",
        "en": "User not found in DB. Ask them to press /start.",
    },
    "analytics_empty": {
        "ru": "Событий за 7 дней пока нет.",
        "uk": "Подій за 7 днів поки немає.",
        "en": "No events for the last 7 days yet.",
    },
    "analytics_title": {
        "ru": "📊 Analytics за 7 дней:",
        "uk": "📊 Analytics за 7 днів:",
        "en": "📊 Analytics for 7 days:",
    },
    "users_empty": {
        "ru": "За 7 дней активных пользователей пока нет.",
        "uk": "За 7 днів активних користувачів поки немає.",
        "en": "No active users for last 7 days yet.",
    },
    "user_card_title": {
        "ru": "👤 Карточка пользователя",
        "uk": "👤 Картка користувача",
        "en": "👤 User card",
    },
    "ban_done": {
        "ru": "⛔️ Готово. Пользователь забанен.",
        "uk": "⛔️ Готово. Користувача забанено.",
        "en": "⛔️ Done. User banned.",
    },
    "unban_done": {
        "ru": "✅ Готово. Пользователь разбанен.",
        "uk": "✅ Готово. Користувача розбанено.",
        "en": "✅ Done. User unbanned.",
    },
    "ban_unavailable": {
        "ru": "Поле бана не найдено в модели User (нужно is_banned или banned_until).",
        "uk": "Поле бану не знайдено в моделі User (потрібно is_banned або banned_until).",
        "en": "Ban field not found in User model (need is_banned or banned_until).",
    },
}


# -------------------- i18n & formatters --------------------

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
    lang2 = _normalize_lang(lang)
    block = TXT.get(key, {})
    return block.get(lang2) or block.get("ru") or key

def _is_really_premium(prem_until_dt: datetime | None, now_dt: datetime) -> bool:
    """Жесткая проверка: если время вышло, премиума нет, даже если флаг застрял."""
    if not prem_until_dt:
        return False
    if getattr(prem_until_dt, "tzinfo", None) is None:
        prem_until_dt = prem_until_dt.replace(tzinfo=timezone.utc)
    return prem_until_dt > now_dt

def _format_dt(dt: datetime | None, fmt: str = "%d.%m.%Y") -> str:
    if not dt:
        return "-"
    return dt.strftime(fmt)

# -------------------- admin menu helper --------------------

def is_admin_btn(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {
        "🛡 админ",
        "🛡 адмін",
        "🛡 admin",
        "admin",
        "админ",
        "адмін",
    }


def _is_admin_by_settings(tg_id: int) -> bool:
    try:
        return bool(getattr(settings, "bot_admin_tg_id", None)) and int(settings.bot_admin_tg_id) == int(tg_id)
    except Exception:
        return False


def _is_admin_by_env(tg_id: int) -> bool:
    raw = os.getenv("ADMIN_IDS", "")
    if not raw:
        return False
    try:
        ids = {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
        return tg_id in ids
    except Exception:
        return False


def is_admin(tg_id: int, user: User | None = None) -> bool:
    if user is not None and bool(getattr(user, "is_admin", False)):
        return True
    if _is_admin_by_settings(tg_id):
        return True
    if _is_admin_by_env(tg_id):
        return True
    return False


def is_admin_tg(tg_id: int) -> bool:
    return is_admin(tg_id)


async def _get_user(session: AsyncSession, tg_id: int) -> User | None:
    q = select(User).where(User.tg_id == tg_id).execution_options(populate_existing=True)
    res = await session.execute(q)
    return res.scalars().one_or_none()


def _user_lang(user: User | None, tg_lang: Optional[str]) -> str:
    raw = (
        (getattr(user, "locale", None) if user else None)
        or (getattr(user, "lang", None) if user else None)
        or tg_lang
        or getattr(settings, "default_locale", None)
        or "ru"
    )
    return _normalize_lang(str(raw))


def _admin_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=_tr(lang, "btn_self"), callback_data="admin:premium_self")],
            [
                InlineKeyboardButton(text=_tr(lang, "btn_give"), callback_data="admin:premium_user"),
                InlineKeyboardButton(text=_tr(lang, "btn_reset"), callback_data="admin:premium_reset"),
            ],
            [InlineKeyboardButton(text=_tr(lang, "btn_analytics"), callback_data="admin:analytics_7d")],
            [
                InlineKeyboardButton(text=_tr(lang, "btn_users"), callback_data="admin:users_7d"),
                InlineKeyboardButton(text=_tr(lang, "btn_users_all"), callback_data="admin:users_all"),
            ],
            [
                InlineKeyboardButton(text=_tr(lang, "btn_find_user"), callback_data="admin:user_find"),
            ],
            [
                InlineKeyboardButton(text=_tr(lang, "btn_ban"), callback_data="admin:ban"),
                InlineKeyboardButton(text=_tr(lang, "btn_unban"), callback_data="admin:unban"),
            ],
        ]
    )


class AdminStates(StatesGroup):
    wait_give_id = State()
    wait_reset_id = State()
    wait_find_id = State()
    wait_ban_id = State()
    wait_unban_id = State()


SYSTEM_EVENTS = {
    "test_event",
    "user_start",
    "user_new",
}

VALUE_EVENTS = {
    "journal_add",
    "assistant_question",
    "premium_click",
}


def _is_system_event(name: str) -> bool:
    n = (name or "").strip().lower()
    return (n in SYSTEM_EVENTS) or n.startswith(("test_", "system_"))


def _take_top(rows: Iterable[Tuple[str, int]], allowed: set[str], limit: int = 3) -> list[Tuple[str, int]]:
    out: list[Tuple[str, int]] = []
    for e, c in rows:
        if e in allowed:
            out.append((e, c))
        if len(out) >= limit:
            break
    return out


CB_GIVE_TIER = "give_tier:"  

def _kb_give_tier(lang: str, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💎 BASIC", callback_data=f"{CB_GIVE_TIER}{user_id}:basic"),
                InlineKeyboardButton(text="👑 PRO", callback_data=f"{CB_GIVE_TIER}{user_id}:pro"),
            ]
        ]
    )


def _apply_premium(user: User, hours: int = 24) -> None:
    now = datetime.now(timezone.utc)
    until = now + timedelta(hours=hours)

    if hasattr(user, "is_premium"):
        try:
            user.is_premium = True  
        except Exception:
            pass

    if hasattr(user, "premium_until"):
        try:
            user.premium_until = until  
        except Exception:
            pass


def _reset_premium(user: User) -> None:
    try:
        setattr(user, "is_premium", False)
    except Exception:
        pass

    try:
        setattr(user, "premium_until", None)
    except Exception:
        pass

    try:
        setattr(user, "premium_plan", "basic")
    except Exception:
        pass

    if hasattr(user, "premium_until"):
        try:
            user.premium_until = None  
        except Exception:
            pass


def _ban_supported(user: User) -> bool:
    return hasattr(user, "is_banned") or hasattr(user, "banned_until")


def _set_ban(user: User, banned: bool) -> bool:
    ok = False
    if hasattr(user, "is_banned"):
        try:
            user.is_banned = bool(banned)  
            ok = True
        except Exception:
            pass

    if hasattr(user, "banned_until"):
        try:
            if banned:
                user.banned_until = datetime.now(timezone.utc) + timedelta(days=3650)  
            else:
                user.banned_until = None  
            ok = True
        except Exception:
            pass
    return ok


def _is_banned(user: User) -> bool:
    if hasattr(user, "is_banned"):
        try:
            return bool(getattr(user, "is_banned"))
        except Exception:
            pass
    if hasattr(user, "banned_until"):
        bu = getattr(user, "banned_until", None)
        if bu:
            try:
                now = datetime.now(timezone.utc)
                if getattr(bu, "tzinfo", None) is None:
                    bu = bu.replace(tzinfo=timezone.utc)
                return bu > now
            except Exception:
                return False
    return False


async def _show_admin_panel(m: Message, session: AsyncSession, state: FSMContext) -> None:
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    if not is_admin(m.from_user.id, user):
        return

    await state.clear()

    lang = _user_lang(user, getattr(m.from_user, "language_code", None))
    text = f"{_tr(lang, 'title')}\n\n{_tr(lang, 'list')}"
    await m.answer(text, reply_markup=_admin_kb(lang))


@router.message(Command("admin"))
async def cmd_admin(m: Message, session: AsyncSession, state: FSMContext) -> None:
    await _show_admin_panel(m, session, state)


@router.message(F.text.func(is_admin_btn))
async def admin_btn_open(m: Message, session: AsyncSession, state: FSMContext) -> None:
    await _show_admin_panel(m, session, state)


@router.callback_query(F.data.startswith("admin:"))
async def on_admin_cb(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    me = await _get_user(session, c.from_user.id)
    if not is_admin(c.from_user.id, me):
        try:
            await c.answer(_tr("ru", "not_admin"), show_alert=True)
        except TelegramBadRequest:
            pass
        return

    try:
        await c.answer()
    except TelegramBadRequest:
        pass

    lang = _user_lang(me, getattr(c.from_user, "language_code", None))
    action = (c.data or "").split("admin:", 1)[1].strip()

    # --- give self ---
    if action == "premium_self":
        if not me:
            me = User(tg_id=c.from_user.id, locale=lang, lang=lang)
            session.add(me)
            await session.flush()

        _apply_premium(me, hours=24)
        session.add(me)
        await session.commit()

        await log_admin_action(
            session,
            admin_tg_id=c.from_user.id,
            action="premium_self",
            target_tg_id=c.from_user.id,
        )

        if c.message:
            await cb_reply(c, _tr(lang, "done_self"))
        return

    # --- give user ---
    if action == "premium_user":
        await state.set_state(AdminStates.wait_give_id)
        if c.message:
            await cb_reply(c, _tr(lang, "ask_id_give"))
        return

    # --- reset user ---
    if action == "premium_reset":
        await state.set_state(AdminStates.wait_reset_id)
        if c.message:
            await cb_reply(c, _tr(lang, "ask_id_reset"))
        return

    # --- analytics ---
    if action == "analytics_7d":
        since = datetime.now(timezone.utc) - timedelta(days=7)

        if AnalyticsEvent is not None:
            raw_rows = (
                await session.execute(
                    select(AnalyticsEvent.event, func.count(AnalyticsEvent.id))
                    .where(AnalyticsEvent.ts >= since)
                    .group_by(AnalyticsEvent.event)
                    .order_by(func.count(AnalyticsEvent.id).desc())
                )
            ).all()

            raw_rows = [tuple(r) for r in raw_rows]
            active_users = (
                await session.execute(
                    select(func.count(func.distinct(AnalyticsEvent.user_id)))
                    .where(AnalyticsEvent.ts >= since)
                    .where(AnalyticsEvent.user_id.is_not(None))
                )
            ).scalar_one()
        else:
            raw_rows = (
                await session.execute(
                    sql_text(
                        "SELECT event, COUNT(*) as cnt "
                        "FROM analytics_events "
                        "WHERE ts >= :since "
                        "GROUP BY event "
                        "ORDER BY cnt DESC"
                    ),
                    {"since": since.isoformat()},
                )
            ).all()

            raw_rows = [tuple(r) for r in raw_rows]
            active_users = (
                await session.execute(
                    sql_text(
                        "SELECT COUNT(DISTINCT user_id) "
                        "FROM analytics_events "
                        "WHERE ts >= :since AND user_id IS NOT NULL"
                    ),
                    {"since": since.isoformat()},
                )
            ).scalar_one()

        rows = [(str(e), int(cnt)) for (e, cnt) in raw_rows if not _is_system_event(str(e))]

        if not rows:
            if c.message:
                await cb_reply(c, _tr(lang, "analytics_empty"))
            return

        top_value = _take_top(rows, VALUE_EVENTS, limit=3)
        if not top_value:
            top_value = rows[:3]

        rest = [(e, cnt) for (e, cnt) in rows if (e, cnt) not in top_value][:10]

        lines = [
            f"<b>{_tr(lang, 'analytics_title')}</b>",
            f"👥 Активных пользователей: <b>{int(active_users or 0)}</b>",
            "",
            "🏆 <b>Топ-3:</b>",
            *[f"• {event}: {cnt}" for event, cnt in top_value],
        ]
        if rest:
            lines += ["", "🧾 <b>Остальное:</b>"]
            lines += [f"• {event}: {cnt}" for event, cnt in rest]

        # --- Trial (7d) ---
        try:
            has_events = (
                await session.execute(
                    sql_text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events' LIMIT 1;")
                )
            ).scalar_one_or_none()

            if has_events:
                rows_trial = (
                    await session.execute(
                        sql_text(
                            "SELECT name, COUNT(*) AS cnt "
                            "FROM events "
                            "WHERE created_at >= datetime('now','-7 day') "
                            "  AND name IN ('trial_click','trial_granted','trial_denied') "
                            "GROUP BY name;"
                        )
                    )
                ).all()

                mp = {str(n): int(c) for (n, c) in rows_trial}

                lines += [
                    "",
                    "🎁 <b>Trial (7d):</b>",
                    f"• trial_click: {mp.get('trial_click', 0)}",
                    f"• trial_granted: {mp.get('trial_granted', 0)}",
                    f"• trial_denied: {mp.get('trial_denied', 0)}",
                ]
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass

        # --- LLM usage (7d) ---
        try:
            since_llm = datetime.utcnow() - timedelta(days=7)
            q = select(
                func.count(LLMUsage.id),
                func.coalesce(func.sum(LLMUsage.total_tokens), 0),
                func.coalesce(func.sum(LLMUsage.input_tokens), 0),
                func.coalesce(func.sum(LLMUsage.output_tokens), 0),
                func.coalesce(func.sum(LLMUsage.cost_usd_micros), 0),
            ).where(LLMUsage.created_at >= since_llm)

            n, total, inp, out, cost = (await session.execute(q)).one()

            lines += [
                "",
                "🧠 <b>LLM usage (7d):</b>",
                f"• requests: {int(n or 0)}",
                f"• tokens: {int(total or 0)} (in {int(inp or 0)} / out {int(out or 0)})",
                f"• cost: ${float(cost or 0) / 1_000_000:.4f}",
            ]

            q2 = (
                select(
                    LLMUsage.feature,
                    LLMUsage.model,
                    func.count(LLMUsage.id).label("req"),
                    func.coalesce(func.sum(LLMUsage.total_tokens), 0).label("tok"),
                    func.coalesce(func.sum(LLMUsage.cost_usd_micros), 0).label("c"),
                )
                .where(LLMUsage.created_at >= since_llm)
                .group_by(LLMUsage.feature, LLMUsage.model)
                .order_by(func.sum(LLMUsage.total_tokens).desc())
                .limit(8)
            )
            top = (await session.execute(q2)).all()
            if top:
                lines += ["", "<b>Топ LLM (feature:model):</b>"]
                for feature, model, req, tok, cost in top:
                    lines.append(
                        f"• {feature}:{model} — {int(req)} req | {int(tok)} tok | ${float(cost or 0) / 1_000_000:.4f}"
                    )
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass
            lines += ["", "🧠 LLM usage (7d): (нет данных)"]

        if c.message:
            try:
                await c.message.edit_text("\n".join(lines), parse_mode="HTML")
            except TelegramBadRequest:
                pass
        return

    # --- users all (latest) ---
    if action == "users_all":
        rows = (
            await session.execute(
                select(
                    User.tg_id,
                    User.id,
                    User.username,
                    User.locale,
                    User.lang,
                    User.last_seen_at,
                    User.is_premium,
                    User.premium_until,
                    User.premium_plan,
                )
                .order_by(User.id.desc())
                .limit(60)
            )
        ).all()

        rows = [tuple(r) for r in rows]
        if not rows:
            if c.message:
                await cb_reply(c, "Users: empty")
            return

        now = datetime.now(timezone.utc)
        lines = ["👥 <b>Users (ALL - latest 60):</b>\n"]
        for (
            tg_id,
            uid,
            username,
            loc,
            langx,
            last_seen_at,
            is_prem,
            prem_until,
            prem_plan,
        ) in rows:
            
            real_prem = _is_really_premium(prem_until, now)
            
            icon = "💎" if real_prem else "👤"
            plan_str = str(prem_plan).upper() if real_prem and prem_plan else "FREE"
            date_str = _format_dt(prem_until)
            seen_str = _format_dt(last_seen_at, "%d.%m %H:%M")
            uname = f"@{username}" if username else "NoUser"
            
            link = f"<a href='tg://user?id={tg_id}'>{tg_id}</a>"
            
            lines.append(f"{icon} {link} | {uname} | <b>{plan_str}</b> | До: {date_str} | Был: {seen_str}")

        if c.message:
            try:
                await c.message.edit_text("\n".join(lines), parse_mode="HTML")
            except TelegramBadRequest:
                pass
        return

    # --- users active 7d ---
    if action == "users_7d":
        since = datetime.now(timezone.utc) - timedelta(days=7)

        if AnalyticsEvent is None:
            rows = (
                await session.execute(
                    sql_text(
                        "SELECT u.tg_id, u.id, u.username, u.locale, u.lang, "
                        "MAX(e.ts) as last_ts, COUNT(*) as cnt, u.is_premium, u.premium_until, u.premium_plan "
                        "FROM analytics_events e "
                        "JOIN users u ON u.id = e.user_id "
                        "WHERE e.ts >= :since AND e.user_id IS NOT NULL "
                        "GROUP BY u.tg_id, u.id, u.username, u.locale, u.lang, u.last_seen_at, u.is_premium, u.premium_until, u.premium_plan "
                        "ORDER BY last_ts DESC "
                        "LIMIT 40"
                    ),
                    {"since": since.isoformat()},
                )
            ).all()
            
            parsed_rows = []
            for r in rows:
                parsed_rows.append((r[0], r[1], r[2], r[3], r[4], None, r[7], r[8], r[9], r[5], r[6]))
            rows = parsed_rows
        else:
            rows = (
                await session.execute(
                    select(
                        User.tg_id,
                        User.id,
                        User.username,
                        User.locale,
                        User.lang,
                        User.last_seen_at,
                        User.is_premium,
                        User.premium_until,
                        User.premium_plan,
                        func.max(AnalyticsEvent.ts).label("last_ts"),
                        func.count(AnalyticsEvent.id).label("cnt"),
                    )
                    .join(AnalyticsEvent, AnalyticsEvent.user_id == User.id)
                    .where(AnalyticsEvent.ts >= since)
                    .where(AnalyticsEvent.user_id.is_not(None))
                    .group_by(
                        User.tg_id,
                        User.id,
                        User.username,
                        User.locale,
                        User.lang,
                        User.last_seen_at,
                        User.is_premium,
                        User.premium_until,
                        User.premium_plan,
                    )
                    .order_by(func.max(AnalyticsEvent.ts).desc())
                    .limit(40)
                )
            ).all()
            rows = [tuple(r) for r in rows]

        if not rows:
            if c.message:
                await cb_reply(c, _tr(lang, "users_empty"))
            return

        now = datetime.now(timezone.utc)
        lines = ["👥 <b>Active users (7d):</b>\n"]
        
        for (tg_id, uid, username, loc, langx, last_seen_at, is_prem, prem_until, prem_plan, last_ts, cnt) in rows:
            real_prem = _is_really_premium(prem_until, now)
            
            icon = "💎" if real_prem else "👤"
            plan_str = str(prem_plan).upper() if real_prem and prem_plan else "FREE"
            date_str = _format_dt(prem_until)
            uname = f"@{username}" if username else "NoUser"
            link = f"<a href='tg://user?id={tg_id}'>{tg_id}</a>"
            
            lines.append(f"{icon} {link} | {uname} | <b>{plan_str}</b> | До: {date_str} | Событий: {cnt}")

        if c.message:
            try:
                await c.message.edit_text("\n".join(lines), parse_mode="HTML")
            except TelegramBadRequest:
                pass
        return

    # --- find user card ---
    if action == "user_find":
        await state.set_state(AdminStates.wait_find_id)
        if c.message:
            await cb_reply(c, _tr(lang, "ask_id_find"))
        return

    # --- ban/unban ---
    if action == "ban":
        await state.set_state(AdminStates.wait_ban_id)
        if c.message:
            await cb_reply(c, _tr(lang, "ask_id_ban"))
        return

    if action == "unban":
        await state.set_state(AdminStates.wait_unban_id)
        if c.message:
            await cb_reply(c, _tr(lang, "ask_id_unban"))
        return


# -------------------- FSM steps --------------------


@router.message(AdminStates.wait_give_id)
async def on_give_id(m: Message, session: AsyncSession, state: FSMContext) -> None:
    me = await _get_user(session, m.from_user.id)
    if not is_admin(m.from_user.id, me):
        await state.clear()
        return

    lang = _user_lang(me, getattr(m.from_user, "language_code", None))

    try:
        tg_id = int((m.text or "").strip())
    except Exception:
        await m.answer(_tr(lang, "bad_id"))
        return

    user = await _get_user(session, tg_id)
    if not user:
        await m.answer(_tr(lang, "user_not_found"))
        await state.clear()
        return
    await m.answer("Выбери, какой Premium выдать:", reply_markup=_kb_give_tier(lang, user.id))
    return


@router.callback_query(F.data.startswith(CB_GIVE_TIER))
async def on_give_tier(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    me = await _get_user(session, c.from_user.id)
    if not is_admin(c.from_user.id, me):
        await state.clear()
        await c.answer("not allowed", show_alert=True)
        return

    lang = _user_lang(me, getattr(c.from_user, "language_code", None))

    raw = (c.data or "")[len(CB_GIVE_TIER) :]
    try:
        user_id_str, tier = raw.split(":", 1)
        user_id = int(user_id_str)
        tier = (tier or "").strip().lower()
    except Exception:
        await c.answer("bad payload", show_alert=True)
        return

    if tier not in {"basic", "pro"}:
        await c.answer("bad tier", show_alert=True)
        return

    user = await session.get(User, user_id)
    if not user:
        await c.answer(_tr(lang, "user_not_found"), show_alert=True)
        return

    now = utcnow()
    existing_sub = await get_current_subscription(session, user.id, now=now)

    if existing_sub:
        existing_sub.status = "active"
        base_from = existing_sub.expires_at or now
        if base_from < now:
            base_from = now
        existing_sub.expires_at = base_from + timedelta(days=1)  # 24h
        existing_sub.auto_renew = False
        existing_sub.plan = tier  
        existing_sub.source = "admin"
        session.add(existing_sub)
    else:
        sub = Subscription(
            user_id=user.id,
            plan=tier,  
            status="active",
            started_at=now,
            expires_at=now + timedelta(days=1),
            auto_renew=False,
            source="admin",
        )
        session.add(sub)
        await session.flush()

    await sync_user_premium_flags(session, user, now=now)
    await session.commit()

    try:
        await log_admin_action(
            session,
            admin_tg_id=c.from_user.id,
            action=f"premium_user_{tier}",
            target_tg_id=getattr(user, "tg_id", None) or 0,
        )
    except Exception:
        pass

    await cb_reply(c, f"Done ✅ Premium {tier.upper()} granted to the user.")
    await c.answer()
    await state.clear()


@router.message(AdminStates.wait_reset_id)
async def on_reset_id(m: Message, session: AsyncSession, state: FSMContext) -> None:
    try:
        await session.rollback()
    except Exception:
        pass

    me = await _get_user(session, m.from_user.id)
    if not is_admin(m.from_user.id, me):
        await state.clear()
        return

    lang = _user_lang(me, getattr(m.from_user, "language_code", None))

    try:
        tg_id = int((m.text or "").strip())
    except Exception:
        await m.answer(_tr(lang, "bad_id"))
        return

    user = await _get_user(session, tg_id)
    if not user:
        await m.answer(_tr(lang, "user_not_found"))
        await state.clear()
        return

    await session.execute(
        update(User).where(User.id == user.id).values(is_premium=False, premium_until=None, premium_plan="basic")
    )

    try:
        await session.execute(
            sql_text("UPDATE subscriptions SET status='expired' WHERE user_id = :uid AND status = 'active'"),
            {"uid": int(user.id)},
        )
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
        await session.execute(
            update(User).where(User.id == user.id).values(is_premium=False, premium_until=None, premium_plan="basic")
        )

    try:
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass
        raise

    await m.answer(_tr(lang, "done_reset"))
    await state.clear()


@router.message(AdminStates.wait_find_id)
async def on_find_id(m: Message, session: AsyncSession, state: FSMContext) -> None:
    me = await _get_user(session, m.from_user.id)
    if not is_admin(m.from_user.id, me):
        await state.clear()
        return

    lang = _user_lang(me, getattr(m.from_user, "language_code", None))

    try:
        tg_id = int((m.text or "").strip())
    except Exception:
        await m.answer(_tr(lang, "bad_id"))
        return

    u = await _get_user(session, tg_id)
    if not u:
        await m.answer(_tr(lang, "user_not_found"))
        await state.clear()
        return

    now = datetime.now(timezone.utc)
    
    # Жесткая проверка реального статуса
    real_prem = _is_really_premium(getattr(u, 'premium_until', None), now)
    
    plan_val = getattr(u, 'premium_plan', getattr(u, 'plan', 'basic'))
    plan_str = str(plan_val).upper() if real_prem else "FREE"
    
    date_str = _format_dt(getattr(u, "premium_until", None), "%d.%m.%Y %H:%M")
    seen_str = _format_dt(getattr(u, "last_seen_at", None), "%d.%m.%Y %H:%M")
    
    uname = getattr(u, 'username', None)
    uname_str = f"@{uname}" if uname else "Нет"
    
    link = f"<a href='tg://user?id={tg_id}'>{tg_id}</a>"

    text = (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"<b>ID:</b> {link}\n"
        f"<b>Username:</b> {uname_str}\n"
        f"<b>Статус:</b> {'💎 Premium' if real_prem else '👤 Free'} (<b>{plan_str}</b>)\n"
        f"<b>Премиум до:</b> {date_str}\n"
        f"<b>Локаль:</b> {getattr(u, 'locale', '-')}\n"
        f"<b>Был в сети:</b> {seen_str}\n"
        f"<b>Бан:</b> {'⛔️ Да' if _is_banned(u) else '✅ Нет'}"
    )

    await m.answer(text, parse_mode="HTML")
    await state.clear()


@router.message(AdminStates.wait_ban_id)
async def on_ban_id(m: Message, session: AsyncSession, state: FSMContext) -> None:
    me = await _get_user(session, m.from_user.id)
    if not is_admin(m.from_user.id, me):
        await state.clear()
        return

    lang = _user_lang(me, getattr(m.from_user, "language_code", None))

    try:
        tg_id = int((m.text or "").strip())
    except Exception:
        await m.answer(_tr(lang, "bad_id"))
        return

    u = await _get_user(session, tg_id)
    if not u:
        await m.answer(_tr(lang, "user_not_found"))
        await state.clear()
        return

    if not _ban_supported(u):
        await m.answer(_tr(lang, "ban_unavailable"))
        await state.clear()
        return

    ok = _set_ban(u, True)
    if not ok:
        await m.answer(_tr(lang, "ban_unavailable"))
        await state.clear()
        return

    session.add(u)
    await session.commit()
    await session.refresh(u)

    await log_admin_action(
        session,
        admin_tg_id=m.from_user.id,
        action="ban",
        target_tg_id=tg_id,
    )

    await m.answer(_tr(lang, "ban_done"))
    await state.clear()


@router.message(AdminStates.wait_unban_id)
async def on_unban_id(m: Message, session: AsyncSession, state: FSMContext) -> None:
    me = await _get_user(session, m.from_user.id)
    if not is_admin(m.from_user.id, me):
        await state.clear()
        return

    lang = _user_lang(me, getattr(m.from_user, "language_code", None))

    try:
        tg_id = int((m.text or "").strip())
    except Exception:
        await m.answer(_tr(lang, "bad_id"))
        return

    u = await _get_user(session, tg_id)
    if not u:
        await m.answer(_tr(lang, "user_not_found"))
        await state.clear()
        return

    if not _ban_supported(u):
        await m.answer(_tr(lang, "ban_unavailable"))
        await state.clear()
        return

    ok = _set_ban(u, False)
    if not ok:
        await m.answer(_tr(lang, "ban_unavailable"))
        await state.clear()
        return

    session.add(u)
    await session.commit()
    await session.refresh(u)

    await log_admin_action(
        session,
        admin_tg_id=m.from_user.id,
        action="unban",
        target_tg_id=tg_id,
    )

    await m.answer(_tr(lang, "unban_done"))
    await state.clear()


__all__ = ["router", "is_admin_btn", "is_admin_tg", "is_admin"]