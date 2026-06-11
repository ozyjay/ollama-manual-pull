import tempfile
import threading
import time
import unittest
import os
from pathlib import Path
from unittest import mock

import ollama_pull.queue as queue_module
from ollama_pull.core import DownloadStoppedAfterBlob
from ollama_pull.queue import DownloadQueue


class DownloadQueueTests(unittest.TestCase):
    def test_add_creates_waiting_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=lambda *args, **kwargs: None)

            item = queue.add("qwen3-coder:30b")

            self.assertEqual(item["model"], "qwen3-coder:30b")
            self.assertEqual(item["status"], "waiting")
            self.assertIsNone(item["error"])
            self.assertIsNone(item["current_blob"])
            self.assertEqual(item["messages"], [])
            self.assertEqual(
                item["progress"],
                {
                    "phase": "waiting",
                    "overall": {"downloaded": 0, "total": None, "percent": None},
                    "current_file": None,
                },
            )
            self.assertIn("id", item)
            self.assertIn("created_at", item)
            self.assertIn("updated_at", item)

    def test_snapshot_includes_installed_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_dir = root / "manifests" / "registry.ollama.ai" / "library" / "qwen3-coder"
            manifest_dir.mkdir(parents=True)
            (manifest_dir / "30b").write_text("{}\n")
            queue = DownloadQueue(models_dir=root, pull_func=lambda *args, **kwargs: None)

            snapshot = queue.snapshot()

        self.assertEqual(
            snapshot["installed_models"],
            [
                {
                    "name": "qwen3-coder:30b",
                    "namespace": "library",
                    "model": "qwen3-coder",
                    "tag": "30b",
                }
            ],
        )

    def test_worker_runs_one_item_at_a_time(self):
        entered = []
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_pull(model, **kwargs):
            nonlocal active, max_active
            progress = kwargs["progress"]
            with lock:
                active += 1
                max_active = max(max_active, active)
                entered.append(model)
            progress({"type": "blob-start", "digest": f"sha256:{model}"})
            time.sleep(0.02)
            with lock:
                active -= 1

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            first = queue.add("first")
            second = queue.add("second")

            queue.start()
            self.assertTrue(queue.wait_until_idle(2))

            snapshot = queue.snapshot()

        self.assertEqual(entered, ["first", "second"])
        self.assertEqual(max_active, 1)
        self.assertEqual([item["status"] for item in snapshot["items"]], ["completed", "completed"])
        self.assertEqual(snapshot["items"][0]["current_blob"], "sha256:first")
        self.assertEqual(snapshot["items"][0]["messages"][0]["text"], "blob-start")
        self.assertIsInstance(snapshot["items"][0]["messages"][0]["timestamp"], float)
        self.assertEqual(first["status"], "waiting")
        self.assertEqual(second["status"], "waiting")

    def test_progress_tracks_model_plan_blob_progress_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=lambda *args, **kwargs: None)
            item = queue.add("qwen3-coder:30b")
            item_id = item["id"]

            queue._record_progress(
                item_id,
                {
                    "type": "model-plan",
                    "total_bytes": 10,
                    "files": [
                        {"digest": "sha256:first", "size": 4},
                        {"digest": "sha256:second", "size": 6},
                    ],
                },
            )
            queue._record_progress(
                item_id,
                {
                    "type": "blob-progress",
                    "digest": "sha256:first",
                    "downloaded": 2,
                    "total": 4,
                    "percent": 50.0,
                    "bytes_per_second": 8.0,
                    "eta_seconds": 1,
                    "line": "50.0% 2B/4B 8B/s eta 0m01s",
                },
            )

            snapshot = queue.snapshot()

            progress = snapshot["items"][0]["progress"]
            self.assertEqual(progress["phase"], "downloading")
            self.assertEqual(
                progress["overall"],
                {
                    "downloaded": 2,
                    "total": 10,
                    "percent": 20.0,
                    "bytes_per_second": 8.0,
                    "eta_seconds": 1,
                },
            )
            self.assertEqual(
                progress["current_file"],
                {
                    "digest": "sha256:first",
                    "index": 1,
                    "total_files": 2,
                    "downloaded": 2,
                    "total": 4,
                    "percent": 50.0,
                    "bytes_per_second": 8.0,
                    "eta_seconds": 1,
                    "line": "50.0% 2B/4B 8B/s eta 0m01s",
                },
            )

            queue._record_progress(
                item_id,
                {
                    "type": "blob-complete",
                    "digest": "sha256:first",
                    "downloaded": 4,
                    "total": 4,
                    "percent": 100.0,
                },
            )
            queue._record_progress(
                item_id,
                {
                    "type": "model-complete",
                    "model": "qwen3-coder:30b",
                },
            )

            snapshot = queue.snapshot()

        progress = snapshot["items"][0]["progress"]
        self.assertEqual(progress["phase"], "completed")
        self.assertEqual(progress["overall"], {"downloaded": 10, "total": 10, "percent": 100.0})
        self.assertEqual(progress["current_file"]["percent"], 100.0)

    def test_progress_handles_retry_and_unknown_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=lambda *args, **kwargs: None)
            item = queue.add("qwen3-coder:30b")
            item_id = item["id"]

            queue._record_progress(item_id, {"type": "blob-start", "digest": "sha256:unknown"})
            queue._record_progress(
                item_id,
                {
                    "type": "blob-progress",
                    "digest": "sha256:unknown",
                    "downloaded": 12,
                    "total": None,
                    "percent": None,
                    "bytes_per_second": 3.0,
                    "eta_seconds": None,
                    "line": "12B 3B/s",
                },
            )
            queue._record_progress(
                item_id,
                {
                    "type": "blob-retry",
                    "digest": "sha256:unknown",
                    "error": "temporary network issue",
                    "attempt": 1,
                },
            )

            snapshot = queue.snapshot()

        progress = snapshot["items"][0]["progress"]
        self.assertEqual(progress["phase"], "retrying")
        self.assertEqual(
            progress["overall"],
            {
                "downloaded": 12,
                "total": None,
                "percent": None,
                "bytes_per_second": 3.0,
                "eta_seconds": None,
            },
        )
        self.assertEqual(progress["current_file"]["digest"], "sha256:unknown")
        self.assertIsNone(progress["current_file"]["total"])
        self.assertIsNone(progress["current_file"]["percent"])

    def test_concurrent_start_calls_reserve_single_worker_slot(self):
        active = 0
        max_active = 0
        lock = threading.Lock()
        first_started = threading.Event()
        release_downloads = threading.Event()

        def fake_pull(model, **kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            first_started.set()
            release_downloads.wait(2)
            with lock:
                active -= 1

        original_thread = queue_module.threading.Thread
        original_current_thread = queue_module.threading.current_thread
        wrappers_by_thread = {}

        class SlowVisibleThread:
            def __init__(self, target, daemon):
                self._target = target
                self._visible = threading.Event()
                self._thread = original_thread(target=self._run, daemon=daemon)
                wrappers_by_thread[self._thread] = self

            def start(self):
                self._thread.start()

            def is_alive(self):
                return self._visible.is_set() and self._thread.is_alive()

            def _run(self):
                time.sleep(0.05)
                self._visible.set()
                self._target()

        def fake_current_thread():
            current = original_current_thread()
            return wrappers_by_thread.get(current, current)

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            queue.add("first")
            queue.add("second")

            with (
                mock.patch.object(queue_module.threading, "Thread", SlowVisibleThread),
                mock.patch.object(queue_module.threading, "current_thread", fake_current_thread),
            ):
                starters = [original_thread(target=queue.start) for _ in range(8)]
                for starter in starters:
                    starter.start()
                for starter in starters:
                    starter.join()

                self.assertTrue(first_started.wait(2))
                release_downloads.set()
                self.assertTrue(queue.wait_until_idle(2))

        self.assertEqual(max_active, 1)

    def test_start_failure_clears_reserved_worker_slot(self):
        class FailingThread:
            def __init__(self, target, daemon):
                self._target = target
                self.daemon = daemon

            def start(self):
                raise RuntimeError("thread failed to start")

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=lambda *args, **kwargs: None)
            queue.add("first")

            with mock.patch.object(queue_module.threading, "Thread", FailingThread):
                with self.assertRaisesRegex(RuntimeError, "thread failed to start"):
                    queue.start()

            self.assertFalse(queue.snapshot()["running"])
            self.assertTrue(queue.wait_until_idle(0.1))

    def test_add_and_start_during_worker_shutdown_gap_is_not_lost(self):
        calls = []
        shutdown_gap_open = threading.Event()
        release_shutdown = threading.Event()

        def fake_pull(model, **kwargs):
            calls.append(model)

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            original_condition = queue._condition
            original_next_waiting_item = queue._next_waiting_item

            class PausingCondition:
                def __init__(self, condition):
                    self._condition = condition
                    self.pause_on_worker_exit = False
                    self.worker_thread = None

                def __enter__(self):
                    return self._condition.__enter__()

                def __exit__(self, exc_type, exc_value, traceback):
                    result = self._condition.__exit__(exc_type, exc_value, traceback)
                    if (
                        self.pause_on_worker_exit
                        and threading.current_thread() is self.worker_thread
                    ):
                        self.pause_on_worker_exit = False
                        shutdown_gap_open.set()
                        release_shutdown.wait(2)
                    return result

                def notify_all(self):
                    self._condition.notify_all()

                def wait(self, timeout=None):
                    return self._condition.wait(timeout)

            pausing_condition = PausingCondition(original_condition)

            def next_waiting_item_with_shutdown_pause():
                item = original_next_waiting_item()
                if item is None:
                    pausing_condition.pause_on_worker_exit = True
                    pausing_condition.worker_thread = threading.current_thread()
                return item

            queue._condition = pausing_condition
            queue._next_waiting_item = next_waiting_item_with_shutdown_pause
            queue.add("first")

            queue.start()
            self.assertTrue(shutdown_gap_open.wait(2))
            queue.add("second")
            queue.start()
            release_shutdown.set()
            self.assertTrue(queue.wait_until_idle(2))

        self.assertEqual(calls, ["first", "second"])

    def test_failure_marks_item_failed_and_retry_resets_to_waiting(self):
        attempts = []

        def fake_pull(model, **kwargs):
            attempts.append(model)
            raise RuntimeError("download broke")

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            item = queue.add("broken")

            queue.start()
            self.assertTrue(queue.wait_until_idle(2))
            failed = queue.snapshot()["items"][0]

            retried = queue.retry(item["id"])

        self.assertEqual(attempts, ["broken"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "download broke")
        self.assertEqual(failed["messages"][0]["text"], "failed: download broke")
        self.assertIsInstance(failed["messages"][0]["timestamp"], float)
        self.assertEqual(retried["status"], "waiting")
        self.assertIsNone(retried["error"])

    def test_worker_failures_are_written_to_log_file(self):
        def fake_pull(model, **kwargs):
            raise RuntimeError("HTTP 400 Bad Request: invalid range")

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "app.log"
            with mock.patch.dict(os.environ, {"OLLAMA_MANUAL_PULL_LOG_FILE": str(log_path)}):
                queue = DownloadQueue(models_dir=Path(tmp) / "models", pull_func=fake_pull)
                queue.add("qwen3-coder:30b")

                queue.start()
                self.assertTrue(queue.wait_until_idle(2))

            log_text = log_path.read_text()

        self.assertIn("qwen3-coder:30b", log_text)
        self.assertIn("HTTP 400 Bad Request: invalid range", log_text)
        self.assertIn("download failed", log_text)

    def test_add_returns_existing_waiting_item_for_duplicate_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=lambda *args, **kwargs: None)

            first = queue.add("qwen3-coder:30b")
            second = queue.add("qwen3-coder:30b")
            snapshot = queue.snapshot()

        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(len(snapshot["items"]), 1)

    def test_add_deduplicates_implicit_latest_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=lambda *args, **kwargs: None)

            first = queue.add("qwen3-coder")
            second = queue.add("qwen3-coder:latest")
            snapshot = queue.snapshot()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["canonical_model"], "qwen3-coder:latest")
        self.assertTrue(second["deduplicated"])
        self.assertEqual(len(snapshot["items"]), 1)

    def test_add_returns_failed_duplicate_without_adding_row(self):
        def fake_pull(model, **kwargs):
            raise RuntimeError("download broke")

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            first = queue.add("broken:latest")
            queue.start()
            self.assertTrue(queue.wait_until_idle(2))

            second = queue.add("broken")
            snapshot = queue.snapshot()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["status"], "failed")
        self.assertTrue(second["deduplicated"])
        self.assertEqual(len(snapshot["items"]), 1)

    def test_pause_after_current_stops_before_next_item(self):
        first_started = threading.Event()
        release_first = threading.Event()
        calls = []

        def fake_pull(model, **kwargs):
            calls.append(model)
            if model == "first":
                first_started.set()
                release_first.wait(2)

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            queue.add("first")
            queue.add("second")

            queue.start()
            self.assertTrue(first_started.wait(2))
            queue.pause_after_current()
            release_first.set()
            self.assertTrue(queue.wait_until_idle(2))
            snapshot = queue.snapshot()

        self.assertEqual(calls, ["first"])
        self.assertTrue(snapshot["pause_requested"])
        self.assertEqual([item["status"] for item in snapshot["items"]], ["completed", "waiting"])

    def test_stop_after_current_blob_returns_running_item_to_waiting(self):
        first_blob_complete = threading.Event()
        stop_requested = threading.Event()
        downloaded = []

        def fake_pull(model, **kwargs):
            progress = kwargs["progress"]
            progress({"type": "blob-start", "digest": "sha256:first"})
            progress({"type": "blob-complete", "digest": "sha256:first", "downloaded": 4, "total": 4})
            downloaded.append("first")
            first_blob_complete.set()
            self.assertTrue(stop_requested.wait(2))
            if kwargs["stop_after_blob"]():
                raise DownloadStoppedAfterBlob
            progress({"type": "blob-start", "digest": "sha256:second"})
            downloaded.append("second")

        with tempfile.TemporaryDirectory() as tmp:
            queue = DownloadQueue(models_dir=Path(tmp), pull_func=fake_pull)
            item = queue.add("qwen3-coder:30b")

            queue.start()
            self.assertTrue(first_blob_complete.wait(2))
            snapshot = queue.stop_after_current_blob()
            stop_requested.set()
            self.assertTrue(queue.wait_until_idle(2))
            final = queue.snapshot()

        self.assertTrue(snapshot["stop_after_blob_requested"])
        self.assertEqual(downloaded, ["first"])
        self.assertFalse(final["running"])
        self.assertTrue(final["pause_requested"])
        self.assertFalse(final["stop_after_blob_requested"])
        self.assertEqual(final["items"][0]["id"], item["id"])
        self.assertEqual(final["items"][0]["status"], "waiting")
        self.assertEqual(final["items"][0]["current_blob"], "sha256:first")
        self.assertEqual(final["items"][0]["messages"][-1]["text"], "stopped after current blob")


if __name__ == "__main__":
    unittest.main()
