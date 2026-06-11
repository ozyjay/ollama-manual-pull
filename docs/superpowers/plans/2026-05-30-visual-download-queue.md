# Visual Download Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local browser-based Operator Panel for queuing Ollama manual downloads one at a time, with best-effort model search and operational details.

**Architecture:** Keep the existing downloader as the engine and add a small standard-library HTTP server around it. Introduce a queue manager that serializes work through one background worker, exposes structured state, and feeds a static HTML/CSS/JS UI.

**Tech Stack:** Python 3.10+, `http.server`, `threading`, `urllib`, `unittest`, plain HTML/CSS/JavaScript.

---

## File Structure

- Modify `src/ollama_pull/core.py`: add optional progress callbacks while preserving the existing CLI output.
- Create `src/ollama_pull/queue.py`: queue item dataclasses, state transitions, single-worker orchestration, and retry/remove controls.
- Create `src/ollama_pull/search.py`: best-effort public Ollama search page parsing.
- Create `src/ollama_pull/server.py`: local HTTP API and static web app launcher.
- Create `src/ollama_pull/web/index.html`: Operator Panel UI shell.
- Create `src/ollama_pull/web/styles.css`: layout and component styling.
- Create `src/ollama_pull/web/app.js`: client-side polling, rendering, search, and queue actions.
- Modify `src/ollama_pull/__init__.py`: export new public entry points.
- Modify `src/ollama_pull/__main__.py`: keep CLI behavior and route `--web` through the web launcher.
- Modify `pyproject.toml`: add `ollamapull-web` console script.
- Add `tests/test_queue.py`: queue state and one-active-worker behavior.
- Add `tests/test_search.py`: saved HTML parsing and failure behavior.
- Add `tests/test_server.py`: API route smoke tests.

## Task 1: Add Progress Events To Core Downloader

**Files:**
- Modify: `src/ollama_pull/core.py`
- Modify: `tests/test_ollama_pull.py`

- [ ] **Step 1: Write tests for progress callbacks**

Add these tests to `tests/test_ollama_pull.py`:

```python
    def test_download_blob_reports_existing_blob_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = omp.parse_model_ref("example:latest")
            paths = omp.model_paths(root, ref)
            paths.blobs.mkdir(parents=True)
            digest = "sha256:" + hashlib.sha256(b"ready").hexdigest()
            (paths.blobs / omp.digest_filename(digest)).write_bytes(b"ready")
            events = []

            omp.download_blob(
                registry="https://registry.ollama.ai",
                ref=ref,
                paths=paths,
                digest=digest,
                retries=0,
                dry_run=False,
                progress=events.append,
            )

            self.assertEqual(events[-1]["type"], "blob-complete")
            self.assertEqual(events[-1]["digest"], digest)
            self.assertTrue(events[-1]["reused"])

    def test_pull_model_reports_manifest_progress(self):
        manifest = {
            "schemaVersion": 2,
            "config": {"digest": "sha256:" + "a" * 64, "size": 3},
            "layers": [],
        }
        events = []

        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.object(omp, "fetch_json", return_value=manifest):
                with unittest.mock.patch.object(omp, "download_blob"):
                    omp.pull_model(
                        "example:latest",
                        models_dir=Path(tmp),
                        registry="https://registry.ollama.ai",
                        retries=0,
                        dry_run=True,
                        progress=events.append,
                    )

        self.assertEqual(events[0]["type"], "manifest-fetch")
        self.assertEqual(events[0]["model"], "example:latest")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_ollama_pull -v
```

Expected: failure because `download_blob()` and `pull_model()` do not accept `progress`.

- [ ] **Step 3: Implement progress callback support**

In `src/ollama_pull/core.py`, add:

```python
ProgressCallback = Any


def emit_progress(progress: ProgressCallback | None, event: dict[str, Any]) -> None:
    if progress is not None:
        progress(event)
```

Update `download_blob()` signature:

```python
def download_blob(
    *,
    registry: str,
    ref: ModelRef,
    paths: ModelPaths,
    digest: str,
    retries: int,
    dry_run: bool,
    progress: ProgressCallback | None = None,
) -> None:
```

Emit events in `download_blob()`:

```python
    if verify_file(final, digest):
        print(f"OK already present: {final.name}", flush=True)
        emit_progress(progress, {"type": "blob-complete", "digest": digest, "path": str(final), "reused": True})
        return

    if dry_run:
        print(f"Would download: {url}", flush=True)
        emit_progress(progress, {"type": "blob-dry-run", "digest": digest, "url": url})
        return
```

