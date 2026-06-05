"""
Critical Path Graph Builder & CriticalScore Engine
====================================================
基于 Frame-Centric 分析结果，构建交互关键路径图，
并对线程进行多维度评分排序，识别卡顿根因。

输入: delay_analysis_result.json + ebpf_target_windows.json
输出: critical_path_graph.json
"""

import json
import os
import sys
import math
from collections import defaultdict


# ═══════════════════════════════════════════════════════════
#  角色识别 (与 analyze_delays.py 保持一致)
# ═══════════════════════════════════════════════════════════

def identify_role(comm, tid, target_pid):
    cl = (comm or "").lower()
    # 内核线程: swapper (CPU idle), kworker (内核工作队列), kswapd0 (内存回收)
    if cl.startswith("swapper/") or cl.startswith("kworker") or cl == "kswapd0":
        return "KernelWorker"
    if tid == target_pid:
        return "UI Thread"
    if "renderthread" in cl or cl.startswith("rend"):
        return "RenderThread"
    if "binder" in cl:
        return "HwBinder RPC" if "hw" in cl else "Binder RPC"
    if "surfaceflinger" in cl:
        return "SurfaceFlinger"
    if "system_server" in cl or "systemui" in cl:
        return "SystemService"
    if cl.startswith("com.") or cl.startswith("android."):
        return "UI Thread"
    if "gpu" in cl or "gl" in cl:
        return "GPU Worker"
    if "io" in cl or "disk" in cl:
        return "I/O Worker"
    if "hwc" in cl or "composer" in cl:
        return "HwComposer"
    return "UnknownWorker"


# ═══════════════════════════════════════════════════════════
#  节点与边的数据结构
# ═══════════════════════════════════════════════════════════

class GraphNode:
    def __init__(self, node_id, node_type, **attrs):
        self.id = node_id
        self.type = node_type      # "thread" | "frame" | "resource"
        self.attrs = attrs

    def to_dict(self):
        return {"id": self.id, "type": self.type, **self.attrs}


class GraphEdge:
    def __init__(self, src, dst, edge_type, **attrs):
        self.src = src
        self.dst = dst
        self.type = edge_type      # RUNNABLE_WAIT | BINDER_CALL | FUTEX_WAIT | CPU_RUN | FRAME_DEPENDENCY
        self.attrs = attrs

    def to_dict(self):
        return {"src": self.src, "dst": self.dst, "type": self.type, **self.attrs}


# ═══════════════════════════════════════════════════════════
#  主构建器
# ═══════════════════════════════════════════════════════════

