"""Measure regional brightness and native/GS error across paired presentations."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from check_presentation_capture import png_pixels


def region_stats(native, reference, bounds, stride):
    width, height, channels, pixels = native
    if reference[:3] != native[:3]:
        raise ValueError("Paired image dimensions differ")
    other = reference[3]
    x0, y0, x1, y1 = bounds
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f"Region {bounds} is outside {width}x{height}")
    native_sum = reference_sum = error_sum = count = 0
    for y in range(y0, y1, stride):
        for x in range(x0, x1, stride):
            offset = (y * width + x) * channels
            for color in range(3):
                a = pixels[offset + color]
                b = other[offset + color]
                native_sum += a
                reference_sum += b
                error_sum += abs(a - b)
                count += 1
    return native_sum / count, reference_sum / count, error_sum / count


def temporal_stats(current, previous, bounds, stride):
    if previous is None:
        return 0.0, 0.0
    width, _, channels, pixels = current
    older = previous[3]
    x0, y0, x1, y1 = bounds
    step_sum = edge_sum = count = 0
    for y in range(y0, y1, stride):
        for x in range(x0, x1 - 1, stride):
            offset = (y * width + x) * channels
            for color in range(3):
                step_sum += abs(pixels[offset + color] - older[offset + color])
                edge_sum += abs(pixels[offset + color] - pixels[offset + channels + color])
                count += 1
    return step_sum / count, edge_sum / count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--roi", action="append", required=True,
                        help="name,x0,y0,x1,y1 in output pixels")
    parser.add_argument("--stride", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.stride < 1:
        parser.error("--stride must be positive")
    regions = {}
    for spec in args.roi:
        name, *values = spec.split(",")
        if len(values) != 4 or not name:
            parser.error(f"Invalid region: {spec}")
        regions[name] = tuple(map(int, values))
    rows = []
    previous_native = previous_reference = None
    for manifest_path in args.manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        for item in manifest["captures"]:
            native = png_pixels(Path(item["present"]))
            reference = png_pixels(Path(item["reference"]))
            row = {"manifest": str(manifest_path), "index": item["index"],
                   "frame": item["frame"], "matches_gs": item["matches_gs"]}
            for name, bounds in regions.items():
                a, b, error = region_stats(native, reference, bounds, args.stride)
                row[f"{name}_native"] = round(a, 3)
                row[f"{name}_gs"] = round(b, 3)
                row[f"{name}_mae"] = round(error, 3)
                if previous_native is not None:
                    native_step, native_edge = temporal_stats(native, previous_native, bounds, args.stride)
                    gs_step, gs_edge = temporal_stats(reference, previous_reference, bounds, args.stride)
                    row[f"{name}_native_step"] = round(native_step, 3)
                    row[f"{name}_gs_step"] = round(gs_step, 3)
                    row[f"{name}_native_edge"] = round(native_edge, 3)
                    row[f"{name}_gs_edge"] = round(gs_edge, 3)
                else:
                    for suffix in ("native_step", "gs_step", "native_edge", "gs_edge"):
                        row[f"{name}_{suffix}"] = ""
            rows.append(row)
            previous_native, previous_reference = native, reference
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Measured {len(rows)} paired frames: {args.output}")


if __name__ == "__main__":
    main()
