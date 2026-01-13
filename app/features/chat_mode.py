from __future__ import annotations
import json, pathlib
from aiogram import Router, types, F
from aiogram.filters import Command
from app.i18n import tr, detect_lang
router = Router()
STORE = pathlib.Path("data/user_flags.json")
def _load():
    if STORE.exists():
        return json.loads(STORE.read_text(encoding="utf-8"))
    return {}
def _save(d):
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
@router.message(Command("chat_on"))
async def chat_on(m: types.Message):
    lang = detect_lang(m.from_user.language_code)
    d = _load(); d[str(m.from_user.id)] = True; _save(d)
    await m.answer(tr("chat_on", lang) + "\n" + tr("chat_hint", lang))
@router.message(Command("chat_off"))
async def chat_off(m: types.Message):
    lang = detect_lang(m.from_user.language_code)
    d = _load(); d[str(m.from_user.id)] = False; _save(d)
    await m.answer(tr("chat_off", lang))
@router.message(F.content_type.in_({"text","photo"}))
async def smart_chat(m: types.Message):
    d = _load()
    if not d.get(str(m.from_user.id)):
        return
    lang = detect_lang(m.from_user.language_code)
    await m.reply(tr("chat_reply_generic", lang))
