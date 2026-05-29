import hashlib
import io
import json
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
