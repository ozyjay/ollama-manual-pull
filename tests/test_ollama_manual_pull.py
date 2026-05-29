import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import ollama_manual_pull as omp


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
                    }
                ],
            )

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


if __name__ == "__main__":
    unittest.main()
