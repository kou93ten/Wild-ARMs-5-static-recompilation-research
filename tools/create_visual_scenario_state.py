#!/usr/bin/env python3
"""Create an isolated diagnostic native state at a verified W01 field position.

This patches only the three player-position floats in a copied PS2STATE v9.
The caller must supply the observed source position and its SHA-256. A visual
reload and movement check are still required before calling the result valid.
"""

import argparse
import hashlib
import json
import math
import shutil
import struct
from pathlib import Path


RAM_START = 20 + 1456
POSITION_IN_RAM = 0x44BEC0
POSITION_AT = RAM_START + POSITION_IN_RAM
RAM_SIZE = 32 << 20
STATE_VERSION = 9


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def triplet(values: list[str]) -> tuple[float, float, float]:
    numbers = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in numbers):
        raise ValueError("positions must be finite numbers")
    return numbers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--card", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--expect-position", required=True, nargs=3, metavar=("X", "Y", "Z"))
    parser.add_argument("--position", required=True, nargs=3, metavar=("X", "Y", "Z"))
    parser.add_argument("--name", required=True)
    parser.add_argument("--source-frame", required=True, type=int)
    args = parser.parse_args()

    source = args.source.resolve(strict=True)
    card = args.card.resolve(strict=True)
    output = args.output_dir.resolve()
    project = Path(__file__).resolve().parent.parent
    scenario_root = (project / "work" / "visual-scenarios").resolve()
    if not output.is_relative_to(scenario_root) or output == scenario_root:
        parser.error(f"output directory must be beneath {scenario_root}")
    if output.exists():
        parser.error(f"output directory already exists: {output}")
    if args.source_frame < 0:
        parser.error("source frame must be nonnegative")
    if source == card:
        parser.error("state and card paths must differ")
    if digest(source) != args.source_sha256.lower():
        parser.error("source SHA-256 differs from --source-sha256")
    raw = bytearray(source.read_bytes())
    if len(raw) < RAM_START + RAM_SIZE or raw[:8] != b"PS2STATE":
        parser.error("source is not a complete native PS2STATE")
    version = struct.unpack_from("<I", raw, 8)[0]
    if version != STATE_VERSION:
        parser.error(f"expected PS2STATE v{STATE_VERSION}, found v{version}")
    original = struct.unpack_from("<fff", raw, POSITION_AT)
    expected = triplet(args.expect_position)
    if any(abs(actual - wanted) > 0.01 for actual, wanted in zip(original, expected)):
        parser.error(f"source position {original} differs from expected {expected}")
    position = triplet(args.position)
    if any(abs(value) > 100000 for value in position):
        parser.error("position outside the diagnostic W01 coordinate range")
    struct.pack_into("<fff", raw, POSITION_AT, *position)

    output.mkdir(parents=True)
    state_out = output / "scenario.p2state"
    card_out = output / "card.ps2"
    state_out.write_bytes(raw)
    shutil.copyfile(card, card_out)
    manifest = {
        "name": args.name,
        "status": "unvalidated; reload, inspect, and test movement",
        "method": "PS2STATE v9 W01 player-position float patch",
        "source_frame": args.source_frame,
        "source_state": str(source),
        "source_sha256": args.source_sha256.lower(),
        "source_card": str(card),
        "source_card_sha256": digest(card),
        "source_position_xyz": original,
        "requested_position_xyz": position,
        "state": str(state_out),
        "state_sha256": digest(state_out),
        "card": str(card_out),
        "card_sha256": digest(card_out),
        "patched_ram_offset": hex(POSITION_IN_RAM),
        "patched_file_offset": hex(POSITION_AT),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(output / "manifest.json")


if __name__ == "__main__":
    main()
