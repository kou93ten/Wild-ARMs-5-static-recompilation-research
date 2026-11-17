#!/usr/bin/env python3
"""Summarize a PCSX2 2.8.2 input recording without changing its raw data."""

import argparse
import json
import struct
from collections import Counter
from pathlib import Path


HEADER = 561  # InputRecordingFileHeader: version + 50 + 255 + 255 bytes.
DATA = HEADER + 4 + 4 + 1  # frame count, rerecord count, savestate flag.
FRAME = 36  # Two 18-byte pad records.
BUTTONS = (
    (0, 0x80, "dleft"), (0, 0x40, "ddown"), (0, 0x20, "dright"),
    (0, 0x10, "dup"), (0, 0x08, "start"), (0, 0x04, "r3"),
    (0, 0x02, "l3"), (0, 0x01, "select"),
    (1, 0x80, "square"), (1, 0x40, "cross"), (1, 0x20, "circle"),
    (1, 0x10, "triangle"), (1, 0x08, "r1"), (1, 0x04, "l1"),
    (1, 0x02, "r2"), (1, 0x01, "l2"),
)


def cstr(blob):
    return blob.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def inspect(path):
    with path.open("rb") as stream:
        header = stream.read(DATA)
        if len(header) != DATA or header[0] != 1:
            raise ValueError("expected PCSX2 input recording version 1")
        total, rerecords = struct.unpack_from("<II", header, HEADER)
        info = {
            "file": str(path.resolve()),
            "emulator": cstr(header[1:51]),
            "author": cstr(header[51:306]),
            "game": cstr(header[306:561]),
            "frames": total,
            "rerecords": rerecords,
            "starts_from_state": bool(header[HEADER + 8]),
            "controllers": [],
        }
        counters = [Counter(), Counter()]
        stick_min = [[255] * 4 for _ in range(2)]
        stick_max = [[0] * 4 for _ in range(2)]
        unpopulated = [0, 0]
        for frame in range(total):
            pair = stream.read(FRAME)
            if len(pair) != FRAME:
                raise ValueError(f"movie ends within frame {frame} of {total}")
            for port in range(2):
                pad = pair[port * 18:(port + 1) * 18]
                # PCSX2 leaves an unused controller port as 18 zero bytes.
                if not any(pad):
                    unpopulated[port] += 1
                    continue
                counts = counters[port]
                for byte, mask, name in BUTTONS:
                    if not pad[byte] & mask:
                        counts[name] += 1
                for axis, name in enumerate(("rx", "ry", "lx", "ly"), start=2):
                    value = pad[axis]
                    stick_min[port][axis - 2] = min(stick_min[port][axis - 2], value)
                    stick_max[port][axis - 2] = max(stick_max[port][axis - 2], value)
                    if abs(value - 127) > 12:
                        counts[name] += 1
        for port in range(2):
            info["controllers"].append({
                "port": port + 1,
                "unpopulated_frames": unpopulated[port],
                "active_frames": dict(sorted(counters[port].items())),
                "stick_ranges": ({name: [stick_min[port][i], stick_max[port][i]]
                                  for i, name in enumerate(("rx", "ry", "lx", "ly"))}
                                 if unpopulated[port] < total else None),
            })
        return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("movie", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect(args.movie)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
