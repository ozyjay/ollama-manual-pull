"""Cross-platform terminal helper for Ollama Manual Pull."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _python() -> str:
    return sys.executable


def _env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(SRC)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not existing else os.pathsep.join([src, existing])
    return env


def _run(args: list[str]) -> int:
    return subprocess.call(args, cwd=ROOT, env=_env())


def _run_module(module: str, args: list[str]) -> int:
    return _run([_python(), "-m", module, *args])


def _install(_args: argparse.Namespace) -> int:
    return _run([_python(), "-m", "pip", "install", "-e", "."])


def _pull(args: argparse.Namespace) -> int:
    return _run_module("ollama_manual_pull", args.model_args)


def _web(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(SRC))
    from ollama_manual_pull import run_web

    return run_web(args.web_args)


def _test(_args: argparse.Namespace) -> int:
    checks = [
        [_python(), "-m", "unittest", "discover", "-s", "tests", "-v"],
        [_python(), "-m", "py_compile", *map(str, (SRC / "ollama_manual_pull").glob("*.py")), *map(str, (ROOT / "tests").glob("*.py"))],
        ["node", "--check", str(SRC / "ollama_manual_pull" / "web" / "app.js")],
    ]
    for check in checks:
        code = _run(check)
        if code != 0:
            return code
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Ollama Manual Pull from Windows/pymanager or macOS/pyenv terminals."
    )
    parser.add_argument("--version-info", action="store_true", help="Print Python and OS details.")
    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser("install", help="Install this repo in editable mode.")
    install_parser.set_defaults(func=_install)

    pull_parser = subparsers.add_parser("pull", help="Download and register a model.")
    pull_parser.add_argument("model_args", nargs=argparse.REMAINDER, help="Arguments for ollama-manual-pull.")
    pull_parser.set_defaults(func=_pull)

    web_parser = subparsers.add_parser("web", help="Launch the local web UI.")
    web_parser.add_argument("web_args", nargs=argparse.REMAINDER, help="Arguments for the web server.")
    web_parser.set_defaults(func=_web)

    test_parser = subparsers.add_parser("test", help="Run development checks.")
    test_parser.set_defaults(func=_test)

    args = parser.parse_args(argv)

    if args.version_info:
        print(f"OS: {platform.platform()}")
        print(f"Python: {sys.version.split()[0]} at {sys.executable}")
        return 0

    if args.command is None:
        parser.print_help()
        return 2

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
