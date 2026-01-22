from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User

# кнопка из главного меню (если есть)
try:
    from app.keyboards import is_meditation_btn
except Exception:  # pragma: no cover
    def is_meditation_btn(_text: str) -> bool:  # type: ignore
        return False

# settings optional
try:
    from app.config import settings  # type: ignore
except Exception:  # pragma: no cover
    settings = None  # type: ignore


router = Router(name="meditation")


# -------------------- i18n --------------------

TXT: Dict[str, Dict[str, str]] = {
    "title": {"ru": "🧘 Медитация", "uk": "🧘 Медитація", "en": "🧘 Meditation"},
    "choose_custom": {
        "ru": "Введи длительность в минутах (1–180):",
        "uk": "Введи тривалість у хвилинах (1–180):",
        "en": "Send duration in minutes (1–180):",
    },
    "bad_custom": {
        "ru": "Не понял. Напиши число минут от 1 до 180.",
        "uk": "Не зрозумів. Напиши кількість хвилин від 1 до 180.",
        "en": "Please send a number from 1 to 180.",
    },
    "saved": {"ru": "Сохранено ✅", "uk": "Збережено ✅", "en": "Saved ✅"},
    "started": {
        "ru": "✅ Старт: {dur} • {mode}\nФон: {bg} • Громк.: {vol}% • Колокол: {bell}",
        "uk": "✅ Старт: {dur} • {mode}\nФон: {bg} • Гучн.: {vol}% • Дзвін: {bell}",
        "en": "✅ Started: {dur} • {mode}\nBG: {bg} • Vol: {vol}% • Bell: {bell}",
    },
    "paused": {"ru": "⏸ Пауза", "uk": "⏸ Пауза", "en": "⏸ Paused"},
    "resumed": {"ru": "▶️ Продолжили", "uk": "▶️ Продовжили", "en": "▶️ Resumed"},
    "stopped": {"ru": "⏹ Остановлено", "uk": "⏹ Зупинено", "en": "⏹ Stopped"},
    "status": {
        "ru": "⏳ Осталось: {left}\nСессия: {dur} • {mode}\nФон: {bg} • {vol}% • Колокол: {bell}",
        "uk": "⏳ Залишилось: {left}\nСесія: {dur} • {mode}\nФон: {bg} • {vol}% • Дзвін: {bell}",
        "en": "⏳ Remaining: {left}\nSession: {dur} • {mode}\nBG: {bg} • {vol}% • Bell: {bell}",
    },
    "done_timer": {
        "ru": "✅ Сессия завершена.",
        "uk": "✅ Сесію завершено.",
        "en": "✅ Session completed.",
    },
    "done_sleep": {
        "ru": "😴 Спокойной ночи.",
        "uk": "😴 Надобраніч.",
        "en": "😴 Good night.",
    },

    # labels
    "dur": {"ru": "⏱ Длительность", "uk": "⏱ Тривалість", "en": "⏱ Duration"},
    "mode": {"ru": "🎛 Режим", "uk": "🎛 Режим", "en": "🎛 Mode"},
    "bg": {"ru": "🎵 Фон", "uk": "🎵 Фон", "en": "🎵 Background"},
    "vol": {"ru": "🔊 Громкость", "uk": "🔊 Гучність", "en": "🔊 Volume"},
    "bell": {"ru": "🔔 Колокол", "uk": "🔔 Дзвін", "en": "🔔 Bell"},

    # buttons
    "b_5": {"ru": "5", "uk": "5", "en": "5"},
    "b_10": {"ru": "10", "uk": "10", "en": "10"},
    "b_15": {"ru": "15", "uk": "15", "en": "15"},
    "b_20": {"ru": "20", "uk": "20", "en": "20"},
    "b_custom": {"ru": "Кастом", "uk": "Кастом", "en": "Custom"},

    "m_timer": {"ru": "Timer", "uk": "Timer", "en": "Timer"},
    "m_guided": {"ru": "Guided", "uk": "Guided", "en": "Guided"},
    "m_sleep": {"ru": "Sleep", "uk": "Sleep", "en": "Sleep"},

    "bg_off": {"ru": "Выключен", "uk": "Вимкнено", "en": "Off"},
    "bg_rain": {"ru": "Rain", "uk": "Rain", "en": "Rain"},
    "bg_forest": {"ru": "Forest", "uk": "Forest", "en": "Forest"},
    "bg_white": {"ru": "White noise", "uk": "White noise", "en": "White noise"},

    "bell_on": {"ru": "On", "uk": "On", "en": "On"},
    "bell_off": {"ru": "Off", "uk": "Off", "en": "Off"},

    "start": {"ru": "▶️ Start", "uk": "▶️ Start", "en": "▶️ Start"},
    "pause": {"ru": "⏸ Pause", "uk": "⏸ Pause", "en": "⏸ Pause"},
    "resume": {"ru": "▶️ Resume", "uk": "▶️ Resume", "en": "▶️ Resume"},
    "stop": {"ru": "⏹ Stop", "uk": "⏹ Stop", "en": "⏹ Stop"},
    "status_btn": {"ru": "ℹ️ Статус", "uk": "ℹ️ Статус", "en": "ℹ️ Status"},
}


