from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


_LOG_LOCK = threading.Lock()


def default_log_file() -> Path:
    configured = os.environ.get("OLLAMA_MANUAL_PULL_LOG_FILE")
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "Ollama Manual Pull" / "app.log"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "ollama-manual-pull" / "app.log"


def write_log(message: str, **fields: Any) -> None:
    path = default_log_file()
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    field_text = " ".join(f"{key}={_format_value(value)}" for key, value in fields.items())
    line = f"{timestamp} {message}"
    if field_text:
        line = f"{line} {field_text}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
    except OSError:
        return


def _format_value(value: Any) -> str:
    text = str(value).replace("\n", "\\n")
    return text if " " not in text else repr(text)
