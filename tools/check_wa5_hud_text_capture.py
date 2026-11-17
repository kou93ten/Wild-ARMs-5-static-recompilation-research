"""Compare the WA5 HUD enhancement against the original across live frames."""

from __future__ import annotations

import argparse
from pathlib import Path

from check_presentation_capture import png_pixels


# Coordinates are in the 1920x1080 presentation, not GS coordinates.
REGIONS = {
    "field": ((270, 80, 355, 180), (1270, 750, 1660, 1040), (700, 300, 900, 500)),
    "battle": ((1390, 780, 1680, 1080), (1390, 780, 1680, 1080), (800, 300, 1000, 500)),
    "pause": ((815, 505, 1090, 585), (815, 505, 1090, 585), (1120, 520, 1260, 650)),
    "equip": ((535, 995, 690, 1055), (970, 995, 1120, 1055), (700, 600, 900, 800)),
}


def frames(directory: Path) -> dict[int, Path]:
    found = {}
    for path in directory.glob("present-f*-p*.png"):
        index = int(path.stem.rsplit("-p", 1)[1])
        found[index] = path
    return found


def changed(a: tuple, b: tuple, bounds: tuple[int, int, int, int]) -> int:
    width, height, channels, pixels = a
    if a[:3] != b[:3] or (width, height) != (1920, 1080):
        raise ValueError("Capture dimensions differ from 1920x1080")
    x0, y0, x1, y1 = bounds
    other = b[3]
    count = 0
    for y in range(y0, y1):
        offset = (y * width + x0) * channels
        end = (y * width + x1) * channels
        for i in range(offset, end, channels):
            count += pixels[i:i + channels] != other[i:i + channels]
    return count


def check(kind: str, original: Path, enhanced: Path) -> None:
    a_frames, b_frames = frames(original), frames(enhanced)
    indices = sorted(set(a_frames) & set(b_frames))
    if kind == "pause":
        indices = [i for i in indices if i >= 3]
    else:
        indices = [i for i in indices if i >= 2]
    if len(indices) < (15 if kind == "pause" else 5):
        raise ValueError(f"{kind}: insufficient matching live frames: {indices}")
    first, second, scene = REGIONS[kind]
    minimum = {"field": (200, 1000), "battle": (4000, 4000), "pause": (1000, 1000), "equip": (1000, 1000)}[kind]
    changes = []
    for index in indices:
        a = png_pixels(a_frames[index])
        b = png_pixels(b_frames[index])
        primary = changed(a, b, first)
        secondary = primary if second == first else changed(a, b, second)
        background = changed(a, b, scene)
        if kind == "pause":
            # The same recent F9 city state also shows the button symbols and
            # compass while PAUSE is up; verify those two paths here as well.
            button = changed(a, b, REGIONS["field"][0])
            compass = changed(a, b, REGIONS["field"][1])
            if button < 200 or compass < 1000:
                raise AssertionError(f"{kind} p{index}: button={button}, compass={compass}")
        if kind == "equip":
            left_shoulder = changed(a, b, (970, 995, 1050, 1055))
            right_shoulder = changed(a, b, (1040, 995, 1120, 1055))
            if left_shoulder < 500 or right_shoulder < 500:
                raise AssertionError(
                    f"{kind} p{index}: L1={left_shoulder}, R1={right_shoulder}"
                )
        if primary < minimum[0] or secondary < minimum[1] or background:
            raise AssertionError(
                f"{kind} p{index}: primary={primary}, secondary={secondary}, "
                f"scene={background} (minimum {minimum})"
            )
        changes.append((primary, secondary))
    print(
        f"{kind}: {len(indices)} consecutive presentations passed; "
        f"primary {min(x for x, _ in changes)}..{max(x for x, _ in changes)} changed pixels, "
        f"secondary {min(y for _, y in changes)}..{max(y for _, y in changes)}; "
        "background unchanged"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=REGIONS)
    parser.add_argument("original", type=Path)
    parser.add_argument("enhanced", type=Path)
    args = parser.parse_args()
    check(args.kind, args.original, args.enhanced)