SUPPORTED_LANGS = {"ru", "uk", "en"}

MODE_TIMER = "timer"
MODE_GUIDED = "guided"
MODE_SLEEP = "sleep"
MODES = (MODE_TIMER, MODE_GUIDED, MODE_SLEEP)

BG_OFF = "off"
BG_RAIN = "rain"
BG_FOREST = "forest"
BG_WHITE = "white"
BGS = (BG_OFF, BG_RAIN, BG_FOREST, BG_WHITE)

CB = "med2"  # новый префикс, чтоб не пересекаться с древним med:


class MeditationFSM(StatesGroup):
    waiting_custom_duration = State()


@dataclass
class SessionCfg:
    duration_min: int = 10
    mode: str = MODE_TIMER
    bg: str = BG_OFF
    volume: int = 70
    bell: bool = True


def _normalize_lang(code: Optional[str]) -> str:
    raw = (code or "ru").strip().lower()
    if raw.startswith(("ua", "uk")):
        return "uk"
    if raw.startswith("en"):
        return "en"
    return "ru"


def _tr(l: str, key: str) -> str:
    l = _normalize_lang(l)
    return TXT[key].get(l, TXT[key]["ru"])


async def _get_user(session: AsyncSession, tg_id: int) -> Optional[User]:
    return (await session.execute(
        select(User).where(User.tg_id == tg_id)
    )).scalar_one_or_none()


async def _lang(obj: Message | CallbackQuery, session: AsyncSession) -> str:
    tg_user = getattr(obj, "from_user", None)
    tg_id = getattr(tg_user, "id", None)
    tg_lang = getattr(tg_user, "language_code", None)

    user: Optional[User] = None
    if tg_id:
        try:
            user = await _get_user(session, tg_id)
        except Exception:
            user = None

    code = (
        getattr(user, "locale", None)
        or getattr(user, "lang", None)
        or tg_lang
        or "ru"
    )
    l = _normalize_lang(code)
    return l if l in SUPPORTED_LANGS else "ru"


def _fmt_minutes(l: str, mins: int) -> str:
    if l == "uk":
        return f"{mins} хв"
    if l == "en":
        return f"{mins} min"
    return f"{mins} мин"


def _mode_label(l: str, mode: str) -> str:
    if mode == MODE_GUIDED:
        return _tr(l, "m_guided")
    if mode == MODE_SLEEP:
        return _tr(l, "m_sleep")
    return _tr(l, "m_timer")


def _bg_label(l: str, bg: str) -> str:
    if bg == BG_RAIN:
        return _tr(l, "bg_rain")
    if bg == BG_FOREST:
        return _tr(l, "bg_forest")
    if bg == BG_WHITE:
        return _tr(l, "bg_white")
    return _tr(l, "bg_off")


def _bell_label(l: str, bell: bool) -> str:
    return _tr(l, "bell_on") if bell else _tr(l, "bell_off")


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


async def _get_cfg(state: FSMContext) -> SessionCfg:
    data = await state.get_data()
    return SessionCfg(
        duration_min=int(data.get("duration_min", 10)),
        mode=str(data.get("mode", MODE_TIMER)),
        bg=str(data.get("bg", BG_OFF)),
        volume=int(data.get("volume", 70)),
        bell=bool(data.get("bell", True)),
    )


async def _set_cfg(state: FSMContext, **patch: Any) -> None:
    await state.update_data(**patch)


def _screen_text(l: str, cfg: SessionCfg) -> str:
    return (
        f"{_tr(l,'title')}\n\n"
        f"{_tr(l,'dur')}: {_fmt_minutes(l, cfg.duration_min)}\n"
        f"{_tr(l,'mode')}: {_mode_label(l, cfg.mode)}\n"
        f"{_tr(l,'bg')}: {_bg_label(l, cfg.bg)}\n"
        f"{_tr(l,'vol')}: {cfg.volume}%\n"
        f"{_tr(l,'bell')}: {_bell_label(l, cfg.bell)}"
    )


