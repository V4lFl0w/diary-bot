from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def download_from_youtube(query: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp.close()

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format",
        "mp3",
        "-o",
        tmp.name,
        f"ytsearch1:{query}",
    ]

    try:
        # capture output so we can classify errors
        r = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("YTDLP_NOT_INSTALLED") from e

    if r.returncode != 0:
        err = (r.stderr or "") + "\n" + (r.stdout or "")
        err_low = err.lower()

        # YouTube anti-bot / sign-in gate
        if (
            ("sign in to confirm" in err_low)
            or ("you're not a bot" in err_low)
            or ("confirm you’re not a bot" in err_low)
        ):
            raise RuntimeError("YTDLP_YT_BOT_CHECK")

        # generic failure
        raise RuntimeError("YTDLP_FAILED")

    return Path(tmp.name)
