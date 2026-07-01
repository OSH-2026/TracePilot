#!/usr/bin/env python3
"""
parse_surfaceflinger_latency.py — SurfaceFlinger 延迟解析
从 Perfetto trace 中提取 SurfaceFlinger 的帧延迟数据，
用于辅助分析渲染管线瓶颈。

用法:
  python parse_surfaceflinger_latency.py <perfetto_trace> <output.txt>
"""
import argparse
import csv
import json
from pathlib import Path


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = round((pct / 100.0) * (len(ordered) - 1))
    return ordered[int(idx)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("latency_trace")
    parser.add_argument("--csv-out", required=True)
    parser.add_argument("--summary-out", required=True)
    parser.add_argument("--layer", default="")
    args = parser.parse_args()

    lines = Path(args.latency_trace).read_text(encoding="utf-8", errors="replace").splitlines()
    refresh_ns = int(lines[0].strip()) if lines and lines[0].strip().isdigit() else 16_666_667
    present_times = []
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            _, actual_present_ns, _ = (int(value) for value in fields)
        except ValueError:
            continue
        if actual_present_ns > 0:
            present_times.append(actual_present_ns)

    budget_ns = round(refresh_ns * 1.5)
    rows = []
    intervals_ms = []
    for index, (start_ns, end_ns) in enumerate(zip(present_times, present_times[1:])):
        interval_ns = end_ns - start_ns
        if interval_ns <= 0:
            continue
        interval_ms = interval_ns / 1e6
        missed = interval_ns > budget_ns
        intervals_ms.append(interval_ms)
        rows.append({
            "frame_index": index,
            "intended_vsync_ns": start_ns,
            "frame_deadline_ns": start_ns + budget_ns,
            "frame_completed_ns": end_ns,
            "frame_time_ms": round(interval_ms, 3),
            "janky_16_6ms": interval_ns > refresh_ns,
            "deadline_missed": missed,
            "flags": "surfaceflinger_present_interval",
        })

    with Path(args.csv_out).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "frame_index", "intended_vsync_ns", "frame_deadline_ns",
            "frame_completed_ns", "frame_time_ms", "janky_16_6ms",
            "deadline_missed", "flags",
        ])
        writer.writeheader()
        writer.writerows(rows)

    missed_count = sum(1 for row in rows if row["deadline_missed"])
    summary = {
        "source": "SurfaceFlinger --latency selected app layer",
        "layer": args.layer,
        "latency_trace": args.latency_trace,
        "frames_csv": args.csv_out,
        "refresh_period_ns": refresh_ns,
        "refresh_period_ms": round(refresh_ns / 1e6, 3),
        "classification_rule": "presentation interval > 1.5 * refresh period",
        "frame_count": len(rows),
        "deadline_missed_count": missed_count,
        "deadline_missed_rate": round(missed_count / len(rows), 4) if rows else 0,
        "frame_time_avg_ms": round(sum(intervals_ms) / len(intervals_ms), 3) if intervals_ms else 0,
        "frame_time_p50_ms": round(percentile(intervals_ms, 50), 3),
        "frame_time_p95_ms": round(percentile(intervals_ms, 95), 3),
        "frame_time_p99_ms": round(percentile(intervals_ms, 99), 3),
        "reported_gfxinfo_summary": {},
        "note": "SurfaceFlinger presentation intervals cover the selected app surface; labels are interval-based suspected jank, not app FrameDeadline verdicts.",
    }
    Path(args.summary_out).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