Inside the download attempt, emit before opening the URL:

```python
            emit_progress(progress, {"type": "blob-start", "digest": digest, "url": url, "resume_at": resume_at})
```

After successful verification:

```python
                emit_progress(progress, {"type": "blob-complete", "digest": digest, "path": str(final), "reused": False})
```

On retry:

```python
            emit_progress(progress, {"type": "blob-retry", "digest": digest, "error": str(error), "attempt": attempt + 1})
```

Update `pull_model()` signature:

```python
def pull_model(
    model: str,
    *,
    models_dir: Path,
    registry: str,
    retries: int,
    dry_run: bool,
    progress: ProgressCallback | None = None,
) -> None:
```

Emit before fetching the manifest:

```python
    emit_progress(progress, {"type": "manifest-fetch", "model": model, "url": manifest_url})
```

Pass `progress=progress` into `download_blob()`. Emit after manifest installation:

```python
    emit_progress(progress, {"type": "model-complete", "model": model, "manifest": str(paths.manifest)})
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_ollama_pull -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ollama_pull/core.py tests/test_ollama_pull.py
git commit -m "Add downloader progress events"
```

## Task 2: Implement Single-Worker Queue Manager

**Files:**
- Create: `src/ollama_pull/queue.py`
- Create: `tests/test_queue.py`

- [ ] **Step 1: Write queue tests**

Create `tests/test_queue.py`:

```python
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ollama_pull.queue import DownloadQueue


class DownloadQueueTests(unittest.TestCase):
    def test_add_creates_waiting_item(self):
        queue = DownloadQueue(models_dir=Path("/tmp/models"))

        item = queue.add("qwen3-coder:30b")

        self.assertEqual(item["model"], "qwen3-coder:30b")
        self.assertEqual(item["status"], "waiting")
        self.assertEqual(queue.snapshot()["items"][0]["id"], item["id"])

    def test_worker_runs_one_item_at_a_time(self):
        calls = []

        def fake_pull(model, **kwargs):
            calls.append(model)
            time.sleep(0.01)

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            first = queue.add("first:latest")
            second = queue.add("second:latest")
            queue.start()
            queue.wait_until_idle(timeout=2)

        state = queue.snapshot()
        statuses = {item["id"]: item["status"] for item in state["items"]}
        self.assertEqual(calls, ["first:latest", "second:latest"])
        self.assertEqual(statuses[first["id"]], "completed")
        self.assertEqual(statuses[second["id"]], "completed")

    def test_failure_marks_item_failed_and_retry_resets_to_waiting(self):
        attempts = []

        def fake_pull(model, **kwargs):
            attempts.append(model)
            if len(attempts) == 1:
                raise RuntimeError("network failed")

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            item = queue.add("broken:latest")
            queue.start()
            queue.wait_until_idle(timeout=2)
            self.assertEqual(queue.snapshot()["items"][0]["status"], "failed")

            queue.retry(item["id"])
            queue.start()
            queue.wait_until_idle(timeout=2)

        self.assertEqual(queue.snapshot()["items"][0]["status"], "completed")

    def test_pause_after_current_stops_before_next_item(self):
        def fake_pull(model, **kwargs):
            queue.pause_after_current()

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            queue.add("first:latest")
            queue.add("second:latest")
            queue.start()
            queue.wait_until_idle(timeout=2)

        statuses = [item["status"] for item in queue.snapshot()["items"]]
        self.assertEqual(statuses, ["completed", "waiting"])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_queue -v
```

Expected: import failure because `ollama_pull.queue` does not exist.

- [ ] **Step 3: Implement queue manager**

Create `src/ollama_pull/queue.py`:

