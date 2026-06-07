import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from ollama_manual_pull import server as server_module


class ServerTests(unittest.TestCase):
    def start_server(self, tmp):
        httpd = server_module.create_server(("127.0.0.1", 0), models_dir=Path(tmp))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        host, port = httpd.server_address
        return f"http://{host}:{port}"

    def request_json(self, url, *, method="GET", body=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def request_error_json(self, url, *, method="GET", body=None):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request_json(url, method=method, body=body)
        error = raised.exception
        return error.code, json.loads(error.read().decode("utf-8"))

    def request_error_status(self, url):
        request = urllib.request.Request(url)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        return raised.exception.code

    def test_state_endpoint_returns_queue_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = self.start_server(tmp)

            status, payload = self.request_json(f"{base_url}/api/state")

        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["models_dir"], tmp)

    def test_add_endpoint_queues_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = self.start_server(tmp)

            status, payload = self.request_json(
                f"{base_url}/api/queue",
                method="POST",
                body={"model": "qwen3-coder:30b"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload["model"], "qwen3-coder:30b")
        self.assertEqual(payload["status"], "waiting")

    def test_queue_route_reports_deduplicated_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = self.start_server(tmp)

            first_status, first = self.request_json(
                f"{base_url}/api/queue",
                method="POST",
                body={"model": "qwen3-coder"},
            )
            second_status, second = self.request_json(
                f"{base_url}/api/queue",
                method="POST",
                body={"model": "qwen3-coder:latest"},
            )
            state_status, state = self.request_json(f"{base_url}/api/state")

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(state_status, 200)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(second["canonical_model"], "qwen3-coder:latest")
        self.assertEqual(len(state["items"]), 1)

    def test_installed_remove_endpoint_deletes_model_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = (
                Path(tmp)
                / "manifests"
                / "registry.ollama.ai"
                / "library"
                / "qwen3-coder"
                / "30b"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"config":{"digest":"sha256:' + "a" * 64 + '"},"layers":[]}\n')
            blob = Path(tmp) / "blobs" / ("sha256-" + "a" * 64)
            blob.parent.mkdir()
            blob.write_text("blob stays")
            base_url = self.start_server(tmp)

            remove_status, remove_payload = self.request_json(
                f"{base_url}/api/installed/remove",
                method="POST",
                body={"model": "qwen3-coder:30b"},
            )
            state_status, state = self.request_json(f"{base_url}/api/state")
            manifest_exists = manifest.exists()
            blob_exists = blob.exists()

        self.assertEqual(remove_status, 200)
        self.assertEqual(remove_payload, {"ok": True})
        self.assertEqual(state_status, 200)
        self.assertFalse(manifest_exists)
        self.assertTrue(blob_exists)
        self.assertEqual(state["installed_models"], [])

    def test_cleanup_scan_reports_orphans_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orphan = self.write_blob(root, "sha256:" + "b" * 64, b"orphan")
            base_url = self.start_server(tmp)

            status, payload = self.request_json(f"{base_url}/api/cleanup/scan", method="POST", body={})
            orphan_exists = orphan.exists()

        self.assertEqual(status, 200)
        self.assertTrue(orphan_exists)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["orphan_blob_count"], 1)
        self.assertEqual(payload["deleted"], [])

    def test_cleanup_delete_recomputes_and_removes_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orphan = self.write_blob(root, "sha256:" + "b" * 64, b"orphan")
            base_url = self.start_server(tmp)

            status, payload = self.request_json(
                f"{base_url}/api/cleanup/delete",
                method="POST",
                body={"include_partials": False, "older_than_days": 7},
            )

        self.assertEqual(status, 200)
        self.assertFalse(orphan.exists())
        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["deleted"], [str(orphan)])

    def test_cleanup_delete_respects_partial_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_partial = self.write_blob(root, "sha256:" + "c" * 64, b"old", suffix=".manual-download")
            fresh_partial = self.write_blob(root, "sha256:" + "d" * 64, b"fresh", suffix=".manual-download")
            old_time = time.time() - (8 * 24 * 60 * 60)
            os.utime(old_partial, (old_time, old_time))
            base_url = self.start_server(tmp)

            status, payload = self.request_json(
                f"{base_url}/api/cleanup/delete",
                method="POST",
                body={"include_partials": True, "older_than_days": 7},
            )
            old_partial_exists = old_partial.exists()
            fresh_partial_exists = fresh_partial.exists()

        self.assertEqual(status, 200)
        self.assertFalse(old_partial_exists)
        self.assertTrue(fresh_partial_exists)
        self.assertEqual(payload["stale_partial_count"], 1)
        self.assertEqual(payload["deleted"], [str(old_partial)])

    def write_blob(self, root: Path, digest: str, content: bytes, *, suffix: str = "") -> Path:
        blob = root / "blobs" / (digest.replace(":", "-") + suffix)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
        return blob

    def test_search_endpoint_url_decodes_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = self.start_server(tmp)

            with mock.patch.object(
                server_module,
                "search_models",
                return_value={"available": True, "results": [{"name": "qwen"}], "error": None},
            ) as search_models:
                status, payload = self.request_json(f"{base_url}/api/search?q=qwen%20coder")

        self.assertEqual(status, 200)
        self.assertEqual(payload["results"], [{"name": "qwen"}])
        search_models.assert_called_once_with("qwen coder")

    def test_bad_queue_body_returns_json_error_status_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = self.start_server(tmp)

            status, payload = self.request_error_json(
                f"{base_url}/api/queue",
                method="POST",
                body={"model": ""},
            )

        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_stop_after_blob_endpoint_sets_queue_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = self.start_server(tmp)

            status, payload = self.request_json(
                f"{base_url}/api/stop-after-blob",
                method="POST",
            )

        self.assertEqual(status, 200)
        self.assertTrue(payload["pause_requested"])
        self.assertTrue(payload["stop_after_blob_requested"])

    def test_path_traversal_and_static_missing_return_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = self.start_server(tmp)

            traversal_status = self.request_error_status(f"{base_url}/%2e%2e/pyproject.toml")
            missing_status = self.request_error_status(f"{base_url}/missing.js")

        self.assertEqual(traversal_status, 404)
        self.assertEqual(missing_status, 404)

    def test_run_web_expands_models_dir(self):
        created = []

        class FakeServer:
            server_address = ("127.0.0.1", 12345)

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        def fake_create_server(address, *, models_dir, registry, retries):
            created.append(models_dir)
            return FakeServer()

        with mock.patch.object(server_module, "create_server", side_effect=fake_create_server):
            with mock.patch.object(server_module.webbrowser, "open"):
                result = server_module.run_web(["--models-dir", "~/ollama-test-models"])

        self.assertEqual(result, 0)
        self.assertEqual(created, [Path("~/ollama-test-models").expanduser()])

    def test_package_data_includes_web_assets(self):
        project_root = Path(__file__).resolve().parents[1]
        pyproject = project_root / "pyproject.toml"
        content = pyproject.read_text()

        self.assertIn("[tool.setuptools.package-data]", content)
        self.assertIn('ollama_manual_pull = ["web/*"]', content)
        for asset in ["index.html", "styles.css", "app.js"]:
            self.assertTrue((project_root / "src" / "ollama_manual_pull" / "web" / asset).is_file())

    def test_web_progress_summary_uses_overall_speed_and_eta(self):
        app_js = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "ollama_manual_pull"
            / "web"
            / "app.js"
        ).read_text()

        self.assertIn("overall.bytes_per_second", app_js)
        self.assertIn("overall.eta_seconds", app_js)
        self.assertIn("blobPositionText(currentFile)", app_js)


if __name__ == "__main__":
    unittest.main()
