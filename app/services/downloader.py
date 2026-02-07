import subprocess
import tempfile
from pathlib import Path

def download_from_youtube(query: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    tmp.close()

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "-o", tmp.name,
        f"ytsearch1:{query}",
    ]

    try:
        subprocess.check_call(cmd)
    except FileNotFoundError as e:
        # yt-dlp is missing on server
        raise RuntimeError("YTDLP_NOT_INSTALLED") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError("YTDLP_FAILED") from e

    return Path(tmp.name)
