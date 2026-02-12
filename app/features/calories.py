from __future__ import annotations

import base64
import json
import os
import re
import logging
from io import BytesIO
from typing import Any, Dict, Optional

import httpx
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_COUNT_PIECES_RE = re.compile(
    r"\b(\d+)\s*(?:шт\.?|штук|pcs|pieces?|piece)?\b\s*([а-яёa-z][а-яёa-z\-\s]{0,40})",
    re.I,
)


def _try_piece_guess(text: str) -> tuple[str, float] | None:
    """
    Возвращает: (ключ_для_FALLBACK, граммы)
    Пример: "5 вареников" -> ("вареник", 250.0)
    """
    m = _COUNT_PIECES_RE.search((text or "").strip().lower())
    if not m:
        return None

    n = int(m.group(1))
    name = (m.group(2) or "").strip()

    # ключ -> грамм за 1 шт
    defaults: dict[str, float] = {
        "вареник": 50.0,
        "пельмен": 12.0,
        "котлет": 100.0,
        "сосиск": 50.0,
        "яйц": 50.0,
        "яблок": 150.0,
    }

    # нормализация словоформ через "вхождение корня"
    for k, g_per_piece in defaults.items():
        if k in name:
            return (k, n * g_per_piece)

    return None


from app.keyboards import (
    get_main_kb,
    is_admin_btn,
    is_back_btn,
    is_calories_btn,
    is_report_bug_btn,
    is_root_assistant_btn,
    is_root_calories_btn,
    is_root_journal_btn,
    is_root_media_btn,
    is_root_premium_btn,
    is_root_reminders_btn,
    is_root_settings_btn,
    is_root_stats_btn,
)

try:
    from app.handlers.admin import is_admin_tg
except Exception:

    def is_admin_tg(tg_id: int, /) -> bool:
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
    lang_norm = _normalize_lang(lang)
    return uk if lang_norm == "uk" else en if lang_norm == "en" else ru


def _cal_hook_inline_kb(lang_code: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=_tr(lang_code, "✍️ Ввести списком", "✍️ Ввести списком", "✍️ Enter as list"),
        callback_data="cal:enter",
    )
    kb.button(
        text=_tr(
            lang_code,
            "📸 Отправить фото (Premium)",
            "📸 Надіслати фото (Premium)",
            "📸 Send photo (Premium)",
        ),
        callback_data="cal:photo",
    )
    kb.adjust(1, 1)
    return kb.as_markup()


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def _user_lang(user: Optional[User], fallback: Optional[str], tg_lang: Optional[str] = None) -> str:
    return _normalize_lang(getattr(user, "locale", None) or getattr(user, "lang", None) or fallback or tg_lang or "ru")


def _format_cal_total(lang_code: str, res: Dict[str, Any]) -> str:
    """Универсальный форматтер КБЖУ с эмодзи."""
    kcal = int(round(float(res.get("kcal", 0))))
    p = round(float(res.get("p", 0)), 1)
    f = round(float(res.get("f", 0)), 1)
    c = round(float(res.get("c", 0)), 1)

    # Локализация меток
    ln = _normalize_lang(lang_code)
    labels = {
        "ru": ("Б", "Ж", "У"),
        "uk": ("Б", "Ж", "В"),
        "en": ("P", "F", "C"),
    }
    lp, lf, lc = labels.get(ln, labels["ru"])

    return f"🔥 {kcal} ккал  |  🥩 {lp}: {p}  🥑 {lf}: {f}  🍞 {lc}: {c}"


def _human_confidence(conf: float, lang: str) -> str:
    if conf >= 0.85:
        return _tr(lang, "Высокая точность", "Висока точність", "High accuracy")
    if conf >= 0.60:
        return _tr(lang, "Примерная оценка", "Приблизна оцінка", "Approximate")
    return _tr(lang, "Низкая точность", "Низька точність", "Low accuracy")


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
    return {
        "kcal": 0.0,
        "p": 0.0,
        "f": 0.0,
        "c": 0.0,
        "confidence": conf,
        "zero_ok": 1.0,
    }


