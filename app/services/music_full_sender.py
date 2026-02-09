from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from aiogram.types import FSInputFile
from pathlib import Path

from app.models.user import User
from app.models.user_track import UserTrack
from app.services.downloader import download_from_youtube
from app.webapp.music_api import _tg_send_audio


async def send_or_fetch_full_track(
    *,
    session: AsyncSession,
    user: User,
    track: UserTrack,
):
    """
    1) Если file_id есть → сразу шлём
    2) Если нет → качаем → шлём → сохраняем file_id
    """

    audio_ref = (track.file_id or "").strip()

    # ❌ URL-треки не качаем
    if audio_ref.startswith("http"):
        await _tg_send_audio(
            chat_id=user.tg_id,
            audio_ref=audio_ref,
            caption=f"🎧 {track.title or 'Track'}",
        )
        return

    # ✅ КЭШ
    if audio_ref:
        await _tg_send_audio(
            chat_id=user.tg_id,
            audio_ref=audio_ref,
            caption=f"🎧 {track.title or 'Track'}",
        )
        return

    # ⬇️ FIRST TIME — качаем
    query = track.title or "music track"
    audio_path: Path = download_from_youtube(query)

    from app.bot import bot  # локально, чтобы не было циклов

    msg = await bot.send_audio(
        chat_id=user.tg_id,
        audio=FSInputFile(audio_path),
        title=track.title or None,
    )

    track.file_id = msg.audio.file_id
    session.add(track)
    await session.commit()
