#!/usr/bin/env python3
"""Render tracepilot graph JSON to SVG for embedding in Markdown reports."""

from __future__ import annotations

import argparse
import json
import math
import textwrap
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


def short_label(n: dict) -> str:
    comm = (n.get("comm") or n.get("type") or "?").strip()
    if len(comm) > 14:
        comm = comm[:13] + "…"
    tid = n.get("tid", 0)
    return f"{comm}\\nTID {tid}" if tid else comm


def filter_edges(data: dict, types: set[str]) -> tuple[list[dict], list[dict]]:
    edges = [e for e in data.get("edges", []) if e.get("type") in types]
    ids: set[int] = set()
    for e in edges:
        ids.add(e["from"])
        ids.add(e["to"])
    nodes = [n for n in data.get("nodes", []) if n["id"] in ids]
    return nodes, edges


def layer_layout(nodes: list[dict], edges: list[dict]) -> dict[int, tuple[float, float]]:
    if not nodes:
        return {}
    by_id = {n["id"]: n for n in nodes}
    indeg: dict[int, int] = defaultdict(int)
    adj: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        adj[e["from"]].append(e["to"])
        indeg[e["to"]] += 1
        indeg.setdefault(e["from"], indeg.get(e["from"], 0))

    roots = sorted(
        [n["id"] for n in nodes if indeg.get(n["id"], 0) == 0],
        key=lambda i: -by_id[i].get("critical_score", 0),
    )
    if not roots:
        roots = sorted([n["id"] for n in nodes], key=lambda i: -by_id[i].get("critical_score", 0))[:3]

    depth: dict[int, int] = {}
    q = deque((r, 0) for r in roots)
    while q:
        nid, d = q.popleft()
        if nid in depth and depth[nid] <= d:
            continue
        depth[nid] = d
        for nxt in adj.get(nid, []):
            q.append((nxt, d + 1))

    max_d = max(depth.values()) if depth else 0
    for n in nodes:
        depth.setdefault(n["id"], max_d + 1)

    layers: dict[int, list[int]] = defaultdict(list)
    for nid, d in depth.items():
        layers[d].append(nid)
    for d in layers:
        layers[d].sort(key=lambda i: -by_id[i].get("critical_score", 0))

    pos: dict[int, tuple[float, float]] = {}
    x_gap, y_gap = 220, 70
    for d in sorted(layers):
        row = layers[d]
        y0 = -(len(row) - 1) * y_gap / 2
        for i, nid in enumerate(row):
            pos[nid] = (d * x_gap, y0 + i * y_gap)
    return pos


def edge_label(e: dict) -> str:
    parts = [e.get("type", "")]
    if e.get("count", 1) > 1:
        parts[0] += f" x{e['count']}"
    dur = e.get("duration_ns")
    if dur:
        parts.append(f"{dur / 1e6:.2f}ms")
    return "\\n".join(parts)


