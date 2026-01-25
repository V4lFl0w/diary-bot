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
        'пельмен': 12.0,
        'котлет': 100.0,
        'сосиск': 50.0,
        'яйц': 50.0,
        'яблок': 150.0,
    }
    for k, g in defaults.items():
        if k in name:
            return (k, n * g)
    # поддержка словоформ:
    if "вареник" in name or "вареник" in name or "вареник"[:6] in name or "вареник" in name or "вареник" in name:
        return ("вареник", n * 50.0)
    if "пельмен" in name:
        return ("пельмен", n * 12.0)
    return None

import base64
import json
from typing import Dict, Optional, Any
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

import httpx
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, BufferedInputFile
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
    # База: ккал/БЖУ. confidence добавляем снаружи через _add_confidence()
    return t(
        "cal_total",
        lang_code,
        kcal=res.get("kcal", 0),
        p=res.get("p", 0),
        f=res.get("f", 0),
        c=res.get("c", 0),
    )


def _is_water_only(text: str) -> bool:
    s = (text or "").strip().lower()
    if not s:
        return False
    if "вод" not in s and "water" not in s:
        return False
    # 5л воды / 2 l water / 500 мл воды
    return bool(re.search(r"\d+(?:[.,]\d+)?\s*(л|l|мл|ml)\b", s))


def _zero_ok_result(conf: float = 0.95) -> Dict[str, float]:
    # спец-флаг, чтобы 0 ккал не считалось ошибкой в хэндлерах
    return {"kcal": 0.0, "p": 0.0, "f": 0.0, "c": 0.0, "confidence": conf, "zero_ok": 1.0}


def _kcal_is_invalid(res: Optional[Dict[str, float]]) -> bool:
    if not res:
        return True
    kcal = float(res.get("kcal", 0) or 0)
    zero_ok = bool(res.get("zero_ok"))
    return (kcal <= 0) and (not zero_ok)

def _format_photo_details(lang_code: str, res: Dict[str, float]) -> str:
    """
    Доп-детали только для фото-анализа:
    title / ingredients / portion / assumptions
    """
    title = (res.get("title") or "").strip() if isinstance(res.get("title"), str) else ""
    portion = (res.get("portion") or "").strip() if isinstance(res.get("portion"), str) else ""

    ingredients = res.get("ingredients")
    if isinstance(ingredients, str):
        ingredients_list = [x.strip() for x in ingredients.split(",") if x.strip()]
    elif isinstance(ingredients, list):
        ingredients_list = [str(x).strip() for x in ingredients if str(x).strip()]
    else:
        ingredients_list = []

    assumptions = res.get("assumptions")
    if isinstance(assumptions, str):
        assumptions_list = [assumptions.strip()] if assumptions.strip() else []
    elif isinstance(assumptions, list):
        assumptions_list = [str(x).strip() for x in assumptions if str(x).strip()]
    else:
        assumptions_list = []

    parts = []
    if title:
        parts.append(_tr(lang_code, f"Блюдо: {title}", f"Страва: {title}", f"Dish: {title}"))
    if ingredients_list:
        joined = ", ".join(ingredients_list[:12])
        parts.append(_tr(lang_code, f"Состав: {joined}", f"Склад: {joined}", f"Ingredients: {joined}"))
    if portion:
        parts.append(_tr(lang_code, f"Порция: {portion}", f"Порція: {portion}", f"Portion: {portion}"))
    if assumptions_list:
        # 1-2 пункта максимум, чтобы карточку не ломать
        joined = "; ".join(assumptions_list[:2])
        parts.append(_tr(lang_code, f"Допущения: {joined}", f"Припущення: {joined}", f"Assumptions: {joined}"))

    return "\n".join(parts).strip()


# -------------------- fallback nutrition база --------------------

