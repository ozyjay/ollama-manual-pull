from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_REGISTRY = "https://registry.ollama.ai"
DEFAULT_HOST = "registry.ollama.ai"
ProgressCallback = Any


@dataclasses.dataclass(frozen=True)
class ModelRef:
    namespace: str
    name: str
    tag: str


@dataclasses.dataclass(frozen=True)
class ModelPaths:
    root: Path
    blobs: Path
    manifest: Path


def parse_model_ref(value: str) -> ModelRef:
    if not value or value.startswith(":") or value.endswith("/"):
        raise ValueError(f"Invalid model reference: {value!r}")

    path, sep, tag = value.partition(":")
    tag = tag if sep else "latest"
    parts = path.split("/")

    if len(parts) == 1:
        namespace, name = "library", parts[0]
    elif len(parts) == 2:
        namespace, name = parts
    else:
        raise ValueError("Model reference must be NAME[:TAG] or NAMESPACE/NAME[:TAG]")

    if not namespace or not name or not tag:
        raise ValueError(f"Invalid model reference: {value!r}")

    return ModelRef(namespace=namespace, name=name, tag=tag)


def model_paths(root: Path, ref: ModelRef, host: str = DEFAULT_HOST) -> ModelPaths:
    return ModelPaths(
        root=root,
        blobs=root / "blobs",
        manifest=root / "manifests" / host / ref.namespace / ref.name / ref.tag,
    )


def digest_filename(digest: str) -> str:
    algorithm, value = digest.split(":", 1)
    if algorithm != "sha256" or len(value) != 64:
        raise ValueError(f"Unsupported digest: {digest}")
    return f"{algorithm}-{value}"


def verify_file(path: Path, digest: str) -> bool:
    expected = digest.split(":", 1)[1]
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                hasher.update(chunk)
    except FileNotFoundError:
        return False
    return hasher.hexdigest() == expected


def emit_progress(progress: ProgressCallback | None, event: dict[str, Any]) -> None:
    if progress is not None:
        progress(event)


def fetch_json(url: str, retries: int) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(min(2**attempt, 10))
    raise RuntimeError(f"Failed to fetch JSON from {url}: {last_error}")


def install_manifest(paths: ModelPaths, manifest: dict[str, Any]) -> None:
    paths.manifest.parent.mkdir(parents=True, exist_ok=True)
    paths.manifest.write_text(json.dumps(manifest, separators=(",", ":")) + "\n")


def manifest_digests(manifest: dict[str, Any]) -> list[str]:
    digests = [manifest["config"]["digest"]]
    digests.extend(layer["digest"] for layer in manifest.get("layers", []))
    return digests


def download_blob(
    *,
    registry: str,
    ref: ModelRef,
    paths: ModelPaths,
    digest: str,
    retries: int,
    dry_run: bool,
    progress: ProgressCallback | None = None,
) -> None:
    final = paths.blobs / digest_filename(digest)
    temp = final.with_name(final.name + ".manual-download")
    url = f"{registry.rstrip('/')}/v2/{ref.namespace}/{ref.name}/blobs/{digest}"

    if verify_file(final, digest):
        print(f"OK already present: {final.name}", flush=True)
        emit_progress(
            progress,
            {
                "type": "blob-complete",
                "digest": digest,
                "path": str(final),
                "reused": True,
            },
        )
        return

    if dry_run:
        print(f"Would download: {url}", flush=True)
        emit_progress(
            progress,
            {
                "type": "blob-dry-run",
                "digest": digest,
                "url": url,
            },
        )
        return

    paths.blobs.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries + 1):
        try:
            resume_at = temp.stat().st_size if temp.exists() else 0
            request = urllib.request.Request(url)
            mode = "ab" if resume_at else "wb"
            if resume_at:
                request.add_header("Range", f"bytes={resume_at}-")

            print(f"Downloading: {final.name}", flush=True)
            emit_progress(
                progress,
                {
                    "type": "blob-start",
                    "digest": digest,
                    "url": url,
                    "resume_at": resume_at,
                },
            )
            with urllib.request.urlopen(request, timeout=60) as response, temp.open(mode) as file:
                shutil.copyfileobj(response, file, length=1024 * 1024)

            if verify_file(temp, digest):
                temp.replace(final)
                print(f"Verified: {final.name}", flush=True)
                emit_progress(
                    progress,
                    {
                        "type": "blob-complete",
                        "digest": digest,
                        "path": str(final),
                        "reused": False,
                    },
                )
                return

            raise RuntimeError(f"Checksum mismatch for {final.name}")
        except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
            if attempt >= retries:
                raise RuntimeError(f"Failed to download {digest}: {error}") from error
            print(f"Retrying {final.name}: {error}", file=sys.stderr, flush=True)
            emit_progress(
                progress,
                {
                    "type": "blob-retry",
                    "digest": digest,
                    "error": str(error),
                    "attempt": attempt + 1,
                },
            )
            time.sleep(min(2**attempt, 10))


def pull_model(
    model: str,
    *,
    models_dir: Path,
    registry: str,
    retries: int,
    dry_run: bool,
    progress: ProgressCallback | None = None,
) -> None:
    ref = parse_model_ref(model)
    host = urllib.parse.urlparse(registry).netloc or DEFAULT_HOST
    paths = model_paths(models_dir, ref, host=host)
    manifest_url = f"{registry.rstrip('/')}/v2/{ref.namespace}/{ref.name}/manifests/{ref.tag}"

    print(f"Fetching manifest: {manifest_url}", flush=True)
    emit_progress(
        progress,
        {
            "type": "manifest-fetch",
            "model": model,
            "url": manifest_url,
        },
    )
    manifest = fetch_json(manifest_url, retries=retries)

    for digest in manifest_digests(manifest):
        download_blob(
            registry=registry,
            ref=ref,
            paths=paths,
            digest=digest,
            retries=retries,
            dry_run=dry_run,
            progress=progress,
        )

    if dry_run:
        print(f"Would write manifest: {paths.manifest}", flush=True)
        return

    install_manifest(paths, manifest)
    emit_progress(
        progress,
        {
            "type": "model-complete",
            "model": model,
            "manifest": str(paths.manifest),
        },
    )
    print(flush=True)
    print(f"Registered {model} with Ollama.", flush=True)
    print("Check with: ollama list", flush=True)


def default_models_dir() -> Path:
    return Path(os.environ.get("OLLAMA_MODELS", Path.home() / ".ollama" / "models")).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manually download and register Ollama models from registry.ollama.ai."
    )
    parser.add_argument("model", help="Model reference, for example qwen3-coder:30b")
    parser.add_argument("--models-dir", type=Path, default=default_models_dir())
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--retries", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        pull_model(
            args.model,
            models_dir=args.models_dir.expanduser(),
            registry=args.registry,
            retries=args.retries,
            dry_run=args.dry_run,
        )
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        return 1
    return 0