class CriticalPathBuilder:
    def __init__(self, delay_json_path, window_json_path):
        with open(delay_json_path) as f:
            self.delay_data = json.load(f)
        with open(window_json_path) as f:
            self.window_data = json.load(f)

        self.target_pid  = self.delay_data.get("pid", 0)
        self.target_uid  = self.delay_data.get("uid", 0)
        self.target_pkg  = self.delay_data.get("target_package", "unknown")

        # 帧列表 (来自 delay_data)
        self.frames = self.delay_data.get("frames", [])

        # 原始 jank_frames (来自 window_data, 含时间戳)
        self.jank_windows = {}
        for w in self.window_data.get("jank_frames", []):
            ft = w.get("frame_token")
            if ft is not None:
                self.jank_windows[ft] = w

        # 全局线程信息累积 (跨帧)
        # tid -> { comm, role, delay_samples[], binder_centrality_score, futex_wait_total, ... }
        self.global_threads = defaultdict(lambda: {
            "comm": "?", "role": "UnknownWorker",
            "delay_samples": [],      # [(frame_token, d_ns), ...]  — 每帧累加延迟
            "delay_events": [],       # 各次唤醒→switch的延迟样本 — 用于 P95
            "run_samples": [],
            "binder_in_degree": 0,    # 作为服务端被调用的次数
            "binder_out_degree": 0,   # 作为客户端发起调用的次数
            "binder_latency_samples": [],
            "futex_wait_total": 0,
            "futex_wake_total": 0,
            "frame_occurrences": 0,   # 出现在多少个 jank frame 中
            "is_foreground": False,
            "is_kernel": False,       # swapper/kworker 等内核线程
            # 关键路径分析新增
            "on_critical_path_count": 0,
            "upstream_block_cost": 0,
            "block_ratios": [],
        })

        # 每帧的图: frame_token -> Graph
        self.per_frame_graphs = {}

    # ── 构建 ──
    def build(self):
        total_frames = len(self.frames)
        if total_frames == 0:
            print("[!] No frames to analyze.")
            return

        for fi, frame_report in enumerate(self.frames):
            ft = frame_report.get("frame_token")
            if ft is None:
                continue
            self._build_one_frame(ft, frame_report)

        print(f"[*] Built graphs for {len(self.per_frame_graphs)} frames.")

    def _build_one_frame(self, frame_token, report):
        nodes = {}   # node_id -> GraphNode
        edges = []   # list of GraphEdge

        window = self.jank_windows.get(frame_token, {})
        ws = window.get("window_start_ns", 0)
        we = window.get("window_end_ns", 0)
        frame_dur = max(1, we - ws)

        # ── Frame Node ──
        frame_nid = f"frame_{frame_token}"
        nodes[frame_nid] = GraphNode(frame_nid, "frame",
            frame_token=frame_token,
            jank_type=window.get("jank_type", ""),
            window_start_ns=ws,
            window_end_ns=we,
            duration_ns=window.get("actual_duration_ns", frame_dur),
        )

        # ── Thread Nodes (from sched) ──
        for t in report.get("threads", []):
            tid = t["tid"]
            comm = t.get("comm", "?")
            role = identify_role(comm, tid, self.target_pid)
            nid = f"thread_{tid}"

            if nid not in nodes:
                nodes[nid] = GraphNode(nid, "thread",
                    tid=tid, comm=comm, role=role)

            # edge: Thread → Frame (FRAME_DEPENDENCY)
            d_ns = t.get("runnable_delay_ns", 0)
            r_ns = t.get("actual_run_ns", 0)
            if d_ns > 0:
                edges.append(GraphEdge(nid, frame_nid, "RUNNABLE_WAIT",
                    duration_ns=d_ns,
                    actual_run_ns=r_ns,
                    overlap_ratio=min(1.0, (d_ns + r_ns) / frame_dur),
                ))

            # 累积全局统计
            gt = self.global_threads[tid]
            gt["comm"] = comm
            gt["role"] = role
            gt["delay_samples"].append((frame_token, d_ns))
            # 各次唤醒→switch的原始样本 (用于 P95)
            for ev in t.get("delay_events", []):
                gt["delay_events"].append(ev)
            gt["run_samples"].append((frame_token, r_ns))
            gt["frame_occurrences"] = len(set(s[0] for s in gt["delay_samples"]))
            if role not in ("UnknownWorker", "KernelWorker"):
                gt["is_foreground"] = True
            if role == "KernelWorker":
                gt["is_kernel"] = True

        # ── Binder Edges ──
        for b in report.get("binder_edges", []):
            tx_tid = b["tx_tid"]
            rx_tid = b["rx_tid"]
            lat_ns = b.get("latency_ns", 0)

            src_nid = f"thread_{tx_tid}"
            dst_nid = f"thread_{rx_tid}"

            # 确保节点存在
            for tid, nid in [(tx_tid, src_nid), (rx_tid, dst_nid)]:
                if nid not in nodes:
                    comm = b.get(f"{'tx' if nid == src_nid else 'rx'}_comm", "?")
                    role = identify_role(comm, tid, self.target_pid)
                    nodes[nid] = GraphNode(nid, "thread",
                        tid=tid, comm=comm, role=role)

            edges.append(GraphEdge(src_nid, dst_nid, "BINDER_CALL",
                debug_id=b.get("debug_id"),
                latency_ns=lat_ns,
                code=b.get("code", 0),
                is_reply=b.get("is_reply", False),
            ))

            # 累积 binder 统计
            self.global_threads[tx_tid]["binder_out_degree"] += 1
            self.global_threads[tx_tid]["binder_latency_samples"].append(lat_ns)
            self.global_threads[rx_tid]["binder_in_degree"] += 1
            self.global_threads[rx_tid]["binder_latency_samples"].append(lat_ns)

        # ── Futex Edges (方向未知, 记录为自环表示"发生过futex等待") ──
        for f in report.get("futex_activity", []):
            tid = f["tid"]
            nid = f"thread_{tid}"
            comm = f.get("comm", "?")
            role = identify_role(comm, tid, self.target_pid)

            if nid not in nodes:
                nodes[nid] = GraphNode(nid, "thread",
                    tid=tid, comm=comm, role=role)

            wc = f.get("futex_wait_count", 0)
            kc = f.get("futex_wake_count", 0)
            if wc > 0:
                edges.append(GraphEdge(nid, nid, "FUTEX_WAIT",
                    wait_count=wc, wake_count=kc))

            self.global_threads[tid]["futex_wait_total"] += wc
            self.global_threads[tid]["futex_wake_total"] += kc

        self.per_frame_graphs[frame_token] = {
            "frame_token": frame_token,
            "nodes": [n.to_dict() for n in nodes.values()],
            "edges": [e.to_dict() for e in edges],
        }

    # ═════════════════════════════════════════════════════════
    #  关键路径分析 (Critical Path Analysis)
    #  ─ 找到每帧 DAG 中 TOP-3 最长阻塞链
    #  ─ 包含 BINDER_CALL 链，不只单个 RUNNABLE_WAIT
    # ═════════════════════════════════════════════════════════

    def find_critical_paths(self):
        """
        对每帧图运行拓扑 DP，找出 TOP-3 关键路径。
        每条路径追踪: Thread →[BINDER]→ ... →[RUNNABLE_WAIT]→ Frame
        结果写入:
          - per_frame_graphs[ft]['critical_paths'] = [ {nodes, edges, total_cost}, ... ]
          - global_threads[tid]['on_critical_path_count']
          - global_threads[tid]['upstream_block_cost']
        """
        print("\n[*] Computing critical paths per frame...")

        for ft, graph in self.per_frame_graphs.items():
            nodes = {n["id"]: n for n in graph["nodes"]}
            edges = graph["edges"]

            # ── 1. 构建加权邻接表 ──
            adj = defaultdict(list)
            in_degree = defaultdict(int)

            for e in edges:
                u, v, etype = e["src"], e["dst"], e["type"]
                if u == v:  # 自环不算 (futex)
                    continue
                w = e.get("duration_ns", 0) or e.get("latency_ns", 0)
                if w <= 0:
                    w = 1
                adj[u].append((v, w, etype))
                in_degree[v] = in_degree.get(v, 0) + 1
                in_degree.setdefault(u, 0)

            if not adj:
                continue

            # ── 2. 拓扑排序 ──
            queue = [nid for nid in nodes if in_degree.get(nid, 0) == 0]
            if not queue and nodes:
                min_in = min(in_degree.values())
                queue = [nid for nid, d in in_degree.items() if d == min_in][:1]

            topo_order = []
            indeg = dict(in_degree)
            q = list(queue)
            while q:
                u = q.pop(0)
                topo_order.append(u)
                for v, w, _ in adj.get(u, []):
                    indeg[v] = indeg.get(v, 0) - 1
                    if indeg[v] == 0:
                        q.append(v)

            # ── 3. 前向 DP: upstream_cost[n] = 到达 n 的最长路径 ──
            upstream_cost = defaultdict(int)
            upstream_parent = {}  # node -> (parent, edge_type, edge_weight)

            for u in topo_order:
                for v, w, etype in adj.get(u, []):
                    new_cost = upstream_cost[u] + w
                    if new_cost > upstream_cost[v]:
                        upstream_cost[v] = new_cost
                        upstream_parent[v] = (u, etype, w)

            frame_nid = f"frame_{ft}"

            # ── 4. 从 Frame 回溯所有直接上游线程, 找 TOP-3 路径 ──
            # 每个线程到 Frame 的 RUNNABLE_WAIT 边形成一条路径的终点段
            all_paths = []

            for e in edges:
                if e["dst"] != frame_nid or e["src"] == frame_nid:
                    continue
                thread_nid = e["src"]
                if thread_nid not in upstream_cost:
                    continue

                # 回溯从 thread_nid 到源头的完整路径
                path_nodes = [frame_nid, thread_nid]
                path_edges = [{
                    "src": thread_nid, "dst": frame_nid,
                    "type": e["type"],
                    "weight_ns": e.get("duration_ns", 0) or e.get("latency_ns", 0),
                }]

                cur = thread_nid
                while cur in upstream_parent:
                    parent, etype, w = upstream_parent[cur]
                    path_nodes.append(parent)
                    path_edges.append({
                        "src": parent, "dst": cur,
                        "type": etype,
                        "weight_ns": w,
                    })
                    cur = parent

                # 总代价 = upstream_cost[thread_nid] + RUNNABLE_WAIT 的权重
                rw_weight = e.get("duration_ns", 0) or 0
                total = upstream_cost[thread_nid] + rw_weight

                all_paths.append({
                    "total_cost_ns": total,
                    "path_nodes": path_nodes,  # 从 frame → source (逆序)
                    "path_edges": path_edges,
                })

            # 按 total_cost 降序, 取 TOP-3
            all_paths.sort(key=lambda p: -p["total_cost_ns"])
            top_paths = all_paths[:3]

            # ── 5. 写入图结构 ──
            graph["critical_paths"] = []
            all_critical_nodes = set()

            for pi, path in enumerate(top_paths):
                node_block_ratios = {}
                for nid in path["path_nodes"]:
                    uc = upstream_cost.get(nid, 0)
                    node_block_ratios[nid] = round(uc / max(path["total_cost_ns"], 1), 4)

                graph["critical_paths"].append({
                    "rank": pi + 1,
                    "total_cost_ns": path["total_cost_ns"],
                    "path_nodes": path["path_nodes"],
                    "path_edges": path["path_edges"],
                    "node_block_ratios": node_block_ratios,
                })
                all_critical_nodes.update(
                    nid for nid in path["path_nodes"] if nid.startswith("thread_")
                )

            # ── 6. 累积全局统计 (去重, 每个线程每帧只计一次) ──
            for nid in all_critical_nodes:
                tid = int(nid.split("_", 1)[1])
                gt = self.global_threads[tid]
                gt["on_critical_path_count"] = gt.get("on_critical_path_count", 0) + 1
                gt["upstream_block_cost"] = gt.get("upstream_block_cost", 0) + upstream_cost.get(nid, 0)
                # block_ratio 取所有路径中的最大值
                max_ratio = max(
                    (cp["node_block_ratios"].get(nid, 0) for cp in graph["critical_paths"]),
                    default=0
                )
                gt["block_ratios"].append(max_ratio)

        # 统计汇总
        cp_count = sum(1 for g in self.per_frame_graphs.values() if g.get("critical_paths"))
        print(f"[*] Critical paths found in {cp_count}/{len(self.per_frame_graphs)} frames.")

    # ── CriticalScore 计算 ──
    def compute_critical_scores(self, weights=None):
        """
        公式:
          CriticalScore(tid) =
              + a * frame_window_overlap_ratio
              + b * runnable_delay_p95_norm
              + c * binder_centrality_norm
              + d * futex_wait_norm
              + e * render_path_proximity
              + f * repeated_jank_cooccurrence
              + h * on_critical_path_norm        ← 新增: 图分析结果
              - g * background_penalty

        关键改进: h 维度由 find_critical_paths() 提供。
        如果某线程在 jank frame 的关键路径上反复出现,
        说明它不是"数字高"而是"真的在阻塞链上", 应给高分。
        """
        if weights is None:
            weights = {
                "a": 0.8,   # frame_window_overlap
                "b": 2.0,   # runnable_delay_p95
                "c": 1.5,   # binder_centrality
                "d": 0.8,   # futex_wait
                "e": 1.2,   # render_path_proximity
                "f": 0.4,   # repeated_jank
                "h": 1.8,   # on_critical_path (图分析, 高权重)
                "g": 0.5,   # background_penalty
            }

        total_frames = max(1, len(self.frames))
        scores = []

        # 先收集原始值用于归一化
        raw = {}
        for tid, gt in self.global_threads.items():
            delays = [e for e in gt["delay_events"] if e > 0]  # 各次唤醒→switch样本
            p95 = sorted(delays)[int(len(delays) * 0.95)] if len(delays) >= 20 else (max(delays) if delays else 0)

            overlap_ratio = gt["frame_occurrences"] / total_frames
            binder_centrality = gt["binder_in_degree"] + gt["binder_out_degree"] * 0.5
            futex_wait = gt["futex_wait_total"]
            repeated = gt["frame_occurrences"] / total_frames
            is_bg = 0.0 if gt["is_foreground"] else 1.0

            # 关键路径维度: 在线程在关键路径上出现的帧占比
            on_cp = gt["on_critical_path_count"] / max(1, total_frames)
            avg_block_ratio = (sum(gt["block_ratios"]) / max(1, len(gt["block_ratios"]))
                               if gt["block_ratios"] else 0.0)

            # render_path_proximity: 越接近渲染路径分越高
            role = gt["role"]
            if role in ("UI Thread", "RenderThread"):
                rpp = 1.0
            elif role in ("SurfaceFlinger", "HwComposer"):
                rpp = 0.8
            elif role in ("Binder RPC", "HwBinder RPC"):
                rpp = 0.6
            elif role in ("GPU Worker",):
                rpp = 0.5
            elif role in ("SystemService",):
                rpp = 0.4
            else:
                rpp = 0.1

            raw[tid] = {
                "p95": p95,
                "overlap": overlap_ratio,
                "binder_c": binder_centrality,
                "futex_w": futex_wait,
                "rpp": rpp,
                "repeated": repeated,
                "on_cp": on_cp,
                "block_ratio": avg_block_ratio,
                "is_bg": is_bg,
            }

        # 归一化辅助
        def safe_max(vals, default=1):
            m = max(vals) if vals else default
            return max(m, 1)

        max_p95     = safe_max([r["p95"] for r in raw.values()])
        max_binder  = safe_max([r["binder_c"] for r in raw.values()])
        max_futex   = safe_max([r["futex_w"] for r in raw.values()])

        for tid, r in raw.items():
            gt = self.global_threads[tid]
            # 内核线程 (swapper/kworker) 不参与 CriticalScore 排名
            if gt.get("is_kernel"):
                continue
            p95_norm     = r["p95"] / max_p95
            binder_norm  = r["binder_c"] / max_binder
            futex_norm   = r["futex_w"] / max_futex

            score = (
                weights["a"] * r["overlap"] +
                weights["b"] * p95_norm +
                weights["c"] * binder_norm +
                weights["d"] * futex_norm +
                weights["e"] * r["rpp"] +
                weights["f"] * r["repeated"] +
                weights["h"] * r["on_cp"] -
                weights["g"] * r["is_bg"]
            )

            scores.append({
                "tid": tid,
                "comm": gt["comm"],
                "role": gt["role"],
                "score": round(score, 4),
                "components": {
                    "frame_overlap":      round(r["overlap"], 4),
                    "runnable_delay_p95_ns": r["p95"],
                    "runnable_delay_p95_norm": round(p95_norm, 4),
                    "binder_centrality_raw": r["binder_c"],
                    "binder_centrality_norm": round(binder_norm, 4),
                    "futex_wait_total":   r["futex_w"],
                    "futex_wait_norm":    round(futex_norm, 4),
                    "render_path_proximity": r["rpp"],
                    "repeated_jank_ratio": round(r["repeated"], 4),
                    "on_critical_path_ratio": round(r["on_cp"], 4),
                    "avg_block_ratio":    round(r["block_ratio"], 4),
                    "is_background":      bool(r["is_bg"]),
                },
            })

        scores.sort(key=lambda x: -x["score"])
        return scores, weights

    # ── 导出 ──
    def export(self, output_path):
        # 先做图分析, 再做聚合评分 (关键路径结果影响 CriticalScore)
        self.find_critical_paths()
        scores, weights = self.compute_critical_scores()

        result = {
            "meta": {
                "target_package": self.target_pkg,
                "pid": self.target_pid,
                "uid": self.target_uid,
                "total_jank_frames": len(self.frames),
                "scoring_weights": weights,
            },
            "global_critical_scores": scores,
            "frames": [
                self.per_frame_graphs[ft]
                for ft in sorted(self.per_frame_graphs.keys())
            ],
        }

        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)

        print(f"\n[*] Critical Path Graph exported to: {output_path}")

        # 打印 Top-K (增强版)
        print(f"\n{'='*70}")
        print(f"  Top-10 Critical Threads (CriticalScore + Graph Analysis)")
        print(f"{'='*70}")
        print(f"  {'#':<3} {'TID':<8} {'Role':<18} {'Comm':<22} {'Score':<8} {'onCP':<6} {'isRoot'}")
        print(f"  {'-'*67}")
        for i, s in enumerate(scores[:10], 1):
            on_cp = s["components"].get("on_critical_path_ratio", 0)
            block = s["components"].get("avg_block_ratio", 0)
            # 根因判定: 在关键路径上 且 阻塞占比高 (上游)
            is_root = " ★ROOT" if (on_cp > 0.3 and block > 0.5) else ""
            print(f"  {i:<3} TID:{s['tid']:<6} [{s['role']:<16}] {s['comm']:<22} "
                  f"{s['score']:<8.4f} {on_cp:<6.3f}{is_root}")

        return result


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════

