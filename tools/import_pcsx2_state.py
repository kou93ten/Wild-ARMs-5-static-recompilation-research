#!/usr/bin/env python3
"""Import a PCSX2 2.8.2 save state as a native build/debug snapshot.

This imports EE RAM and code windows. It does not resume the native machine:
PCSX2's device, kernel, IOP, GS, and SPU2 state has a different layout.
"""

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path


EXPECTED_FILES = {
    "PCSX2 Savestate Version.id": 36,
    "PCSX2 Internal Structures.dat": None,
    "eeMemory.bin": 32 << 20,
    "iopMemory.bin": 2 << 20,
    "eeHwRegs.bin": 1 << 16,
    "iopHwRegs.bin": 1 << 16,
    "Scratchpad.bin": 1 << 14,
    "vu0Memory.bin": 1 << 12,
    "vu1Memory.bin": 1 << 14,
    "vu0MicroMem.bin": 1 << 12,
    "vu1MicroMem.bin": 1 << 14,
    "SPU2.bin": None,
    "USB.bin": None,
    "PAD.bin": None,
    "GS.bin": None,
    "Screenshot.png": None,
}


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_cpu(internals, ram, expected_serial):
    """Read the verified fixed prefix of PCSX2 v2.8.2's Freeze(cpuRegs)."""
    tag = b"cpuRegs".ljust(32, b"\0")
    at = internals.find(tag)
    if at < 0 or internals.find(tag, at + 1) >= 0:
        raise ValueError("missing or ambiguous PCSX2 cpuRegs tag")
    prefix = internals[:at]
    serials = re.findall(rb"cdrom0:\\([A-Z]{4}_[0-9]{3}\.[0-9]{2});[0-9]", prefix)
    serial = serials[-1].decode("ascii") if serials else None
    if serial != expected_serial:
        raise ValueError(f"state disc {serial!r} does not match {expected_serial!r}")

    # PCSX2 R5900.h v2.8.2: 32 x 16-byte GPR, 16-byte HI/LO,
    # 32 x 4-byte CP0, then sa, IsDelaySlot, pc, code.
    cpu = at + len(tag)
    gpr = [struct.unpack_from("<QQ", internals, cpu + i * 16) for i in range(32)]
    hi = struct.unpack_from("<QQ", internals, cpu + 512)
    lo = struct.unpack_from("<QQ", internals, cpu + 528)
    cp0 = struct.unpack_from("<32I", internals, cpu + 544)
    sa, delay_slot, pc, code = struct.unpack_from("<4I", internals, cpu + 672)
    if pc & 3 or pc >= len(ram) or not any(ram[pc:pc + 16]):
        raise ValueError(f"EE PC 0x{pc:08X} is not executable-looking EE RAM")
    return {
        "disc_serial": serial,
        "ee_pc": f"0x{pc:08X}",
        "ee_pc_instruction": f"0x{struct.unpack_from('<I', ram, pc)[0]:08X}",
        "ee_code_register": f"0x{code:08X}",
        "ee_delay_slot": delay_slot,
        "ee_sa": sa,
        "ee_gpr_low64": [f"0x{r[0]:016X}" for r in gpr],
        "ee_hi": [f"0x{x:016X}" for x in hi],
        "ee_lo": [f"0x{x:016X}" for x in lo],
        "ee_cp0": [f"0x{x:08X}" for x in cp0],
    }


