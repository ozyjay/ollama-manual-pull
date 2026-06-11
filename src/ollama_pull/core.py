from __future__ import annotations

import argparse
import base64
import dataclasses
import functools
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .app_logging import write_log


DEFAULT_REGISTRY = "https://registry.ollama.ai"
DEFAULT_HOST = "registry.ollama.ai"
ProgressCallback = Callable[[dict[str, Any]], None]
StopAfterBlobCallback = Callable[[], bool]
SHA256_FILE_RE = re.compile(r"^sha256-([a-fA-F0-9]{64})$")
SHA256_IN_NAME_RE = re.compile(r"sha256[-:]([a-fA-F0-9]{64})")
ED25519_P = 2**255 - 19
ED25519_Q = 2**252 + 27742317777372353535851937790883648493
ED25519_D = -121665 * pow(121666, ED25519_P - 2, ED25519_P) % ED25519_P
ED25519_B = (
    15112221349535400772501151409588531511454012693041857206046113283949847762202,
    46316835694926478169428394003475163141307993866256225615783033603165251855960,
)


class ProgressCallbackError(Exception):
    pass


class DownloadStoppedAfterBlob(Exception):
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


def progress_payload(
    *,
    downloaded: int,
    total: int | None,
    bytes_per_second: float,
) -> dict[str, Any]:
    percent = downloaded / total * 100 if total and total > 0 else None
    eta_seconds = None
    if total and total > 0 and bytes_per_second > 0:
        eta_seconds = int(max(total - downloaded, 0) / bytes_per_second)
    return {
        "downloaded": downloaded,
        "total": total,
        "percent": percent,
        "bytes_per_second": bytes_per_second,
        "eta_seconds": eta_seconds,
        "line": format_progress(
            downloaded=downloaded,
            total=total,
            bytes_per_second=bytes_per_second,
        ),
    }


def emit_progress(progress: ProgressCallback | None, event: dict[str, Any]) -> None:
    if progress is not None:
        try:
            progress(event)
        except Exception as error:
            raise ProgressCallbackError("Progress callback failed") from error


def registry_request(url: str) -> urllib.request.Request:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", ollama_user_agent())
    identity = default_ollama_identity()
    if identity is not None:
        seed, public_key = identity
        request.add_header("Authorization", "Bearer " + ollama_auth_token(seed, public_key))
    return request


class RegistryRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        original_host = urllib.parse.urlparse(req.full_url).netloc
        redirected_host = urllib.parse.urlparse(newurl).netloc
        if original_host != redirected_host:
            redirected.remove_header("Authorization")
        return redirected


def registry_urlopen(request: urllib.request.Request, *, timeout: int) -> Any:
    opener = urllib.request.build_opener(RegistryRedirectHandler())
    return opener.open(request, timeout=timeout)


def ollama_user_agent() -> str:
    arch = platform.machine().lower()
    arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(arch, arch)
    os_name = "darwin" if sys.platform == "darwin" else sys.platform.split("-", 1)[0].lower()
    return f"ollama/v0.0.0 ({arch} {os_name}) Python/{platform.python_version()}"


@functools.cache
def default_ollama_identity() -> tuple[bytes, bytes] | None:
    path = Path.home() / ".ollama" / "id_ed25519"
    try:
        return load_openssh_ed25519_identity(path)
    except FileNotFoundError:
        return None


def load_openssh_ed25519_identity(path: Path) -> tuple[bytes, bytes]:
    text = path.read_text()
    encoded = "".join(line for line in text.splitlines() if not line.startswith("-----"))
    data = base64.b64decode(encoded)
    magic = b"openssh-key-v1\0"
    if not data.startswith(magic):
        raise ValueError("Unsupported Ollama identity format")

    offset = len(magic)
    cipher, offset = read_ssh_string(data, offset)
    kdf, offset = read_ssh_string(data, offset)
    _, offset = read_ssh_string(data, offset)
    key_count, offset = read_ssh_uint32(data, offset)
    if cipher != b"none" or kdf != b"none" or key_count != 1:
        raise ValueError("Unsupported encrypted or multi-key Ollama identity")

    _, offset = read_ssh_string(data, offset)
    private_blob, _ = read_ssh_string(data, offset)
    offset = 8
    key_type, offset = read_ssh_string(private_blob, offset)
    public_key, offset = read_ssh_string(private_blob, offset)
    private_key, _ = read_ssh_string(private_blob, offset)
    if key_type != b"ssh-ed25519" or len(private_key) != 64:
        raise ValueError("Ollama identity must be an Ed25519 key")

    seed, derived_public_key = private_key[:32], private_key[32:]
    if public_key != derived_public_key or ed25519_public_key(seed) != derived_public_key:
        raise ValueError("Ollama identity public key mismatch")
    return seed, derived_public_key


