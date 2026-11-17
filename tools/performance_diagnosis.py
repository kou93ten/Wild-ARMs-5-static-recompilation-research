"""Opt-in, repeatable performance capture from a native PS2STATE checkpoint.

The normal .cmd launchers do not invoke this tool or set its environment variables.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime


ROOT = Path(__file__).resolve().parents[1]
GAME = ROOT / "external/ps2-native-recomp/games/wildarms5"
RUNNER = ROOT / "external/ps2-native-recomp/build/native-wildarms5/ps2run.exe"
ELF = GAME / "disc/SLUS_216.15"
DISC = Path(r"D:\Emulation\PS2\Wild Arms V\WildArmsV.iso")
STATES = ROOT / "work/native-play"
OUTPUT = ROOT / "work/performance-diagnosis"
MIN_GAMEPLAY_SECONDS = 5
DIAGNOSTIC_ENV = (
    "PS2_FRAME_LOG", "PS2_HITCH_LOG", "PS2_HITCH_PICTURES", "PS2_PRESENT_LOG",
    "PS2_PROFILE", "PS2_PROFILE_FROM", "PS2_GPU_PROFILE", "PS2_GPU_FRAME_LOG", "PS2_GPU_BENCH",
    "PS2_GPU_BENCH_DETAIL", "PS2_TIME", "PS2_ONE_THREAD", "PS2_THREADS",
    "PS2_RECORD", "PS2_PAD_LOG", "PS2_DUMP", "PS2_BATCH_LOG", "PS2_RENDER_PROFILE", "PS2_SCENE_GEOMETRY",
    "PS2_SCENE_GRID_SCALE", "PS2_SCALE", "PS2_SCALE_AUTO",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def state_frame(path: Path) -> int | None:
    info = path.parent / "info.txt"
    if info.exists():
        match = re.search(r"(?m)^frame:\s*(\d+)", info.read_text(errors="replace"))
        if match:
            return int(match.group(1))
    match = re.search(r"(?:^|[_-])f(\d{4,})(?:\.|[_-]|$)", path.name)
    if not match:
        match = re.search(r"[_-]f(\d{4,})(?:$|[_-])", path.parent.name)
    return int(match.group(1)) if match else None


def validate_state(path: Path) -> bool:
    with path.open("rb") as stream:
        header = stream.read(12)
    return len(header) == 12 and header[:8] == b"PS2STATE" and 4 <= int.from_bytes(header[8:], "little") <= 9


def latest_state() -> Path:
    candidates = []
    for state in STATES.glob("*/reports/*/report.state"):
        if state.with_name("report.state.ps2").is_file() and state.stat().st_size > 1024 * 1024:
            candidates.append(state)
    for state in STATES.glob("*/states/quick_*.state"):
        if Path(str(state) + ".ps2").is_file() and state.stat().st_size > 1024 * 1024:
            candidates.append(state)
    candidates.sort(key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)
    for state in candidates:
        if validate_state(state) and state_frame(state) is not None:
            return state
    raise ValueError(f"No complete F5/F9 native state with a known frame under {STATES}")


def paired_card(state: Path) -> Path:
    return state.with_name("report.state.ps2") if state.name == "report.state" else Path(str(state) + ".ps2")


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    point = (len(ordered) - 1) * p / 100
    lo = int(point)
    return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (point - lo)


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as stream:
        return list(csv.DictReader(stream, delimiter=" ", skipinitialspace=True))


def number(row: dict, key: str) -> float:
    try:
        return float(row.get(key, 0))
    except (ValueError, TypeError):
        return 0.0


def nvidia_probe(stop: threading.Event, output: Path, pid: int, frame_log: Path, first_frame: int) -> None:
    """Sample actual device engine load; NVIDIA does not expose reliable per-process load here."""
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("timestamp", "pid", "last_flushed_frame", "gpu_device_pct", "memory_device_pct", "vram_used_mib", "vram_total_mib"))
        while not stop.is_set():
            flushed_frame = 0
            if frame_log.exists():
                try:
                    with frame_log.open("rb") as frames:
                        frames.seek(max(0, frames.seek(0, 2) - 256))
                        tail = frames.read().decode(errors="replace")
                    matches = re.findall(r"(?m)^(\d+)\s+[\d.]+\s", tail)
                    flushed_frame = int(matches[-1]) if matches else 0
                except OSError:
                    pass
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total",
                     "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode == 0 and flushed_frame >= first_frame:
                    for line in result.stdout.splitlines():
                        fields = [x.strip() for x in line.split(",")]
                        if len(fields) == 4:
                            writer.writerow((f"{time.time():.3f}", pid, flushed_frame, *fields))
                    stream.flush()
            except (OSError, subprocess.TimeoutExpired):
                pass
            stop.wait(1.0)


def diagnose(folder: Path, start: int, warmup: int, target: int, exit_code: int | None = None) -> dict:
    all_frames = rows(folder / "frames.txt")
    all_pictures = rows(folder / "pictures.txt")
    frames = [r for r in all_frames if int(r["frame"]) >= start + warmup]
    pictures = [r for r in all_pictures if int(r["frame"]) >= start + warmup]
    gpu_samples = rows_csv(folder / "gpu_device.csv")
    poll_by_frame: dict[int, float] = {}
    log_path = folder / "runner.log"
    if log_path.exists():
        for ms, frame in re.findall(r"\[hitch\] window event polling took ([\d.]+) ms \(around frame (\d+)\)",
                                    log_path.read_text(encoding="utf-8", errors="replace")):
            at = int(frame)
            poll_by_frame[at] = poll_by_frame.get(at, 0.0) + float(ms)
    gpu_frames = rows(folder / "gpu-frame.txt")
    gpu_by_frame: dict[int, float] = {}
    for r in gpu_frames:
        frame = int(r["frame"])
        gpu_by_frame[frame] = gpu_by_frame.get(frame, 0.0) + number(r, "gpu_submit_ms")
    measured_gpu_frames = [ms for frame, ms in gpu_by_frame.items() if frame >= start + warmup]
    budget = 1000 / target
    walls = [number(r, "wall_ms") for r in frames]
    pic_by_frame = {int(r["frame"]): r for r in pictures}
    worst = sorted(frames, key=lambda r: number(r, "wall_ms"), reverse=True)[:12]
    events = []
    for r in worst:
        frame = int(r["frame"])
        # The producer can be two pictures ahead. A frame's wait can be caused by
        # the previous queued picture, even when its own row has no GIF work.
        nearby = [pic_by_frame.get(frame + offset, {}) for offset in (-2, -1, 0, 1)]
        pic = max(nearby, key=lambda x: number(x, "gif_ms") + number(x, "other_ms"))
        wall = number(r, "wall_ms")
        wait_window = number(r, "wait_window_ms")
        wait_sound = number(r, "wait_sound_ms")
        gif = number(pic, "gif_ms")
        present = sum(number(pic, k) for k in ("flush_ms", "acquire_ms", "submit_ms", "present_ms"))
        gpu_nearby = max((gpu_by_frame.get(frame + offset, 0.0) for offset in (-2, -1, 0, 1)), default=0.0)
        poll_nearby = sum(poll_by_frame.get(frame + offset, 0.0) for offset in (-1, 0, 1))
        if wait_sound >= wall * 0.45:
            cause = "audio pacing/device wait"
        elif poll_nearby >= budget and wait_window >= budget:
            cause = "window event polling stall"
        elif wait_window >= wall * 0.35 and number(pic, "acquire_ms") >= budget * 0.5:
            cause = "GPU/swap-chain back-pressure likely"
        elif wait_window >= wall * 0.35 and gpu_nearby >= budget * 0.8:
            cause = "GPU execution may contribute; compare sampled command buffer and queue time"
        elif wait_window >= wall * 0.35 and gif >= budget * 0.7:
            cause = "render-thread GS/GIF processing likely"
        elif wait_window >= wall * 0.35 and present >= budget * 0.7:
            cause = "GPU hand-over or display pacing likely"
        elif wait_window >= wall * 0.35:
            cause = "render queue back-pressure; inspect neighboring picture rows"
        elif number(r, "interpreted") > percentile([number(x, "interpreted") for x in frames], 90):
            cause = "CPU-side VU interpretation may contribute"
        elif number(r, "cpu_ms") >= wall * 0.8:
            cause = "CPU work likely; inspect sampler and hitch log"
        else:
            cause = "unresolved; inspect hitch and device samples"
        events.append({"frame": frame, "wall_ms": wall, "wait_window_ms": wait_window,
                       "wait_sound_ms": wait_sound, "process_cpu_ms": number(r, "cpu_ms"),
                       "picture_frame": int(pic["frame"]) if pic else None,
                       "gpu_submit_sample_ms_nearby": round(gpu_nearby, 2),
                       "window_poll_ms_nearby": round(poll_nearby, 2),
                       "gif_ms_nearby": gif, "present_steps_ms_nearby": present,
                       "likely_cause": cause})
    gpu_pct = [number(r, "gpu_device_pct") for r in gpu_samples if r.get("gpu_device_pct", "").isdigit()]
    capture_meta = json.loads((folder / "capture.json").read_text(encoding="utf-8")) if (folder / "capture.json").exists() else {}
    gpu_profile_file = folder / "gpu-commands.txt"
    gpu_profile_status = ("rows available" if gpu_profile_file.exists() and gpu_profile_file.stat().st_size else
                          "no command rows on this path" if capture_meta.get("gpu_profile") else "not requested")
    report = {
        "target_fps": target, "budget_ms": round(budget, 3), "start_frame": start,
        "warmup_frames": warmup, "measured_frames": len(frames), "exit_code": exit_code,
        "minimum_gameplay_seconds": MIN_GAMEPLAY_SECONDS,
        "measured_gameplay_seconds_at_target_cadence": round(len(frames) / target, 2),
        "measured_wall_seconds": round(sum(walls) / 1000, 2),
        "five_second_capture_complete": len(frames) >= MIN_GAMEPLAY_SECONDS * target,
        "observed_emulated_fps": round(1000 / statistics.mean(walls), 1) if walls else 0,
        "frame_wall_ms": {"median": round(percentile(walls, 50), 2),
                          "p95": round(percentile(walls, 95), 2),
                          "p99": round(percentile(walls, 99), 2),
                          "max": round(max(walls, default=0), 2)},
        "frames_over_budget": sum(v > budget for v in walls),
        "frames_over_budget_by_1ms": sum(v > budget + 1.0 for v in walls),
        "frames_over_2x_budget": sum(v > budget * 2 for v in walls),
        "process_cpu_core_pct_median": round(percentile(
            [100 * number(r, "cpu_ms") / number(r, "wall_ms") for r in frames if number(r, "wall_ms")], 50), 1),
        "gpu_device_pct": {"samples": len(gpu_pct), "median": round(percentile(gpu_pct, 50), 1),
                           "p95": round(percentile(gpu_pct, 95), 1), "max": max(gpu_pct, default=0)},
        "gpu_usage_scope": "whole GPU device, sampled once per second after warmup; other processes can contribute",
        "gpu_submit_timestamp_samples": len(gpu_frames),
        "gpu_timestamped_frames": len(measured_gpu_frames),
        "gpu_submit_sample_ms": {"median": round(percentile(measured_gpu_frames, 50), 3),
                                 "p95": round(percentile(measured_gpu_frames, 95), 3),
                                 "max": round(max(measured_gpu_frames, default=0), 3)},
        "gpu_command_profile": gpu_profile_status,
        "worst_frames": events,
        "limits": ["Frame wall time includes pacing; it is not pure render time.",
                   "GPU device utilization is not process attribution. Correlate with picture stages and optional GPU timestamp profile.",
                   "GPU submit timestamps sample command buffers, not every GPU operation or swap-chain present.",
                   "Emulation, render-thread, and GPU work overlap; their times must not be added into one pie chart.",
                   "Render-thread picture rows can lag emulation frames by up to two frames.",
                   "120 FPS readiness requires an actual 120 FPS runner; a 120 FPS budget comparison alone cannot prove it."],
    }
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = ["# Performance diagnosis", "", f"Measured {len(frames)} frames after {warmup} warmup frames from state frame {start}.",
             f"Gameplay window: {report['measured_gameplay_seconds_at_target_cadence']:.2f} s at the target cadence; {report['measured_wall_seconds']:.2f} s of observed wall time. Five-second minimum: {'met' if report['five_second_capture_complete'] else 'NOT MET'}.",
             f"Target: {target} FPS ({budget:.2f} ms/frame).",
             f"Observed emulated-frame cadence: {report['observed_emulated_fps']:.1f} frames/s over the measured window.",
             f"Frame wall time: median {report['frame_wall_ms']['median']:.2f}, p95 {report['frame_wall_ms']['p95']:.2f}, p99 {report['frame_wall_ms']['p99']:.2f}, max {report['frame_wall_ms']['max']:.2f} ms.",
             f"Over budget: {report['frames_over_budget']}/{len(frames)}; over by more than 1 ms: {report['frames_over_budget_by_1ms']}/{len(frames)}; over 2x: {report['frames_over_2x_budget']}/{len(frames)}.",
             f"Process CPU: median {report['process_cpu_core_pct_median']:.1f}% of one core (all process threads combined).",
             f"GPU device: median {report['gpu_device_pct']['median']:.0f}%, p95 {report['gpu_device_pct']['p95']:.0f}%, max {report['gpu_device_pct']['max']:.0f}% ({len(gpu_pct)} one-second samples).",
             f"GPU command-buffer timestamps: {len(gpu_frames)} samples across {len(measured_gpu_frames)} measured frames; median sampled total {report['gpu_submit_sample_ms']['median']:.3f} ms per sampled frame (p95 {report['gpu_submit_sample_ms']['p95']:.3f}).",
             f"GPU command timestamps: {gpu_profile_status}.",
             "", "See `frame-timeline.svg` for the frame-time graph and separate stage lanes; `timeline.csv` contains every measured frame.",
             "", "## Worst frames", "", "| Frame | Wall ms | Window wait | Audio wait | CPU ms | Poll ms | Picture frame | GIF ms | GPU sample ms | Present ms | Likely cause |",
             "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for e in events:
        lines.append(f"| {e['frame']} | {e['wall_ms']:.2f} | {e['wait_window_ms']:.2f} | {e['wait_sound_ms']:.2f} | {e['process_cpu_ms']:.2f} | {e['window_poll_ms_nearby']:.2f} | {e['picture_frame']} | {e['gif_ms_nearby']:.2f} | {e['gpu_submit_sample_ms_nearby']:.2f} | {e['present_steps_ms_nearby']:.2f} | {e['likely_cause']} |")
    lines += ["", "## Interpretation", "", *[f"- {x}" for x in report["limits"]],
              "- Inspect `runner.log` for `[hitch]`, `[present]`, `[gpu]`, disc, and live-translation events near these frames.",
              "- For a suspected GPU bottleneck, repeat the same state with `--gpu-profile` or `--gpu-bench-frame FRAME`; compare 720p/1080p/4K and GS scale at identical frames.",
              "- For a suspected CPU bottleneck, repeat with `--sample-cpu` and inspect `cpu-profile.txt`.", ""]
    (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")
    write_timeline(folder, frames, pic_by_frame, gpu_samples, gpu_by_frame, poll_by_frame, budget)
    return report


def write_timeline(folder: Path, frames: list[dict], pictures: dict[int, dict], gpu_samples: list[dict],
                   gpu_by_frame: dict[int, float], poll_by_frame: dict[int, float], budget: float) -> None:
    gpu_at = sorted(((int(r.get("last_flushed_frame", 0)), number(r, "gpu_device_pct"))
                     for r in gpu_samples), key=lambda x: x[0])
    gpu_index = 0
    gpu_pct = None
    columns = ("frame", "wall_ms", "emu_active_est_ms", "wait_window_ms", "wait_sound_ms",
               "process_cpu_ms", "process_cpu_core_pct", "shapes", "interpreted", "picture_gap_ms", "gif_ms", "other_ms",
               "flush_ms", "acquire_ms", "submit_ms", "present_ms", "window_poll_ms",
               "gpu_submit_sample_ms",
               "gpu_device_pct_sample", "picture_frame")
    timeline = []
    for frame_row in frames:
        frame = int(frame_row["frame"])
        while gpu_index < len(gpu_at) and gpu_at[gpu_index][0] <= frame:
            gpu_pct = gpu_at[gpu_index][1]
            gpu_index += 1
        pic = pictures.get(frame - 1, {}) if number(pictures.get(frame, {}), "gif_msgs") == 0 else pictures.get(frame, {})
        wall = number(frame_row, "wall_ms")
        wait_window = number(frame_row, "wait_window_ms")
        wait_sound = number(frame_row, "wait_sound_ms")
        timeline.append({"frame": frame, "wall_ms": wall,
                         "emu_active_est_ms": max(0, wall - wait_window - wait_sound),
                         "wait_window_ms": wait_window, "wait_sound_ms": wait_sound,
                         "process_cpu_ms": number(frame_row, "cpu_ms"),
                         "process_cpu_core_pct": 100 * number(frame_row, "cpu_ms") / wall if wall else 0,
                         "shapes": number(frame_row, "shapes"),
                         "interpreted": number(frame_row, "interpreted"),
                         **{key: number(pic, key) for key in columns[9:16]},
                         "window_poll_ms": poll_by_frame.get(frame, 0.0),
                         "gpu_submit_sample_ms": gpu_by_frame.get(frame),
                         "gpu_device_pct_sample": gpu_pct,
                         "picture_frame": int(pic["frame"]) if pic else None})
    with (folder / "timeline.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(timeline)
    # A compact vector graph: top lane is emulated frame wall time. The second
    # lane is render-thread picture time and the third is sampled device load.
    # Lanes deliberately remain separate because the threads/GPU overlap.
    if not timeline:
        return
    width, plot_left, plot_width = 1400, 82, 1290
    height = 745
    scale = plot_width / max(1, len(timeline) - 1)
    max_ms = max(budget * 2, max(r["wall_ms"] for r in timeline) * 1.05)
    lane_h = 110
    def poly(key: str, top: int, cap: float, color: str) -> str:
        points = " ".join(f"{plot_left + i * scale:.1f},{top + lane_h - min(cap, r[key] or 0) / cap * lane_h:.1f}"
                          for i, r in enumerate(timeline))
        return f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.7"/>'
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#101820"/>',
             '<style>text{fill:#dce5ea;font:13px sans-serif}.muted{fill:#9fb0bd}</style>',
             '<text x="82" y="24" font-size="17">Frame timeline: emulation, render thread, GPU device</text>',
             f'<text x="82" y="43" class="muted">frames {timeline[0]["frame"]}–{timeline[-1]["frame"]}; GPU load sampled once per second</text>']
    for top, label, cap in ((62, "Emulation wall ms", max_ms), (204, "Render picture stages ms", max_ms),
                            (345, "GPU command buffer ms (sampled)", max_ms), (485, "GPU device %", 100),
                            (625, "Process CPU % of one core", 200)):
        parts += [f'<line x1="{plot_left}" y1="{top + lane_h}" x2="{plot_left + plot_width}" y2="{top + lane_h}" stroke="#52616b"/>',
                  f'<text x="82" y="{top - 8}">{html.escape(label)}</text>',
                  f'<text x="5" y="{top + 9}" class="muted">{cap:.0f}</text>']
    bar_width = max(0.8, min(5.0, plot_width / len(timeline) * 0.8))
    for i, row in enumerate(timeline):
        x = plot_left + i * scale - bar_width / 2
        for top, stages in ((62, (("emu_active_est_ms", "#b794ee"), ("wait_window_ms", "#66b9ed"),
                                 ("wait_sound_ms", "#e5bd64"))),
                            (204, (("gif_ms", "#8edf82"), ("other_ms", "#93a2ac"),
                                   ("flush_ms", "#ca88df"), ("acquire_ms", "#eeaa66"),
                                   ("submit_ms", "#6ec7ed"), ("present_ms", "#f36b65")))):
            y = top + lane_h
            for key, color in stages:
                h = min(lane_h, max(0, row[key]) / max_ms * lane_h)
                if h >= 0.35:
                    y -= h
                    parts.append(f'<rect x="{x:.1f}" y="{max(top, y):.1f}" width="{bar_width:.1f}" height="{min(h, top + lane_h - max(top, y)):.1f}" fill="{color}"/>')
    target_y = 62 + lane_h - budget / max_ms * lane_h
    parts.append(f'<line x1="{plot_left}" y1="{target_y:.1f}" x2="{plot_left + plot_width}" y2="{target_y:.1f}" stroke="#e5bd64" stroke-dasharray="5,5"/>')
    gpu_measured = [r for r in timeline if r["gpu_device_pct_sample"] is not None]
    gpu_start = len(timeline) - len(gpu_measured)
    gpu_points = "\n".join(f'<circle cx="{plot_left + i * scale:.1f}" cy="{345 + lane_h - min(max_ms, r["gpu_submit_sample_ms"]) / max_ms * lane_h:.1f}" r="2" fill="#73d5c7"/>'
                           for i, r in enumerate(timeline) if r["gpu_submit_sample_ms"] is not None)
    parts += [poly("wall_ms", 62, max_ms, "#f36b65"),
              poly("window_poll_ms", 204, max_ms, "#f7cf6c"),
              gpu_points,
              (f'<polyline points="' + " ".join(
                  f"{plot_left + (gpu_start + i) * scale:.1f},{485 + lane_h - r['gpu_device_pct_sample'] / 100 * lane_h:.1f}"
                  for i, r in enumerate(gpu_measured)) + '" fill="none" stroke="#b794ee" stroke-width="1.7"/>') if gpu_measured else "",
              poly("process_cpu_core_pct", 625, 200, "#73d5c7"),
              '<text x="625" y="194" class="muted">purple active estimate  ·  blue window wait  ·  gold sound wait  ·  red wall</text>',
              '<text x="560" y="336" class="muted">green GIF  ·  gray other  ·  violet flush  ·  orange acquire  ·  blue submit  ·  red present  ·  yellow poll</text>',
              '<text x="740" y="476" class="muted">teal timestamped GPU command buffer samples</text>',
              '<text x="740" y="616" class="muted">purple whole-device GPU utilization samples</text>',
              '</svg>']
    (folder / "frame-timeline.svg").write_text("\n".join(parts), encoding="utf-8")


def rows_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, help="native .state checkpoint; default: latest complete F5/F9")
    parser.add_argument("--card", type=Path, help="source card if state lacks its paired card")
    parser.add_argument("--start-frame", type=int, help="override checkpoint frame when its name/metadata lacks one")
    parser.add_argument("--frames", type=int, help="total frames after checkpoint; default: warmup plus five seconds at target FPS")
    parser.add_argument("--warmup", type=int, default=120, help="initial frames excluded from summary")
    parser.add_argument("--target-fps", type=int, choices=(60, 120), default=60)
    parser.add_argument("--window-size", default="1920x1080")
    parser.add_argument("--display-mode", choices=("windowed", "borderless", "fullscreen"), default="windowed")
    parser.add_argument("--aspect", choices=("4:3", "16:10", "16:9", "21:9"), default="16:9")
    parser.add_argument("--renderer", choices=("exact", "gs"), default="exact")
    parser.add_argument("--gs-scale", type=int, choices=range(1, 7), default=4)
    parser.add_argument("--axis", action="append", default=[],
                        help="script analog input as FRAME:rx|ry|lx|ly:VALUE:DURATION; repeatable")
    parser.add_argument("--sample-cpu", action="store_true", help="sample emulation-thread call sites (adds overhead)")
    parser.add_argument("--render-profile", action="store_true", help="time CPU-side GS draw preparation (adds overhead)")
    parser.add_argument("--gpu-profile", action="store_true", help="collect GPU command timestamps (adds overhead)")
    parser.add_argument("--gpu-bench-frame", type=int, help="replay one GS frame after run for isolated chip timings (intrusive)")
    parser.add_argument("--batch-log-frame", type=int, help="log GS batch reasons for one frame (intrusive)")
    parser.add_argument("--batch-summary", action="store_true", help="log GS batch reasons and exact-overlap cost per frame")
    parser.add_argument("--analyze", type=Path, help="analyze an existing capture folder without launching")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.analyze:
        metadata = json.loads((args.analyze / "capture.json").read_text(encoding="utf-8"))
        report = diagnose(args.analyze, metadata["start_frame"], metadata["warmup"], metadata["target_fps"])
        print(args.analyze / "report.md", f"({report['measured_frames']} frames)")
        return 0
    if args.warmup < 0:
        parser.error("--warmup must be nonnegative")
    min_measured_frames = MIN_GAMEPLAY_SECONDS * args.target_fps
    if args.frames is None:
        args.frames = args.warmup + min_measured_frames
    if args.frames - args.warmup < min_measured_frames:
        parser.error(f"capture at least {MIN_GAMEPLAY_SECONDS} seconds after warmup: "
                     f"--frames must be at least --warmup + {min_measured_frames} for {args.target_fps} FPS")
    if not re.fullmatch(r"\d{3,5}x\d{3,5}", args.window_size):
        parser.error("--window-size must be WIDTHxHEIGHT")
    for axis in args.axis:
        if not re.fullmatch(r"\d+:(?:rx|ry|lx|ly):\d{1,3}:\d+", axis):
            parser.error(f"invalid --axis specification: {axis}")
    state = (args.state or latest_state()).resolve()
    if not state.is_file() or not validate_state(state):
        parser.error("--state must be a native PS2STATE v4-v9 file, not a PCSX2 .p2s")
    start = args.start_frame if args.start_frame is not None else state_frame(state)
    if start is None:
        parser.error("checkpoint frame unknown; provide --start-frame")
    source_card = (args.card or paired_card(state)).resolve()
    required = (RUNNER, ELF, DISC, source_card)
    for path in required:
        if not path.is_file():
            parser.error(f"missing required file: {path}")
    folder = OUTPUT / f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"
    scratch_card = folder / "card.ps2"
    video_mode = "--window" if args.display_mode == "windowed" else f"--{args.display_mode}"
    command = [str(RUNNER), str(ELF), "--disc", str(DISC), video_mode,
               "--aspect", args.aspect, "--memcard", str(scratch_card), "--game-dir", str(GAME),
               "--quick-dir", str(folder), "--misses", str(folder / "misses.txt"),
               "--load-state", str(state), "--frames", str(start + args.frames + 1), "--no-record"]
    if args.display_mode != "borderless":
        command += ["--window-size", args.window_size]
    for axis in args.axis:
        command += ["--axis", axis]
    env = os.environ.copy()
    for key in DIAGNOSTIC_ENV:
        env.pop(key, None)
    env.update({"PS2_DEBUG_REQUEUE": "1", "PS2_GS_IMR_PENDING_IRQ": "0", "PS2_GS_REJECT_INVALID_STQ": "1",
                "PS2_VU_INTERPRET_ENTRIES": "1B8", "PS2_FRAME_LOG": str(folder / "frames.txt"),
                "PS2_HITCH_LOG": "25", "PS2_HITCH_PICTURES": str(folder / "pictures.txt"),
                "PS2_GPU_FRAME_LOG": str(folder / "gpu-frame.txt"),
                "PS2_PRESENT_LOG": "1", "PS2_SCENE_GEOMETRY": "1" if args.renderer == "exact" else "0",
                "PS2_SCENE_GRID_SCALE": "0", "PS2_SCALE": str(args.gs_scale), "PS2_SCALE_AUTO": "0"})
    if args.sample_cpu:
        env["PS2_PROFILE"] = str(folder / "cpu-profile.txt")
        env["PS2_PROFILE_FROM"] = str(start + args.warmup)
    if args.render_profile:
        env["PS2_RENDER_PROFILE"] = "1"
    if args.gpu_profile:
        env["PS2_GPU_PROFILE"] = f"{start + args.warmup},{start + args.frames},{folder / 'gpu-commands.txt'}"
    if args.gpu_bench_frame is not None:
        if not start < args.gpu_bench_frame < start + args.frames:
            parser.error("--gpu-bench-frame must fall within the measured run")
        env["PS2_GPU_BENCH"] = f"{args.gpu_bench_frame},3"
        env["PS2_GPU_BENCH_DETAIL"] = "0"
    if args.batch_log_frame is not None:
        if not start < args.batch_log_frame < start + args.frames:
            parser.error("--batch-log-frame must fall within the measured run")
        env["PS2_BATCH_LOG"] = str(args.batch_log_frame)
    elif args.batch_summary:
        env["PS2_BATCH_LOG"] = "1"
    metadata = {"state": str(state), "state_sha256": sha256(state), "source_card": str(source_card),
                "source_card_sha256": sha256(source_card), "runner": str(RUNNER), "runner_sha256": sha256(RUNNER),
                "start_frame": start, "warmup": args.warmup, "frames": args.frames, "target_fps": args.target_fps,
                "minimum_gameplay_seconds": MIN_GAMEPLAY_SECONDS,
                "planned_measured_frames": args.frames - args.warmup,
                "renderer": args.renderer, "display_mode": args.display_mode,
                "window_size": args.window_size, "aspect": args.aspect,
                "gs_scale": args.gs_scale, "axes": args.axis, "sample_cpu": args.sample_cpu,
                "render_profile": args.render_profile, "gpu_profile": args.gpu_profile,
                "gpu_bench_frame": args.gpu_bench_frame, "batch_log_frame": args.batch_log_frame,
                "batch_summary": args.batch_summary,
                "command": command}
    if args.dry_run:
        print(json.dumps(metadata, indent=2))
        return 0
    folder.mkdir(parents=True)
    shutil.copy2(source_card, scratch_card)
    (folder / "capture.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Capturing {args.frames} frames from state {start}; analyzing at least "
          f"{MIN_GAMEPLAY_SECONDS} seconds after {args.warmup} warmup frames into {folder}", flush=True)
    stop = threading.Event()
    with (folder / "runner.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        monitor = threading.Thread(target=nvidia_probe,
                                  args=(stop, folder / "gpu_device.csv", process.pid,
                                        folder / "frames.txt", start + args.warmup), daemon=True)
        monitor.start()
        try:
            exit_code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            exit_code = process.wait()
        finally:
            stop.set()
            monitor.join(timeout=5)
    report = diagnose(folder, start, args.warmup, args.target_fps, exit_code)
    print(f"Report: {folder / 'report.md'} ({report['measured_frames']} measured frames, exit {exit_code})")
    if not report["five_second_capture_complete"]:
        print("Capture ended before five seconds of measured gameplay; inspect runner.log.", file=sys.stderr)
        return 1
    return 0 if exit_code == 0 else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
