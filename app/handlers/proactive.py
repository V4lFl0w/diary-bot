from __future__ import annotations

import re
from datetime import time as dtime
from typing import Optional, Union

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

router = Router(name="proactive")

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

# mode: 0 off, 1 morning, 2 evening, 3 both
_MODE_CYCLE = [0, 1, 2, 3]

# ---------- i18n ----------
def _norm_lang(v: Optional[str]) -> str:
    if not v:
        return "ru"
    s = (v or "").strip().lower()
    # Telegram часто даёт "uk", "ru", "en", или "uk-UA"
    if s.startswith("uk"):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    return "ru"

I18N = {
    "ru": {
        "title": "⚡ Проактивность",
        "subtitle": "Режим, в котором бот сам помогает держать фокус и не сливать день.",
        "how_it_works": "Как это работает:",
        "how_1": "• 🌅 Утром — помогаем начать день без хаоса",
        "how_2": "• 🌙 Вечером — спокойно закрываем день и фиксируем результат",
        "line_help": "Ты не думаешь что делать — бот задаёт правильные вопросы.",
        "current_mode": "🧠 Текущий режим",
        "mode": "Режим",
        "time": "Время",
        "morning": "🌅 Утро",
        "evening": "🌙 Вечер",
        "practice": "🔍 Что ты получаешь на практике",
        "m_block_title": "🌅 Утро — чтобы день не «сожрал» тебя",
        "m_q": "Бот спросит:",
        "m_q1": "• 🎯 Что сегодня главное? (1 вещь, дающая максимум)",
        "m_q2": "• 👣 Какие 3 простых шага приблизят к этому?",
        "m_q3": "• ⚡ С чего начать прямо сейчас? (2 минуты, без прокрастинации)",
        "m_idea": "👉 Идея: не «планировать», а начать.",
        "e_block_title": "🌙 Вечер — чтобы день не прошёл впустую",
        "e_q": "Бот мягко спросит:",
        "e_q1": "• 🔭 Как прошёл день? (1 фраза)",
        "e_q2": "• 🏆 Что сегодня получилось?",
        "e_q3": "• 📘 Какой вывод / урок?",
        "e_idea": "👉 Идея: закрепить опыт и не тащить хаос в завтра.",
        "setup": "⏱ Настройка — 10 секунд",
        "setup_1": "1 клик — выбрать режим",
        "setup_2": "2 клика — задать время",
        "setup_3": "И всё. Бот делает остальное.",
        "why": "🧩 Почему это важно (коротко, по-человечески)",
        "why_1": "• ❌ не мотивация",
        "why_2": "• ❌ не «надо быть продуктивным»",
        "why_3": "• ✅ меньше хаоса",
        "why_4": "• ✅ меньше откладывания",
        "why_5": "• ✅ больше ощущения контроля",
        "kb_mode": "🧠 Режим",
        "kb_morning": "🕘 Утро",
        "kb_evening": "🌙 Вечер",
        "kb_sample_m": "✏️ Пример утра",
        "kb_sample_e": "✏️ Пример вечера",
        "kb_back": "⬅️ Назад",
        "ask_time_m": "🕘 Введи время для утра (HH:MM)\nПример: 09:30\nОтмена: /cancel",
        "ask_time_e": "🕘 Введи время для вечера (HH:MM)\nПример: 00:00\nОтмена: /cancel",
        "press_start": "Нажми /start",
        "saved": "✅ Сохранено.",
        "done": "Готово",
        "bad_format": "❌ Формат HH:MM, пример 09:30",
        "out_of_range": "❌ Время вне диапазона 00:00–23:59",
        "sample_m": (
            "🌅 Утро — пример\n\n"
            "Представь обычный день.\n\n"
            "Бот:\n🎯 Что сегодня действительно важно?\n\n"
            "Ты:\n«Закрыть презентацию для клиента»\n\n"
            "Бот:\n👣 Назови 3 простых шага\n\n"
            "Ты:\n«Открыть файл / дописать 2 слайда / отправить»\n\n"
            "Бот:\n⚡ С чего начнём прямо сейчас? (2 минуты)\n\n"
            "Ты:\n«Открываю файл»\n\n"
            "👉 И ты уже в действии, а не в размышлениях."
        ),
        "sample_e": (
            "🌙 Вечер — пример\n\n"
            "Бот:\n🔭 Как прошёл день? (1 фраза)\n\n"
            "Ты:\n«Сложно, но полезно»\n\n"
            "Бот:\n🏆 Что получилось?\n\n"
            "Ты:\n«Отправил презентацию»\n\n"
            "Бот:\n📘 Какой урок?\n\n"
            "Ты:\n«Лучше начинать утром»\n\n"
            "👉 Мозг закрывает день, а не варится в нём ночью."
        ),
        "when": "🕒 Когда это приходит",
        "why_short": "🎯 Зачем это тебе",
        "what_writes": "💬 Что будет писать бот",
        "how_to_answer": "👉 Как отвечать: коротко, одной фразой. Не идеально — просто начни.",
        "kb_info": "💡 Как это помогает",
        "info": (
            "💡 Как это помогает\n\n"
            "🎯 Зачем тебе этот режим\n"
            "• Меньше хаоса в голове\n"
            "• Проще начать дела\n"
            "• День не пролетает впустую\n"
            "• Появляется чувство контроля\n\n"
            "🌅 Утром бот помогает войти в день\n"
            "Он спросит:\n"
            "• Что сегодня главное?\n"
            "• Какие 3 шага приблизят к этому?\n"
            "• С чего начнёшь прямо сейчас?\n\n"
            "🌙 Вечером бот помогает закрыть день\n"
            "Он спросит:\n"
            "• Как прошёл день?\n"
            "• Что получилось?\n"
            "• Какой вывод на будущее?\n\n"
            "⚙️ Настройка — 10 секунд\n"
            "1 клик — выбрать режим\n"
            "2 клика — задать время\n"
            "Дальше бот делает всё сам\n\n"
            "🗂 Твои ответы сохраняются — ты сможешь видеть прогресс."
        ),

    },
    "uk": {
        "title": "⚡ Проактивність",
        "subtitle": "Режим, у якому бот сам допомагає тримати фокус і не зливати день.",
        "how_it_works": "Як це працює:",
        "how_1": "• 🌅 Вранці — допомагаємо почати день без хаосу",
        "how_2": "• 🌙 Увечері — спокійно закриваємо день і фіксуємо результат",
        "line_help": "Ти не думаєш що робити — бот ставить правильні питання.",
        "current_mode": "🧠 Поточний режим",
        "mode": "Режим",
        "time": "Час",
        "morning": "🌅 Ранок",
        "evening": "🌙 Вечір",
        "practice": "🔍 Що ти отримуєш на практиці",
        "m_block_title": "🌅 Ранок — щоб день не «з’їв» тебе",
        "m_q": "Бот спитає:",
        "m_q1": "• 🎯 Що сьогодні головне? (1 річ, що дає максимум)",
        "m_q2": "• 👣 Які 3 прості кроки наблизять до цього?",
        "m_q3": "• ⚡ З чого почати прямо зараз? (2 хвилини, без прокрастинації)",
        "m_idea": "👉 Ідея: не «планувати», а почати.",
        "e_block_title": "🌙 Вечір — щоб день не минув дарма",
        "e_q": "Бот м’яко спитає:",
        "e_q1": "• 🔭 Як пройшов день? (1 фраза)",
        "e_q2": "• 🏆 Що сьогодні вийшло?",
        "e_q3": "• 📘 Який висновок / урок?",
        "e_idea": "👉 Ідея: закріпити досвід і не тягнути хаос у завтра.",
        "setup": "⏱ Налаштування — 10 секунд",
        "setup_1": "1 клік — вибрати режим",
        "setup_2": "2 кліки — задати час",
        "setup_3": "І все. Бот робить решту.",
        "why": "🧩 Чому це важливо (коротко, по-людськи)",
        "why_1": "• ❌ не мотивація",
        "why_2": "• ❌ не «треба бути продуктивним»",
        "why_3": "• ✅ менше хаосу",
        "why_4": "• ✅ менше відкладання",
        "why_5": "• ✅ більше відчуття контролю",
        "kb_mode": "🧠 Режим",
        "kb_morning": "🕘 Ранок",
        "kb_evening": "🌙 Вечір",
        "kb_sample_m": "✏️ Приклад ранку",
        "kb_sample_e": "✏️ Приклад вечора",
        "kb_back": "⬅️ Назад",
        "ask_time_m": "🕘 Введи час для ранку (HH:MM)\nПриклад: 09:30\nСкасування: /cancel",
        "ask_time_e": "🕘 Введи час для вечора (HH:MM)\nПриклад: 00:00\nСкасування: /cancel",
        "press_start": "Натисни /start",
        "saved": "✅ Збережено.",
        "done": "Готово",
        "bad_format": "❌ Формат HH:MM, приклад 09:30",
        "out_of_range": "❌ Час поза діапазоном 00:00–23:59",
        "sample_m": (
            "🌅 Ранок — приклад\n\n"
            "Уяви звичайний день.\n\n"
            "Бот:\n🎯 Що сьогодні справді важливо?\n\n"
            "Ти:\n«Закрити презентацію для клієнта»\n\n"
            "Бот:\n👣 Назви 3 прості кроки\n\n"
            "Ти:\n«Відкрити файл / дописати 2 слайди / відправити»\n\n"
            "Бот:\n⚡ З чого почнемо просто зараз? (2 хвилини)\n\n"
            "Ти:\n«Відкриваю файл»\n\n"
            "👉 І ти вже в дії, а не в роздумах."
        ),
        "sample_e": (
            "🌙 Вечір — приклад\n\n"
            "Бот:\n🔭 Як пройшов день? (1 фраза)\n\n"
            "Ти:\n«Складно, але корисно»\n\n"
            "Бот:\n🏆 Що вийшло?\n\n"
            "Ти:\n«Відправив презентацію»\n\n"
            "Бот:\n📘 Який урок?\n\n"
            "Ти:\n«Краще починати зранку»\n\n"
            "👉 Мозок закриває день, а не вариться в ньому вночі."
        ),
        "when": "🕒 Коли це приходить",
        "why_short": "🎯 Навіщо це тобі",
        "what_writes": "💬 Що буде писати бот",
        "how_to_answer": "👉 Як відповідати: коротко, одним реченням. Не ідеально — просто почни.",
        "kb_info": "💡 Як це допомагає",
        "info": (
            "💡 Як це допомагає\n\n"
            "🎯 Навіщо тобі цей режим\n"
            "• Менше хаосу в голові\n"
            "• Легше почати справи\n"
            "• День не пролітає дарма\n"
            "• З’являється відчуття контролю\n\n"
            "🌅 Вранці бот допомагає увійти в день\n"
            "Він спитає:\n"
            "• Що сьогодні головне?\n"
            "• Які 3 кроки наблизять до цього?\n"
            "• З чого почнеш прямо зараз?\n\n"
            "🌙 Увечері бот допомагає закрити день\n"
            "Він спитає:\n"
            "• Як пройшов день?\n"
            "• Що вийшло?\n"
            "• Який висновок на майбутнє?\n\n"
            "⚙️ Налаштування — 10 секунд\n"
            "1 клік — вибрати режим\n"
            "2 кліки — задати час\n"
            "Далі бот робить все сам\n\n"
            "🗂 Твої відповіді зберігаються — ти бачитимеш прогрес."
        ),

    },
    "en": {
        "title": "⚡ Proactivity",
        "subtitle": "A mode where the bot helps you stay focused and not waste your day.",
        "how_it_works": "How it works:",
        "how_1": "• 🌅 Morning — start your day without chaos",
        "how_2": "• 🌙 Evening — close the day calmly and lock the result",
        "line_help": "You don’t guess what to do — the bot asks the right questions.",
        "current_mode": "🧠 Current mode",
        "mode": "Mode",
        "time": "Time",
        "morning": "🌅 Morning",
        "evening": "🌙 Evening",
        "practice": "🔍 What you get in practice",
        "m_block_title": "🌅 Morning — so the day doesn’t eat you alive",
        "m_q": "The bot will ask:",
        "m_q1": "• 🎯 What’s the one main thing today? (max impact)",
        "m_q2": "• 👣 What 3 simple steps move you forward?",
        "m_q3": "• ⚡ What’s the 2-minute start right now? (no procrastination)",
        "m_idea": "👉 Idea: don’t plan forever — start.",
        "e_block_title": "🌙 Evening — so the day doesn’t disappear",
        "e_q": "The bot will gently ask:",
        "e_q1": "• 🔭 How was your day? (1 sentence)",
        "e_q2": "• 🏆 What worked today?",
        "e_q3": "• 📘 What’s the lesson?",
        "e_idea": "👉 Idea: lock the experience and don’t carry chaos into tomorrow.",
        "setup": "⏱ Setup — 10 seconds",
        "setup_1": "1 tap — choose mode",
        "setup_2": "2 taps — set time",
        "setup_3": "That’s it. The bot does the rest.",
        "why": "🧩 Why it matters (human, short)",
        "why_1": "• ❌ not motivation",
        "why_2": "• ❌ not “be productive”",
        "why_3": "• ✅ less chaos",
        "why_4": "• ✅ less delaying",
        "why_5": "• ✅ more control feeling",
        "kb_mode": "🧠 Mode",
        "kb_morning": "🕘 Morning",
        "kb_evening": "🌙 Evening",
        "kb_sample_m": "✏️ Morning example",
        "kb_sample_e": "✏️ Evening example",
        "kb_back": "⬅️ Back",
        "ask_time_m": "🕘 Enter morning time (HH:MM)\nExample: 09:30\nCancel: /cancel",
        "ask_time_e": "🕘 Enter evening time (HH:MM)\nExample: 00:00\nCancel: /cancel",
        "press_start": "Press /start",
        "saved": "✅ Saved.",
        "done": "Done",
        "bad_format": "❌ Format HH:MM, example 09:30",
        "out_of_range": "❌ Time out of range 00:00–23:59",
        "sample_m": (
            "🌅 Morning — example\n\n"
            "Imagine a normal day.\n\n"
            "Bot:\n🎯 What actually matters today?\n\n"
            "You:\n“Finish the client presentation”\n\n"
            "Bot:\n👣 Name 3 simple steps\n\n"
            "You:\n“Open the file / add 2 slides / send it”\n\n"
            "Bot:\n⚡ What’s the 2-minute start right now?\n\n"
            "You:\n“Opening the file”\n\n"
            "👉 You’re already acting — not overthinking."
        ),
        "sample_e": (
            "🌙 Evening — example\n\n"
            "Bot:\n🔭 How was your day? (1 sentence)\n\n"
            "You:\n“Hard, but useful”\n\n"
            "Bot:\n🏆 What worked?\n\n"
            "You:\n“Sent the presentation”\n\n"
            "Bot:\n📘 What’s the lesson?\n\n"
            "You:\n“Start earlier in the morning”\n\n"
            "👉 Your brain closes the day instead of boiling in it at night."
        ),
        "when": "🕒 When it arrives",
        "why_short": "🎯 Why you want it",
        "what_writes": "💬 What the bot will write",
        "how_to_answer": "👉 How to reply: short, one line. Not perfect — just start.",
        "kb_info": "💡 How it helps",
        "info": (
            "💡 How it helps\n\n"
            "🎯 Why this mode matters\n"
            "• Less chaos in your head\n"
            "• Easier to start\n"
            "• The day doesn’t vanish\n"
            "• More sense of control\n\n"
            "🌅 Morning helps you enter the day\n"
            "It will ask:\n"
            "• What’s the main thing today?\n"
            "• What 3 steps move you forward?\n"
            "• What’s your first tiny start right now?\n\n"
            "🌙 Evening helps you close the day\n"
            "It will ask:\n"
            "• How was your day?\n"
            "• What worked?\n"
            "• What’s the takeaway?\n\n"
            "⚙️ Setup — 10 seconds\n"
            "1 tap — choose mode\n"
            "2 taps — set time\n"
            "Then the bot does the rest\n\n"
            "🗂 Your answers are saved — you can track progress."
        ),

    },
}

