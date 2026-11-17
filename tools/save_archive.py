#!/usr/bin/env python3
"""Convert Wild Arms V memory-card saves to/from a portable .wavsave ZIP.

The archive contains individual save files and directory-entry metadata, not
an 8 MB card image. A temporary PS2 card is assembled for the current runtime.
"""

import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "external" / "ps2-native-recomp" / "scripts"))
import mcformat as mc

FORMAT = "wild-arms-v-native-save"
VERSION = 1
GAME_ID = "SLUS-21615"
ENTRY_SIZE = mc.DIRENT_LENGTH
ENTRY_FORMAT = "<HHL8sLL8sL28x448s"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    if not name or name in (".", "..") or len(name.encode("latin-1")) > 127:
        raise ValueError(f"invalid memory-card name: {name!r}")
    if any(ch in name for ch in "/\\:") or any(ord(ch) < 32 for ch in name):
        raise ValueError(f"unsafe memory-card name: {name!r}")
    return name


def entry_name(entry):
    if len(entry) != ENTRY_SIZE:
        raise ValueError("directory entry must be 512 bytes")
    return struct.unpack(ENTRY_FORMAT, entry)[-1].split(b"\0", 1)[0].decode("latin-1")


def entry_fields(entry):
    mode, _, length, _, cluster, _, _, _, _ = struct.unpack(ENTRY_FORMAT, entry)
    return mode, length, cluster


def encode_entry(entry):
    return base64.b64encode(entry).decode("ascii")


def decode_entry(encoded, expected_name):
    entry = base64.b64decode(encoded, validate=True)
    if entry_name(entry) != expected_name:
        raise ValueError(f"entry name does not match {expected_name!r}")
    return entry


def validate_card(path):
    with contextlib.redirect_stdout(io.StringIO()):
        if mc.check_card(str(path)):
            raise ValueError(f"invalid PS2 card: {path}; run mcformat.py check for details")


def read_card(path):
    validate_card(path)
    raw = Path(path).read_bytes()
    sb = raw[:mc.PAGE_SIZE]
    if not sb.startswith(mc.MAGIC):
        raise ValueError("memory card is not formatted")
    fields = struct.unpack(mc.SUPERBLOCK_FORMAT, sb[:mc.SUPERBLOCK_BYTES])
    alloc_offset, alloc_end, root_cluster, ifc_raw = fields[7], fields[8], fields[9], fields[12]
    if alloc_offset != mc.layout()["alloc_offset"] or alloc_end != mc.layout()["alloc_end"]:
        raise ValueError("unsupported memory-card layout")
    pages = {}

    def page(n):
        if n in pages:
            return pages[n]
        at = n * mc.RAW_PAGE
        data = bytearray(raw[at:at + mc.PAGE_SIZE])
        spare = raw[at + mc.PAGE_SIZE:at + mc.RAW_PAGE]
        if spare[-1] != 0xFF:
            for part in range(4):
                start = part * 128
                chunk = data[start:start + 128]
                stored = spare[part * 3:part * 3 + 3]
                if mc.ecc_verdict(chunk, stored) == "corrected" and mc.ecc_chunk(chunk) != stored:
                    # Correct a flipped data bit. If only the ECC is wrong,
                    # no single data-bit flip matches and the data stays put.
                    for byte in range(128):
                        for bit in range(8):
                            chunk[byte] ^= 1 << bit
                            if mc.ecc_chunk(chunk) == stored:
                                data[start:start + 128] = chunk
                                break
                            chunk[byte] ^= 1 << bit
                        else:
                            continue
                        break
        pages[n] = bytes(data)
        return pages[n]

    def cluster(physical):
        return page(physical * 2) + page(physical * 2 + 1)

    ifc = struct.unpack("<256L", cluster(struct.unpack_from("<L", ifc_raw)[0]))
    fat = []
    for physical in ifc[:mc.layout()["fat_clusters"]]:
        fat.extend(struct.unpack("<256L", cluster(physical)))

    def chain(start):
        found = []
        while True:
            if start >= alloc_end or start in found:
                raise ValueError("invalid memory-card FAT chain")
            found.append(start)
            next_entry = fat[start]
            if next_entry == mc.FAT_CHAIN_END:
                return found
            if not next_entry & mc.FAT_ALLOCATED_BIT:
                raise ValueError("free cluster in memory-card FAT chain")
            start = next_entry & ~mc.FAT_ALLOCATED_BIT

    def directory(first, count):
        data = b"".join(cluster(alloc_offset + n) for n in chain(first))
        if count < 2 or count * ENTRY_SIZE > len(data):
            raise ValueError("invalid memory-card directory length")
        return [data[n * ENTRY_SIZE:(n + 1) * ENTRY_SIZE] for n in range(count)]

    root_count = struct.unpack_from("<L", cluster(alloc_offset + root_cluster), 4)[0]
    root = directory(root_cluster, root_count)
    if [entry_name(e) for e in root[:2]] != [".", ".."]:
        raise ValueError("invalid root directory")
    saves = []
    for slot_entry in root[2:]:
        mode, count, first = entry_fields(slot_entry)
        if not mode & mc.DF_EXISTS:
            continue
        if not mode & mc.DF_DIR:
            raise ValueError("root files are not supported; source card is untouched")
        name = safe_name(entry_name(slot_entry))
        if not name.startswith("BASLUS-21615"):
            raise ValueError(f"non-Wild-Arms-V save directory {name!r}; source card is untouched")
        entries = directory(first, count)
        if [entry_name(e) for e in entries[:2]] != [".", ".."]:
            raise ValueError(f"invalid save directory {name!r}")
        files = []
        for file_entry in entries[2:]:
            file_mode, size, file_first = entry_fields(file_entry)
            if not file_mode & mc.DF_EXISTS:
                continue
            if file_mode & mc.DF_DIR:
                raise ValueError("nested save directory is not supported; source card is untouched")
            filename = safe_name(entry_name(file_entry))
            content = b"".join(cluster(alloc_offset + n) for n in chain(file_first))[:size] if size else b""
            files.append((file_entry, content))
        saves.append((slot_entry, entries[:2], files))
    return root[:2], saves


