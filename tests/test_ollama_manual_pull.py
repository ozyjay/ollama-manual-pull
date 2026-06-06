import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import ollama_manual_pull as omp
import ollama_manual_pull.core as core


class OllamaManualPullTests(unittest.TestCase):
    def test_parse_model_ref_defaults_latest_tag_and_library_namespace(self):
        ref = omp.parse_model_ref("qwen3-coder")

        self.assertEqual(ref.namespace, "library")
        self.assertEqual(ref.name, "qwen3-coder")
        self.assertEqual(ref.tag, "latest")

    def test_parse_model_ref_accepts_namespace_and_tag(self):
        ref = omp.parse_model_ref("acme/model:7b")

        self.assertEqual(ref.namespace, "acme")
        self.assertEqual(ref.name, "model")
        self.assertEqual(ref.tag, "7b")

    def test_fetch_json_includes_plain_text_http_error_body(self):
        error = urllib.error.HTTPError(
            "https://registry.ollama.ai/v2/library/qwen3.6/manifests/27b-coding-mxfp8",
            412,
            "Precondition Failed",
            {},
            io.BytesIO(b"this model requires macOS"),
        )

        with mock.patch("ollama_manual_pull.core.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 412.*this model requires macOS"):
                core.fetch_json(error.url, retries=0)

    def test_model_paths_match_ollama_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = omp.model_paths(Path(tmp), omp.parse_model_ref("qwen3-coder:30b"))

            self.assertEqual(paths.blobs, Path(tmp) / "blobs")
            self.assertEqual(
                paths.manifest,
                Path(tmp)
                / "manifests"
                / "registry.ollama.ai"
                / "library"
                / "qwen3-coder"
                / "30b",
            )

    def test_installed_models_reads_all_namespaces_from_manifest_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library_dir = root / "manifests" / "registry.ollama.ai" / "library" / "qwen3-coder"
            custom_dir = root / "manifests" / "registry.ollama.ai" / "someuser" / "custom-model"
            library_dir.mkdir(parents=True)
            custom_dir.mkdir(parents=True)
            (library_dir / "30b").write_text("{}\n")
            (custom_dir / "q4").write_text("{}\n")

            models = omp.installed_models(root)

        self.assertEqual(
            models,
            [
                {
                    "name": "qwen3-coder:30b",
                    "namespace": "library",
                    "model": "qwen3-coder",
                    "tag": "30b",
                },
                {
                    "name": "someuser/custom-model:q4",
                    "namespace": "someuser",
                    "model": "custom-model",
                    "tag": "q4",
                },
            ],
        )

    def test_verify_file_matches_sha256_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "blob"
            file_path.write_bytes(b"model bytes")
            digest = "sha256:" + hashlib.sha256(b"model bytes").hexdigest()

            self.assertTrue(omp.verify_file(file_path, digest))
            self.assertFalse(omp.verify_file(file_path, "sha256:" + "0" * 64))

    def test_contiguous_prefix_size_for_normal_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "blob"
            file_path.write_bytes(b"model bytes")

            self.assertEqual(omp.contiguous_prefix_size(file_path), len(b"model bytes"))

    def test_install_manifest_writes_ollama_manifest_json(self):
        manifest = {
            "schemaVersion": 2,
            "config": {"digest": "sha256:" + "a" * 64, "size": 3},
            "layers": [{"digest": "sha256:" + "b" * 64, "size": 5}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            ref = omp.parse_model_ref("qwen3-coder:30b")
            paths = omp.model_paths(Path(tmp), ref)
            omp.install_manifest(paths, manifest)

            self.assertTrue(paths.manifest.exists())
            self.assertEqual(json.loads(paths.manifest.read_text()), manifest)

    def test_format_progress_shows_percent_size_speed_and_eta(self):
        line = omp.format_progress(
            downloaded=1_500_000_000,
            total=3_000_000_000,
            bytes_per_second=5_000_000,
        )

        self.assertIn("50.0%", line)
        self.assertIn("1.5GB/3.0GB", line)
        self.assertIn("5.0MB/s", line)
        self.assertIn("eta 5m00s", line)

    def test_format_progress_handles_unknown_total(self):
        line = omp.format_progress(
            downloaded=1_500_000,
            total=None,
            bytes_per_second=500_000,
        )

        self.assertIn("1.5MB", line)
        self.assertIn("500.0KB/s", line)
        self.assertNotIn("eta", line)

    def test_download_blob_resumes_from_existing_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = omp.model_paths(Path(tmp), omp.parse_model_ref("qwen3-coder:30b"))
            paths.blobs.mkdir(parents=True)
            digest = "sha256:" + hashlib.sha256(b"abcdef").hexdigest()
            temp = paths.blobs / (omp.digest_filename(digest) + ".manual-download")
            temp.write_bytes(b"abc")
            requests = []

            class FakeResponse(io.BytesIO):
                headers = {"Content-Length": "3"}

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    self.close()

            def fake_urlopen(request, timeout):
                requests.append(request)
                return FakeResponse(b"def")

            with mock.patch.object(core.urllib.request, "urlopen", side_effect=fake_urlopen):
                omp.download_blob(
                    registry="https://registry.ollama.ai",
                    ref=omp.parse_model_ref("qwen3-coder:30b"),
                    paths=paths,
                    digest=digest,
                    retries=0,
                    dry_run=False,
                )

            self.assertEqual(requests[0].get_header("Range"), "bytes=3-")
            self.assertTrue((paths.blobs / omp.digest_filename(digest)).exists())

    def test_resume_from_uses_matching_external_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = omp.model_paths(Path(tmp), omp.parse_model_ref("qwen3-coder:30b"))
            paths.blobs.mkdir(parents=True)
            digest = "sha256:" + hashlib.sha256(b"abcdef").hexdigest()
            external = paths.blobs / (omp.digest_filename(digest) + "-partial")
            external.write_bytes(b"abc")
            requests = []

            class FakeResponse(io.BytesIO):
                headers = {"Content-Length": "3"}

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    self.close()

            def fake_urlopen(request, timeout):
                requests.append(request)
                return FakeResponse(b"def")

            with mock.patch.object(core.urllib.request, "urlopen", side_effect=fake_urlopen):
                omp.download_blob(
                    registry="https://registry.ollama.ai",
                    ref=omp.parse_model_ref("qwen3-coder:30b"),
                    paths=paths,
                    digest=digest,
                    retries=0,
                    dry_run=False,
                    resume_from=external,
                )

            self.assertEqual(requests[0].get_header("Range"), "bytes=3-")
            self.assertFalse(external.exists())
            self.assertTrue((paths.blobs / omp.digest_filename(digest)).exists())

    def test_resume_from_deletes_smaller_default_restart_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = omp.model_paths(Path(tmp), omp.parse_model_ref("qwen3-coder:30b"))
            paths.blobs.mkdir(parents=True)
            digest = "sha256:" + hashlib.sha256(b"abcdef").hexdigest()
            default_restart = paths.blobs / (omp.digest_filename(digest) + ".manual-download")
            external = paths.blobs / (omp.digest_filename(digest) + ".older-download")
            default_restart.write_bytes(b"a")
            external.write_bytes(b"abc")
            requests = []

            class FakeResponse(io.BytesIO):
                headers = {"Content-Length": "3"}

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    self.close()

            def fake_urlopen(request, timeout):
                requests.append(request)
                return FakeResponse(b"def")

            with mock.patch.object(core.urllib.request, "urlopen", side_effect=fake_urlopen):
                omp.download_blob(
                    registry="https://registry.ollama.ai",
                    ref=omp.parse_model_ref("qwen3-coder:30b"),
                    paths=paths,
                    digest=digest,
                    retries=0,
                    dry_run=False,
                    resume_from=external,
                )

            self.assertFalse(default_restart.exists())
            self.assertEqual(requests[0].get_header("Range"), "bytes=3-")

    def test_download_blob_reports_complete_event_for_existing_verified_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = omp.parse_model_ref("qwen3-coder:30b")
            paths = omp.model_paths(Path(tmp), ref)
            paths.blobs.mkdir(parents=True)
            digest = "sha256:" + hashlib.sha256(b"model bytes").hexdigest()
            final = paths.blobs / omp.digest_filename(digest)
            final.write_bytes(b"model bytes")
            events = []

            omp.download_blob(
                registry=omp.DEFAULT_REGISTRY,
                ref=ref,
                paths=paths,
                digest=digest,
                retries=0,
                dry_run=False,
                progress=events.append,
            )

            self.assertEqual(
                events,
                [
                    {
                        "type": "blob-complete",
                        "digest": digest,
                        "path": str(final),
                        "reused": True,
                        "downloaded": len(b"model bytes"),
                        "total": len(b"model bytes"),
                        "percent": 100.0,
                    }
                ],
            )

    def test_download_blob_reports_structured_progress_for_active_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = omp.parse_model_ref("qwen3-coder:30b")
            paths = omp.model_paths(Path(tmp), ref)
            digest = "sha256:" + hashlib.sha256(b"abcdef").hexdigest()
            events = []

            class FakeResponse(io.BytesIO):
                headers = {"Content-Length": "6"}

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    self.close()

            with (
                mock.patch.object(core.urllib.request, "urlopen", return_value=FakeResponse(b"abcdef")),
                mock.patch.object(core.time, "monotonic", side_effect=[0.0, 0.6, 0.8]),
            ):
                omp.download_blob(
                    registry=omp.DEFAULT_REGISTRY,
                    ref=ref,
                    paths=paths,
                    digest=digest,
                    retries=0,
                    dry_run=False,
                    progress=events.append,
                )

            progress_events = [event for event in events if event["type"] == "blob-progress"]
            self.assertEqual(len(progress_events), 1)
            self.assertEqual(progress_events[0]["digest"], digest)
            self.assertEqual(progress_events[0]["downloaded"], 6)
            self.assertEqual(progress_events[0]["total"], 6)
            self.assertEqual(progress_events[0]["percent"], 100.0)
            self.assertGreater(progress_events[0]["bytes_per_second"], 0)
            self.assertEqual(progress_events[0]["eta_seconds"], 0)
            self.assertIn("100.0%", progress_events[0]["line"])
            self.assertEqual(events[-1]["downloaded"], 6)
            self.assertEqual(events[-1]["total"], 6)

    def test_download_blob_wraps_progress_callback_error_for_existing_verified_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = omp.parse_model_ref("qwen3-coder:30b")
            paths = omp.model_paths(Path(tmp), ref)
            paths.blobs.mkdir(parents=True)
            digest = "sha256:" + hashlib.sha256(b"model bytes").hexdigest()
            final = paths.blobs / omp.digest_filename(digest)
            final.write_bytes(b"model bytes")
            attempts = []

            def failing_progress(event):
                attempts.append(event)
                raise RuntimeError("callback exploded")

            with self.assertRaises(omp.core.ProgressCallbackError) as raised:
                omp.download_blob(
                    registry=omp.DEFAULT_REGISTRY,
                    ref=ref,
                    paths=paths,
                    digest=digest,
                    retries=2,
                    dry_run=False,
                    progress=failing_progress,
                )

            self.assertIsInstance(raised.exception.__cause__, RuntimeError)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["type"], "blob-complete")

    def test_pull_model_reports_manifest_fetch_before_fetching_manifest(self):
        manifest_url_seen = []
        events = []
        original_fetch_json = omp.core.fetch_json

        def fake_fetch_json(url, retries):
            manifest_url_seen.append(url)
            self.assertEqual(
                events,
                [
                    {
                        "type": "manifest-fetch",
                        "model": "qwen3-coder:30b",
                        "url": url,
                    }
                ],
            )
            return {
                "schemaVersion": 2,
                "config": {"digest": "sha256:" + "a" * 64, "size": 3},
                "layers": [],
            }

        try:
            omp.core.fetch_json = fake_fetch_json
            with tempfile.TemporaryDirectory() as tmp:
                omp.pull_model(
                    "qwen3-coder:30b",
                    models_dir=Path(tmp),
                    registry=omp.DEFAULT_REGISTRY,
                    retries=0,
                    dry_run=True,
                    progress=events.append,
                )
        finally:
            omp.core.fetch_json = original_fetch_json

        self.assertEqual(
            manifest_url_seen,
            [
                "https://registry.ollama.ai/v2/library/qwen3-coder/manifests/30b",
            ],
        )

    def test_pull_model_reports_model_plan_after_manifest_fetch(self):
        events = []
        manifest = {
            "schemaVersion": 2,
            "config": {"digest": "sha256:" + "a" * 64, "size": 3},
            "layers": [{"digest": "sha256:" + "b" * 64, "size": 5}],
        }

        with mock.patch.object(omp.core, "fetch_json", return_value=manifest):
            with tempfile.TemporaryDirectory() as tmp:
                omp.pull_model(
                    "qwen3-coder:30b",
                    models_dir=Path(tmp),
                    registry=omp.DEFAULT_REGISTRY,
                    retries=0,
                    dry_run=True,
                    progress=events.append,
                )

        self.assertEqual(events[1]["type"], "model-plan")
        self.assertEqual(events[1]["model"], "qwen3-coder:30b")
        self.assertEqual(events[1]["total_bytes"], 8)
        self.assertEqual(
            events[1]["files"],
            [
                {"digest": "sha256:" + "a" * 64, "size": 3},
                {"digest": "sha256:" + "b" * 64, "size": 5},
            ],
        )

if __name__ == "__main__":
    unittest.main()
