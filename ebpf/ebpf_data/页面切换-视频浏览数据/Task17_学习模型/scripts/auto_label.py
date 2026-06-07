#!/usr/bin/env python3
"""
TracePilot Task 17 — 自动标注（用现有推断结果作为初始标签）
导出 CSV 后人工纠错，再喂给 train_jank_model.py 训练。

用法：
  python3 scripts/auto_label.py output/page_switch/result.json output/video/result.json
  python3 scripts/auto_label.py output/*/result.json

输出：同目录下 label_auto.csv + 汇总 output/labels_all.csv
"""
import json
import csv
import sys
import os

FEATURES = [
    "runnable_delay",
    "binder_centrality",
    "futex_wait",
    "thermal_throttle",
    "decode_late",
    "system_irq",
]


def extract_frames(result_path):
    with open(result_path, encoding="utf-8") as f:
        data = json.load(f)
    inferences = data.get("inference", {}).get("frame_inferences", [])
    session = data.get("session_id", "unknown")
    scenario = data.get("detected_scenario", data.get("scenario", "unknown"))
    frames = []
    for inf in inferences:
        if not inf.get("hypothesis"):
            continue
        evidence = {e["signal"]: e["weight"] for e in inf.get("evidence", [])}
        features = {feat: evidence.get(feat, 0.0) for feat in FEATURES}
        label = inf.get("hypothesis", "UNKNOWN")
        # 启发式修正：当其他信号明显高于 runnable_delay 时，修正标签
        rd = features.get("runnable_delay", 0.0)
        if features.get("thermal_throttle", 0.0) > 0.3:
            label = "THERMAL_THROTTLE"
        elif features.get("binder_centrality", 0.0) > 0.3:
            label = "BINDER_BLOCKING"
        elif features.get("futex_wait", 0.0) > 0.3:
            label = "FUTEX_BLOCKING"
        elif features.get("decode_late", 0.0) > 0.3:
            label = "VIDEO_LATE_RENDER"
        elif features.get("system_irq", 0.0) > 0.3:
            label = "IO_WAIT"
        frames.append({
            "frame_id": inf["frame_id"],
            "session": session,
            "scenario": scenario,
            "hypothesis": inf.get("hypothesis", "UNKNOWN"),
            "confidence": inf.get("confidence", 0.0),
            **features,
            "label": label,
        })
    return session, frames


def write_csv(frames, out_path):
    fieldnames = [
        "frame_id", "session", "scenario", "hypothesis", "confidence",
        "runnable_delay", "binder_centrality", "futex_wait",
        "thermal_throttle", "decode_late", "system_irq", "label",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(frames)
    print(f"  {len(frames)} 条 → {out_path}")


def main():
    if len(sys.argv) < 2:
        print("用法: python3 auto_label.py <result.json> [result2.json ...]")
        sys.exit(1)

    all_frames = []
    for path in sys.argv[1:]:
        if not os.path.exists(path):
            print(f"文件不存在: {path}")
            continue
        session, frames = extract_frames(path)
        if not frames:
            print(f"无 jank 帧: {path}")
            continue
        print(f"Session {session}: {len(frames)} 帧")
        out_path = os.path.join(os.path.dirname(path), f"label_auto_{session}.csv")
        write_csv(frames, out_path)
        all_frames.extend(frames)

    if all_frames:
        out_dir = os.path.dirname(sys.argv[1])
        total_path = os.path.join(out_dir, "labels_all.csv")
        write_csv(all_frames, total_path)
        print(f"\n共 {len(all_frames)} 条自动标注")
        print("下一步：人工纠错 label_auto_*.csv 中的 label 列")
        print("然后运行：python3 scripts/train_jank_model.py output/labels_all.csv")


if __name__ == "__main__":
    main()