def _t(lang: str, key: str) -> str:
    lang = _norm_lang(lang)
    return I18N.get(lang, I18N["ru"]).get(key, I18N["ru"].get(key, key))

def _mode_label(lang: str, mode: int) -> str:
    lang = _norm_lang(lang)
    if lang == "uk":
        return {0: "Вимкнено", 1: "Ранок", 2: "Вечір", 3: "Ранок + Вечір"}.get(mode, "—")
    if lang == "en":
        return {0: "Off", 1: "Morning", 2: "Evening", 3: "Morning + Evening"}.get(mode, "—")
    return {0: "Выключено", 1: "Утро", 2: "Вечер", 3: "Утро + Вечер"}.get(mode, "—")

# ---------- db helpers ----------
class ProactiveStates(StatesGroup):
    waiting_time = State()

async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()

def _fmt_time(v: Union[None, dtime, str]) -> str:
    if v is None:
        return "—"
    if isinstance(v, dtime):
        return f"{v.hour:02d}:{v.minute:02d}"
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return "—"
        parts = s.split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            h = int(parts[0]); m = int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
        return s
    return str(v)

def _current_mode(u: User) -> int:
    m = bool(getattr(u, "morning_auto", False))
    e = bool(getattr(u, "evening_auto", False))
    if m and e:
        return 3
    if m:
        return 1
    if e:
        return 2
    return 0

