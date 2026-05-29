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
from typing import Any, Callable


DEFAULT_REGISTRY = "https://registry.ollama.ai"
DEFAULT_HOST = "registry.ollama.ai"
ProgressCallback = Callable[[dict[str, Any]], None]


class ProgressCallbackError(Exception):
    pass


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


def contiguous_prefix_size(path: Path) -> int:
    size = path.stat().st_size
    if size == 0:
        return 0
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            first_data = os.lseek(fd, 0, os.SEEK_DATA)
            if first_data != 0:
                return 0
            return os.lseek(fd, 0, os.SEEK_HOLE)
        finally:
            os.close(fd)
    except OSError:
        return size


def format_size(num_bytes: int | float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if abs(size) < 1000 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)}B"
            return f"{size:.1f}{unit}"
        size /= 1000
    return f"{size:.1f}TB"


def format_duration(seconds: int | float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"


def format_progress(
    *,
    downloaded: int,
    total: int | None,
    bytes_per_second: float,
) -> str:
    speed = f"{format_size(bytes_per_second)}/s"
    if total and total > 0:
        percent = downloaded / total * 100
        remaining = max(total - downloaded, 0)
        eta = format_duration(remaining / bytes_per_second) if bytes_per_second > 0 else "--"
        return f"{percent:5.1f}% {format_size(downloaded)}/{format_size(total)} {speed} eta {eta}"
    return f"{format_size(downloaded)} {speed}"


def emit_progress(progress: ProgressCallback | None, event: dict[str, Any]) -> None:
    if progress is not None:
        try:
            progress(event)
        except Exception as error:
            raise ProgressCallbackError("Progress callback failed") from error


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
    resume_from: Path | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    final = paths.blobs / digest_filename(digest)
    default_temp = final.with_name(final.name + ".manual-download")
    if resume_from and digest.split(":", 1)[1] in resume_from.name:
        temp = resume_from
    else:
        temp = default_temp
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
    if temp != default_temp and default_temp.exists() and temp.exists():
        default_size = default_temp.stat().st_size
        resume_size = temp.stat().st_size
        if default_size < resume_size and default_size == contiguous_prefix_size(default_temp):
            print(
                f"Removing smaller restart temp: {default_temp.name} ({format_size(default_size)})",
                flush=True,
            )
            default_temp.unlink()

    for attempt in range(retries + 1):
        try:
            resume_at = temp.stat().st_size if temp.exists() else 0
            prefix_size = contiguous_prefix_size(temp) if temp.exists() else 0
            if resume_at != prefix_size:
                raise RuntimeError(
                    f"{temp} is sparse or non-contiguous; only {format_size(prefix_size)} "
                    f"is usable as a simple resume prefix"
                )
            request = urllib.request.Request(url)
            mode = "ab" if resume_at else "wb"
            if resume_at:
                request.add_header("Range", f"bytes={resume_at}-")

            print(f"Downloading: {final.name}", flush=True)
            if resume_at:
                print(f"Resuming at: {format_size(resume_at)}", flush=True)
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
                total = response.headers.get("Content-Length")
                total_bytes = int(total) + resume_at if total and total.isdigit() else None
                downloaded = resume_at
                started = time.monotonic()
                last_printed = 0.0

                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    file.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_printed >= 0.5:
                        elapsed = max(now - started, 0.001)
                        line = format_progress(
                            downloaded=downloaded,
                            total=total_bytes,
                            bytes_per_second=(downloaded - resume_at) / elapsed,
                        )
                        print(f"\r{line}", end="", flush=True)
                        last_printed = now

                elapsed = max(time.monotonic() - started, 0.001)
                line = format_progress(
                    downloaded=downloaded,
                    total=total_bytes,
                    bytes_per_second=(downloaded - resume_at) / elapsed,
                )
                print(f"\r{line}", flush=True)

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
    resume_from: Path | None = None,
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
            resume_from=resume_from,
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
    parser.add_argument(
        "--resume-from",
        type=Path,
        help="Use a specific partial blob file if its filename contains the blob digest.",
    )
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
            resume_from=args.resume_from.expanduser() if args.resume_from else None,
        )
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        return 1
    return 0
