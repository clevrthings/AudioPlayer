from __future__ import annotations

import os
import sys
from pathlib import Path


def _parse_dotenv_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        env_key = key.strip()
        if not env_key:
            continue
        env_value = _parse_dotenv_value(raw_value)
        os.environ.setdefault(env_key, env_value)


def load_dotenv() -> None:
    candidates: list[Path] = []
    meipass_root = str(getattr(sys, "_MEIPASS", "")).strip()
    if meipass_root:
        try:
            candidates.append(Path(meipass_root) / ".env")
        except Exception:  # noqa: BLE001
            pass
    try:
        candidates.append(Path.cwd() / ".env")
    except Exception:  # noqa: BLE001
        pass
    try:
        candidates.append(Path(__file__).resolve().parent / ".env")
    except Exception:  # noqa: BLE001
        pass
    if sys.argv and sys.argv[0]:
        try:
            candidates.append(Path(sys.argv[0]).resolve().parent / ".env")
        except Exception:  # noqa: BLE001
            pass
    try:
        candidates.append(Path(sys.executable).resolve().parent / ".env")
    except Exception:  # noqa: BLE001
        pass

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        _load_dotenv_file(candidate)
