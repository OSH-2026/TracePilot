#!/usr/bin/env python3
"""
parse_perfetto_frametimeline.py — Perfetto FrameTimeline 帧提取解析
通过 trace_processor_shell 执行 SQL 查询，从 Perfetto trace 中
提取 SF/VD/VF/AP 帧的 expected_start / actual_end / jank 标记。

用法:
  python parse_perfetto_frametimeline.py <perfetto_trace> <output_frames.txt>
"""
import argparse
import csv
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE_PROCESSOR = ROOT / "dependencies" / "perfetto编译工具linux-amd64" / "trace_processor_shell"


FRAME_QUERY = """
SELECT
  actual.name AS frame_token,
  process.pid AS pid,
  process.uid AS uid,
  process.name AS process_name,
  expected.ts AS expected_start_ns,
  expected.dur AS expected_dur_ns,
  expected.ts + expected.dur AS expected_end_ns,
  actual.ts AS actual_start_ns,
  actual.dur AS actual_dur_ns,
  actual.ts + actual.dur AS actual_end_ns,
  actual.jank_type AS jank_type
FROM actual_frame_timeline_slice actual
JOIN expected_frame_timeline_slice expected
  ON actual.name = expected.name
 AND actual.upid = expected.upid
LEFT JOIN process
  ON actual.upid = process.upid
__WHERE_CLAUSE__
ORDER BY actual.ts;
"""

PACKAGE_WHERE = """
WHERE
  process.name LIKE '%' || :package || '%'
  OR process.name LIKE '%' || :package_tail || '%'
"""


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = round((pct / 100.0) * (len(ordered) - 1))
    return ordered[int(idx)]


def run_trace_processor(trace_processor, trace_path, query):
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sql", delete=False) as handle:
        handle.write(query)
        query_file = Path(handle.name)
    try:
        attempts = [
            [str(trace_processor), str(trace_path), "--query-file", str(query_file)],
            [str(trace_processor), str(trace_path), "-q", str(query_file)],
        ]
        last_error = ""
        for cmd in attempts:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
            last_error = result.stdout
        raise RuntimeError(last_error or "trace_processor_shell returned no output")
    finally:
        query_file.unlink(missing_ok=True)


def parse_csv_output(output):
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return []
    header_idx = 0
    for idx, line in enumerate(lines):
        if line.startswith('"frame_token"') or line.startswith("frame_token,"):
            header_idx = idx
            break
    reader = csv.DictReader(lines[header_idx:])
    return list(reader)


def is_janky(jank_type):
    value = (jank_type or "").strip()
    return bool(value and value.lower() not in {"none", "0", "jank_type_none"})


def main():
    parser = argparse.ArgumentParser(description="Export all Perfetto FrameTimeline frame windows for a game package.")
    parser.add_argument("trace", help="Input .perfetto-trace")
    parser.add_argument("--package", default="com.tencent.tmgp.sgame")
    parser.add_argument("--trace-processor", default=str(DEFAULT_TRACE_PROCESSOR))
    parser.add_argument("--csv-out", required=True)
    parser.add_argument("--summary-out", required=True)
    parser.add_argument("--window-margin-ms", type=float, default=2.0)
    args = parser.parse_args()

    trace_processor = Path(args.trace_processor)
    if not trace_processor.exists():
        raise SystemExit(f"trace_processor_shell not found: {trace_processor}")

    package_tail = args.package.split(".")[-1]
    package_query = (
        FRAME_QUERY.replace("__WHERE_CLAUSE__", PACKAGE_WHERE)
        .replace(":package_tail", f"'{package_tail}'")
        .replace(":package", f"'{args.package}'")
    )
    output = run_trace_processor(trace_processor, Path(args.trace), package_query)
    rows = parse_csv_output(output)
    source_filter = "package_filter"
    if not rows:
        all_query = FRAME_QUERY.replace("__WHERE_CLAUSE__", "")
        output = run_trace_processor(trace_processor, Path(args.trace), all_query)
        rows = parse_csv_output(output)
        source_filter = "all_frametimeline_rows_fallback"

    margin_ns = int(args.window_margin_ms * 1_000_000)
    exported = []
    frame_times_ms = []
    for row in rows:
        try:
            expected_start = int(row["expected_start_ns"])
            expected_end = int(row["expected_end_ns"])
            actual_start = int(row["actual_start_ns"])
            actual_end = int(row["actual_end_ns"])
            actual_dur = int(row["actual_dur_ns"])
        except (KeyError, TypeError, ValueError):
            continue
        frame_time_ms = actual_dur / 1e6
        frame_times_ms.append(frame_time_ms)
        jank_type = row.get("jank_type", "")
        deadline_missed = "Deadline Missed" in jank_type or is_janky(jank_type)
        exported.append({
            "frame_token": row.get("frame_token", ""),
            "pid": row.get("pid", ""),
            "uid": row.get("uid", ""),
            "process_name": row.get("process_name", ""),
            "expected_start_ns": expected_start,
            "expected_end_ns": expected_end,
            "actual_start_ns": actual_start,
            "actual_end_ns": actual_end,
            "frame_time_ms": round(frame_time_ms, 3),
            "jank_type": jank_type,
            "deadline_missed": deadline_missed,
            "window_start_ns": min(expected_start, actual_start) - margin_ns,
            "window_end_ns": max(expected_end, actual_end) + margin_ns,
        })

    with Path(args.csv_out).open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "frame_token", "pid", "uid", "process_name",
            "expected_start_ns", "expected_end_ns",
            "actual_start_ns", "actual_end_ns", "frame_time_ms",
            "jank_type", "deadline_missed", "window_start_ns", "window_end_ns",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(exported)

    janky_count = sum(1 for row in exported if row["deadline_missed"])
    summary = {
        "trace": args.trace,
        "package": args.package,
        "source_filter": source_filter,
        "frames_csv": args.csv_out,
        "frame_count": len(exported),
        "deadline_missed_count": janky_count,
        "deadline_missed_rate": round(janky_count / len(exported), 4) if exported else 0,
        "frame_time_avg_ms": round(sum(frame_times_ms) / len(frame_times_ms), 3) if frame_times_ms else 0,
        "frame_time_p50_ms": round(percentile(frame_times_ms, 50), 3),
        "frame_time_p95_ms": round(percentile(frame_times_ms, 95), 3),
        "frame_time_p99_ms": round(percentile(frame_times_ms, 99), 3),
        "window_margin_ms": args.window_margin_ms,
    }
    Path(args.summary_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
