#!/usr/bin/env python3
"""Archive Wild ARMs V PCSX2 states while another person plays the game.

The source profile is read only. State archives, logs, and the final memory
card remain in ignored work/ so the user's game data never enters source control.
"""

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from inspect_pcsx2_movie import inspect as inspect_movie


PROJECT = Path(__file__).resolve().parents[1]
PROFILE = Path.home() / "Documents" / "PCSX2"
STATES = PROFILE / "sstates"
LOG = PROFILE / "logs" / "emulog.txt"
CARD = PROFILE / "memcards" / "Mcd001.ps2"
ELF = PROJECT / "external" / "ps2-native-recomp" / "games" / "wildarms5" / "disc" / "SLUS_216.15"
NATIVE = PROJECT / "external" / "ps2-native-recomp"
STATE_PREFIX = "SLUS-21615 (C164550A)"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sources(session):
    found = list(STATES.glob(f"{STATE_PREFIX}.*.p2s"))
    found += list((session / "input").glob("*_SaveState.p2s"))
    return sorted(p for p in found if p.is_file())


def signature(path):
    info = path.stat()
    return [info.st_size, info.st_mtime_ns]


def read_index(session):
    path = session / "index.json"
    if not path.exists():
        raise SystemExit(f"Session not initialized: {session}. Run 'init' first.")
    return json.loads(path.read_text(encoding="utf-8"))


def initialize(session):
    session.mkdir(parents=True, exist_ok=True)
    (session / "input").mkdir(exist_ok=True)
    path = session / "index.json"
    if path.exists():
        raise SystemExit(f"Session already exists: {session}")
    index = {"started_utc": stamp(), "source_signatures": {}, "captures": []}
    for source in sources(session):
        index["source_signatures"][str(source)] = signature(source)
    write_json(path, index)
    print(f"Watching future Wild ARMs V states in {STATES}", flush=True)
    print(f"Session: {session}", flush=True)
    print(f"Input recording destination: {session / 'input' / 'playthrough.p2m2'}", flush=True)


def stable_zip(path):
    before = signature(path)
    if time.time_ns() - before[1] < 2_000_000_000:
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            if "eeMemory.bin" not in archive.namelist() or "Screenshot.png" not in archive.namelist():
                return False
    except (OSError, zipfile.BadZipFile):
        return False
    return signature(path) == before


def recorded_frame(internals):
    tag = b"InputRecording".ljust(32, b"\0")
    data = internals.read_bytes()
    at = data.find(tag)
    if at < 0 or data.find(tag, at + 1) >= 0 or at + 36 > len(data):
        raise ValueError("missing or ambiguous PCSX2 input frame tag")
    return struct.unpack_from("<I", data, at + 32)[0]


def collect(session, index):
    count = 0
    for source in sources(session):
        key = str(source)
        current = signature(source)
        if index["source_signatures"].get(key) == current or not stable_zip(source):
            continue
        sha = digest(source)
        if any(c["sha256"] == sha for c in index["captures"]):
            index["source_signatures"][key] = current
            write_json(session / "index.json", index)
            continue
        name = f"{stamp()}-{source.stem.replace(' ', '_')}-{sha[:10]}"
        target = session / "states" / name
        target.mkdir(parents=True, exist_ok=False)
        snapshot = target / "state.p2s"
        shutil.copy2(source, snapshot)
        if digest(snapshot) != sha:
            shutil.rmtree(target)
            print(f"Source changed during copy; retrying {source.name}", flush=True)
            continue
        command = [sys.executable, str(PROJECT / "tools" / "import_pcsx2_state.py"),
                   str(snapshot), str(ELF), str(NATIVE), str(target / "analysis")]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            shutil.rmtree(target)
            print(f"Import deferred for {source.name}: {result.stderr.strip()}", flush=True)
            continue
        extracted = target / "analysis" / "extracted"
        input_frame = recorded_frame(extracted / "PCSX2 Internal Structures.dat")
        shutil.copy2(extracted / "Screenshot.png", target / "screenshot.png")
        # The original compressed state retains every machine component.
        shutil.rmtree(extracted)
        if LOG.is_file():
            shutil.copy2(LOG, target / "emulog.txt")
        manifest = json.loads((target / "analysis" / "manifest.json").read_text(encoding="utf-8"))
        record = {"source": key, "source_signature": current, "sha256": sha,
                  "captured_utc": stamp(), "path": str(target.relative_to(session)),
                  "ee_pc": manifest["cpu"]["ee_pc"], "pcsx2_frame": input_frame,
                  "code_images": manifest["overlays"]}
        index["captures"].append(record)
        index["source_signatures"][key] = current
        write_json(session / "index.json", index)
        print(f"Captured {source.name}: frame {input_frame}, EE {record['ee_pc']} -> {target}", flush=True)
        count += 1
    return count


def finalize(session):
    index = read_index(session)
    collect(session, index)
    for source, name in ((LOG, "emulog-final.txt"), (CARD, "memcard-slot-1-final.ps2")):
        if not source.is_file():
            print(f"Missing {source}", flush=True)
            continue
        target = session / name
        shutil.copy2(source, target)
        print(f"Copied {source.name}: SHA-256 {digest(target)}", flush=True)
    movies = sorted((session / "input").glob("*.p2m2"))
    if movies:
        for movie in movies:
            print(f"Input recording: {movie} ({movie.stat().st_size} bytes)", flush=True)
            try:
                report = inspect_movie(movie)
                output = movie.with_suffix(".summary.json")
                write_json(output, report)
                print(f"  {report['frames']} frames; summary: {output}", flush=True)
            except (OSError, ValueError) as exc:
                print(f"  Could not summarize recording: {exc}", flush=True)
    else:
        print("No input recording found in session/input", flush=True)
    print(f"Captured {len(index['captures'])} distinct states", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "collect", "watch", "finalize"))
    parser.add_argument("--session", type=Path,
                        default=PROJECT / "work" / "capture-sessions" / "current")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()
    session = args.session.resolve()
    if args.action == "init":
        initialize(session)
        return
    if args.action == "finalize":
        finalize(session)
        return
    index = read_index(session)
    if args.action == "collect":
        print(f"Captured {collect(session, index)} new states", flush=True)
        return
    print(f"Watching {STATES}; Ctrl+C stops collection", flush=True)
    try:
        while True:
            collect(session, index)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Collector stopped", flush=True)


if __name__ == "__main__":
    main()
