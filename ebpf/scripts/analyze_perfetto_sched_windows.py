#!/usr/bin/env python3
import argparse
import csv
import json
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE_PROCESSOR = ROOT / "dependencies" / "perfetto编译工具linux-amd64" / "trace_processor_shell"


def percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    idx = round((pct / 100.0) * (len(ordered) - 1))
    return int(ordered[int(idx)])


def sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def read_frames(path):
    frames = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                start = int(row["window_start_ns"])
                end = int(row["window_end_ns"])
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            frames.append({
                "frame_token": row.get("frame_token", ""),
                "window_start_ns": start,
                "window_end_ns": end,
                "deadline_missed": str(row.get("deadline_missed", "")).lower() in {"1", "true", "yes"},
                "frame_time_ms": row.get("frame_time_ms", ""),
            })
    return frames


def build_query(frames):
    values = []
    for idx, frame in enumerate(frames):
        values.append(
            "("
            f"{idx},"
            f"{sql_string(frame['frame_token'])},"
            f"{frame['window_start_ns']},"
            f"{frame['window_end_ns']},"
            f"{1 if frame['deadline_missed'] else 0},"
            f"{sql_string(frame['frame_time_ms'])}"
            ")"
        )
    frame_values = ",\n".join(values)
    return f"""
WITH frames(frame_index, frame_token, window_start_ns, window_end_ns, deadline_missed, frame_time_ms) AS (
  VALUES
  {frame_values}
),
overlap AS (
  SELECT
    f.frame_index,
    f.frame_token,
    f.deadline_missed,
    f.frame_time_ms,
    th.utid,
    thread.tid AS tid,
    COALESCE(thread.name, '') AS comm,
    COALESCE(process.pid, '') AS pid,
    COALESCE(process.name, '') AS process_name,
    COALESCE(process.uid, '') AS uid,
    SUM(
      CASE WHEN th.state = 'Running' THEN
        max(0, min(th.ts + th.dur, f.window_end_ns) - max(th.ts, f.window_start_ns))
      ELSE 0 END
    ) AS on_cpu_ns,
    SUM(
      CASE WHEN th.state IN ('R', 'R+') THEN
        max(0, min(th.ts + th.dur, f.window_end_ns) - max(th.ts, f.window_start_ns))
      ELSE 0 END
    ) AS runnable_wait_ns,
    SUM(CASE WHEN th.state = 'Running' THEN 1 ELSE 0 END) AS running_slices,
    SUM(CASE WHEN th.state IN ('R', 'R+') THEN 1 ELSE 0 END) AS runnable_slices
  FROM frames f
  JOIN thread_state th
    ON th.dur > 0
   AND th.ts < f.window_end_ns
   AND th.ts + th.dur > f.window_start_ns
   AND th.state IN ('Running', 'R', 'R+')
  LEFT JOIN thread
    ON th.utid = thread.utid
  LEFT JOIN process
    ON thread.upid = process.upid
  GROUP BY
    f.frame_index, f.frame_token, f.deadline_missed, f.frame_time_ms,
    th.utid, thread.tid, thread.name, process.pid, process.name, process.uid
)
SELECT
  frame_index,
  frame_token,
  deadline_missed,
  frame_time_ms,
  utid,
  tid,
  comm,
  pid,
  process_name,
  uid,
  on_cpu_ns,
  runnable_wait_ns,
  running_slices,
  runnable_slices
FROM overlap
WHERE on_cpu_ns > 0 OR runnable_wait_ns > 0
ORDER BY deadline_missed DESC, frame_index, (on_cpu_ns + runnable_wait_ns) DESC;
"""


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
    header_idx = None
    for idx, line in enumerate(lines):
        if line.startswith('"frame_index"') or line.startswith("frame_index,"):
            header_idx = idx
            break
    if header_idx is None:
        raise RuntimeError("trace_processor output did not contain frame scheduler CSV")
    csv_lines = []
    for line in lines[header_idx:]:
        if line.startswith("[") or line.startswith("Loading trace:") or line.startswith("column "):
            break
        csv_lines.append(line)
    return list(csv.DictReader(csv_lines))


def as_int(row, key):
    try:
        return int(row.get(key, 0) or 0)
    except ValueError:
        return 0