def _apply_mode(u: User, mode: int) -> None:
    u.morning_auto = mode in (1, 3)
    u.evening_auto = mode in (2, 3)

def _user_lang(u: User, fallback: str = "ru") -> str:
    # приоритет: user.lang -> user.language -> telegram language_code
    v = getattr(u, "lang", None) or getattr(u, "language", None) or fallback
    return _norm_lang(v)

def _screen_text(u: User, lang: str) -> str:
    mode = _current_mode(u)
    mt = _fmt_time(getattr(u, "morning_time", None))
    et = _fmt_time(getattr(u, "evening_time", None))

    streak = getattr(u, "proactive_streak", None)
    streak_line = ""
    if isinstance(streak, int) and streak > 0:
        if _norm_lang(lang) == "en":
            streak_line = f"\n🔥 Streak: {streak} day(s)"
        elif _norm_lang(lang) == "uk":
            streak_line = f"\n🔥 Серія: {streak} день(дні)"
        else:
            streak_line = f"\n🔥 Серия: {streak} день(дней)"

    if _norm_lang(lang) == "uk":
        benefits = [
            "Менше хаосу в голові",
            "Легше почати справи",
            "День не минає дарма",
            "З’являється відчуття контролю",
        ]
    elif _norm_lang(lang) == "en":
        benefits = [
            "Less chaos in your head",
            "Easier to start",
            "The day doesn’t vanish",
            "More sense of control",
        ]
    else:
        benefits = [
            "Меньше хаоса в голове",
            "Проще начать дела",
            "День не пролетает впустую",
            "Появляется чувство контроля",
        ]

    return (
        f"{_t(lang, 'title')}\n"
        f"{_t(lang, 'subtitle')}\n\n"
        f"🧠 {_t(lang, 'how_it_works')}\n"
        f"{_t(lang, 'how_1')}\n"
        f"{_t(lang, 'how_2')}\n\n"
        f"{_t(lang, 'current_mode')}\n"
        f"{_t(lang, 'mode')}: {_mode_label(lang, mode)}\n\n"
        f"{_t(lang, 'when')}\n"
        f"{_t(lang, 'morning')}: {mt}\n"
        f"{_t(lang, 'evening')}: {et}"
        f"{streak_line}\n\n"
        f"{_t(lang, 'why_short')}\n"
        f"• " + "\n• ".join(benefits) + "\n\n"
        f"{_t(lang, 'what_writes')}\n\n"
        f"🌅 {_t(lang, 'morning')}\n"
        f"• {_t(lang, 'm_q1').replace('• 🎯','').strip()}\n"
        f"• {_t(lang, 'm_q2').replace('• 👣','').strip()}\n"
        f"• {_t(lang, 'm_q3').replace('• ⚡','').strip()}\n\n"
        f"🌙 {_t(lang, 'evening')}\n"
        f"• {_t(lang, 'e_q1').replace('• 🔭','').strip()}\n"
        f"• {_t(lang, 'e_q2').replace('• 🏆','').strip()}\n"
        f"• {_t(lang, 'e_q3').replace('• 📘','').strip()}\n\n"
        f"{_t(lang, 'how_to_answer')}"
    )


