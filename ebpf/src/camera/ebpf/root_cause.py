#!/usr/bin/env python3
"""
root_cause.py — 延迟归因: 把 Top-K 线程的总延迟拆成 "调度竞争/Binder等待/Futex锁"

输入:
  output/analysis/delay_analysis_result.json  (per-frame 原始数据)
  output/analysis/critical_path_graph.json    (Top-K 排名)

输出:
  output/analysis/root_cause_analysis.json

逻辑:
  对每个 Top-K 线程, 跨帧计算其延迟构成:
  
  总阻塞时间 = 调度竞争(Runnable Delay) + Binder IPC 等待 + Futex 锁等待
  
  其中调度竞争直接从 sched 数据取, Binder/Futex 从 binder_edges 和 
  futex_activity 匹配该线程作为 tx_tid / futex_wait 贡献者的帧.
"""

import json
import os
import sys
from collections import defaultdict


def fmt_ns(ns):
    """纳秒 → 可读字符串"""
    if ns < 1000:
        return f"{ns}ns"
    if ns < 1_000_000:
        return f"{ns/1000:.1f}µs"
    return f"{ns/1_000_000:.2f}ms"


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    delay_path = os.path.join(base_dir, "..", "output", "analysis",
                              "delay_analysis_result.json")
    graph_path = os.path.join(base_dir, "..", "output", "analysis",
                              "critical_path_graph.json")
    output_path = os.path.join(base_dir, "..", "output", "analysis",
                               "root_cause_analysis.json")

    for p in [delay_path, graph_path]:
        if not os.path.exists(p):
            print(f"[!] {p} not found.")
            sys.exit(1)

    with open(delay_path) as f:
        delay_data = json.load(f)
    with open(graph_path) as f:
        graph_data = json.load(f)

    scores = graph_data.get("global_critical_scores", [])
    frames = delay_data.get("frames", [])

    if not scores or not frames:
        print("[!] No scores or frames found.")
        sys.exit(0)

    # ── 建立: frame_token → frame_data ──
    frame_map = {}
    for fr in frames:
        ft = fr.get("frame_token")
        if ft is not None:
            frame_map[ft] = fr

    # ── 取 Top-K (前 10) ──
    top_k = sorted(scores, key=lambda s: -s["score"])[:10]

    print(f"{'='*70}")
    print(f"  Root-Cause Analysis — Top {len(top_k)} Threads")
    print(f"{'='*70}")

    results = []

    for tk in top_k:
        tid = tk["tid"]
        comm = tk.get("comm", "?")
        role = tk.get("role", "")
        score = tk["score"]

        total_delay = 0
        total_binder = 0
        total_futex = 0
        total_binder_count = 0
        total_futex_count = 0
        frames_with_data = 0
        binder_details = []
        futex_details = []
        cpu_freqs_all = []     # 跨帧收集所有 CPU 频率样本(MHz)
        thermal_all = []       # 跨帧收集所有温度样本(°C)

        for fr in frames:
            ft = fr.get("frame_token", "?")

            # ── Runnable Delay ──
            for t in fr.get("threads", []):
                if t["tid"] == tid:
                    d = t.get("runnable_delay_ns", 0)
                    total_delay += d
                    if d > 0:
                        frames_with_data += 1

            # ── CPU 频率 ──
            cf = fr.get("cpu_freq", {})
            if cf:
                cpu_freqs_all.append(cf.get("avg_mhz", 0))

            # ── Thermal ──
            th = fr.get("thermal", {})
            if th:
                thermal_all.append(th.get("max_c", 0))

        for fr in frames:
            ft = fr.get("frame_token", "?")

            # ── Runnable Delay ──
            for t in fr.get("threads", []):
                if t["tid"] == tid:
                    d = t.get("runnable_delay_ns", 0)
                    total_delay += d
                    if d > 0:
                        frames_with_data += 1

            # ── Binder: 该线程作为 tx_tid (调用方) 的 IPC 耗时 ──
            for b in fr.get("binder_edges", []):
                if b.get("tx_tid") == tid:
                    lat = b.get("latency_ns", 0)
                    total_binder += lat
                    total_binder_count += 1
                    binder_details.append({
                        "frame": ft,
                        "rx_comm": b.get("rx_comm", "?"),
                        "rx_tid": b.get("rx_tid", 0),
                        "latency_ns": lat,
                    })

            # ── Futex: 该线程的 FUTEX_WAIT 次数 ──
            for f in fr.get("futex_activity", []):
                if f["tid"] == tid:
                    wc = f.get("futex_wait_count", 0)
                    total_futex += wc
                    total_futex_count += wc
                    if wc > 0:
                        futex_details.append({
                            "frame": ft,
                            "wait_count": wc,
                        })

        if total_delay == 0 and total_binder == 0:
            continue

        # ── 归因结论 ──
        avg_freq = sum(cpu_freqs_all) // max(len(cpu_freqs_all), 1) if cpu_freqs_all else 0
        max_thermal = max(thermal_all) if thermal_all else 0

        if total_binder > 5_000_000 and total_binder > total_delay:
            root_cause = "Binder IPC (Binder耗时 > 调度延迟)"
        elif total_delay > 10_000_000:
            freq_note = ""
            if avg_freq > 0 and avg_freq < 1000:
                freq_note = f", CPU平均仅{avg_freq}MHz"
            elif max_thermal > 45:
                freq_note = f", 温度{max_thermal}°C(疑似热降频)"
            root_cause = "CPU Scheduling Contention (调度延迟>10ms%s)" % freq_note
        elif total_futex_count >= 5:
            root_cause = "Futex Lock Contention (FUTEX_WAIT>=5次)"
        else:
            root_cause = "CPU Scheduling Contention"

        result = {
            "tid": tid,
            "comm": comm,
            "role": role,
            "score": score,
            "attribution": {
                "runnable_delay_ms": round(total_delay / 1_000_000, 3),
                "binder_latency_ms": round(total_binder / 1_000_000, 3),
                "binder_call_count": total_binder_count,
                "futex_wait_count": total_futex_count,
                "cpu_freq_avg_mhz": avg_freq,
                "cpu_freq_min_mhz": min(cpu_freqs_all) if cpu_freqs_all else 0,
                "thermal_max_c": max_thermal,
            },
            "root_cause": root_cause,
            "frames_with_data": frames_with_data,
            "top_binder_calls": sorted(binder_details,
                                       key=lambda x: -x["latency_ns"])[:5],
            "top_futex_frames": futex_details[:5],
        }
        results.append(result)

        # ── 终端输出 ──
        print(f"\n  TID:{tid} {comm} ({role}) — Score={score:.3f}")
        print(f"  {'─'*60}")
        print(f"    ├─ 调度竞争 (Runnable Delay): {fmt_ns(total_delay)}")
        print(f"    ├─ Binder IPC 耗时:          {fmt_ns(total_binder)} ({total_binder_count} 次调用)")
        print(f"    └─ Futex WAIT:               {total_futex_count} 次")
        if cpu_freqs_all:
            print(f"    CPU 频率 (帧内平均):          {avg_freq}MHz  "
                  f"(min={min(cpu_freqs_all)}MHz max={max(cpu_freqs_all)}MHz)")
        if max_thermal > 0:
            print(f"    Thermal (帧内最高):           {max_thermal}°C")
        if binder_details:
            top_b = sorted(binder_details, key=lambda x: -x["latency_ns"])[:3]
            print(f"    Top Binder 调用:")
            for b in top_b:
                print(f"      → {b['rx_comm']}(TID:{b['rx_tid']}) "
                      f"耗时 {fmt_ns(b['latency_ns'])} [Frame {b['frame']}]")

        if futex_details:
            frames_with_futex = [f for f in futex_details if f["wait_count"] > 0]
            if frames_with_futex:
                print(f"    Futex 等待出现的帧: "
                      f"{', '.join(str(f['frame']) for f in frames_with_futex[:5])}")

        print(f"    ★ 根因分类: {root_cause}")

    # ── 汇总表 ──
    print(f"\n{'='*70}")
    print(f"  归因汇总")
    print(f"{'='*70}")
    print(f"  {'TID':<8} {'Comm':<20} {'Score':>6} {'调度延迟':>10} {'Binder耗时':>10} {'Futex':>6}  根因")
    print(f"  {'-'*78}")
    for r in results:
        a = r["attribution"]
        print(f"  TID:{r['tid']:<4} {r['comm']:<20} {r['score']:>5.2f} "
              f"{fmt_ns(a['runnable_delay_ms']*1_000_000):>10}  "
              f"{fmt_ns(a['binder_latency_ms']*1_000_000):>10}  "
              f"{a['futex_wait_count']:>4d}次  "
              f"{r['root_cause']}")

    # ── 导出 JSON ──
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump({
            "generated_at": __import__('time').strftime("%Y-%m-%dT%H:%M:%S"),
            "top_k_count": len(results),
            "attributions": results,
        }, f, indent=2, ensure_ascii=False)

    print(f"\n[✓] Root-cause analysis → {output_path}")


if __name__ == "__main__":
    main()
