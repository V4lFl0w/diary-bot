from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram import Router, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.payment import Payment, PaymentStatus, PaymentProvider
from aiogram.fsm.context import FSMContext
from app.services.refund_flow import request_refund, approve_refund
from app.services.admin_audit import log_admin_action


router = Router(name="refund_ui")

CB_PREFIX = "refund"
CB_PICK = f"{CB_PREFIX}:pick:"          # refund:pick:<id>
CB_REASON = f"{CB_PREFIX}:reason:"      # refund:reason:<id>:<kind>

AUTO_OK_HOURS = int(os.getenv("REFUND_AUTO_OK_HOURS", "48"))          # 48h
AUTO_DENY_DAYS = int(os.getenv("REFUND_AUTO_DENY_DAYS", "14"))        # 14d

KEYWORDS_OK = (
    "случайн", "ошибк", "не понрав", "не зайшл", "не зашло", "передумал",
    "ошибочно", "случайно"
)

# -------- utils --------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _admin_ids() -> list[int]:
    raw = (os.getenv("ADMIN_TG_ID") or "").strip()
    if not raw:
        return []
    out: list[int] = []
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.append(int(x))
        except Exception:
            continue
    return out

def _t(lang: str, ru: str, uk: str, en: str) -> str:
    return {"ru": ru, "uk": uk, "en": en}.get(lang, ru)

def _refund_btn_text(lang: str) -> str:
    return _t(lang, "💸 Возврат", "💸 Повернення", "💸 Refund")

def _looks_auto_ok(text: str) -> bool:
    s = (text or "").lower()
    return any(k in s for k in KEYWORDS_OK)

def _refund_info(provider: str, lang: str) -> str:
    p = (provider or "").lower()
    if p == "stars":
        return _t(lang,
                  "⭐ Возврат вернётся в Telegram Stars. Обычно несколько минут.",
                  "⭐ Повернення прийде в Telegram Stars. Зазвичай кілька хвилин.",
                  "⭐ Refund returns to Telegram Stars. Usually a few minutes.")
    if p == "mono":
        return _t(lang,
                  "💳 Возврат придёт на ту же карту (MonoPay). Обычно 1–5 рабочих дней.",
                  "💳 Повернення прийде на ту ж картку (MonoPay). Зазвичай 1–5 робочих днів.",
                  "💳 Refund returns to the same card (MonoPay). Usually 1–5 business days.")
    if p == "crypto":
        return _t(lang,
                  "🪙 Возврат по крипте делаем вручную. Нужен адрес USDT TRC20. Обычно 24–72 часа.",
                  "🪙 Повернення криптою робимо вручну. Потрібна адреса USDT TRC20. Зазвичай 24–72 години.",
                  "🪙 Crypto refunds are processed manually. USDT TRC20 address required. Usually 24–72 hours.")
    return _t(lang,
              "ℹ️ Возврат будет обработан по правилам платёжного провайдера.",
              "ℹ️ Повернення буде оброблено за правилами платіжного провайдера.",
              "ℹ️ Refund will be processed according to the payment provider rules.")

async def _get_lang(session: AsyncSession, tg_id: int) -> str:
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        return "ru"
    l = (getattr(u, "lang", None) or "ru").lower()
    if l == "ua":
        l = "uk"
    if l not in ("ru", "uk", "en"):
        l = "ru"
    return l

async def _list_recent_paid(session: AsyncSession, tg_id: int, limit: int = 5) -> list[Payment]:
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        return []
    q = (
        select(Payment)
        .where(Payment.user_id == u.id)
        .where(Payment.status == PaymentStatus.PAID)
        .order_by(Payment.paid_at.desc().nulls_last(), Payment.id.desc())
        .limit(limit)
    )
    return list((await session.execute(q)).scalars().all())

