#!/usr/bin/env python3
"""
session_compare.py — 多会话对比分析
======================================
扫描 output/reports/ 目录, 读取所有历史运行的 JSON 产物,
生成跨会话对比报告.

对比维度: jank 帧数 / 线程数 / Top-3 线程 / 卡顿分类分布 / 温度 / 事件量

输出: output/analysis/compare_report.json

用法:
  python session_compare.py
"""

import json, os, sys, glob
from collections import defaultdict
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_DIR = os.path.join(BASE_DIR, "..", "output", "analysis")
REPORT_DIR = os.path.join(BASE_DIR, "..", "output", "reports")
OUT_FILE = os.path.join(ANALYSIS_DIR, "compare_report.json")


def extract_summary(label, path):
    """从单个运行目录提取摘要统计"""
    delay_json = os.path.join(ANALYSIS_DIR, "delay_analysis_result.json")
    graph_json = os.path.join(ANALYSIS_DIR, "critical_path_graph.json")
    jank_json  = os.path.join(ANALYSIS_DIR, "jank_classification.json")

    summary = {"label": label, "available": False}

    # 调度数据
    if os.path.exists(delay_json):
        with open(delay_json) as f:
            d = json.load(f)
        summary["available"] = True
        summary["target"] = d.get("target_package", "unknown")
        summary["n_frames"] = len(d.get("frames", []))

        # 温度 + CPU 频率
        frames = d.get("frames", [])
        temps = []
        min_freqs = []
        for fr in frames:
            t = fr.get("thermal", {})
            if t.get("max_c"):
                temps.append(t["max_c"])
            cf = fr.get("cpu_freq", {})
            per = cf.get("per_cpu", {})
            for v in per.values():
                if v.get("min"):
                    min_freqs.append(v["min"])
        summary["thermal_max_c"] = max(temps) if temps else 0
        summary["cpu_min_mhz"] = min(min_freqs) if min_freqs else 0
    else:
        summary["n_frames"] = 0

    # 图评分
    if os.path.exists(graph_json):
        with open(graph_json) as f:
            g = json.load(f)
        scores = g.get("global_critical_scores", [])
        summary["n_threads"] = len(scores)
        summary["top3"] = [
            {"tid": s["tid"], "comm": s["comm"], "role": s["role"], "score": s["score"]}
            for s in scores[:3]
        ]
    else:
        summary["n_threads"] = 0
        summary["top3"] = []

    # 卡顿分类
    if os.path.exists(jank_json):
        with open(jank_json) as f:
            j = json.load(f)
        summary["jank_causes"] = j.get("summary", {})
    else:
        summary["jank_causes"] = {}

    return summary


def main():
    # 查找所有分析目录或报告文件来识别会话
    report_files = sorted(glob.glob(os.path.join(REPORT_DIR, "report_*.md")), reverse=True)
    if not report_files:
        print("[✗] No report files found. Run full pipeline first.")
        sys.exit(0)

    summaries = []
    for rf in report_files[:10]:  # 最近 10 次
        ts = rf.replace("report_", "").replace(".md", "")
        try:
            dt = datetime.strptime(ts.split("_")[0] if "_" in ts else ts, "%Y-%m-%d-%H%M")
            label = dt.strftime("%m/%d %H:%M")
        except:
            label = ts[-12:]
        summary = extract_summary(label, rf)
        if summary["available"]:
            summaries.append(summary)

    if len(summaries) < 2:
        print("[!] Need at least 2 sessions to compare. Run the pipeline more than once.")
        sys.exit(0)

    # 生成对比报告
    output = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sessions": summaries,
        "cross_analysis": {},
    }

    # 跨会话: Top-1 线程重叠
    all_top1 = [s["top3"][0]["comm"] if s["top3"] else "?" for s in summaries]
    unique_top1 = set(all_top1)
    output["cross_analysis"]["top1_unique_count"] = len(unique_top1)
    output["cross_analysis"]["top1_recurring"] = [
        t for t in unique_top1 if all_top1.count(t) >= 2
    ]

    # 跨会话: 卡顿分类演变
    cause_trend = defaultdict(list)
    for s in summaries:
        for cause, cnt in s.get("jank_causes", {}).items():
            cause_trend[cause].append(cnt)
    output["cross_analysis"]["cause_trends"] = {
        k: v for k, v in cause_trend.items()
    }

    with open(OUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"[*] Compare report exported to: {OUT_FILE}")
    print(f"    {len(summaries)} sessions compared:")
    print(f"    {'Session':<16} {'Target':<30} {'Frames':>6} {'Threads':>8} {'Temp':>6} {'Top-1':>20}")
    print(f"    {'-'*90}")
    for s in summaries:
        top1 = s["top3"][0]["comm"][:20] if s["top3"] else "?"
        print(f"    {s['label']:<16} {(s.get('target') or '?')[:30]:<30} {s['n_frames']:>6} "
              f"{s['n_threads']:>8} {s.get('thermal_max_c',0):>4}°C {'':>2}{top1}")

    # 跨会话发现
    if output["cross_analysis"]["top1_recurring"]:
        print(f"\n  ⚠ Top-1 recurring threads: {', '.join(output['cross_analysis']['top1_recurring'])}")
    if len(cause_trend) > 1:
        print(f"  📊 Dominant causes: {', '.join(cause_trend.keys())}")


if __name__ == "__main__":
    main()
