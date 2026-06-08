#!/usr/bin/env python3
import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


ENH_RE = re.compile(
    r"\[DBG\]\s+ENH\s+(?P<event>BINDER_CALL|FUTEX_WAIT)\s+"
    r"ts=(?P<ts>\d+)\s+tid=(?P<tid>\d+)\s+comm=(?P<comm>.*)$"
)


DEFAULT_RELEVANT = r"Unity|sgame|surfaceflinger|RenderThread|Input|binder:600|binder:15364|HwBinder"


def write_csv(path, rows, fieldnames):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Extract TracePilot debug ENH Binder/Futex candidates from stdout.")
    parser.add_argument("stdout")
    parser.add_argument("--summary-out", required=True)
    parser.add_argument("--csv-out", required=True)
    parser.add_argument("--relevant-regex", default=DEFAULT_RELEVANT)
    parser.add_argument("--top-k", type=int, default=30)
    args = parser.parse_args()

    relevant = re.compile(args.relevant_regex, re.IGNORECASE)
    by_key = defaultdict(lambda: {
        "event": "",
        "tid": "",
        "comm": "",
        "count": 0,
        "first_ts_ns": None,
        "last_ts_ns": None,
        "relevant_to_game_pipeline": False,
    })
    event_counts = defaultdict(int)
    relevant_event_counts = defaultdict(int)
    total_lines = 0
    matched_lines = 0

    with Path(args.stdout).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            total_lines += 1
            match = ENH_RE.search(line)
            if not match:
                continue
            matched_lines += 1
            event = match.group("event")
            tid = match.group("tid")
            comm = match.group("comm").strip()
            ts = int(match.group("ts"))
            key = (event, tid, comm)
            row = by_key[key]
            row.update({"event": event, "tid": tid, "comm": comm})
            row["count"] += 1
            row["first_ts_ns"] = ts if row["first_ts_ns"] is None else min(row["first_ts_ns"], ts)
            row["last_ts_ns"] = ts if row["last_ts_ns"] is None else max(row["last_ts_ns"], ts)
            is_relevant = bool(relevant.search(comm))
            row["relevant_to_game_pipeline"] = row["relevant_to_game_pipeline"] or is_relevant
            event_counts[event] += 1
            if is_relevant:
                relevant_event_counts[event] += 1

    rows = list(by_key.values())
    rows.sort(key=lambda row: (row["relevant_to_game_pipeline"], row["count"]), reverse=True)
    for idx, row in enumerate(rows, 1):
        row["rank"] = idx
        row["duration_ms"] = round((row["last_ts_ns"] - row["first_ts_ns"]) / 1e6, 3) if row["first_ts_ns"] else 0

    write_csv(
        args.csv_out,
        rows,
        [
            "rank", "event", "tid", "comm", "count", "relevant_to_game_pipeline",
            "first_ts_ns", "last_ts_ns", "duration_ms",
        ],
    )
    summary = {
        "stdout": args.stdout,
        "total_lines": total_lines,
        "enhanced_event_lines": matched_lines,
        "event_counts": dict(event_counts),
        "relevant_regex": args.relevant_regex,
        "relevant_event_counts": dict(relevant_event_counts),
        "top_candidates": rows[:args.top_k],
        "outputs": {"csv": args.csv_out},
        "method": "Parse TracePilot debug lines of the form [DBG] ENH BINDER_CALL/FUTEX_WAIT ts=... tid=... comm=...",
        "limitation": "Debug ENH lines provide candidate event ownership by comm/tid, not full blocking duration or cross-thread dependency proof.",
    }
    Path(args.summary_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
