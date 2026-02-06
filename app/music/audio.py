from __future__ import annotations

import os

import aiohttp
from aiogram.exceptions import TelegramBadRequest
from aiogram.types.input_file import BufferedInputFile


MUSIC_DL_MAX_MB = int(os.getenv("MUSIC_DL_MAX_MB", "18"))
MUSIC_DL_UA = os.getenv("MUSIC_DL_UA", "ValFlowMusic/1.0")


def _is_http_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith(("http://", "https://"))


async def send_audio_safe(bot, chat_id: int, audio_src: str, caption: str | None = None) -> None:
    src = (audio_src or "").strip()
    if not src:
        return

    # 1) try direct (file_id or url)
    try:
        await bot.send_audio(chat_id=chat_id, audio=src, caption=caption)
        return
    except TelegramBadRequest:
        if not _is_http_url(src):
            await bot.send_message(
                chat_id=chat_id,
                text="⚠️ Не удалось отправить этот трек. Попробуй выбрать другой.",
            )
            return

    # 2) URL -> download -> BufferedInputFile
    try:
        timeout = aiohttp.ClientTimeout(total=40)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": MUSIC_DL_UA}) as s:
            async with s.get(src, allow_redirects=True) as r:
                if r.status != 200:
                    raise RuntimeError(f"download failed {r.status}")

                ct = (r.headers.get("Content-Type") or "").lower()
                if "text/html" in ct:
                    raise RuntimeError("download is html (not audio)")

                size = r.headers.get("Content-Length")
                if size:
                    try:
                        b = int(size)
                        if b > MUSIC_DL_MAX_MB * 1024 * 1024:
                            raise RuntimeError("file too large")
                    except Exception:
                        pass

                data = await r.read()
                if len(data) > MUSIC_DL_MAX_MB * 1024 * 1024:
                    raise RuntimeError("file too large (read)")

                name = src.split("/")[-1].split("?")[0] or "track"
                if "." not in name:
                    name += ".mp3"

                buf = BufferedInputFile(data, filename=name)
                await bot.send_audio(chat_id=chat_id, audio=buf, caption=caption)
                return

    except Exception:
        await bot.send_message(chat_id=chat_id, text=f"🎧 Не получилось отправить файлом. Вот ссылка:\n{src}")
