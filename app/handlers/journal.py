from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.assistant import _usage_tokens_last_24h, _quota_limits_tokens, _assistant_plan

from app.keyboards import (
    get_main_kb,
    is_history_btn,
    is_journal_add_btn,
    is_range_btn,
    is_search_btn,
    is_stats_btn,
    is_today_btn,
    is_week_btn,
)
from app.models.journal import JournalEntry
from app.models.user import User

# premium trial hook (мягкий, не ломаем если модуля нет)
# premium trial hook (shim; always returns bool)
try:
    from app.handlers.premium import maybe_grant_trial as _maybe_grant_trial  # type: ignore
except Exception:
    _maybe_grant_trial = None  # type: ignore


async def maybe_grant_trial(*a, **k) -> bool:
    """Safe wrapper: imported func may return None/bool."""
    fn = _maybe_grant_trial
    if not fn:
        return False
    try:
        res = await fn(*a, **k)
        return bool(res) if res is not None else False
    except Exception:
        return False


# feature-gates
# feature-gates (shim; always returns bool)
try:
    from app.services.features_v2 import require_feature_v2 as _require_feature_v2  # type: ignore
except Exception:
    _require_feature_v2 = None  # type: ignore


async def require_feature_v2(*a, **k) -> bool:
    """Safe wrapper: imported func should return bool, but keep it robust."""
    fn = _require_feature_v2
    if not fn:
        return True
    try:
        res = await fn(*a, **k)
        return bool(res)
    except Exception:
        return True


router = Router(name="journal")


class JournalFSM(StatesGroup):
    waiting_text = State()


class JournalSearch(StatesGroup):
    waiting_query = State()


SUPPORTED_LANGS = {"ru", "uk", "en"}


def _normalize_lang(code: Optional[str]) -> str:
    """
    Нормализация языка к ru/uk/en.
    Поддерживает ua, uk-UA, en-US, ru-RU и т.п.
    """
    s = (code or "ru").strip().lower()
    # берём базовый префикс до дефиса
    base = s.split("-")[0]

    if base in ("ua", "uk"):
        return "uk"
    if base == "en":
        return "en"
    if base == "ru":
        return "ru"
    return "ru"


def _tr(lang: Optional[str], ru: str, uk: str, en: str) -> str:
    lng = _normalize_lang(lang)
    if lng == "uk":
        return uk
    if lng == "en":
        return en
    return ru


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def _user_lang(user: Optional[User], fallback: Optional[str]) -> str:
    raw = getattr(user, "locale", None) or getattr(user, "lang", None) or fallback or "ru"
    return _normalize_lang(str(raw))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _user_tz(user: User):
    tz_name = getattr(user, "tz", None) or "Europe/Kyiv"
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _is_premium_user(user: Optional[User]) -> bool:
    if not user:
        return False

    # 1) флаг (учитываем int/str)
    try:
        v = getattr(user, "is_premium", False)
        if isinstance(v, str):
            if v.strip().lower() in ("1", "true", "yes", "y", "on"):
                return True
        else:
            if bool(v):
                return True
    except Exception:
        pass

    # 2) premium_until (может прийти как datetime или как str из sqlite)
    pu = getattr(user, "premium_until", None)
    if not pu:
        return False

    try:
        if isinstance(pu, str):
            pu = pu.strip()
            # поддержка "YYYY-MM-DD HH:MM:SS.ffffff"
            pu = pu.replace("Z", "+00:00").replace(" ", "T", 1)
            from datetime import datetime as _dt

            pu = _dt.fromisoformat(pu)

        if getattr(pu, "tzinfo", None) is None:
            pu = pu.replace(tzinfo=timezone.utc)

        return pu > _now_utc()
    except Exception:
        return False


# --- admin check (best-effort) ---
try:
    from app.handlers.admin import is_admin_tg  # type: ignore
except Exception:  # pragma: no cover

    def is_admin_tg(tg_id: int, /) -> bool:
        return False


def _is_admin_user(user: Optional[User], tg_id: Optional[int] = None) -> bool:
    """
    Каноничная проверка админа:
    1) по tg_id через is_admin_tg (главный источник истины)
    2) fallback на поле user.is_admin (если где-то используешь)
    """
    try:
        if tg_id and is_admin_tg(int(tg_id)):
            return True
    except Exception:
        pass

    try:
        return bool(getattr(user, "is_admin", False)) if user else False
    except Exception:
        return False


