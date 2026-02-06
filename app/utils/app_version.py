from __future__ import annotations

from pathlib import Path


def _read_git_sha(root: Path) -> str | None:
    git_dir = root / ".git"
    head = git_dir / "HEAD"
    if not head.exists():
        return None

    head_txt = head.read_text(encoding="utf-8").strip()

    if head_txt.startswith("ref:"):
        ref_path = head_txt.split(" ", 1)[1].strip()
        ref_file = git_dir / ref_path
        if ref_file.exists():
            return ref_file.read_text(encoding="utf-8").strip()

        packed = git_dir / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.startswith("#") or line.startswith("^") or not line.strip():
                    continue
                parts = line.split(" ", 1)
                if len(parts) != 2:
                    continue
                sha, ref = parts
                if ref.strip() == ref_path:
                    return sha.strip()
        return None

    if len(head_txt) >= 7:
        return head_txt
    return None


def get_app_version() -> str:
    root = Path(__file__).resolve().parents[2]
    sha = _read_git_sha(root)
    if not sha:
        return "unknown"
    return sha[:8]
