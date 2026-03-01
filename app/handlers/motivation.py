from __future__ import annotations

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
from app.services.assistant import run_assistant

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # type: ignore

router = Router(name="motivation")

# Кнопки — человеческие и понятные
BTN_SUPPORT = {"ru": "💬 Поддержка (1 строка)", "uk": "💬 Підтримка (1 рядок)", "en": "💬 Support (1 line)"}
BTN_JUMP = {"ru": "⚡ Святой прыжок (15 минут)", "uk": "⚡ Святий стрибок (15 хв)", "en": "⚡ Holy jump (15 min)"}
BTN_COMEBACK = {"ru": "🔄 Вернуться (без вины)", "uk": "🔄 Повернутися (без провини)", "en": "🔄 Come back (no guilt)"}
BTN_QUOTE = {"ru": "🪶 Цитата (новая)", "uk": "🪶 Цитата (нова)", "en": "🪶 Quote (new)"}
BTN_STREAK = {"ru": "🏆 Серия (дни)", "uk": "🏆 Серія (дні)", "en": "🏆 Streak (days)"}
BTN_BACK = {"ru": "⬅️ Назад", "uk": "⬅️ Назад", "en": "⬅️ Back"}

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


def _kb(lang: str) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_SUPPORT.get(lang, BTN_SUPPORT["ru"])), KeyboardButton(text=BTN_JUMP.get(lang, BTN_JUMP["ru"]))],
        [KeyboardButton(text=BTN_COMEBACK.get(lang, BTN_COMEBACK["ru"])), KeyboardButton(text=BTN_STREAK.get(lang, BTN_STREAK["ru"]))],
        [KeyboardButton(text=BTN_QUOTE.get(lang, BTN_QUOTE["ru"])), KeyboardButton(text=BTN_BACK.get(lang, BTN_BACK["ru"]))],
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


@router.message(Command("cancel"))
async def motivation_cancel(m: Message, state: FSMContext, session: AsyncSession):
    cur = await state.get_state()
    if not cur or not cur.startswith("MotStates:"):
        return  # не наша отмена

    await state.clear()
    
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)
    
    text = _t(
        lang,
        "Ок, отменил. Выбирай кнопку ниже 👇",
        "Ок, скасував. Обирай кнопку нижче 👇",
        "Ok, cancelled. Choose a button below 👇"
    )
    # Возвращаем меню мотивации на нужном языке
    await m.answer(text, reply_markup=_kb(lang))


def _is_motivation_open(text: str) -> bool:
    t = (text or "").strip().lower()
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
        "🥇 Мотивация\n\nЯ здесь, чтобы быстро вернуть тебе энергию и ясность.\nЧтобы о твоём следующем шаге говорили всем: «как он(а) это смог(ла)?»\n\nВыбери, что нужно прямо сейчас:",
        "🥇 Мотивація\n\nЯ тут, щоб швидко повернути тобі енергію й ясність.\nЩоб про твій наступний крок казали всім: «як він(вона) це зміг(змогла)?»\n\nОбери, що треба просто зараз:",
        "🥇 Motivation\n\nI’m here to quickly bring back your energy and clarity.\nSo everyone thinks about your next step: “how did he/she do that?”\n\nPick what you need right now:",
    )

    await m.answer(text, reply_markup=_kb(lang))


@router.message(F.text.in_(set(BTN_SUPPORT.values())))
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

    wait_msg = await m.answer("⏳")

    prompt = (
        f"Пользователь написал в разделе Мотивации/Поддержки: «{txt}».\n"
        "Правила ответа (максимум 3-4 короткие строки):\n"
        "1. Если сообщение позитивное (радость, успех, всё хорошо) — похвали, дай заряд энергии, скажи что он красавчик. НИКАКОГО УТЕШЕНИЯ И ЖАЛОСТИ!\n"
        "2. Если сообщение негативное (усталость, грусть, страх) — дай короткую эмпатичную поддержку без воды и один микро-совет.\n"
        f"ОТВЕЧАЙ СТРОГО НА ЯЗЫКЕ: {lang}\n"
    )

    reply = await run_assistant(user, prompt, lang, session=session)

    await wait_msg.delete()
    await m.answer(reply, reply_markup=_kb(lang))


