from __future__ import annotations

import io
import json
import gzip
from datetime import datetime, timezone
from typing import Optional

from aiogram import Router
from aiogram.filters import StateFilter, Command
from aiogram.types import Message, BufferedInputFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.models.journal import JournalEntry
from app.models.reminder import Reminder
from app.models.bug_report import BugReport  # ВАЖНО: используем BugReport

router = Router()


# Простенькая локализация для 2 сообщений
_L10N = {
    "export_disabled": {
        "ru": "Экспорт временно выключен.",
        "uk": "Експорт тимчасово вимкнено.",
        "en": "Export is temporarily disabled.",
    },
    "press_start": {
        "ru": "Нажми /start",
        "uk": "Натисни /start",
        "en": "Press /start",
    },
}


def _t(lang: str, key: str, fallback: dict) -> str:
    """Простой безопасный перевод для локальных _L10N:
    без зависимостей от _BAD_I18N и app.i18n.
    """
    loc = (lang or "ru")[:2].lower()
    if loc == "ua":
        loc = "uk"
    return fallback.get(loc, fallback.get("ru", key))
def _ser_dt(dt: Optional[datetime]) -> Optional[str]:
    """ISO8601 UTC, безопасно для naive/aware дат."""
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@router.message(Command("export"))
async def export_data(m: Message, session: AsyncSession):
    # На проде можно выключать экспорт флагом
    if not settings.enable_exporter:
        # пытаемся показать на языке пользователя
        user_lang = getattr(getattr(m, "from_user", None), "language_code", None)
        return await m.answer(_t(user_lang, "export_disabled", _L10N["export_disabled"]))

    # Ищем пользователя по Telegram ID
    user = (
        await session.execute(select(User).where(User.tg_id == m.from_user.id))
    ).scalar_one_or_none()
    if not user:
        user_lang = getattr(getattr(m, "from_user", None), "language_code", None)
        return await m.answer(_t(user_lang, "press_start", _L10N["press_start"]))

    # Забираем все данные пользователя
    entries = (
        await session.execute(
            select(JournalEntry)
            .where(JournalEntry.user_id == user.id)
            .order_by(JournalEntry.id)
        )
    ).scalars().all()

    reminders = (
        await session.execute(
            select(Reminder).where(Reminder.user_id == user.id).order_by(Reminder.id)
        )
    ).scalars().all()

    reports = (
        await session.execute(
            select(BugReport).where(BugReport.user_id == user.id).order_by(BugReport.id)
        )
    ).scalars().all()

    payload = {
        "user": {
            "id": user.id,
            "tg_id": user.tg_id,
            "language": getattr(user, "language", None),
            "consent_accepted_at": _ser_dt(getattr(user, "consent_accepted_at", None)),
        },
        "journal": [
            {
                "id": e.id,
                "text": e.text,
                "created_at": _ser_dt(getattr(e, "created_at", None)),
            }
            for e in entries
        ],
        "reminders": [
            {
                "id": r.id,
                "text": r.text,
                "due_at": _ser_dt(getattr(r, "due_at", None)),
                "sent_at": _ser_dt(getattr(r, "sent_at", None)),
            }
            for r in reminders
        ],
        "reports": [
            {
                "id": r.id,
                "text": r.text,
                "created_at": _ser_dt(getattr(r, "created_at", None)),
            }
            for r in reports
        ],
        "meta": {
            "exported_at": _ser_dt(datetime.now(timezone.utc)),
            "counts": {
                "journal": len(entries),
                "reminders": len(reminders),
                "reports": len(reports),
            },
            "version": "1",
        },
    }

    # Формируем имя файла
    fname_base = f"diary_export_{user.tg_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    # Телеграм-лимит для ботов ~50 МБ. Подстрахуемся gzip’ом, если > 8 МБ.
    if len(raw) > 8 * 1024 * 1024:
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(raw)
        data = buf.getvalue()
        fname = f"{fname_base}.json.gz"
    else:
        data = raw
        fname = f"{fname_base}.json"

    await m.answer_document(BufferedInputFile(data, filename=fname))