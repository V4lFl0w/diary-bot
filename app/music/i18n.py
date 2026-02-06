from __future__ import annotations

TXT: dict[str, dict[str, str]] = {
    "menu": {"ru": "Выбери плейлист:", "uk": "Оберіть плейлист:", "en": "Choose a playlist:"},
    "focus_btn": {"ru": "Фокус", "uk": "Фокус", "en": "Focus"},
    "sleep_btn": {"ru": "Сон", "uk": "Сон", "en": "Sleep"},
    "open_focus": {"ru": "Открыть Focus ▶️", "uk": "Відкрити Focus ▶️", "en": "Open Focus ▶️"},
    "open_sleep": {"ru": "Открыть Sleep ▶️", "uk": "Відкрити Sleep ▶️", "en": "Open Sleep ▶️"},
    "my_btn": {"ru": "Мой плейлист", "uk": "Мій плейлист", "en": "My playlist"},
    "add_btn": {"ru": "Добавить трек", "uk": "Додати трек", "en": "Add a track"},
    "search_btn": {"ru": "🔎 Поиск", "uk": "🔎 Пошук", "en": "🔎 Search"},
    "link_btn": {"ru": "➕ По ссылке", "uk": "➕ За посиланням", "en": "➕ By link"},
    "link_hint": {
        "ru": "Пришли прямую HTTPS-ссылку на full аудио (mp3/ogg/m4a).",
        "uk": "Надішли пряму HTTPS-лінку на full аудіо (mp3/ogg/m4a).",
        "en": "Send a direct HTTPS link to full audio (mp3/ogg/m4a).",
    },
    "bad_url": {
        "ru": "Нужна прямая HTTPS-ссылка на файл (mp3/ogg/m4a).",
        "uk": "Потрібне пряме HTTPS-посилання на файл (mp3/ogg/m4a).",
        "en": "Need a direct HTTPS file link (mp3/ogg/m4a).",
    },
    "link_saved": {
        "ru": "Сохранил ссылку в плейлист ✅",
        "uk": "Зберіг посилання у плейлист ✅",
        "en": "Saved link to playlist ✅",
    },
    "search_hint": {
        "ru": "Напиши название трека или артиста.",
        "uk": "Напиши назву треку або артиста.",
        "en": "Type a song name or an artist.",
    },
    "search_results": {
        "ru": "Результаты поиска (full):",
        "uk": "Результати пошуку (full):",
        "en": "Search results (full):",
    },
    "back": {"ru": "⬅️ Назад", "uk": "⬅️ Назад", "en": "⬅️ Back"},
    "send_audio_hint": {
        "ru": "Пришли мне аудио-файл(ы) — добавлю в твой плейлист.",
        "uk": "Надішли аудіофайл(и) — додам у твій плейлист.",
        "en": "Send me audio file(s) — I will add them to your playlist.",
    },
    "saved": {
        "ru": "Сохранил в твой плейлист ✅",
        "uk": "Зберіг у твій плейлист ✅",
        "en": "Saved to your playlist ✅",
    },
    "empty": {"ru": "Пока пусто.", "uk": "Поки порожньо.", "en": "No tracks yet."},
    "your_tracks": {"ru": "Твои треки:", "uk": "Твої треки:", "en": "Your tracks:"},
    "too_many": {
        "ru": "Пока максимум 50 треков в плейлисте.",
        "uk": "Поки максимум 50 треків у плейлисті.",
        "en": "For now the playlist limit is 50 tracks.",
    },
    "need_start": {"ru": "Нажми /start", "uk": "Натисни /start", "en": "Type /start"},
}


def normalize(code: str | None) -> str:
    c = (code or "ru").strip().lower()
    if c.startswith(("ua", "uk")):
        return "uk"
    if c.startswith("en"):
        return "en"
    if c.startswith("ru"):
        return "ru"
    return "ru"


def tr(lang: str | None, key: str) -> str:
    l = normalize(lang)
    return TXT.get(key, {}).get(l, TXT.get(key, {}).get("ru", key))
