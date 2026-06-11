from __future__ import annotations

import argparse
import json
import plistlib
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path


APP_NAME = "OllamaPull"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
NATIVE_APP_SOURCE_DIR = PROJECT_ROOT / "macos" / "OllamaPull"


def default_applications_dir() -> Path:
    return Path.home() / "Applications"


def build_app(
    *,
    output_dir: Path = PROJECT_ROOT / "dist",
    python_executable: Path = Path(sys.executable),
) -> Path:
    app_path = output_dir / f"{APP_NAME}.app"
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"

    if app_path.exists():
        shutil.rmtree(app_path)

    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    shutil.copytree(PROJECT_ROOT / "src", resources / "src")
    shutil.copy2(PROJECT_ROOT / "README.md", resources / "README.md")
    shutil.copy2(PROJECT_ROOT / "LICENSE", resources / "LICENSE")

    _write_app_icon(resources)
    _write_info_plist(contents / "Info.plist")
    native_source_dir = resources / "macos" / "OllamaPull"
    _write_native_sources(native_source_dir, python_executable)
    _compile_native_app(sorted(native_source_dir.rglob("*.swift")), macos / APP_NAME)
    return app_path


def install_app(
    app_path: Path,
    *,
    applications_dir: Path | None = None,
) -> Path:
    if applications_dir is None:
        applications_dir = default_applications_dir()
    destination = applications_dir / app_path.name
    applications_dir.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(app_path, destination)
    destination.touch()
    return destination


def install_app_with_admin_prompt(
    app_path: Path,
    *,
    applications_dir: Path | None = None,
) -> Path:
    if applications_dir is None:
        applications_dir = default_applications_dir()
    destination = applications_dir / app_path.name
    command = " && ".join(
        [
            f"/bin/mkdir -p {shlex.quote(str(applications_dir))}",
            f"/bin/rm -rf {shlex.quote(str(destination))}",
            f"/bin/cp -R {shlex.quote(str(app_path))} {shlex.quote(str(destination))}",
            f"/usr/bin/touch {shlex.quote(str(destination))}",
        ]
    )
    subprocess.run(
        [
            "osascript",
            "-e",
            f"do shell script {json.dumps(command)} with administrator privileges",
        ],
        check=True,
    )
    return destination


def _write_info_plist(path: Path) -> None:
    payload = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": APP_NAME,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIconFile": "AppIcon",
        "CFBundleIconName": "AppIcon",
        "CFBundleIdentifier": "local.ollamapull",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "11.0",
    }
    path.write_bytes(plistlib.dumps(payload, sort_keys=True))


def _write_app_icon(resources: Path) -> None:
    (resources / "AppIcon.svg").write_text(_app_icon_svg())
    icon_sizes = {
        "icp4": 16,
        "icp5": 32,
        "icp6": 64,
        "ic11": 32,
        "ic12": 64,
        "ic07": 128,
        "ic08": 256,
        "ic13": 256,
        "ic09": 512,
        "ic14": 512,
        "ic10": 1024,
    }
    _write_icns(resources / "AppIcon.icns", {kind: _render_icon_png(size) for kind, size in icon_sizes.items()})


def _app_icon_svg() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#30353b"/>
      <stop offset="1" stop-color="#14171b"/>
    </linearGradient>
    <linearGradient id="arrow" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#72f0cf"/>
      <stop offset="1" stop-color="#2dbd8f"/>
    </linearGradient>
  </defs>
  <rect x="64" y="64" width="896" height="896" rx="216" fill="url(#bg)"/>
  <path d="M450 238h124v302h118L512 720 332 540h118z" fill="url(#arrow)"/>
  <rect x="250" y="700" width="524" height="68" rx="34" fill="#d7e5dd"/>
  <rect x="302" y="778" width="420" height="56" rx="28" fill="#98b8aa"/>
  <rect x="368" y="844" width="288" height="44" rx="22" fill="#657e75"/>