```python
from __future__ import annotations

import dataclasses
import itertools
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .core import DEFAULT_REGISTRY, parse_model_ref, pull_model

PullFunc = Callable[..., None]


@dataclasses.dataclass
class QueueItem:
    id: str
    model: str
    status: str = "waiting"
    error: str | None = None
    current_blob: str | None = None
    messages: list[str] = dataclasses.field(default_factory=list)
    created_at: float = dataclasses.field(default_factory=time.time)
    updated_at: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class DownloadQueue:
    def __init__(
        self,
        *,
        models_dir: Path,
        registry: str = DEFAULT_REGISTRY,
        retries: int = 12,
        pull_func: PullFunc = pull_model,
    ) -> None:
        self.models_dir = models_dir
        self.registry = registry
        self.retries = retries
        self.pull_func = pull_func
        self._items: list[QueueItem] = []
        self._counter = itertools.count(1)
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._pause_requested = False

    def add(self, model: str) -> dict[str, Any]:
        parse_model_ref(model)
        with self._lock:
            item = QueueItem(id=str(next(self._counter)), model=model)
            self._items.append(item)
            return item.to_dict()

    def start(self) -> None:
        with self._lock:
            self._pause_requested = False
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def pause_after_current(self) -> None:
        with self._lock:
            self._pause_requested = True

    def retry(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._find(item_id)
            if item.status != "failed":
                raise ValueError("Only failed items can be retried")
            item.status = "waiting"
            item.error = None
            item.updated_at = time.time()
            return item.to_dict()

    def remove(self, item_id: str) -> None:
        with self._lock:
            item = self._find(item_id)
            if item.status == "running":
                raise ValueError("Running items cannot be removed")
            self._items = [candidate for candidate in self._items if candidate.id != item_id]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._worker is not None and self._worker.is_alive(),
                "pause_requested": self._pause_requested,
                "models_dir": str(self.models_dir),
                "registry": self.registry,
                "retries": self.retries,
                "items": [item.to_dict() for item in self._items],
            }

    def wait_until_idle(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            worker = self._worker
            if worker is None or not worker.is_alive():
                return
            time.sleep(0.01)
        raise TimeoutError("Queue worker did not become idle")

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._pause_requested:
                    return
                item = next((candidate for candidate in self._items if candidate.status == "waiting"), None)
                if item is None:
                    return
                item.status = "running"
                item.error = None
                item.updated_at = time.time()
                item.messages.append("Started download")

            try:
                self.pull_func(
                    item.model,
                    models_dir=self.models_dir,
                    registry=self.registry,
                    retries=self.retries,
                    dry_run=False,
                    progress=lambda event, item_id=item.id: self._record_progress(item_id, event),
                )
            except Exception as error:
                with self._lock:
                    item.status = "failed"
                    item.error = str(error)
                    item.updated_at = time.time()
                    item.messages.append(f"Failed: {error}")
            else:
                with self._lock:
                    item.status = "completed"
                    item.updated_at = time.time()
                    item.messages.append("Completed")

    def _record_progress(self, item_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            item = self._find(item_id)
            if "digest" in event:
                item.current_blob = str(event["digest"])
            item.messages.append(str(event.get("type", "progress")))
            item.updated_at = time.time()

    def _find(self, item_id: str) -> QueueItem:
        for item in self._items:
            if item.id == item_id:
                return item
        raise KeyError(item_id)
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_queue -v
```

Expected: all queue tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ollama_pull/queue.py tests/test_queue.py
git commit -m "Add single worker download queue"
```

## Task 3: Add Best-Effort Model Search

**Files:**
- Create: `src/ollama_pull/search.py`
- Create: `tests/test_search.py`

- [ ] **Step 1: Write search tests**

Create `tests/test_search.py`:

```python
import unittest
from unittest import mock

from ollama_pull.search import parse_search_results, search_models


SAMPLE_HTML = """
<a href="/library/qwen3-coder">
  <h2>qwen3-coder</h2>
  <p>Qwen coding model</p>
  <span>30b</span>
  <span>tools</span>
</a>
<a href="/library/deepseek-r1">
  <h2>deepseek-r1</h2>
  <p>Reasoning model</p>
  <span>14b</span>
</a>
"""


class SearchTests(unittest.TestCase):
    def test_parse_search_results_extracts_names_descriptions_and_tags(self):
        results = parse_search_results(SAMPLE_HTML)

        self.assertEqual(results[0]["name"], "qwen3-coder")
        self.assertEqual(results[0]["description"], "Qwen coding model")
        self.assertIn("30b", results[0]["tags"])
        self.assertEqual(results[1]["name"], "deepseek-r1")

    def test_search_models_returns_unavailable_on_network_failure(self):
        with mock.patch("ollama_pull.search.fetch_search_html", side_effect=OSError("offline")):
            payload = search_models("qwen")

        self.assertFalse(payload["available"])
        self.assertEqual(payload["results"], [])
        self.assertIn("offline", payload["error"])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_search -v
