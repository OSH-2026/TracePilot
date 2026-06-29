#!/usr/bin/env python3
"""
jank_classifier.py — 卡顿根因分类器
======================================
读取 delay_analysis_result.json, 对每帧进行多信号分类:
  SCHED_DELAY / BINDER_DEP / FUTEX_LOCK / CPU_THROTTLE / 
  THERMAL_THROTTLE / MEM_PRESSURE / GPU_STALL / RENDER_QUEUE / UNKNOWN

输出: jank_classification.json

用法:
  python jank_classifier.py
"""

import json, os, sys
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DELAY_JSON = os.path.join(BASE_DIR, "..", "output", "analysis", "delay_analysis_result.json")
OUT_FILE  = os.path.join(BASE_DIR, "..", "output", "analysis", "jank_classification.json")

def classify_frame(frame_data):
    """对单帧进行多信号加权分类"""
    threads = frame_data.get("threads", [])
    binder_edges = frame_data.get("binder_edges", [])
    futex_activity = frame_data.get("futex_activity", [])
    cpu_freq = frame_data.get("cpu_freq", {})
    thermal = frame_data.get("thermal", {})
    irq_data = frame_data.get("irq", {})
    mem_data = frame_data.get("mem_reclaim", {})

    scores = defaultdict(float)

    # 1. 调度延迟: 总 runnable_delay 占比
    total_delay = sum(t.get("runnable_delay_ns", 0) for t in threads)
    if total_delay > 0:
        # P95 超过 2ms → 高分
        p95s = [t.get("runnable_delay_p95_ns", 0) for t in threads if t.get("runnable_delay_p95_ns", 0) > 0]
        if p95s:
            avg_p95 = sum(p95s) / len(p95s)
            scores["SCHED_DELAY"] = min(1.0, max(0, avg_p95 / 5_000_000))  # 归一化到 5ms
        else:
            scores["SCHED_DELAY"] = 0.3  # 有延迟但不高
    else:
        scores["SCHED_DELAY"] = 0.0

    # 2. Binder 依赖: 是否有高延迟 Binder 调用
    if binder_edges:
        max_binder_lat = max(b.get("latency_ns", 0) for b in binder_edges)
        n_binder = len(binder_edges)
        if max_binder_lat > 5_000_000:  # >5ms
            scores["BINDER_DEP"] = min(1.0, max_binder_lat / 10_000_000)
        else:
            scores["BINDER_DEP"] = min(0.5, n_binder / 10.0)
    else:
        scores["BINDER_DEP"] = 0.0

    # 3. Futex 锁竞争
    total_waits = sum(f.get("futex_wait_count", 0) for f in futex_activity)
    if total_waits > 50:
        scores["FUTEX_LOCK"] = min(1.0, total_waits / 200.0)
    elif total_waits > 10:
        scores["FUTEX_LOCK"] = 0.3
    else:
        scores["FUTEX_LOCK"] = 0.0

    # 4. CPU 频率: min < 1000MHz → 降频
    if cpu_freq:
        per_cpu = cpu_freq.get("per_cpu", {})
        min_freqs = [v["min"] for v in per_cpu.values()]
        if min_freqs:
            min_f = min(min_freqs)
            if min_f < 800:
                scores["CPU_THROTTLE"] = 0.9
            elif min_f < 1200:
                scores["CPU_THROTTLE"] = 0.5
            elif min_f < 1500:
                scores["CPU_THROTTLE"] = 0.2

    # 5. 温度: >50°C → 温控降频
    if thermal:
        tmax = thermal.get("max_c", 0)
        if tmax > 50:
            scores["THERMAL_THROTTLE"] = min(1.0, (tmax - 40) / 20.0)
        elif tmax > 40:
            scores["THERMAL_THROTTLE"] = 0.2

    # 6. 渲染压力: RenderThread 延迟占比
    render_delay = sum(t.get("runnable_delay_ns", 0) 
                      for t in threads 
                      if t.get("role") == "RenderThread" and t.get("runnable_delay_ns", 0) > 0)
    if total_delay > 0 and render_delay / total_delay > 0.3:
        scores["RENDER_QUEUE"] = min(1.0, (render_delay / total_delay) * 2)

    # 7. GPU 压力
    gpu_delay = sum(t.get("runnable_delay_ns", 0)
                   for t in threads
                   if t.get("role") == "GPU Worker" and t.get("runnable_delay_ns", 0) > 0)
    if total_delay > 0 and gpu_delay / total_delay > 0.15:
        scores["GPU_STALL"] = min(1.0, (gpu_delay / total_delay) * 3)

    # 8. 系统中断 (IRQ + SoftIRQ)
    if irq_data:
        total_irq = irq_data.get("hard_ns", 0) + irq_data.get("soft_ns", 0)
        frame_dur = sum(t.get("runnable_delay_ns", 0) + t.get("actual_run_ns", 0) for t in threads)
        if frame_dur > 0 and total_irq / frame_dur > 0.05:
            scores["SYSTEM_IRQ"] = min(1.0, total_irq / frame_dur * 5)

    # 9. 内存压力
    if mem_data and mem_data.get("count", 0) > 0:
        scores["MEM_PRESSURE"] = min(1.0, mem_data["count"] / 10.0)

    # 选出最高分作为主导因
    if scores:
        dominant = max(scores, key=scores.get)
        confidence = scores[dominant]
    else:
        dominant = "UNKNOWN"
        confidence = 0.0

    return {
        "dominant_cause": dominant,
        "confidence": round(confidence, 4),
        "cause_scores": {k: round(v, 4) for k, v in sorted(scores.items(), key=lambda x: -x[1])},
    }

def main():
    if not os.path.exists(DELAY_JSON):
        print(f"[✗] {DELAY_JSON} not found. Run analyze_delays.py first.")
        sys.exit(1)

    with open(DELAY_JSON) as f:
        data = json.load(f)

    frames = data.get("frames", [])
    if not frames:
        print("[!] No frames to classify.")
        sys.exit(0)

    results = []
    summary = defaultdict(int)

    for f in frames:
        ft = f.get("frame_token", "?")
        classification = classify_frame(f)
        results.append({
            "frame_token": ft,
            **classification,
        })
        summary[classification["dominant_cause"]] += 1

    output = {
        "target_package": data.get("target_package"),
        "total_frames": len(frames),
        "summary": dict(summary),
        "classifications": results,
    }

    with open(OUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"[*] Jank classification exported to: {OUT_FILE}")
    print(f"    {len(frames)} frames classified:")
    for cause, cnt in sorted(summary.items(), key=lambda x: -x[1]):
        pct = cnt / len(frames) * 100
        print(f"      {cause:<20} {cnt:>3} frames ({pct:.0f}%)")


if __name__ == "__main__":
    main()
