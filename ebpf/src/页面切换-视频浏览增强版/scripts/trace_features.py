#!/usr/bin/env python3
"""
TracePilot Task 17 — 基于 trace 原始事件的特征提取 + 标注
从 Perfetto trace 的 sched/binder/futex/decode 事件中直接提取每个 jank 帧的原始指标，
不依赖 inference_engine 的加权分数。

用法：
  python3 scripts/trace_features.py <frames.txt> <perfetto_trace> <thermal_profile.txt> <output.csv>

依赖：trace_processor_shell（本地）
"""
import csv
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

# Convert to WSL path
if TP_BIN:
    try:
        wsl_path = subprocess.run(
            ["wsl", "wslpath", "-a", TP_BIN.replace("\\", "/")],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if wsl_path:
            TP_BIN = wsl_path
    except Exception:
        pass


def run_sql(trace_path, sql):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql)
        sql_path = f.name
    try:
        wsl_sql = subprocess.run(
            ["wsl", "wslpath", "-a", sql_path.replace("\\", "/")],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        wsl_trace = subprocess.run(
            ["wsl", "wslpath", "-a", trace_path.replace("\\", "/")],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not wsl_sql or not wsl_trace:
            return ""
        result = subprocess.run(
            ["wsl", TP_BIN, "-q", wsl_sql, wsl_trace],
            capture_output=True, text=True, timeout=120,
        )
        return result.stdout.strip()
    except Exception:
        return ""
    finally:
        os.unlink(sql_path)


def load_frames(frames_path):
    frames = []
    with open(frames_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "frame_type" in line:
                continue
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) >= 8:
                try:
                    frames.append({
                        "frame_type": parts[0],
                        "frame_token": int(parts[1]),
                        "intended_vsync": int(parts[2]),
                        "expected_start": int(parts[3]),
                        "expected_end": int(parts[4]),
                        "actual_end": int(parts[5]),
                        "is_jank": int(parts[6]),
                        "delay_ms": float(parts[7]),
                    })
                except (ValueError, IndexError):
                    pass
    return frames


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


def get_thermal_delta(temps, ts_start, ts_end):
    if not temps:
        return 0
    in_window = [t for t_ns, t in temps if ts_start <= t_ns <= ts_end]
    if len(in_window) < 2:
        return 0
    return max(in_window) - min(in_window)


def extract_frame_features(trace_path, frame):
    ts = frame["intended_vsync"]
    end = frame["actual_end"]

    sql = f"""
    SELECT
        COALESCE(SUM(CASE WHEN s.name GLOB '*monitor contention*' THEN s.dur ELSE 0 END), 0) AS binder_ns,
        COALESCE(SUM(CASE WHEN s.name GLOB '*decode*' OR s.name GLOB '*codec*' THEN s.dur ELSE 0 END), 0) AS decode_ns,
        COALESCE(MAX(CASE WHEN s.name GLOB '*monitor contention*' THEN s.dur ELSE 0 END), 0) AS max_binder_ns,
        COALESCE(MAX(CASE WHEN s.name GLOB '*decode*' OR s.name GLOB '*codec*' THEN s.dur ELSE 0 END), 0) AS max_decode_ns,
        COUNT(CASE WHEN s.name GLOB '*monitor contention*' THEN 1 END) AS binder_count,
        COUNT(CASE WHEN s.name GLOB '*decode*' OR s.name GLOB '*codec*' THEN 1 END) AS decode_count
    FROM slice s
    WHERE s.ts >= {ts} AND s.ts <= {end}
      AND (s.name GLOB '*monitor contention*' OR s.name GLOB '*decode*' OR s.name GLOB '*codec*')
    """
    result = run_sql(trace_path, sql)
    if not result:
        return 0.0, 0.0, 0, 0, 0, 0
    lines = result.strip().split("\n")
    if len(lines) < 2:
        return 0.0, 0.0, 0, 0, 0, 0
    try:
        vals = lines[1].strip('"').split('","')
        binder_ns = float(vals[0]) if vals[0] else 0.0
        decode_ns = float(vals[1]) if vals[1] else 0.0
        max_binder_ns = float(vals[2]) if vals[2] else 0.0
        max_decode_ns = float(vals[3]) if vals[3] else 0.0
        binder_count = int(vals[4]) if vals[4] else 0
        decode_count = int(vals[5]) if vals[5] else 0
        return binder_ns / 1e9, decode_ns / 1e9, max_binder_ns / 1e9, max_decode_ns / 1e9, binder_count, decode_count
    except (ValueError, IndexError):
        return 0.0, 0.0, 0, 0, 0, 0


def extract_sched_features(trace_path, frame):
    ts = frame["intended_vsync"]
    end = frame["actual_end"]

    sql = f"""
    SELECT
        COALESCE(SUM(CASE WHEN ss.dur > 5000000 THEN ss.dur ELSE 0 END), 0) AS long_runnable_ns,
        COALESCE(MAX(ss.dur), 0) AS max_runnable_ns,
        COUNT(CASE WHEN ss.dur > 5000000 THEN 1 END) AS long_runnable_count
    FROM sched_slice ss
    WHERE ss.ts >= {ts} AND ss.ts <= {end}
      AND ss.dur > 0
    """
    result = run_sql(trace_path, sql)
    if not result:
        return 0.0, 0.0, 0
    lines = result.strip().split("\n")
    if len(lines) < 2:
        return 0.0, 0.0, 0
    try:
        vals = lines[1].strip('"').split('","')
        long_runnable_ns = float(vals[0]) if vals[0] else 0.0
        max_runnable_ns = float(vals[1]) if vals[1] else 0.0
        count = int(vals[2]) if vals[2] else 0
        return long_runnable_ns / 1e9, max_runnable_ns / 1e9, count
    except (ValueError, IndexError):
        return 0.0, 0.0, 0


def extract_decode_features(trace_path, frame):
    ts = frame["intended_vsync"]
    end = frame["actual_end"]

    sql = f"""
    SELECT
        COALESCE(SUM(s.dur), 0) AS decode_ns,
        COALESCE(MAX(s.dur), 0) AS max_decode_ns,
        COUNT(*) AS decode_count
    FROM slice s
    WHERE s.ts >= {ts} AND s.ts <= {end}
      AND (s.name GLOB '*decode*' OR s.name GLOB '*codec*' OR s.name GLOB '*MediaCodec*')
    """
    result = run_sql(trace_path, sql)
    if not result:
        return 0.0, 0.0, 0
    lines = result.strip().split("\n")
    if len(lines) < 2:
        return 0.0, 0.0, 0
    try:
        vals = lines[1].strip('"').split('","')
        decode_ns = float(vals[0]) if vals[0] else 0.0
        max_decode_ns = float(vals[1]) if vals[1] else 0.0
        count = int(vals[2]) if vals[2] else 0
        return decode_ns / 1e9, max_decode_ns / 1e9, count
    except (ValueError, IndexError):
        return 0.0, 0.0, 0


def label_frame(features):
    rd = features["runnable_delay"]
    binder = features["binder_total_s"]
    decode = features["decode_total_s"]
    thermal = features["thermal_delta_mc"]

    if thermal > 2000:
        return "THERMAL_THROTTLE"
    if binder > 0.01:
        return "BINDER_BLOCKING"
    if decode > 0.01:
        return "VIDEO_LATE_RENDER"
    if rd > 0.05:
        return "RUNNABLE_DELAY"
    return "RUNNABLE_DELAY"


def main():
    if len(sys.argv) < 5:
        print("用法: python3 trace_features.py <frames.txt> <perfetto_trace> <thermal_profile.txt> <output.csv>")
        sys.exit(1)

    frames_path, trace_path, thermal_path, out_path = sys.argv[1:5]
    if not TP_BIN:
        print("找不到 trace_processor_shell")
        sys.exit(1)

    frames = load_frames(frames_path)
    temps = load_thermal(thermal_path)
    jank_frames = [f for f in frames if f["is_jank"] == 1]
    print(f"总帧: {len(frames)}, Jank 帧: {len(jank_frames)}, Thermal 样本: {len(temps)}")

    all_rows = []
    for i, f in enumerate(jank_frames):
        if (i + 1) % 50 == 0:
            print(f"  处理 {i+1}/{len(jank_frames)}...")

        binder_s, decode_s, max_binder_s, max_decode_s, binder_cnt, decode_cnt = extract_frame_features(trace_path, f)
        long_rd_s, max_rd_s, rd_cnt = extract_sched_features(trace_path, f)
        thermal_delta = get_thermal_delta(temps, f["intended_vsync"], f["actual_end"])

        features = {
            "frame_token": f["frame_token"],
            "delay_ms": f["delay_ms"],
            "runnable_delay": long_rd_s,
            "max_runnable_s": max_rd_s,
            "binder_total_s": binder_s,
            "binder_max_s": max_binder_s,
            "binder_count": binder_cnt,
            "decode_total_s": decode_s,
            "decode_max_s": max_decode_s,
            "decode_count": decode_cnt,
            "thermal_delta_mc": thermal_delta,
        }
        features["label"] = label_frame(features)
        all_rows.append(features)

    fieldnames = [
        "frame_token", "delay_ms", "runnable_delay", "max_runnable_s",
        "binder_total_s", "binder_max_s", "binder_count",
        "decode_total_s", "decode_max_s", "decode_count",
        "thermal_delta_mc", "label",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_rows)

    label_dist = {}
    for r in all_rows:
        label_dist[r["label"]] = label_dist.get(r["label"], 0) + 1
    print(f"\n标签分布: {label_dist}")
    print(f"已保存 {len(all_rows)} 条 → {out_path}")


if __name__ == "__main__":
    main()