def parse_args():
    args = {'delay': None, 'window': None, 'out': None}
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '--delay' and i+1 < len(sys.argv):
            args['delay'] = sys.argv[i+1]; i += 2
        elif sys.argv[i] == '--window' and i+1 < len(sys.argv):
            args['window'] = sys.argv[i+1]; i += 2
        elif sys.argv[i] == '--out' and i+1 < len(sys.argv):
            args['out'] = sys.argv[i+1]; i += 2
        else:
            i += 1
    return args


def main():
    args = parse_args()
    base = os.path.dirname(os.path.abspath(__file__))

    delay_path = args['delay'] or os.path.join(base, "..", "output", "analysis", "delay_analysis_result.json")
    window_path = args['window'] or os.path.join(base, "..", "output", "analysis", "ebpf_target_windows.json")
    out_path = args['out'] or os.path.join(base, "..", "output", "analysis", "critical_path_graph.json")

    if not os.path.exists(delay_path):
        print(f"Error: {delay_path} not found. Run analyze_delays.py first.")
        sys.exit(1)
    if not os.path.exists(window_path):
        print(f"Error: {window_path} not found.")
        sys.exit(1)

    print(f"[*] Loading delay analysis: {delay_path}")
    print(f"[*] Loading jank windows:   {window_path}")

    builder = CriticalPathBuilder(delay_path, window_path)
    builder.build()
    builder.export(out_path)


if __name__ == "__main__":
    main()
