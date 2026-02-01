from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentStatus
from app.models.subscription import Subscription
from app.models.user import User


@dataclass
class RefundResult:
    ok: bool
    msg: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def request_refund(
    session: AsyncSession,
    tg_id: int,
    payment_id: int,
    reason: str = "",
) -> RefundResult:
    user = (
        await session.execute(select(User).where(User.tg_id == tg_id))
    ).scalar_one_or_none()
    if not user:
        return RefundResult(False, "❌ Пользователь не найден. Нажми /start")

    pay = (
        await session.execute(select(Payment).where(Payment.id == payment_id))
    ).scalar_one_or_none()
    if not pay:
        return RefundResult(False, "❌ Платёж не найден")

    # если есть user_id в Payment — проверим принадлежность
    pay_user_id = getattr(pay, "user_id", None)
    if pay_user_id is not None and int(pay_user_id) != int(user.id):
        return RefundResult(False, "❌ Это не твой платёж")

    status_obj = getattr(pay, "status", None)
    st = (
        getattr(status_obj, "value", None)
        or getattr(status_obj, "name", None)
        or str(status_obj)
        or ""
    ).lower()

    # если уже возвращён — повторно не создаём заявку
    if st == "refunded":
        return RefundResult(
            False,
            "ℹ️ Этот платёж уже отмечен как REFUNDED. Повторный возврат не требуется.",
        )

    # если заявка уже была создана — сообщим
    raw_payload = getattr(pay, "payload", None)
    if isinstance(raw_payload, str) and raw_payload.strip():
        try:
            _p = json.loads(raw_payload)
            if isinstance(_p, dict) and _p.get("refund_status") == "requested":
                return RefundResult(
                    False, "ℹ️ Заявка на возврат уже создана и находится в обработке."
                )
        except Exception:
            pass

    if st not in ("paid", "succeeded", "success"):
        return RefundResult(
            False,
            f"❌ Возврат возможен только для paid-платежей. Текущий статус: {status_obj}",
        )

    # пометим запрос (best-effort: если полей нет — ничего не ломаем)
    changed = False
    pay.refund_requested_at = _now_utc()
    changed = True
    pay.refund_reason = (reason or "").strip()[:500] if reason else None
    changed = True
    pay.refund_status = "requested"
    changed = True
    # fallback: если нет отдельных полей — положим в payload (TEXT -> JSON string)
    if not changed:
        raw = getattr(pay, "payload", None)

        payload: dict = {}
        if isinstance(raw, dict):
            payload = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                payload = {}
        else:
            payload = {}

        payload["refund_requested_at"] = _now_utc().isoformat()
        payload["refund_reason"] = (reason or "").strip()[:500]
        payload["refund_status"] = "requested"

        setattr(pay, "payload", json.dumps(payload, ensure_ascii=False))

    await session.commit()
    return RefundResult(
        True, "✅ Запрос на возврат создан. Админ рассмотрит и ответит."
    )


async def approve_refund(
    session: AsyncSession,
    payment_id: int,
    admin_note: str = "",
) -> RefundResult:
    pay = (
        await session.execute(select(Payment).where(Payment.id == payment_id))
    ).scalar_one_or_none()
    if not pay:
        return RefundResult(False, "❌ Платёж не найден")
    # 1) payment.status -> refunded
    pay.status = PaymentStatus.REFUNDED

    # 2) payment.paid_at/ refunded_at (best-effort)
    pay.refunded_at = _now_utc()
    pay.refund_status = "approved"
    pay.refund_admin_note = (admin_note or "").strip()[:500] if admin_note else None

    # payload fallback (TEXT -> JSON string)
    raw = getattr(pay, "payload", None)
    payload: dict = {}
    if isinstance(raw, dict):
        payload = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            import json

            payload = json.loads(raw)
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
    payload["refund_status"] = "approved"
    payload["refund_admin_note"] = (admin_note or "").strip()[:500]
    payload["refunded_at"] = _now_utc().isoformat()
    import json

    setattr(pay, "payload", json.dumps(payload, ensure_ascii=False))

    # 3) close subscription if exists
    sub_id = getattr(pay, "subscription_id", None)
    if sub_id:
        sub = (
            await session.execute(select(Subscription).where(Subscription.id == sub_id))
        ).scalar_one_or_none()
        if sub and hasattr(sub, "status"):
            setattr(sub, "status", "cancelled")
        if sub and hasattr(sub, "expires_at"):
            setattr(sub, "expires_at", _now_utc())

    await session.commit()
    return RefundResult(
        True, "✅ Возврат подтверждён: payment=refunded, подписка закрыта (если была)."
    )
