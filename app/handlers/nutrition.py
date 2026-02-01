from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.services.nutrition import NutritionError, fetch_nutrition

router = Router()

TR = {
    "ru": {
        "ask": 'Напиши продукты одной строкой. Пример: "200 г курицы, 100 г риса, яблоко"',
        "processing": "Считаю калории…",
        "result": "Суммарно: {cal} ккал\nБ: {p} г, Ж: {f} г, У: {c} г",
        "error": "Не смог получить данные по калориям: {msg}",
        "empty": "Я не вижу текст. Напиши продукты одной строкой 🙂",
    },
    "uk": {
        "ask": 'Напиши продукти одним рядком. Наприклад: "200 г курки, 100 г рису, яблуко"',
        "processing": "Рахую калорії…",
        "result": "Разом: {cal} ккал\nБ: {p} г, Ж: {f} г, В: {c} г",
        "error": "Не вдалось отримати дані по калоріях: {msg}",
        "empty": "Я не бачу текст. Напиши продукти одним рядком 🙂",
    },
    "en": {
        "ask": 'Write your foods in one line. Example: "200 g chicken, 100 g rice, 1 apple"',
        "processing": "Calculating calories…",
        "result": "Total: {cal} kcal\nP: {p} g, F: {f} g, C: {c} g",
        "error": "Could not get nutrition data: {msg}",
        "empty": "I can't see any text. Write your foods in one line 🙂",
    },
}


def _t(lang: str | None, key: str, tr: dict, **kwargs) -> str:
    loc = (lang or "ru")[:2].lower()
    if loc == "ua":
        loc = "uk"
    tpl = tr.get(loc, tr.get("ru", {})).get(key, key)
    return tpl.format(**kwargs)


@router.message(Command("calories"))
async def calories_cmd(m: Message, lang: str):
    await m.answer(_t(lang, "ask", TR), parse_mode=None)


@router.message(F.text.lower().contains("калор"))
async def calories_text(m: Message, lang: str):
    await m.answer(_t(lang, "processing", TR), parse_mode=None)

    query = (m.text or "").strip()
    if not query:
        await m.answer(_t(lang, "empty", TR), parse_mode=None)
        return

    try:
        total, _items = await fetch_nutrition(query)
    except NutritionError as e:
        await m.answer(_t(lang, "error", TR, msg=str(e)), parse_mode=None)
        return

    text = _t(
        lang,
        "result",
        TR,
        cal=round(float(total.get("calories", 0.0)), 1),
        p=round(float(total.get("protein", 0.0)), 1),
        f=round(float(total.get("fat", 0.0)), 1),
        c=round(float(total.get("carbohydrates", 0.0)), 1),
    )
    await m.answer(text, parse_mode=None)
