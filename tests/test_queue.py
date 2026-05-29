import tempfile
import threading
import time
import unittest
from pathlib import Path

from ollama_manual_pull.queue import DownloadQueue


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
            self.assertIn("id", item)
            self.assertIn("created_at", item)
            self.assertIn("updated_at", item)

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
        self.assertIn("blob-start", snapshot["items"][0]["messages"])
        self.assertEqual(first["status"], "waiting")
        self.assertEqual(second["status"], "waiting")

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
        self.assertEqual(retried["status"], "waiting")
        self.assertIsNone(retried["error"])

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


if __name__ == "__main__":
    unittest.main()