def proactive_kb(u: User, lang: str):
    kb = InlineKeyboardBuilder()
    mode = _current_mode(u)

    kb.button(text=f"{_t(lang, 'kb_mode')}: {_mode_label(lang, mode)}", callback_data="proactive:mode")

    kb.button(text=f"{_t(lang, 'kb_morning')}: {_fmt_time(getattr(u, 'morning_time', None))}", callback_data="proactive:time:morning")
    kb.button(text=f"{_t(lang, 'kb_evening')}: {_fmt_time(getattr(u, 'evening_time', None))}", callback_data="proactive:time:evening")

    kb.button(text=_t(lang, "kb_sample_m"), callback_data="proactive:sample:morning")
    kb.button(text=_t(lang, "kb_sample_e"), callback_data="proactive:sample:evening")

    kb.button(text=_t(lang, "kb_info"), callback_data="proactive:info")

    kb.button(text=_t(lang, "kb_back"), callback_data="menu:home")

    kb.adjust(1, 2, 2, 1, 1)
    return kb.as_markup()

async def _render_to_message(m: Message, u: User, lang: str):
    await m.answer(_screen_text(u, lang), reply_markup=proactive_kb(u, lang), parse_mode=None)

async def _render_edit(msg: Message, u: User, lang: str):
    try:
        await msg.edit_text(_screen_text(u, lang), reply_markup=proactive_kb(u, lang), parse_mode=None)
    except Exception:
        await msg.answer(_screen_text(u, lang), reply_markup=proactive_kb(u, lang), parse_mode=None)

