"""Check paired 4K Wild Arms V F9 menu captures with text sampling off/on.

Arguments are the original and enhanced first menu frame, followed by the
original and enhanced frame after scripted menu input. Only the Python standard
library is needed; PNG decoding is shared with the presentation checker.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from check_presentation_capture import png_pixels


HEADING = (470, 80, 1510, 240)
LIVE_TEXT = (
    (460, 65, 1510, 245),
    (470, 235, 2900, 380),
    (2360, 50, 3380, 1205),
    (500, 515, 1050, 670),
    (710, 650, 1240, 1050),
    (1510, 495, 2400, 1210),
)


def compare(before: Path, after: Path, allowed: tuple[tuple[int, int, int, int], ...]):
    a = png_pixels(before)
    b = png_pixels(after)
    if a[:3] != b[:3] or a[:2] != (3840, 2160):
        raise ValueError("Both captures must be matching 3840x2160 PNG images")
    width, height, channels, aa = a
    bb = b[3]
    changed = outside = 0
    bounds = [width, height, 0, 0]
    for y in range(height):
        row = y * width * channels
        boxes = [(x0, x1) for x0, y0, x1, y1 in allowed if y0 <= y < y1]
        for x in range(width):
            at = row + x * channels
            if aa[at : at + 3] == bb[at : at + 3]:
                continue
            changed += 1
            if not any(x0 <= x < x1 for x0, x1 in boxes):
                outside += 1
            bounds[0] = min(bounds[0], x)
            bounds[1] = min(bounds[1], y)
            bounds[2] = max(bounds[2], x)
            bounds[3] = max(bounds[3], y)
    return {"changed_pixels": changed, "outside_text_pixels": outside, "bounds": bounds}, a, b


def mean_rgb(image, box):
    width, _, channels, pixels = image
    x0, y0, x1, y1 = box
    total = 0
    for y in range(y0, y1):
        start = (y * width + x0) * channels
        stop = (y * width + x1) * channels
        row = pixels[start:stop]
        total += sum(row) if channels == 3 else sum(row[i] for i in range(len(row)) if i % 4 != 3)
    return total / ((x1 - x0) * (y1 - y0) * 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("first_original", "first_enhanced", "live_original", "live_enhanced"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    heading, a, b = compare(args.first_original, args.first_enhanced, (HEADING,))
    heading["mean_original"] = mean_rgb(a, HEADING)
    heading["mean_enhanced"] = mean_rgb(b, HEADING)
    live, _, _ = compare(args.live_original, args.live_enhanced, LIVE_TEXT)
    result = {"cached_heading": heading, "live_text": live}
    print(json.dumps(result, indent=2))
    if heading["changed_pixels"] < 10000 or heading["outside_text_pixels"]:
        raise SystemExit("Cached heading changed outside its bounds or did not improve")
    if abs(heading["mean_enhanced"] - heading["mean_original"]) > 1.0:
        raise SystemExit("Cached heading brightness changed too much")
    if live["changed_pixels"] < 100000 or live["outside_text_pixels"]:
        raise SystemExit("Live text changed outside the expected menu text regions")


if __name__ == "__main__":
    main()
