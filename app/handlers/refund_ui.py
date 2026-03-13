from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Dict

from aiogram import F, Router, Bot
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentProvider, PaymentStatus
from app.models.user import User
from app.services.admin_audit import log_admin_action
from app.services.refund_flow import approve_refund, request_refund
from app.utils.aiogram_guards import is_message, safe_chat_id, safe_message_id

router = Router(name="refund_ui")

CB_PREFIX = "refund"
CB_PICK = f"{CB_PREFIX}:pick:"  # refund:pick:<id>
CB_REASON = f"{CB_PREFIX}:reason:"  # refund:reason:<id>:<kind>

AUTO_OK_HOURS = int(os.getenv("REFUND_AUTO_OK_HOURS", "48"))  # 48h
AUTO_DENY_DAYS = int(os.getenv("REFUND_AUTO_DENY_DAYS", "14"))  # 14d

KEYWORDS_OK = (
    "случайн",
    "ошибк",
    "не понрав",
    "не зайшл",
    "не зашло",
    "передумал",
    "ошибочно",
    "случайно",
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


async def _edit_cb_message(bot: Optional[Bot], cb: CallbackQuery, text: str, *, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    if bot is None:
        try:
            await cb.answer("Не могу обновить сообщение. Открой меню заново.")
        except Exception:
            pass
        return

    m = cb.message
    if m is None:
        await cb.answer("Не могу обновить сообщение. Открой меню заново.")
        return

    if is_message(m):
        await m.edit_text(text, reply_markup=reply_markup)
        return

    await bot.edit_message_text(
        chat_id=safe_chat_id(m),
        message_id=safe_message_id(m),
        text=text,
        reply_markup=reply_markup,
    )


async def _delete_cb_message(bot: Optional[Bot], cb: CallbackQuery) -> None:
    if bot is None:
        return
    m = cb.message
    if m is None:
        return

    if is_message(m):
        await m.delete()
        return

    await bot.delete_message(chat_id=safe_chat_id(m), message_id=safe_message_id(m))


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
        return _t(
            lang,
            "⭐ Возврат вернётся в Telegram Stars. Обычно несколько минут.",
            "⭐ Повернення прийде в Telegram Stars. Зазвичай кілька хвилин.",
            "⭐ Refund returns to Telegram Stars. Usually a few minutes.",
        )
    if p == "mono":
        return _t(
            lang,
            "💳 Возврат придёт на ту же карту (MonoPay). Обычно 1–5 рабочих дней.",
            "💳 Повернення прийде на ту ж картку (MonoPay). Зазвичай 1–5 робочих днів.",
            "💳 Refund returns to the same card (MonoPay). Usually 1–5 business days.",
        )
    if p == "crypto":
        return _t(
            lang,
            "🪙 Возврат по крипте делаем вручную. Нужен адрес USDT TRC20. Обычно 24–72 часа.",
            "🪙 Повернення криптою робимо вручну. Потрібна адреса USDT TRC20. Зазвичай 24–72 години.",
            "🪙 Crypto refunds are processed manually. USDT TRC20 address required. Usually 24–72 hours.",
        )
    return _t(
        lang,
        "ℹ️ Возврат будет обработан по правилам платёжного провайдера.",
        "ℹ️ Повернення буде оброблено за правилами платіжного провайдера.",
        "ℹ️ Refund will be processed according to the payment provider rules.",
    )


async def _get_lang(session: AsyncSession, tg_id: int) -> str:
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if not u:
        return "ru"
    lang_code = (getattr(u, "lang", None) or "ru").lower()
    if lang_code == "ua":
        lang_code = "uk"
    if lang_code not in ("ru", "uk", "en"):
        lang_code = "ru"
    return lang_code


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
        label = f"#{p.id} • {getattr(p.provider, 'value', str(p.provider))} • {p.amount}{p.currency}"
        if p.paid_at:
            label += f" • {p.paid_at.date().isoformat()}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{CB_PICK}{p.id}")])
    rows.append(
        [
            InlineKeyboardButton(
                text=_t(lang, "↩️ Назад", "↩️ Назад", "↩️ Back"),
                callback_data="refund:close",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_reason(payment_id: int, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=_t(
                    lang,
                    "😬 Случайно оплатил",
                    "😬 Випадково оплатив",
                    "😬 Paid by mistake",
                ),
                callback_data=f"{CB_REASON}{payment_id}:mistake",
            )
        ],
        [
            InlineKeyboardButton(
                text=_t(lang, "😕 Не понравилось", "😕 Не сподобалось", "😕 Didn't like it"),
                callback_data=f"{CB_REASON}{payment_id}:dislike",
            )
        ],
        [
            InlineKeyboardButton(
                text=_t(
                    lang,
                    "🧾 Другое (создать заявку)",
                    "🧾 Інше (створити заявку)",
                    "🧾 Other (create request)",
                ),
                callback_data=f"{CB_REASON}{payment_id}:other",
            )
        ],
        [
            InlineKeyboardButton(
                text=_t(lang, "↩️ Назад", "↩️ Назад", "↩️ Back"),
                callback_data="refund:back:pick:",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _deny_payload(session: AsyncSession, pay: Payment, *, reason: str, code: str) -> None:
    raw = getattr(pay, "payload", None)
    payload: dict[str, Any] = {}
    if isinstance(raw, dict):
        payload = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            pass
    payload["refund_status"] = "denied"
    payload["refund_denied_code"] = code
    payload["refund_denied_reason"] = (reason or "")[:500]
    payload["refund_denied_at"] = _now_utc().isoformat()
    pay.payload = json.dumps(payload, ensure_ascii=False)
    await session.commit()


def _prov_low(pay: Payment) -> str:
    prov = getattr(pay, "provider", None)
    prov_val = prov.value if hasattr(prov, "value") else str(prov or "")
    return (prov_val or "").lower()


# -------- entry point (кнопка/команда) --------


@router.message(
    F.text.in_(
        {
            "💸 Возврат",
            "💸 Возврат средств",
            "💸 Повернення",
            "💸 Повернення коштів",
            "💸 Refund",
        }
    )
)
async def refund_open(m: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    tg_id = m.from_user.id
    lang = await _get_lang(session, tg_id)

    pays = await _list_recent_paid(session, tg_id, limit=5)
    if not pays:
        await m.answer(
            _t(
                lang,
                "Пока не вижу оплаченных платежей, по которым можно сделать возврат.",
                "Поки не бачу оплачених платежів для повернення.",
                "I can't find paid payments eligible for refund.",
            )
        )
        return

    await m.answer(
        _t(lang, "Выбери платеж:", "Обери платіж:", "Pick a payment:"),
        reply_markup=_kb_pick(pays, lang),
    )


@router.callback_query(F.data == "refund:open")
async def refund_open_cb(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await c.answer()
    await state.clear()

    bot = c.bot
    if not bot:
        return

    tg_id = c.from_user.id
    lang = await _get_lang(session, tg_id)

    pays = await _list_recent_paid(session, tg_id, limit=5)
    if not pays:
        await bot.send_message(
            tg_id,
            _t(
                lang,
                "Пока не вижу оплаченных платежей, по которым можно сделать возврат.",
                "Поки не бачу оплачених платежів для повернення.",
                "I can't find paid payments eligible for refund.",
            ),
        )
        return

    await bot.send_message(
        tg_id,
        _t(lang, "Выбери платеж:", "Обери платіж:", "Pick a payment:"),
        reply_markup=_kb_pick(pays, lang),
    )


# -------- callbacks --------


@router.callback_query(F.data == "refund:close")
async def refund_close(c: CallbackQuery) -> None:
    await c.answer()
    try:
        await _delete_cb_message(c.bot, c)
    except Exception:
        pass


@router.callback_query(F.data.startswith(CB_PICK))
async def refund_pick(c: CallbackQuery, session: AsyncSession) -> None:
    await c.answer()
    bot = c.bot
    if not bot:
        return

    tg_id = c.from_user.id
    lang = await _get_lang(session, tg_id)

    data = c.data or ""
    payment_id = int(data[len(CB_PICK) :])
    u = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    pay = (await session.execute(select(Payment).where(Payment.id == payment_id))).scalar_one_or_none()

    if not u or not pay or int(getattr(pay, "user_id", 0) or 0) != int(u.id):
        await bot.send_message(
            tg_id,
            _t(lang, "Платёж не найден.", "Платіж не знайдено.", "Payment not found."),
        )
        return

    await _edit_cb_message(
        bot,
        c,
        _t(
            lang,
            f"Платёж #{payment_id}. Выбери причину:",
            f"Платіж #{payment_id}. Обери причину:",
            f"Payment #{payment_id}. Choose a reason:",
        ),
        reply_markup=_kb_reason(payment_id, lang),
    )


@router.callback_query(F.data.startswith(CB_REASON))
async def refund_reason(c: CallbackQuery, session: AsyncSession) -> None:
    await c.answer()
    bot = c.bot
    if not bot:
        return

    tg_id = c.from_user.id
    lang = await _get_lang(session, tg_id)

    parts = (c.data or "").split(":")
    # refund:reason:<id>:<kind>
    if len(parts) < 4:
        await bot.send_message(tg_id, _t(lang, "Ошибка данных.", "Помилка даних.", "Bad data."))
        return

    try:
        payment_id = int(parts[2])
    except Exception:
        await bot.send_message(
            tg_id,
            _t(lang, "Ошибка ID платежа.", "Помилка ID платежу.", "Bad payment id."),
        )
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
        await bot.send_message(
            tg_id,
            _t(lang, "Платёж не найден.", "Платіж не знайдено.", "Payment not found."),
        )
        return

    if pay.status == PaymentStatus.REFUNDED:
        await bot.send_message(
            tg_id,
            _t(
                lang,
                "Этот платёж уже возвращён.",
                "Цей платіж уже повернений.",
                "This payment is already refunded.",
            ),
        )
        return

    paid_at = getattr(pay, "paid_at", None)
    if paid_at is None:
        res = await request_refund(session, tg_id=tg_id, payment_id=payment_id, reason=reason_text)
        await bot.send_message(tg_id, res.msg)
        return

    now = _now_utc()
    age = now - _as_utc(paid_at)

    # ЖЕСТКИЙ СТОП: Если прошло больше 48 часов — никаких возвратов вообще
    if age > timedelta(hours=AUTO_OK_HOURS):
        await _deny_payload(session, pay, reason=reason_text, code="too_late_48h")

        await log_admin_action(
            session,
            admin_tg_id=c.from_user.id,
            action="refund_deny_ui",
            payment_id=payment_id,
            target_tg_id=tg_id,
            extra={"reason": reason_text, "code": "too_late_48h"},
        )
        await bot.send_message(
            tg_id,
            _t(
                lang,
                "ℹ️ Возврат средств возможен только в течение 48 часов с момента оплаты. К сожалению, этот период уже прошел, поэтому возврат недоступен.",
                "ℹ️ Повернення коштів можливе лише протягом 48 годин з моменту оплати. На жаль, цей період уже минув, тому повернення недоступне.",
                "ℹ️ Refunds are only available within the first 48 hours after payment. Unfortunately, this period has passed.",
            ),
        )
        return

    # Если мы здесь, значит прошло МЕНЬШЕ 48 часов.
    auto_ok = _looks_auto_ok(reason_text)
    prov_low = _prov_low(pay)

    # авто-ок (48 часов) — делаем по провайдеру
    if auto_ok:
        # ⭐ Stars — пытаемся вернуть stars + закрываем доступ (ОБНОВЛЕНО!)
        if prov_low == "stars":
            charge_id = getattr(pay, "external_id", None) or ""
            refund_success = False
            stars_err = ""
            
            if charge_id:
                try:
                    # refund_star_payment возвращает True в случае успеха
                    res_stars = await bot.refund_star_payment(user_id=tg_id, telegram_payment_charge_id=str(charge_id))
                    if res_stars:
                        refund_success = True
                except Exception as e:
                    stars_err = repr(e)
            else:
                stars_err = "No charge_id"

            if refund_success:
                r = await approve_refund(session, payment_id=payment_id, admin_note=f"auto_ok:{reason_text}")
                if getattr(r, "ok", False):
                    # 🔥 ОТМЕНА ПРЕМИУМА
                    u.is_premium = False
                    u.premium_until = None
                    if hasattr(u, "premium_plan"):
                        u.premium_plan = "free"
                    if hasattr(u, "plan"):
                        u.plan = "basic"
                    if hasattr(u, "assistant_plan"):
                        u.assistant_plan = "basic"
                    await session.commit()

                    await log_admin_action(
                        session,
                        admin_tg_id=c.from_user.id,
                        action="refund_approve_ui",
                        payment_id=payment_id,
                        extra={"reason": reason_text},
                    )
                await bot.send_message(
                    tg_id,
                    _t(
                        lang,
                        "✅ Возврат одобрен. Ваша Premium-подписка отменена.\n" + _refund_info("stars", lang),
                        "✅ Повернення схвалено. Ваша Premium-підписка скасована.\n" + _refund_info("stars", lang),
                        "✅ Refund approved. Your Premium subscription has been canceled.\n" + _refund_info("stars", lang),
                    ),
                )
            else:
                # Fallback: автоматический возврат провалился -> ручная заявка
                await request_refund(session, tg_id=tg_id, payment_id=payment_id, reason=reason_text)
                await bot.send_message(
                    tg_id,
                    _t(lang, 
                       f"⚠️ Заявка создана, но автоматический возврат не прошел (Ошибка: {stars_err[:100]}). Админ проверит вручную.",
                       f"⚠️ Заявку створено, але автоматичне повернення не пройшло (Помилка: {stars_err[:100]}). Адмін перевірить вручну.",
                       f"⚠️ Request created, but auto-refund failed (Error: {stars_err[:100]}). Admin will check manually.")
                )
                
                admins = _admin_ids()
                if admins:
                    txt = f"🧾 Refund FAILED in Stars API\nuser_tg={tg_id}\npayment_id={payment_id}\nreason={reason_text}\nerror={stars_err[:100]}"
                    for aid in admins:
                        try:
                            await bot.send_message(aid, txt)
                        except Exception:
                            pass
            return

        # 💳 Mono — РЕАЛЬНЫЙ ВОЗВРАТ ЧЕРЕЗ API
        if prov_low == "mono":
            mono_token = str(os.getenv("MONO_TOKEN") or os.getenv("MONOBANK_TOKEN") or os.getenv("MONO_API_TOKEN") or "")
            invoice_id = getattr(pay, "external_id", None)
            amount_raw = getattr(pay, "amount_cents", None)
            
            refund_success = False
            mono_err_text = ""
            
            if mono_token and invoice_id:
                try:
                    payload_data: Dict[str, Any] = {"invoiceId": str(invoice_id)}
                    
                    if amount_raw is not None:
                        try:
                            payload_data["amount"] = int(float(str(amount_raw)))
                        except (ValueError, TypeError):
                            pass

                    import httpx
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.post(
                            "https://api.monobank.ua/api/merchant/invoice/cancel",
                            headers={"X-Token": mono_token},
                            json=payload_data
                        )
                        if resp.status_code == 200:
                            refund_success = True
                        else:
                            mono_err_text = f"API {resp.status_code}: {resp.text}"
                except Exception as e:
                    mono_err_text = f"Code Exception: {repr(e)}"
            else:
                mono_err_text = f"Missing Data: token={bool(mono_token)}, inv_id={invoice_id}"

            if refund_success:
                await approve_refund(session, payment_id=payment_id, admin_note=f"auto_mono_refund:{reason_text}")
                
                # 🔥 ОТМЕНА ПРЕМИУМА
                u.is_premium = False
                u.premium_until = None
                if hasattr(u, "premium_plan"):
                    u.premium_plan = "free"
                if hasattr(u, "plan"):
                    u.plan = "basic"
                if hasattr(u, "assistant_plan"):
                    u.assistant_plan = "basic"
                await session.commit()

                await log_admin_action(session, admin_tg_id=c.from_user.id, action="refund_approve_ui", payment_id=payment_id)
                await bot.send_message(
                    tg_id,
                    _t(lang, "✅ Возврат успешно проведен. Деньги скоро вернутся на карту.\nВаша Premium-подписка отменена.", 
                             "✅ Повернення успішно проведено. Гроші скоро повернуться на картку.\nВаша Premium-підписка скасована.", 
                             "✅ Refund processed. Money will return to your card soon.\nYour Premium subscription has been canceled.")
                )
            else:
                await request_refund(session, tg_id=tg_id, payment_id=payment_id, reason=reason_text)
                await bot.send_message(
                    tg_id,
                    _t(lang, f"⚠️ Заявка создана, но автоматический возврат не прошел (Банк: {mono_err_text[:100]}). Админ проверит вручную.",
                             f"⚠️ Заявку створено, але автоматичне повернення не пройшло (Банк: {mono_err_text[:100]}). Адмін перевірить вручну.",
                             f"⚠️ Request created, but auto-refund failed (Bank: {mono_err_text[:100]}). Admin will check manually.")
                )
                
                admins = _admin_ids()
                if admins:
                    txt = f"🧾 Refund FAILED in Mono API\nuser_tg={tg_id}\npayment_id={payment_id}\nreason={reason_text}\nerror={mono_err_text[:100]}"
                    for aid in admins:
                        try:
                            await bot.send_message(aid, txt)
                        except Exception:
                            pass
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
            await bot.send_message(
                tg_id,
                _t(
                    lang,
                    "✅ Заявка создана.\n"
                    + _refund_info("crypto", lang)
                    + "\n\nОтправь адрес USDT TRC20 одним сообщением (начинается с T...).",
                    "✅ Заявку створено.\n"
                    + _refund_info("crypto", lang)
                    + "\n\nНадішли адресу USDT TRC20 одним повідомленням (починається з T...).",
                    "✅ Request created.\n"
                    + _refund_info("crypto", lang)
                    + "\n\nSend your USDT TRC20 address in one message (starts with T...).",
                ),
            )
            return

    # серый кейс → заявка + пинг админу (для тех кто выбрал причину "другое", но в пределах 48 часов)
    res = await request_refund(session, tg_id=tg_id, payment_id=payment_id, reason=reason_text)
    await bot.send_message(
        tg_id,
        _t(
            lang,
            "✅ Заявка создана. Обычно ответ приходит быстро.\n" + _refund_info(prov_low, lang),
            "✅ Заявку створено. Зазвичай відповідь приходить швидко.\n" + _refund_info(prov_low, lang),
            "✅ Request created. Usually reviewed quickly.\n" + _refund_info(prov_low, lang),
        ),
    )

    admins = _admin_ids()
    if admins:
        txt = f"🧾 Refund request\nuser_tg={tg_id}\npayment_id={payment_id}\nreason={reason_text}\nage_days={age.days}\nprovider={prov_low}"
        for aid in admins:
            try:
                await bot.send_message(aid, txt)
            except Exception:
                pass


@router.callback_query(F.data.startswith("refund:back:pick:"))
async def refund_back_to_pick(c: CallbackQuery, session: AsyncSession) -> None:
    await c.answer()
    bot = c.bot
    if not bot:
        return

    tg_id = c.from_user.id
    lang = await _get_lang(session, tg_id)

    pays = await _list_recent_paid(session, tg_id, limit=5)
    if not pays:
        await _edit_cb_message(
            bot,
            c,
            _t(
                lang,
                "Пока не вижу оплаченных платежей, по которым можно сделать возврат.",
                "Поки не бачу оплачених платежів для повернення.",
                "I can't find paid payments eligible for refund.",
            ),
            reply_markup=None,
        )
        return

    await _edit_cb_message(
        bot,
        c,
        _t(
            lang,
            "Выбери платёж для возврата:",
            "Обери платіж для повернення:",
            "Pick a payment to refund:",
        ),
        reply_markup=_kb_pick(pays, lang),
    )


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
    target_payload: dict[str, Any] = {}

    for p in pays:
        raw = getattr(p, "payload", None)
        payload: dict[str, Any] = {}
        if isinstance(raw, dict):
            payload = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                pass
        # Ищем активную заявку, где ещё нет адреса
        if payload.get("refund_status") == "requested" and not payload.get("refund_address"):
            target = p
            target_payload = payload
            break

    if not target:
        await m.answer(
            _t(
                lang,
                "Я вижу адрес, но не нашёл активной crypto-заявки на возврат. Сначала создай возврат через кнопку 💸 Возврат.",
                "Бачу адресу, але не знайшов активної crypto-заявки. Спочатку створи повернення через кнопку 💸 Повернення.",
                "I see the address, but I can't find an active crypto refund request. Create it via 💸 Refund first.",
            )
        )
        return

    target_payload["refund_address"] = text
    target_payload["refund_network"] = "TRC20"
    target_payload["refund_status"] = "address_received"
    target_payload["refund_address_received_at"] = _now_utc().isoformat()

    target.payload = json.dumps(target_payload, ensure_ascii=False)
    await session.commit()

    await m.answer(
        _t(
            lang,
            "✅ Адрес получен. Передал в обработку. Обычно 24–72 часа.",
            "✅ Адресу отримано. Передав в обробку. Зазвичай 24–72 години.",
            "✅ Address received. Sent for processing. Usually 24–72 hours.",
        )
    )

    admins = _admin_ids()
    bot = m.bot
    if admins and bot:
        txt = (
            f"🪙 Crypto refund address received\nuser_tg={tg_id}\npayment_id={target.id}\naddress={text}\nnetwork=TRC20"
        )
        for aid in admins:
            try:
                await bot.send_message(aid, txt)
            except Exception:
                pass