def archive_parts(root_entries, saves, source_hash):
    manifest = {"format": FORMAT, "version": VERSION, "game_id": GAME_ID,
                "source_card_sha256": source_hash,
                "root_entries": [encode_entry(e) for e in root_entries], "saves": []}
    payloads = {}
    for slot_index, (slot_entry, dots, files) in enumerate(saves):
        name = safe_name(entry_name(slot_entry))
        slot = {"name": name, "entry": encode_entry(slot_entry),
                "directory_entries": [encode_entry(e) for e in dots], "files": []}
        for file_entry, content in files:
            filename = safe_name(entry_name(file_entry))
            key = f"saves/{slot_index:03d}/{filename}"
            payloads[key] = content
            slot["files"].append({"name": filename, "entry": encode_entry(file_entry),
                                  "path": key, "size": len(content), "sha256": digest(content)})
        manifest["saves"].append(slot)
    return manifest, payloads


def load_archive(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) > 256 or len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("invalid archive entries")
        if sum(info.file_size for info in z.infolist()) > MAX_ARCHIVE_BYTES:
            raise ValueError("save archive exceeds size limit")
        manifest = json.loads(z.read("manifest.json"))
        if (manifest.get("format"), manifest.get("version"), manifest.get("game_id")) != (FORMAT, VERSION, GAME_ID):
            raise ValueError("unsupported save archive format or game")
        if len(manifest["root_entries"]) != 2 or len(manifest["saves"]) > 64:
            raise ValueError("invalid archive save count")
        roots = [decode_entry(e, name) for e, name in zip(manifest["root_entries"], (".", ".."))]
        saves, expected_paths, seen_slots = [], {"manifest.json"}, set()
        for slot_index, slot in enumerate(manifest["saves"]):
            name = safe_name(slot["name"])
            if not name.startswith("BASLUS-21615") or name in seen_slots or len(slot["files"]) > 128:
                raise ValueError("invalid archive save directory")
            seen_slots.add(name)
            entry = decode_entry(slot["entry"], name)
            if not entry_fields(entry)[0] & mc.DF_DIR:
                raise ValueError("archive save entry is not a directory")
            if len(slot["directory_entries"]) != 2:
                raise ValueError("invalid archive directory entries")
            dots = [decode_entry(e, n) for e, n in zip(slot["directory_entries"], (".", ".."))]
            files = []
            for file in slot["files"]:
                filename = safe_name(file["name"])
                key = f"saves/{slot_index:03d}/{filename}"
                if file["path"] != key or key in expected_paths:
                    raise ValueError("invalid archive file path")
                expected_paths.add(key)
                content = z.read(key)
                if len(content) != file["size"] or digest(content) != file["sha256"]:
                    raise ValueError(f"save file checksum mismatch: {key}")
                file_entry = decode_entry(file["entry"], filename)
                if entry_fields(file_entry)[0] & mc.DF_DIR:
                    raise ValueError("archive file entry is a directory")
                files.append((file_entry, content))
            saves.append((entry, dots, files))
        if set(names) != expected_paths:
            raise ValueError("unexpected archive entries")
        return roots, saves