def is_int(value):
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def build_summaries(rows, top_k, exclude_comm_regex):
    thread_stats = {}
    frame_stats = defaultdict(lambda: {
        "thread_count": 0,
        "on_cpu_ns": 0,
        "runnable_wait_ns": 0,
        "deadline_missed": 0,
        "frame_time_ms": "",
        "top_thread": "",
        "top_thread_score_ns": 0,
    })
    per_thread_frame_runnable = defaultdict(list)

    for row in rows:
        on_cpu = as_int(row, "on_cpu_ns")
        runnable = as_int(row, "runnable_wait_ns")
        tid = row.get("tid", "")
        key = (tid, row.get("comm", ""), row.get("process_name", ""), row.get("pid", ""), row.get("uid", ""))
        if key not in thread_stats:
            thread_stats[key] = {
                "tid": tid,
                "comm": row.get("comm", ""),
                "pid": row.get("pid", ""),
                "process_name": row.get("process_name", ""),
                "uid": row.get("uid", ""),
                "frame_count": 0,
                "jank_frame_count": 0,
                "on_cpu_ns": 0,
                "runnable_wait_ns": 0,
                "running_slices": 0,
                "runnable_slices": 0,
            }
        stat = thread_stats[key]
        stat["frame_count"] += 1
        stat["jank_frame_count"] += as_int(row, "deadline_missed")
        stat["on_cpu_ns"] += on_cpu
        stat["runnable_wait_ns"] += runnable
        stat["running_slices"] += as_int(row, "running_slices")
        stat["runnable_slices"] += as_int(row, "runnable_slices")
        per_thread_frame_runnable[key].append(runnable)

        frame_key = (row.get("frame_index", ""), row.get("frame_token", ""))
        frame = frame_stats[frame_key]
        frame["thread_count"] += 1
        frame["deadline_missed"] = as_int(row, "deadline_missed")
        frame["frame_time_ms"] = row.get("frame_time_ms", "")
        frame["on_cpu_ns"] += on_cpu
        frame["runnable_wait_ns"] += runnable
        score = on_cpu + runnable
        if score > frame["top_thread_score_ns"]:
            frame["top_thread_score_ns"] = score
            frame["top_thread"] = f"{row.get('comm', '')}:{tid}"

    exclude_pattern = re.compile(exclude_comm_regex) if exclude_comm_regex else None
    thread_rows = []
    for key, stat in thread_stats.items():
        text = " ".join(str(stat.get(field, "")) for field in ("comm", "process_name", "pid", "tid"))
        if exclude_pattern and exclude_pattern.search(text):
            continue
        runnable_values = per_thread_frame_runnable[key]
        stat["runnable_wait_p95_ns"] = percentile(runnable_values, 95)
        stat["critical_score_ns"] = stat["on_cpu_ns"] + stat["runnable_wait_ns"]
        stat["on_cpu_ms"] = round(stat["on_cpu_ns"] / 1e6, 3)
        stat["runnable_wait_ms"] = round(stat["runnable_wait_ns"] / 1e6, 3)
        stat["runnable_wait_p95_ms"] = round(stat["runnable_wait_p95_ns"] / 1e6, 3)
        thread_rows.append(stat)
    thread_rows.sort(key=lambda row: (row["jank_frame_count"], row["critical_score_ns"]), reverse=True)
    for idx, row in enumerate(thread_rows, 1):
        row["rank"] = idx

    frame_rows = []
    for (frame_index, frame_token), stat in frame_stats.items():
        row = {"frame_index": frame_index, "frame_token": frame_token, **stat}
        row["on_cpu_ms"] = round(row["on_cpu_ns"] / 1e6, 3)
        row["runnable_wait_ms"] = round(row["runnable_wait_ns"] / 1e6, 3)
        frame_rows.append(row)
    frame_rows.sort(key=lambda row: int(row["frame_index"] or 0))

    return thread_rows[:top_k], frame_rows


def write_csv(path, rows, fieldnames):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Aggregate Perfetto sched thread_state rows inside FrameTimeline windows.")
    parser.add_argument("trace", help="Input .perfetto-trace")
    parser.add_argument("--frames-csv", required=True, help="FrameTimeline CSV from parse_perfetto_frametimeline.py")
    parser.add_argument("--trace-processor", default=str(DEFAULT_TRACE_PROCESSOR))
    parser.add_argument("--frame-thread-csv-out", required=True)
    parser.add_argument("--thread-summary-csv-out", required=True)
    parser.add_argument("--frame-summary-csv-out", required=True)
    parser.add_argument("--summary-out", required=True)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument(
        "--exclude-comm-regex",
        default=r"(^|[/\s])(tracepilot|traced|traced_probes)(\s|$)|trace_processor_shell",
        help="Regex excluded from ranked thread summary only; raw frame-thread CSV keeps all rows.",
    )
    args = parser.parse_args()

    trace_processor = Path(args.trace_processor)
    if not trace_processor.exists():
        raise SystemExit(f"trace_processor_shell not found: {trace_processor}")

    frames = read_frames(args.frames_csv)
    if not frames:
        raise SystemExit(f"No usable frame windows found in {args.frames_csv}")

    output = run_trace_processor(trace_processor, Path(args.trace), build_query(frames))
    rows = [
        row for row in parse_csv_output(output)
        if is_int(row.get("frame_index"))
    ]
    write_csv(
        args.frame_thread_csv_out,
        rows,
        [
            "frame_index", "frame_token", "deadline_missed", "frame_time_ms", "utid", "tid",
            "comm", "pid", "process_name", "uid", "on_cpu_ns", "runnable_wait_ns",
            "running_slices", "runnable_slices",
        ],
    )
    top_threads, frame_rows = build_summaries(rows, args.top_k, args.exclude_comm_regex)
    write_csv(
        args.thread_summary_csv_out,
        top_threads,
        [
            "rank", "tid", "comm", "pid", "process_name", "uid", "frame_count",
            "jank_frame_count", "on_cpu_ms", "runnable_wait_ms", "runnable_wait_p95_ms",
            "running_slices", "runnable_slices", "critical_score_ns",
        ],
    )
    write_csv(
        args.frame_summary_csv_out,
        frame_rows,
        [
            "frame_index", "frame_token", "deadline_missed", "frame_time_ms", "thread_count",
            "on_cpu_ms", "runnable_wait_ms", "top_thread", "top_thread_score_ns",
        ],
    )
    summary = {
        "trace": args.trace,
        "frames_csv": args.frames_csv,
        "frame_count": len(frames),
        "frame_thread_rows": len(rows),
        "top_k": args.top_k,
        "exclude_comm_regex": args.exclude_comm_regex,
        "top_threads": top_threads,
        "outputs": {
            "frame_thread_csv": args.frame_thread_csv_out,
            "thread_summary_csv": args.thread_summary_csv_out,
            "frame_summary_csv": args.frame_summary_csv_out,
        },
        "method": "Perfetto thread_state overlap with FrameTimeline windows; Running=on_cpu, R/R+=runnable_wait.",
    }
    Path(args.summary_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
