from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal, Optional
from zoneinfo import ZoneInfo


@dataclass
class ParsedReminder:
    what: str
    raw_when: str
    next_run_utc: Optional[datetime] = None
    cron: Optional[str] = None


@dataclass
class ToggleRequest:
    action: Literal["enable", "disable"]
    query: Optional[str] = None
    all: bool = False


@dataclass
class ParseResult:
    intent: Literal["create", "enable", "disable"]
    reminder: Optional[ParsedReminder] = None
    toggle: Optional[ToggleRequest] = None


def parse_any(
    text: str,
    user_tz: str = "Europe/Kyiv",
    now: Optional[datetime] = None,
) -> Optional[ParseResult]:
    # 1) enable/disable
    tgl = parse_toggle(text)
    if tgl:
        return ParseResult(intent=tgl.action, toggle=tgl)

    # 2) create
    rem = parse_remind(text, user_tz=user_tz, now=now)
    if rem:
        return ParseResult(intent="create", reminder=rem)

    return None


def parse_remind(
    text: str,
    user_tz: str = "Europe/Kyiv",
    now: Optional[datetime] = None,
) -> Optional[ParsedReminder]:
    tz = ZoneInfo(user_tz)
    now = now or datetime.now(tz)
    # VF_REMIND_NOW_TZ_V2
    # normalize provided `now` into user's timezone
    try:
        if now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        else:
            now = now.astimezone(tz)
    except Exception:
        pass
    # VF_REMIND_NOW_TZ_V1
    try:
        if getattr(now, 'tzinfo', None) is not None:
            now = now.astimezone(tz)
        else:
            now = now.replace(tzinfo=tz)
    except Exception:
        now = datetime.now(tz)

    text_norm = _normalize(text)

    # VF_REMIND_DUAL_TIME_V1
    # Handle inputs like:
    #  - "напомни за вокал на 16:00 в 14:00 в четверг"
    #  - "напомни тренировка на 18:00 в 16:30 завтра"
    # Strategy: first time = event time (goes into title), second time = remind time (schedule).
    _VF_DUAL_MARK = "__vf_dual__"
    if _VF_DUAL_MARK in text_norm:
        text_norm = (text_norm or "").replace(_VF_DUAL_MARK, "").strip()
    else:
        _times = re.findall(r"\b([01]?\d|2[0-3])[:.][0-5]\d\b", text_norm)
        if len(_times) >= 2:
            # take first two occurrences in the original string order
            m_time = list(re.finditer(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", text_norm))
            if len(m_time) >= 2:
                event_time = m_time[0].group(0).replace(".", ":")
                remind_time = m_time[1].group(0).replace(".", ":")

                # remove trigger prefix to get "what + tail"
                _raw = text_norm
                _raw = re.sub(r"^\s*(?:напомни(?:ть)?|нагадай|remind(?:\s+me\s+to)?)\s+", "", _raw, flags=re.I).strip()

                # split around first time (event) then second time (remind)
                # left of first time = what part, right side contains tail and remind time
                left, _, right1 = _raw.partition(m_time[0].group(0))
                # right1 still contains remind time; cut it out and keep the tail (date/day words)
                _, _, tail = right1.partition(m_time[1].group(0))
                what = (left or "").strip()
                tail = (tail or "").strip()

                # drop leading prepositions after trigger: "за/про/о/об"
                what = re.sub(r"^(?:за|про|о|об)\s+", "", what, flags=re.I).strip()

                if what:
                    # drop leading prepositions after trigger: "за/про/о/об"
                    what = re.sub(r"^(?:за|про|о|об)\s+", "", what, flags=re.I).strip()
                    # drop trailing connector like "на" to avoid "на на"
                    what = re.sub(r"\bна\s*$", "", what, flags=re.I).strip()

                    if not what:
                        return None

                    title = f"{what} на {event_time}"

                    # Build schedule-only text using ONLY remind_time + tail (weekday/today/tomorrow/etc)
                    schedule_text = f"{tail} в {remind_time}".strip()
                    schedule_norm = _normalize(schedule_text)

                    # 1) recurring?
                    cron2 = _parse_recurring_cron(schedule_norm)
                    if cron2:
                        return ParsedReminder(
                            what=title,
                            raw_when=text.strip(),
                            cron=cron2,
                        )

                    # 2) once?
                    dt2 = _parse_once_datetime(schedule_norm, now, tz)
                    if dt2:
                        return ParsedReminder(
                            what=title,
                            raw_when=text.strip(),
                            next_run_utc=dt2.astimezone(ZoneInfo("UTC")),
                        )

                    return None

    # повторяющиеся (cron)
    cron = _parse_recurring_cron(text_norm)
    if cron:
        what = _extract_what(text_norm, recurring=True)
        if not what:
            return None
        return ParsedReminder(
            what=what,
            raw_when=text.strip(),
            cron=cron,
        )

    # разовое
    dt = _parse_once_datetime(text_norm, now, tz)
    if dt:
        what = _extract_what(text_norm, recurring=False)
        if not what:
            return None
        return ParsedReminder(
            what=what,
            raw_when=text.strip(),
            next_run_utc=dt.astimezone(ZoneInfo("UTC")),
        )

    return None


# ---------- toggle / on/off ----------

_TOGGLE_ON_WORDS = r"(?:включи|вкл|увімкни|увімк|on|enable)"
_TOGGLE_OFF_WORDS = r"(?:выключи|выкл|відключи|вимкни|вимк|off|disable)"
_REMINDER_WORDS = r"(?:напоминани(?:е|я)|нагадування|reminder(?:s)?)"
_ALL_WORDS = r"(?:все|усі|всі|all)"

_RE_TOGGLE = re.compile(
    rf"(?i)\b(?P<act>{_TOGGLE_ON_WORDS}|{_TOGGLE_OFF_WORDS})\b"
    rf"(?:\s+{_REMINDER_WORDS})?"
    rf"(?:\s+(?:про|на|по|about|for))?"
    rf"(?:\s+(?P<all>{_ALL_WORDS}))?"
    rf"(?:\s*(?P<query>.+))?$"
)


def parse_toggle(text: str) -> Optional[ToggleRequest]:
    s = _normalize(text)
    m = _RE_TOGGLE.match(s)
    if not m:
        return None

    act = m.group("act")
    action: Literal["enable", "disable"] = (
        "enable" if re.search(rf"^{_TOGGLE_ON_WORDS}$", act, flags=re.I) else "disable"
    )

    is_all = bool(m.group("all") and m.group("all").strip())
    query = (m.group("query") or "").strip()

    if is_all:
        return ToggleRequest(action=action, query=None, all=True)

    if not query:
        # «выключи напоминания» → по смыслу тоже все
        return ToggleRequest(action=action, query=None, all=True)

    return ToggleRequest(action=action, query=query, all=False)


# ---------- напоминания (create) ----------

_TRIGGERS = r"(?:напомни|нагадай|remind(?:\s+me\s+to)?)"
_RE_IN = r"(?:через|за|in)"
_RE_AT = r"(?:в|о|at)"
_RE_TODAY = r"(?:сегодня|сьогодні|today)"
_RE_TOMORROW = r"(?:завтра|tomorrow)"
# чуть расширили: добавили «щоденно», «кожен», «кожного»
_RE_EVERY = r"(?:каждый|каждую|каждое|щодня|щоденно|щотижня|щосереди|щопонеділка|кожен|кожного|every|weekdays|daily)"

_DOW_MAP = {
    # RU short
    "пн": 1,
    "вт": 2,
    "ср": 3,
    "чт": 4,
    "пт": 5,
    "сб": 6,
    "вс": 0,
    # UK short
    "пн.": 1,
    "вт.": 2,
    "ср.": 3,
    "чт.": 4,
    "пт.": 5,
    "сб.": 6,
    "нд": 0,
    "пн": 1,
    "вт": 2,
    "ср": 3,
    "чт": 4,
    "пт": 5,
    "сб": 6,
    "нд.": 0,
    "нед": 0,
    "нед.": 0,
    
    # RU
    "понедельник": 1,
    "вторник": 2,
    "среда": 3,
    "четверг": 4,
    "пятница": 5,
    "суббота": 6,
    "воскресенье": 0,
    "воскресение": 0,
    # UK
    "понеділок": 1,
    "вівторок": 2,
    "середа": 3,
    "четвер": 4,
    "пʼятниця": 5,
    "п'ятниця": 5,
    "субота": 6,
    "неділя": 0,
    # EN
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
    "sunday": 0,
}
_WEEKDAY_SET = set(_DOW_MAP.keys())

# VF_RE_TIME_V3
_RE_TIME = re.compile(
    r"\b(?P<h>[01]?\d|2[0-3])(?:\s*[:.\- ]\s*(?P<m>[0-5]\d))?\s*(?P<ampm>am|pm)?\b",
    re.I,
)

_RE_DATE_DOT = re.compile(r"\b(?P<d>\d{1,2})\.(?P<m>\d{1,2})(?:\.(?P<y>\d{4}))?\b")
_RE_DATE_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b")

_RE_REL = re.compile(
    rf"\b{_RE_IN}\s+"
    r"(?:(?P<hours>\d+)\s*(?:час(?:а|ов)?|год(?:ини)?|h|hours?)\s*)?"
    r"(?:(?P<minutes>\d+)\s*(?:минут(?:ы)?|хв(?:илин)?|m|mins?|minutes?)\s*)?"
    r"(?:(?P<days>\d+)\s*(?:дн(?:я|ей|ів)?|days?)\s*)?"
    r"(?:(?P<weeks>\d+)\s*(?:недел(?:я|и|ь)|тижн(?:і|ів)|weeks?)\s*)?"
    r"\b",
    re.I,
)


def _normalize(text: str) -> str:
    t = (text or "").strip().lower().replace("’", "'")

    # unify time separators: 14-30 / 9.05 / 14 30 -> 14:30 / 9:05
    t = re.sub(r"\b(\d{1,2})\s*[-]\s*(\d{2})\b", r"\1:\2", t)
    t = re.sub(r"\b(\d{1,2})\s+(\d{2})\b", r"\1:\2", t)
    t = re.sub(r"\b(\d{1,2})[.](\d{2})\b", r"\1:\2", t)

    # normalize weekday shorts with trailing dots: пн. -> пн
    t = re.sub(r"\b(пн|вт|ср|чт|пт|сб|вс|нд)\.", r"\1", t)

    # weekday shorts -> full names (helps _find_weekday)
    wd_map = {
        "пн": "понедельник",
        "вт": "вторник",
        "ср": "среда",
        "чт": "четверг",
        "пт": "пятница",
        "сб": "суббота",
        "вс": "воскресенье",
        "нд": "неділя",
    }
    for k, v in wd_map.items():
        t = re.sub(rf"\b{re.escape(k)}\b", v, t)

    t = re.sub(r"\s+", " ", t).strip()
    return t

def _extract_what(
    text_norm: str,
    recurring: bool,
    allow_without_trigger: bool = True,
) -> Optional[str]:
    """
    Вырезаем «что напоминать» до маркеров времени.
    """
    markers = [
        r"\b" + _RE_IN + r"\b",
        r"\b" + _RE_AT + r"\b",
        r"\b" + _RE_TODAY + r"\b",
        r"\b" + _RE_TOMORROW + r"\b",
        r"\b" + _RE_EVERY + r"\b",
        r"\b" + "|".join(map(re.escape, _WEEKDAY_SET)) + r"\b",
        r"\bпо будням\b",
        r"\bпо буднях\b",
        r"\bweekdays\b",
        r"\bdaily\b",
        r"\bщодня\b",
        r"\bщоденно\b",
    ]

    # ищем триггер, типа «напомни / remind me to»
    m = re.search(rf"{_TRIGGERS}\s+(?:me\s+to\s+)?", text_norm)
    if m:
        start = m.end()
    elif allow_without_trigger:
        start = 0
    else:
        return None

    end = len(text_norm)
    tail = text_norm[start:]
    for mk in markers:
        mm = re.search(mk, tail)
        if mm:
            end = min(end, start + mm.start())

    what = text_norm[start:end].strip(" ,.;:—-")
    # clean quotes / hidden chars just in case
    what = (what or "").strip().strip('"\'').replace("\u200b", "").strip()
    return what or None

def _parse_time_fragment(s: str) -> Optional[time]:
    m = _RE_TIME.search(s)
    if not m:
        return None

    h = int(m.group("h"))
    mnt = int(m.group("m") or 0)
    ampm = (m.group("ampm") or "").lower()

    if ampm == "pm" and 1 <= h <= 11:
        h += 12
    if ampm == "am" and h == 12:
        h = 0

    if not (0 <= h <= 23 and 0 <= mnt <= 59):
        return None

    return time(hour=h, minute=mnt)


def _apply_time(base: datetime, t: time) -> datetime:
    return base.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


def _parse_once_datetime(
    text_norm: str,
    now: datetime,
    tz: ZoneInfo,
) -> Optional[datetime]:
    # относительные: через X минут/часов/дней/недель
    m = _RE_REL.search(text_norm)
    if m and any(m.group(g) for g in ("minutes", "hours", "days", "weeks")):
        dt = now
        if m.group("minutes"):
            dt += timedelta(minutes=int(m.group("minutes")))
        if m.group("hours"):
            dt += timedelta(hours=int(m.group("hours")))
        if m.group("days"):
            dt += timedelta(days=int(m.group("days")))
        if m.group("weeks"):
            dt += timedelta(weeks=int(m.group("weeks")))
        return dt

    # даты: 2025-12-31 или 31.12.(2025)
    date_dt = None
    mi = _RE_DATE_ISO.search(text_norm)
    md = _RE_DATE_DOT.search(text_norm)
    if mi:
        y, mo, d = int(mi.group("y")), int(mi.group("m")), int(mi.group("d"))
        date_dt = datetime(y, mo, d, tzinfo=tz)
    elif md:
        d, mo = int(md.group("d")), int(md.group("m"))
        y = int(md.group("y")) if md.group("y") else now.year
        date_dt = datetime(y, mo, d, tzinfo=tz)

    tm = _parse_time_fragment(text_norm)
    if date_dt:
        dt = _apply_time(date_dt, tm or time(9, 0))
        return dt


    # VF_REMIND_WEEKDAY_ONCE_V2
    # one-time weekday: "в пятницу в 14:30" / "пн в 10"
    wd = _find_weekday(text_norm)
    if wd is not None:
        base = now
        tm2 = _parse_time_fragment(text_norm) or time(9, 0)
        # python weekday: Mon=0..Sun=6 ; our map uses Mon=1..Sun=0 (cron style)
        now_wd = base.weekday()
        target_wd = 6 if wd == 0 else (wd - 1)
        days_ahead = (target_wd - now_wd) % 7
        # If user explicitly says "в <weekday>" and today is that weekday, treat as NEXT week (unless "today" present).
        # Example: "в четверг в 14:00" (next week if today is Thursday), but "чт в 9:05" -> today (nearest).
        if days_ahead == 0 and not re.search(rf"\b{_RE_TODAY}\b", text_norm):
            if re.search(r"\bв\s+(понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)\b", text_norm):
                days_ahead = 7

        dt = _apply_time(base + timedelta(days=days_ahead), tm2)
        # if same day and time already passed -> next week
        if days_ahead == 0 and dt <= base:
            dt = _apply_time(base + timedelta(days=7), tm2)
        return dt

    # сегодня
    if re.search(rf"\b{_RE_TODAY}\b", text_norm):
        dt = _apply_time(now, tm or time(9, 0))
        # если время не указано и уже прошло — чуть сдвинем
        if tm is None and dt < now:
            dt = dt + timedelta(hours=1)
        return dt

    # завтра
    if re.search(rf"\b{_RE_TOMORROW}\b", text_norm):
        base = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        if tm:
            base = _apply_time(base, tm)
        return base

    # VF_REMIND_WEEKDAY_ONCE_V1
    # одноразово: «пн в 10», «в пт 14:30», «чт 9.05» → ближайший такой день недели
    wd = _find_weekday(text_norm)
    if wd is not None:
        base = now
        # Python: Monday=0..Sunday=6; our map uses Monday=1..Sunday=0
        py_target = 6 if wd == 0 else wd - 1
        delta = (py_target - base.weekday()) % 7
        # если это сегодня, но время уже прошло — переносим на следующую неделю
        dt_candidate = base + timedelta(days=delta)
        tm2 = tm or _parse_time_fragment(text_norm)
        dt_candidate = _apply_time(dt_candidate, tm2 or time(9, 0))
        if dt_candidate <= now:
            dt_candidate = dt_candidate + timedelta(days=7)
        return dt_candidate

    # только время → сегодня или завтра, если уже прошло
    if tm:
        dt = _apply_time(now, tm)
        if dt <= now:
            dt += timedelta(days=1)
        return dt

    return None


def _parse_recurring_cron(text_norm: str) -> Optional[str]:
    """
    Парсим повторяющиеся напоминания в cron-строку:
    - every day at 10:00 / каждый день в 10 / щодня о 10:00
    - по будням в 9 / weekdays at 9
    - every monday at 10, щосереди о 20 и т.п.
    """
    tm = _parse_time_fragment(text_norm)
    if not tm:
        return None

    minute = tm.minute
    hour = tm.hour

    # daily: каждый день / щодня / щоденно / every day
    if re.search(
        r"\b("
        r"daily"
        r"|щодня"
        r"|щоденно"
        r"|каждый день"
        r"|кожен день"
        r"|кожного дня"
        r"|every day"
        r"|everyday"
        r")\b",
        text_norm,
    ):
        return f"{minute} {hour} * * *"

    # weekdays: по будням / по буднях / weekdays
    if re.search(r"\b(weekdays|по будням|по буднях)\b", text_norm):
        return f"{minute} {hour} * * 1-5"

    # every monday / щосереди и т.п.
    if re.search(r"\b(кажд\w+|щос\w+|every)\b", text_norm):
        wd = _find_weekday(text_norm)
        if wd is not None:
            return f"{minute} {hour} * * {wd}"

    # VF_REMIND_RECURRING_STRICT_V1
    # plain weekday + time is treated as ONE-TIME (handled in _parse_once_datetime)

    return None


def _find_weekday(text_norm: str) -> Optional[int]:
    pos = -1
    val = None
    for name, dow in _DOW_MAP.items():
        m = re.search(rf"\b{name}\b", text_norm)
        if m and m.start() > pos:
            pos = m.start()
            val = dow
    return val


__all__ = [
    "ParsedReminder",
    "ToggleRequest",
    "ParseResult",
    "parse_any",
    "parse_remind",
    "parse_toggle",
]
