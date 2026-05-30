from __future__ import annotations

import argparse
import plistlib
import shutil
import stat
import sys
from pathlib import Path


APP_NAME = "Ollama Manual Pull"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_app(
    *,
    output_dir: Path = PROJECT_ROOT / "dist",
    python_executable: Path = Path(sys.executable),
) -> Path:
    app_path = output_dir / f"{APP_NAME}.app"
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"

    if app_path.exists():
        shutil.rmtree(app_path)

    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    shutil.copytree(PROJECT_ROOT / "src", resources / "src")
    shutil.copy2(PROJECT_ROOT / "README.md", resources / "README.md")
    shutil.copy2(PROJECT_ROOT / "LICENSE", resources / "LICENSE")

    _write_info_plist(contents / "Info.plist")
    _write_launcher(macos / APP_NAME, python_executable)
    return app_path


def _write_info_plist(path: Path) -> None:
    payload = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": APP_NAME,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIdentifier": "local.ollama-manual-pull",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "11.0",
    }
    path.write_bytes(plistlib.dumps(payload, sort_keys=True))


def _write_launcher(path: Path, python_executable: Path) -> None:
    script = f"""#!/bin/sh
set -eu

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RESOURCES_DIR="$APP_DIR/Resources"
PYTHON="{python_executable}"

if [ ! -x "$PYTHON" ]; then
  if [ -n "${{PYENV_ROOT:-}}" ] && [ -x "$PYENV_ROOT/shims/python3" ]; then
    PYTHON="$PYENV_ROOT/shims/python3"
  elif [ -x "$HOME/.pyenv/shims/python3" ]; then
    PYTHON="$HOME/.pyenv/shims/python3"
  elif [ -x "/opt/homebrew/bin/python3" ]; then
    PYTHON="/opt/homebrew/bin/python3"
  else
    PYTHON="/usr/bin/python3"
  fi
fi

export PYTHONPATH="$RESOURCES_DIR/src"
exec "$PYTHON" -c "from ollama_manual_pull import run_web; raise SystemExit(run_web())"
"""
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the macOS app bundle.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)

    app_path = build_app(output_dir=args.output_dir, python_executable=args.python)
    print(app_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