def build_card(root_entries, saves):
    raw = bytearray(mc.make_card(time.gmtime()))
    lay = mc.layout()
    fat = [mc.FAT_FREE] * lay["fat_entries"]
    for n in range(lay["alloc_end"], len(fat)):
        fat[n] = mc.FAT_CHAIN_END
    next_cluster = 0

    def allocate(count):
        nonlocal next_cluster
        if count <= 0:
            return []
        if next_cluster + count > lay["alloc_end"]:
            raise ValueError("save archive does not fit an 8 MB memory card")
        result = list(range(next_cluster, next_cluster + count))
        next_cluster += count
        for i, n in enumerate(result):
            fat[n] = mc.FAT_CHAIN_END if i + 1 == len(result) else mc.FAT_ALLOCATED_BIT | result[i + 1]
        return result

    def write_cluster(physical, content):
        content = content.ljust(mc.CLUSTER_SIZE, b"\0")
        if len(content) != mc.CLUSTER_SIZE:
            raise ValueError("cluster data is too long")
        for i in range(mc.PAGES_PER_CLUSTER):
            page = physical * mc.PAGES_PER_CLUSTER + i
            chunk = content[i * mc.PAGE_SIZE:(i + 1) * mc.PAGE_SIZE]
            at = page * mc.RAW_PAGE
            raw[at:at + mc.RAW_PAGE] = chunk + mc.ecc_page(chunk)

    def patch(entry, length=None, first=None):
        entry = bytearray(entry)
        if length is not None:
            struct.pack_into("<L", entry, 4, length)
        if first is not None:
            struct.pack_into("<L", entry, 16, first)
        return bytes(entry)

    root_count = 2 + len(saves)
    root_clusters = allocate((root_count + 1) // 2)
    if root_clusters[0] != 0:
        raise AssertionError("root directory must begin at cluster 0")
    root = [patch(root_entries[0], length=root_count), root_entries[1]]
    for slot_entry, dots, files in saves:
        dir_count = 2 + len(files)
        dir_clusters = allocate((dir_count + 1) // 2)
        directory = [dots[0], dots[1]]
        for file_entry, content in files:
            file_clusters = allocate((len(content) + mc.CLUSTER_SIZE - 1) // mc.CLUSTER_SIZE)
            directory.append(patch(file_entry, length=len(content), first=file_clusters[0] if file_clusters else 0))
            for i, n in enumerate(file_clusters):
                write_cluster(lay["alloc_offset"] + n, content[i * mc.CLUSTER_SIZE:(i + 1) * mc.CLUSTER_SIZE])
        for i, n in enumerate(dir_clusters):
            chunk = b"".join(directory[i * 2:i * 2 + 2])
            write_cluster(lay["alloc_offset"] + n, chunk)
        root.append(patch(slot_entry, length=dir_count, first=dir_clusters[0]))
    for i, n in enumerate(root_clusters):
        write_cluster(lay["alloc_offset"] + n, b"".join(root[i * 2:i * 2 + 2]))
    for n in range(lay["fat_clusters"]):
        chunk = struct.pack("<256L", *fat[n * 256:(n + 1) * 256])
        write_cluster(lay["first_ifc"] + lay["ifc_clusters"] + n, chunk)
    return bytes(raw)


def logical_files(saves):
    return [(entry_name(slot), [(entry_name(entry), data) for entry, data in files])
            for slot, _, files in saves]


def export_card(card, archive):
    card, archive = Path(card), Path(archive)
    roots, saves = read_card(card)
    manifest, payloads = archive_parts(roots, saves, digest(card.read_bytes()))
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=archive.parent, prefix=archive.name + ".", suffix=".tmp", delete=False) as f:
        temporary = Path(f.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            for key in sorted(payloads):
                z.writestr(key, payloads[key])
        with temporary.open("r+b") as written:
            written.flush()
            os.fsync(written.fileno())
        if load_archive(temporary) != (roots, saves):
            raise ValueError("save archive failed its round-trip check")
        if archive.exists():
            backup_dir = archive.parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            backup = backup_dir / f"{archive.stem}-{stamp}-{digest(archive.read_bytes())[:8]}{archive.suffix}"
            shutil.copy2(archive, backup)
            with backup.open("r+b") as saved:
                saved.flush()
                os.fsync(saved.fileno())
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Exported {len(saves)} saves to {archive}")


def import_archive(archive, card):
    roots, saves = load_archive(archive)
    image = build_card(roots, saves)
    card = Path(card)
    card.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=card.parent, prefix=card.name + ".", suffix=".tmp", delete=False) as f:
        temporary = Path(f.name)
    try:
        with temporary.open("wb") as f:
            f.write(image)
            f.flush()
            os.fsync(f.fileno())
        validate_card(temporary)
        if logical_files(read_card(temporary)[1]) != logical_files(saves):
            raise ValueError("rebuilt card differs from archive save files")
        os.replace(temporary, card)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Imported {len(saves)} saves from {archive} to {card}")


def inspect(archive):
    _, saves = load_archive(archive)
    print(f"{archive}: {len(saves)} saves")
    for slot_entry, _, files in saves:
        print(f"  {entry_name(slot_entry)}: {len(files)} files, {sum(len(data) for _, data in files)} bytes")


@contextlib.contextmanager
def archive_lock(archive):
    lock_path = Path(str(archive) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        lock.seek(0)
        lock.write(b"\0")
        lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError(f"save archive is already in use: {archive}") from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def run_game(archive, card, seed, runner, arguments):
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if not arguments:
        raise ValueError("run requires the native game's command-line arguments")
    archive, card = Path(archive), Path(card)
    with archive_lock(archive):
        if not archive.exists():
            if seed is None:
                raise ValueError("archive does not exist; provide --seed-card once to import existing saves")
            export_card(seed, archive)
        import_archive(archive, card)
        before = digest(card.read_bytes())
        print(f"Native save archive: {archive}", flush=True)
        code = 1
        try:
            code = subprocess.call([str(runner), *arguments])
        finally:
            if card.exists() and digest(card.read_bytes()) != before:
                export_card(card, archive)
        print(f"Native runner exit code: {code}", flush=True)
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="convert a valid .ps2 card to a .wavsave archive")
    export.add_argument("card", type=Path)
    export.add_argument("archive", type=Path)
    restore = sub.add_parser("import", help="build a temporary .ps2 card from an archive")
    restore.add_argument("archive", type=Path)
    restore.add_argument("card", type=Path)
    check = sub.add_parser("inspect", help="list and validate the saves in an archive")
    check.add_argument("archive", type=Path)
    play = sub.add_parser("run", help="run the recomp using a portable archive and a temporary PS2 card")
    play.add_argument("--archive", required=True, type=Path)
    play.add_argument("--card", required=True, type=Path)
    play.add_argument("--seed-card", type=Path)
    play.add_argument("--runner", required=True, type=Path)
    play.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.command == "export":
            with archive_lock(args.archive):
                export_card(args.card, args.archive)
        elif args.command == "import":
            import_archive(args.archive, args.card)
        elif args.command == "run":
            raise SystemExit(run_game(args.archive, args.card, args.seed_card, args.runner, args.arguments))
        else:
            inspect(args.archive)
    except (OSError, ValueError, TypeError, KeyError, IndexError, struct.error, zipfile.BadZipFile) as error:
        parser.exit(1, f"save archive: {error}\n")


if __name__ == "__main__":
    main()
