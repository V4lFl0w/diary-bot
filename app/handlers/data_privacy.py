from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text as sql_text

router = Router()

BTN_MENU_RU = "🔐 Данные и приватность"
BTN_MENU_UA = "🔐 Дані та приватність"
BTN_MENU_EN = "🔐 Data & Privacy"

def _dp_title(lang: str) -> str:
    l = (lang or "ru").lower()
    if l.startswith("uk"):
        return "🔐 Дані та приватність"
    if l.startswith("en"):
        return "🔐 Data & Privacy"
    return "🔐 Данные и приватность"

def _dp_kb(lang: str) -> InlineKeyboardMarkup:
    l = (lang or "ru").lower()
    if l.startswith("uk"):
        exp = "📤 Експорт даних"
        wipe = "🧹 Видалити дані"
        delacc = "❌ Видалити акаунт"
        back = "⬅️ Назад"
    elif l.startswith("en"):
        exp = "📤 Export data"
        wipe = "🧹 Delete data"
        delacc = "❌ Delete account"
        back = "⬅️ Back"
    else:
        exp = "📤 Экспорт данных"
        wipe = "🧹 Удалить данные"
        delacc = "❌ Удалить аккаунт"
        back = "⬅️ Назад"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=exp, callback_data="dp:export")],
        [InlineKeyboardButton(text=wipe, callback_data="dp:wipe:ask")],
        [InlineKeyboardButton(text=delacc, callback_data="dp:delete:ask")],
        [InlineKeyboardButton(text=back, callback_data="settings:back")],
    ])

def _confirm_kb(kind: str, lang: str) -> InlineKeyboardMarkup:
    l = (lang or "ru").lower()
    if l.startswith("uk"):
        sure = "Ви впевнені?"
        yes = "Так, продовжити"
        no = "Скасувати"
        yes_hard = "Так, видалити назавжди"
    elif l.startswith("en"):
        sure = "Are you sure?"
        yes = "Yes, continue"
        no = "Cancel"
        yes_hard = "Yes, delete forever"
    else:
        sure = "Вы уверены?"
        yes = "Да, продолжить"
        no = "Отмена"
        yes_hard = "Да, удалить навсегда"

    if kind == "wipe":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=sure, callback_data="dp:noop")],
            [InlineKeyboardButton(text=yes, callback_data="dp:wipe:go")],
            [InlineKeyboardButton(text=no, callback_data="dp:cancel")],
        ])

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=sure, callback_data="dp:noop")],
        [InlineKeyboardButton(text=yes_hard, callback_data="dp:delete:go")],
        [InlineKeyboardButton(text=no, callback_data="dp:cancel")],
    ])

async def _get_lang(session: AsyncSession, tg_id: int) -> str:
    try:
        row = (await session.execute(sql_text("SELECT lang FROM users WHERE tg_id=:tg LIMIT 1"), {"tg": tg_id})).first()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return "ru"

async def _fetch_all_data(session: AsyncSession, tg_id: int) -> dict:
    data: dict = {"exported_at": datetime.now(timezone.utc).isoformat(), "tg_id": tg_id}

    queries = {
        "user": ("SELECT * FROM users WHERE tg_id=:tg LIMIT 1", True),
        "journal_entries": ("SELECT * FROM journal_entries WHERE user_id=(SELECT id FROM users WHERE tg_id=:tg LIMIT 1) ORDER BY id", False),
        "events": ("SELECT * FROM events WHERE tg_id=:tg ORDER BY id", False),
        "analytics": ("SELECT * FROM analytics_events WHERE tg_id=:tg ORDER BY id", False),
    }

    for key, (q, single) in queries.items():
        try:
            res = await session.execute(sql_text(q), {"tg": tg_id})
            rows = res.mappings().all()
            if single:
                data[key] = rows[0] if rows else None
            else:
                data[key] = rows
        except Exception:
            data[key] = None

    return data