def read_ssh_uint32(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack(">I", data[offset : offset + 4])[0], offset + 4


def read_ssh_string(data: bytes, offset: int) -> tuple[bytes, int]:
    length, offset = read_ssh_uint32(data, offset)
    return data[offset : offset + length], offset + length


def ssh_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def ollama_auth_token(seed: bytes, public_key: bytes) -> str:
    check_url = f"https://ollama.com?ts={int(time.time())}"
    zero_sum = base64.b64encode(hashlib.sha256(b"").hexdigest().encode("ascii")).decode("ascii")
    check_data = f"GET,{check_url},{zero_sum}".encode("utf-8")
    signature = ed25519_sign(check_data, seed, public_key)
    public_key_blob = ssh_string(b"ssh-ed25519") + ssh_string(public_key)
    return ":".join(
        [
            base64.b64encode(check_url.encode("utf-8")).decode("ascii"),
            base64.b64encode(public_key_blob).decode("ascii"),
            base64.b64encode(signature).decode("ascii"),
        ]
    )


def ed25519_public_key(seed: bytes) -> bytes:
    hashed = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(hashed[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return ed25519_encode_point(ed25519_scalar_multiply(ED25519_B, scalar))


def ed25519_sign(message: bytes, seed: bytes, public_key: bytes) -> bytes:
    hashed = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(hashed[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    nonce = ed25519_hint(hashed[32:] + message) % ED25519_Q
    encoded_nonce = ed25519_encode_point(ed25519_scalar_multiply(ED25519_B, nonce))
    signature_scalar = (
        nonce + ed25519_hint(encoded_nonce + public_key + message) * scalar
    ) % ED25519_Q
    return encoded_nonce + signature_scalar.to_bytes(32, "little")


def ed25519_hint(value: bytes) -> int:
    return int.from_bytes(hashlib.sha512(value).digest(), "little")


def ed25519_inverse(value: int) -> int:
    return pow(value, ED25519_P - 2, ED25519_P)


def ed25519_add(
    first: tuple[int, int],
    second: tuple[int, int],
) -> tuple[int, int]:
    x1, y1 = first
    x2, y2 = second
    x3 = (x1 * y2 + x2 * y1) * ed25519_inverse(1 + ED25519_D * x1 * x2 * y1 * y2) % ED25519_P
    y3 = (y1 * y2 + x1 * x2) * ed25519_inverse(1 - ED25519_D * x1 * x2 * y1 * y2) % ED25519_P
    return x3, y3


def ed25519_scalar_multiply(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    if scalar == 0:
        return (0, 1)
    result = ed25519_scalar_multiply(point, scalar // 2)
    result = ed25519_add(result, result)
    if scalar & 1:
        result = ed25519_add(result, point)
    return result


def ed25519_encode_point(point: tuple[int, int]) -> bytes:
    x, y = point
    encoded = bytearray(y.to_bytes(32, "little"))
    encoded[31] |= (x & 1) << 7
    return bytes(encoded)


def fetch_json(url: str, retries: int) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            write_log("manifest request", url=url, attempt=attempt + 1)
            with registry_urlopen(registry_request(url), timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            message = http_error_message(error)
            write_log("manifest request failed", url=url, attempt=attempt + 1, error=message)
            last_error = RuntimeError(message)
            if 400 <= error.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            write_log("manifest request failed", url=url, attempt=attempt + 1, error=error)
            last_error = error
        if attempt < retries:
            time.sleep(min(2**attempt, 10))
    raise RuntimeError(f"Failed to fetch JSON from {url}: {last_error}")


def http_error_message(error: urllib.error.HTTPError) -> str:
    body = error.read().decode("utf-8", errors="replace").strip()
    message = f"HTTP {error.code} {error.reason}"
    if body:
        message = f"{message}: {body}"
    return message


def install_manifest(paths: ModelPaths, manifest: dict[str, Any]) -> None:
    paths.manifest.parent.mkdir(parents=True, exist_ok=True)
    paths.manifest.write_text(json.dumps(manifest, separators=(",", ":")) + "\n")


def installed_models(root: Path, host: str = DEFAULT_HOST) -> list[dict[str, str]]:
    manifests = Path(root) / "manifests" / host
    if not manifests.is_dir():
        return []

    models: list[dict[str, str]] = []
    for tag_path in manifests.glob("*/*/*"):
        if not tag_path.is_file():
            continue
        try:
            namespace, model, tag = tag_path.relative_to(manifests).parts
        except ValueError:
            continue
        name = f"{model}:{tag}" if namespace == "library" else f"{namespace}/{model}:{tag}"
        models.append(
            {
                "name": name,
                "namespace": namespace,
                "model": model,
                "tag": tag,
            }
        )

    return sorted(models, key=lambda item: item["name"].lower())


def delete_installed_model(root: Path, model: str, host: str = DEFAULT_HOST) -> None:
    ref = parse_model_ref(model)
    paths = model_paths(Path(root), ref, host=host)
    try:
        paths.manifest.unlink()
    except FileNotFoundError as error:
        raise KeyError(model) from error

    current = paths.manifest.parent
    manifests_root = Path(root) / "manifests" / host
    while current != manifests_root and manifests_root in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def cleanup_orphan_blobs(
    root: Path,
    *,
    delete: bool = False,
    include_partials: bool = False,
    older_than_days: int = 7,
) -> dict[str, Any]:
    root = Path(root)
    referenced = referenced_manifest_digests(root)
    blobs_dir = root / "blobs"
    orphan_blobs: list[dict[str, Any]] = []
    stale_partials: list[dict[str, Any]] = []
    skipped_partials: list[dict[str, Any]] = []
    deleted: list[str] = []
    warnings: list[str] = []
    stale_before = time.time() - max(0, older_than_days) * 24 * 60 * 60

    if blobs_dir.is_dir():
        for path in sorted(blobs_dir.iterdir(), key=lambda candidate: candidate.name):
            if not path.is_file():
                continue
            final_match = SHA256_FILE_RE.fullmatch(path.name)
            if final_match:
                digest = f"sha256:{final_match.group(1).lower()}"
                if digest not in referenced:
                    orphan_blobs.append(cleanup_file_item(path, digest=digest, kind="blob"))
                continue

            partial_match = SHA256_IN_NAME_RE.search(path.name)
            if not partial_match:
                continue
            digest = f"sha256:{partial_match.group(1).lower()}"
            item = cleanup_file_item(path, digest=digest, kind="partial")
            if digest in referenced:
                skipped_partials.append(item)
            elif include_partials and path.stat().st_mtime <= stale_before:
                stale_partials.append(item)
            else:
                skipped_partials.append(item)

    delete_candidates = [*orphan_blobs, *stale_partials]
    if delete:
        for item in delete_candidates:
            path = Path(item["path"])
            try:
                path.unlink()
                deleted.append(str(path))
            except FileNotFoundError:
                warnings.append(f"Already gone: {path}")

    return {
        "dry_run": not delete,
        "include_partials": include_partials,
        "older_than_days": older_than_days,
        "referenced_count": len(referenced),
        "orphan_blob_count": len(orphan_blobs),
        "orphan_blob_bytes": sum(item["size"] for item in orphan_blobs),
        "stale_partial_count": len(stale_partials),
        "stale_partial_bytes": sum(item["size"] for item in stale_partials),
        "skipped_partial_count": len(skipped_partials),
        "skipped_partial_bytes": sum(item["size"] for item in skipped_partials),
        "orphan_blobs": orphan_blobs,
        "stale_partials": stale_partials,
        "skipped_partials": skipped_partials,
        "deleted": deleted,
        "warnings": warnings,
    }


def referenced_manifest_digests(root: Path) -> set[str]:
    manifests = Path(root) / "manifests"
    if not manifests.is_dir():
        return set()

    referenced: set[str] = set()
    for manifest_path in sorted(path for path in manifests.rglob("*") if path.is_file()):
        try:
            manifest = json.loads(manifest_path.read_text())
            referenced.update(manifest_digests(manifest))
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not parse manifest {manifest_path}: {error}") from error
    return referenced


def cleanup_file_item(path: Path, *, digest: str, kind: str) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "name": path.name,
        "digest": digest,
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
        "type": kind,
    }


def manifest_digests(manifest: dict[str, Any]) -> list[str]:
    digests = [manifest["config"]["digest"]]
    digests.extend(layer["digest"] for layer in manifest.get("layers", []))
    return digests


def manifest_files(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = [
        {
            "digest": manifest["config"]["digest"],
            "size": manifest["config"].get("size"),
        }
    ]
    files.extend(
        {
            "digest": layer["digest"],
            "size": layer.get("size"),
        }
        for layer in manifest.get("layers", [])
    )
    return files


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
        size = final.stat().st_size
        print(f"OK already present: {final.name}", flush=True)
        emit_progress(
            progress,
            {
                "type": "blob-complete",
                "digest": digest,
                "path": str(final),
                "reused": True,
                "downloaded": size,
                "total": size,
                "percent": 100.0,
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
            request = registry_request(url)
            mode = "ab" if resume_at else "wb"
            if resume_at:
                request.add_header("Range", f"bytes={resume_at}-")

            print(f"Downloading: {final.name}", flush=True)
            if resume_at:
                print(f"Resuming at: {format_size(resume_at)}", flush=True)
            write_log(
                "blob request",
                url=url,
                digest=digest,
                attempt=attempt + 1,
                resume_at=resume_at,
            )
            emit_progress(
                progress,
                {
                    "type": "blob-start",
                    "digest": digest,
                    "url": url,
                    "resume_at": resume_at,
                },
            )
            with registry_urlopen(request, timeout=60) as response, temp.open(mode) as file:
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
                        payload = progress_payload(
                            downloaded=downloaded,
                            total=total_bytes,
                            bytes_per_second=(downloaded - resume_at) / elapsed,
                        )
                        print(f"\r{payload['line']}", end="", flush=True)
                        emit_progress(
                            progress,
                            {
                                "type": "blob-progress",
                                "digest": digest,
                                **payload,
                            },
                        )
                        last_printed = now

                elapsed = max(time.monotonic() - started, 0.001)
                payload = progress_payload(
                    downloaded=downloaded,
                    total=total_bytes,
                    bytes_per_second=(downloaded - resume_at) / elapsed,
                )
                print(f"\r{payload['line']}", flush=True)

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
                        "downloaded": downloaded,
                        "total": total_bytes,
                        "percent": payload["percent"],
                    },
                )
                return

            raise RuntimeError(f"Checksum mismatch for {final.name}")
        except urllib.error.HTTPError as error:
            message = http_error_message(error)
            write_log("blob request failed", url=url, digest=digest, attempt=attempt + 1, error=message)
            if attempt >= retries or 400 <= error.code < 500:
                raise RuntimeError(f"Failed to download {digest}: {message}") from error
            print(f"Retrying {final.name}: {message}", file=sys.stderr, flush=True)
            emit_progress(
                progress,
                {
                    "type": "blob-retry",
                    "digest": digest,
                    "error": message,
                    "attempt": attempt + 1,
                },
            )
            time.sleep(min(2**attempt, 10))
        except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
            write_log("blob request failed", url=url, digest=digest, attempt=attempt + 1, error=error)
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
    stop_after_blob: StopAfterBlobCallback | None = None,
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
    files = manifest_files(manifest)
    sizes = [file.get("size") for file in files]
    total_bytes = sum(sizes) if all(isinstance(size, int) for size in sizes) else None
    emit_progress(
        progress,
        {
            "type": "model-plan",
            "model": model,
            "total_bytes": total_bytes,
            "files": files,
        },
    )

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
        if stop_after_blob is not None and stop_after_blob():
            raise DownloadStoppedAfterBlob

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


def build_gc_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Remove Ollama blob shards not referenced by installed manifests.")
    parser.add_argument("command", choices=["gc"])
    parser.add_argument("--models-dir", type=Path, default=default_models_dir())
    parser.add_argument("--delete", action="store_true", help="Delete candidates. Defaults to dry-run.")
    parser.add_argument(
        "--include-partials",
        action="store_true",
        help="Include stale partial download files in cleanup candidates.",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=7,
        help="Minimum age for partial download cleanup when --include-partials is set.",
    )
    return parser


def print_cleanup_report(report: dict[str, Any]) -> None:
    mode = "Dry run" if report["dry_run"] else "Deleted"
    print(f"{mode}: orphan shard cleanup", flush=True)
    print(f"Referenced blobs: {report['referenced_count']}", flush=True)
    print(
        f"Orphan complete blobs: {report['orphan_blob_count']} "
        f"({format_size(report['orphan_blob_bytes'])})",
        flush=True,
    )
    print(
        f"Stale partial downloads: {report['stale_partial_count']} "
        f"({format_size(report['stale_partial_bytes'])})",
        flush=True,
    )
    print(f"Skipped partial downloads: {report['skipped_partial_count']}", flush=True)
    print(f"Deleted files: {len(report['deleted'])}", flush=True)
    if report["dry_run"]:
        print("Re-run with --delete to remove listed complete orphan blobs.", flush=True)
    for warning in report["warnings"]:
        print(f"Warning: {warning}", flush=True)


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args[:1] == ["gc"]:
        parser = build_gc_parser()
        args = parser.parse_args(raw_args)
        try:
            report = cleanup_orphan_blobs(
                args.models_dir.expanduser(),
                delete=args.delete,
                include_partials=args.include_partials,
                older_than_days=args.older_than_days,
            )
            print_cleanup_report(report)
        except Exception as error:
            print(f"Error: {error}", file=sys.stderr, flush=True)
            return 1
        return 0

    parser = build_parser()
    args = parser.parse_args(raw_args)
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