FALLBACK: Dict[str, Dict[str, float]] = {
    "молок": dict(kcal=60, p=3.2, f=3.2, c=4.7),
    "milk": dict(kcal=60, p=3.2, f=3.2, c=4.7),

    "банан": dict(kcal=89, p=1.1, f=0.3, c=23.0),
    "banana": dict(kcal=89, p=1.1, f=0.3, c=23.0),

    # --- extended basics (autopatch) ---  # COLA_EXTENDED_MARKER
    "яблок": dict(kcal=52, p=0.3, f=0.2, c=14.0),
    "apple": dict(kcal=52, p=0.3, f=0.2, c=14.0),

    # напитки (на 100 мл)
    "вода": dict(kcal=0, p=0.0, f=0.0, c=0.0),
    "water": dict(kcal=0, p=0.0, f=0.0, c=0.0),
    "кола": dict(kcal=42, p=0.0, f=0.0, c=10.6),
    "coke": dict(kcal=42, p=0.0, f=0.0, c=10.6),
    "coca": dict(kcal=42, p=0.0, f=0.0, c=10.6),
    "pepsi": dict(kcal=43, p=0.0, f=0.0, c=10.9),

    "сок": dict(kcal=46, p=0.2, f=0.1, c=11.0),
    "juice": dict(kcal=46, p=0.2, f=0.1, c=11.0),

    "чай": dict(kcal=1, p=0.0, f=0.0, c=0.2),
    "tea": dict(kcal=1, p=0.0, f=0.0, c=0.2),
    "кофе": dict(kcal=2, p=0.3, f=0.0, c=0.0),
    "coffee": dict(kcal=2, p=0.3, f=0.0, c=0.0),

    # крупы/гарниры (ГОТОВЫЕ, на 100 г)
    "рис": dict(kcal=130, p=2.7, f=0.3, c=28.0),
    "rice": dict(kcal=130, p=2.7, f=0.3, c=28.0),

    "овсянк": dict(kcal=68, p=2.4, f=1.4, c=12.0),
    "oat": dict(kcal=68, p=2.4, f=1.4, c=12.0),

    "пшеничн": dict(kcal=98, p=3.2, f=1.1, c=20.0),  # пшеничная каша
    "wheat": dict(kcal=98, p=3.2, f=1.1, c=20.0),

    "макарон": dict(kcal=131, p=5.0, f=1.1, c=25.0),
    "pasta": dict(kcal=131, p=5.0, f=1.1, c=25.0),

    "картоф": dict(kcal=80, p=2.0, f=0.1, c=17.0),
    "пюре": dict(kcal=110, p=2.2, f=4.0, c=16.0),

    # мясо/готовое
    "котлет": dict(kcal=240, p=16.0, f=18.0, c=6.0),  # усреднённо
    "cutlet": dict(kcal=240, p=16.0, f=18.0, c=6.0),

    "грудинк": dict(kcal=330, p=15.0, f=30.0, c=0.0),  # свиная грудка/грудинка
    "porkbelly": dict(kcal=330, p=15.0, f=30.0, c=0.0),

    # кото-корм (сухой, усреднённо)
    "корм": dict(kcal=360, p=30.0, f=12.0, c=30.0),
    "catfood": dict(kcal=360, p=30.0, f=12.0, c=30.0),

    "арахис": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "арахіс": dict(kcal=567, p=26.0, f=49.0, c=16.0),
    "peanut": dict(kcal=567, p=26.0, f=49.0, c=16.0),

    # гречка: по умолчанию ГОТОВАЯ
    "гречк": dict(kcal=110, p=3.6, f=1.3, c=21.3),
    "buckwheat": dict(kcal=110, p=3.6, f=1.3, c=21.3),

    # гречка сухая (только если явно указано "сух"/"крупа")
    "гречк_сух": dict(kcal=343, p=13.3, f=3.4, c=71.5),

    # вареники/пельмени (усреднённо, на 100 г)
    "вареник": dict(kcal=210, p=6.0, f=4.0, c=38.0),
    "пельмен": dict(kcal=260, p=11.0, f=14.0, c=22.0),

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
    # --- extended pieces (autopatch) ---  # PIECE_EXTENDED_MARKER
    "яблок": 150, "apple": 150,
    "котлет": 100, "cutlet": 100,
    # стакан/чашка/кружка (очень грубо)
    "стакан": 250,
    "чашк": 250,
    "кружк": 300,
    "яйц": 50, "egg": 50,
    "банан": 120, "banana": 120,
    "хлеб": 30, "хліб": 30, "bread": 30,
    "сыр": 30, "сир": 30, "cheese": 30,
    "сосиск": 50, "sausage": 50,
    "куриц": 80, "курк": 80, "chicken": 80,

    "вареник": 50,
    "пельмен": 12,
}


REV_DRINK_KEYS = {
    "вода","water","кола","coke","coca","pepsi",
    "сок","juice","чай","tea","кофе","coffee",
    "молок","milk",
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



def _add_confidence(out: str, conf: float | None, lang_code: str = "ru") -> str:
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



def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int):
    lines = []
    for paragraph in (text or "").split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        words = paragraph.split()
        cur = ""
        for w in words:
            test = (cur + " " + w).strip()
            if draw.textlength(test, font=font) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines

def render_text_card(text: str) -> bytes:
    W, H = 1080, 620
    PAD = 54
    bg = Image.new("RGB", (W, H), (15, 18, 22))
    draw = ImageDraw.Draw(bg)

    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 52)
    font_body  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)

    draw.text((PAD, 40), "Калории", font=font_title, fill=(255, 255, 255))

    max_w = W - PAD * 2
    lines = _wrap_text(draw, text, font_body, max_w)
    y = 40 + 86
    for ln in lines[:11]:
        draw.text((PAD, y), ln, font=font_body, fill=(220, 230, 240))
        y += 46

    buf = BytesIO()
    bg.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()