```

Expected: import failure because `ollama_pull.search` does not exist.

- [ ] **Step 3: Implement search helper**

Create `src/ollama_pull/search.py`:

```python
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from typing import Any

SEARCH_URL = "https://ollama.com/search"


def fetch_search_html(query: str) -> str:
    url = SEARCH_URL + "?" + urllib.parse.urlencode({"q": query})
    with urllib.request.urlopen(url, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


def strip_tags(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()


def parse_search_results(page: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href="/library/([^"]+)"[^>]*>(.*?)</a>', page, re.DOTALL):
        name = html.unescape(match.group(1)).strip("/")
        body = match.group(2)
        if not name or name in seen:
            continue
        seen.add(name)
        heading = re.search(r"<h[12][^>]*>(.*?)</h[12]>", body, re.DOTALL)
        paragraph = re.search(r"<p[^>]*>(.*?)</p>", body, re.DOTALL)
        spans = re.findall(r"<span[^>]*>(.*?)</span>", body, re.DOTALL)
        display_name = strip_tags(heading.group(1)) if heading else name
        description = strip_tags(paragraph.group(1)) if paragraph else ""
        tags = [strip_tags(span) for span in spans if strip_tags(span)]
        results.append({"name": display_name, "description": description, "tags": tags})
    return results


def search_models(query: str) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {"available": True, "results": [], "error": None}
    try:
        return {"available": True, "results": parse_search_results(fetch_search_html(query)), "error": None}
    except Exception as error:
        return {"available": False, "results": [], "error": str(error)}
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_search -v
```

Expected: all search tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ollama_pull/search.py tests/test_search.py
git commit -m "Add best effort model search"
```

## Task 4: Add Local HTTP Server API

**Files:**
- Create: `src/ollama_pull/server.py`
- Create: `tests/test_server.py`
- Modify: `src/ollama_pull/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write server tests**

Create `tests/test_server.py`:

```python
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from ollama_pull.server import create_server


class ServerTests(unittest.TestCase):
    def test_state_endpoint_returns_queue_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = create_server(("127.0.0.1", 0), models_dir=Path(tmp))
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/state"
                payload = json.loads(urllib.request.urlopen(url, timeout=5).read())
            finally:
                server.server_close()

        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["models_dir"], tmp)

    def test_add_endpoint_queues_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = create_server(("127.0.0.1", 0), models_dir=Path(tmp))
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/api/queue",
                    data=json.dumps({"model": "qwen3-coder:30b"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                payload = json.loads(urllib.request.urlopen(request, timeout=5).read())
            finally:
                server.server_close()

        self.assertEqual(payload["model"], "qwen3-coder:30b")
        self.assertEqual(payload["status"], "waiting")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_server -v
```

Expected: import failure because `ollama_pull.server` does not exist.

- [ ] **Step 3: Implement server API**

Create `src/ollama_pull/server.py`:

```python
from __future__ import annotations

import json
import mimetypes
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .core import DEFAULT_REGISTRY, default_models_dir
from .queue import DownloadQueue
from .search import search_models

WEB_DIR = Path(__file__).with_name("web")


class AppServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], queue: DownloadQueue):
        super().__init__(server_address, handler_class)
        self.queue = queue


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._json(self.server.queue.snapshot())
            return
        if self.path.startswith("/api/search"):
            query = self.path.partition("?")[2].replace("q=", "")
            self._json(search_models(query))
            return
        self._static()

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            if self.path == "/api/queue":
                self._json(self.server.queue.add(str(payload["model"])))
                return
            if self.path == "/api/start":
                self.server.queue.start()
                self._json(self.server.queue.snapshot())
                return
            if self.path == "/api/pause":
                self.server.queue.pause_after_current()
                self._json(self.server.queue.snapshot())
                return
            if self.path.startswith("/api/retry/"):
                self._json(self.server.queue.retry(self.path.rsplit("/", 1)[1]))
                return
            if self.path.startswith("/api/remove/"):
                self.server.queue.remove(self.path.rsplit("/", 1)[1])
                self._json({"ok": True})
                return
            self.send_error(404)
        except Exception as error:
            self._json({"error": str(error)}, status=400)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _static(self) -> None:
        requested = "index.html" if self.path in {"/", ""} else self.path.lstrip("/")
        path = (WEB_DIR / requested).resolve()
        if not str(path).startswith(str(WEB_DIR.resolve())) or not path.exists():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_server(address: tuple[str, int], *, models_dir: Path, registry: str = DEFAULT_REGISTRY, retries: int = 12) -> AppServer:
    return AppServer(address, AppHandler, DownloadQueue(models_dir=models_dir, registry=registry, retries=retries))