@router.message(Command("proactive"))
async def proactive_cmd(m: Message, session: AsyncSession):
    if not m.from_user:
        return
    u = await _get_user(session, m.from_user.id)
    if not u:
        await m.answer(_t(_norm_lang(getattr(m.from_user, "language_code", "ru")), "press_start"), parse_mode=None)
        return
    lang = _user_lang(u, fallback=_norm_lang(getattr(m.from_user, "language_code", "ru")))
    await _render_to_message(m, u, lang)

# ВАЖНО: menus.py вызывает show_proactive_screen(m, session, lang)
async def show_proactive_screen(message: Message, session: AsyncSession, lang: str = "ru", *_a, **_k):
    if not message.from_user:
        return
    u = await _get_user(session, message.from_user.id)
    if not u:
        await message.answer(_t(lang, "press_start"), parse_mode=None)
        return
    # если в БД есть lang — используем, иначе аргумент
    lang = _user_lang(u, fallback=lang)
    await _render_to_message(message, u, lang)

@router.callback_query(F.data == "proactive:mode")
async def proactive_mode(cb: CallbackQuery, session: AsyncSession):
    if not cb.message:
        return
    u = await _get_user(session, cb.from_user.id)
    if not u:
        await cb.answer(" /start ")
        return

    lang = _user_lang(u, fallback=_norm_lang(getattr(cb.from_user, "language_code", "ru")))

    cur = _current_mode(u)
    idx = _MODE_CYCLE.index(cur) if cur in _MODE_CYCLE else 0
    nxt = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
    _apply_mode(u, nxt)

    # чтобы не стрелял “сразу” после включения режима — сбрасываем last_sent_at
    if nxt in (1, 3):
        u.morning_last_sent_at = None
    if nxt in (2, 3):
        u.evening_last_sent_at = None

    await session.commit()
    await _render_edit(cb.message, u, lang)
    await cb.answer(_t(lang, "done"))

