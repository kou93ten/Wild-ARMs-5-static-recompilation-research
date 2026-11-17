"""Check every paired GPU presentation in a capture_native_present sequence.

Uses only the Python standard library, including for RGBA PNG decoding.
The dialogue check compares the detected GS panel rectangle pixel for pixel;
the rest of the exact-size scene may legitimately differ from the GS image.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import zlib
from pathlib import Path


COMPOSITE = re.compile(
    r"\[present-capture\] composite frame (\d+) index (\d+) ready (\d+) "
    r"flags (\d+) scene-drawn (\d+) dialogue (\d+),(\d+)-(\d+),(\d+)"
)
FALLBACK = 4096 | 8192 | 65536 | 4194304 | 33554432
PANEL = 262144


def png_pixels(path: Path) -> tuple[int, int, int, bytes]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Invalid PNG: {path}")
    position = 8
    compressed = bytearray()
    width = height = channels = 0
    while position < len(data):
        length = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4 : position + 8]
        chunk = data[position + 8 : position + 8 + length]
        position += length + 12
        if kind == b"IHDR":
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
                raise ValueError(f"Unsupported PNG format: {path}")
            channels = 4 if color == 6 else 3
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break
    stride = width * channels
    raw = zlib.decompress(compressed)
    if len(raw) != height * (stride + 1):
        raise ValueError(f"Incorrect PNG data length: {path}")
    pixels = bytearray(height * stride)
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filtering = raw[start]
        row = bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            above = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filtering == 1:
                row[i] = (row[i] + left) & 255
            elif filtering == 2:
                row[i] = (row[i] + above) & 255
            elif filtering == 3:
                row[i] = (row[i] + (left + above) // 2) & 255
            elif filtering == 4:
                prediction = left + above - upper_left
                distances = (abs(prediction - left), abs(prediction - above), abs(prediction - upper_left))
                predictor = (left, above, upper_left)[distances.index(min(distances))]
                row[i] = (row[i] + predictor) & 255
            elif filtering != 0:
                raise ValueError(f"Unsupported PNG row filter {filtering}: {path}")
        pixels[y * stride : (y + 1) * stride] = row
        previous = row
    return width, height, channels, bytes(pixels)


def panel_equal(
    present: tuple[int, int, int, bytes],
    reference: tuple[int, int, int, bytes],
    bounds: tuple[int, int, int, int],
    aspect: float,
) -> bool:
    width, height, channels, pixels = present
    if (width, height, channels) != reference[:3]:
        return False
    vw = min(float(width), float(height) * aspect)
    vh = vw / aspect
    left, top = (width - vw) / 2, (height - vh) / 2
    x0, y0, x1, y1 = bounds
    left_px = max(0, math.ceil(left + x0 / 512 * vw - 0.5))
    right_px = min(width, math.ceil(left + x1 / 512 * vw - 0.5))
    top_px = max(0, math.ceil(top + y0 / 448 * vh - 0.5))
    bottom_px = min(height, math.ceil(top + y1 / 448 * vh - 0.5))
    if right_px <= left_px or bottom_px <= top_px:
        return False
    for y in range(top_px, bottom_px):
        start = (y * width + left_px) * channels
        end = (y * width + right_px) * channels
        if pixels[start:end] != reference[3][start:end]:
            return False
    return True


def region_error(
    present: tuple[int, int, int, bytes],
    reference: tuple[int, int, int, bytes],
    region: tuple[int, int, int, int, float],
) -> float:
    width, height, channels, pixels = present
    x0, y0, x1, y1, _ = region
    left = round(x0 * width / 1920)
    top = round(y0 * height / 1080)
    right = round(x1 * width / 1920)
    bottom = round(y1 * height / 1080)
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError(f"Invalid scaled region: {region}")
    error = 0
    for y in range(top, bottom):
        for x in range(left, right):
            offset = (y * width + x) * channels
            error += sum(abs(pixels[offset + c] - reference[3][offset + c]) for c in range(3))
    return error / ((right - left) * (bottom - top) * 3)


def check(manifest_path: Path, expect: str, regions: tuple[tuple[int, int, int, int, float], ...] = (),
          continuous_ready: bool = False, synchronized_static_gs: bool = False) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    captures = manifest["captures"]
    if not captures or len(captures) != manifest["capture_count"]:
        raise ValueError("Incomplete presentation sequence")
    log = Path(manifest["log"]).read_text(encoding="utf-8", errors="replace")
    if re.search(r"WebGPU validation error|Validation Error", log, re.IGNORECASE):
        raise ValueError("WebGPU validation error in runner log")
    states = {int(m[2]): m for m in COMPOSITE.finditer(log)}
    ratio = manifest["aspect"].split(":")
    aspect = int(ratio[0]) / int(ratio[1])
    ready_count = panel_count = exact_count = fallback_count = 0
    previous_frame = -1
    previous_present = previous_reference = None
    previous_ready = False
    for expected_index, capture in enumerate(captures):
        index = capture["index"]
        if index != expected_index:
            raise ValueError(f"Missing or out-of-order presentation {expected_index}")
        if capture["frame"] < previous_frame:
            raise ValueError(f"Presentation {index} moved backward in game frames")
        previous_frame = capture["frame"]
        state = states.get(index)
        if state is None:
            raise ValueError(f"Presentation {index} has no composite log")
        if int(state[1]) != capture["frame"]:
            raise ValueError(f"Presentation {index} has an incorrect frame label")
        present = png_pixels(Path(capture["present"]))
        reference = png_pixels(Path(capture["reference"]))
        if present[:3] != reference[:3]:
            raise ValueError(f"Presentation {index} dimensions differ")
        matches = present[3] == reference[3]
        if matches != capture["matches_gs"]:
            raise ValueError(f"Presentation {index} manifest comparison is stale")
        ready, flags = int(state[3]), int(state[4])
        if continuous_ready and ready_count and not ready:
            raise ValueError(f"Presentation {index} dropped the exact scene after it became ready")
        if (synchronized_static_gs and ready and previous_ready and
            reference[3] == previous_reference[3] and present[3] != previous_present[3]):
            raise ValueError(f"Presentation {index} changed native pixels while GS held its previous picture")
        if ready:
            ready_count += 1
            if not flags & FALLBACK:
                for region in regions:
                    error = region_error(present, reference, region)
                    if error > region[4]:
                        raise ValueError(f"Presentation {index} region {region[:4]} error {error:.3f} exceeds {region[4]}")
                    print(f"  region {region[:4]} MAE={error:.3f}")
            if flags & FALLBACK:
                fallback_count += 1
                if not matches:
                    raise ValueError(f"Presentation {index} should preserve the GS fallback")
            elif flags & PANEL:
                panel_count += 1
                bounds = tuple(int(state[n]) for n in range(6, 10))
                if not panel_equal(present, reference, bounds, aspect):
                    raise ValueError(f"Presentation {index} changes pixels inside the dialogue panel")
                if not matches:
                    exact_count += 1
            elif not matches:
                exact_count += 1
        elif not matches:
            raise ValueError(f"Presentation {index} differs from GS before the exact scene is ready")
        print(f"p{index} f{capture['frame']} ready={ready} flags={flags} GS={'equal' if matches else 'different'}")
        previous_present, previous_reference, previous_ready = present, reference, bool(ready)
    if ready_count == 0:
        raise ValueError("The sequence never presented a ready exact scene")
    if expect == "fallback" and fallback_count != ready_count:
        raise ValueError("A presentation left the expected GS fallback")
    if expect == "panel" and (panel_count != ready_count or exact_count == 0):
        raise ValueError("The dialogue panel or surrounding exact scene was not maintained")
    if expect == "exact" and (fallback_count or panel_count or exact_count == 0):
        raise ValueError("The ordinary exact scene was replaced by a fallback")
    print(f"PASS: {len(captures)} presentations; ready={ready_count}, fallback={fallback_count}, panel={panel_count}, exact={exact_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--expect", choices=("mixed", "fallback", "panel", "exact"), default="mixed")
    parser.add_argument("--roi", action="append", default=[],
                        help="x0,y0,x1,y1,max-MAE at a 1920x1080 reference size; checks every ready exact presentation")
    parser.add_argument("--continuous-ready", action="store_true",
                        help="reject a GS-only presentation after the exact scene first becomes ready")
    parser.add_argument("--synchronized-static-gs", action="store_true",
                        help="reject native image changes when the paired GS image repeats unchanged")
    args = parser.parse_args()
    regions = tuple(tuple(map(float, value.split(","))) for value in args.roi)
    if any(len(region) != 5 or any(int(v) != v for v in region[:4]) for region in regions):
        parser.error("--roi requires x0,y0,x1,y1,max-MAE")
    check(args.manifest, args.expect, regions, args.continuous_ready, args.synchronized_static_gs)
