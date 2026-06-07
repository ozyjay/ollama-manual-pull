from __future__ import annotations

import argparse
import json
import mimetypes
import posixpath
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .core import DEFAULT_REGISTRY, cleanup_orphan_blobs, default_models_dir
from .queue import DownloadQueue
from .search import search_models


WEB_DIR = Path(__file__).with_name("web")


class AppServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        queue: DownloadQueue,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.queue = queue


class AppRequestHandler(BaseHTTPRequestHandler):
    server: AppServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/state":
            self._send_json(self.server.queue.snapshot())
            return
        if path == "/api/search":
            query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
            self._send_json(search_models(query))
            return
        if path.startswith("/api/"):
            self._send_api_error("Unknown API route", status=404)
            return
        self._serve_static(path)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        try:
            if path == "/api/queue":
                payload = self._read_json_body()
                model = payload.get("model")
                if not isinstance(model, str) or not model.strip():
                    raise ValueError("Expected non-empty model")
                self._send_json(self.server.queue.add(model))
                return
            if path == "/api/start":
                self.server.queue.start()
                self._send_json(self.server.queue.snapshot())
                return
            if path == "/api/pause":
                self.server.queue.pause_after_current()
                self._send_json(self.server.queue.snapshot())
                return
            if path == "/api/stop-after-blob":
                self._send_json(self.server.queue.stop_after_current_blob())
                return
            if path == "/api/installed/remove":
                payload = self._read_json_body()
                model = payload.get("model")
                if not isinstance(model, str) or not model.strip():
                    raise ValueError("Expected non-empty model")
                self.server.queue.delete_installed_model(model)
                self._send_json({"ok": True})
                return
            if path == "/api/cleanup/scan":
                payload = self._read_optional_json_body()
                include_partials, older_than_days = self._cleanup_options(payload)
                self._send_json(
                    cleanup_orphan_blobs(
                        self.server.queue.models_dir,
                        include_partials=include_partials,
                        older_than_days=older_than_days,
                    )
                )
                return
            if path == "/api/cleanup/delete":
                payload = self._read_optional_json_body()
                include_partials, older_than_days = self._cleanup_options(payload)
                self._send_json(
                    cleanup_orphan_blobs(
                        self.server.queue.models_dir,
                        delete=True,
                        include_partials=include_partials,
                        older_than_days=older_than_days,
                    )
                )
                return
            if path.startswith("/api/retry/"):
                item_id = unquote(path.removeprefix("/api/retry/"))
                self._send_json(self.server.queue.retry(item_id))
                return
            if path.startswith("/api/remove/"):
                item_id = unquote(path.removeprefix("/api/remove/"))
                self.server.queue.remove(item_id)
                self._send_json({"ok": True})
                return
            if path.startswith("/api/"):
                self._send_api_error("Unknown API route", status=404)
                return
        except KeyError as error:
            self._send_api_error(f"Not found: {error.args[0]}", status=404)
            return
        except (json.JSONDecodeError, ValueError) as error:
            self._send_api_error(str(error), status=400)
            return

        self.send_error(404)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if not body:
            raise ValueError("Expected JSON body")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object")
        return payload

    def _read_optional_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        body = self.rfile.read(length)
        if not body:
            return {}
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected JSON object")
        return payload

    def _cleanup_options(self, payload: dict[str, Any]) -> tuple[bool, int]:
        include_partials = payload.get("include_partials", False)
        older_than_days = payload.get("older_than_days", 7)
        if not isinstance(include_partials, bool):
            raise ValueError("Expected include_partials to be a boolean")
        if not isinstance(older_than_days, int) or older_than_days < 0:
            raise ValueError("Expected older_than_days to be a non-negative integer")
        return include_partials, older_than_days

    def _send_json(self, payload: Any, *, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_api_error(self, message: str, *, status: int) -> None:
        self._send_json({"error": message}, status=status)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path == "/" else unquote(path).lstrip("/")
        normalized = posixpath.normpath(relative)
        if normalized == "." or normalized.startswith("../") or normalized == "..":
            self.send_error(404)
            return

        root = WEB_DIR.resolve()
        candidate = (WEB_DIR / normalized).resolve()
        if root not in (candidate, *candidate.parents):
            self.send_error(404)
            return
        if not candidate.is_file():
            self.send_error(404)
            return

        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_server(
    address: tuple[str, int],
    *,
    models_dir: Path,
    registry: str = DEFAULT_REGISTRY,
    retries: int = 12,
) -> AppServer:
    queue = DownloadQueue(models_dir=models_dir, registry=registry, retries=retries)
    return AppServer(address, AppRequestHandler, queue=queue)


def run_web(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Ollama Manual Pull web app.")
    parser.add_argument("--models-dir", type=Path, default=default_models_dir())
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--retries", type=int, default=12)
    args = parser.parse_args(argv)

    httpd = create_server(
        ("127.0.0.1", 0),
        models_dir=args.models_dir.expanduser(),
        registry=args.registry,
        retries=args.retries,
    )
    host, port = httpd.server_address
    url = f"http://{host}:{port}/"
    print(f"Ollama Manual Pull web app: {url}", flush=True)
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        httpd.server_close()
    return 0