@router.callback_query(F.data.startswith("proactive:time:"))
async def proactive_set_time(cb: CallbackQuery, state: FSMContext):
    part = cb.data.split(":")[-1]
    await state.set_state(ProactiveStates.waiting_time)
    await state.update_data(part=part)

    # язык берём от Telegram, потому что user ещё не в этом handler
    lang = _norm_lang(getattr(cb.from_user, "language_code", "ru"))

    await cb.message.answer(
        _t(lang, "ask_time_m") if part == "morning" else _t(lang, "ask_time_e"),
        parse_mode=None,
    )
    await cb.answer()

@router.message(ProactiveStates.waiting_time, Command("cancel"))
async def proactive_cancel(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    await show_proactive_screen(message, session, lang=_norm_lang(getattr(message.from_user, "language_code", "ru")))

@router.message(ProactiveStates.waiting_time)
async def proactive_time_input(message: Message, session: AsyncSession, state: FSMContext):
    if not message.from_user:
        return

    u = await _get_user(session, message.from_user.id)
    lang = _user_lang(u, fallback=_norm_lang(getattr(message.from_user, "language_code", "ru"))) if u else _norm_lang(getattr(message.from_user, "language_code", "ru"))

    txt = (message.text or "").strip()
    m = _TIME_RE.match(txt)
    if not m:
        await message.answer(_t(lang, "bad_format"), parse_mode=None)
        return

    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        await message.answer(_t(lang, "out_of_range"), parse_mode=None)
        return

    data = await state.get_data()
    part = data.get("part")

    if not u:
        await state.clear()
        await message.answer(_t(lang, "press_start"), parse_mode=None)
        return

    new_time = dtime(hh, mm)

    if part == "morning":
        u.morning_time = new_time
        u.morning_auto = True
        u.morning_last_sent_at = None
    else:
        u.evening_time = new_time
        u.evening_auto = True
        u.evening_last_sent_at = None

    await session.commit()
    await state.clear()

    await message.answer(_t(lang, "saved"), parse_mode=None)
    await show_proactive_screen(message, session, lang=lang)


@router.callback_query(F.data == "proactive:info")
async def proactive_info(cb: CallbackQuery, session: AsyncSession):
    if not cb.message:
        return
    u = await _get_user(session, cb.from_user.id)
    lang = _user_lang(u, fallback=_norm_lang(getattr(cb.from_user, "language_code", "ru"))) if u else _norm_lang(getattr(cb.from_user, "language_code", "ru"))
    await cb.message.answer(_t(lang, "info"), parse_mode=None)
    await cb.answer("Ок")

@router.callback_query(F.data.startswith("proactive:sample:"))
async def proactive_sample(cb: CallbackQuery, session: AsyncSession):
    part = cb.data.split(":")[-1]
    u = await _get_user(session, cb.from_user.id)
    lang = _user_lang(u, fallback=_norm_lang(getattr(cb.from_user, "language_code", "ru"))) if u else _norm_lang(getattr(cb.from_user, "language_code", "ru"))

    if part == "morning":
        await cb.message.answer(_t(lang, "sample_m"), parse_mode=None)
    else:
        await cb.message.answer(_t(lang, "sample_e"), parse_mode=None)
    await cb.answer("Ок")

__all__ = ["router", "show_proactive_screen"]
