#!/usr/bin/env python3
"""
generate_report.py — 生成分析报告文档
========================================
读取 delay_analysis_result.json + critical_path_graph.json
输出: report_YYYY-MM-DD_HHMM.md
"""

import json, os, sys
from datetime import datetime
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DELAY_JSON = os.path.join(BASE_DIR, "..", "output", "analysis", "delay_analysis_result.json")
GRAPH_JSON = os.path.join(BASE_DIR, "..", "output", "analysis", "critical_path_graph.json")
ROOTCAUSE_JSON = os.path.join(BASE_DIR, "..", "output", "analysis", "root_cause_analysis.json")
OUT_DIR   = os.path.join(BASE_DIR, "..", "output", "reports")


def load_json(path):
    if not os.path.exists(path):
        print(f"[✗] {path} not found")
        return None
    with open(path) as f:
        return json.load(f)


def role_emoji(role):
    m = {
        "UI Thread": "🖥️", "RenderThread": "🎨", "SurfaceFlinger": "🖼️",
        "Binder RPC": "🔗", "HwBinder RPC": "🔗", "HwComposer": "🖌️",
        "GPU Worker": "🎮", "I/O Worker": "💾", "SystemService": "⚙️",
    }
    return m.get(role, "⚡")


def fmt_ns(ns):
    ms = ns / 1_000_000
    if ms >= 1000:
        return f"{ms/1000:.2f}s"
    return f"{ms:.2f}ms"


def fmt_score(s):
    return f"{s:.4f}"


