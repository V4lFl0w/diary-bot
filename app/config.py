# app/config.py
import json
import os
import subprocess
import time
import urllib.request
from typing import Optional


def _as_bool(v: Optional[str], default=False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(v: Optional[str], default: int) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _discover_ngrok_https() -> str:
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as r:
            data = json.load(r)
        for t in data.get("tunnels", []):
            u = t.get("public_url", "")
            if u.startswith("https://"):
                return u.rstrip("/")
    except Exception:
        pass
    return ""


class Settings:
    def __init__(self) -> None:
        # environment
        self.environment = (os.getenv("ENV") or os.getenv("APP_ENV") or "dev").strip().lower()

        # --- DB resolution (variant 3) ---
        self.database_url = self._resolve_database_url()

        # bot token
        self.tg_token = (
            os.getenv("TG_TOKEN")
            or os.getenv("TELEGRAM_TOKEN")
            or os.getenv("BOT_TOKEN")
            or os.getenv("TELEGRAM_BOT_TOKEN")
            or os.getenv("TOKEN")
            or ""
        )

        self._public_url = (os.getenv("PUBLIC_URL") or "").strip().rstrip("/")

        # locale
        self.default_locale = (os.getenv("DEFAULT_LOCALE") or os.getenv("APP_DEFAULT_LOCALE") or "ru").strip().lower()
        if self.default_locale == "ua":
            self.default_locale = "uk"
        if self.default_locale not in {"ru", "uk", "en"}:
            self.default_locale = "ru"

        # timezone
        self.default_tz = (
            os.getenv("DEFAULT_TZ") or os.getenv("APP_DEFAULT_TZ") or "Europe/Kyiv"
        ).strip() or "Europe/Kyiv"

        # premium channel
        self.premium_channel = (os.getenv("PREMIUM_CHANNEL") or "@NoticesDiarY").strip()

        # music urls
        self.music_focus_url = (os.getenv("MUSIC_FOCUS_URL") or "https://www.youtube.com/watch?v=jfKfPfyJRdk").strip()
        self.music_sleep_url = (os.getenv("MUSIC_SLEEP_URL") or "https://www.youtube.com/watch?v=5qap5aO4i9A").strip()

        # misc
        self.reminder_tick_sec = _as_int(os.getenv("REMINDER_TICK_SEC"), 5)
        self.debug = _as_bool(os.getenv("DEBUG"), False)

    def _resolve_database_url(self) -> str:
        # 1) explicit override (highest priority)
        explicit = (os.getenv("DATABASE_URL") or os.getenv("DB_URL") or os.getenv("DB_URI") or "").strip()
        if explicit:
            return explicit

        env = (os.getenv("ENV") or os.getenv("APP_ENV") or "dev").strip().lower()

        # 2) env-specific vars
        if env in {"prod", "production"}:
            return (
                os.getenv("DATABASE_URL_PROD") or os.getenv("DB_URL_PROD") or os.getenv("DB_URI_PROD") or ""
            ).strip()

        # default: dev/local/test
        return (os.getenv("DATABASE_URL_DEV") or os.getenv("DB_URL_DEV") or os.getenv("DB_URI_DEV") or "").strip()

    def ensure_public_url(self) -> str:
        # In production we never use ngrok discovery
        if self.environment in {"prod", "production"}:
            return self._public_url
        if self._public_url:
            return self._public_url

        u = _discover_ngrok_https()
        if not u:
            try:
                subprocess.Popen(
                    ["ngrok", "http", "8000"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(2.5)
                u = _discover_ngrok_https()
            except Exception:
                u = ""

        if u:
            self._public_url = u
            os.environ["PUBLIC_URL"] = u

        return self._public_url

    @property
    def public_url(self) -> str:
        return self.ensure_public_url()


settings = Settings()
