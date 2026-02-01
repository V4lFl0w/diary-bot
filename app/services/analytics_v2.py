from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import AnalyticsEvent

# что НЕ показываем в админ-дашборде (но в БД можно хранить)
HIDDEN_EVENTS = {"test_event"}
HIDDEN_PREFIXES = ("test_",)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _since(days: int) -> datetime:
    return _now_utc() - timedelta(days=days)


def _is_hidden_event(event: str) -> bool:
    e = (event or "").strip()
    if e in HIDDEN_EVENTS:
        return True
    return any(e.startswith(p) for p in HIDDEN_PREFIXES)


async def log_event_v2(
    session: AsyncSession,
    *,
    user_id: Optional[int],
    event: str,
    props: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Пишем событие в analytics_events.
    Никаких commit внутри — пусть решает вызывающий (как ты уже сделал).
    """
    _ts_dt = datetime.now().astimezone()
    ev = AnalyticsEvent(
        user_id=user_id,
        event=event,
        props=props or None,
        ts=_ts_dt,  # если у модели есть поле ts; если нет — убери эту строку
    )
    session.add(ev)


async def get_dashboard_7d(
    session: AsyncSession,
    *,
    include_system: bool = False,
    limit_events: int = 50,
) -> Tuple[Dict[str, int], int]:
    """
    Возвращает:
      - counts: dict[event] = cnt
      - active_users: кол-во уникальных user_id за 7 дней
    """
    since = _since(7)

    # Считаем события
    q = (
        select(AnalyticsEvent.event, func.count().label("cnt"))
        .where(AnalyticsEvent.ts >= since)
        .group_by(AnalyticsEvent.event)
        .order_by(desc("cnt"))
        .limit(limit_events)
    )
    rows = (await session.execute(q)).all()

    counts: Dict[str, int] = {}
    for event, cnt in rows:
        if not include_system and _is_hidden_event(event):
            continue
        counts[event] = int(cnt or 0)

    # Активные пользователи
    q2 = (
        select(func.count(func.distinct(AnalyticsEvent.user_id)))
        .where(AnalyticsEvent.ts >= since)
        .where(AnalyticsEvent.user_id.isnot(None))
    )
    active_users = (await session.execute(q2)).scalar() or 0

    return counts, int(active_users)


async def get_top_actions_7d(
    session: AsyncSession,
    *,
    top_n: int = 3,
) -> Dict[str, int]:
    counts, _ = await get_dashboard_7d(session, include_system=False, limit_events=1000)
    return dict(list(counts.items())[:top_n])


async def get_recent_visitors_7d(
    session: AsyncSession,
    *,
    limit: int = 30,
) -> list[tuple[int, str, str, str]]:
    """
    Показать последних "заходивших" (через событие user_start / user_seen).
    Возвращает список:
      (user_id, ts, event, props_json)
    """
    since = _since(7)
    q = (
        select(
            AnalyticsEvent.user_id,
            AnalyticsEvent.ts,
            AnalyticsEvent.event,
            AnalyticsEvent.props,
        )
        .where(AnalyticsEvent.ts >= since)
        .where(AnalyticsEvent.user_id.isnot(None))
        .where(AnalyticsEvent.event.in_(("user_start", "user_seen")))
        .order_by(desc(AnalyticsEvent.id))
        .limit(limit)
    )
    rows = (await session.execute(q)).all()
    out: list[tuple[int, str, str, str]] = []
    for uid, ts, ev, props in rows:
        out.append((int(uid), str(ts), str(ev), str(props or "")))
    return out