def render_svg(
    title: str,
    nodes: list[dict],
    edges: list[dict],
    out: Path,
    max_edges: int | None = None,
) -> None:
    if max_edges and len(edges) > max_edges:
        edges = sorted(edges, key=lambda e: e.get("duration_ns", 0), reverse=True)[:max_edges]
        ids = set()
        for e in edges:
            ids.add(e["from"])
            ids.add(e["to"])
        nodes = [n for n in nodes if n["id"] in ids]

    pos = layer_layout(nodes, edges)
    if not pos:
        out.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg"><text x="10" y="20">{title}: empty</text></svg>',
            encoding="utf-8",
        )
        return

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    pad = 80
    w = max(xs) - min(xs) + pad * 2 + 120
    h = max(ys) - min(ys) + pad * 2 + 80
    ox = -min(xs) + pad
    oy = -min(ys) + pad + 30

    colors = {
        "SURFACEFLINGER": "#e45756",
        "RENDER_THREAD": "#4c78a8",
        "BINDER_SERVER": "#72b7b2",
        "BINDER_CLIENT": "#72b7b2",
        "FUTEX_WAIT": "#bab0ac",
        "UI_THREAD": "#b279a2",
        "VIDEO_DECODER": "#f58518",
    }

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(w)}" height="{int(h)}" viewBox="0 0 {int(w)} {int(h)}">',
        f'<rect width="100%" height="100%" fill="#fafafa"/>',
        f'<text x="{int(w/2)}" y="24" text-anchor="middle" font-family="Arial,sans-serif" font-size="14" font-weight="bold">{title}</text>',
        "<defs><marker id=\"arrow\" markerWidth=\"8\" markerHeight=\"8\" refX=\"6\" refY=\"3\" orient=\"auto\">"
        "<path d=\"M0,0 L6,3 L0,6 Z\" fill=\"#555\"/></marker></defs>",
    ]

    by_id = {n["id"]: n for n in nodes}
    node_xy: dict[int, tuple[float, float]] = {}
    nw, nh = 110, 42
    for nid, (x, y) in pos.items():
        cx, cy = x + ox, y + oy
        node_xy[nid] = (cx, cy)
        n = by_id[nid]
        fill = colors.get(n.get("type", ""), "#b279a2")
        lines.append(
            f'<rect x="{cx-nw/2:.1f}" y="{cy-nh/2:.1f}" width="{nw}" height="{nh}" rx="6" '
            f'fill="{fill}" stroke="#333" stroke-width="1"/>'
        )
        label = short_label(n).replace("\\n", "&#10;")
        lines.append(
            f'<text x="{cx:.1f}" y="{cy-4:.1f}" text-anchor="middle" font-family="Arial,sans-serif" '
            f'font-size="9" fill="#111">{label}</text>'
        )
        sc = n.get("critical_score")
        if sc:
            lines.append(
                f'<text x="{cx:.1f}" y="{cy+12:.1f}" text-anchor="middle" font-family="Arial,sans-serif" '
                f'font-size="8" fill="#333">score {sc:.3f}</text>'
            )

    for e in edges:
        a = node_xy.get(e["from"])
        b = node_xy.get(e["to"])
        if not a or not b:
            continue
        x1, y1 = a
        x2, y2 = b
        col = "#0077b6" if e.get("type") == "BINDER_CALL" else "#6a4c93"
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        lines.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{col}" '
            f'stroke-width="1.2" marker-end="url(#arrow)"/>'
        )
        lbl = edge_label(e).replace("\\n", " ")
        lines.append(
            f'<text x="{mx:.1f}" y="{my-4:.1f}" text-anchor="middle" font-family="Arial,sans-serif" '
            f'font-size="7" fill="{col}">{lbl}</text>'
        )

    lines.append("</svg>")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(nodes)} nodes, {len(edges)} edges)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("graph_json", type=Path)
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    args = ap.parse_args()
    data = json.loads(args.graph_json.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    bn, be = filter_edges(data, {"BINDER_CALL"})
    render_svg("Step 2 · Binder 依赖图", bn, be, args.out_dir / "graph_binder.svg")

    fn, fe = filter_edges(data, {"FUTEX_WAIT"})
    render_svg(
        "Step 2 · Futex 等待图（Top 18 边 by duration）",
        fn,
        fe,
        args.out_dir / "graph_futex.svg",
        max_edges=18,
    )

    kn, ke = filter_edges(
        data,
        {"FRAME_DEPENDENCY", "BINDER_CALL", "FUTEX_WAIT", "RESOURCE_STALL", "NETWORK_WAIT"},
    )
    by_id = {n["id"]: n for n in data.get("nodes", [])}
    adj: dict[int, set[int]] = defaultdict(set)
    for e in ke:
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])
    seeds = sorted(data.get("nodes", []), key=lambda n: -n.get("critical_score", 0))[:6]
    sel: set[int] = set()
    q = deque((s["id"], 0) for s in seeds)
    while q and len(sel) < 24:
        nid, hop = q.popleft()
        if nid in sel:
            continue
        sel.add(nid)
        if hop >= 2:
            continue
        for nb in adj.get(nid, set()):
            if nb not in sel:
                q.append((nb, hop + 1))
    kn = [by_id[i] for i in sel if i in by_id]
    ke = [e for e in ke if e["from"] in sel and e["to"] in sel]
    render_svg("Step 2 · 关键路径子图（Top 节点 + 2-hop）", kn, ke, args.out_dir / "graph_critical.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