def _kcal_is_invalid(res: Optional[Dict[str, float]]) -> bool:
    if not res:
        return True
    kcal = float(res.get("kcal", 0) or 0)
    zero_ok = bool(res.get("zero_ok"))
    return (kcal <= 0) and (not zero_ok)


def _format_photo_details(lang_code: str, res: Dict[str, Any]) -> str:
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
        parts.append(_tr(lang_code, f"🍽 Блюдо: {title}", f"🍽 Страва: {title}", f"🍽 Dish: {title}"))
    if ingredients_list:
        joined = ", ".join(ingredients_list[:12])
        lbl = _tr(lang_code, "Состав", "Склад", "Ingredients")
        parts.append(f"🥗 {lbl}: {joined}")
    if portion:
        lbl = _tr(lang_code, "Порция", "Порція", "Portion")
        parts.append(f"⚖️ {lbl}: {portion}")
    if assumptions_list:
        joined = "; ".join(assumptions_list[:2])
        lbl = _tr(lang_code, "Допущения", "Припущення", "Assumptions")
        parts.append(f"🔍 {lbl}: {joined}")

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
    "яблок": 150,
    "apple": 150,
    "котлет": 100,
    "cutlet": 100,
    # стакан/чашка/кружка (очень грубо)
    "стакан": 250,
    "чашк": 250,
    "кружк": 300,
    "яйц": 50,
    "egg": 50,
    "банан": 120,
    "banana": 120,
    "хлеб": 30,
    "хліб": 30,
    "bread": 30,
    "сыр": 30,
    "сир": 30,
    "cheese": 30,
    "сосиск": 50,
    "sausage": 50,
    "куриц": 80,
    "курк": 80,
    "chicken": 80,
    "вареник": 50,
    "пельмен": 12,
}


REV_DRINK_KEYS = {
    "вода",
    "water",
    "кола",
    "coke",
    "coca",
    "pepsi",
    "сок",
    "juice",
    "чай",
    "tea",
    "кофе",
    "coffee",
    "молок",
    "milk",
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
    # Метод оставлен для совместимости, но логика вынесена в человеческий форматтер
    return out


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont | ImageFont.FreeTypeFont, max_w: int):
    lines = []
    # Чистим от тегов перед рендером
    clean_text = text.replace("<b>", "").replace("</b>", "")
    for paragraph in clean_text.split("\n"):
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
    bg = Image.new("RGB", (W, H), (20, 24, 28))
    draw = ImageDraw.Draw(bg)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 52)
        font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 38)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()

    draw.text((PAD, 40), "Nutrition AI", font=font_title, fill=(255, 255, 255))

    max_w = W - PAD * 2
    lines = _wrap_text(draw, text, font_body, max_w)
    y = 40 + 86
    for ln in lines[:11]:
        color = (220, 230, 240)
        if "🔥" in ln:
            color = (255, 215, 0)
        draw.text((PAD, y), ln, font=font_body, fill=color)
        y += 50

    buf = BytesIO()
    bg.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


def render_result_card(photo_bytes: bytes, text: str) -> bytes:
    W = 1080
    PAD = 48
    PANEL_MIN_H = 480

    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    scale = W / img.width
    new_h = int(img.height * scale)
    if new_h > 1200:
        new_h = 1200
        img = img.resize((W, new_h))
    else:
        img = img.resize((W, new_h))

    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not os.path.exists(font_path):
            font_path = "arial.ttf"
        font_body = ImageFont.truetype(font_path, 36)
    except Exception:
        font_body = ImageFont.load_default()

    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = _wrap_text(dummy, text, font_body, W - PAD * 2)

    text_h = len(lines) * 52 + 100
    panel_h = max(PANEL_MIN_H, text_h)

    out = Image.new("RGB", (W, new_h + panel_h), (20, 24, 28))
    out.paste(img, (0, 0))

    draw = ImageDraw.Draw(out)
    y = new_h + 40
    for ln in lines:
        color = (230, 230, 230)
        if "🔥" in ln or "kcal" in ln:
            color = (255, 215, 0)
        draw.text((PAD, y), ln, font=font_body, fill=color)
        y += 52

    buf = BytesIO()
    out.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue()


