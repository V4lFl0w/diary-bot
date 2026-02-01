from __future__ import annotations

import random
from typing import Optional


def _norm_lang(v: Optional[str]) -> str:
    if not v:
        return "ru"
    s = (v or "").strip().lower()
    if s.startswith("uk"):
        return "uk"
    if s.startswith("en"):
        return "en"
    if s.startswith("ru"):
        return "ru"
    return "ru"


# Комбинаторика = много уникальных вариантов без “робота”
_BANK = {
    "ru": {
        "openers": [
            "Слушай.",
            "Ок.",
            "Дышим.",
            "Спокойно.",
            "Братски:",
            "Честно:",
            "Без героизма:",
        ],
        "truths": [
            "тебе не нужна мотивация — тебе нужен старт",
            "не надо идеала — нужен шаг",
            "даже 2 минуты — это уже победа",
            "сейчас важнее действие, чем настроение",
            "лучше маленькое, чем ноль",
            "ты не сломан — ты устал",
            "ты не обязан тащить один",
        ],
        "actions": [
            "сделай первый микро-шаг",
            "выбери одну простую задачу",
            "начни с 2 минут",
            "убери одну помеху",
            "открой то, что откладываешь",
            "сделай самое лёгкое действие",
        ],
        "finishes": [
            "и дальше станет проще.",
            "и мозг включится по ходу.",
            "и ты снова в игре.",
            "и это уже контроль.",
            "и вот это — сила.",
            "и ты почувствуешь опору.",
        ],
        "one_liners": [
            "Дисциплина — это забота о себе, а не наказание.",
            "Не жди уверенности. Действие приносит уверенность.",
            "Стабильность выигрывает у вдохновения.",
            "Сейчас — один шаг. Завтра — второй.",
        ],
    },
    "uk": {
        "openers": [
            "Слухай.",
            "Ок.",
            "Дихаємо.",
            "Спокійно.",
            "По-людськи:",
            "Чесно:",
            "Без героїзму:",
        ],
        "truths": [
            "тобі не потрібна мотивація — тобі потрібен старт",
            "не треба ідеалу — потрібен крок",
            "навіть 2 хвилини — це вже перемога",
            "зараз важливіша дія, ніж настрій",
            "краще маленьке, ніж нуль",
            "ти не зламаний — ти втомився",
            "ти не мусиш тягнути сам",
        ],
        "actions": [
            "зроби перший мікро-крок",
            "обери одну просту задачу",
            "почни з 2 хвилин",
            "прибери одну перешкоду",
            "відкрий те, що відкладаєш",
            "зроби найлегшу дію",
        ],
        "finishes": [
            "і далі стане легше.",
            "і мозок увімкнеться по ходу.",
            "і ти знову в грі.",
            "і це вже контроль.",
            "і це — сила.",
            "і з’явиться опора.",
        ],
        "one_liners": [
            "Дисципліна — це турбота про себе, а не покарання.",
            "Не чекай впевненості. Дія дає впевненість.",
            "Стабільність сильніша за натхнення.",
            "Зараз — один крок. Завтра — другий.",
        ],
    },
    "en": {
        "openers": [
            "Listen.",
            "Ok.",
            "Breathe.",
            "Easy.",
            "Human truth:",
            "Honestly:",
            "No hero mode:",
        ],
        "truths": [
            "you don’t need motivation — you need a start",
            "you don’t need perfection — you need a step",
            "even 2 minutes is a win",
            "action matters more than mood",
            "small beats zero",
            "you’re not broken — you’re tired",
            "you don’t have to carry it alone",
        ],
        "actions": [
            "take the first micro-step",
            "pick one simple task",
            "start with 2 minutes",
            "remove one blocker",
            "open what you’ve been avoiding",
            "do the easiest action",
        ],
        "finishes": [
            "and it gets easier.",
            "and your brain kicks in while moving.",
            "and you’re back in the game.",
            "and that’s control.",
            "and that’s strength.",
            "and you’ll feel support.",
        ],
        "one_liners": [
            "Discipline is self-care, not punishment.",
            "Don’t wait for confidence. Action creates it.",
            "Consistency beats inspiration.",
            "One step now. Another tomorrow.",
        ],
    },
}


def generate_quote(lang: str, *, seed: Optional[int] = None) -> str:
    lang = _norm_lang(lang)
    b = _BANK[lang]

    rnd = random.Random(seed) if seed is not None else random

    # 20% — короткая “чистая” цитата
    if rnd.random() < 0.2:
        return rnd.choice(b["one_liners"])

    return " ".join(
        [
            rnd.choice(b["openers"]),
            rnd.choice(b["truths"]) + ",",
            rnd.choice(b["actions"]) + " —",
            rnd.choice(b["finishes"]),
        ]
    )
