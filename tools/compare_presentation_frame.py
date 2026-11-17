"""Measure a paired native/GS presentation using the repository PNG decoder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from check_presentation_capture import png_pixels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="capture_native_present.ps1 JSON for one frame")
    parser.add_argument("--max-mae", type=float, required=True, help="maximum sampled RGB mean absolute error")
    parser.add_argument("--roi", help="optional x0,y0,x1,y1 rectangle in output pixels")
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text(encoding="utf-8-sig"))
    present = png_pixels(Path(capture["screenshot"]))
    reference = png_pixels(Path(capture["reference_screenshot"]))
    if present[:3] != reference[:3]:
        raise ValueError("Native and GS image dimensions differ")
    if args.roi:
        try:
            x0, y0, x1, y1 = map(int, args.roi.split(","))
        except ValueError as exc:
            parser.error("--roi requires four comma-separated pixel coordinates")
        if not (0 <= x0 < x1 <= present[0] and 0 <= y0 < y1 <= present[1]):
            parser.error("--roi is outside the captured image")
        step = 2
    else:
        x0, y0, x1, y1 = 0, 0, present[0], present[1]
        step = 4
    # The threshold is a broad visual regression gate, not a claim of
    # pixel-perfect equivalence between renderers.
    count = 0
    error = 0
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            for color in range(3):
                present_at = (y * present[0] + x) * present[2] + color
                reference_at = (y * reference[0] + x) * reference[2] + color
                error += abs(present[3][present_at] - reference[3][reference_at])
                count += 1
    mae = error / count
    region = f" ROI={x0},{y0}-{x1},{y1}" if args.roi else ""
    frame = capture["captures"][0]["frame"] if capture["captures"] else capture["frame_captured"]
    print(f"frame {frame}{region} sampled RGB MAE={mae:.3f}; limit={args.max_mae:.3f}")
    if mae > args.max_mae:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
