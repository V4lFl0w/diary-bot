from __future__ import annotations

from typing import Optional

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.refund_flow import request_refund, approve_refund
from app.services.admin_audit import log_admin_action

router = Router(name="refund")


@router.message(Command("refund"))
async def cmd_refund(m: Message, session: AsyncSession, lang: Optional[str] = None):
    """
    /refund <payment_id> [reason...]
    """
    if not m.from_user:
        return
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer("Формат: /refund payment_id причина(optional)")
        return

    pid = int(parts[1])
    reason = parts[2] if len(parts) > 2 else ""
    r = await request_refund(session, m.from_user.id, pid, reason=reason)
    await m.answer(r.msg)


@router.message(Command("refund_approve"))
async def cmd_refund_approve(m: Message, session: AsyncSession, lang: Optional[str] = None):
    """
    MVP админ-команда:
    /refund_approve <payment_id> [note...]
    (дальше перенесёшь в админ-панель)
    """
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer("Формат: /refund_approve payment_id note(optional)")
        return

    pid = int(parts[1])
    note = parts[2] if len(parts) > 2 else ""
    r = await approve_refund(session, pid, admin_note=note)
    await m.answer(r.msg)

    if getattr(r, "ok", False):
        await log_admin_action(
            session,
            admin_tg_id=m.from_user.id,
            action="refund_approve",
            payment_id=pid,
            extra={"note": note},
        )


__all__ = ["router"]