def render_result_card(photo_bytes: bytes, text: str) -> bytes:
    W = 1080
    PAD = 48
    PANEL_H = 520

    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    scale = W / img.width
    new_h = int(img.height * scale)
    img = img.resize((W, new_h))

    out = Image.new("RGB", (W, new_h + PANEL_H), (15, 18, 22))
    out.paste(img, (0, 0))

    draw = ImageDraw.Draw(out)
    draw.rectangle([0, new_h, W, new_h + PANEL_H], fill=(15, 18, 22))

    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48)
    font_body  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)

    draw.text((PAD, new_h + 28), "Расчёт калорий", font=font_title, fill=(255, 255, 255))

    max_w = W - PAD*2
    lines = _wrap_text(draw, text, font_body, max_w)
    y = new_h + 28 + 72
    for ln in lines[:12]:
        draw.text((PAD, y), ln, font=font_body, fill=(220, 230, 240))
        y += 46

    buf = BytesIO()
    out.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


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

    if _is_water_only(text):
        return _zero_ok_result(0.95)

    low = (text or "").lower()

    # --- normalize glued units: "1л" -> "1 л", "500мл" -> "500 мл"
    low = re.sub(r"(\d)(л|l|мл|ml)\b", r"\1 \2", low)

    # --- normalize cola words to match FALLBACK keys
    # "cola" -> "coke" (у тебя есть coke/coca/pepsi/кола)
    low = re.sub(r"\bcola\b", "coke", low)

    

    # --- normalize RU cola declensions: колы/колу/колой -> кола
    low = re.sub(r"\bкол(?:а|ы|у|е|ой|ою)\b", "кола", low)
    is_dry_buckwheat = False
    # если явно пишут "сух" или "крупа" — считаем как сухую
    if ("греч" in low or "buckwheat" in low) and ("сух" in low or "крупа" in low):
        low = re.sub(r"гречк\w*", "гречк_сух", low)
        is_dry_buckwheat = True

    piece_hint = _try_piece_guess(text)
    grams_info: list[tuple[float, Dict[str, float]]] = []
    # rough bowls/cups for some foods (very approximate)
    if "миска" in low and ("корм" in low or "catfood" in low) and not re.search(r"\d+\s*(г|гр|g|мл|ml|л|l)\b", low):
        meta = FALLBACK.get("корм")
        if meta:
            grams_info.append((60.0, meta))  # ~60g dry cat food
            return {
                "kcal": round(meta["kcal"] * 0.60),
                "p": round(meta["p"] * 0.60, 1),
                "f": round(meta["f"] * 0.60, 1),
                "c": round(meta["c"] * 0.60, 1),
                "confidence": 0.45,
            }
    
    if piece_hint and not re.search(r"\d+\s*(г|гр|g|мл|ml)\b", low):
        k, g = piece_hint
        if k in FALLBACK:
            grams_info.append((float(g), FALLBACK[k]))

    # если распознали 'шт' режим — считаем только по нему (чтобы не было двойного подсчёта)
    if piece_hint and grams_info:
        kcal = p = f = c = 0.0
        for g, meta in grams_info:
            factor = g / 100.0
            kcal += meta["kcal"] * factor
            p += meta["p"] * factor
            f += meta["f"] * factor
            c += meta["c"] * factor
        return {
            "kcal": round(kcal),
            "p": round(p, 1),
            "f": round(f, 1),
            "c": round(c, 1),
            "confidence": 0.60,
        }

    num = r"(\d+(?:[.,]\d+)?)"
    unit_re = r"(г|g|гр|ml|мл|л|l)"


    for name, meta in FALLBACK.items():
        if is_dry_buckwheat and name == "гречк":
            continue
        safe_name = re.escape(name)
        pattern = rf"{num}\s*{unit_re}\s*{safe_name}\w*"
        # reverse pattern: "пшеничная каша 300 г", "cola 0.5 l"
        unit_re_rev = r"(г|g|гр)"
        if name in REV_DRINK_KEYS:
            unit_re_rev = r"(г|g|гр|мл|ml|л|l)"
        pattern_rev = rf"{safe_name}\w*(?:\s+[а-яёa-z]+){{0,3}}\s*{num}\s*{unit_re_rev}\b"
        for m in re.finditer(pattern, low):
            qty_raw = m.group(1).replace(",", ".")
            try:
                qty = float(qty_raw)
            except ValueError:
                continue

            unit = (m.group(2) or "").lower()
            g = qty
            if unit in ("л", "l"):
                g = qty * 1000.0  # литры -> мл (и далее 1:1 к граммам)
            grams_info.append((float(g), meta))

        for m in re.finditer(pattern_rev, low):
            qty_raw = m.group(1).replace(',', '.')
            try:
                qty = float(qty_raw)
            except ValueError:
                continue
            unit = (m.group(2) or '').lower()
            g = qty
            if unit in ('л', 'l'):
                g = qty * 1000.0
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

    has_explicit_grams = bool(re.search(r"\d+\s*(г|гр|g|мл|ml|л|l)\b", low))
    if has_explicit_grams:
        confidence = 0.95 if re.search(r"\d+(?:[.,]\d+)?\s*(мл|ml|л|l)\b", low) else 0.90
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
        "Return ONLY valid JSON (no markdown, no extra text) with fields: "
        '{"title": string, "ingredients": array, "portion": string, '
        '"kcal": number, "p": number, "f": number, "c": number, '
        '"confidence": number, "assumptions": array}. '
        "confidence must be between 0 and 1 and reflects how sure you are about portion size and ingredients. "
        "If unsure, set confidence <= 0.65. "
        "ingredients: short list of main items (strings). "
        "portion: short human description (e.g., 'средняя порция', '≈250 г', '8 кусочков'). "
        "assumptions: 1-3 short notes about what you assumed (sauce type, cheese amount, etc.)."
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
            "title": (data.get("title") or "") if isinstance(data.get("title"), str) else "",
            "ingredients": data.get("ingredients") if isinstance(data.get("ingredients"), (list, str)) else [],
            "portion": (data.get("portion") or "") if isinstance(data.get("portion"), str) else "",
            "assumptions": data.get("assumptions") if isinstance(data.get("assumptions"), (list, str)) else [],
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
        if _kcal_is_invalid(res):
            await message.answer(
                "Не смог нормально посчитать. Укажи граммы/начинку, например: "
                "‘5 шт (~250 г), начинка: вишня/картошка/капуста/творог’ или ‘250 г вареников с картошкой’."
            )
            return
        out = _format_cal_total(lang_code, res)

        out = _add_confidence(out, float(res.get('confidence', 0) or 0), lang_code)

        card = render_text_card(out)
        await message.answer_photo(BufferedInputFile(card, filename="kcal.jpg"))
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
    if _kcal_is_invalid(res):
        await message.answer(
            "Не смог нормально посчитать. Укажи граммы/начинку, например: "
            "‘5 шт (~250 г), начинка: вишня/картошка/капуста/творог’ или ‘250 г вареников с картошкой’."
        )
        return
    out = _format_cal_total(lang_code, res)

    out = _add_confidence(out, float(res.get('confidence', 0) or 0), lang_code)

    await message.answer(out)