# -------------------- analyze text --------------------


async def analyze_text(text: str, lang_code: str = "ru") -> Dict[str, Any]:
    """
    1) Ninjas API (если есть ключ).
    2) Локальная база (FALLBACK) + Регулярки.
    3) OpenAI (GPT-4o-mini) — для сложных блюд ("борщ", "шаурма", "пюрешка с котлеткой").
    """
    # ---------------------------------------------------------
    # 1. API NINJAS (Оставляем как было)
    # ---------------------------------------------------------
    key = os.getenv("NINJAS_API_KEY") or os.getenv("NUTRITION_API_KEY")
    # Ninjas понимает только английский, поэтому используем его аккуратно
    # Если хочешь, можно вообще убрать этот блок, если GPT справляется лучше.
    if key and re.match(r"^[a-zA-Z0-9\s]+$", text):  # Простая проверка на латиницу
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.api-ninjas.com/v1/nutrition",
                    params={"query": text},
                    headers={"X-Api-Key": key},
                )
                if resp.status_code == 200:
                    items = resp.json()
                    if isinstance(items, list) and items:
                        kcal = sum(float(i.get("calories", 0) or 0) for i in items)
                        p = sum(float(i.get("protein_g", 0) or 0) for i in items)
                        f = sum(float(i.get("fat_total_g", 0) or 0) for i in items)
                        c = sum(float(i.get("carbohydrates_total_g", 0) or 0) for i in items)
                        return {
                            "kcal": round(kcal),
                            "p": round(p, 1),
                            "f": round(f, 1),
                            "c": round(c, 1),
                            "confidence": 0.90,
                            "title": text,  # Ninjas не возвращает название на русском
                        }
        except Exception:
            pass

    # ---------------------------------------------------------
    # 2. ЛОКАЛЬНЫЙ ПОИСК (Твоя база + Регулярки)
    # ---------------------------------------------------------
    if _is_water_only(text):
        return _zero_ok_result(0.95)

    low = (text or "").lower()

    # Нормализация (твоя логика)
    low = re.sub(r"(\d)(л|l|мл|ml)\b", r"\1 \2", low)
    low = re.sub(r"\bcola\b", "coke", low)
    low = re.sub(r"\bкол(?:а|ы|у|е|ой|ою)\b", "кола", low)

    is_dry_buckwheat = False
    if ("греч" in low or "buckwheat" in low) and ("сух" in low or "крупа" in low):
        low = re.sub(r"гречк\w*", "гречк_сух", low)
        is_dry_buckwheat = True

    piece_hint = _try_piece_guess(text)
    grams_info: list[tuple[float, Dict[str, float]]] = []

    # Твоя логика поиска "миска корма"
    if "миска" in low and ("корм" in low or "catfood" in low) and not re.search(r"\d+\s*(г|гр|g|мл|ml|л|l)\b", low):
        meta = FALLBACK.get("корм")
        if meta:
            # Возвращаем сразу, если нашли специфичный кейс
            return {
                "kcal": round(meta["kcal"] * 0.60),
                "p": round(meta["p"] * 0.60, 1),
                "f": round(meta["f"] * 0.60, 1),
                "c": round(meta["c"] * 0.60, 1),
                "confidence": 0.45,
                "title": "Корм (миска)",
            }

    # Твоя логика поиска по штукам
    if piece_hint and not re.search(r"\d+\s*(г|гр|g|мл|ml)\b", low):
        k, g = piece_hint
        if k in FALLBACK:
            grams_info.append((float(g), FALLBACK[k]))

    # Если нашли по штукам — возвращаем результат
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
            "confidence": 0.70,
            "title": piece_hint[0].capitalize(),
        }

    # Твоя логика поиска по граммам
    num = r"(\d+(?:[.,]\d+)?)"
    unit_re = r"(г|g|гр|ml|мл|л|l)"

    for name, meta in FALLBACK.items():
        if is_dry_buckwheat and name == "гречк":
            continue

        safe_name = re.escape(name)
        # Паттерн: "200 г риса"
        pattern = rf"{num}\s*{unit_re}\s*{safe_name}\w*"
        # Паттерн: "рис 200 г"
        unit_re_rev = r"(г|g|гр|мл|ml|л|l)" if name in REV_DRINK_KEYS else r"(г|g|гр)"
        pattern_rev = rf"{safe_name}\w*(?:\s+[а-яёa-z]+){{0,3}}\s*{num}\s*{unit_re_rev}\b"

        # Прямой поиск
        for m in re.finditer(pattern, low):
            try:
                qty = float(m.group(1).replace(",", "."))
                unit = (m.group(2) or "").lower()
                g = qty * 1000.0 if unit in ("л", "l") else qty
                grams_info.append((float(g), meta))
            except ValueError:
                continue

        # Обратный поиск
        for m in re.finditer(pattern_rev, low):
            try:
                qty = float(m.group(1).replace(",", "."))
                unit = (m.group(2) or "").lower()
                g = qty * 1000.0 if unit in ("л", "l") else qty
                grams_info.append((float(g), meta))
            except ValueError:
                continue

        # Дефолт порция (если не нашли вес, но нашли слово из базы PIECE_GRAMS)
        if (
            (not piece_hint)
            and name in PIECE_GRAMS
            and name in low
            and not re.search(pattern, low)
            and not re.search(pattern_rev, low)
        ):
            grams_info.append((float(PIECE_GRAMS[name]), meta))

    # Считаем результат локального поиска
    if grams_info:
        kcal = p = f = c = 0.0
        for g, meta in grams_info:
            factor = g / 100.0
            kcal += meta["kcal"] * factor
            p += meta["p"] * factor
            f += meta["f"] * factor
            c += meta["c"] * factor

        has_explicit_grams = bool(re.search(r"\d+\s*(г|гр|g|мл|ml|л|l)\b", low))
        confidence = 0.95 if has_explicit_grams else 0.70

        return {
            "kcal": round(kcal),
            "p": round(p, 1),
            "f": round(f, 1),
            "c": round(c, 1),
            "confidence": confidence,
            # Можно попытаться угадать название из найденного, но оставим пустым, форматтер разберется
        }

    # ---------------------------------------------------------
    # 3. OPENAI "SMART TRACK" (Если локально не нашли)
    # ---------------------------------------------------------
    # Если мы здесь, значит локальная база не справилась.
    # Зовем GPT-4o-mini, чтобы он понял что такое "шаурма" или "борщ со сметаной"

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"kcal": 0, "p": 0, "f": 0, "c": 0, "confidence": 0.0}

    lang_name = "Russian"
    if lang_code == "uk":
        lang_name = "Ukrainian"
    if lang_code == "en":
        lang_name = "English"

    prompt = (
        f"Act as a professional nutritionist. Analyze this food text in {lang_name}: '{text}'. "
        "Calculate approximate total calories (kcal), proteins (p), fats (f), and carbohydrates (c). "
        "If the portion size is not specified, assume a standard average serving size for an adult. "
        "Return ONLY a valid JSON object with keys: kcal (number), p (number), f (number), c (number), "
        "title (string, short summary of the food in the target language), and confidence (number 0.0-1.0). "
        f"Ensure all text fields are in {lang_name}."
    )

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "gpt-4o-mini",  # Дешевый и быстрый
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 200,
                },
            )
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)

            # Валидация ответа
            return {
                "kcal": float(result.get("kcal", 0)),
                "p": float(result.get("p", 0)),
                "f": float(result.get("f", 0)),
                "c": float(result.get("c", 0)),
                "confidence": float(result.get("confidence", 0.8)),  # Доверяем AI, если он вернул JSON
                "title": result.get("title", text),
            }

    except Exception as e:
        logging.error(f"Text AI Analysis Error: {e}")
        return {"kcal": 0, "p": 0, "f": 0, "c": 0, "confidence": 0.0}


