from __future__ import annotations

import os
import re

_COUNT_PIECES_RE = re.compile(r"(\d+)\s*(?:шт\.?|штук|pcs|piece)?\s*([а-яёa-z\-\s]+)", re.I)

def _try_piece_guess(text: str) -> tuple[str, float] | None:
    # '5 вареников' -> ('вареники', 250)
    m = _COUNT_PIECES_RE.search((text or '').strip().lower())
    if not m:
        return None
    n = int(m.group(1))
    name = m.group(2).strip()
    defaults = {
        'вареник': 50.0,
        'вареники': 50.0,
        'пельмень': 12.0,
        'пельмени': 12.0,
    }
    for k, g in defaults.items():
        if k in name:
            return (k, n * g)
    return None

import base64
import json
from typing import Dict, Optional, Any

import httpx
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.i18n import t
from app.keyboards import (
    get_main_kb,
    is_calories_btn,
    is_root_journal_btn,
    is_root_reminders_btn,
    is_root_calories_btn,
    is_root_stats_btn,
    is_root_assistant_btn,
    is_root_media_btn,
    is_root_premium_btn,
    is_root_settings_btn,
    is_report_bug_btn,
    is_admin_btn,
    is_back_btn,
)

try:
    from app.handlers.admin import is_admin_tg
except Exception:
    def is_admin_tg(_: int) -> bool:
        return False

from app.models.user import User

# v2-feature gating (канон)
try:
    from app.services.features_v2 import require_feature_v2
except Exception:
    require_feature_v2 = None  # type: ignore


router = Router(name="calories")

FEATURE_CAL_PHOTO = "calories_photo"
SUPPORTED_LANGS = {"ru", "uk", "en"}


# -------------------- FSM --------------------

class CaloriesFSM(StatesGroup):
    waiting_input = State()
    waiting_photo = State()


# -------------------- i18n helpers --------------------

def _normalize_lang(code: Optional[str]) -> str:
    c = (code or "ru").strip().lower()
    if c.startswith(("ua", "uk")):
        c = "uk"
    elif c.startswith("en"):
        c = "en"
    else:
        c = "ru"
    if c not in SUPPORTED_LANGS:
        c = "ru"
    return c


def _tr(lang: str, ru: str, uk: str, en: str) -> str:
    l = _normalize_lang(lang)
    return uk if l == "uk" else en if l == "en" else ru


def _cal_hook_inline_kb(lang_code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=_tr(lang_code, "✍️ Ввести списком", "✍️ Ввести списком", "✍️ Enter as list"),
        callback_data="cal:enter",
    )
    kb.button(
        text=_tr(lang_code, "📸 Отправить фото (Premium)", "📸 Надіслати фото (Premium)", "📸 Send photo (Premium)"),
        callback_data="cal:photo",
    )
    kb.adjust(1, 1)
    return kb.as_markup()


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def _user_lang(user: Optional[User], fallback: Optional[str], tg_lang: Optional[str] = None) -> str:
    return _normalize_lang(
        getattr(user, "locale", None)
        or getattr(user, "lang", None)
        or fallback
        or tg_lang
        or "ru"
    )


def _format_cal_total(lang_code: str, res: Dict[str, float]) -> str:
    out = _format_cal_total(lang_code, res)
    conf = res.get("confidence", None)
    try:
        conf_f = float(conf) if conf is not None else None
    except Exception:
        conf_f = None

    if conf_f is not None:
        conf_f = max(0.0, min(1.0, conf_f))
        pct = int(round(conf_f * 100))
        out += f"\nУверенность: {pct}%"
        if pct < 65:
            out += "\n⚠️ Если скажешь граммовку/порцию — пересчитаю точнее."
    return out


# -------------------- fallback nutrition база --------------------

