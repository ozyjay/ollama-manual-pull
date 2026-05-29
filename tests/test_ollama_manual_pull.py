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

    def test_download_blob_resumes_from_existing_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = omp.model_paths(Path(tmp), omp.parse_model_ref("qwen3-coder:30b"))
            paths.blobs.mkdir(parents=True)
            digest = "sha256:" + hashlib.sha256(b"abcdef").hexdigest()
            temp = paths.blobs / (omp.digest_filename(digest) + ".manual-download")
            temp.write_bytes(b"abc")
            requests = []

            class FakeResponse(io.BytesIO):
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


if __name__ == "__main__":
    unittest.main()
