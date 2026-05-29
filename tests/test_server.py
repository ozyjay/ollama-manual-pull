import json
import tempfile
import threading
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

    def test_path_traversal_and_static_missing_return_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = self.start_server(tmp)

            traversal_status = self.request_error_status(f"{base_url}/%2e%2e/pyproject.toml")
            missing_status = self.request_error_status(f"{base_url}/missing.js")

        self.assertEqual(traversal_status, 404)
        self.assertEqual(missing_status, 404)


if __name__ == "__main__":
    unittest.main()
