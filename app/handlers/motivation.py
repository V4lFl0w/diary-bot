from __future__ import annotations

import random
from datetime import timezone
from typing import Optional
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.services.quotes_bank import generate_quote

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # type: ignore

router = Router(name="motivation")

# Кнопки — человеческие и понятные
BTN_SUPPORT = "💬 Поддержка (1 строка)"
BTN_JUMP = "⚡ Святой прыжок (15 минут)"
BTN_COMEBACK = "🔄 Вернуться (без вины)"
BTN_QUOTE = "🪶 Цитата (новая)"
BTN_STREAK = "🏆 Серия (дни)"
BTN_BACK = "⬅️ Назад"

OPEN_TRIGGERS = (
    "🥇 Мотивация",
    "🥇 Мотивація",
    "🥇 Motivation",
    "Мотивация",
    "Мотивація",
    "Motivation",
)


class MotStates(StatesGroup):
    waiting_support = State()
    waiting_jump = State()
    waiting_comeback = State()


def _kb() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_SUPPORT), KeyboardButton(text=BTN_JUMP)],
        [KeyboardButton(text=BTN_COMEBACK), KeyboardButton(text=BTN_STREAK)],
        [KeyboardButton(text=BTN_QUOTE), KeyboardButton(text=BTN_BACK)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


def _user_lang(user: Optional[User], tg_lang: Optional[str]) -> str:
    raw = (getattr(user, "locale", None) or getattr(user, "lang", None)) if user is not None else None
    loc = (raw or tg_lang or "ru").lower()
    if loc.startswith(("ua", "uk")):
        return "uk"
    if loc.startswith("en"):
        return "en"
    return "ru"


def _user_tz(user: User):
    tz_name = getattr(user, "tz", None) or "Europe/Kyiv"
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _t(lang: str, ru: str, uk: str, en: str) -> str:
    if lang == "uk":
        return uk
    if lang == "en":
        return en
    return ru


# ---- support infinite combinatorics (ru/uk/en) ----
_SUPPORT_BANK = {
    "ru": {
        "openers": [
            "Слышу тебя.",
            "Я рядом.",
            "Ок, я понял(а).",
            "Понял(а).",
            "Принял(а).",
            "Дышим.",
            "Стоп. Я тут.",
            "Спасибо, что написал(а).",
            "Не один(одна). Я здесь.",
            "Я с тобой.",
            "Сейчас без героизма.",
            "По-человечески:",
        ],
        "validate": [
            "То, что ты это чувствуешь — нормально.",
            "Это не делает тебя слабым(ой).",
            "Это не приговор — это сигнал.",
            "Тебя можно понять.",
            "С тобой всё ок — ты просто устал(а).",
            "Да, так бывает.",
            "Это реально может давить.",
            "Ты не обязан(а) тащить в одиночку.",
        ],
        "reframe": [
            "Нам не нужна идеальность — нам нужен один шаг.",
            "Не надо чинить всю жизнь — только ближайшие 2 минуты.",
            "Сейчас важнее действие, чем настроение.",
            "Сначала маленькое — потом станет легче.",
            "Мы не ускоряемся — мы стабилизируемся.",
            "Один микро-шаг возвращает контроль.",
        ],
        "micro": [
            "Сделай вдох/выдох 3 раза.",
            "Поставь таймер на 2 минуты.",
            "Открой задачу, но ничего не делай 10 секунд — просто посмотри.",
            "Убери одну помеху (закрой лишнюю вкладку/чат).",
            "Сядь ровно и расслабь плечи.",
            "Сделай глоток воды.",
            "Напиши одно слово: что важнее всего прямо сейчас?",
        ],
        "next": [
            "Выбирай кнопку ниже 👇",
            "Какой вариант берём?",
            "Что тебе нужно сейчас больше всего?",
            "Давай выберем один вариант и пойдём.",
            "Ок — выбираем следующий шаг.",
            "Дальше — только один пункт.",
        ],
    },
    "uk": {
        "openers": [
            "Чую тебе.",
            "Я поруч.",
            "Ок, зрозумів(ла).",
            "Зрозумів(ла).",
            "Прийняв(ла).",
            "Дихаємо.",
            "Стоп. Я тут.",
            "Дякую, що написав(ла).",
            "Ти не один(одна). Я тут.",
            "Я з тобою.",
            "Зараз без героїзму.",
            "По-людськи:",
        ],
        "validate": [
            "Те, що ти це відчуваєш — нормально.",
            "Це не робить тебе слабким(ою).",
            "Це не вирок — це сигнал.",
            "Тебе можна зрозуміти.",
            "З тобою все ок — ти просто втомився(лась).",
            "Так буває.",
            "Це реально може тиснути.",
            "Ти не мусиш тягнути сам(а).",
        ],
        "reframe": [
            "Нам не потрібна ідеальність — потрібен один крок.",
            "Не треба лагодити все життя — лише найближчі 2 хвилини.",
            "Зараз важливіша дія, ніж настрій.",
            "Спочатку маленьке — потім стане легше.",
            "Ми не прискорюємось — ми стабілізуємось.",
            "Один мікро-крок повертає контроль.",
        ],
        "micro": [
            "Зроби вдих/видих 3 рази.",
            "Постав таймер на 2 хвилини.",
            "Відкрий задачу й 10 секунд просто подивись — без дій.",
            "Прибери одну перешкоду (закрий зайву вкладку/чат).",
            "Сядь рівно й розслаб плечі.",
            "Зроби ковток води.",
            "Напиши одне слово: що найважливіше просто зараз?",
        ],
        "next": [
            "Обирай кнопку нижче 👇",
            "Який варіант беремо?",
            "Що тобі потрібно зараз найбільше?",
            "Обираємо один варіант і йдемо.",
            "Ок — обираємо наступний крок.",
            "Далі — лише один пункт.",
        ],
    },
    "en": {
        "openers": [
            "I hear you.",
            "I’m here.",
            "Ok, got you.",
            "Got it.",
            "Accepted.",
            "Breathe.",
            "Pause. I’m here.",
            "Thanks for saying it.",
            "You’re not alone — I’m here.",
            "I’m with you.",
            "No hero mode right now.",
            "Human truth:",
        ],
        "validate": [
            "Feeling this is normal.",
            "This doesn’t make you weak.",
            "It’s not a sentence — it’s a signal.",
            "It makes sense.",
            "You’re not broken — you’re tired.",
            "Yeah, it happens.",
            "This can genuinely feel heavy.",
            "You don’t have to carry it alone.",
        ],
        "reframe": [
            "We don’t need perfection — we need one step.",
            "Don’t fix your whole life — just the next 2 minutes.",
            "Action matters more than mood right now.",
            "Start small — it gets easier.",
            "We’re not rushing — we’re stabilizing.",
            "One micro-step brings control back.",
        ],
        "micro": [
            "Take 3 slow breaths.",
            "Set a 2-minute timer.",
            "Open the task and just look at it for 10 seconds — no action.",
            "Remove one blocker (close one tab/chat).",
            "Relax your shoulders.",
            "Drink a sip of water.",
            "Write one word: what matters most right now?",
        ],
        "next": [
            "Pick a button below 👇",
            "Which option do we take?",
            "What do you need most right now?",
            "Let’s pick one option and move.",
            "Ok — choose the next step.",
            "Next — only one item.",
        ],
    },
}


def generate_support(lang: str, user_text: str, *, seed: int | None = None) -> str:
    # seed можно не задавать — тогда будет максимально “живое”
    # user_text участвует в тексте => уже добавляет уникальности
    lang = (lang or "ru").lower()
    if lang.startswith(("ua", "uk")):
        lang = "uk"
    elif lang.startswith("en"):
        lang = "en"
    else:
        lang = "ru"

    b = _SUPPORT_BANK[lang]
    rnd = random.Random(seed) if seed is not None else random

    # “склейка” из 5 частей => огромная комбинаторика
    opener = rnd.choice(b["openers"])
    validate = rnd.choice(b["validate"])
    reframe = rnd.choice(b["reframe"])
    micro = rnd.choice(b["micro"])
    nxt = rnd.choice(b["next"])

    # user_text аккуратно вставляем (не всегда первым)
    if lang == "en":
        echo = f"“{user_text}”"
    else:
        echo = f"«{user_text}»"

    # иногда ставим echo в начале, иногда — в середине (ещё вариативность)
    if rnd.random() < 0.5:
        line1 = f"{opener} {echo}"
    else:
        line1 = f"{opener} {validate}"

    # итоговый текст
    return f"{line1}\n\n{validate}\n{reframe}\n\n{micro}\n\n{nxt}"


# ---- /support infinite combinatorics ----


@router.message(Command("cancel"))
async def motivation_cancel(m: Message, state: FSMContext):
    cur = await state.get_state()
    if not cur or not cur.startswith("MotStates:"):
        return  # не наша отмена

    await state.clear()
    # Возвращаем меню мотивации
    await m.answer("Ок, отменил. Выбирай кнопку ниже 👇", reply_markup=_kb())


def _is_motivation_open(text: str) -> bool:
    t = (text or "").strip().lower()
    # убираем ведущие эмодзи/символы
    t = t.lstrip("🥇🔥⭐️✅⚡️⚡🏅 ").strip()
    return t in {"мотивация", "мотивація", "motivation"}


@router.message(F.text.func(_is_motivation_open))
async def motivation_open(m: Message, session: AsyncSession, state: FSMContext):
    if not m.text or not _is_motivation_open(m.text):
        return
    await state.clear()
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    text = _t(
        lang,
        "🥇 Мотивация\n\n"
        "Я здесь, чтобы быстро вернуть тебе энергию и ясность.\n"
        "Чтобы о твоём следующем шаге говорили всем: «как он(а) это смог(ла)?»\n\n"
        "Выбери, что нужно прямо сейчас:",
        "🥇 Мотивація\n\n"
        "Я тут, щоб швидко повернути тобі енергію й ясність.\n"
        "Щоб про твій наступний крок казали всім: «як він(вона) це зміг(змогла)?»\n\n"
        "Обери, що треба просто зараз:",
        "🥇 Motivation\n\n"
        "I’m here to quickly bring back your energy and clarity.\n"
        "So everyone thinks about your next step: “how did he/she do that?”\n\n"
        "Pick what you need right now:",
    )

    await m.answer(text, reply_markup=_kb())


@router.message(F.text == BTN_SUPPORT)
async def motivation_support_start(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await state.set_state(MotStates.waiting_support)
    await m.answer(
        _t(
            lang,
            "💬 Поддержка\n\nНапиши ОДНУ строку: что сейчас внутри?\n(пример: «страшно», «злюсь», «пусто», «давит»)\n\nОтмена: /cancel",
            "💬 Підтримка\n\nНапиши ОДИН рядок: що зараз всередині?\n(приклад: «страшно», «злюсь», «порожньо», «тисне»)\n\nСкасування: /cancel",
            "💬 Support\n\nWrite ONE line: what’s inside right now?\n(example: “scared”, “angry”, “empty”, “pressure”)\n\nCancel: /cancel",
        )
    )


@router.message(MotStates.waiting_support, F.text)
async def motivation_support_reply(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    txt = (m.text or "").strip()
    await state.clear()

    msg = generate_support(lang, txt)
    await m.answer(msg, reply_markup=_kb())


@router.message(F.text == BTN_JUMP)
async def motivation_jump_start(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await state.set_state(MotStates.waiting_jump)
    await m.answer(
        _t(
            lang,
            "⚡ Святой прыжок (15 минут)\n\n"
            "Выбери ОДНУ мини-задачу на 15 минут и напиши её одной строкой.\n"
            "Пример: «делаю: 2 звонка» / «делаю: черновик 1 экрана»\n\n"
            "Отмена: /cancel",
            "⚡ Святой прыжок (15 хв)\n\n"
            "Обери ОДНУ міні-задачу на 15 хв і напиши одним рядком.\n"
            "Приклад: «роблю: 2 дзвінки» / «роблю: чернетку 1 екрану»\n\n"
            "Скасування: /cancel",
            "⚡ Holy jump (15 min)\n\n"
            "Pick ONE mini task for 15 minutes and write it in one line.\n"
            "Example: “doing: 2 calls” / “doing: draft 1 screen”\n\n"
            "Cancel: /cancel",
        )
    )


@router.message(MotStates.waiting_jump, F.text)
async def motivation_jump_reply(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    task = (m.text or "").strip()
    await state.clear()

    await m.answer(
        _t(
            lang,
            f"Принято ✅\n\nТвоя задача: «{task}»\n\n"
            "Сделай старт на 2 минуты прямо сейчас.\n"
            "Потом напиши: «Готово» — я закреплю смысл и дам следующий шаг.\n\n"
            "Если тяжко — нажми 💬 Поддержка.",
            f"Прийнято ✅\n\nТвоя задача: «{task}»\n\n"
            "Почни з 2 хвилин просто зараз.\n"
            "Потім напиши: «Готово» — я закріплю сенс і дам наступний крок.\n\n"
            "Якщо важко — натисни 💬 Підтримка.",
            f"Accepted ✅\n\nYour task: “{task}”\n\n"
            "Start with 2 minutes right now.\n"
            "Then reply: “Done” — I’ll lock the win and give the next step.\n\n"
            "If it’s heavy — tap 💬 Support.",
        ),
        reply_markup=_kb(),
    )


@router.message(F.text.casefold().in_({"готово", "done"}))
async def motivation_done(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await m.answer(
        _t(
            lang,
            "Красавчик ✅\n"
            "Теперь самое важное: не потерять импульс.\n\n"
            "Выбери:\n"
            "1) ещё 15 минут (продолжаю)\n"
            "2) закрываю и фиксирую (стоп)\n\n"
            "Напиши: «ещё 15» или «стоп».",
            "Красень ✅\n"
            "Тепер головне: не втратити імпульс.\n\n"
            "Обери:\n"
            "1) ще 15 хв (продовжую)\n"
            "2) закриваю і фіксую (стоп)\n\n"
            "Напиши: «ще 15» або «стоп».",
            "Nice ✅\n"
            "Now the key: keep the impulse.\n\n"
            "Choose:\n"
            "1) another 15 min (continue)\n"
            "2) stop and lock it (stop)\n\n"
            "Reply: “another 15” or “stop”.",
        ),
        reply_markup=_kb(),
    )


@router.message(F.text.casefold().in_({"еще 15", "ещё 15", "another 15"}))
async def motivation_more_15(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await m.answer(
        _t(
            lang,
            "Погнали 🥇\nПоставь таймер на 15 минут и просто делай.\nПосле — напиши «Готово».",
            "Погнали 🥇\nПостав таймер на 15 хв і просто роби.\nПісля — напиши «Готово».",
            "Let’s go 🥇\nSet a 15-min timer and just do it.\nAfter — reply “Done”.",
        )
    )


@router.message(F.text.casefold().in_({"стоп", "stop"}))
async def motivation_stop(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await m.answer(
        _t(
            lang,
            "Зафиксировал ✅\n\nОдин честный шаг сделан.\nХочешь — возьми 🪶 Цитату (новая) для закрепления.",
            "Зафіксував ✅\n\nОдин чесний крок зроблено.\nХочеш — візьми 🪶 Цитату (нова) для закріплення.",
            "Locked ✅\n\nOne honest step is done.\nIf you want — grab 🪶 New quote to seal it.",
        ),
        reply_markup=_kb(),
    )


@router.message(F.text == BTN_COMEBACK)
async def motivation_comeback_start(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await state.set_state(MotStates.waiting_comeback)
    await m.answer(
        _t(
            lang,
            "🔄 Вернуться (без вины)\n\n"
            "Одна строка: что сейчас важно вернуть под контроль?\n"
            "Пример: «сон», «деньги», «проект», «отношения», «здоровье»\n\n"
            "Отмена: /cancel",
            "🔄 Повернутися (без провини)\n\n"
            "Один рядок: що важливо повернути під контроль?\n"
            "Приклад: «сон», «гроші», «проєкт», «стосунки», «здоров’я»\n\n"
            "Скасування: /cancel",
            "🔄 Come back (no guilt)\n\n"
            "One line: what do you want back under control?\n"
            "Example: sleep, money, project, relationships, health\n\n"
            "Cancel: /cancel",
        )
    )


@router.message(MotStates.waiting_comeback, F.text)
async def motivation_comeback_reply(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    focus = (m.text or "").strip()
    await state.clear()

    await m.answer(
        _t(
            lang,
            f"Ок. Возвращаем «{focus}» ✅\n\n"
            "Сейчас — один микро-шаг на 2 минуты.\n"
            "Если хочешь, я дам толчок: нажми ⚡ Святой прыжок (15 минут).",
            f"Ок. Повертаємо «{focus}» ✅\n\n"
            "Зараз — один мікро-крок на 2 хвилини.\n"
            "Якщо хочеш, дам поштовх: натисни ⚡ Святой прыжок (15 хв).",
            f"Ok. We bring back “{focus}” ✅\n\n"
            "Now — one 2-minute micro step.\n"
            "If you want a push: tap ⚡ Holy jump (15 min).",
        ),
        reply_markup=_kb(),
    )


@router.message(F.text == BTN_STREAK)
async def motivation_streak(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    streak = 0
    if user is not None and hasattr(user, "proactive_streak"):
        try:
            streak = int(getattr(user, "proactive_streak") or 0)
        except Exception:
            streak = 0

    if streak <= 0:
        msg = _t(
            lang,
            "🏆 Серия: 0 дней.\nХочешь начать? Сделай сегодня один маленький шаг — и поехали.",
            "🏆 Серія: 0 днів.\nХочеш почати? Зроби сьогодні один маленький крок — і поїхали.",
            "🏆 Streak: 0 days.\nWant to start? Take one small step today — and we go.",
        )
    else:
        msg = _t(
            lang,
            f"🏆 Серия: {streak} дн.\nТы держишь темп. Продолжим сегодня?",
            f"🏆 Серія: {streak} дн.\nТи тримаєш темп. Продовжимо сьогодні?",
            f"🏆 Streak: {streak} days.\nYou’re keeping the pace. Continue today?",
        )

    await m.answer(msg, reply_markup=_kb())


@router.message(F.text == BTN_QUOTE)
async def motivation_quote(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    # “бесконечность”: каждый раз новая комбинация
    await m.answer(generate_quote(lang))


@router.message(F.text == BTN_BACK)
async def motivation_back(m: Message):
    # меню:home у тебя есть в другом модуле, тут не ломаем — просто текстом
    await m.answer("Ок. Возвращаю назад 👇", reply_markup=None)
