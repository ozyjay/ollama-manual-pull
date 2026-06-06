from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .app_logging import write_log
from .core import DEFAULT_REGISTRY, delete_installed_model, installed_models, parse_model_ref, pull_model


PullFunc = Callable[..., None]


def canonical_model_ref(model: str) -> str:
    ref = parse_model_ref(model)
    name = f"{ref.name}:{ref.tag}"
    return name if ref.namespace == "library" else f"{ref.namespace}/{name}"


def _empty_progress() -> dict[str, Any]:
    return {
        "phase": "waiting",
        "overall": {"downloaded": 0, "total": None, "percent": None},
        "current_file": None,
    }


class DownloadQueue:
    def __init__(
        self,
        *,
        models_dir: Path,
        registry: str = DEFAULT_REGISTRY,
        retries: int = 12,
        pull_func: PullFunc = pull_model,
    ) -> None:
        self.models_dir = Path(models_dir)
        self.registry = registry
        self.retries = retries
        self.pull_func = pull_func
        self._items: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self._condition = threading.Condition()
        self._worker: threading.Thread | None = None
        self._pause_requested = False

    def add(self, model: str) -> dict[str, Any]:
        canonical = canonical_model_ref(model)
        now = time.time()
        with self._condition:
            for existing in self._items:
                if existing.get("canonical_model") != canonical:
                    continue
                if existing["status"] in {"waiting", "running", "completed", "failed"}:
                    copied = self._copy_item(existing)
                    copied["deduplicated"] = True
                    return copied
            item = {
                "id": str(next(self._ids)),
                "model": model,
                "canonical_model": canonical,
                "deduplicated": False,
                "status": "waiting",
                "error": None,
                "current_blob": None,
                "messages": [],
                "progress": _empty_progress(),
                "_planned_files": {},
                "_completed_files": {},
                "created_at": now,
                "updated_at": now,
            }
            self._items.append(item)
            self._condition.notify_all()
            return self._copy_item(item)

    def start(self) -> None:
        with self._condition:
            if self._worker is not None:
                return
            self._pause_requested = False
            worker = threading.Thread(target=self._run_worker, daemon=True)
            self._worker = worker
        try:
            worker.start()
        except Exception:
            with self._condition:
                if self._worker is worker:
                    self._worker = None
                self._condition.notify_all()
            raise

    def pause_after_current(self) -> None:
        with self._condition:
            self._pause_requested = True
            self._condition.notify_all()

    def retry(self, item_id: str) -> dict[str, Any]:
        with self._condition:
            item = self._find_item(item_id)
            if item["status"] != "failed":
                raise ValueError("Only failed items can be retried")
            item["status"] = "waiting"
            item["error"] = None
            item["current_blob"] = None
            item["progress"] = _empty_progress()
            item["_planned_files"] = {}
            item["_completed_files"] = {}
            item["updated_at"] = time.time()
            self._condition.notify_all()
            return self._copy_item(item)

    def remove(self, item_id: str) -> dict[str, Any]:
        with self._condition:
            for index, item in enumerate(self._items):
                if item["id"] != item_id:
                    continue
                if item["status"] == "running":
                    raise ValueError("Running items cannot be removed")
                removed = self._items.pop(index)
                self._condition.notify_all()
                return self._copy_item(removed)
        raise KeyError(item_id)

    def delete_installed_model(self, model: str) -> None:
        delete_installed_model(self.models_dir, model)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "running": self._worker is not None
                or any(item["status"] == "running" for item in self._items),
                "pause_requested": self._pause_requested,
                "models_dir": str(self.models_dir),
                "registry": self.registry,
                "retries": self.retries,
                "installed_models": installed_models(self.models_dir),
                "items": [self._copy_item(item) for item in self._items],
            }

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                worker_reserved = self._worker is not None
                running_item = any(item["status"] == "running" for item in self._items)
                if not worker_reserved and not running_item:
                    return True
                if deadline is None:
                    remaining = None
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                self._condition.wait(remaining)

    def _run_worker(self) -> None:
        try:
            while True:
                with self._condition:
                    if self._pause_requested:
                        self._clear_worker_locked()
                        return
                    item = self._next_waiting_item()
                    if item is None:
                        self._clear_worker_locked()
                        return
                    item["status"] = "running"
                    item["updated_at"] = time.time()
                    self._condition.notify_all()

                try:
                    self.pull_func(
                        item["model"],
                        models_dir=self.models_dir,
                        registry=self.registry,
                        retries=self.retries,
                        dry_run=False,
                        progress=lambda event, item_id=item["id"]: self._record_progress(item_id, event),
                    )
                except Exception as error:
                    write_log("download failed", model=item["model"], error=error)
                    with self._condition:
                        item["status"] = "failed"
                        item["error"] = str(error)
                        self._append_message_locked(item, f"failed: {error}")
                        item["progress"]["phase"] = "failed"
                        item["updated_at"] = time.time()
                        self._condition.notify_all()
                else:
                    with self._condition:
                        item["status"] = "completed"
                        self._complete_progress_locked(item)
                        item["updated_at"] = time.time()
                        self._condition.notify_all()
        finally:
            with self._condition:
                self._clear_worker_locked()
                self._condition.notify_all()

    def _record_progress(self, item_id: str, event: dict[str, Any]) -> None:
        with self._condition:
            item = self._find_item(item_id)
            event_type = event.get("type")
            if event_type is not None:
                self._append_message_locked(item, str(event_type))
                self._update_progress_locked(item, event)
            digest = event.get("digest")
            if digest is not None:
                item["current_blob"] = str(digest)
            item["updated_at"] = time.time()
            self._condition.notify_all()

    def _update_progress_locked(self, item: dict[str, Any], event: dict[str, Any]) -> None:
        event_type = event.get("type")
        progress = item["progress"]
        if event_type == "manifest-fetch":
            progress["phase"] = "fetching"
            return
        if event_type == "model-plan":
            planned = {
                str(file["digest"]): file.get("size")
                for file in event.get("files", [])
                if file.get("digest") is not None
            }
            item["_planned_files"] = planned
            progress["overall"] = {
                "downloaded": 0,
                "total": event.get("total_bytes"),
                "percent": None,
            }
            return
        if event_type == "blob-start":
            progress["phase"] = "downloading"
            progress["current_file"] = {
                "digest": event.get("digest"),
                "downloaded": event.get("resume_at", 0),
                "total": item["_planned_files"].get(event.get("digest")),
                "percent": None,
                "bytes_per_second": None,
                "eta_seconds": None,
                "line": None,
            }
            self._refresh_overall_locked(item)
            return
        if event_type == "blob-progress":
            progress["phase"] = "downloading"
            progress["current_file"] = self._file_progress_from_event(event)
            self._refresh_overall_locked(item)
            return
        if event_type == "blob-complete":
            digest = event.get("digest")
            if digest is not None:
                item["_completed_files"][str(digest)] = event.get("total") or event.get("downloaded")
            progress["current_file"] = self._file_progress_from_event(event)
            self._refresh_overall_locked(item)
            return
        if event_type == "blob-retry":
            progress["phase"] = "retrying"
            if progress["current_file"] is None:
                progress["current_file"] = {"digest": event.get("digest")}
            return
        if event_type == "model-complete":
            self._complete_progress_locked(item)

    def _file_progress_from_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "digest": event.get("digest"),
            "downloaded": event.get("downloaded"),
            "total": event.get("total"),
            "percent": event.get("percent"),
            "bytes_per_second": event.get("bytes_per_second"),
            "eta_seconds": event.get("eta_seconds"),
            "line": event.get("line"),
        }

    def _append_message_locked(self, item: dict[str, Any], text: str) -> None:
        item["messages"].append({"timestamp": time.time(), "text": text})

    def _refresh_overall_locked(self, item: dict[str, Any]) -> None:
        progress = item["progress"]
        current = progress.get("current_file") or {}
        completed = sum(
            size for size in item["_completed_files"].values() if isinstance(size, int)
        )
        current_digest = current.get("digest")
        current_downloaded = current.get("downloaded")
        if current_digest in item["_completed_files"]:
            current_downloaded = 0
        elif not isinstance(current_downloaded, int):
            current_downloaded = 0
        downloaded = completed + current_downloaded
        total = progress["overall"].get("total")
        percent = downloaded / total * 100 if total and total > 0 else None
        progress["overall"] = {
            "downloaded": downloaded,
            "total": total,
            "percent": percent,
        }

    def _complete_progress_locked(self, item: dict[str, Any]) -> None:
        progress = item["progress"]
        total = progress["overall"].get("total")
        if total and total > 0:
            progress["overall"] = {"downloaded": total, "total": total, "percent": 100.0}
        progress["phase"] = "completed"

    def _next_waiting_item(self) -> dict[str, Any] | None:
        for item in self._items:
            if item["status"] == "waiting":
                return item
        return None

    def _find_item(self, item_id: str) -> dict[str, Any]:
        for item in self._items:
            if item["id"] == item_id:
                return item
        raise KeyError(item_id)

    def _clear_worker_locked(self) -> None:
        if self._worker is threading.current_thread():
            self._worker = None
        self._condition.notify_all()

    def _copy_item(self, item: dict[str, Any]) -> dict[str, Any]:
        copied = {key: value for key, value in item.items() if not key.startswith("_")}
        copied["messages"] = list(item["messages"])
        copied["progress"] = {
            "phase": item["progress"]["phase"],
            "overall": dict(item["progress"]["overall"]),
            "current_file": dict(item["progress"]["current_file"])
            if item["progress"]["current_file"] is not None
            else None,
        }
        return copied
