#!/usr/bin/env python3
import argparse
import csv
import gzip
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


EVENT_TYPES = ("sched_switch", "sched_waking", "sched_wakeup", "cpu_frequency")


def percentile(values, pct):
    if not values:
        return 0
    values = sorted(values)
    idx = int(round((pct / 100.0) * (len(values) - 1)))
    return values[idx]


def open_trace(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "rt", encoding="utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--comm-regex", default=r"chrome|cr|compositor|render|gpu|viz")
    parser.add_argument("--file-prefix", default="feed_scroll")
    args = parser.parse_args()

    trace_path = Path(args.trace)
    out_dir = Path(args.out_dir) if args.out_dir else trace_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    target_re = re.compile(args.comm_regex, re.IGNORECASE)
    comm_by_tid = {}
    target_tids = set()
    event_counts = Counter()
    per_second = defaultdict(Counter)

    running = {}
    wake_ts = {}
    runnable_start = {}
    last_cpu = {}

    on_cpu_ns = defaultdict(int)
    wakeup_to_run_ns = defaultdict(list)
    runnable_delay_ns = defaultdict(list)
    migration_count = Counter()
    thread_event_counts = defaultdict(Counter)

    first_ts = None
    last_ts = None

    with open_trace(trace_path) as f:
        for line in f:
            event = json.loads(line)
            typ = event.get("type")
            ts = event.get("ts_ns")
            if ts is None:
                continue

            if first_ts is None:
                first_ts = ts
            last_ts = ts

            window_sec = int((ts - first_ts) // 1_000_000_000)
            event_counts[typ] += 1
            per_second[window_sec][typ] += 1
            per_second[window_sec]["total_events"] += 1

            if typ in ("sched_waking", "sched_wakeup"):
                tid = event.get("pid")
                comm = event.get("comm", "")
                if tid is not None:
                    comm_by_tid[tid] = comm or comm_by_tid.get(tid, "")
                    thread_event_counts[tid][typ] += 1
                    if target_re.search(comm):
                        target_tids.add(tid)
                    if typ == "sched_waking":
                        wake_ts[tid] = ts
                continue

            if typ == "cpu_frequency":
                continue

            if typ != "sched_switch":
                continue

            cpu = event.get("cpu")
            prev_tid = event.get("prev_pid")
            next_tid = event.get("next_pid")
            prev_comm = event.get("prev_comm", "")
            next_comm = event.get("next_comm", "")
            prev_state = event.get("prev_state", 0)

            if prev_tid is not None:
                comm_by_tid[prev_tid] = prev_comm or comm_by_tid.get(prev_tid, "")
                thread_event_counts[prev_tid]["sched_switch_out"] += 1
                if target_re.search(prev_comm):
                    target_tids.add(prev_tid)
            if next_tid is not None:
                comm_by_tid[next_tid] = next_comm or comm_by_tid.get(next_tid, "")
                thread_event_counts[next_tid]["sched_switch_in"] += 1
                if target_re.search(next_comm):
                    target_tids.add(next_tid)

            if cpu in running:
                tid, start_ts = running[cpu]
                if tid in target_tids and ts >= start_ts:
                    on_cpu_ns[tid] += ts - start_ts

            if prev_tid in target_tids and prev_state == 0:
                runnable_start[prev_tid] = ts

            if next_tid in target_tids:
                if next_tid in wake_ts and ts >= wake_ts[next_tid]:
                    wakeup_to_run_ns[next_tid].append(ts - wake_ts.pop(next_tid))
                if next_tid in runnable_start and ts >= runnable_start[next_tid]:
                    runnable_delay_ns[next_tid].append(ts - runnable_start.pop(next_tid))
                if next_tid in last_cpu and last_cpu[next_tid] != cpu:
                    migration_count[next_tid] += 1
                last_cpu[next_tid] = cpu

            if cpu is not None and next_tid is not None:
                running[cpu] = (next_tid, ts)

    by_second_path = out_dir / f"{args.file_prefix}_events_by_second.csv"
    with open(by_second_path, "w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "window_sec",
            "window_start_ns",
            "window_end_ns",
            "total_events",
            *EVENT_TYPES,
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for sec in sorted(per_second):
            counts = per_second[sec]
            row = {
                "window_sec": sec,
                "window_start_ns": first_ts + sec * 1_000_000_000,
                "window_end_ns": first_ts + (sec + 1) * 1_000_000_000,
                "total_events": counts.get("total_events", 0),
            }
            for typ in EVENT_TYPES:
                row[typ] = counts.get(typ, 0)
            writer.writerow(row)

    threads_path = out_dir / f"{args.file_prefix}_threads_summary.csv"
    with open(threads_path, "w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "tid",
            "comm",
            "on_cpu_ms",
            "migration_count",
            "wakeup_to_run_count",
            "wakeup_to_run_p95_ms",
            "wakeup_to_run_p99_ms",
            "runnable_delay_count",
            "runnable_delay_p95_ms",
            "runnable_delay_p99_ms",
            "sched_switch_in",
            "sched_switch_out",
            "sched_waking",
            "sched_wakeup",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        tids = sorted(target_tids, key=lambda tid: on_cpu_ns.get(tid, 0), reverse=True)
        for tid in tids:
            wake_values = wakeup_to_run_ns.get(tid, [])
            runnable_values = runnable_delay_ns.get(tid, [])
            row = {
                "tid": tid,
                "comm": comm_by_tid.get(tid, ""),
                "on_cpu_ms": round(on_cpu_ns.get(tid, 0) / 1e6, 3),
                "migration_count": migration_count.get(tid, 0),
                "wakeup_to_run_count": len(wake_values),
                "wakeup_to_run_p95_ms": round(percentile(wake_values, 95) / 1e6, 3),
                "wakeup_to_run_p99_ms": round(percentile(wake_values, 99) / 1e6, 3),
                "runnable_delay_count": len(runnable_values),
                "runnable_delay_p95_ms": round(percentile(runnable_values, 95) / 1e6, 3),
                "runnable_delay_p99_ms": round(percentile(runnable_values, 99) / 1e6, 3),
                "sched_switch_in": thread_event_counts[tid].get("sched_switch_in", 0),
                "sched_switch_out": thread_event_counts[tid].get("sched_switch_out", 0),
                "sched_waking": thread_event_counts[tid].get("sched_waking", 0),
                "sched_wakeup": thread_event_counts[tid].get("sched_wakeup", 0),
            }
            writer.writerow(row)

    print(json.dumps({
        "trace": str(trace_path),
        "events_by_second_csv": str(by_second_path),
        "threads_summary_csv": str(threads_path),
        "duration_s": round((last_ts - first_ts) / 1e9, 3) if first_ts and last_ts else 0,
        "raw_event_count": sum(event_counts.values()),
        "second_rows": len(per_second),
        "target_thread_rows": len(target_tids),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
