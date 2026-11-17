"""Inventory an ISO9660 PS2 disc or extract one file, using only Python's stdlib.

Examples:
  python tools/disc_inventory.py inventory WildArmsV.iso -o analysis/disc_manifest.json
  python tools/disc_inventory.py extract WildArmsV.iso /SLUS_216.15 boot.elf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import BinaryIO

SECTOR = 2048
CHUNK = 1024 * 1024


def read_at(stream: BinaryIO, offset: int, size: int) -> bytes:
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError(f"Short read at ISO offset {offset}, wanted {size} bytes")
    return data


def both_endian_u32(data: bytes, offset: int) -> int:
    little = int.from_bytes(data[offset : offset + 4], "little")
    big = int.from_bytes(data[offset + 4 : offset + 8], "big")
    if little != big:
        raise ValueError(f"ISO9660 endian mismatch at record offset {offset}")
    return little


def record(data: bytes) -> dict:
    if len(data) < 34 or data[0] != len(data):
        raise ValueError("Invalid ISO9660 directory record")
    name_len = data[32]
    if 33 + name_len > len(data):
        raise ValueError("Truncated ISO9660 identifier")
    raw_name = data[33 : 33 + name_len]
    return {
        "name": raw_name.decode("ascii", errors="replace"),
        "extent_sector": both_endian_u32(data, 2),
        "size": both_endian_u32(data, 10),
        "directory": bool(data[25] & 2),
    }


def records_in_directory(stream: BinaryIO, extent: int, size: int):
    data = read_at(stream, extent * SECTOR, size)
    cursor = 0
    while cursor < size:
        length = data[cursor]
        if not length:
            cursor = ((cursor // SECTOR) + 1) * SECTOR
            continue
        if cursor + length > size or (cursor % SECTOR) + length > SECTOR:
            raise ValueError(f"Directory record crosses a boundary at {cursor}")
        item = record(data[cursor : cursor + length])
        if item["name"] not in ("\x00", "\x01"):
            yield item
        cursor += length


def digest_extent(stream: BinaryIO, offset: int, size: int) -> str:
    stream.seek(offset)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = stream.read(min(CHUNK, remaining))
        if not block:
            raise ValueError(f"Short read while hashing offset {offset}")
        digest.update(block)
        remaining -= len(block)
    return digest.hexdigest()


def parse_elf32(data: bytes) -> dict:
    if len(data) < 52 or data[:4] != b"\x7fELF" or data[4:6] != b"\x01\x01":
        raise ValueError("Expected a little-endian ELF32 boot executable")
    machine = struct.unpack_from("<H", data, 18)[0]
    entry, phoff, shoff = struct.unpack_from("<III", data, 24)
    phentsize, phnum, shentsize, shnum, shstrndx = struct.unpack_from("<HHHHH", data, 42)
    if phoff + phentsize * phnum > len(data) or shoff + shentsize * shnum > len(data):
        raise ValueError("ELF header tables extend beyond the boot file")
    if phentsize < 32 or shentsize < 40 or shstrndx >= shnum:
        raise ValueError("Invalid ELF header table metadata")

    sections = []
    raw_sections = [
        struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize)
        for i in range(shnum)
    ]
    strings = raw_sections[shstrndx]
    if strings[4] + strings[5] > len(data):
        raise ValueError("ELF section-name table extends beyond the boot file")
    names = data[strings[4] : strings[4] + strings[5]]
    for index, section in enumerate(raw_sections):
        name_offset = section[0]
        name = (
            names[name_offset:].split(b"\0", 1)[0].decode("ascii", "replace")
            if name_offset < len(names)
            else ""
        )
        sections.append(
            {
                "index": index,
                "name": name,
                "type": section[1],
                "virtual_address": section[3],
                "file_offset": section[4],
                "size": section[5],
            }
        )

    segments = []
    for index in range(phnum):
        kind, offset, vaddr, _paddr, filesz, memsz, flags, align = struct.unpack_from(
            "<IIIIIIII", data, phoff + index * phentsize
        )
        if filesz and offset + filesz > len(data):
            raise ValueError(f"ELF segment {index} extends beyond the boot file")
        segments.append(
            {
                "index": index,
                "type": kind,
                "file_offset": offset,
                "virtual_address": vaddr,
                "file_size": filesz,
                "memory_size": memsz,
                "flags": flags,
                "alignment": align,
            }
        )

    return {
        "machine": machine,
        "entry_point": entry,
        "program_header_count": phnum,
        "section_header_count": shnum,
        "sections": sections,
        "segments": segments,
    }


def scan_disc(iso: Path) -> dict:
    image_size = iso.stat().st_size
    with iso.open("rb") as stream:
        pvd = read_at(stream, 16 * SECTOR, SECTOR)
        if pvd[:7] != b"\x01CD001\x01":
            raise ValueError("No ISO9660 primary volume descriptor at sector 16")
        root = record(pvd[156 : 156 + pvd[156]])
        if not root["directory"]:
            raise ValueError("ISO root record is not a directory")

        files = []
        directories = []
        seen_directories = set()

        def walk(prefix: str, extent: int, size: int) -> None:
            key = (extent, size)
            if key in seen_directories:
                raise ValueError(f"Repeated directory extent at {prefix}")
            seen_directories.add(key)
            for item in records_in_directory(stream, extent, size):
                path = prefix + "/" + item["name"]
                if item["extent_sector"] * SECTOR + item["size"] > image_size:
                    raise ValueError(f"File extends beyond image: {path}")
                info = {
                    "path": path,
                    "extent_sector": item["extent_sector"],
                    "size": item["size"],
                }
                if item["directory"]:
                    directories.append(info)
                    walk(path, item["extent_sector"], item["size"])
                else:
                    info["sha256"] = digest_extent(
                        stream, item["extent_sector"] * SECTOR, item["size"]
                    )
                    files.append(info)

        walk("", root["extent_sector"], root["size"])
        files.sort(key=lambda item: item["path"])
        directories.sort(key=lambda item: item["path"])
        boot = next((item for item in files if item["path"] == "/SLUS_216.15;1"), None)
        elf = None
        if boot:
            data = read_at(stream, boot["extent_sector"] * SECTOR, boot["size"])
            elf = parse_elf32(data)

        return {
            "schema_version": 1,
            "image": {"path": str(iso.resolve()), "size": image_size, "sha256": digest_extent(stream, 0, image_size)},
            "iso9660": {
                "volume_id": pvd[40:72].decode("ascii", "replace").strip(" \0"),
                "root_extent_sector": root["extent_sector"],
                "root_size": root["size"],
            },
            "directories": directories,
            "files": files,
            "boot_elf": {"path": boot["path"], "sha256": boot["sha256"], **elf} if boot else None,
        }


def extract_file(iso: Path, source: str, output: Path, overwrite: bool) -> None:
    manifest = scan_disc(iso)
    item = next((entry for entry in manifest["files"] if entry["path"] == source), None)
    if item is None:
        raise ValueError(f"No disc file with exact path {source!r}")
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output exists; pass --force to replace it: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with iso.open("rb") as stream, output.open("wb") as destination:
        stream.seek(item["extent_sector"] * SECTOR)
        remaining = item["size"]
        while remaining:
            block = stream.read(min(CHUNK, remaining))
            if not block:
                raise ValueError("Disc file ended unexpectedly")
            destination.write(block)
            remaining -= len(block)
    with output.open("rb") as check:
        digest = hashlib.file_digest(check, "sha256").hexdigest()
    if digest != item["sha256"]:
        raise ValueError("Extracted file hash does not match disc manifest")
    print(f"Extracted {source} -> {output} ({item['size']} bytes, SHA-256 {digest})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory", help="write a deterministic JSON inventory")
    inventory.add_argument("iso", type=Path)
    inventory.add_argument("-o", "--output", type=Path, required=True)
    extract = commands.add_parser("extract", help="extract one exact ISO path")
    extract.add_argument("iso", type=Path)
    extract.add_argument("source", help="exact path from the inventory, including ;1")
    extract.add_argument("output", type=Path)
    extract.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "inventory":
        result = scan_disc(args.iso)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {len(result['files'])} files and {len(result['directories'])} directories to {args.output}")
        print(f"Image SHA-256: {result['image']['sha256']}")
    else:
        extract_file(args.iso, args.source, args.output, args.force)


if __name__ == "__main__":
    main()
