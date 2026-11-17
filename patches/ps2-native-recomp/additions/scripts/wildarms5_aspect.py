"""Keep the owner's verified Wild ARMs 5 camera constants aspect-aware after translation."""

import hashlib
import pathlib
import struct
import sys


ELF_SHA256 = "eb54729c9cd29aea1168d4cc8ba6aabe6cbbc1f46b944bba9a4d2f1dd9965b33"
SITES = (
    (0x0015391C, 0x3C024400, "L_001538F0:", "0x44000000u"),
    (0x00153994, 0x3C0244C0, "L_00153980:", "0x44C00000u"),
    (0x00153A30, 0x3C023F80, "L_00153A2C:", "0x3F800000u"),
)


def main(elf_path: pathlib.Path, unit_path: pathlib.Path) -> None:
    elf = elf_path.read_bytes()
    digest = hashlib.sha256(elf).hexdigest()
    if digest != ELF_SHA256:
        raise SystemExit(f"Wild ARMs 5 aspect hook: unexpected boot ELF SHA-256 {digest}")
    phoff = struct.unpack_from("<I", elf, 28)[0]
    entsize = struct.unpack_from("<H", elf, 42)[0]
    count = struct.unpack_from("<H", elf, 44)[0]
    segments = [struct.unpack_from("<IIIIIIII", elf, phoff + i * entsize) for i in range(count)]
    for address, expected, _, _ in SITES:
        value = next(
            (struct.unpack_from("<I", elf, seg[1] + address - seg[2])[0]
             for seg in segments if seg[2] <= address < seg[2] + seg[4]), None
        )
        if value != expected:
            raise SystemExit(f"Wild ARMs 5 aspect hook: {address:08X} is {value!r}, expected {expected:08X}")

    source = unit_path.read_text(encoding="utf-8")
    if '#include "ps2/system.hpp"' not in source:
        source = source.replace('#include "units.hpp"\n', '#include "units.hpp"\n#include "ps2/system.hpp"\n', 1)
    for address, _, label, bits in SITES:
        start = source.find(label)
        if start < 0:
            raise SystemExit(f"Wild ARMs 5 aspect hook: missing generated label {label}")
        end = source.find("\nL_", start + len(label))
        if end < 0:
            raise SystemExit(f"Wild ARMs 5 aspect hook: missing end of {label}")
        block = source[start:end]
        old = f"c.r[2].sd[0] = static_cast<std::int32_t>({bits});"
        new = (f"c.r[2].sd[0] = static_cast<std::int32_t>("
               f"wildarms5_aspect_bits(0x{address:08X}u, {bits}));")
        if new in block:
            continue
        if block.count(old) != 1:
            raise SystemExit(f"Wild ARMs 5 aspect hook: {label} has {block.count(old)} matching constants")
        source = source[:start] + block.replace(old, new, 1) + source[end:]
    unit_path.write_text(source, encoding="utf-8")
    print("Wild ARMs 5 aspect hook: three verified camera constants are runtime-selectable")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: wildarms5_aspect.py boot-elf generated/unit_0014.cpp")
    main(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
