#!/usr/bin/env python3
"""
root_cause.py — 多信号根因归因引擎 (v2)
==========================================
对每个 jank 帧, 拆解延迟构成:
  总延迟 = RunnableDelay + BinderIPC + FutexLock + IRQ + SoftIRQ

然后回答: "这帧卡顿的真正元凶是什么?"

输入: delay_analysis_result.json + critical_path_graph.json
输出: root_cause_analysis.json

用法: python root_cause.py
"""

import json, os, sys
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DELAY_JSON = os.path.join(BASE_DIR, "..", "output", "analysis", "delay_analysis_result.json")
GRAPH_JSON = os.path.join(BASE_DIR, "..", "output", "analysis", "critical_path_graph.json")
OUT_FILE   = os.path.join(BASE_DIR, "..", "output", "analysis", "root_cause_analysis.json")


def fmt_ns(ns):
    if ns < 1000: return f"{ns}ns"
    elif ns < 1_000_000: return f"{ns/1000:.1f}us"
    return f"{ns/1_000_000:.2f}ms"


def analyze_frame(frame_data):
    threads = frame_data.get("threads", [])
    binder_edges = frame_data.get("binder_edges", [])
    futex_activity = frame_data.get("futex_activity", [])
    irq_data = frame_data.get("irq", {})
    mem_data = frame_data.get("mem_reclaim", {})
    cpu_data = frame_data.get("cpu_freq", {})
    thermal_data = frame_data.get("thermal", {})

    br = {
        "runnable_ns": sum(t.get("runnable_delay_ns", 0) for t in threads),
        "binder_ns": sum(b.get("latency_ns", 0) for b in binder_edges),
        "futex_count": sum(f.get("futex_wait_count", 0) for f in futex_activity),
        "irq_hard_ns": irq_data.get("hard_ns", 0),
        "irq_soft_ns": irq_data.get("soft_ns", 0),
        "mem_reclaim": mem_data.get("count", 0),
        "cpu_mhz": cpu_data.get("avg_mhz", 0) if cpu_data else 0,
        "thermal_c": thermal_data.get("max_c", 0) if thermal_data else 0,
    }
    br["futex_est_ns"] = br["futex_count"] * 50_000
    br["total_ns"] = (br["runnable_ns"] + br["binder_ns"] + br["futex_est_ns"] +
                       br["irq_hard_ns"] + br["irq_soft_ns"])

    total = max(1, br["total_ns"])
    candidates = []
    for label, key in [
        ("CPU Scheduling Contention", "runnable_ns"),
        ("Binder IPC Blocking", "binder_ns"),
        ("Futex Lock Contention", "futex_est_ns"),
        ("Hard IRQ Overhead", "irq_hard_ns"),
        ("SoftIRQ Overhead", "irq_soft_ns"),
    ]:
        r = br[key] / total
        if r > 0.20:
            candidates.append((label, r))
    if br["thermal_c"] > 45:
        candidates.append(("Thermal Throttling", br["thermal_c"] / 80))
    elif br["cpu_mhz"] > 0 and br["cpu_mhz"] < 1200:
        candidates.append(("CPU Freq Throttling", 1 - br["cpu_mhz"] / 2400))
    if br["mem_reclaim"] >= 3:
        candidates.append(("Memory Pressure", min(1, br["mem_reclaim"] / 20)))

    candidates.sort(key=lambda x: -x[1])
    dominant = candidates[0] if candidates else ("Unknown", 0)

    thread_attr = []
    for t in threads:
        d = t.get("runnable_delay_ns", 0)
        if d > 1_000_000:
            thread_attr.append({
                "tid": t["tid"], "comm": t.get("comm","?"), "role": t.get("role",""),
                "runnable_ns": d,
                "binder_ns": sum(b.get("latency_ns",0) for b in binder_edges if b.get("tx_tid")==t["tid"]),
                "futex_count": sum(f.get("futex_wait_count",0) for f in futex_activity if f["tid"]==t["tid"]),
            })

    return {
        "breakdown": br,
        "dominant_cause": dominant[0],
        "dominant_score": round(dominant[1], 4),
        "candidates": [{"cause": c[0], "score": round(c[1],4)} for c in candidates],
        "top_threads": sorted(thread_attr, key=lambda x: -x["runnable_ns"])[:5],
    }


