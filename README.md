# ollama-manual-pull

Download and register Ollama models manually, without relying on `ollama pull`.

This is useful when `ollama pull` fails partway through a model download or exits with a registry error such as `EOF`, but direct registry blob downloads still work.

## Usage

This project requires Python 3.10 or newer.

Install it in editable mode from the repo:

```bash
python -m pip install -e .
ollama-manual-pull qwen3-coder:30b
```

On some macOS/Linux systems, use `python3` instead of `python`:

```bash
python3 -m pip install -e .
ollama-manual-pull qwen3-coder:30b
```

You can also run it without installing by setting `PYTHONPATH` to `src`.

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m ollama_manual_pull qwen3-coder:30b
```

macOS/Linux shells:

```bash
PYTHONPATH=src python3 -m ollama_manual_pull qwen3-coder:30b
```

The Ollama app/server does not need to be running while this tool downloads. After it finishes, start Ollama and check:

```bash
ollama list
ollama run qwen3-coder:30b
```

During large blob downloads, the tool prints resumable progress with percent, downloaded size, total size, transfer speed, and ETA:

```text
Downloading: sha256-1194192cf2a187eb02722edcc3f77b11d21f537048ce04b67ccf8ba78863006a
Resuming at: 16.7GB
 96.0% 17.8GB/18.6GB 4.8MB/s eta 2m04s
```

## Options

```bash
ollama-manual-pull --dry-run qwen3-coder:30b
ollama-manual-pull --models-dir /path/to/models qwen3-coder:30b
ollama-manual-pull --registry https://registry.ollama.ai qwen3-coder:30b
ollama-manual-pull --resume-from ~/.ollama/models/blobs/sha256-...-partial qwen3-coder:30b
```

By default, models are written to `$OLLAMA_MODELS` when set, otherwise `~/.ollama/models`.

## Web UI

Launch the local browser UI:

```bash
ollama-manual-pull-web
```

The web UI runs on `127.0.0.1`, queues one model download at a time, and preserves the same safety behavior as the CLI downloader. Search is best effort; direct model references such as `qwen3-coder:30b` always remain supported.

## macOS App

Build a lightweight local `.app` bundle:

```bash
python3 scripts/build_macos_app.py
open "dist/Ollama Manual Pull.app"
```

The app launches the same local web UI and opens it in your browser. The builder bakes in the Python interpreter used to run the build command, which works well with `pyenv` because Finder-launched apps do not inherit your shell setup. If that interpreter is unavailable later, the launcher falls back to common `pyenv`, Homebrew, and system `python3` paths.

## Safety

- Existing verified blobs are reused.
- Downloads resume from `.manual-download` temp files.
- `--resume-from` can point at a specific partial blob file when you know which file should be continued.
- When `--resume-from` points to a larger clean partial, a smaller default `.manual-download` restart file is removed automatically.
- Every blob is verified by SHA-256 before being registered.
- The manifest is written only after all referenced blobs verify.

## Development

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m py_compile src/ollama_manual_pull/*.py tests/*.py
node --check src/ollama_manual_pull/web/app.js
```

macOS/Linux shells:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m py_compile src/ollama_manual_pull/*.py tests/*.py
node --check src/ollama_manual_pull/web/app.js
```