def _kb(l: str, cfg: SessionCfg, running: bool, paused: bool) -> InlineKeyboardMarkup:
    # duration row
    dur_row = [
        InlineKeyboardButton(text=_tr(l, "b_5"), callback_data=f"{CB}:dur:5"),
        InlineKeyboardButton(text=_tr(l, "b_10"), callback_data=f"{CB}:dur:10"),
        InlineKeyboardButton(text=_tr(l, "b_15"), callback_data=f"{CB}:dur:15"),
        InlineKeyboardButton(text=_tr(l, "b_20"), callback_data=f"{CB}:dur:20"),
        InlineKeyboardButton(text=_tr(l, "b_custom"), callback_data=f"{CB}:dur:custom"),
    ]

    mode_row = [
        InlineKeyboardButton(text=_tr(l, "m_timer"), callback_data=f"{CB}:mode:{MODE_TIMER}"),
        InlineKeyboardButton(text=_tr(l, "m_guided"), callback_data=f"{CB}:mode:{MODE_GUIDED}"),
        InlineKeyboardButton(text=_tr(l, "m_sleep"), callback_data=f"{CB}:mode:{MODE_SLEEP}"),
    ]

    bg_row = [
        InlineKeyboardButton(text=_tr(l, "bg_off"), callback_data=f"{CB}:bg:{BG_OFF}"),
        InlineKeyboardButton(text=_tr(l, "bg_rain"), callback_data=f"{CB}:bg:{BG_RAIN}"),
        InlineKeyboardButton(text=_tr(l, "bg_forest"), callback_data=f"{CB}:bg:{BG_FOREST}"),
        InlineKeyboardButton(text=_tr(l, "bg_white"), callback_data=f"{CB}:bg:{BG_WHITE}"),
    ]

    vol_row = [
        InlineKeyboardButton(text="−", callback_data=f"{CB}:vol:-10"),
        InlineKeyboardButton(text=f"{cfg.volume}%", callback_data=f"{CB}:noop"),
        InlineKeyboardButton(text="+", callback_data=f"{CB}:vol:+10"),
    ]

    bell_row = [
        InlineKeyboardButton(
            text=f"{_tr(l,'bell')} {_bell_label(l,cfg.bell)}",
            callback_data=f"{CB}:bell:toggle",
        )
    ]

    # controls
    controls: list[InlineKeyboardButton] = []
    if not running:
        controls = [
            InlineKeyboardButton(text=_tr(l, "start"), callback_data=f"{CB}:start"),
            InlineKeyboardButton(text=_tr(l, "status_btn"), callback_data=f"{CB}:status"),
        ]
    else:
        if paused:
            controls = [
                InlineKeyboardButton(text=_tr(l, "resume"), callback_data=f"{CB}:resume"),
                InlineKeyboardButton(text=_tr(l, "stop"), callback_data=f"{CB}:stop"),
                InlineKeyboardButton(text=_tr(l, "status_btn"), callback_data=f"{CB}:status"),
            ]
        else:
            controls = [
                InlineKeyboardButton(text=_tr(l, "pause"), callback_data=f"{CB}:pause"),
                InlineKeyboardButton(text=_tr(l, "stop"), callback_data=f"{CB}:stop"),
                InlineKeyboardButton(text=_tr(l, "status_btn"), callback_data=f"{CB}:status"),
            ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            dur_row,
            mode_row,
            bg_row,
            vol_row,
            bell_row,
            controls,
        ]
    )


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


async def _is_running(state: FSMContext) -> tuple[bool, bool]:
    d = await state.get_data()
    running = bool(d.get("running", False))
    paused = bool(d.get("paused", False))
    return running, paused


async def _session_left_seconds(state: FSMContext) -> int:
    d = await state.get_data()
    if not d.get("running"):
        return 0

    start_ts = float(d.get("start_ts", 0.0))
    duration_s = int(d.get("duration_s", 0))
    paused_total = int(d.get("paused_total", 0))
    paused = bool(d.get("paused", False))
    last_pause_ts = float(d.get("last_pause_ts", 0.0)) if paused else 0.0

    now = _now_ts()
    extra_pause = int(now - last_pause_ts) if paused and last_pause_ts else 0
    elapsed = int(now - start_ts) - paused_total - extra_pause
    left = duration_s - max(0, elapsed)
    return max(0, left)


def _fmt_left(l: str, sec: int) -> str:
    m = sec // 60
    s = sec % 60
    if l == "en":
        return f"{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