def load_windows_module(native_root):
    module_path = native_root / "scripts" / "windows.py"
    spec = importlib.util.spec_from_file_location("native_windows", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path, help="PCSX2 .p2s archive")
    parser.add_argument("elf", type=Path, help="the matching PS2 ELF")
    parser.add_argument("native_root", type=Path, help="ps2-native-recomp checkout")
    parser.add_argument("output", type=Path, help="ignored debug output directory")
    parser.add_argument("--serial", default="SLUS_216.15")
    parser.add_argument("--sevenzip", type=Path, default=Path(r"C:\Program Files\7-Zip\7z.exe"))
    parser.add_argument("--install-overlays", action="store_true",
                        help="copy new code images into games/wildarms5/overlays")
    args = parser.parse_args()

    state, output = args.state.resolve(), args.output.resolve()
    if not state.is_file() or not args.elf.is_file():
        parser.error("state and ELF must exist")
    if not args.sevenzip.is_file() and not shutil.which(str(args.sevenzip)):
        parser.error("7-Zip is required for PCSX2 ZIP/Zstd states; set --sevenzip")
    if output == state.parent or state.is_relative_to(output):
        parser.error("output must be a separate directory")

    with zipfile.ZipFile(state) as archive:
        members = archive.infolist()
        names = [m.filename for m in members]
        if len(names) != len(set(names)) or set(names) != set(EXPECTED_FILES):
            raise ValueError("unexpected or duplicate PCSX2 archive entries")
        for member in members:
            wanted = EXPECTED_FILES[member.filename]
            if wanted is not None and member.file_size != wanted:
                raise ValueError(f"incorrect size for {member.filename}: {member.file_size}")
            if member.file_size > 64 << 20:
                raise ValueError(f"archive entry too large: {member.filename}")

    extracted = output / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(args.sevenzip), "x", "-y", f"-o{extracted}", str(state)],
                   check=True, stdout=subprocess.DEVNULL)
    version_bytes = (extracted / "PCSX2 Savestate Version.id").read_bytes()
    version, = struct.unpack_from("<I", version_bytes)
    build = version_bytes[4:20].split(b"\0", 1)[0].decode("ascii")
    if version != 0x9A590000 or build != "v2.8.2":
        raise ValueError(f"unsupported PCSX2 state version 0x{version:08X} ({build})")
    ram = (extracted / "eeMemory.bin").read_bytes()
    cpu = read_cpu((extracted / "PCSX2 Internal Structures.dat").read_bytes(),
                   ram, args.serial)

    windows = load_windows_module(args.native_root)
    window_dir = output / "overlays"
    window_dir.mkdir(exist_ok=True)
    elf = args.elf.read_bytes()
    window_bases = {base for base, _ in windows.windows(elf)}
    if not window_bases:
        raise ValueError("ELF has no native code windows")
    captured = []
    for base in sorted(window_bases):
        size = windows.extent(ram, base)
        blob = ram[base:base + size]
        if not any(blob):
            continue
        import binascii
        name = f"{base:08X}_{binascii.crc32(blob) & 0xffffffff:08X}.bin"
        path = window_dir / name
        path.write_bytes(blob)
        captured.append((base, path, blob))

    installed_dir = args.native_root / "games" / "wildarms5" / "overlays"
    entries_path = args.native_root / "games" / "wildarms5" / "entries.txt"
    entry_tokens = ([token for line in entries_path.read_text().splitlines()
                     for token in line.split("#", 1)[0].split()]
                    if entries_path.is_file() else [])
    report = []
    for base, path, blob in captured:
        existing = sorted(installed_dir.glob(f"{base:08X}_*.bin"))
        entry_offsets = [offset for token in entry_tokens
                         if (offset := windows.missed_offset(token, base, len(blob))) is not None]
        match = next((p for p in existing
                      if (saved := p.read_bytes()) and windows.near_duplicate(blob, saved)
                      and windows.same_entries(saved, blob, entry_offsets)), None)
        status = "already_present" if match and match.name == path.name else (
            "similar_code" if match else "new_code")
        installed = False
        # The first window includes its later 16 KiB units; a nested window
        # with the same code adds no separate translated entry points.
        if args.install_overlays and status == "new_code":
            installed_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, installed_dir / path.name)
            installed = True
        report.append({"image": path.name, "bytes": len(blob), "status": status,
                       "similar_to": match.name if match else None, "installed": installed})

    result = {
        "source": str(state), "sha256": sha256(state), "pcsx2_build": build,
        "state_version": f"0x{version:08X}", "cpu": cpu,
        "ee_ram_sha256": sha256(extracted / "eeMemory.bin"),
        "overlays": report,
        "scope": "EE memory and code images for build/debug; native execution resume is unsupported",
    }
    (output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {build} {cpu['disc_serial']} state; EE PC {cpu['ee_pc']}")
    for image in report:
        print(f"  {image['image']}: {image['status']}" +
              (" (installed)" if image["installed"] else ""))
    print(f"Manifest: {output / 'manifest.json'}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        sys.exit(f"import failed: {exc}")
