"""Inventory one traced Wild ARMs V GS frame for the native scene renderer.

Capture a frame with PS2_TRACE_DRAWS=<frame> and PS2_TRACE_VERTS=0,10000,
then pass the runner log here. The SVG is geometry coverage only: it does not
sample textures, perform depth tests, or replace the game's renderer.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


DRAW = re.compile(r"^\[gs-draw\] f(\d+) #(\d+)-(\d+) n\d+ box .*?\| prim (\d+).*?\| FRAME fbp ([0-9A-F]+).*?\| ZBUF zbp ([0-9A-F]+) psm ([0-9A-F]+) zmsk (\d+).*?\| TEST .*? zte (\d+) ztst (\d+).*?\| TEX0 tbp ([0-9A-F]+) tbw (\d+) psm ([0-9A-F]+) (\d+)x(\d+).*?\| vu1pc ([0-9A-F]+)")
VERT = re.compile(r"^\[gs-vert\] f(\d+) #(\d+)(.*)$")
POINT = re.compile(r"^\s*(-?[\d.]+) (-?[\d.]+) (\d+) ([\deE+.-]+) ([\deE+.-]+) ([\deE+.-]+)(?: rgba (\d+) (\d+) (\d+) (\d+) uv (\d+) (\d+) fog (\d+))?")


def parse(log: Path, frame: int) -> tuple[dict[int, dict], dict[int, list[dict]]]:
    draws: dict[int, dict] = {}
    verts: dict[int, list[dict]] = {}
    with log.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if match := DRAW.match(line):
                (f, first, last, prim, fbp, zbp, zpsm, zmsk, zte, ztst,
                 tbp, tbw, tpsm, tw, th, vu) = match.groups()
                if int(f) != frame:
                    continue
                state = {"prim": int(prim), "fbp": fbp, "zbp": zbp,
                         "zpsm": zpsm, "zmsk": bool(int(zmsk)),
                         "zte": bool(int(zte)), "ztst": int(ztst),
                         "tbp": tbp, "tbw": int(tbw), "tpsm": tpsm,
                         "texture_width": int(tw), "texture_height": int(th),
                         "vu1pc": vu,
                         "gs_registers": "| prim " + line.split("| prim ", 1)[1].rstrip()}
                for number in range(int(first), int(last) + 1):
                    draws[number] = state
            elif match := VERT.match(line):
                f, number, tail = match.groups()
                if int(f) != frame:
                    continue
                points = []
                for part in tail.split("|")[1:]:
                    if point := POINT.match(part):
                        x, y, z, s, t, q, *extra = point.groups()
                        vertex = {"x": float(x), "y": float(y), "z": int(z),
                                  "s": float(s), "t": float(t), "q": float(q)}
                        if extra[0] is not None:
                            vertex["rgba"] = [int(v) for v in extra[:4]]
                            vertex["uv"] = [int(v) for v in extra[4:6]]
                            vertex["fog"] = int(extra[6])
                        points.append(vertex)
                verts[int(number)] = points
    return draws, verts


def classify(state: dict, points: list[dict]) -> str:
    if (state["prim"] in (3, 4, 5) and len(points) == 3 and state["zte"] and
            state["ztst"] == 2 and state["fbp"] == "E00" and
            all(p["z"] != 0xFFFFFF for p in points)):
        return "scene_overlay" if state["zmsk"] else "scene_triangle"
    if points and all(p["z"] == 0xFFFFFF for p in points):
        return "hud"
    return "effect_or_other"


def region_spec(value: str) -> tuple[float, float, float, float]:
    try:
        x0, y0, x1, y1 = (float(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("region must be x0,y0,x1,y1 in PS2 pixels") from exc
    if x0 >= x1 or y0 >= y1:
        raise argparse.ArgumentTypeError("region bounds must increase")
    return x0, y0, x1, y1


def triangle_area(points: list[dict]) -> float:
    a, b, c = points
    return abs((b["x"] - a["x"]) * (c["y"] - a["y"]) -
               (b["y"] - a["y"]) * (c["x"] - a["x"])) * 0.5


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--frame", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--region", type=region_spec,
                        help="profile triangle area in x0,y0,x1,y1 PS2 pixel bounds")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.width < 320 or args.height < 240:
        parser.error("target must be at least 320x240")
    draws, verts = parse(args.log, args.frame)
    if not draws or not verts:
        parser.error("no draw states or vertices for this frame; enable both GS trace variables")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    textures: Counter[str] = Counter()
    framebuffers: Counter[str] = Counter()
    vu_programs: Counter[str] = Counter()
    missing = 0
    complete_vertices = 0
    offscreen_triangles = 0
    region_triangles: dict[str, dict[str, float | int]] = {}
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{args.width}" height="{args.height}" viewBox="0 0 512 448" preserveAspectRatio="none">',
           '<rect width="512" height="448" fill="#20242b"/>']
    with (args.out_dir / "draws.jsonl").open("w", encoding="utf-8") as out:
        for number in sorted(verts):
            state = draws.get(number)
            if state is None:
                missing += 1
                continue
            points = verts[number]
            kind = classify(state, points)
            counts[kind] += 1
            if points and all("rgba" in p for p in points):
                complete_vertices += 1
            textures[f'{state["tbp"]}:{state["tpsm"]}'] += 1
            framebuffers[state["fbp"]] += 1
            if kind in ("scene_triangle", "scene_overlay"):
                vu_programs[state["vu1pc"]] += 1
                if args.region:
                    x0, y0, x1, y1 = args.region
                    if (min(p["x"] for p in points) <= x1 and max(p["x"] for p in points) >= x0 and
                            min(p["y"] for p in points) <= y1 and max(p["y"] for p in points) >= y0):
                        key = f'{state["tbp"]}:{kind}'
                        item = region_triangles.setdefault(key, {"triangles": 0, "projected_area": 0.0,
                                                                  "area_under_half_pixel": 0, "area_under_one_pixel": 0})
                        area = triangle_area(points)
                        item["triangles"] += 1
                        item["projected_area"] += area
                        item["area_under_half_pixel"] += area < 0.5
                        item["area_under_one_pixel"] += area < 1.0
                if any(p["x"] < 0 or p["x"] >= 512 or p["y"] < 0 or p["y"] >= 448 for p in points):
                    offscreen_triangles += 1
                if all(-512 <= p["x"] <= 1024 and -512 <= p["y"] <= 896 for p in points):
                    hue = (int(state["tbp"], 16) * 47) % 360
                    path = " ".join(f'{p["x"]:.3f},{p["y"]:.3f}' for p in points)
                    svg.append(f'<polygon points="{path}" fill="hsl({hue},70%,60%)" fill-opacity="0.22"/>')
            out.write(json.dumps({"number": number, "kind": kind, **state, "vertices": points}, separators=(",", ":")) + "\n")
    svg.append("</svg>")
    (args.out_dir / "coverage.svg").write_text("\n".join(svg), encoding="utf-8")
    summary = {"source_log": str(args.log.resolve()), "frame": args.frame,
               "target_width": args.width, "target_height": args.height,
               "note": "Coverage diagnostic only; textures, depth, blending, and HUD composition are not rendered.",
               "draws": len(draws), "vertices": len(verts), "missing_states": missing,
               "complete_vertex_attributes": complete_vertices,
               "scene_triangles_crossing_ps2_viewport": offscreen_triangles,
               "classes": dict(counts), "framebuffers": dict(framebuffers),
               "top_textures": textures.most_common(20), "scene_vu_programs": vu_programs.most_common(20)}
    if args.region:
        summary["region"] = {"bounds": args.region, "units": "PS2 pixels",
                             "note": "Bounding-box overlap and projected area before depth and alpha tests.",
                             "by_texture_and_kind": region_triangles}
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"frame": args.frame, "target": f"{args.width}x{args.height}",
                      "classes": dict(counts), "missing_states": missing}))


if __name__ == "__main__":
    main()