FALLBACK: Dict[str, Dict[str, float]] = {
    "молок": dict(kcal=60, p=3.2, f=3.2, c=4.7),
    "milk": dict(kcal=60, p=3.2, f=3.2, c=4.7),
    "банан": dict(kcal=89, p=1.1, f=0.3, c=23.0),
    "banana": dict(kcal=89, p=1.1, f=0.3, c=23.0),
    "арахис": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "арахіс": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "peanut": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "греч": dict(kcal=343, p=13.3, f=3.4, c=71.5),
    "гречк": dict(kcal=343, p=13.3, f=3.4, c=71.5),
    "buckwheat": dict(kcal=343, p=13.3, f=3.4, c=71.5),
    "яйц": dict(kcal=143, p=13.0, f=10.0, c=1.1),
    "egg": dict(kcal=143, p=13.0, f=10.0, c=1.1),
    "хлеб": dict(kcal=250, p=9.0, f=3.0, c=49.0),
    "хліб": dict(kcal=250, p=9.0, f=3.0, c=49.0),
    "bread": dict(kcal=250, p=9.0, f=3.0, c=49.0),
    "сыр": dict(kcal=350, p=26.0, f=27.0, c=3.0),
    "сир": dict(kcal=350, p=26.0, f=27.0, c=3.0),
    "cheese": dict(kcal=350, p=26.0, f=27.0, c=3.0),
    "сосиск": dict(kcal=300, p=12.0, f=27.0, c=2.0),
    "sausage": dict(kcal=300, p=12.0, f=27.0, c=2.0),
    "куриц": dict(kcal=190, p=29.0, f=7.0, c=0.0),
    "курк": dict(kcal=190, p=29.0, f=7.0, c=0.0),
    "chicken": dict(kcal=190, p=29.0, f=7.0, c=0.0),
    "свинин": dict(kcal=260, p=26.0, f=18.0, c=0.0),
    "шашлык": dict(kcal=250, p=22.0, f=18.0, c=0.0),
    "мяс": dict(kcal=230, p=23.0, f=15.0, c=0.0),
}

PIECE_GRAMS: Dict[str, int] = {
    "яйц": 50, "egg": 50,
    "банан": 120, "banana": 120,
    "хлеб": 30, "хліб": 30, "bread": 30,
    "сыр": 30, "сир": 30, "cheese": 30,
    "сосиск": 50, "sausage": 50,
    "куриц": 80, "курк": 80, "chicken": 80,
}

CAL_KEYS = list(FALLBACK.keys())


