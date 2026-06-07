#!/usr/bin/env python3
"""
TracePilot Task 17 — 筛选可疑帧
找出现有推断为 RUNNABLE_DELAY 但特征值异常的帧，供人工重点标注。

用法：
  python3 scripts/suspect_frames.py output/labels_all.csv

输出：打印可能被误判的帧（binder/futex/thermal 特征偏高）
"""
import csv
import sys

FEATURES = [
    "runnable_delay", "binder_centrality", "futex_wait",
    "thermal_throttle", "decode_late", "system_irq",
]

THRESHOLDS = {
    "binder_centrality": 0.05,
    "futex_wait": 0.05,
    "thermal_throttle": 0.05,
    "decode_late": 0.05,
    "system_irq": 0.10,
}


def main():
    if len(sys.argv) < 2:
        print("用法: python3 suspect_frames.py <labels_all.csv>")
        sys.exit(1)

    suspects = []
    total = 0
    with open(sys.argv[1], encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            label = row.get("label", "")
            if label != "RUNNABLE_DELAY":
                continue
            reasons = []
            for feat, thresh in THRESHOLDS.items():
                val = float(row.get(feat, 0.0))
                if val > thresh:
                    reasons.append(f"{feat}={val:.4f}")
            if reasons:
                suspects.append({
                    "frame_id": row.get("frame_id", ""),
                    "session": row.get("session", "")[:12],
                    "confidence": float(row.get("confidence", 0)),
                    "reasons": reasons,
                    "row": row,
                })

    print(f"共 {total} 条标注，{len(suspects)} 条可疑帧（label=RUNNABLE_DELAY 但其他特征偏高）\n")
    if not suspects:
        print("无可疑帧。可能需要在 Perfetto UI 中手动检查帧。")
        return

    for i, s in enumerate(suspects[:30]):
        print(f"  [{i+1}] frame={s['frame_id']}  session={s['session']}  "
              f"conf={s['confidence']:.2f}  原因: {', '.join(s['reasons'])}")

    print(f"\n建议：在 Perfetto UI 中检查这些帧，确认根因后修改 label 列。")
    print("然后重新运行：python3 scripts/train_jank_model.py output/labels_all.csv")


if __name__ == "__main__":
    main()