# -------------------- photo analyze (OpenAI Vision) --------------------


async def _download_photo_bytes(message: types.Message) -> Optional[bytes]:
    if not message.photo:
        return None

    ph = message.photo[-1]
    try:
        f = await message.bot.get_file(ph.file_id)
        if not f.file_path:
            return None
        buf = BytesIO()
        await message.bot.download_file(f.file_path, destination=buf)
        return buf.getvalue()
    except Exception:
        return None


async def analyze_photo(message: types.Message, lang_code: str = "ru") -> Optional[Dict[str, Any]]:
    """
    OpenAI Vision (Responses API) с динамическим языком.
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

    lang_name = "Russian"
    if lang_code == "uk":
        lang_name = "Ukrainian"
    if lang_code == "en":
        lang_name = "English"

    prompt = (
        f"Identify the food. Reply in {lang_name}. "
        "Return ONLY valid JSON (no markdown) with fields: "
        '{"title": string, "ingredients": array, "portion": string, '
        '"kcal": number, "p": number, "f": number, "c": number, '
        '"confidence": number, "assumptions": array}. '
        "Estimate total calories. "
        f"IMPORTANT: All text fields must be in {lang_name}!"
    )

    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
        "max_output_tokens": 350,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            j = r.json()

        txt = j.get("output_text")
        if not txt:
            out = j.get("output") or []
            chunks = []
            for item in out:
                if item.get("type") == "message":
                    for part in item.get("content") or []:
                        if part.get("type") in ("output_text", "text"):
                            chunks.append(part.get("text", ""))
            txt = "\n".join(chunks).strip()

        if not txt:
            return None

        txt = re.sub(r"```json|```", "", txt).strip()
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
async def cal_cmd(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
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
        card = render_text_card(out)
        await message.answer_photo(BufferedInputFile(card, filename="kcal.jpg"))
        return

    await state.set_state(CaloriesFSM.waiting_input)

    hook = _tr(
        lang_code,
        """🔥 Калории — быстро и без занудства