</svg>
"""


def _render_icon_png(size: int) -> bytes:
    scale = 2 if size <= 256 else 1
    high_size = size * scale
    pixels = [_icon_pixel(x / scale, y / scale, size) for y in range(high_size) for x in range(high_size)]
    if scale > 1:
        pixels = _downsample_rgba(pixels, high_size, size, scale)
    return _encode_png(size, size, pixels)


def _icon_pixel(x: float, y: float, size: int) -> tuple[int, int, int, int]:
    nx = x / size
    ny = y / size
    background_alpha = _rounded_rect_alpha(nx, ny, 0.0625, 0.0625, 0.875, 0.875, 0.211)
    top = (48, 53, 59)
    bottom = (20, 23, 27)
    base = _mix(top, bottom, min(1.0, ny * 1.1))
    color = (*base, int(255 * background_alpha))

    shadow_alpha = 0.18 * _rounded_rect_alpha(nx, ny, 0.26, 0.70, 0.48, 0.18, 0.04)
    color = _over(color, (0, 0, 0, int(255 * shadow_alpha)))

    for rect, fill in [
        ((0.244, 0.684, 0.512, 0.066, 0.033), (215, 229, 221, 255)),
        ((0.295, 0.760, 0.410, 0.055, 0.027), (152, 184, 170, 255)),
        ((0.359, 0.824, 0.281, 0.043, 0.021), (101, 126, 117, 255)),
    ]:
        alpha = _rounded_rect_alpha(nx, ny, *rect)
        color = _over(color, (*fill[:3], int(fill[3] * alpha)))

    arrow = [
        (0.439, 0.232),
        (0.561, 0.232),
        (0.561, 0.527),
        (0.676, 0.527),
        (0.500, 0.703),
        (0.324, 0.527),
        (0.439, 0.527),
    ]
    if _point_in_polygon(nx, ny, arrow):
        arrow_color = _mix((114, 240, 207), (45, 189, 143), min(1.0, max(0.0, (ny - 0.22) / 0.48)))
        color = _over(color, (*arrow_color, 255))

    highlight_alpha = 0.09 * _rounded_rect_alpha(nx, ny, 0.12, 0.10, 0.76, 0.16, 0.08)
    color = _over(color, (255, 255, 255, int(255 * highlight_alpha)))
    return color


def _rounded_rect_alpha(
    x: float,
    y: float,
    left: float,
    top: float,
    width: float,
    height: float,
    radius: float,
) -> float:
    right = left + width
    bottom = top + height
    if x < left or x > right or y < top or y > bottom:
        return 0.0
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    distance = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    edge = radius - distance
    return max(0.0, min(1.0, edge * 180.0 + 0.5))


def _point_in_polygon(x: float, y: float, points: list[tuple[float, float]]) -> bool:
    inside = False
    previous_x, previous_y = points[-1]
    for current_x, current_y in points:
        intersects = (current_y > y) != (previous_y > y)
        if intersects:
            crossing_x = (previous_x - current_x) * (y - current_y) / (previous_y - current_y) + current_x
            if x < crossing_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def _downsample_rgba(
    pixels: list[tuple[int, int, int, int]],
    source_size: int,
    target_size: int,
    scale: int,
) -> list[tuple[int, int, int, int]]:
    result = []
    samples = scale * scale
    for y in range(target_size):
        for x in range(target_size):
            totals = [0, 0, 0, 0]
            for sy in range(scale):
                row = (y * scale + sy) * source_size
                for sx in range(scale):
                    pixel = pixels[row + x * scale + sx]
                    for channel in range(4):
                        totals[channel] += pixel[channel]
            result.append(tuple(round(total / samples) for total in totals))
    return result


def _encode_png(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for pixel in pixels[y * width : (y + 1) * width]:
            raw.extend(pixel)

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(data, checksum)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _write_icns(path: Path, entries: dict[str, bytes]) -> None:
    chunks = []
    total_size = 8
    for chunk_type, data in entries.items():
        encoded_type = chunk_type.encode("ascii")
        chunk = encoded_type + struct.pack(">I", len(data) + 8) + data
        chunks.append(chunk)
        total_size += len(chunk)
    path.write_bytes(b"icns" + struct.pack(">I", total_size) + b"".join(chunks))


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(start + (end - start) * t) for start, end in zip(a, b))


def _over(bottom: tuple[int, int, int, int], top: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    top_alpha = top[3] / 255
    bottom_alpha = bottom[3] / 255
    out_alpha = top_alpha + bottom_alpha * (1 - top_alpha)
    if out_alpha == 0:
        return (0, 0, 0, 0)
    channels = []
    for index in range(3):
        value = (top[index] * top_alpha + bottom[index] * bottom_alpha * (1 - top_alpha)) / out_alpha
        channels.append(round(value))
    channels.append(round(out_alpha * 255))
    return tuple(channels)


def _write_native_sources(destination: Path, python_executable: Path) -> None:
    shutil.copytree(NATIVE_APP_SOURCE_DIR, destination, ignore=shutil.ignore_patterns("*.in"))
    template = (NATIVE_APP_SOURCE_DIR / "AppConfig.swift.in").read_text()
    rendered = template.replace("%%PYTHON_EXECUTABLE%%", _swift_string_literal(python_executable))
    (destination / "AppConfig.swift").write_text(rendered)


def _swift_string_literal(value: Path) -> str:
    return json.dumps(str(value))


def _compile_native_app(sources: list[Path], executable: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="ollamapull-swift-cache-") as module_cache:
        subprocess.run(
            [
                "swiftc",
                *[str(source) for source in sources],
                "-o",
                str(executable),
                "-parse-as-library",
                "-module-cache-path",
                module_cache,
                "-framework",
                "AppKit",
                "-framework",
                "SwiftUI",
            ],
            check=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the macOS app bundle.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--install", action="store_true", help="Copy the app to ~/Applications after building.")
    parser.add_argument(
        "--applications-dir",
        type=Path,
        default=default_applications_dir(),
        help="Applications directory to use with --install.",
    )
    args = parser.parse_args(argv)

    app_path = build_app(output_dir=args.output_dir, python_executable=args.python)
    print(f"Built: {app_path}")
    if args.install:
        try:
            installed = install_app(app_path, applications_dir=args.applications_dir)
        except PermissionError as error:
            print(f"Permission denied installing to {args.applications_dir}; requesting administrator approval...")
            try:
                installed = install_app_with_admin_prompt(app_path, applications_dir=args.applications_dir)
            except subprocess.CalledProcessError as admin_error:
                raise SystemExit(
                    f"Administrator install failed or was cancelled for {args.applications_dir}."
                ) from admin_error
        print(f"Installed: {installed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
