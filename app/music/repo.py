from __future__ import annotations
from typing import Optional, List, Tuple
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.user_track import UserTrack

PLAYLIST_LIMIT = 50


async def get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


async def list_tracks(session: AsyncSession, user: User, limit: int = 10) -> List[Tuple[int, str]]:
    rows = (
        (
            await session.execute(
                select(UserTrack).where(UserTrack.user_id == user.id).order_by(UserTrack.id.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [(t.id, t.title or "Track") for t in rows]


async def get_track(session: AsyncSession, user: User, track_id: int) -> Optional[UserTrack]:
    return (
        await session.execute(select(UserTrack).where(UserTrack.user_id == user.id, UserTrack.id == track_id))
    ).scalar_one_or_none()


async def save_track(session: AsyncSession, user: User, title: str, file_id: str) -> None:
    total = await session.scalar(select(func.count()).select_from(UserTrack).where(UserTrack.user_id == user.id))
    if (total or 0) >= PLAYLIST_LIMIT:
        raise ValueError("limit")

    session.add(UserTrack(user_id=user.id, tg_id=user.tg_id, title=title or None, file_id=file_id.strip()))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return
