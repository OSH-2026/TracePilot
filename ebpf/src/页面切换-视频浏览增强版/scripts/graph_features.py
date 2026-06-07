#!/usr/bin/env python3
"""
TracePilot Task 17 — 基于图拓扑的特征提取
从 graph_topology.json 的边类型分布中提取特征，不依赖 trace_processor_shell。

用法：
  python3 scripts/graph_features.py <result.json> <graph_topology.json> <output.csv>

输出：每个 jank 帧的图特征 + 自动标签
"""
import csv
import json
import os
import sys

EDGE_TYPES = [
    "FUTEX_WAIT", "BINDER_CALL", "DECODE_DEPENDENCY",
    "RESOURCE_STALL", "NETWORK_WAIT", "BUFFER_QUEUE",
]


def load_graph(graph_path):
    with open(graph_path, encoding="utf-8") as f:
        data = json.load(f)
    nodes = {n["id"]: n for n in data.get("nodes", [])}
    edges = data.get("edges", [])
    return nodes, edges


def load_result(result_path):
    with open(result_path, encoding="utf-8") as f:
        data = json.load(f)
    session = data.get("session_id", "unknown")
    inferences = data.get("inference", {}).get("frame_inferences", [])
    return session, inferences


def extract_frame_graph_features(nodes, edges, inference):
    evidence = {e["signal"]: e["weight"] for e in inference.get("evidence", [])}

    # Find nodes with high frame_window_overlap
    high_overlap_nodes = set()
    for nid, n in nodes.items():
        if n.get("frame_window_overlap", 0) > 0.1:
            high_overlap_nodes.add(nid)

    # Count edge types connected to high-overlap nodes
    edge_counts = {et: 0 for et in EDGE_TYPES}
    edge_durations = {et: 0.0 for et in EDGE_TYPES}
    for e in edges:
        src = e.get("source", -1)
        dst = e.get("target", -1)
        etype = e.get("type", "")
        if etype in EDGE_TYPES:
            if src in high_overlap_nodes or dst in high_overlap_nodes:
                edge_counts[etype] += 1
                edge_durations[etype] += e.get("duration_ns", 0) / 1e9

    features = {
        "runnable_delay": evidence.get("runnable_delay", 0.0),
        "binder_count": edge_counts["BINDER_CALL"],
        "binder_duration_s": edge_durations["BINDER_CALL"],
        "futex_count": edge_counts["FUTEX_WAIT"],
        "futex_duration_s": edge_durations["FUTEX_WAIT"],
        "decode_count": edge_counts["DECODE_DEPENDENCY"],
        "decode_duration_s": edge_durations["DECODE_DEPENDENCY"],
        "resource_stall_count": edge_counts["RESOURCE_STALL"],
        "network_count": edge_counts["NETWORK_WAIT"],
        "buffer_count": edge_counts["BUFFER_QUEUE"],
    }
    return features


def label_frame(features):
    rd = features["runnable_delay"]
    binder = features["binder_count"]
    futex = features["futex_count"]
    decode = features["decode_count"]
    resource = features["resource_stall_count"]

    if binder > 2:
        return "BINDER_BLOCKING"
    if futex > 5:
        return "FUTEX_BLOCKING"
    if decode > 2:
        return "VIDEO_LATE_RENDER"
    if resource > 2:
        return "RESOURCE_STALL"
    return "RUNNABLE_DELAY"


def main():
    if len(sys.argv) < 4:
        print("用法: python3 graph_features.py <result.json> <graph_topology.json> <output.csv>")
        sys.exit(1)

    result_path, graph_path, out_path = sys.argv[1:4]

    nodes, edges = load_graph(graph_path)
    session, inferences = load_result(result_path)
    print(f"Session: {session}, Nodes: {len(nodes)}, Edges: {len(edges)}")
    print(f"Inferences: {len(inferences)}")

    rows = []
    for inf in inferences:
        if not inf.get("hypothesis"):
            continue
        features = extract_frame_graph_features(nodes, edges, inf)
        label = label_frame(features)
        rows.append({
            "frame_id": inf["frame_id"],
            "session": session,
            "hypothesis": inf.get("hypothesis", "UNKNOWN"),
            **features,
            "label": label,
        })

    fieldnames = [
        "frame_id", "session", "hypothesis",
        "runnable_delay", "binder_count", "binder_duration_s",
        "futex_count", "futex_duration_s", "decode_count", "decode_duration_s",
        "resource_stall_count", "network_count", "buffer_count", "label",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    label_dist = {}
    for r in rows:
        label_dist[r["label"]] = label_dist.get(r["label"], 0) + 1
    print(f"\n标签分布: {label_dist}")
    print(f"已保存 {len(rows)} 条 → {out_path}")


if __name__ == "__main__":
    main()