def _main_kb_for(user: Optional[User], lang: str, *, tg_id: Optional[int] = None, is_premium=None):
    """
    Безопасный вызов get_main_kb:
    - премиум считаем по _is_premium_user(user)
    - админ считаем через is_admin_tg(tg_id) (а не только user.is_admin)
    """
    if is_premium is None:
        is_premium = _is_premium_user(user)
    is_admin = _is_admin_user(user, tg_id=tg_id)

    try:
        return get_main_kb(lang, is_premium=is_premium, is_admin=is_admin)
    except TypeError:
        try:
            return get_main_kb(lang, is_premium=is_premium)
        except TypeError:
            return get_main_kb(lang)


def _policy_ok(user: Optional[User]) -> bool:
    """
    Совместимость:
    - policy_accepted (старый/временный флаг)
    - consent_accepted_at (если уже используешь)
    """
    if not user:
        return False

    try:
        if bool(getattr(user, "policy_accepted", False)):
            return True
    except Exception:
        pass

    return bool(getattr(user, "consent_accepted_at", None))


# -------------------- базовые команды/кнопки --------------------


@router.message(Command("journal"))
@router.message(F.text.func(is_journal_add_btn))
async def journal_prompt(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    if not _policy_ok(user):
        await m.answer(
            _tr(
                loc,
                "Нужно принять политику: нажми 🔒 Политика",
                "Потрібно прийняти політику: натисни 🔒 Політика",
                "You need to accept the policy: tap 🔒 Privacy",
            ),
            reply_markup=_main_kb_for(user, loc, tg_id=m.from_user.id),
        )
        return

    await state.set_state(JournalFSM.waiting_text)
    is_premium = _is_premium_user(user)

    await m.answer(
        _tr(
            loc,
            (
                "📒 <b>Твой личный дневник</b>\n\n"
                "Выгружай сюда всё, что крутится в голове. Я не просто сохраню текст, а помогу найти взаимосвязи, подсвечу главное и напомню о важных инсайтах.\n\n"
                "💡 <i>С чего начать?</i>\n"
                "• Какая главная победа или мысль за сегодня?\n"
                "• Что забирает твою энергию прямо сейчас?\n"
                "• Какой один микро-шаг сделает завтрашний день лучше?\n\n"
                "Напиши всё одним сообщением 👇\n"
                + ("<i>💎 В Premium доступен поиск, фильтры и глубокая аналитика мыслей.</i>\n\n" if not is_premium else "")
                + "/cancel — отменить"
            ),
            (
                "📒 <b>Твій особистий щоденник</b>\n\n"
                "Вивантажуй сюди все, що крутиться в голові. Я не просто збережу текст, а допоможу знайти взаємозв'язки, підсвічу головне і нагадаю про важливі інсайти.\n\n"
                "💡 <i>З чого почати?</i>\n"
                "• Яка головна перемога чи думка за сьогодні?\n"
                "• Що забирає твою енергію прямо зараз?\n"
                "• Який один мікро-крок зробить завтрашній день кращим?\n\n"
                "Напиши все одним повідомленням 👇\n"
                + ("<i>💎 У Premium доступний пошук, фільтри та глибока аналітика думок.</i>\n\n" if not is_premium else "")
                + "/cancel — скасувати"
            ),
            (
                "📒 <b>Your personal journal</b>\n\n"
                "Offload everything on your mind. I won't just save the text, I'll help you find patterns, highlight what matters, and remind you of key insights.\n\n"
                "💡 <i>Where to start?</i>\n"
                "• What was your main win or thought today?\n"
                "• What's draining your energy right now?\n"
                "• What's one micro-step to make tomorrow better?\n\n"
                "Write it all in one message 👇\n"
                + ("<i>💎 Premium unlocks search, filters, and deep thought analytics.</i>\n\n" if not is_premium else "")
                + "/cancel — cancel"
            ),
        )
    )


@router.message(JournalFSM.waiting_text, Command("cancel"))
async def journal_cancel(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    await state.clear()
    await m.answer(
        _tr(
            loc,
            "Отменил. Запись не сохранена.",
            "Скасував. Запис не збережено.",
            "Cancelled. Entry not saved.",
        ),
        reply_markup=_main_kb_for(user, loc, tg_id=m.from_user.id),
    )


@router.message(JournalFSM.waiting_text, F.text)
async def journal_save(
    m: Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await state.clear()
        await m.answer(
            _tr(loc, "Нажми /start", "Натисни /start", "Press /start"),
            reply_markup=_main_kb_for(None, loc, tg_id=m.from_user.id),
        )
        return

    user_id = user.id
    is_premium = _is_premium_user(user)

    if not _policy_ok(user):
        await state.clear()
        await m.answer(
            _tr(
                loc,
                "Нужно принять политику: нажми 🔒 Политика",
                "Потрібно прийняти політику: натисни 🔒 Політика",
                "You need to accept the policy: tap 🔒 Privacy",
            ),
            reply_markup=_main_kb_for(user, loc, tg_id=m.from_user.id, is_premium=is_premium),
        )
        return

    text = (m.text or "").strip()
    if len(text) < 3:
        await m.answer(
            _tr(
                loc,
                "Коротковато. Добавь деталей и отправь одним сообщением.",
                "Занадто коротко. Додай деталей і надішли одним повідомленням.",
                "Too short. Add a bit more detail and send again in one message.",
            )
        )
        return

    entry = JournalEntry(user_id=user_id, text=text)
    session.add(entry)
    await session.commit()

    try:
        await maybe_grant_trial(session, m.from_user.id)
    except Exception:
        await session.rollback()

    await state.clear()

    total = (
        await session.execute(select(func.count()).select_from(JournalEntry).where(JournalEntry.user_id == user_id))
    ).scalar() or 0

    await m.answer(
        _tr(
            loc,
            f"✅ Сохранил. Записей всего: {total}.\n\n"
            "Хочешь продолжить?\n"
            "• /today — что было за 24 часа\n"
            "• /week — итоги недели\n"
            "• /history — вся лента\n\n"
            "Премиум открывает: поиск, диапазоны, расширенную историю и статистику.",
            f"✅ Зберіг. Записів всього: {total}.\n\n"
            "Хочеш продовжити?\n"
            "• /today — що було за 24 години\n"
            "• /week — підсумки тижня\n"
            "• /history — вся стрічка\n\n"
            "Преміум відкриває: пошук, діапазони, розширену історію та статистику.",
            f"✅ Saved. Total entries: {total}.\n\n"
            "Want to continue?\n"
            "• /today — last 24 hours\n"
            "• /week — weekly summary\n"
            "• /history — full feed\n\n"
            "Premium unlocks: search, ranges, extended history and stats.",
        ),
        reply_markup=_main_kb_for(user, loc, tg_id=m.from_user.id, is_premium=is_premium),
    )


# -------------------- stats --------------------


@router.message(Command("stats"))
@router.message(F.text.func(is_stats_btn))
async def journal_stats(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    is_admin = _is_admin_user(user, tg_id=m.from_user.id)
    if not user:
        await m.answer(_tr(loc, "Нажми /start", "Натисни /start", "Press /start"))
        return

    ok = await require_feature_v2(m, session, user, "journal_stats")
    if not ok and not _is_premium_user(user):
        return

    total = (
        await session.execute(select(func.count()).select_from(JournalEntry).where(JournalEntry.user_id == user.id))
    ).scalar() or 0

    parts: list[str] = []
    plan = _assistant_plan(user)
    limit_ast = _quota_limits_tokens(plan, "assistant")
    used_ast = await _usage_tokens_last_24h(session, user.id, "assistant")

    limit_vis = _quota_limits_tokens(plan, "vision")
    used_vis = await _usage_tokens_last_24h(session, user.id, "vision")

    # Считаем остаток
    left_ast = max(0, limit_ast - used_ast)
    left_vis = max(0, limit_vis - used_vis)

    parts: list[str] = []

    stats_text = _tr(
        loc,
        f"📒 <b>Дневник</b>\n• Записей всего: {total}\n\n"
        f"🤖 <b>Нейросети (доступно на сегодня):</b>\n"
        f"• Текстовые запросы: ~{left_ast // 500} шт. (остаток {left_ast} токенов)\n"
        f"• Анализ фото: {left_vis // 800} шт.\n\n"
        f"👑 Твой тариф: {plan.upper()}",

        f"📒 <b>Щоденник</b>\n• Записів всього: {total}\n\n"
        f"🤖 <b>Нейромережі (доступно на сьогодні):</b>\n"
        f"• Текстові запити: ~{left_ast // 500} шт. (залишок {left_ast} токенів)\n"
        f"• Аналіз фото: {left_vis // 800} шт.\n\n"
        f"👑 Твій тариф: {plan.upper()}",

        f"📒 <b>Journal</b>\n• Total entries: {total}\n\n"
        f"🤖 <b>AI limits (available today):</b>\n"
        f"• Text queries: ~{left_ast // 500} (left {left_ast} tokens)\n"
        f"• Photo analysis: {left_vis // 800} left\n\n"
        f"👑 Your plan: {plan.upper()}",
    )
    parts.append(stats_text)

    # analytics_events (7d)
    try:
        has_analytics = (
            await session.execute(
                sql_text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analytics_events' LIMIT 1;")
            )
        ).scalar_one_or_none()

        if has_analytics:
            cols = [r[1] for r in (await session.execute(sql_text("PRAGMA table_info(analytics_events);"))).all()]
            col_tg = "tg_id" if "tg_id" in cols else ("user_id" if "user_id" in cols else None)
            col_name = (
                "name"
                if "name" in cols
                else ("event" if "event" in cols else ("event_name" if "event_name" in cols else None))
            )
            col_created = (
                "created_at"
                if "created_at" in cols
                else ("ts" if "ts" in cols else ("created" if "created" in cols else None))
            )

            if col_tg and col_name and col_created:
                active_7d = (
                    await session.execute(
                        sql_text(
                            f"SELECT COUNT(DISTINCT {col_tg}) "
                            f"FROM analytics_events "
                            f"WHERE {col_created} >= datetime('now','-7 day');"
                        )
                    )
                ).scalar() or 0

                rows = (
                    await session.execute(
                        sql_text(
                            f"SELECT {col_name} AS n, COUNT(*) AS c "
                            f"FROM analytics_events "
                            f"WHERE {col_created} >= datetime('now','-7 day') "
                            f"GROUP BY {col_name} "
                            f"ORDER BY c DESC;"
                        )
                    )
                ).all()

                block: list[str] = []
                block.append("📊 Analytics за 7 дней:")
                block.append(f"• active_users_7d: {active_7d}")

                if rows:
                    top3 = rows[:3]
                    rest = rows[3:][:50]

                    block.append("")
                    block.append("🏆 Top-3:")
                    for n, c in top3:
                        block.append(f"• {n}: {c}")

                    if rest:
                        block.append("")
                        block.append("🧾 Остальное:")
                        for n, c in rest:
                            block.append(f"• {n}: {c}")

                if is_admin:
                    parts.append("\n".join(block))
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass

    # events trial_* (7d)
    try:
        has_events = (
            await session.execute(sql_text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events' LIMIT 1;"))
        ).scalar_one_or_none()

        if has_events:
            rows = (
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

            mp = {str(n): int(c) for (n, c) in rows}
            if is_admin:
                parts.append(
                    "🎁 Trial (7d):\n"
                    f"• trial_click: {mp.get('trial_click', 0)}\n"
                    f"• trial_granted: {mp.get('trial_granted', 0)}\n"
                    f"• trial_denied: {mp.get('trial_denied', 0)}"
                )
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass

    try:
        await m.answer("\n\n".join(parts))
    except Exception as e:
        try:
            await session.rollback()
        except Exception:
            pass
        # покажем ошибку прямо в телегу, чтобы не гадать
        await m.answer("❌ /stats send failed: " + repr(e))
        raise


@router.message(Command("today"))
@router.message(F.text.func(is_today_btn))
async def journal_today(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await m.answer(_tr(loc, "Нажми /start", "Натисни /start", "Press /start"))
        return

    tz = _user_tz(user)
    now = _now_utc().astimezone(tz)
    since = now - timedelta(days=1)

    q = (
        select(JournalEntry)
        .where(JournalEntry.user_id == user.id)
        .where(JournalEntry.created_at >= since.astimezone(timezone.utc))
        .order_by(JournalEntry.created_at.desc())
    )
    rows = (await session.execute(q)).scalars().all()

    if not rows:
        await m.answer(
            _tr(
                loc,
                "За последние 24 часа записей не было.",
                "За останні 24 години записів не було.",
                "No entries in the last 24 hours.",
            )
        )
        return

    lines: list[str] = []
    for e in rows:
        dt_local = e.created_at
        if dt_local.tzinfo is None:
            dt_local = dt_local.replace(tzinfo=timezone.utc)
        dt_local = dt_local.astimezone(tz)
        snippet = (e.text or "").strip()
        if len(snippet) > 80:
            snippet = snippet[:77] + "…"
        lines.append(f"{dt_local:%Y-%m-%d %H:%M} — {snippet}")

    header = _tr(
        loc,
        "Записи за последние 24 часа:",
        "Записи за останні 24 години:",
        "Entries for the last 24 hours:",
    )
    await m.answer(header + "\n\n" + "\n".join(lines))


# -------------------- history --------------------


@router.message(Command("history"))
@router.message(F.text.func(is_history_btn))
async def journal_history(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await m.answer(_tr(loc, "Нажми /start", "Натисни /start", "Press /start"))
        return

    # /history [N]  (если N>5 → premium gate как было)
    parts = (m.text or "").split()
    requested: Optional[int] = None
    if len(parts) > 1 and parts[1].isdigit():
        requested = max(1, min(50, int(parts[1])))

    limit = 5
    if requested and requested > 5:
        ok = await require_feature_v2(m, session, user, "journal_history_extended")
        if not ok and not _is_premium_user(user):
            return
        limit = requested
    elif requested:
        limit = requested

    await _render_history(m, session, user, loc, offset=0, limit=limit, edit=False)


# -------------------- history ui --------------------


async def _render_history(
    m: Message,
    session: AsyncSession,
    user: User,
    loc: str,
    *,
    offset: int,
    limit: int,
    edit: bool,
) -> None:
    total = (
        await session.execute(select(func.count()).select_from(JournalEntry).where(JournalEntry.user_id == user.id))
    ).scalar() or 0

    q = (
        select(JournalEntry)
        .where(JournalEntry.user_id == user.id)
        .order_by(JournalEntry.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = (await session.execute(q)).scalars().all()

    if not rows:
        text = _tr(loc, "Записей пока нет.", "Записів поки немає.", "No entries yet.")
        if edit:
            try:
                await m.edit_text(text)
            except Exception:
                await m.answer(text)
        else:
            await m.answer(text)
        return

    tz = _user_tz(user)
    lines = []
    for e in rows:
        dt_local = e.created_at
        if dt_local.tzinfo is None:
            dt_local = dt_local.replace(tzinfo=timezone.utc)
        dt_local = dt_local.astimezone(tz)

        snippet = (e.text or "").strip()
        if len(snippet) > 80:
            snippet = snippet[:77] + "…"

        lines.append(
            _tr(
                loc,
                f"• {dt_local:%Y-%m-%d %H:%M} — {snippet}  (/open_{e.id})",
                f"• {dt_local:%Y-%m-%d %H:%M} — {snippet}  (/open_{e.id})",
                f"• {dt_local:%Y-%m-%d %H:%M} — {snippet}  (/open_{e.id})",
            )
        )

    header = _tr(
        loc,
        f"🕘 История ({offset + 1}-{min(offset + limit, total)} из {total})",
        f"🕘 Історія ({offset + 1}-{min(offset + limit, total)} із {total})",
        f"🕘 History ({offset + 1}-{min(offset + limit, total)} of {total})",
    )

    kb = _history_kb(offset=offset, limit=limit, total=total, loc=loc)

    out = header + "\n\n" + "\n\n".join(lines)

    if edit:
        try:
            await m.edit_text(out, reply_markup=kb)
        except Exception:
            await m.answer(out, reply_markup=kb)
    else:
        await m.answer(out, reply_markup=kb)


def _history_kb(offset: int, limit: int, total: int, loc: str) -> InlineKeyboardMarkup:
    prev_off = max(0, offset - limit)
    next_off = offset + limit

    prev_btn = InlineKeyboardButton(
        text=_tr(loc, "⬅️ Назад", "⬅️ Назад", "⬅️ Back"),
        callback_data=f"journal:history:{prev_off}:{limit}",
    )
    next_btn = InlineKeyboardButton(
        text=_tr(loc, "➡️ Далее", "➡️ Далі", "➡️ Next"),
        callback_data=f"journal:history:{next_off}:{limit}",
    )

    row = []
    if offset > 0:
        row.append(prev_btn)
    if next_off < total:
        row.append(next_btn)

    return InlineKeyboardMarkup(inline_keyboard=[row] if row else [])


@router.callback_query(F.data.startswith("journal:history:"))
async def cb_journal_history(call: CallbackQuery, session: AsyncSession, lang: Optional[str] = None):
    if not call.from_user:
        return

    user = await _get_user(session, call.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await call.answer()
        return

    # journal:history:{offset}:{limit}
    try:
        _, _, off_s, lim_s = (call.data or "").split(":", 3)
        offset = max(0, int(off_s))
        limit = max(1, min(50, int(lim_s)))
    except Exception:
        await call.answer()
        return

    msg = cast(Message, call.message)

    await _render_history(msg, session, user, loc, offset=offset, limit=limit, edit=True)
    await call.answer()


@router.callback_query(F.data.startswith("journal:open:"))
async def cb_journal_open(call: CallbackQuery, session: AsyncSession, lang: Optional[str] = None):
    if not call.from_user:
        return

    user = await _get_user(session, call.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await call.answer()
        return

    try:
        entry_id = int((call.data or "").split(":", 2)[2])
    except Exception:
        await call.answer()
        return

    q = select(JournalEntry).where(JournalEntry.user_id == user.id, JournalEntry.id == entry_id).limit(1)
    entry = (await session.execute(q)).scalar_one_or_none()
    if not entry:
        await call.answer(
            _tr(loc, "Запись не найдена.", "Запис не знайдено.", "Entry not found."),
            show_alert=True,
        )
        return

    tz = _user_tz(user)
    dt_local = entry.created_at
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=timezone.utc)
    dt_local = dt_local.astimezone(tz)

    text = (entry.text or "").strip()
    if not text:
        text = _tr(loc, "(пусто)", "(порожньо)", "(empty)")

    msg = _tr(
        loc,
        f"📖 Запись {dt_local:%Y-%m-%d %H:%M}\n\n{text}",
        f"📖 Запис {dt_local:%Y-%m-%d %H:%M}\n\n{text}",
        f"📖 Entry {dt_local:%Y-%m-%d %H:%M}\n\n{text}",
    )

    await call.message.answer(msg)
    await call.answer()


@router.message(F.text.regexp(r"^/open_(\d+)$"))
async def journal_open_cmd(m: Message, session: AsyncSession, lang: Optional[str] = None):
    if not m.from_user:
        return
    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await m.answer(_tr(loc, "Нажми /start", "Натисни /start", "Press /start"))
        return

    import re as _re

    mm = _re.match(r"^/open_(\d+)$", (m.text or "").strip())
    if not mm:
        return
    entry_id = int(mm.group(1))

    q = select(JournalEntry).where(JournalEntry.user_id == user.id, JournalEntry.id == entry_id).limit(1)
    entry = (await session.execute(q)).scalar_one_or_none()
    if not entry:
        await m.answer(_tr(loc, "Запись не найдена.", "Запис не знайдено.", "Entry not found."))
        return

    tz = _user_tz(user)
    dt_local = entry.created_at
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=timezone.utc)
    dt_local = dt_local.astimezone(tz)

    text = (entry.text or "").strip()
    if not text:
        text = _tr(loc, "(пусто)", "(порожньо)", "(empty)")

    await m.answer(
        _tr(
            loc,
            f"📖 Запись {dt_local:%Y-%m-%d %H:%M}\n\n{text}",
            f"📖 Запис {dt_local:%Y-%m-%d %H:%M}\n\n{text}",
            f"📖 Entry {dt_local:%Y-%m-%d %H:%M}\n\n{text}",
        )
    )


# -------------------- search --------------------


@router.message(Command("search"))
async def journal_search_cmd(
    m: Message,
    session: AsyncSession,
    state: FSMContext,
    lang: Optional[str] = None,
):
    # /search слово
    if not m.from_user:
        return
    await state.clear()

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await m.answer(_tr(loc, "Нажми /start", "Натисни /start", "Press /start"))
        return

    ok = await require_feature_v2(m, session, user, "journal_search")
    if not ok and not _is_premium_user(user):
        return

    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await m.answer(
            _tr(
                loc,
                "Формат: /search слово",
                "Формат: /search слово",
                "Format: /search word",
            )
        )
        return

    await _run_journal_search(m, session, user, loc, parts[1].strip())


@router.message(F.text.func(is_search_btn))
async def journal_search_btn(
    m: Message,
    session: AsyncSession,
    state: FSMContext,
    lang: Optional[str] = None,
):
    # Нажали кнопку "🔎 Поиск" → просим слово
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await m.answer(_tr(loc, "Нажми /start", "Натисни /start", "Press /start"))
        return

    ok = await require_feature_v2(m, session, user, "journal_search")
    if not ok and not _is_premium_user(user):
        return

    await state.set_state(JournalSearch.waiting_query)
    await m.answer(
        _tr(
            loc,
            "Введи слово или фразу для поиска.",
            "Введи слово або фразу для пошуку.",
            "Type a word or phrase to search.",
        )
    )


@router.message(JournalSearch.waiting_query)
async def journal_search_query(
    m: Message,
    session: AsyncSession,
    state: FSMContext,
    lang: Optional[str] = None,
):
    # Следующее сообщение пользователя — это запрос
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    await state.clear()

    if not user:
        await m.answer(_tr(loc, "Нажми /start", "Натисни /start", "Press /start"))
        return

    query_text = (m.text or "").strip()
    if not query_text:
        await m.answer(
            _tr(
                loc,
                "Пустой запрос. Напиши слово.",
                "Порожній запит. Напиши слово.",
                "Empty query. Type a word.",
            )
        )
        return

    await _run_journal_search(m, session, user, loc, query_text)


async def _run_journal_search(m: Message, session: AsyncSession, user: User, loc: str, query_text: str) -> None:
    # экранируем %, _, \ чтобы LIKE не ломался
    q_raw = query_text.strip()
    q_esc = q_raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{q_esc}%"

    q = (
        select(JournalEntry)
        .where(JournalEntry.user_id == user.id)
        .where(JournalEntry.text.ilike(pattern, escape="\\"))
        .order_by(JournalEntry.created_at.desc())
        .limit(10)
    )
    rows = (await session.execute(q)).scalars().all()

    if not rows:
        await m.answer(
            _tr(
                loc,
                "Ничего не нашёл по запросу.",
                "Нічого не знайшов за запитом.",
                "No matches found.",
            )
        )
        return

    tz = _user_tz(user)
    lines: list[str] = []
    for e in rows:
        dt_local = e.created_at
        if dt_local.tzinfo is None:
            dt_local = dt_local.replace(tzinfo=timezone.utc)
        dt_local = dt_local.astimezone(tz)

        snippet = (e.text or "").strip()
        if len(snippet) > 120:
            snippet = snippet[:117] + "…"

        lines.append(f"{dt_local:%Y-%m-%d %H:%M} — {snippet}")

    await m.answer(_tr(loc, "Нашёл:", "Знайшов:", "Found:") + "\n\n" + "\n".join(lines))


# -------------------- range --------------------


@router.message(Command("range"))
@router.message(F.text.func(is_range_btn))
async def journal_range(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await m.answer(_tr(loc, "Нажми /start", "Натисни /start", "Press /start"))
        return

    ok = await require_feature_v2(m, session, user, "journal_range")
    if not ok and not _is_premium_user(user):
        return

    parts = (m.text or "").split()
    if len(parts) < 3:
        await m.answer(
            _tr(
                loc,
                "Формат: /range YYYY-MM-DD YYYY-MM-DD",
                "Формат: /range YYYY-MM-DD YYYY-MM-DD",
                "Format: /range YYYY-MM-DD YYYY-MM-DD",
            )
        )
        return

    try:
        start = datetime.fromisoformat(parts[1]).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(parts[2]).replace(tzinfo=timezone.utc) + timedelta(days=1)
    except Exception:
        await m.answer(
            _tr(
                loc,
                "Не понял даты. Пример: /range 2025-12-01 2025-12-06",
                "Не зрозумів дати. Приклад: /range 2025-12-01 2025-12-06",
                "Invalid dates. Example: /range 2025-12-01 2025-12-06",
            )
        )
        return

    q = (
        select(JournalEntry)
        .where(JournalEntry.user_id == user.id)
        .where(JournalEntry.created_at >= start)
        .where(JournalEntry.created_at < end)
        .order_by(JournalEntry.created_at.desc())
        .limit(50)
    )
    rows = (await session.execute(q)).scalars().all()

    if not rows:
        await m.answer(
            _tr(
                loc,
                "В этом диапазоне записей не было.",
                "У цьому діапазоні записів не було.",
                "No entries in this range.",
            )
        )
        return

    tz = _user_tz(user)
    lines: list[str] = []
    for e in rows:
        dt_local = e.created_at
        if dt_local.tzinfo is None:
            dt_local = dt_local.replace(tzinfo=timezone.utc)
        dt_local = dt_local.astimezone(tz)
        snippet = (e.text or "").strip()
        if len(snippet) > 90:
            snippet = snippet[:87] + "…"
        lines.append(f"{dt_local:%Y-%m-%d %H:%M} — {snippet}")

    await m.answer(
        _tr(
            loc,
            "Записи за выбранный период:",
            "Записи за вибраний період:",
            "Entries for the selected period:",
        )
        + "\n\n"
        + "\n".join(lines)
    )


# -------------------- week --------------------


@router.message(Command("week"))
@router.message(F.text.func(is_week_btn))
async def journal_week(
    m: Message,
    session: AsyncSession,
    lang: Optional[str] = None,
):
    if not m.from_user:
        return

    user = await _get_user(session, m.from_user.id)
    loc = _user_lang(user, lang)

    if not user:
        await m.answer(_tr(loc, "Нажми /start", "Натисни /start", "Press /start"))
        return

    tz = _user_tz(user)
    now = _now_utc().astimezone(tz)
    since = now - timedelta(days=7)

    q = (
        select(JournalEntry)
        .where(JournalEntry.user_id == user.id)
        .where(JournalEntry.created_at >= since.astimezone(timezone.utc))
    )
    rows = (await session.execute(q)).scalars().all()

    total = len(rows)

    dates = set()
    for e in rows:
        dt_local = e.created_at
        if dt_local.tzinfo is None:
            dt_local = dt_local.replace(tzinfo=timezone.utc)
        dt_local = dt_local.astimezone(tz)
        dates.add(dt_local.date())
    active_days = len(dates)

    overall_total = (
        await session.execute(select(func.count()).select_from(JournalEntry).where(JournalEntry.user_id == user.id))
    ).scalar() or 0

    text = _tr(
        loc,
        (
            "Итоги за последние 7 дней:\n\n"
            f"• Записей за неделю: {total}\n"
            f"• Дней с записями: {active_days} из 7\n"
            f"• Всего записей в дневнике: {overall_total}\n\n"
            "Это уже движение. Продолжай вести дневник."
        ),
        (
            "Підсумки за останні 7 днів:\n\n"
            f"• Записів за тиждень: {total}\n"
            f"• Днів із записами: {active_days} з 7\n"
            f"• Всього записів у щоденнику: {overall_total}\n\n"
            "Це вже рух. Продовжуй вести щоденник."
        ),
        (
            "Summary for the last 7 days:\n\n"
            f"• Entries this week: {total}\n"
            f"• Days with entries: {active_days} of 7\n"
            f"• Total entries in the journal: {overall_total}\n\n"
            "This is progress already. Keep writing."
        ),
    )

    await m.answer(text)


__all__ = ["router"]
