# ollama-manual-pull

Download and register Ollama models manually, without relying on `ollama pull`.

This is useful when `ollama pull` fails partway through a model download or exits with a registry error such as `EOF`, but direct registry blob downloads still work.

## Usage

Run from the repo:

```bash
PYTHONPATH=src python3 -m ollama_manual_pull qwen3-coder:30b
```

Or install it in editable mode:

```bash
python3 -m pip install -e .
ollama-manual-pull qwen3-coder:30b
```

The Ollama app/server does not need to be running while this tool downloads. After it finishes, start Ollama and check:

```bash
ollama list
ollama run qwen3-coder:30b
```

## Options

```bash
ollama-manual-pull --dry-run qwen3-coder:30b
ollama-manual-pull --models-dir /path/to/models qwen3-coder:30b
ollama-manual-pull --registry https://registry.ollama.ai qwen3-coder:30b
```

By default, models are written to `$OLLAMA_MODELS` when set, otherwise `~/.ollama/models`.

## Web UI

Launch the local browser UI:

```bash
ollama-manual-pull-web
```

The web UI runs on `127.0.0.1`, queues one model download at a time, and preserves the same safety behavior as the CLI downloader. Search is best effort; direct model references such as `qwen3-coder:30b` always remain supported.

## Safety

- Existing verified blobs are reused.
- Downloads resume from `.manual-download` temp files.
- Every blob is verified by SHA-256 before being registered.
- The manifest is written only after all referenced blobs verify.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m py_compile src/ollama_manual_pull/*.py tests/test_ollama_manual_pull.py
```