# -------------------- MODE: waiting_photo --------------------

@router.message(CaloriesFSM.waiting_photo, F.photo)
async def cal_photo_waiting(message: types.Message, state: FSMContext, session: AsyncSession, lang: Optional[str] = None) -> None:
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
    details = _format_photo_details(lang_code, res)
    out = _format_cal_total(lang_code, res)
    if details:
        out = details + "\n" + out
    out = _add_confidence(out, conf, lang_code)

    img_bytes = await _download_photo_bytes(message)
    if img_bytes:
        card = render_result_card(img_bytes, out)
        await message.answer_photo(BufferedInputFile(card, filename="calories.jpg"))
    else:
        await message.answer(out)
    await state.set_state(CaloriesFSM.waiting_input)
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
    if _kcal_is_invalid(res):
        await message.answer(
            "Не смог нормально посчитать. Укажи граммы/начинку, например: "
            "‘5 шт (~250 г), начинка: вишня/картошка/капуста/творог’ или ‘250 г вареников с картошкой’."
        )
        return
    out = _format_cal_total(lang_code, res)

    out = _add_confidence(out, float(res.get('confidence', 0) or 0), lang_code)

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
        if _kcal_is_invalid(res):
            await message.answer(
                "Не смог нормально посчитать. Укажи граммы/начинку, например: "
                "‘5 шт (~250 г), начинка: вишня/картошка/капуста/творог’ или ‘250 г вареников с картошкой’."
            )
            return
        out = _format_cal_total(lang_code, res)

        out = _add_confidence(out, float(res.get('confidence', 0) or 0), lang_code)

        await message.answer(out)
        return

    res2 = await analyze_photo(message)
    if not res2:
        await message.answer("Фото-анализ не настроен (нужен OPENAI_API_KEY) или OpenAI Vision не вернул JSON.")
        return

    conf = float(res2.get("confidence", 0) or 0)
    details = _format_photo_details(lang_code, res2)
    out = _format_cal_total(lang_code, res2)
    if details:
        out = details + "\n" + out
    out = _add_confidence(out, conf, lang_code)

    img_bytes = await _download_photo_bytes(message)
    if img_bytes:
        card = render_result_card(img_bytes, out)
        await message.answer_photo(BufferedInputFile(card, filename="calories.jpg"))
    else:
        await message.answer(out)
__all__ = ["router"]
