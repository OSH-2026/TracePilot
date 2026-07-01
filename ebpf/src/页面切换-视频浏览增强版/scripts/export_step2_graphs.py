#!/usr/bin/env python3
"""
export_step2_graphs.py — Step 2 Binder/Futex 子图导出
从 tracepilot graph JSON 中过滤 BINDER_CALL / FUTEX_WAIT 边，
导出为 Graphviz DOT 格式并渲染为 SVG 图。

用法:
  python export_step2_graphs.py <graph_topology.json> <output_dir>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

NODE_COLORS = {
    "SURFACEFLINGER": "#e45756",
    "RENDER_THREAD": "#4c78a8",
    "UI_THREAD": "#b279a2",
    "VIDEO_DECODER": "#f58518",
    "BINDER_CLIENT": "#72b7b2",
    "BINDER_SERVER": "#72b7b2",
    "FUTEX_WAIT": "#bab0ac",
    "SYSTEM_SERVER": "#9c755f",
    "CPU_RESOURCE": "#54a24b",
    "MEMORY_RECLAIM": "#54a24b",
    "IO_WAIT": "#54a24b",
    "NETWORK": "#ff9da6",
    "BUFFER_QUEUE": "#edc948",
    "THERMAL": "#d67195",
    "FRAME": "#4c78a8",
}

EDGE_COLORS = {
    "BINDER_CALL": "#0077b6",
    "FUTEX_WAIT": "#6a4c93",
    "FRAME_DEPENDENCY": "#999999",
    "WAKEUP": "#666666",
    "RUNNABLE_WAIT": "#666666",
    "RESOURCE_STALL": "#54a24b",
    "NETWORK_WAIT": "#ff9da6",
    "BUFFER_QUEUE": "#edc948",
    "DECODE_DEPENDENCY": "#f58518",
    "THERMAL_STALL": "#d67195",
}


def dot_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")


def node_label(n: dict) -> str:
    comm = n.get("comm") or n.get("type", "?")
    tid = n.get("tid", 0)
    score = n.get("critical_score", 0.0)
    parts = [comm]
    if tid:
        parts.append(f"TID {tid}")
    parts.append(f"score {score:.3f}")
    return "\\n".join(parts)


def write_dot(path: Path, title: str, nodes: list[dict], edges: list[dict]) -> None:
    by_id = {n["id"]: n for n in nodes}
    lines = [
        "digraph step2_graph {",
        "  rankdir=LR;",
        f"  graph [fontname=\"Arial\", fontsize=11, label=\"{dot_escape(title)}\"];",
        "  node [fontname=\"Arial\", fontsize=9, style=filled, shape=box];",
        "  edge [fontname=\"Arial\", fontsize=8];",
    ]
    for n in nodes:
        color = NODE_COLORS.get(n.get("type", ""), "#b279a2")
        lines.append(
            f"  n{n['id']} [label=\"{dot_escape(node_label(n))}\", fillcolor=\"{color}\"];"
        )
    for e in edges:
        et = e.get("type", "")
        color = EDGE_COLORS.get(et, "#666666")
        label = et
        if e.get("count", 1) > 1:
            label += f" x{e['count']}"
        dur = e.get("duration_ns")
        if dur:
            label += f"\\n{dur / 1e6:.2f}ms"
        lines.append(
            f"  n{e['from']} -> n{e['to']} [label=\"{label}\", color=\"{color}\"];"
        )
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path} ({len(nodes)} nodes, {len(edges)} edges)")


def filter_graph(data: dict, edge_types: set[str]) -> tuple[list[dict], list[dict]]:
    edges = [e for e in data.get("edges", []) if e.get("type") in edge_types]
    node_ids = set()
    for e in edges:
        node_ids.add(e["from"])
        node_ids.add(e["to"])
    nodes = [n for n in data.get("nodes", []) if n["id"] in node_ids]
    return nodes, edges


def render_png(dot_path: Path, png_path: Path) -> bool:
    try:
        subprocess.run(
            ["dot", "-Tpng", str(dot_path), "-o", str(png_path)],
            check=True,
            capture_output=True,
        )
        print(f"Rendered {png_path}")
        return True
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"PNG skip ({dot_path.name}): {exc}", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Export Step 2 binder/futex graphs")
    ap.add_argument("graph_json", type=Path, help="graph_subgraph.json or graph_topology.json")
    ap.add_argument("-o", "--out-dir", type=Path, required=True)
    ap.add_argument("--render-png", action="store_true")
    args = ap.parse_args()

    data = json.loads(args.graph_json.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    stem = args.graph_json.stem
    full_dot = args.out_dir / f"{stem}_full.dot"
    write_dot(full_dot, f"Critical Path Subgraph ({stem})", data["nodes"], data["edges"])
    if args.render_png:
        render_png(full_dot, args.out_dir / f"{stem}_full.png")

    binder_nodes, binder_edges = filter_graph(data, {"BINDER_CALL"})
    binder_dot = args.out_dir / "graph_binder.dot"
    write_dot(
        binder_dot,
        f"Step 2 Binder Dependency Graph ({len(binder_edges)} edges)",
        binder_nodes,
        binder_edges,
    )
    if args.render_png:
        render_png(binder_dot, args.out_dir / "graph_binder.png")

    futex_nodes, futex_edges = filter_graph(data, {"FUTEX_WAIT"})
    futex_dot = args.out_dir / "graph_futex.dot"
    write_dot(
        futex_dot,
        f"Step 2 Futex Wait Graph ({len(futex_edges)} edges)",
        futex_nodes,
        futex_edges,
    )
    if args.render_png:
        render_png(futex_dot, args.out_dir / "graph_futex.png")

    summary = {
        "source": str(args.graph_json),
        "total_nodes": len(data.get("nodes", [])),
        "total_edges": len(data.get("edges", [])),
        "binder_nodes": len(binder_nodes),
        "binder_edges": len(binder_edges),
        "futex_nodes": len(futex_nodes),
        "futex_edges": len(futex_edges),
        "outputs": [
            str(full_dot),
            str(binder_dot),
            str(futex_dot),
        ],
    }
    summary_path = args.out_dir / "graph_step2_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