def _kb_pick(payments: list[Payment], lang: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in payments:
        label = f"#{p.id} • {p.provider} • {p.amount}{p.currency}"
        if p.paid_at:
            label += f" • {p.paid_at.date().isoformat()}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{CB_PICK}{p.id}")])
    rows.append([InlineKeyboardButton(text=_t(lang, "↩️ Назад", "↩️ Назад", "↩️ Back"), callback_data="refund:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_reason(payment_id: int, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=_t(lang, "😬 Случайно оплатил", "😬 Випадково оплатив", "😬 Paid by mistake"),
            callback_data=f"{CB_REASON}{payment_id}:mistake"
        )],
        [InlineKeyboardButton(
            text=_t(lang, "😕 Не понравилось", "😕 Не сподобалось", "😕 Didn't like it"),
            callback_data=f"{CB_REASON}{payment_id}:dislike"
        )],
        [InlineKeyboardButton(
            text=_t(lang, "🧾 Другое (создать заявку)", "🧾 Інше (створити заявку)", "🧾 Other (create request)"),
            callback_data=f"{CB_REASON}{payment_id}:other"
        )],
        [InlineKeyboardButton(text=_t(lang, "↩️ Назад", "↩️ Назад", "↩️ Back"), callback_data="refund:back:pick:")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _deny_payload(session: AsyncSession, pay: Payment, *, reason: str, code: str) -> None:
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
    payload["refund_status"] = "denied"
    payload["refund_denied_code"] = code
    payload["refund_denied_reason"] = (reason or "")[:500]
    payload["refund_denied_at"] = _now_utc().isoformat()
    pay.payload = json.dumps(payload, ensure_ascii=False)
    await session.commit()

def _prov_low(pay: Payment) -> str:
    prov = getattr(pay, "provider", None)
    prov = prov.value if hasattr(prov, "value") else str(prov or "")
    return (prov or "").lower()

# -------- entry point (кнопка/команда) --------

@router.message(F.text.in_({"💸 Возврат", "💸 Возврат средств", "💸 Повернення", "💸 Повернення коштів", "💸 Refund"}))
async def refund_open(m: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    tg_id = m.from_user.id
    lang = await _get_lang(session, tg_id)

    pays = await _list_recent_paid(session, tg_id, limit=5)
    if not pays:
        await m.answer(_t(lang,
            "Пока не вижу оплаченных платежей, по которым можно сделать возврат.",
            "Поки не бачу оплачених платежів для повернення.",
            "I can't find paid payments eligible for refund."))
        return

    await m.answer(_t(lang, "Выбери платеж:", "Обери платіж:", "Pick a payment:"), reply_markup=_kb_pick(pays, lang))

@router.callback_query(F.data == "refund:open")
async def refund_open_cb(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await c.answer()
    await state.clear()

    tg_id = c.from_user.id
    lang = await _get_lang(session, tg_id)

    pays = await _list_recent_paid(session, tg_id, limit=5)
    if not pays:
        await c.message.answer(_t(lang,
            "Пока не вижу оплаченных платежей, по которым можно сделать возврат.",
            "Поки не бачу оплачених платежів для повернення.",
            "I can't find paid payments eligible for refund."))
        return

    await c.message.answer(_t(lang, "Выбери платеж:", "Обери платіж:", "Pick a payment:"), reply_markup=_kb_pick(pays, lang))

# -------- callbacks --------

@router.callback_query(F.data == "refund:close")
async def refund_close(c: CallbackQuery) -> None:
    await c.answer()
    try:
        await c.message.delete()
    except Exception:
        pass

@router.callback_query(F.data.startswith(CB_PICK))
async def refund_pick(c: CallbackQuery, session: AsyncSession) -> None:
    await c.answer()
    tg_id = c.from_user.id
    lang = await _get_lang(session, tg_id)

    payment_id = int(c.data[len(CB_PICK):])
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    pay = (await session.execute(select(Payment).where(Payment.id == payment_id))).scalar_one_or_none()

    if not u or not pay or int(getattr(pay, "user_id", 0) or 0) != int(u.id):
        await c.message.answer(_t(lang, "Платёж не найден.", "Платіж не знайдено.", "Payment not found."))
        return

    await c.message.edit_text(_t(lang,
                                 f"Платёж #{payment_id}. Выбери причину:",
                                 f"Платіж #{payment_id}. Обери причину:",
                                 f"Payment #{payment_id}. Choose a reason:"),
                              reply_markup=_kb_reason(payment_id, lang))

@router.callback_query(F.data.startswith(CB_REASON))
async def refund_reason(c: CallbackQuery, session: AsyncSession) -> None:
    await c.answer()
    tg_id = c.from_user.id
    lang = await _get_lang(session, tg_id)

    parts = (c.data or "").split(":")
    # refund:reason:<id>:<kind>
    if len(parts) < 4:
        await c.message.answer(_t(lang, "Ошибка данных.", "Помилка даних.", "Bad data."))
        return

    try:
        payment_id = int(parts[2])
    except Exception:
        await c.message.answer(_t(lang, "Ошибка ID платежа.", "Помилка ID платежу.", "Bad payment id."))
        return

    kind = parts[3].strip().lower()
    reason_text = {
        "mistake": _t(lang, "случайно оплатил", "випадково оплатив", "paid by mistake"),
        "dislike": _t(lang, "не понравилось", "не сподобалось", "didn't like it"),
        "other": _t(lang, "другое", "інше", "other"),
    }.get(kind, "other")

    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    pay = (await session.execute(select(Payment).where(Payment.id == payment_id))).scalar_one_or_none()
    if not u or not pay or int(getattr(pay, "user_id", 0) or 0) != int(u.id):
        await c.message.answer(_t(lang, "Платёж не найден.", "Платіж не знайдено.", "Payment not found."))
        return

    if pay.status == PaymentStatus.REFUNDED:
        await c.message.answer(_t(lang, "Этот платёж уже возвращён.", "Цей платіж уже повернений.", "This payment is already refunded."))
        return

    paid_at = getattr(pay, "paid_at", None)
    if not paid_at:
        res = await request_refund(session, tg_id=tg_id, payment_id=payment_id, reason=reason_text)
        await c.message.answer(res.msg)
        return

    now = _now_utc()
    age = now - _as_utc(paid_at)
    auto_ok = age <= timedelta(hours=AUTO_OK_HOURS) and _looks_auto_ok(reason_text)
    auto_deny = age >= timedelta(days=AUTO_DENY_DAYS)

    prov_low = _prov_low(pay)

    # слишком поздно
    if auto_deny:
        await _deny_payload(session, pay, reason=reason_text, code="too_late")

        await log_admin_action(
            session,
            admin_tg_id=c.from_user.id,
            action="refund_deny_ui",
            payment_id=payment_id,
            target_tg_id=tg_id,
            extra={"reason": reason_text, "code": "too_late"},
        )
        await c.message.answer(_t(lang,
                                  f"❌ Возврат недоступен: прошло больше {AUTO_DENY_DAYS} дней с момента оплаты.",
                                  f"❌ Повернення недоступне: минуло більше {AUTO_DENY_DAYS} днів з моменту оплати.",
                                  f"❌ Refund is not available: more than {AUTO_DENY_DAYS} days have passed since payment."))
        return

    # авто-ок (48 часов) — делаем по провайдеру
    if auto_ok:
        # ⭐ Stars — пытаемся вернуть stars + закрываем доступ
        if prov_low == "stars":
            charge_id = getattr(pay, "external_id", None) or ""
            if charge_id:
                try:
                    await c.bot.refund_star_payment(user_id=tg_id, telegram_payment_charge_id=charge_id)
                except Exception:
                    # даже если TG refund упал — мы не падаем, но всё равно пометим и закроем доступ
                    pass
            r = await approve_refund(session, payment_id=payment_id, admin_note=f"auto_ok:{reason_text}")
            if getattr(r, "ok", False):
                await log_admin_action(
                    session,
                    admin_tg_id=c.from_user.id,
                    action="refund_approve_ui",
                    payment_id=payment_id,
                    extra={"reason": reason_text},
                )
            await c.message.answer(_t(lang,
                                      "✅ Возврат одобрен.\n" + _refund_info("stars", lang),
                                      "✅ Повернення схвалено.\n" + _refund_info("stars", lang),
                                      "✅ Refund approved.\n" + _refund_info("stars", lang)))
            return

        # 💳 Mono — заявка (реальный refund делается через провайдера/банк)
        if prov_low == "mono":
            await request_refund(session, tg_id=tg_id, payment_id=payment_id, reason=reason_text)
            await log_admin_action(
                session,
                admin_tg_id=c.from_user.id,
                action="refund_request_ui",
                payment_id=payment_id,
                target_tg_id=tg_id,
                extra={"provider": "mono", "reason": reason_text},
            )
            await c.message.answer(_t(lang,
                                      "✅ Заявка на возврат создана.\n" + _refund_info("mono", lang),
                                      "✅ Заявку на повернення створено.\n" + _refund_info("mono", lang),
                                      "✅ Refund request created.\n" + _refund_info("mono", lang)))
            return

        # 🪙 Crypto — заявка + просим адрес
        if prov_low == "crypto":
            await request_refund(session, tg_id=tg_id, payment_id=payment_id, reason=reason_text)
            await log_admin_action(
                session,
                admin_tg_id=c.from_user.id,
                action="refund_request_ui",
                payment_id=payment_id,
                target_tg_id=tg_id,
                extra={"provider": "crypto", "reason": reason_text},
            )
            await c.message.answer(_t(lang,
                                      "✅ Заявка создана.\n" + _refund_info("crypto", lang) + "\n\nОтправь адрес USDT TRC20 одним сообщением (начинается с T...).",
                                      "✅ Заявку створено.\n" + _refund_info("crypto", lang) + "\n\nНадішли адресу USDT TRC20 одним повідомленням (починається з T...).",
                                      "✅ Request created.\n" + _refund_info("crypto", lang) + "\n\nSend your USDT TRC20 address in one message (starts with T...)."))
            return

    # серый кейс → заявка + пинг админу
    res = await request_refund(session, tg_id=tg_id, payment_id=payment_id, reason=reason_text)
    await c.message.answer(_t(lang,
                              "✅ Заявка создана. Обычно ответ приходит быстро.\n" + _refund_info(prov_low, lang),
                              "✅ Заявку створено. Зазвичай відповідь приходить швидко.\n" + _refund_info(prov_low, lang),
                              "✅ Request created. Usually reviewed quickly.\n" + _refund_info(prov_low, lang)))

    admins = _admin_ids()
    if admins:
        txt = f"🧾 Refund request\nuser_tg={tg_id}\npayment_id={payment_id}\nreason={reason_text}\nage_days={age.days}\nprovider={prov_low}"
        for aid in admins:
            try:
                await c.bot.send_message(aid, txt)
            except Exception:
                pass

@router.callback_query(F.data.startswith("refund:back:pick:"))
async def refund_back_to_pick(c: CallbackQuery, session: AsyncSession) -> None:
    await c.answer()
    tg_id = c.from_user.id
    lang = await _get_lang(session, tg_id)

    pays = await _list_recent_paid(session, tg_id, limit=5)
    if not pays:
        await c.message.edit_text(_t(lang,
            "Пока не вижу оплаченных платежей, по которым можно сделать возврат.",
            "Поки не бачу оплачених платежів для повернення.",
            "I can't find paid payments eligible for refund."), reply_markup=None)
        return

    await c.message.edit_text(_t(lang,
                                 "Выбери платёж для возврата:",
                                 "Обери платіж для повернення:",
                                 "Pick a payment to refund:"),
                              reply_markup=_kb_pick(pays, lang))

# -------- crypto refund address capture --------

def _is_trc20_address(text: str) -> bool:
    s = (text or "").strip()
    # TRON base58 адрес обычно 34 символа и начинается с T
    if not s.startswith("T"):
        return False
    if len(s) < 33 or len(s) > 36:
        return False
    # мягкая проверка (без полной base58 валидации)
    return True


@router.message(F.text & ~F.text.startswith("/"))
async def refund_crypto_address_capture(m: Message, session: AsyncSession) -> None:
    """
    Если пользователь после crypto refund отправляет адрес (T...),
    то сохраняем его в payload платежа с refund_status='address_received'.
    """
    text = (m.text or "").strip()
    if not _is_trc20_address(text):
        raise SkipHandler

    tg_id = m.from_user.id
    lang = await _get_lang(session, tg_id)

    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        raise SkipHandler

    # Берём последние crypto-платежи пользователя и ищем тот, где refund_status == requested
    q = (
        select(Payment)
        .where(Payment.user_id == u.id)
        .where(Payment.provider == PaymentProvider.CRYPTO)
        .order_by(Payment.id.desc())
        .limit(25)
    )
    pays = list((await session.execute(q)).scalars().all())

    target: Optional[Payment] = None
    target_payload: dict = {}

    for p in pays:
        raw = getattr(p, "payload", None)
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
        # Ищем активную заявку, где ещё нет адреса
        if payload.get("refund_status") == "requested" and not payload.get("refund_address"):
            target = p
            target_payload = payload
            break

    if not target:
        await m.answer(_t(lang,
                          "Я вижу адрес, но не нашёл активной crypto-заявки на возврат. Сначала создай возврат через кнопку 💸 Возврат.",
                          "Бачу адресу, але не знайшов активної crypto-заявки. Спочатку створи повернення через кнопку 💸 Повернення.",
                          "I see the address, but I can't find an active crypto refund request. Create it via 💸 Refund first."))
        return

    target_payload["refund_address"] = text
    target_payload["refund_network"] = "TRC20"
    target_payload["refund_status"] = "address_received"
    target_payload["refund_address_received_at"] = _now_utc().isoformat()

    target.payload = json.dumps(target_payload, ensure_ascii=False)
    await session.commit()

    await m.answer(_t(lang,
                      "✅ Адрес получен. Передал в обработку. Обычно 24–72 часа.",
                      "✅ Адресу отримано. Передав в обробку. Зазвичай 24–72 години.",
                      "✅ Address received. Sent for processing. Usually 24–72 hours."))

    admins = _admin_ids()
    if admins:
        txt = (
            "🪙 Crypto refund address received\n"
            f"user_tg={tg_id}\n"
            f"payment_id={target.id}\n"
            f"address={text}\n"
            "network=TRC20"
        )
        for aid in admins:
            try:
                await m.bot.send_message(aid, txt)
            except Exception:
                pass
