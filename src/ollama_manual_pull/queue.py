from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .core import DEFAULT_REGISTRY, parse_model_ref, pull_model


PullFunc = Callable[..., None]


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
        parse_model_ref(model)
        now = time.time()
        item = {
            "id": str(next(self._ids)),
            "model": model,
            "status": "waiting",
            "error": None,
            "current_blob": None,
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        with self._condition:
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
        worker.start()

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

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "running": self._worker is not None
                or any(item["status"] == "running" for item in self._items),
                "pause_requested": self._pause_requested,
                "models_dir": str(self.models_dir),
                "registry": self.registry,
                "retries": self.retries,
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
                        return
                    item = self._next_waiting_item()
                    if item is None:
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
                    with self._condition:
                        item["status"] = "failed"
                        item["error"] = str(error)
                        item["updated_at"] = time.time()
                        self._condition.notify_all()
                else:
                    with self._condition:
                        item["status"] = "completed"
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
                item["messages"].append(str(event_type))
            digest = event.get("digest")
            if digest is not None:
                item["current_blob"] = str(digest)
            item["updated_at"] = time.time()
            self._condition.notify_all()

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
        copied = dict(item)
        copied["messages"] = list(item["messages"])
        return copied