✅ Напиши списком, что ты съел/выпил
Или отправь фото еды (💎 Премиум)

Я посчитаю: ккал • Б/Ж/У""",
        """🔥 Калорії — швидко і без занудства

✅ Напиши списком, що ти з'їв/випив
Або надішли фото їжі (💎 Преміум)

Я порахую: ккал • Б/Ж/В""",
        """🔥 Calories — fast, no fluff

✅ Send your food list
Or food photo (💎 Premium)""",
    )

    await message.answer(hook, reply_markup=_cal_hook_inline_kb(lang_code))


@router.message(F.text.func(is_calories_btn))
async def cal_btn(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    await state.set_state(CaloriesFSM.waiting_input)

    hook = _tr(
        lang_code,
        "Кидай список еды одним сообщением или фото.\nПример: «250 мл молока, банан, 40 г арахиса»",
        "Кидай список їжі одним повідомленням або фото.\nПриклад: «250 мл молока, банан, 40 г арахісу»",
        "Send your food list or photo.\nExample: 250ml milk, 1 banana, 40g peanuts",
    )

    await message.answer(hook, reply_markup=_cal_hook_inline_kb(lang_code))


# -------------------- callbacks --------------------


@router.callback_query(F.data == "cal:enter")
async def cal_enter_cb(cb: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CaloriesFSM.waiting_input)
    await cb.answer()
    await cb.message.answer("✍️")


@router.callback_query(F.data == "cal:photo")
async def cal_photo_cb(
    cb: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    tg_lang = getattr(cb.from_user, "language_code", None)
    user = await _get_user(session, cb.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    msg = cb.message
    if msg is None or not isinstance(msg, types.Message):
        await cb.answer()
        return

    ok = await _require_photo_premium(msg, session, user, lang_code, source="hook_button")
    if not ok:
        return

    await state.set_state(CaloriesFSM.waiting_photo)
    await cb.answer()
    await cb.message.answer("📸")


# -------------------- cancel --------------------


@router.message(Command("cancel"))
async def cal_cancel_global(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
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
async def cal_text_in_mode(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
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
    await message.answer(out)


# -------------------- MODE: waiting_photo --------------------


@router.message(CaloriesFSM.waiting_photo, F.photo)
async def cal_photo_waiting(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    lang: Optional[str] = None,
) -> None:
    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    ok = await _require_photo_premium(message, session, user, lang_code, source="waiting_photo")
    if not ok:
        return

    wait_msg = await message.answer("⏳ ...")
    res = await analyze_photo(message, lang_code=lang_code)
    await wait_msg.delete()

    if not res:
        await message.answer("Не удалось распознать еду.")
        return

    conf = float(res.get("confidence", 0) or 0)
    details = _format_photo_details(lang_code, res)
    total_line = _format_cal_total(lang_code, res)
    conf_str = _human_confidence(conf, lang_code)

    card_text = f"{details}\n\n{total_line}\n\n🎯 {conf_str}".replace("<b>", "").replace("</b>", "")

    img_bytes = await _download_photo_bytes(message)
    if img_bytes:
        card = render_result_card(img_bytes, card_text)
        await message.answer_photo(
            BufferedInputFile(card, filename="calories.jpg"), caption=f"{details}\n\n{total_line}", parse_mode="HTML"
        )
    else:
        await message.answer(f"{details}\n\n{total_line}")
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
        return
    out = _format_cal_total(lang_code, res)
    await message.answer(out)


# -------------------- photo with caption trigger --------------------


@router.message(F.photo)
async def cal_photo_caption_trigger(message: types.Message, session: AsyncSession, lang: Optional[str] = None) -> None:
    caption = (message.caption or "").strip()
    if not caption:
        return

    low = caption.lower()
    is_cmd = low.startswith(("/calories", "/kcal"))
    is_trigger = any(x in low for x in ("кал", "кбжу", "cal"))
    if not (is_cmd or is_trigger):
        return

    tg_lang = getattr(getattr(message, "from_user", None), "language_code", None)
    user = await _get_user(session, message.from_user.id)
    lang_code = _user_lang(user, lang, tg_lang)

    ok = await _require_photo_premium(message, session, user, lang_code, source="photo_caption_trigger")
    if not ok:
        return

    wait_msg = await message.answer("⏳ ...")
    res = await analyze_photo(message, lang_code=lang_code)
    await wait_msg.delete()

    if not res:
        await message.answer("Не удалось распознать еду.")
        return

    conf = float(res.get("confidence", 0) or 0)
    details = _format_photo_details(lang_code, res)
    total_line = _format_cal_total(lang_code, res)
    conf_str = _human_confidence(conf, lang_code)

    card_text = f"{details}\n\n{total_line}\n\n🎯 {conf_str}".replace("<b>", "").replace("</b>", "")

    img_bytes = await _download_photo_bytes(message)
    if img_bytes:
        card = render_result_card(img_bytes, card_text)
        await message.answer_photo(BufferedInputFile(card, filename="calories.jpg"))
    else:
        await message.answer(f"{details}\n\n{total_line}")


__all__ = ["router"]