def run_web(argv: list[str] | None = None) -> int:
    server = create_server(("127.0.0.1", 0), models_dir=default_models_dir())
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"OllamaPull web app: {url}", flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
```

Update `src/ollama_pull/__init__.py`:

```python
from .core import (
    DEFAULT_REGISTRY,
    ModelPaths,
    ModelRef,
    build_parser,
    default_models_dir,
    digest_filename,
    download_blob,
    fetch_json,
    install_manifest,
    main,
    manifest_digests,
    model_paths,
    parse_model_ref,
    pull_model,
    verify_file,
)
from .server import run_web
```

Update `pyproject.toml`:

```toml
[project.scripts]
ollamapull = "ollama_pull:main"
ollamapull-web = "ollama_pull:run_web"
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_server -v
```

Expected: all server tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ollama_pull/server.py src/ollama_pull/__init__.py pyproject.toml tests/test_server.py
git commit -m "Add local web server API"
```

## Task 5: Build Operator Panel Web UI

**Files:**
- Create: `src/ollama_pull/web/index.html`
- Create: `src/ollama_pull/web/styles.css`
- Create: `src/ollama_pull/web/app.js`

- [ ] **Step 1: Create UI shell**

Create `src/ollama_pull/web/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OllamaPull</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <main class="app">
    <section class="workspace">
      <header class="topbar">
        <div>
          <h1>OllamaPull</h1>
          <p>One active download. Queue the rest.</p>
        </div>
        <div id="target" class="target"></div>
      </header>
      <section class="search-panel">
        <form id="search-form">
          <input id="model-input" placeholder="Search models or paste a ref, e.g. qwen3-coder:30b">
          <button type="submit">Search</button>
          <button id="add-direct" type="button">Add</button>
        </form>
        <div id="search-status" class="muted"></div>
        <div id="search-results" class="results"></div>
      </section>
      <section id="active" class="active-card"></section>
      <section>
        <div class="section-title">Queue</div>
        <div id="queue" class="queue-list"></div>
      </section>
      <footer class="actions">
        <button id="start">Start</button>
        <button id="pause">Pause after current</button>
      </footer>
    </section>
    <aside class="details">
      <h2>Details</h2>
      <div id="details"></div>
    </aside>
  </main>
  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Add responsive styling**

Create `src/ollama_pull/web/styles.css` with the Operator Panel layout. Use a two-column desktop layout, collapse to one column below `900px`, keep cards at `8px` radius or less, and use non-purple blue, green, amber, and neutral tones.

- [ ] **Step 3: Add client behavior**

Create `src/ollama_pull/web/app.js` with:

```javascript
let state = { items: [] };
let selectedId = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.error) throw new Error(payload.error || "Request failed");
  return payload;
}

async function refresh() {
  state = await api("/api/state");
  if (!selectedId && state.items.length) selectedId = state.items[0].id;
  render();
}

function render() {
  document.getElementById("target").textContent = state.models_dir || "";
  renderActive();
  renderQueue();
  renderDetails();
}

function renderActive() {
  const active = state.items.find((item) => item.status === "running");
  const element = document.getElementById("active");
  if (!active) {
    element.innerHTML = '<div class="empty">No active download</div>';
    return;
  }
  element.innerHTML = `<h2>${escapeHtml(active.model)}</h2><p>${escapeHtml(active.current_blob || "Preparing download")}</p><div class="status running">Running</div>`;
}

function renderQueue() {
  const queue = document.getElementById("queue");
  queue.innerHTML = state.items.map((item) => `
    <button class="queue-item ${item.id === selectedId ? "selected" : ""}" data-id="${item.id}">
      <span>${escapeHtml(item.model)}</span>
      <span class="status ${item.status}">${escapeHtml(item.status)}</span>
    </button>
  `).join("");
  queue.querySelectorAll(".queue-item").forEach((button) => {
    button.addEventListener("click", () => {
      selectedId = button.dataset.id;
      render();
    });
  });
}

