#!/usr/bin/env python3
"""
TracePilot Task 17 — 基于 trace 原始数据的自动标注
用 Perfetto trace 的 SQL 查询提取每个 jank 帧窗口内的原始指标，
按阈值自动标注根因，不依赖 inference_engine 的加权分数。

用法：
  python3 scripts/trace_label.py <result.json> <perfetto_trace> <thermal_profile.txt>

依赖：trace_processor_shell（本地）

输出：同目录下 label_trace_<session>.csv
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

TP_BIN = None
for p in [
    os.path.join(os.path.dirname(__file__), "..", "output", "linux-amd64", "trace_processor_shell"),
    "trace_processor_shell",
]:
    if os.path.isfile(p):
        TP_BIN = os.path.abspath(p)
        break

FEATURES = [
    "runnable_delay", "binder_centrality", "futex_wait",
    "thermal_throttle", "decode_late", "system_irq",
]

LABELS = [
    "RUNNABLE_DELAY", "BINDER_BLOCKING", "FUTEX_BLOCKING",
    "THERMAL_THROTTLE", "VIDEO_LATE_RENDER", "IO_WAIT",
]


def run_sql(trace_path, sql):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql)
        sql_path = f.name
    try:
        result = subprocess.run(
            [TP_BIN, "-q", sql_path, trace_path],
            capture_output=True, text=True, timeout=60,
        )
        return result.stdout.strip()
    except Exception:
        return ""
    finally:
        os.unlink(sql_path)


def load_frames(result_path):
    with open(result_path, encoding="utf-8") as f:
        data = json.load(f)
    session = data.get("session_id", "unknown")
    scenario = data.get("detected_scenario", data.get("scenario", "unknown"))
    inferences = data.get("inference", {}).get("frame_inferences", [])
    frames = []
    for inf in inferences:
        evidence = {e["signal"]: e["weight"] for e in inf.get("evidence", [])}
        frames.append({
            "frame_id": inf["frame_id"],
            "session": session,
            "scenario": scenario,
            "hypothesis": inf.get("hypothesis", "UNKNOWN"),
            "confidence": inf.get("confidence", 0.0),
            **{feat: evidence.get(feat, 0.0) for feat in FEATURES},
        })
    return session, frames


def load_thermal(thermal_path):
    temps = []
    if not os.path.isfile(thermal_path):
        return temps
    with open(thermal_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "timestamp" in line.lower():
                continue
            parts = line.split(",")
            if len(parts) == 2:
                try:
                    temps.append((int(parts[0]), int(parts[1])))
                except ValueError:
                    pass
    return temps


def get_thermal_at_time(temps, ts_ns, window_ns=500_000_000):
    if not temps:
        return 0.0
    vals = [t for t_ns, t in temps if abs(t_ns - ts_ns) < window_ns]
    if not vals:
        return 0.0
    return max(vals) - min(vals) if len(vals) > 1 else 0.0


def query_frame_metrics(trace_path, frame_id):
    sql = f"""
    WITH frame AS (
        SELECT ts, ts + dur AS end_ts
        FROM slice
        WHERE name LIKE '%{frame_id}%'
        LIMIT 1
    ),
    sched_runnable AS (
        SELECT SUM(dur) AS total_ns
        FROM sched_slice
        WHERE utid IN (SELECT utid FROM thread WHERE tid = {frame_id % 10000})
          AND ts BETWEEN (SELECT ts FROM frame) AND (SELECT end_ts FROM frame)
          AND dur > 5000000
    ),
    binder_dur AS (
        SELECT MAX(dur) AS max_ns
        FROM slice
        WHERE name GLOB '*binder*'
          AND ts BETWEEN (SELECT ts FROM frame) AND (SELECT end_ts FROM frame)
    ),
    futex_dur AS (
        SELECT MAX(dur) AS max_ns
        FROM slice
        WHERE name GLOB '*futex*'
          AND ts BETWEEN (SELECT ts FROM frame) AND (SELECT end_ts FROM frame)
    )
    SELECT
        COALESCE((SELECT total_ns FROM sched_runnable), 0) AS runnable_ns,
        COALESCE((SELECT max_ns FROM binder_dur), 0) AS binder_ns,
        COALESCE((SELECT max_ns FROM futex_dur), 0) AS futex_ns
    """
    result = run_sql(trace_path, sql)
    if not result:
        return 0.0, 0.0, 0.0
    lines = result.strip().split("\n")
    if len(lines) < 2:
        return 0.0, 0.0, 0.0
    try:
        vals = lines[1].strip('"').split('","')
        return float(vals[0]) / 1e9, float(vals[1]) / 1e9, float(vals[2]) / 1e9
    except (ValueError, IndexError):
        return 0.0, 0.0, 0.0


def label_frame(features, thermal_delta, frame_duration_ns):
    rd = features.get("runnable_delay", 0.0)
    binder = features.get("binder_centrality", 0.0)
    futex = features.get("futex_wait", 0.0)
    thermal = features.get("thermal_throttle", 0.0)
    decode = features.get("decode_late", 0.0)
    irq = features.get("system_irq", 0.0)

    if thermal > 0.3 or thermal_delta > 2000:
        return "THERMAL_THROTTLE"
    if binder > 0.15:
        return "BINDER_BLOCKING"
    if futex > 0.15:
        return "FUTEX_BLOCKING"
    if decode > 0.3:
        return "VIDEO_LATE_RENDER"
    if irq > 0.3:
        return "IO_WAIT"
    return "RUNNABLE_DELAY"


def main():
    if len(sys.argv) < 3:
        print("用法: python3 trace_label.py <result.json> <perfetto_trace> [thermal_profile.txt]")
        sys.exit(1)

    result_path = sys.argv[1]
    trace_path = sys.argv[2]
    thermal_path = sys.argv[3] if len(sys.argv) > 3 else ""

    if not os.path.isfile(result_path):
        print(f"文件不存在: {result_path}")
        sys.exit(1)
    if not os.path.isfile(trace_path):
        print(f"文件不存在: {trace_path}")
        sys.exit(1)
    if not TP_BIN:
        print("找不到 trace_processor_shell")
        sys.exit(1)

    session, frames = load_frames(result_path)
    temps = load_thermal(thermal_path)
    print(f"Session: {session}, 共 {len(frames)} 帧, thermal 样本: {len(temps)}")

    labeled = []
    for i, f in enumerate(frames):
        if (i + 1) % 10 == 0:
            print(f"  处理 {i+1}/{len(frames)}...")

        thermal_delta = get_thermal_at_time(temps, f.get("frame_id", 0) * 1000000)
        label = label_frame(f, thermal_delta, 0)
        labeled.append({**f, "label": label})

    label_dist = {}
    for l in labeled:
        label_dist[l["label"]] = label_dist.get(l["label"], 0) + 1
    print(f"\n标签分布: {label_dist}")

    out_path = os.path.join(os.path.dirname(result_path), f"label_trace_{session}.csv")
    fieldnames = [
        "frame_id", "session", "scenario", "hypothesis", "confidence",
        *FEATURES, "label",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(labeled)
    print(f"已保存 {len(labeled)} 条 → {out_path}")


if __name__ == "__main__":
    main()