async def _render(m_or_c: Message | CallbackQuery, session: AsyncSession, state: FSMContext, edit: bool = False) -> None:
    l = await _lang(m_or_c, session)
    cfg = await _get_cfg(state)
    running, paused = await _is_running(state)

    text = _screen_text(l, cfg)
    kb = _kb(l, cfg, running=running, paused=paused)

    if isinstance(m_or_c, CallbackQuery):
        msg = m_or_c.message
    else:
        msg = m_or_c

    if edit and isinstance(msg, Message):
        try:
            await msg.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass

    await msg.answer(text, reply_markup=kb)


# -------------------- entrypoints --------------------

@router.message(Command("meditation"))
@router.message(F.text.func(is_meditation_btn))
async def cmd_meditation(m: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    # дефолты
    await _set_cfg(state, duration_min=10, mode=MODE_TIMER, bg=BG_OFF, volume=70, bell=True)
    await _set_cfg(state, running=False, paused=False, start_ts=0.0, duration_s=0, paused_total=0, last_pause_ts=0.0)
    await _render(m, session, state, edit=False)


# -------------------- callbacks --------------------

@router.callback_query(F.data == f"{CB}:noop")
async def cb_noop(c: CallbackQuery) -> None:
    await c.answer()


@router.callback_query(F.data.startswith(f"{CB}:dur:"))
async def cb_dur(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)
    _, _, value = (c.data or "").split(":", 2)

    running, _paused = await _is_running(state)
    if running:
        await c.answer()
        return

    if value == "custom":
        await c.answer()
        await state.set_state(MeditationFSM.waiting_custom_duration)
        await c.message.answer(_tr(l, "choose_custom"))
        return

    try:
        mins = int(value)
    except Exception:
        await c.answer()
        return

    mins = _clamp(mins, 1, 180)
    await _set_cfg(state, duration_min=mins)
    await c.answer(_tr(l, "saved"))
    await _render(c, session, state, edit=True)


@router.message(MeditationFSM.waiting_custom_duration)
async def custom_duration_input(m: Message, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(m, session)
    raw = (m.text or "").strip()
    try:
        mins = int(raw)
    except Exception:
        await m.answer(_tr(l, "bad_custom"))
        return

    if mins < 1 or mins > 180:
        await m.answer(_tr(l, "bad_custom"))
        return

    await _set_cfg(state, duration_min=mins)
    await state.clear()
    # восстановим дефолтные флаги сессии (если их уже нет)
    data = await state.get_data()
    if "running" not in data:
        await _set_cfg(state, running=False, paused=False, start_ts=0.0, duration_s=0, paused_total=0, last_pause_ts=0.0)

    await m.answer(_tr(l, "saved"))
    await _render(m, session, state, edit=False)


@router.callback_query(F.data.startswith(f"{CB}:mode:"))
async def cb_mode(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)
    _, _, mode = (c.data or "").split(":", 2)

    running, _paused = await _is_running(state)
    if running:
        await c.answer()
        return

    if mode not in MODES:
        await c.answer()
        return

    await _set_cfg(state, mode=mode)
    await c.answer(_tr(l, "saved"))
    await _render(c, session, state, edit=True)


@router.callback_query(F.data.startswith(f"{CB}:bg:"))
async def cb_bg(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)
    _, _, bg = (c.data or "").split(":", 2)

    running, _paused = await _is_running(state)
    if running:
        await c.answer()
        return

    if bg not in BGS:
        await c.answer()
        return

    await _set_cfg(state, bg=bg)
    await c.answer(_tr(l, "saved"))
    await _render(c, session, state, edit=True)


@router.callback_query(F.data.startswith(f"{CB}:vol:"))
async def cb_vol(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)
    _, _, delta_s = (c.data or "").split(":", 2)

    running, _paused = await _is_running(state)
    if running:
        await c.answer()
        return

    try:
        delta = int(delta_s)
    except Exception:
        await c.answer()
        return

    cfg = await _get_cfg(state)
    vol = _clamp(cfg.volume + delta, 0, 100)
    await _set_cfg(state, volume=vol)
    await c.answer(_tr(l, "saved"))
    await _render(c, session, state, edit=True)


@router.callback_query(F.data == f"{CB}:bell:toggle")
async def cb_bell(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)

    running, _paused = await _is_running(state)
    if running:
        await c.answer()
        return

    cfg = await _get_cfg(state)
    await _set_cfg(state, bell=not cfg.bell)
    await c.answer(_tr(l, "saved"))
    await _render(c, session, state, edit=True)


@router.callback_query(F.data == f"{CB}:start")
async def cb_start(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)
    cfg = await _get_cfg(state)

    running, _paused = await _is_running(state)
    if running:
        await c.answer()
        return

    duration_s = int(cfg.duration_min * 60)

    await _set_cfg(
        state,
        running=True,
        paused=False,
        start_ts=_now_ts(),
        duration_s=duration_s,
        paused_total=0,
        last_pause_ts=0.0,
    )
    await c.answer()

    await c.message.answer(
        TXT["started"][l].format(
            dur=_fmt_minutes(l, cfg.duration_min),
            mode=_mode_label(l, cfg.mode),
            bg=_bg_label(l, cfg.bg),
            vol=cfg.volume,
            bell=_bell_label(l, cfg.bell),
        )
    )

    # IMPORTANT: музыку и колокол отправляем максимально аккуратно.
    # Файлы лучше слать по file_id (кэш телеги). Можно переопределить через settings:
    # settings.meditation_audio_file_ids = {"rain": "<file_id>", "forest": "...", "white": "...", "bell": "..."}
    try:
        ids = getattr(settings, "meditation_audio_file_ids", None) if settings else None
    except Exception:
        ids = None

    # bell at start
    if cfg.bell and ids and isinstance(ids, dict):
        bell_id = ids.get("bell")
        if isinstance(bell_id, str) and bell_id.strip():
            try:
                await c.message.answer_audio(audio=bell_id.strip())
            except Exception:
                pass

    # background
    if cfg.bg != BG_OFF and ids and isinstance(ids, dict):
        bg_id = ids.get(cfg.bg)
        if isinstance(bg_id, str) and bg_id.strip():
            try:
                await c.message.answer_audio(audio=bg_id.strip())
            except Exception:
                pass

    await _render(c, session, state, edit=False)


@router.callback_query(F.data == f"{CB}:pause")
async def cb_pause(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)
    running, paused = await _is_running(state)
    if not running or paused:
        await c.answer()
        return

    await _set_cfg(state, paused=True, last_pause_ts=_now_ts())
    await c.answer(_tr(l, "saved"))
    await c.message.answer(_tr(l, "paused"))
    await _render(c, session, state, edit=True)


@router.callback_query(F.data == f"{CB}:resume")
async def cb_resume(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)
    running, paused = await _is_running(state)
    if not running or not paused:
        await c.answer()
        return

    d = await state.get_data()
    last_pause_ts = float(d.get("last_pause_ts", 0.0))
    paused_total = int(d.get("paused_total", 0))
    now = _now_ts()
    add = int(now - last_pause_ts) if last_pause_ts else 0

    await _set_cfg(state, paused=False, last_pause_ts=0.0, paused_total=paused_total + max(0, add))
    await c.answer(_tr(l, "saved"))
    await c.message.answer(_tr(l, "resumed"))
    await _render(c, session, state, edit=True)


@router.callback_query(F.data == f"{CB}:stop")
async def cb_stop(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)

    await _set_cfg(state, running=False, paused=False, start_ts=0.0, duration_s=0, paused_total=0, last_pause_ts=0.0)
    await c.answer(_tr(l, "saved"))
    await c.message.answer(_tr(l, "stopped"))
    await _render(c, session, state, edit=False)


@router.callback_query(F.data == f"{CB}:status")
async def cb_status(c: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    l = await _lang(c, session)
    cfg = await _get_cfg(state)

    running, _paused = await _is_running(state)
    if not running:
        await c.answer()
        await _render(c, session, state, edit=True)
        return

    left = await _session_left_seconds(state)
    if left <= 0:
        # завершение
        await _set_cfg(state, running=False, paused=False, start_ts=0.0, duration_s=0, paused_total=0, last_pause_ts=0.0)

        # bell at end (не для sleep, либо мягко)
        try:
            ids = getattr(settings, "meditation_audio_file_ids", None) if settings else None
        except Exception:
            ids = None

        if cfg.mode != MODE_SLEEP and cfg.bell and ids and isinstance(ids, dict):
            bell_id = ids.get("bell")
            if isinstance(bell_id, str) and bell_id.strip():
                try:
                    await c.message.answer_audio(audio=bell_id.strip())
                except Exception:
                    pass

        await c.message.answer(_tr(l, "done_sleep") if cfg.mode == MODE_SLEEP else _tr(l, "done_timer"))
        await c.answer()
        await _render(c, session, state, edit=False)
        return

    await c.answer()
    await c.message.answer(
        TXT["status"][l].format(
            left=_fmt_left(l, left),
            dur=_fmt_minutes(l, cfg.duration_min),
            mode=_mode_label(l, cfg.mode),
            bg=_bg_label(l, cfg.bg),
            vol=cfg.volume,
            bell=_bell_label(l, cfg.bell),
        )
    )


__all__ = ["router"]
