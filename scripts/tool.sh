#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PYTHON:-python3}"
APP_NAME="Ollama Manual Pull.app"
BUILD_DIR="${ROOT_DIR}/build"
APP_PATH="${BUILD_DIR}/${APP_NAME}"

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/tool.sh <command> [args...]

Commands:
  test             Run the Python test suite
  build            Build build/Ollama Manual Pull.app
  run-app          Build if needed, then open the app from build/
  install          Build and install the app into /Applications
  install-cli      Install the CLI and web entry points in editable mode
  run-web          Run the local web UI
  pull             Run the CLI downloader; pass a model ref and options
  clean            Remove local build output
  help             Show this help

Environment:
  PYTHON           Python executable to use; defaults to python3

Examples:
  ./scripts/tool.sh test
  ./scripts/tool.sh build
  ./scripts/tool.sh run-app
  ./scripts/tool.sh install
  ./scripts/tool.sh install-cli
  ./scripts/tool.sh run-web
  ./scripts/tool.sh pull qwen3-coder:30b --dry-run
USAGE
}

need_python() {
  if ! command -v "${PYTHON}" >/dev/null 2>&1; then
    echo "Python executable not found: ${PYTHON}" >&2
    exit 127
  fi
}

run_build() {
  need_python
  cd "${ROOT_DIR}"
  "${PYTHON}" scripts/build_macos_app.py --output-dir "${BUILD_DIR}" "$@"
}

command="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "${command}" in
  test)
    need_python
    cd "${ROOT_DIR}"
    PYTHONPATH=src "${PYTHON}" -m unittest discover -s tests -v "$@"
    ;;
  build)
    run_build "$@"
    ;;
  run-app)
    if [[ ! -d "${APP_PATH}" ]]; then
      run_build
    fi
    open "${APP_PATH}"
    ;;
  install)
    run_build --install "$@"
    ;;
  install-cli)
    need_python
    cd "${ROOT_DIR}"
    "${PYTHON}" -m pip install -e . "$@"
    ;;
  run-web)
    need_python
    cd "${ROOT_DIR}"
    PYTHONPATH=src "${PYTHON}" -c 'from ollama_manual_pull import run_web; raise SystemExit(run_web())' "$@"
    ;;
  pull)
    need_python
    cd "${ROOT_DIR}"
    PYTHONPATH=src "${PYTHON}" -m ollama_manual_pull "$@"
    ;;
  clean)
    rm -rf "${ROOT_DIR}/dist"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: ${command}" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
esac