def _csv_journal(rows: list[dict]) -> bytes:
    out = io.StringIO()
    fieldnames = []
    if rows:
        fieldnames = list(rows[0].keys())
    w = csv.DictWriter(out, fieldnames=fieldnames or ["id"], extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(dict(r))
    return out.getvalue().encode("utf-8")

async def _wipe_data(session: AsyncSession, tg_id: int) -> None:
    stmts = [
        "DELETE FROM journal_entries WHERE user_id=(SELECT id FROM users WHERE tg_id=:tg LIMIT 1)",
        "DELETE FROM events WHERE tg_id=:tg",
        "DELETE FROM analytics_events WHERE tg_id=:tg",
    ]
    for s in stmts:
        try:
            await session.execute(sql_text(s), {"tg": tg_id})
        except Exception:
            pass
    await session.commit()

async def _delete_account(session: AsyncSession, tg_id: int) -> None:
    stmts = [
        "DELETE FROM journal_entries WHERE user_id=(SELECT id FROM users WHERE tg_id=:tg LIMIT 1)",
        "DELETE FROM events WHERE tg_id=:tg",
        "DELETE FROM analytics_events WHERE tg_id=:tg",
        "DELETE FROM subscriptions WHERE user_id=(SELECT id FROM users WHERE tg_id=:tg LIMIT 1)",
        "DELETE FROM users WHERE tg_id=:tg",
    ]
    for s in stmts:
        try:
            await session.execute(sql_text(s), {"tg": tg_id})
        except Exception:
            pass
    await session.commit()

@router.message(F.text.in_({BTN_MENU_RU, BTN_MENU_UA, BTN_MENU_EN}))
async def data_privacy_menu(m: Message, session: AsyncSession) -> None:
    lang = await _get_lang(session, m.from_user.id)
    await m.answer(_dp_title(lang), reply_markup=_dp_kb(lang))

@router.callback_query(F.data == "dp:export")
async def dp_export(call: CallbackQuery, session: AsyncSession) -> None:
    tg_id = call.from_user.id
    lang = await _get_lang(session, tg_id)

    all_data = await _fetch_all_data(session, tg_id)

    js = json.dumps(all_data, ensure_ascii=False, indent=2).encode("utf-8")
    jf = BufferedInputFile(js, filename="diary_export.json")

    journal_rows = all_data.get("journal_entries") or []
    cf = BufferedInputFile(_csv_journal(journal_rows), filename="journal_entries.csv")

    await call.message.answer_document(jf)
    await call.message.answer_document(cf)
    await call.answer()

@router.callback_query(F.data == "dp:wipe:ask")
async def dp_wipe_ask(call: CallbackQuery, session: AsyncSession) -> None:
    lang = await _get_lang(session, call.from_user.id)
    txt = "🧹 Удалить данные: записи, события и аналитику.\nАккаунт останется." if not lang.startswith(("uk","en")) else \
          ("🧹 Видалити дані: записи, події та аналітику.\nАкаунт залишиться." if lang.startswith("uk") else \
           "🧹 Delete data: journal, events and analytics.\nAccount will stay.")
    await call.message.answer(txt, reply_markup=_confirm_kb("wipe", lang))
    await call.answer()

@router.callback_query(F.data == "dp:delete:ask")
async def dp_delete_ask(call: CallbackQuery, session: AsyncSession) -> None:
    lang = await _get_lang(session, call.from_user.id)
    txt = "❌ Удалить аккаунт навсегда. Это необратимо." if not lang.startswith(("uk","en")) else \
          ("❌ Видалити акаунт назавжди. Це незворотно." if lang.startswith("uk") else \
           "❌ Delete account forever. This is irreversible.")
    await call.message.answer(txt, reply_markup=_confirm_kb("delete", lang))
    await call.answer()

@router.callback_query(F.data == "dp:wipe:go")
async def dp_wipe_go(call: CallbackQuery, session: AsyncSession) -> None:
    tg_id = call.from_user.id
    lang = await _get_lang(session, tg_id)
    await _wipe_data(session, tg_id)
    done = "✅ Данные удалены." if not lang.startswith(("uk","en")) else ("✅ Дані видалено." if lang.startswith("uk") else "✅ Data deleted.")
    await call.message.answer(done)
    await call.answer()

@router.callback_query(F.data == "dp:delete:go")
async def dp_delete_go(call: CallbackQuery, session: AsyncSession) -> None:
    tg_id = call.from_user.id
    lang = await _get_lang(session, tg_id)
    await _delete_account(session, tg_id)
    done = "✅ Аккаунт удалён." if not lang.startswith(("uk","en")) else ("✅ Акаунт видалено." if lang.startswith("uk") else "✅ Account deleted.")
    await call.message.answer(done)
    await call.answer()

@router.callback_query(F.data.in_({"dp:cancel","dp:noop"}))
async def dp_cancel(call: CallbackQuery) -> None:
    await call.answer()
