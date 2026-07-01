#!/usr/bin/env python3
"""
analyze_perfetto_cpu_freq_windows.py — CPU 大小核帧窗口频率归因
读取 Perfetto trace 中的 cpu_frequency 事件，按 jank 帧窗口
聚合 little/big 核心频率分布，输出 freq_throttle_ratio。

用法:
  python analyze_perfetto_cpu_freq_windows.py <trace_file> <frames.txt> <output.json>
"""
import argparse
import csv
import json
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def default_trace_processor():
    candidates = list((ROOT / "dependencies").glob("*/trace_processor_shell"))
    return str(candidates[0]) if candidates else "trace_processor_shell"


def sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    idx = round((pct / 100.0) * (len(ordered) - 1))
    return ordered[int(idx)]


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
                "frame_index": len(frames),
                "frame_token": row.get("frame_token", ""),
                "window_start_ns": start,
                "window_end_ns": end,
                "deadline_missed": str(row.get("deadline_missed", "")).lower() in {"1", "true", "yes"},
                "frame_time_ms": row.get("frame_time_ms", ""),
            })
    return frames


def frame_values_sql(frames):
    return ",\n".join(
        "("
        f"{frame['frame_index']},"
        f"{sql_string(frame['frame_token'])},"
        f"{frame['window_start_ns']},"
        f"{frame['window_end_ns']},"
        f"{1 if frame['deadline_missed'] else 0},"
        f"{sql_string(frame['frame_time_ms'])}"
        ")"
        for frame in frames
    )


def cluster_case():
    return """
CASE
  WHEN cpu BETWEEN 0 AND 3 THEN 'little'
  WHEN cpu BETWEEN 4 AND 5 THEN 'middle'
  WHEN cpu BETWEEN 6 AND 7 THEN 'big'
  ELSE 'other'
END
"""


def build_frame_freq_query(frames):
    values = frame_values_sql(frames)
    cluster = cluster_case()
    max_end = max(frame["window_end_ns"] for frame in frames)
    return f"""
WITH frames(frame_index, frame_token, window_start_ns, window_end_ns, deadline_missed, frame_time_ms) AS (
  VALUES
  {values}
),
freq AS (
  SELECT
    ct.cpu AS cpu,
    c.ts AS ts,
    COALESCE(
      LEAD(c.ts) OVER (PARTITION BY c.track_id ORDER BY c.ts),
      {max_end}
    ) AS next_ts,
    c.value AS freq_khz
  FROM counter c
  JOIN cpu_counter_track ct
    ON c.track_id = ct.id
  WHERE ct.type = 'cpu_frequency'
),
overlap AS (
  SELECT
    f.frame_index,
    f.frame_token,
    f.deadline_missed,
    f.frame_time_ms,
    freq.cpu,
    {cluster} AS cluster,
    max(0, min(freq.next_ts, f.window_end_ns) - max(freq.ts, f.window_start_ns)) AS overlap_ns,
    freq.freq_khz AS freq_khz
  FROM frames f
  JOIN freq
    ON freq.next_ts > f.window_start_ns
   AND freq.ts < f.window_end_ns
)
SELECT
  frame_index,
  frame_token,
  deadline_missed,
  frame_time_ms,
  cluster,
  SUM(overlap_ns) AS sampled_time_ns,
  SUM(freq_khz * overlap_ns) / SUM(overlap_ns) AS avg_freq_khz,
  MIN(freq_khz) AS min_freq_khz,
  MAX(freq_khz) AS max_freq_khz,
  COUNT(*) AS sample_count
FROM overlap
WHERE overlap_ns > 0
GROUP BY frame_index, frame_token, deadline_missed, frame_time_ms, cluster
ORDER BY frame_index, cluster;
"""