def generate():
    delay = load_json(DELAY_JSON)
    graph = load_json(GRAPH_JSON)
    root_cause = load_json(ROOTCAUSE_JSON)
    if not delay or not graph:
        sys.exit(1)

    now = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_path = os.path.join(OUT_DIR, f"report_{now}.md")
    os.makedirs(OUT_DIR, exist_ok=True)

    meta = graph.get("meta", {})
    scores = graph.get("global_critical_scores", [])
    frames = delay.get("frames", [])
    weights = meta.get("scoring_weights", {})

    lines = []
    def w(s=""): lines.append(s)
    def h(n, t): w(f"{'#' * n} {t}")
    def table(headers, rows):
        w("| " + " | ".join(headers) + " |")
        w("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows:
            w("| " + " | ".join(str(c) for c in row) + " |")
        w()

    # ═══════ 标题 ═══════
    h(1, "TracePilot — Android 交互级调度延迟分析报告")
    w()
    w(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w(f"**目标应用**: `{meta.get('target_package', '?')}`  (PID={meta.get('pid','?')}, UID={meta.get('uid','?')})")
    w(f"**卡顿帧数**: {meta.get('total_jank_frames', 0)} 个 App Deadline Missed")
    w(f"**评分的线程数**: {len(scores)}")
    w()

    # ═══════ 一、Top-K 关键线程 ═══════
    h(2, "一、Top-K 关键线程 (CriticalScore)")
    w()
    w(f"评分公式权重: `a={weights.get('a')}  b={weights.get('b')}  c={weights.get('c')}  "
      f"d={weights.get('d')}  e={weights.get('e')}  f={weights.get('f')}  "
      f"h={weights.get('h')}  g={weights.get('g')}`")
    w()

    top_k = scores[:15]
    table_h = ["排名", "TID", "角色", "线程名", "得分", "帧覆盖率", "关键路径", "P95延迟"]
    table_r = []
    for i, s in enumerate(top_k, 1):
        c = s["components"]
        on_cp = c.get("on_critical_path_ratio", 0)
        table_r.append([
            f"#{i}",
            s["tid"],
            f"{role_emoji(s['role'])}{s['role']}",
            s["comm"],
            fmt_score(s["score"]),
            f"{c.get('frame_overlap', 0)*100:.0f}%",
            f"{on_cp*100:.0f}%" if on_cp > 0 else "—",
            fmt_ns(c.get("runnable_delay_p95_ns", 0)),
        ])
    table(table_h, table_r)

    # 评分明细
    h(3, "评分明细")
    table_h2 = ["TID", "线程名", "overlap", "p95_norm", "binder", "futex", "rpp", "repeated", "onCP"]
    table_r2 = []
    for s in top_k:
        c = s["components"]
        table_r2.append([
            s["tid"], s["comm"],
            round(c.get("frame_overlap",0), 3),
            round(c.get("runnable_delay_p95_norm",0), 3),
            round(c.get("binder_centrality_norm",0), 3),
            round(c.get("futex_wait_norm",0), 3),
            round(c.get("render_path_proximity",0), 3),
            round(c.get("repeated_jank_ratio",0), 3),
            round(c.get("on_critical_path_ratio",0), 3),
        ])
    table(table_h2, table_r2)
    w()

    # ═══════ 二、Binder 依赖分析 ═══════
    h(2, "二、Binder 依赖分析")
    w()
    all_binder = []
    for f in frames:
        for b in f.get("binder_edges", []):
            all_binder.append(b)

    if all_binder:
        # 按延迟排序
        all_binder.sort(key=lambda x: -x.get("latency_ns", 0))
        w(f"共捕获 **{len(all_binder)}** 对 Binder 调用。")
        w()

        h(3, "Top-10 最长 Binder 延迟")
        table_h3 = ["延迟", "调用方", "→", "服务方", "debug_id", "类型"]
        table_r3 = []
        for b in all_binder[:10]:
            lat = fmt_ns(b.get("latency_ns", 0))
            rep = " [REPLY]" if b.get("is_reply") else ""
            table_r3.append([
                lat,
                f"`{b.get('tx_comm','?')}`(TID:{b['tx_tid']})",
                "→",
                f"`{b.get('rx_comm','?')}`(TID:{b['rx_tid']})",
                b.get("debug_id", ""),
                f"0x{b.get('code',0):x}{rep}",
            ])
        table(table_h3, table_r3)

        # 关键路径上的 Binder 调用
        cp_edges = []
        for f_data in graph.get("frames", []):
            for cp in f_data.get("critical_paths", []):
                for e in cp.get("path_edges", []):
                    if e["type"] == "BINDER_CALL":
                        src_tid = e["src"].replace("thread_", "")
                        dst_tid = e["dst"].replace("thread_", "")
                        cp_edges.append(f"`TID:{src_tid}` → `TID:{dst_tid}`")
        if cp_edges:
            w()
            h(3, "关键路径上的 Binder 边")
            for e in cp_edges:
                w(f"- {e}")
    else:
        w("*未捕获到 Binder 事件*")
    w()

    # ═══════ 三、Futex 活动 ═══════
    h(2, "三、Futex 锁等待活动")
    w()
    futex_stats = defaultdict(lambda: {"wait": 0, "wake": 0, "role": "", "comm": ""})
    for f in frames:
        for fa in f.get("futex_activity", []):
            t = fa["tid"]
            futex_stats[t]["wait"] += fa.get("futex_wait_count", 0)
            futex_stats[t]["wake"] += fa.get("futex_wake_count", 0)
            futex_stats[t]["role"] = fa.get("role", "")
            futex_stats[t]["comm"] = fa.get("comm", "")

    if futex_stats:
        sorted_futex = sorted(futex_stats.items(), key=lambda x: -x[1]["wait"])
        w(f"共 {len(futex_stats)} 个线程有 Futex 活动。")
        w()
        table_h4 = ["TID", "角色", "线程名", "FUTEX_WAIT", "FUTEX_WAKE"]
        table_r4 = []
        for tid, st in sorted_futex[:15]:
            if st["wait"] > 0 or st["wake"] > 0:
                table_r4.append([
                    tid, f"{role_emoji(st['role'])}{st['role']}",
                    st["comm"], st["wait"], st["wake"],
                ])
        table(table_h4, table_r4)
    w()

    # ═══════ 四、每帧详情 ═══════
    h(2, "四、逐帧分析")
    w()
    for fi, f in enumerate(frames, 1):
        ft = f.get("frame_token", "?")
        threads = f.get("threads", [])
        binder = f.get("binder_edges", [])
        futex = f.get("futex_activity", [])

        h(3, f"帧 #{fi} — Token: {ft}")
        w()

        if threads:
            sorted_t = sorted(threads, key=lambda x: -x.get("runnable_delay_ns", 0))
            h(4, f"调度延迟 (Top-10)")
            table_h5 = ["TID", "角色", "线程名", "Total Delay", "P95 Delay", "Actual Run", "关键?"]
            table_r5 = []
            for t in sorted_t[:10]:
                table_r5.append([
                    t["tid"], f"{role_emoji(t.get('role',''))}{t.get('role','')}",
                    t.get("comm", "?"),
                    fmt_ns(t.get("runnable_delay_ns", 0)),
                    fmt_ns(t.get("runnable_delay_p95_ns", 0)),
                    fmt_ns(t.get("actual_run_ns", 0)),
                    "⚠️" if t.get("critical_for_hint") else "",
                ])
            table(table_h5, table_r5)

        if binder:
            h(4, f"Binder 调用 ({len(binder)} 对)")
            table_h6 = ["延迟", "调用方", "→", "服务方"]
            table_r6 = []
            for b in sorted(binder, key=lambda x: -x.get("latency_ns", 0))[:5]:
                table_r6.append([
                    fmt_ns(b.get("latency_ns", 0)),
                    f"`{b.get('tx_comm','?')}`",
                    "→",
                    f"`{b.get('rx_comm','?')}`",
                ])
            table(table_h6, table_r6)

        if futex:
            h(4, "Futex 活动")
            for fa in futex[:5]:
                w(f"- TID:{fa['tid']} `{fa['comm']}`  WAIT={fa.get('futex_wait_count',0)}  WAKE={fa.get('futex_wake_count',0)}")
        w()

    # ═══════ 五、关键路径 ═══════
    h(2, "五、关键路径分析")
    w()
    for f_data in graph.get("frames", []):
        cps = f_data.get("critical_paths", [])
        if not cps:
            continue

        ft = f_data.get("frame_token", "?")
        h(3, f"Frame {ft} — {len(cps)} 条关键路径")
        w()

        for cp in cps:
            rank = cp.get("rank", "?")
            total = cp.get("total_cost_ns", 0)
            path_nodes = cp.get("path_nodes", [])
            path_edges = cp.get("path_edges", [])
            if not path_nodes:
                continue

            # 构建边类型摘要
            edge_types = set(e["type"] for e in path_edges)
            type_summary = " → ".join(sorted(edge_types))
            w(f"**路径 #{rank}**: 总代价={fmt_ns(total)}, 边类型: {type_summary}")
            w()

            # 简化: 只列出线程名 + 角色
            thread_parts = []
            for nid in path_nodes:
                if nid.startswith("thread_"):
                    for node in f_data.get("nodes", []):
                        if node["id"] == nid:
                            role = node.get("role", "")
                            em = role_emoji(role)
                            tid_val = node.get("tid", "?")
                            comm = node.get("comm", "?")
                            br = cp.get("node_block_ratios", {}).get(nid, 0)
                            thread_parts.append(f"{em}`{comm}`(TID:{tid_val}) [br={br:.2f}]")
                            break
                elif nid.startswith("frame_"):
                    thread_parts.append(f"📦 Frame")

            w(" → ".join(thread_parts))
            w()

    # ═══════ 六、归因分析 ═══════
    h(2, "六、延迟归因 (Root-Cause Attribution)")
    w()
    if root_cause:
        attributions = root_cause.get("attributions", [])
        if attributions:
            w("对 Top-K 线程逐帧汇总延迟构成：调度竞争(Runnable Delay) + Binder IPC + Futex 锁。")
            w()
            table_h7 = ["TID", "线程名", "调度竞争", "Binder IPC", "Futex", "CPU频率(avg)", "根因"]
            table_r7 = []
            for a in attributions[:10]:
                attr = a.get("attribution", {})
                freq = attr.get("cpu_freq_avg_mhz", 0)
                freq_str = f"{freq}MHz" if freq > 0 else "—"
                table_r7.append([
                    a["tid"], a["comm"],
                    f"{attr.get('runnable_delay_ms',0):.2f}ms",
                    f"{attr.get('binder_latency_ms',0):.2f}ms",
                    f"{attr.get('futex_wait_count',0)}次",
                    freq_str,
                    a.get("root_cause", ""),
                ])
            table(table_h7, table_r7)
        else:
            w("*未生成归因数据*")
    else:
        w("*运行 root_cause.py 后可生成此部分*")
    w()

    # ═══════ 七、总结 ═══════
    h(2, "七、总结与建议")
    w()

    # 从 Top-K 提取卡片
    if top_k:
        w("### 首要关注线程")
        w()
        for s in top_k[:5]:
            c = s["components"]
            on_cp = c.get("on_critical_path_ratio", 0)
            root_mark = " ★ 关键路径根因" if on_cp > 0.3 else ""
            w(f"- **{role_emoji(s['role'])} {s['role']}** `{s['comm']}` (TID:{s['tid']}) — "
              f"Score={fmt_score(s['score'])}, "
              f"P95延迟={fmt_ns(c.get('runnable_delay_p95_ns',0))}"
              f"{root_mark}")
        w()

    w("### 观察者效应")
    ebpf_tids = [s for s in scores if "camera_ebpf" in s.get("comm", "")]
    if ebpf_tids:
        w(f"- eBPF 探针自身出现在 Top-K 中（最高排名 #{next(i+1 for i,s in enumerate(scores) if 'camera_ebpf' in s.get('comm',''))}), "
          f"对系统有观测扰动")
    w()

    w("### 下一步建议")
    w("- 若 Binder 延迟高 → 关注 `system_server` / `surfaceflinger` 的调度压力")
    w("- 若 Futex 竞争多 → 检查相机 pipeline 内部锁设计")
    w("- 若 GPU 线程卡顿 → 添加 GPU frequency / Mali 跟踪点进一步定位")
    w("- 若探针扰动大 → 考虑内核态 UID 预过滤减少事件量")

    # 写文件
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[✓] Report generated: {out_path}")
    print(f"    {len(lines)} lines, {len(frames)} frames, {len(scores)} threads scored")


if __name__ == "__main__":
    generate()
