"""Extract Wild ARMs 5's early constructor pointers for static recompilation.

This is specific to the SLUS-21615 boot ELF recorded in analysis/disc_manifest.json.
The first headless native runs reached an indirect-call loop at 0x00100f64 and
halted on one constructor per exploration round. The contiguous pointer table
begins at 0x003d42c0 and ends at the first zero word.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path

EXPECTED_ELF_SHA256 = "eb54729c9cd29aea1168d4cc8ba6aabe6cbbc1f46b944bba9a4d2f1dd9965b33"
TABLE_ADDRESS = 0x003D42C0
EXPECTED_FIRST = 0x003D0C00
EXPECTED_LAST = 0x003D41D0
EXPECTED_COUNT = 27


def file_offset_for(data: bytes, address: int) -> int:
    phoff = struct.unpack_from("<I", data, 28)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    for index in range(phnum):
        kind, offset, vaddr, _paddr, filesz, _memsz, _flags, _align = struct.unpack_from(
            "<IIIIIIII", data, phoff + index * phentsize
        )
        if kind == 1 and vaddr <= address < vaddr + filesz:
            return offset + address - vaddr
    raise ValueError(f"Address 0x{address:08x} is not in a file-backed load segment")


def constructor_entries(elf: Path) -> list[int]:
    data = elf.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != EXPECTED_ELF_SHA256:
        raise ValueError(f"Wrong boot ELF: SHA-256 {digest}")
    if data[:6] != b"\x7fELF\x01\x01":
        raise ValueError("Expected little-endian ELF32")
    table_offset = file_offset_for(data, TABLE_ADDRESS)
    entries = []
    for index in range(128):
        address = struct.unpack_from("<I", data, table_offset + index * 4)[0]
        if address == 0:
            break
        file_offset_for(data, address)
        entries.append(address)
    if (
        len(entries) != EXPECTED_COUNT
        or entries[0] != EXPECTED_FIRST
        or entries[-1] != EXPECTED_LAST
        or len(set(entries)) != len(entries)
    ):
        raise ValueError("Constructor table differs from the inspected SLUS-21615 build")
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()
    entries = constructor_entries(args.elf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(f"{address:x}\n" for address in entries), encoding="ascii")
    print(f"Wrote {len(entries)} constructor entries to {args.output}")


if __name__ == "__main__":
    main()
