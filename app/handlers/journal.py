from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text as sql_text

from app.models.user import User
from app.models.journal import JournalEntry
from app.keyboards import (
    get_main_kb,
    is_journal_add_btn,
    is_history_btn,
    is_today_btn,
    is_week_btn,
    is_search_btn,
    is_range_btn,
    is_stats_btn,
)

# premium trial hook (мягкий, не ломаем если модуля нет)
try:
    from app.handlers.premium import maybe_grant_trial
except Exception:
    async def maybe_grant_trial(*a, **k):
        return False

# feature-gates
try:
    from app.services.features_v2 import require_feature_v2
except Exception:
    async def require_feature_v2(*a, **k):
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
    l = _normalize_lang(lang)
    if l == "uk":
        return uk
    if l == "en":
        return en
    return ru


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (
        await session.execute(select(User).where(User.tg_id == tg_id))
    ).scalar_one_or_none()


def _user_lang(user: Optional[User], fallback: Optional[str]) -> str:
    raw = (
        getattr(user, "locale", None)
        or getattr(user, "lang", None)
        or fallback
        or "ru"
    )
    return _normalize_lang(str(raw))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _user_tz(user: Optional[User]):
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
    def is_admin_tg(_: int) -> bool:
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


def _main_kb_for(user: Optional[User], lang: str, *, tg_id: Optional[int] = None):
    """
    Безопасный вызов get_main_kb:
    - премиум считаем по _is_premium_user(user)
    - админ считаем через is_admin_tg(tg_id) (а не только user.is_admin)
    """
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
    await m.answer(
        _tr(
            loc,
            "Напиши 2–3 мысли за сегодня одним сообщением.\n\n/cancel — отменить",
            "Напиши 2–3 думки за сьогодні одним повідомленням.\n\n/cancel — скасувати",
            "Send 2–3 thoughts for today in one message.\n\n/cancel — cancel",
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

    if not _policy_ok(user):
        await state.clear()
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

    entry = JournalEntry(user_id=user.id, text=text)
    session.add(entry)
    await session.commit()

    try:
        await maybe_grant_trial(session, m.from_user.id)
    except Exception:
        pass

    await state.clear()

    total = (
        await session.execute(
            select(func.count())
            .select_from(JournalEntry)
            .where(JournalEntry.user_id == user.id)
        )
    ).scalar() or 0

    await m.answer(
        _tr(
            loc,
            f"Сохранил. Записей всего: {total}.\n\n"
            "Быстрые действия есть в меню.\n"
            "Премиум расширяет: поиск, диапазоны, расширенную историю и статистику.",
            f"Зберіг. Записів всього: {total}.\n\n"
            "Швидкі дії є в меню.\n"
            "Преміум розширює: пошук, діапазони, розширену історію та статистику.",
            f"Saved. Total entries: {total}.\n\n"
            "Quick actions are in the menu.\n"
            "Premium expands: search, ranges, extended history and stats.",
        ),
        reply_markup=_main_kb_for(user, loc, tg_id=m.from_user.id),
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
        await session.execute(
            select(func.count())
            .select_from(JournalEntry)
            .where(JournalEntry.user_id == user.id)
        )
    ).scalar() or 0

    parts: list[str] = []
    parts.append(
        _tr(
            loc,
            f"📒 Дневник\n• Записей всего: {total}",
            f"📒 Щоденник\n• Записів всього: {total}",
            f"📒 Journal\n• Total entries: {total}",
        )
    )

    # analytics_events (7d)
    try:
        has_analytics = (
            await session.execute(
                sql_text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='analytics_events' LIMIT 1;"
                )
            )
        ).scalar_one_or_none()

        if has_analytics:
            cols = [r[1] for r in (await session.execute(sql_text("PRAGMA table_info(analytics_events);"))).all()]
            col_tg = "tg_id" if "tg_id" in cols else ("user_id" if "user_id" in cols else None)
            col_name = "name" if "name" in cols else ("event" if "event" in cols else ("event_name" if "event_name" in cols else None))
            col_created = "created_at" if "created_at" in cols else ("ts" if "ts" in cols else ("created" if "created" in cols else None))

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
            await session.execute(
                sql_text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='events' LIMIT 1;"
                )
            )
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

    tz = _user_tz(user)

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

    q = (
        select(JournalEntry)
        .where(JournalEntry.user_id == user.id)
        .order_by(JournalEntry.created_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(q)).scalars().all()

    if not rows:
        await m.answer(
            _tr(
                loc,
                "Записей пока не было.",
                "Записів поки не було.",
                "No entries yet.",
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
        f"Последние {len(rows)} записей:",
        f"Останні {len(rows)} записів:",
        f"Last {len(rows)} entries:",
    )
    await m.answer(header + "\n\n" + "\n".join(lines))


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
        await m.answer(_tr(loc, "Формат: /search слово", "Формат: /search слово", "Format: /search word"))
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
    await m.answer(_tr(loc, "Введи слово или фразу для поиска.", "Введи слово або фразу для пошуку.", "Type a word or phrase to search."))


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
        await m.answer(_tr(loc, "Пустой запрос. Напиши слово.", "Порожній запит. Напиши слово.", "Empty query. Type a word."))
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
        await m.answer(_tr(loc, "Ничего не нашёл по запросу.", "Нічого не знайшов за запитом.", "No matches found."))
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
        ) + "\n\n" + "\n".join(lines)
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
        await session.execute(
            select(func.count())
            .select_from(JournalEntry)
            .where(JournalEntry.user_id == user.id)
        )
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