def build_thread_cluster_query(frames):
    values = frame_values_sql(frames)
    cluster = cluster_case()
    return f"""
WITH frames(frame_index, frame_token, window_start_ns, window_end_ns, deadline_missed, frame_time_ms) AS (
  VALUES
  {values}
),
running AS (
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
    CAST(th.cpu AS INTEGER) AS cpu,
    max(0, min(th.ts + th.dur, f.window_end_ns) - max(th.ts, f.window_start_ns)) AS overlap_ns
  FROM frames f
  JOIN thread_state th
    ON th.dur > 0
   AND th.ts < f.window_end_ns
   AND th.ts + th.dur > f.window_start_ns
   AND th.state = 'Running'
  LEFT JOIN thread
    ON th.utid = thread.utid
  LEFT JOIN process
    ON thread.upid = process.upid
)
SELECT
  tid,
  comm,
  pid,
  process_name,
  uid,
  {cluster} AS cluster,
  SUM(overlap_ns) AS on_cpu_ns,
  SUM(deadline_missed) AS jank_frame_slices,
  COUNT(DISTINCT frame_index) AS frame_count,
  COUNT(*) AS running_slices
FROM running
WHERE overlap_ns > 0
GROUP BY tid, comm, pid, process_name, uid, cluster
ORDER BY on_cpu_ns DESC;
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


def parse_csv_output(output, first_header):
    lines = [line for line in output.splitlines() if line.strip()]
    header_idx = None
    for idx, line in enumerate(lines):
        if line.startswith(f'"{first_header}"') or line.startswith(f"{first_header},"):
            header_idx = idx
            break
    if header_idx is None:
        raise RuntimeError(f"trace_processor output did not contain {first_header} CSV")
    csv_lines = []
    for line in lines[header_idx:]:
        if line.startswith("[") or line.startswith("Loading trace:") or line.startswith("column "):
            break
        csv_lines.append(line)
    return list(csv.DictReader(csv_lines))


def valid_int(value):
    try:
        int(float(value))
        return True
    except (TypeError, ValueError):
        return False


def write_csv(path, rows, fieldnames):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def to_float(row, key):
    try:
        return float(row.get(key, 0) or 0)
    except ValueError:
        return 0.0


def to_int(row, key):
    try:
        return int(float(row.get(key, 0) or 0))
    except ValueError:
        return 0


def summarize(frame_freq_rows, thread_rows, top_k):
    clusters = defaultdict(lambda: {"sampled_time_ns": 0, "freq_weighted_sum": 0.0, "min": None, "max": None})
    jank_clusters = defaultdict(lambda: {"sampled_time_ns": 0, "freq_weighted_sum": 0.0})
    for row in frame_freq_rows:
        cluster = row.get("cluster", "")
        if cluster not in {"little", "middle", "big", "other"} or not valid_int(row.get("frame_index")):
            continue
        sampled = to_float(row, "sampled_time_ns")
        avg = to_float(row, "avg_freq_khz")
        stat = clusters[cluster]
        stat["sampled_time_ns"] += sampled
        stat["freq_weighted_sum"] += sampled * avg
        mn = to_float(row, "min_freq_khz")
        mx = to_float(row, "max_freq_khz")
        stat["min"] = mn if stat["min"] is None else min(stat["min"], mn)
        stat["max"] = mx if stat["max"] is None else max(stat["max"], mx)
        if to_int(row, "deadline_missed"):
            jstat = jank_clusters[cluster]
            jstat["sampled_time_ns"] += sampled
            jstat["freq_weighted_sum"] += sampled * avg

    cluster_summary = {}
    for cluster, stat in clusters.items():
        sampled = stat["sampled_time_ns"]
        jstat = jank_clusters.get(cluster, {})
        j_sampled = jstat.get("sampled_time_ns", 0)
        cluster_summary[cluster] = {
            "avg_freq_khz": round(stat["freq_weighted_sum"] / sampled, 1) if sampled else 0,
            "jank_avg_freq_khz": round(jstat.get("freq_weighted_sum", 0) / j_sampled, 1) if j_sampled else 0,
            "min_freq_khz": round(stat["min"], 1) if stat["min"] is not None else 0,
            "max_freq_khz": round(stat["max"], 1) if stat["max"] is not None else 0,
            "sampled_time_ns": int(sampled),
        }

    per_thread = defaultdict(lambda: {
        "tid": "",
        "comm": "",
        "pid": "",
        "process_name": "",
        "uid": "",
        "little_on_cpu_ns": 0,
        "middle_on_cpu_ns": 0,
        "big_on_cpu_ns": 0,
        "other_on_cpu_ns": 0,
        "frame_count": 0,
        "running_slices": 0,
    })
    for row in thread_rows:
        key = (row.get("tid", ""), row.get("comm", ""), row.get("pid", ""), row.get("process_name", ""), row.get("uid", ""))
        stat = per_thread[key]
        stat.update({
            "tid": row.get("tid", ""),
            "comm": row.get("comm", ""),
            "pid": row.get("pid", ""),
            "process_name": row.get("process_name", ""),
            "uid": row.get("uid", ""),
        })
        cluster = row.get("cluster", "other")
        stat[f"{cluster}_on_cpu_ns"] += to_int(row, "on_cpu_ns")
        stat["frame_count"] = max(stat["frame_count"], to_int(row, "frame_count"))
        stat["running_slices"] += to_int(row, "running_slices")

    ranked_threads = []
    for stat in per_thread.values():
        total = (
            stat["little_on_cpu_ns"] + stat["middle_on_cpu_ns"] +
            stat["big_on_cpu_ns"] + stat["other_on_cpu_ns"]
        )
        if total <= 0:
            continue
        stat["total_on_cpu_ns"] = total
        stat["little_on_cpu_ms"] = round(stat["little_on_cpu_ns"] / 1e6, 3)
        stat["middle_on_cpu_ms"] = round(stat["middle_on_cpu_ns"] / 1e6, 3)
        stat["big_on_cpu_ms"] = round(stat["big_on_cpu_ns"] / 1e6, 3)
        stat["total_on_cpu_ms"] = round(total / 1e6, 3)
        stat["big_middle_ratio"] = round((stat["big_on_cpu_ns"] + stat["middle_on_cpu_ns"]) / total, 4)
        ranked_threads.append(stat)
    ranked_threads.sort(key=lambda row: row["total_on_cpu_ns"], reverse=True)
    for idx, row in enumerate(ranked_threads, 1):
        row["rank"] = idx

    return cluster_summary, ranked_threads[:top_k]


def main():
    parser = argparse.ArgumentParser(description="Aggregate Perfetto cpu_frequency and Running slices inside FrameTimeline windows.")
    parser.add_argument("trace", help="Input .perfetto-trace")
    parser.add_argument("--frames-csv", required=True)
    parser.add_argument("--trace-processor", default=default_trace_processor())
    parser.add_argument("--frame-cluster-csv-out", required=True)
    parser.add_argument("--thread-cluster-csv-out", required=True)
    parser.add_argument("--summary-out", required=True)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument(
        "--exclude-comm-regex",
        default=r"(^|[/\s])(tracepilot|traced|traced_probes)(\s|$)|trace_processor_shell",
        help="Regex excluded from ranked thread summary only; raw thread cluster CSV keeps all rows.",
    )
    args = parser.parse_args()

    frames = read_frames(args.frames_csv)
    if not frames:
        raise SystemExit(f"No usable frame windows found in {args.frames_csv}")

    trace_processor = Path(args.trace_processor)
    if not trace_processor.exists():
        raise SystemExit(f"trace_processor_shell not found: {trace_processor}")

    frame_output = run_trace_processor(trace_processor, Path(args.trace), build_frame_freq_query(frames))
    frame_rows = parse_csv_output(frame_output, "frame_index")
    thread_output = run_trace_processor(trace_processor, Path(args.trace), build_thread_cluster_query(frames))
    thread_rows = parse_csv_output(thread_output, "tid")
    frame_rows = [
        row for row in frame_rows
        if row.get("cluster") in {"little", "middle", "big", "other"} and valid_int(row.get("frame_index"))
    ]
    thread_rows = [row for row in thread_rows if valid_int(row.get("tid")) and row.get("cluster") in {"little", "middle", "big", "other"}]
    exclude_pattern = re.compile(args.exclude_comm_regex) if args.exclude_comm_regex else None
    ranked_source_rows = []
    for row in thread_rows:
        text = " ".join(str(row.get(field, "")) for field in ("comm", "process_name", "pid", "tid"))
        if exclude_pattern and exclude_pattern.search(text):
            continue
        ranked_source_rows.append(row)
    cluster_summary, top_threads = summarize(frame_rows, ranked_source_rows, args.top_k)

    write_csv(
        args.frame_cluster_csv_out,
        frame_rows,
        [
            "frame_index", "frame_token", "deadline_missed", "frame_time_ms", "cluster",
            "sampled_time_ns", "avg_freq_khz", "min_freq_khz", "max_freq_khz", "sample_count",
        ],
    )
    write_csv(
        args.thread_cluster_csv_out,
        thread_rows,
        ["tid", "comm", "pid", "process_name", "uid", "cluster", "on_cpu_ns", "frame_count", "running_slices"],
    )
    summary = {
        "trace": args.trace,
        "frames_csv": args.frames_csv,
        "frame_count": len(frames),
        "exclude_comm_regex": args.exclude_comm_regex,
        "cluster_summary": cluster_summary,
        "top_threads": top_threads,
        "outputs": {
            "frame_cluster_csv": args.frame_cluster_csv_out,
            "thread_cluster_csv": args.thread_cluster_csv_out,
        },
        "method": "Perfetto cpu_frequency counters and thread_state Running slices overlapped with FrameTimeline windows; clusters use Tensor CPU0-3 little, CPU4-5 middle, CPU6-7 big.",
    }
    Path(args.summary_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