@router.message(F.text.in_(set(BTN_JUMP.values())))
async def motivation_jump_start(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await state.set_state(MotStates.waiting_jump)
    await m.answer(
        _t(
            lang,
            "⚡ Святой прыжок (15 минут)\n\nВыбери ОДНУ мини-задачу на 15 минут и напиши её одной строкой.\nПример: «делаю: 2 звонка» / «делаю: черновик 1 экрана»\n\nОтмена: /cancel",
            "⚡ Святий стрибок (15 хв)\n\nОбери ОДНУ міні-задачу на 15 хв і напиши одним рядком.\nПриклад: «роблю: 2 дзвінки» / «роблю: чернетку 1 екрану»\n\nСкасування: /cancel",
            "⚡ Holy jump (15 min)\n\nPick ONE mini task for 15 minutes and write it in one line.\nExample: “doing: 2 calls” / “doing: draft 1 screen”\n\nCancel: /cancel",
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
            f"Принято ✅\n\nТвоя задача: «{task}»\n\nСделай старт на 2 минуты прямо сейчас.\nПотом напиши: «Готово» — я закреплю смысл и дам следующий шаг.\n\nЕсли тяжко — нажми 💬 Поддержка.",
            f"Прийнято ✅\n\nТвоя задача: «{task}»\n\nПочни з 2 хвилин просто зараз.\nПотім напиши: «Готово» — я закріплю сенс і дам наступний крок.\n\nЯкщо важко — натисни 💬 Підтримка.",
            f"Accepted ✅\n\nYour task: “{task}”\n\nStart with 2 minutes right now.\nThen reply: “Done” — I’ll lock the win and give the next step.\n\nIf it’s heavy — tap 💬 Support.",
        ),
        reply_markup=_kb(lang),
    )


@router.message(F.text.casefold().in_({"готово", "done"}))
async def motivation_done(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await m.answer(
        _t(
            lang,
            "Красавчик ✅\nТеперь самое важное: не потерять импульс.\n\nВыбери:\n1) ещё 15 минут (продолжаю)\n2) закрываю и фиксирую (стоп)\n\nНапиши: «ещё 15» или «стоп».",
            "Красень ✅\nТепер головне: не втратити імпульс.\n\nОбери:\n1) ще 15 хв (продовжую)\n2) закриваю і фіксую (стоп)\n\nНапиши: «ще 15» або «стоп».",
            "Nice ✅\nNow the key: keep the impulse.\n\nChoose:\n1) another 15 min (continue)\n2) stop and lock it (stop)\n\nReply: “another 15” or “stop”.",
        ),
        reply_markup=_kb(lang),
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
        reply_markup=_kb(lang),
    )


@router.message(F.text.in_(set(BTN_COMEBACK.values())))
async def motivation_comeback_start(m: Message, session: AsyncSession, state: FSMContext):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    await state.set_state(MotStates.waiting_comeback)
    await m.answer(
        _t(
            lang,
            "🔄 Вернуться (без вины)\n\nОдна строка: что сейчас важно вернуть под контроль?\nПример: «сон», «деньги», «проект», «отношения», «здоровье»\n\nОтмена: /cancel",
            "🔄 Повернутися (без провини)\n\nОдин рядок: що важливо повернути під контроль?\nПриклад: «сон», «гроші», «проєкт», «стосунки», «здоров’я»\n\nСкасування: /cancel",
            "🔄 Come back (no guilt)\n\nOne line: what do you want back under control?\nExample: sleep, money, project, relationships, health\n\nCancel: /cancel",
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
            f"Ок. Возвращаем «{focus}» ✅\n\nСейчас — один микро-шаг на 2 минуты.\nЕсли хочешь, я дам толчок: нажми ⚡ Святой прыжок (15 минут).",
            f"Ок. Повертаємо «{focus}» ✅\n\nЗараз — один мікро-крок на 2 хвилини.\nЯкщо хочеш, дам поштовх: натисни ⚡ Святий стрибок (15 хв).",
            f"Ok. We bring back “{focus}” ✅\n\nNow — one 2-minute micro step.\nIf you want a push: tap ⚡ Holy jump (15 min).",
        ),
        reply_markup=_kb(lang),
    )


@router.message(F.text.in_(set(BTN_STREAK.values())))
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
            f"🏆 Серия: {streak} дн.\nТы держишь темп. Отличная работа!",
            f"🏆 Серія: {streak} дн.\nТи тримаєш темп. Чудова робота!",
            f"🏆 Streak: {streak} days.\nYou’re keeping the pace. Great job!",
        )

    await m.answer(msg, reply_markup=_kb(lang))


@router.message(F.text.in_(set(BTN_QUOTE.values())))
async def motivation_quote(m: Message, session: AsyncSession):
    user = await _get_user(session, m.from_user.id) if m.from_user else None
    lang = _user_lang(user, getattr(m.from_user, "language_code", None) if m.from_user else None)

    wait_msg = await m.answer("⏳")

    prompt = (
        "Сгенерируй одну мощную, хлесткую и нестандартную мысль для фокуса и дисциплины. "
        "СТРОГО ЗАПРЕЩЕНО использовать банальности вроде 'никогда не сдавайся', 'верь в себя', 'следуй за мечтой'. "
        "Стиль: стоицизм, суровый прагматизм, глубокая психология. "
        "Максимум 1-2 предложения. Без приветствий, кавычек, хэштегов и лишней воды. Только сама суть. "
        f"ОТВЕЧАЙ СТРОГО НА ЯЗЫКЕ: {lang}"
    )

    reply = await run_assistant(user, prompt, lang, session=session)

    await wait_msg.delete()
    await m.answer(reply, reply_markup=_kb(lang))
