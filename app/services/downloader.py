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
        f"ytsearch1:{query}"
    ]

    subprocess.check_call(cmd)
    return Path(tmp.name)
