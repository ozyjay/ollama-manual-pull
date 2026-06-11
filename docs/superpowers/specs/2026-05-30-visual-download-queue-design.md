# Visual Download Queue Design

## Context

`ollamapull` is currently a small Python command-line tool that manually downloads and registers Ollama models from a registry. It already handles the important safety behavior: resumable blob downloads, SHA-256 verification, existing blob reuse, and manifest installation only after all referenced blobs verify.

The new feature is a local web app that turns the downloader into a visual queue tool. It should preserve the existing downloader behavior and make it easier to discover models, queue downloads, inspect progress, and recover from failures.

## Goals

- Provide a local browser UI for manually downloading Ollama models.
- Support a queue of model downloads with one active download at a time.
- Add best-effort model search while keeping manual model reference entry as the reliable fallback.
- Show enough operational detail to understand what the tool is doing without requiring command-line usage.
- Reuse the current downloader logic where possible instead of replacing the core behavior.

## Non-Goals

- Multiple simultaneous downloads.
- Running or chatting with downloaded models.
- Replacing the Ollama app or local Ollama server.
- Depending on the Ollama server being active while downloads run.
- Guaranteeing search results if the public Ollama search page changes or network access is unavailable.

## User Experience

The app is a local web page with an Operator Panel layout.

The main area contains:

- A search/input box that accepts either a search term or a direct model reference such as `qwen3-coder:30b`.
- Search results with model names, brief descriptions, and available tags when discoverable.
- An active download card with model name, status, progress, current blob, and transferred bytes when available.
- A queue list showing waiting, completed, failed, and paused items.

The right-side panel contains details for the selected queue item:

- Registry URL.
- Models directory.
- Retry count.
- Current blob digest.
- Disk target information when available.
- Recent messages and errors.

Primary controls:

- Add searched or manually entered model to queue.
- Start or continue the queue.
- Pause after the current download finishes.
- Retry a failed item.
- Remove waiting or completed items from the queue.

## Search Behavior

Search is best effort because the official Ollama API documentation does not expose a stable JSON model-search endpoint.

The backend should support:

- Direct model references without using search.
- Querying public Ollama search pages, such as `https://ollama.com/search` or `https://registry.ollama.com/search`, and parsing useful result metadata.
- Returning a clear unavailable state if search fails.

Manual entry remains the dependable path. A user can always paste a full model reference and queue it even when search is unavailable.

## Architecture

Add a lightweight local web server to the Python package. The server owns queue state and exposes HTTP endpoints consumed by the browser UI.

Suggested modules:

- `core.py`: keep model parsing, path calculation, manifest fetching, blob downloading, verification, and manifest installation.
- `queue.py`: queue item model, queue state, worker lifecycle, status transitions, and event messages.
- `search.py`: best-effort Ollama search helpers.
- `server.py`: local HTTP server, API routes, static UI serving, and graceful shutdown.
- `web/`: static HTML, CSS, and JavaScript for the browser app.

The existing CLI should continue to work. A new command or option can launch the web UI, for example:

```bash
ollamapull-web
```

or:

```bash
ollamapull --web
```

The implementation should choose whichever fits the package cleanly.

## Data Flow

1. User opens the local web app.
2. User searches for a model or enters a direct model reference.
3. UI sends an add request to the server.
4. Server validates the model reference and creates a waiting queue item.
5. A single worker starts the next waiting item when no item is active.
6. Worker uses the existing downloader flow and emits structured progress events.
7. UI polls or streams queue state and updates the active card, queue list, and details panel.
8. On success, the item becomes completed after the manifest is installed.
9. On failure, the item becomes failed with a retry option.

## State Model

Queue item statuses:

- `waiting`: queued but not started.
- `running`: active download.
- `completed`: verified and registered.
- `failed`: ended with an error.
- `paused`: intentionally stopped between downloads, or ready to resume if no active transfer is running.

For v1, pausing should avoid interrupting an in-flight blob write. The pause control means "finish the current item, then stop before the next one." This keeps file integrity simple and aligns with the existing resumable temp-file behavior.

## Error Handling

- Invalid model references should be rejected before entering the queue.
- Search failures should not block manual entry.
- Manifest fetch failures should mark the item failed and show the error.
- Blob download failures should use the configured retry behavior, then mark the item failed if retries are exhausted.
- Checksum mismatches should fail the item and preserve enough detail for the user to retry or inspect files.
- Manifest writing should happen only after all referenced blobs verify.

## Testing

Keep existing CLI tests passing.

Add focused tests for:

- Queue status transitions for success and failure.
- One-active-download-at-a-time behavior.
- Adding multiple items while one is running.
- Retry path for failed queue items.
- Direct model reference validation through the server API.
- Search helper parsing using saved sample HTML or mocked HTTP responses.
- API responses for queue state and errors.

For the browser UI, keep v1 tests lightweight:

- Static asset serving.
- Core JavaScript state rendering if a JS test setup already exists or can be added cheaply.
- Manual browser smoke test for adding an item, viewing details, and seeing queue status update.

## Open Decisions

- Whether the web launcher should be a separate console script or a `--web` option.
- Whether queue updates should use polling first or server-sent events. Polling is simpler for v1; server-sent events can be added later if the UI feels laggy.
- Whether search should prefer `ollama.com/search` or `registry.ollama.com/search` as the primary source after implementation tests confirm which page is easier and more stable to parse.
