# app/bot.py
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.config import settings


def _default_props() -> DefaultBotProperties:
    try:
        return DefaultBotProperties(
            parse_mode=ParseMode.HTML, link_preview_is_disabled=True
        )
    except TypeError:
        return DefaultBotProperties(parse_mode=ParseMode.HTML)


# ВАЖНО: timeout — это число секунд, а не ClientTimeout
_session = AiohttpSession(timeout=25)

bot = Bot(
    token=settings.tg_token,
    session=_session,
    default=_default_props(),
)

__all__ = ["bot"]