def main():
    for p in [DELAY_JSON, GRAPH_JSON]:
        if not os.path.exists(p):
            print(f"[!] {p} not found."); sys.exit(1)

    with open(DELAY_JSON) as f: delay_data = json.load(f)
    with open(GRAPH_JSON) as f: graph_data = json.load(f)

    frames = delay_data.get("frames", [])
    scores = graph_data.get("global_critical_scores", [])
    if not frames:
        print("[!] No frames to analyze."); sys.exit(0)

    print(f"{'='*70}")
    print(f"  Multi-Signal Root-Cause Analysis ({len(frames)} frames)")
    print(f"{'='*70}")

    frame_results = []
    cause_summary = defaultdict(int)

    for fr in frames:
        ft = fr.get("frame_token", "?")
        a = analyze_frame(fr)
        b = a["breakdown"]
        cause_summary[a["dominant_cause"]] += 1

        print(f"\n  Frame {ft}: total={fmt_ns(b['total_ns'])}")
        print(f"    Sched={fmt_ns(b['runnable_ns'])} Binder={fmt_ns(b['binder_ns'])} "
              f"Futex={fmt_ns(b['futex_est_ns'])} IRQ={fmt_ns(b['irq_hard_ns'])} "
              f"SoftIRQ={fmt_ns(b['irq_soft_ns'])}")
        env = []
        if b['cpu_mhz'] > 0: env.append(f"CPU={b['cpu_mhz']}MHz")
        if b['thermal_c'] > 0: env.append(f"T={b['thermal_c']}C")
        if b['mem_reclaim'] > 0: env.append(f"MemReclaim={b['mem_reclaim']}x")
        if env: print(f"    Env: {', '.join(env)}")
        print(f"    >>> ROOT CAUSE: {a['dominant_cause']} ({a['dominant_score']*100:.0f}%)")
        for t in a["top_threads"][:3]:
            extra = ""
            if t["binder_ns"] > 0: extra += f" +Binder={fmt_ns(t['binder_ns'])}"
            if t["futex_count"] > 0: extra += f" +Futex={t['futex_count']}x"
            print(f"      {t['comm']}(TID:{t['tid']}): {fmt_ns(t['runnable_ns'])}{extra}")

        frame_results.append({"frame_token": ft, **a})

    print(f"\n{'='*70}")
    print(f"  Cross-Frame Root-Cause Distribution")
    print(f"{'='*70}")
    for cause, cnt in sorted(cause_summary.items(), key=lambda x: -x[1]):
        print(f"    {cause:<35} {cnt:>2}/{len(frames)} ({cnt/len(frames)*100:.0f}%)")

    thread_agg = defaultdict(lambda: {"runnable": 0, "binder": 0, "futex": 0, "frames": 0})
    for fr in frame_results:
        for t in fr["top_threads"]:
            tid = t["tid"]
            thread_agg[tid]["runnable"] += t["runnable_ns"]
            thread_agg[tid]["binder"] += t["binder_ns"]
            thread_agg[tid]["futex"] += t["futex_count"]
            thread_agg[tid]["frames"] += 1

    tid_to_score = {s["tid"]: s for s in scores}
    global_attr = []
    for tid, agg in sorted(thread_agg.items(), key=lambda x: -x[1]["runnable"])[:15]:
        r, b, f = agg["runnable"], agg["binder"], agg["futex"]
        if b > r * 0.4: root = "Binder IPC Blocking"
        elif f > 10: root = "Futex Lock Contention"
        elif r > 5_000_000: root = "CPU Scheduling Contention (delay>5ms)"
        else: root = "CPU Scheduling Contention"
        info = tid_to_score.get(tid, {})
        global_attr.append({
            "tid": tid, "comm": info.get("comm","?"), "role": info.get("role",""),
            "runnable_ms": round(r/1e6,3), "binder_ms": round(b/1e6,3),
            "futex_count": f, "frames": agg["frames"], "root_cause": root,
        })

    output = {
        "target": delay_data.get("target_package", "unknown"),
        "total_frames": len(frames),
        "frame_analyses": frame_results,
        "cause_distribution": dict(cause_summary),
        "global_thread_attributions": global_attr,
    }
    with open(OUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n[*] Root-cause analysis exported to: {OUT_FILE}")


if __name__ == "__main__":
    main()
