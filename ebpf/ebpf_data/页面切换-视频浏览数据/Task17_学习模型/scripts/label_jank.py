#!/usr/bin/env python3
"""
TracePilot Task 17 — 人工标注工具
从 result.json 导出 jank 帧的特征，人工逐帧标注根因。

用法：
  python3 scripts/label_jank.py output/page_switch/result.json
  python3 scripts/label_jank.py output/page_switch/result.json output/video/result.json

输出：同目录下 label_<session>.csv
"""
import json
import csv
import sys
import os

CAUSES = [
    "RUNNABLE_DELAY",
    "BINDER_BLOCKING",
    "FUTEX_BLOCKING",
    "THERMAL_THROTTLE",
    "VIDEO_LATE_RENDER",
    "IO_WAIT",
    "MEMORY_RECLAIM",
    "GPU_STALL",
    "AUDIO_SYNC_DRIFT",
    "CPU_CONTENTION",
]

FEATURES = [
    "cpu_contention",
    "binder_blocking",
    "futex_blocking",
    "io_wait",
    "memory_reclaim",
    "gpu_stall",
    "runnable_delay",
    "video_late_render",
    "audio_sync_drift",
    "thermal_throttle",
]

def load_frames(result_path):
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
        frames.append({
            "frame_id": inf["frame_id"],
            "session": session,
            "scenario": scenario,
            "hypothesis": inf.get("hypothesis", "UNKNOWN"),
            "confidence": inf.get("confidence", 0.0),
            "runnable_delay": evidence.get("runnable_delay", 0.0),
            "binder_centrality": evidence.get("binder_centrality", 0.0),
            "futex_wait": evidence.get("futex_wait", 0.0),
            "thermal_throttle": evidence.get("thermal_throttle", 0.0),
            "decode_late": evidence.get("decode_late", 0.0),
            "system_irq": evidence.get("system_irq", 0.0),
        })
    return session, frames


def interactive_label(session, frames):
    labels = []
    total = len(frames)
    print(f"\n=== 标注 Session: {session} ===")
    print(f"共 {total} 个 jank 帧待标注\n")
    print("可用标签:")
    for i, c in enumerate(CAUSES):
        print(f"  {i}: {c}")
    print(f"  s: 跳过")
    print(f"  q: 退出并保存\n")

    for idx, f in enumerate(frames):
        print(f"[{idx+1}/{total}] frame={f['frame_id']}  "
              f"auto={f['hypothesis']}({f['confidence']:.2f})  "
              f"rd={f['runnable_delay']:.4f} binder={f['binder_centrality']:.4f} "
              f"futex={f['futex_wait']:.4f} thermal={f['thermal_throttle']:.4f} "
              f"decode={f['decode_late']:.4f} irq={f['system_irq']:.4f}")
        while True:
            ans = input("  标注 > ").strip().lower()
            if ans == "q":
                print(f"已保存 {len(labels)} 条标注")
                return labels
            if ans == "s":
                break
            try:
                ci = int(ans)
                if 0 <= ci < len(CAUSES):
                    labels.append({**f, "label": CAUSES[ci]})
                    break
                print("  输入 0-9 或 s/q")
            except ValueError:
                print("  输入数字 0-9，s=跳过，q=退出")
    print(f"标注完成，共 {len(labels)} 条")
    return labels


def write_csv(labels, out_path):
    if not labels:
        print(f"无标注数据，跳过 {out_path}")
        return
    fieldnames = [
        "frame_id", "session", "scenario", "hypothesis", "confidence",
        "runnable_delay", "binder_centrality", "futex_wait",
        "thermal_throttle", "decode_late", "system_irq", "label",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(labels)
    print(f"已保存 {len(labels)} 条标注 → {out_path}")


def main():
    if len(sys.argv) < 2:
        print("用法: python3 label_jank.py <result.json> [result2.json ...]")
        sys.exit(1)

    all_labels = []
    for path in sys.argv[1:]:
        if not os.path.exists(path):
            print(f"文件不存在: {path}")
            continue
        session, frames = load_frames(path)
        if not frames:
            print(f"无 jank 帧: {path}")
            continue
        labels = interactive_label(session, frames)
        all_labels.extend(labels)

        out_path = os.path.join(os.path.dirname(path), f"label_{session}.csv")
        write_csv(labels, out_path)

    if all_labels:
        total_path = os.path.join(os.path.dirname(sys.argv[1]), "labels_all.csv")
        write_csv(all_labels, total_path)


if __name__ == "__main__":
    main()