function renderDetails() {
  const item = state.items.find((candidate) => candidate.id === selectedId);
  const details = document.getElementById("details");
  if (!item) {
    details.innerHTML = '<p class="muted">Select a queue item to see details.</p>';
    return;
  }
  details.innerHTML = `
    <dl>
      <dt>Model</dt><dd>${escapeHtml(item.model)}</dd>
      <dt>Status</dt><dd>${escapeHtml(item.status)}</dd>
      <dt>Registry</dt><dd>${escapeHtml(state.registry)}</dd>
      <dt>Retries</dt><dd>${state.retries}</dd>
      <dt>Current blob</dt><dd>${escapeHtml(item.current_blob || "None")}</dd>
      <dt>Error</dt><dd>${escapeHtml(item.error || "None")}</dd>
    </dl>
    <pre>${escapeHtml((item.messages || []).slice(-8).join("\\n"))}</pre>
  `;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}
```

Wire buttons:

```javascript
document.getElementById("add-direct").addEventListener("click", async () => {
  const model = document.getElementById("model-input").value.trim();
  if (!model) return;
  await api("/api/queue", { method: "POST", body: JSON.stringify({ model }) });
  await refresh();
});

document.getElementById("start").addEventListener("click", async () => {
  await api("/api/start", { method: "POST", body: "{}" });
  await refresh();
});

document.getElementById("pause").addEventListener("click", async () => {
  await api("/api/pause", { method: "POST", body: "{}" });
  await refresh();
});

document.getElementById("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = document.getElementById("model-input").value.trim();
  const payload = await api(`/api/search?q=${encodeURIComponent(query)}`);
  const status = document.getElementById("search-status");
  const results = document.getElementById("search-results");
  status.textContent = payload.available ? "" : `Search unavailable: ${payload.error}`;
  results.innerHTML = payload.results.map((result) => `
    <button class="result" data-model="${escapeHtml(result.name)}">
      <strong>${escapeHtml(result.name)}</strong>
      <span>${escapeHtml(result.description || "No description")}</span>
    </button>
  `).join("");
  results.querySelectorAll(".result").forEach((button) => {
    button.addEventListener("click", async () => {
      await api("/api/queue", { method: "POST", body: JSON.stringify({ model: button.dataset.model }) });
      await refresh();
    });
  });
});

setInterval(refresh, 1000);
refresh();
```

- [ ] **Step 4: Run static server smoke check**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_server -v
```

Expected: server tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/ollama_pull/web/index.html src/ollama_pull/web/styles.css src/ollama_pull/web/app.js
git commit -m "Add operator panel web UI"
```

## Task 6: Wire CLI Launch And Full Verification

**Files:**
- Modify: `src/ollama_pull/__main__.py`
- Modify: `README.md`

- [ ] **Step 1: Add README usage**

Add a "Web UI" section to `README.md`:

```markdown
## Web UI

Launch the local browser UI:

```bash
ollamapull-web
```

The web UI runs on `127.0.0.1`, queues one model download at a time, and preserves the same safety behavior as the CLI downloader. Search is best effort; direct model references such as `qwen3-coder:30b` always remain supported.
```
```

- [ ] **Step 2: Verify package scripts**

Run:

```bash
python3 -m pip install -e .
ollamapull --help
ollamapull-web
```

Expected: CLI help works, and the web command prints a local URL. Stop the web server with `Ctrl-C` after confirming the URL appears.

- [ ] **Step 3: Run full test suite**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m py_compile src/ollama_pull/*.py tests/*.py
```

Expected: all tests pass and compilation succeeds.

- [ ] **Step 4: Manual browser smoke test**

Run:

```bash
ollamapull-web
```

Open the printed URL. Confirm:

- The Operator Panel renders.
- A direct model reference can be added.
- The item appears in the queue.
- The details panel updates when the item is selected.
- Search failure displays a readable unavailable state if offline.

- [ ] **Step 5: Commit**

```bash
git add README.md src/ollama_pull/__main__.py pyproject.toml
git commit -m "Document and verify web UI launch"
```

## Self-Review

- Spec coverage: The plan covers the local web app, one-active-download queue, search with manual fallback, operational detail panel, reuse of core downloader behavior, error states, and tests.
- Placeholder scan: The plan contains no placeholder markers or undefined future work markers.
- Type consistency: Queue item fields are consistently `id`, `model`, `status`, `error`, `current_blob`, `messages`, `created_at`, and `updated_at`; API routes consume and return those fields.