def _strip_cmd_prefix(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^/(calories|kcal)\s*", "", s, flags=re.IGNORECASE)
    return s.strip()


def _is_root_menu_text(text: str) -> bool:
    return any(
        fn(text)
        for fn in (
            is_root_journal_btn,
            is_root_reminders_btn,
            is_root_calories_btn,
            is_root_stats_btn,
            is_root_assistant_btn,
            is_root_media_btn,
            is_root_premium_btn,
            is_root_settings_btn,
            is_report_bug_btn,
            is_admin_btn,
            is_back_btn,
        )
    )


def _is_foreign_command(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low.startswith("/"):
        return False
    return not low.startswith(("/calories", "/kcal", "/cancel"))


def _looks_like_food(text: Optional[str]) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.startswith("/"):
        return False
    if _is_root_menu_text(raw):
        return False
    low = raw.lower()
    return any(k in low for k in CAL_KEYS)



def _add_confidence(out: str, conf: float | None) -> str:
    try:
        c = float(conf or 0)
    except Exception:
        c = 0.0
    if c <= 0:
        return out
    pct = int(round(c * 100))
    out += f"\nУверенность: {pct}%"
    if c < 0.65:
        out += "\n⚠️ Если уточнишь граммовку/порцию — пересчитаю точнее."
    return out


# -------------------- analyze text --------------------

async def analyze_text(text: str) -> Dict[str, float]:
    """
    1) Пробуем Api Ninjas, если задан ключ.
    2) Если не удалось — считаем грубо по FALLBACK.
    + confidence (0..1)
    """
    key = os.getenv("NINJAS_API_KEY") or os.getenv("NUTRITION_API_KEY")
    if key:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.api-ninjas.com/v1/nutrition",
                    params={"query": text},
                    headers={"X-Api-Key": key},
                )
                resp.raise_for_status()
                items = resp.json()
                if isinstance(items, list) and items:
                    kcal = sum(float(i.get("calories", 0) or 0) for i in items)
                    p = sum(float(i.get("protein_g", 0) or 0) for i in items)
                    f = sum(float(i.get("fat_total_g", 0) or 0) for i in items)
                    c = sum(float(i.get("carbohydrates_total_g", 0) or 0) for i in items)

                    confidence = 0.85
                    return {
                        "kcal": round(kcal),
                        "p": round(p, 1),
                        "f": round(f, 1),
                        "c": round(c, 1),
                        "confidence": confidence,
                    }
        except Exception:
            pass

    low = (text or "").lower()

    piece_hint = _try_piece_guess(text)
    grams_info: list[tuple[float, Dict[str, float]]] = []
    if piece_hint and not re.search(r"\d+\s*(г|гр|g|мл|ml)\b", low):
        k, g = piece_hint
        if k in FALLBACK:
            grams_info.append((float(g), FALLBACK[k]))

    num = r"(\d+(?:[.,]\d+)?)"
    unit_re = r"(г|g|гр|ml|мл)"

    for name, meta in FALLBACK.items():
        safe_name = re.escape(name)
        pattern = rf"{num}\s*{unit_re}?\s*{safe_name}"

        for m in re.finditer(pattern, low):
            qty_raw = m.group(1).replace(",", ".")
            try:
                qty = float(qty_raw)
            except ValueError:
                continue

            unit = (m.group(2) or "").lower()
            g = qty  # г/ml считаем 1:1
            if unit == "" and name in PIECE_GRAMS:
                g = qty * float(PIECE_GRAMS[name])

            grams_info.append((float(g), meta))

        if name in PIECE_GRAMS and name in low and not re.search(pattern, low):
            grams_info.append((float(PIECE_GRAMS[name]), meta))

    kcal = p = f = c = 0.0
    for g, meta in grams_info:
        factor = g / 100.0
        kcal += meta["kcal"] * factor
        p += meta["p"] * factor
        f += meta["f"] * factor
        c += meta["c"] * factor

    has_explicit_grams = bool(re.search(r"\d+\s*(г|гр|g|мл|ml)\b", low))
    if has_explicit_grams:
        confidence = 0.90
    elif piece_hint:
        confidence = 0.60
    elif grams_info:
        confidence = 0.70
    else:
        confidence = 0.0

    return {
        "kcal": round(kcal),
        "p": round(p, 1),
        "f": round(f, 1),
        "c": round(c, 1),
        "confidence": confidence,
    }

# -------------------- photo analyze (OpenAI Vision) --------------------

async def _download_photo_bytes(message: types.Message) -> Optional[bytes]:
    if not message.photo:
        return None
    ph = message.photo[-1]
    file = await message.bot.get_file(ph.file_id)
    bio = await message.bot.download_file(file.file_path)
    return bio.read()


async def analyze_photo(message: types.Message) -> Optional[Dict[str, float]]:
    """
    OpenAI Vision (Responses API):
    - требуются переменные окружения:
      OPENAI_API_KEY
      (опционально) OPENAI_VISION_MODEL, по умолчанию gpt-4.1-mini
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    img = await _download_photo_bytes(message)
    if not img:
        return None

    model = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")
    b64 = base64.b64encode(img).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"

    prompt = (

        "Estimate nutrition for the meal on the photo. "

        "Return ONLY valid JSON with fields: "

        '{"kcal": number, "p": number, "f": number, "c": number, "confidence": number}. '

        "confidence must be between 0 and 1 and reflects how sure you are about portion size and ingredients. "

        "If unsure, set confidence <= 0.65. No extra text."

    )

    payload = {
        "model": model,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url},
            ],
        }],
        "max_output_tokens": 200,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            j = r.json()

        txt = j.get("output_text")
        if not txt:
            # fallback: вытаскиваем текст из output[]
            out = j.get("output") or []
            chunks = []
            for item in out:
                if item.get("type") == "message":
                    for part in (item.get("content") or []):
                        if part.get("type") in ("output_text", "text"):
                            chunks.append(part.get("text", ""))
            txt = "\n".join(chunks).strip()

        if not txt:
            return None

        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))

        return {
            "kcal": float(data.get("kcal", 0) or 0),
            "p": float(data.get("p", 0) or 0),
            "f": float(data.get("f", 0) or 0),
            "c": float(data.get("c", 0) or 0),
            "confidence": float(data.get("confidence", 0) or 0),
}
    except Exception:
        return None


# -------------------- premium gate --------------------

async def _require_photo_premium(
    message: types.Message,
    session: AsyncSession,
    user: Optional[User],
    lang_code: str,
    *,
    source: str,
    props: Optional[Dict[str, Any]] = None,
) -> bool:
    if not user:
        await message.answer(_tr(lang_code, "Нажми /start", "Натисни /start", "Press /start"))
        return False

    if require_feature_v2 is None:
        await message.answer(
            _tr(
                lang_code,
                "📸 Подсчёт по фото доступен в 💎 Премиум.",
                "📸 Підрахунок по фото доступний у 💎 Преміум.",
                "📸 Photo calories are available in 💎 Premium.",
            )
        )
        return False

    ok = await require_feature_v2(
        message,
        session,
        user,
        FEATURE_CAL_PHOTO,
        event_on_fail="calories_photo_locked",
        props={"source": source, **(props or {})},
    )
    return bool(ok)


# -------------------- entrypoints --------------------

@router.message(Command("calories"))
@router.message(Command("kcal"))
async def cal_cmd(message: types.Message, state: FSMContext, session: AsyncSession, lang: Optional[str] = None) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    raw = (message.text or "").strip()
    query = _strip_cmd_prefix(raw)

    if query:
        res = await analyze_text(query)
        if not res or float(res.get('kcal', 0) or 0) <= 0:
            await message.answer(
                "Не смог нормально посчитать. Укажи граммы/начинку, например: "
                "‘5 шт (~250 г), начинка: вишня/картошка/капуста/творог’ или ‘250 г вареников с картошкой’."
            )
            return
        out = _format_cal_total(lang_code, res)

        out = _add_confidence(out, float(res.get('confidence', 0) or 0))

        await message.answer(out)
        return

    await state.set_state(CaloriesFSM.waiting_input)

    hook = _tr(
        lang_code,
        """🔥 Калории — быстро и без занудства

✅ Напиши списком, что ты съел/выпил — одним сообщением
Или отправь фото еды (💎 Премиум)

Я посчитаю: ккал • Б/Ж/У

Примеры:
• 250 мл молока, банан, 40 г арахиса
• 200 г курицы, 100 г риса, 1 яблоко

/cancel — выйти из режима""",
        """🔥 Калорії — швидко і без занудства

✅ Напиши списком, що ти з'їв/випив — одним повідомленням
Або надішли фото їжі (💎 Преміум)

Я порахую: ккал • Б/Ж/В

Приклади:
• 250 мл молока, банан, 40 г арахісу
• 200 г курки, 100 г рису, 1 яблуко

/cancel — вийти з режиму""",
        """🔥 Calories — fast, no fluff

✅ Send your food/drink list in one message
Or send a food photo (💎 Premium)

I’ll calculate: kcal • P/F/C

Examples:
• 250 ml milk, 1 banana, 40 g peanuts
• 200 g chicken, 100 g rice, 1 apple

/cancel — exit the mode""",
    )

    await message.answer(hook, reply_markup=_cal_hook_inline_kb(lang_code))


@router.message(F.text.func(is_calories_btn))
async def cal_btn(message: types.Message, state: FSMContext, session: AsyncSession, lang: Optional[str] = None) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    await state.set_state(CaloriesFSM.waiting_input)

    hook = _tr(
        lang_code,
        """🔥 Калории — быстро и без занудства

✅ Напиши списком, что ты съел/выпил — одним сообщением
Или отправь фото еды (💎 Премиум)

Я посчитаю: ккал • Б/Ж/У

Примеры:
• 250 мл молока, банан, 40 г арахиса
• 200 г курицы, 100 г риса, 1 яблоко

/cancel — выйти из режима""",
        """🔥 Калорії — швидко і без занудства

✅ Напиши списком, що ти з'їв/випив — одним повідомленням
Або надішли фото їжі (💎 Преміум)

Я порахую: ккал • Б/Ж/В

Приклади:
• 250 мл молока, банан, 40 г арахісу
• 200 г курки, 100 г рису, 1 яблуко

/cancel — вийти з режиму""",
        """🔥 Calories — fast, no fluff

✅ Send your food/drink list in one message
Or send a food photo (💎 Premium)

I’ll calculate: kcal • P/F/C

Examples:
• 250 ml milk, 1 banana, 40 g peanuts
• 200 g chicken, 100 g rice, 1 apple

/cancel — exit the mode""",
    )

    await message.answer(hook, reply_markup=_cal_hook_inline_kb(lang_code))


# -------------------- callbacks --------------------

@router.callback_query(F.data == "cal:enter")
async def cal_enter_cb(cb: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CaloriesFSM.waiting_input)
    await cb.answer()
    await cb.message.answer("Ок, пиши списком одним сообщением 🙂")


@router.callback_query(F.data == "cal:photo")
async def cal_photo_cb(cb: types.CallbackQuery, state: FSMContext, session: AsyncSession, lang: Optional[str] = None) -> None:
    tg_lang = getattr(cb.from_user, "language_code", None)
    user = await _get_user(session, cb.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    ok = await _require_photo_premium(cb.message, session, user, lang_code, source="hook_button")
    if not ok:
        return

    await state.set_state(CaloriesFSM.waiting_photo)
    await cb.answer()
    await cb.message.answer("Кидай фото еды 📸")


# -------------------- cancel --------------------

@router.message(Command("cancel"))
async def cal_cancel_global(message: types.Message, state: FSMContext, session: AsyncSession, lang: Optional[str] = None) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    await state.clear()
    await message.answer(
        _tr(lang_code, "Ок, отменил.", "Ок, скасував.", "Ok, cancelled."),
        reply_markup=get_main_kb(
            lang_code,
            is_premium=bool(getattr(user, "is_premium", False)),
            is_admin=is_admin_tg(message.from_user.id if message.from_user else 0),
        ),
    )


# -------------------- MODE: waiting_input --------------------

@router.message(CaloriesFSM.waiting_input, F.text)
async def cal_text_in_mode(message: types.Message, state: FSMContext, session: AsyncSession, lang: Optional[str] = None) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    if _is_root_menu_text(text) or _is_foreign_command(text):
        await state.clear()
        return

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    payload = _strip_cmd_prefix(text)
    if not payload:
        return

    res = await analyze_text(payload)
    if not res or float(res.get('kcal', 0) or 0) <= 0:
        await message.answer(
            "Не смог нормально посчитать. Укажи граммы/начинку, например: "
            "‘5 шт (~250 г), начинка: вишня/картошка/капуста/творог’ или ‘250 г вареников с картошкой’."
        )
        return
    out = _format_cal_total(lang_code, res)

    out = _add_confidence(out, float(res.get('confidence', 0) or 0))

    await message.answer(out)
# -------------------- MODE: waiting_photo --------------------

@router.message(CaloriesFSM.waiting_photo, F.photo)
async def cal_photo_waiting(message: types.Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    ok = await _require_photo_premium(message, session, user, lang_code, source="waiting_photo")
    if not ok:
        return

    res = await analyze_photo(message)
    if not res:
        await message.answer("Фото-анализ не настроен (нужен OPENAI_API_KEY) или OpenAI Vision не вернул JSON.")
        return

    conf = float(res.get("confidence", 0) or 0)

    pct = int(round(conf * 100))

    out = _format_cal_total(lang_code, res)

    out += f"\nУверенность: {pct}%"

    if conf and conf < 0.65:

        out += "\n⚠️ Если скажешь граммовку/порцию — пересчитаю точнее."

    await message.answer(out)
# -------------------- free text autodetect --------------------

@router.message(F.text.func(_looks_like_food))
async def cal_text_free_autodetect(message: types.Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    if _is_root_menu_text(text) or _is_foreign_command(text):
        return

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    res = await analyze_text(text)
    if not res or float(res.get('kcal', 0) or 0) <= 0:
        await message.answer(
            "Не смог нормально посчитать. Укажи граммы/начинку, например: "
            "‘5 шт (~250 г), начинка: вишня/картошка/капуста/творог’ или ‘250 г вареников с картошкой’."
        )
        return
    out = _format_cal_total(lang_code, res)

    out = _add_confidence(out, float(res.get('confidence', 0) or 0))

    await message.answer(out)
# -------------------- photo with caption trigger --------------------

@router.message(F.photo)
async def cal_photo_caption_trigger(message: types.Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    caption = (message.caption or "").strip()
    if not caption:
        return

    low = caption.lower()
    is_cmd = low.startswith(("/calories", "/kcal"))
    is_food_caption = _looks_like_food(caption)
    if not (is_cmd or is_food_caption):
        return

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    ok = await _require_photo_premium(message, session, user, lang_code, source="photo_caption_trigger")
    if not ok:
        return

    # если подпись с едой — считаем по подписи; иначе — по фото
    payload_text = _strip_cmd_prefix(caption) if is_cmd else caption
    payload_text = payload_text.strip()

    if payload_text and _looks_like_food(payload_text):
        res = await analyze_text(payload_text)
        if not res or float(res.get('kcal', 0) or 0) <= 0:
            await message.answer(
                "Не смог нормально посчитать. Укажи граммы/начинку, например: "
                "‘5 шт (~250 г), начинка: вишня/картошка/капуста/творог’ или ‘250 г вареников с картошкой’."
            )
            return
        out = _format_cal_total(lang_code, res)

        out = _add_confidence(out, float(res.get('confidence', 0) or 0))

        await message.answer(out)
        return

    res2 = await analyze_photo(message)
    if not res2:
        await message.answer("Фото-анализ не настроен (нужен OPENAI_API_KEY) или OpenAI Vision не вернул JSON.")
        return
    conf = float(res2.get("confidence", 0) or 0)
    pct = int(round(conf * 100))
    out = _format_cal_total(lang_code, res2)
    out += f"\nУверенность: {pct}%"
    if conf and conf < 0.65:
        out += "\n⚠️ Если скажешь граммовку/порцию — пересчитаю точнее."
    await message.answer(out)
__all__ = ["router"]
