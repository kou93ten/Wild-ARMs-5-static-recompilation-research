"""Check that battle command glyph enhancement survives the texture ring wrap."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from check_presentation_capture import png_pixels


REGIONS = {
    "Command": (270, 125, 520, 178),
    "Attack": (270, 230, 530, 290),
}


def captures(directory: Path):
    found = {}
    for path in directory.glob("present-f*-p*.png"):
        match = re.fullmatch(r"present-f(\d+)-p\d+\.png", path.name)
        if match:
            found[int(match[1])] = path
    return found


def region_mae(a, b, region):
    width, height, channels, before = a
    if b[:3] != a[:3] or (width, height) != (1920, 1080):
        raise ValueError("Battle captures must be matching 1920x1080 PNGs")
    after = b[3]
    x0, y0, x1, y1 = region
    total = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            at = (y * width + x) * channels
            total += sum(abs(before[at + color] - after[at + color]) for color in range(3))
    return total / ((x1 - x0) * (y1 - y0) * 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("enhanced", type=Path)
    args = parser.parse_args()
    original = captures(args.original)
    enhanced = captures(args.enhanced)
    frames = sorted(original.keys() & enhanced.keys())
    # The first saved presentation may come from the loaded state's queued GS
    # work, before this run's shader setting has reached the display circuit.
    frames = frames[1:]
    if len(frames) < 10:
        raise SystemExit("Too few matching battle frames")
    for frame in frames:
        before = png_pixels(original[frame])
        after = png_pixels(enhanced[frame])
        errors = {name: region_mae(before, after, box) for name, box in REGIONS.items()}
        print(f"{frame}: " + ", ".join(f"{name} MAE {error:.2f}" for name, error in errors.items()), flush=True)
        if any(error < 5.0 for error in errors.values()):
            raise SystemExit(f"Battle text lost its enhancement in frame {frame}")


if __name__ == "__main__":
    main()
