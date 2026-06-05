#!/usr/bin/env python3
"""
Tuning Profile Generator — 从离线 CriticalScore 分析生成持久化调度调优配置

定位说明:
  本工具是 OFFLINE pipeline 的最后一步。它的输出不是"实时 hint"（那需要
  设备端在线分析），而是基于完整 trace 会话的**持久化调优建议**。
  
  工作流:  手机采集 → PC 分析 → 生成 Tuning Profile → (下次)应用到手机
  时效性:  建议在下一次相机启动时应用，或在系统 init 阶段固化
  重评估:  建议每次 trace 后重新生成，覆盖旧配置

设计原则:
  1. 安全性: 黑名单 + 置信度阈值 → 宁可漏报不可误报
  2. 可落地: 每个建议附带具体的 adb shell 命令
  3. 可审计: 每个建议附带 rationale 说明为什么
  4. 持久化: 输出是配置而非瞬态 hint，无 TTL

输入: output/analysis/critical_path_graph.json
输出: output/analysis/tuning_profile.json

用法:
  python3 safe_hint_engine.py [--threshold 0.6] [--max-hints 10]
"""

import json
import os
import sys
import time
from collections import defaultdict

# ═══════════════════════════════════════════════════════════
#  安全黑名单
# ═══════════════════════════════════════════════════════════

SYSTEM_BLACKLIST = {
    "system_server", "surfaceflinger", "servicemanager",
    "init", "kthreadd", "vold", "logd", "lmkd",
    "hwservicemanager", "audioserver", "mediaserver",
    "netd", "zygote", "zygote64",
}

ROLE_BLACKLIST = {
    "SystemService",
    "KernelWorker",
    "HwBinder RPC",
}

OBSERVER_COMMS = {"camera_ebpf_and"}


# ═══════════════════════════════════════════════════════════
#  置信度计算
# ═══════════════════════════════════════════════════════════

def compute_confidence(score, score_max, on_cp_ratio, p95_ns, sample_count):
    """
    4 因子加权置信度:
      - score_percentile  (0.35): CriticalScore 在全量中的百分位
      - on_critical_path  (0.30): 是否在 DAG 关键路径上
      - p95_severity      (0.20): P95 延迟严重程度
      - sample_sufficiency(0.15): 样本量是否充足
    """
    score_pct = min(1.0, score / max(score_max, 0.001))
    cp_factor = min(1.0, on_cp_ratio * 3.0)

    p95_ms = p95_ns / 1_000_000
    if p95_ms < 3:
        p95_factor = 0.0
    elif p95_ms < 10:
        p95_factor = (p95_ms - 3) / 7 * 0.5
    elif p95_ms < 50:
        p95_factor = 0.5 + (p95_ms - 10) / 40 * 0.5
    else:
        p95_factor = 1.0

    sample_factor = min(1.0, sample_count / 20.0)

    return round(0.35 * score_pct + 0.30 * cp_factor +
                 0.20 * p95_factor + 0.15 * sample_factor, 4)


# ═══════════════════════════════════════════════════════════
#  调优动作生成 + 落地方案
# ═══════════════════════════════════════════════════════════

def generate_recommendations(scores_entry):
    """
    返回 recommendations 列表。每条包含具体的 adb shell 命令。
    """
    role = scores_entry.get("role", "")
    comm = scores_entry.get("comm", "")
    c = scores_entry["components"]
    p95_ms = c.get("runnable_delay_p95_ns", 0) / 1_000_000
    on_cp = c.get("on_critical_path_ratio", 0)
    overlap = c.get("frame_overlap", 0)

    recs = []
    seen_types = set()

    def add(atype, params, rationale, commands):
        if atype not in seen_types:
            seen_types.add(atype)
            recs.append({
                "type": atype,
                "params": params,
                "rationale": rationale,
                "apply_commands": commands,
            })

    # ── RenderThread ──
    if role == "RenderThread":
        if p95_ms > 5 and on_cp > 0.1:
            boost = 512 if p95_ms > 20 else 384
            add("uclamp_boost", {"util_min": boost},
                f"P95={p95_ms:.1f}ms 在关键路径上",
                [f'TID=$(find_tid "{comm}") && echo {boost} > /proc/$TID/sched_util_clamp_min'])
        elif p95_ms > 3:
            add("cpu_affinity_big", {"cpus": "4-7"},
                f"P95={p95_ms:.1f}ms, 固定大核",
                [f'TID=$(find_tid "{comm}") && taskset -p 0xF0 $TID'])
        else:
            add("monitor", {},
                "在关键路径上, 当前正常但需持续关注",
                [])

    # ── UI Thread ──
    elif role == "UI Thread":
        if p95_ms > 5:
            add("uclamp_boost", {"util_min": 384},
                f"P95={p95_ms:.1f}ms",
                [f'TID=$(find_tid "{comm}") && echo 384 > /proc/$TID/sched_util_clamp_min'])
        elif p95_ms > 1:
            add("cgroup_fg", {"cgroup": "foreground"},
                f"P95={p95_ms:.1f}ms, 前台 cgroup",
                [f'echo $PID > /dev/cpuctl/foreground/tasks'])
        if on_cp > 0.1 and "uclamp_boost" not in seen_types:
            add("monitor", {},
                f"在关键路径上但 P95 正常 (ratio={on_cp:.0%})",
                [])

    # ── GPU Worker ──
    elif role == "GPU Worker":
        if p95_ms > 20:
            add("cpu_affinity_big", {"cpus": "4-7"},
                f"P95={p95_ms:.1f}ms",
                [f'TID=$(find_tid "{comm}") && taskset -p 0xF0 $TID'])
        if p95_ms > 10:
            boost = 512 if p95_ms > 30 else 384
            add("uclamp_boost", {"util_min": boost},
                f"P95={p95_ms:.1f}ms",
                [f'TID=$(find_tid "{comm}") && echo {boost} > /proc/$TID/sched_util_clamp_min'])

    # ── I/O Worker ──
    elif role == "I/O Worker":
        if p95_ms > 15:
            add("cpu_affinity_big", {"cpus": "4-7"},
                f"P95={p95_ms:.1f}ms",
                [f'TID=$(find_tid "{comm}") && taskset -p 0xF0 $TID'])
        if p95_ms > 10:
            add("uclamp_boost", {"util_min": 256},
                f"P95={p95_ms:.1f}ms",
                [f'TID=$(find_tid "{comm}") && echo 256 > /proc/$TID/sched_util_clamp_min'])

    # ── Binder RPC ──
    elif role == "Binder RPC":
        if p95_ms > 10:
            add("uclamp_boost", {"util_min": 384},
                f"P95={p95_ms:.1f}ms, 加速 IPC",
                [f'TID=$(find_tid "{comm}") && echo 384 > /proc/$TID/sched_util_clamp_min'])
        elif p95_ms > 3:
            add("uclamp_boost", {"util_min": 256},
                f"P95={p95_ms:.1f}ms",
                [f'TID=$(find_tid "{comm}") && echo 256 > /proc/$TID/sched_util_clamp_min'])

    # ── HwComposer ──
    elif role == "HwComposer":
        if p95_ms > 5:
            add("cpu_affinity_big", {"cpus": "4-7"},
                f"P95={p95_ms:.1f}ms",
                [f'TID=$(find_tid "{comm}") && taskset -p 0xF0 $TID'])

    # ── UnknownWorker ──
    else:
        if p95_ms > 30:
            add("uclamp_boost", {"util_min": 512},
                f"P95={p95_ms:.1f}ms 严重延迟",
                [f'TID=$(find_tid "{comm}") && echo 512 > /proc/$TID/sched_util_clamp_min'])
        elif p95_ms > 15:
            add("monitor", {},
                f"P95={p95_ms:.1f}ms 覆盖 {overlap:.0%} 帧",
                [])
        elif p95_ms > 5 and overlap > 0.4:
            add("monitor", {},
                f"P95={p95_ms:.1f}ms 覆盖 {overlap:.0%} 帧",
                [])

    return recs


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(base_dir, "..", "output", "analysis",
                              "critical_path_graph.json")
    output_path = os.path.join(base_dir, "..", "output", "analysis",
                               "tuning_profile.json")

    threshold = 0.6
    max_hints = 10

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--threshold" and i + 1 < len(sys.argv):
            threshold = float(sys.argv[i + 1]); i += 2
        elif sys.argv[i] == "--max-hints" and i + 1 < len(sys.argv):
            max_hints = int(sys.argv[i + 1]); i += 2
        else:
            i += 1

    if not os.path.exists(input_path):
        print(f"[!] {input_path} not found. Run critical_path.py first.")
        sys.exit(1)

    with open(input_path, 'r') as f:
        graph = json.load(f)

    scores = graph.get("global_critical_scores", [])
    if not scores:
        print("[!] No critical scores found.")
        sys.exit(0)

    print(f"[*] Loaded {len(scores)} scored threads.")
    print(f"[*] Confidence threshold: {threshold:.0%}, Max: {max_hints}")
    print()

    score_max = max(s["score"] for s in scores) if scores else 1
    candidates = []

    for s in scores:
        tid = s["tid"]
        comm = s.get("comm", "")
        role = s.get("role", "")

        if comm in SYSTEM_BLACKLIST or role in ROLE_BLACKLIST:
            continue
        if comm in OBSERVER_COMMS:
            continue

        c = s["components"]
        p95_ns = c.get("runnable_delay_p95_ns", 0)
        on_cp = c.get("on_critical_path_ratio", 0)
        overlap = c.get("frame_overlap", 0)

        total_frames = graph.get("meta", {}).get("total_frames", 7)
        sample_count = int(overlap * total_frames * 10)

        conf = compute_confidence(s["score"], score_max, on_cp, p95_ns, sample_count)
        if conf < threshold:
            continue

        recs = generate_recommendations(s)
        if not recs:
            continue

        candidates.append({
            "tid": tid,
            "comm": comm,
            "role": role,
            "score": s["score"],
            "confidence": conf,
            "p95_delay_ms": round(p95_ns / 1_000_000, 2),
            "on_critical_path": round(on_cp, 3),
            "frame_coverage": round(overlap, 3),
            "recommendations": recs,
        })

    candidates.sort(key=lambda x: -x["confidence"])

    # 去重: 每种角色最多 2 条
    role_counts = defaultdict(int)
    final = []
    for c in candidates:
        if role_counts[c["role"]] < 2:
            role_counts[c["role"]] += 1
            final.append(c)
        if len(final) >= max_hints:
            break

    # 汇总所有 shell 命令
    all_commands = []
    for f in final:
        for r in f["recommendations"]:
            all_commands.extend(r.get("apply_commands", []))

    profile = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "generator": "safe_hint_engine.py (Tuning Profile mode)",
        "semantics": "PERSISTENT",
        "description": (
            "离线 trace 分析 → 持久化调度调优配置。"
            "下次相机启动时应用这些设置, "
            "或集成到 init.rc。新 trace 后重新生成覆盖。"
        ),
        "config": {
            "confidence_threshold": threshold,
        },
        "summary": {
            "total_scored": len(scores),
            "recommendations": len(final),
        },
        "apply_all_commands": all_commands,
        "profiles": final,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)

    # ── 生成可部署的 shell 脚本 ──
    sh_path = os.path.join(base_dir, "..", "output", "analysis", "apply_tuning.sh")
    target_pkg = graph.get("meta", {}).get("target_package",
                                           "com.google.android.GoogleCamera")

    sh_lines = [
        "#!/system/bin/sh",
        "# ============================================================",
        f"#  Tuning Profile — generated {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"#  Target: {target_pkg}",
        "#",
        "#  部署方式:",
        "#    方式A (临时): adb push apply_tuning.sh /sdcard/ && adb shell sh /sdcard/apply_tuning.sh",
        "#    方式B (持久/Magisk): cp apply_tuning.sh /data/adb/service.d/",
        "#    方式C (持久/init): 将内容追加到 /system/etc/init/hw/init.rc",
        "#",
        "#  注意: 方式A 在进程重启后失效, 方式B/C 在每次启动时自动应用.",
        "#        TID 是动态的, 本脚本通过 /proc/PID/task/*/comm 按线程名查找.",
        "# ============================================================",
        "",
        'echo "[*] Camera Tuning Profile — $(date)"',
        "",
        f'PKG="{target_pkg}"',
        "",
        "# 等待目标应用启动 (最多等 60 秒)",
        "for i in $(seq 1 60); do",
        '    PID=$(pidof $PKG 2>/dev/null | awk \'{print $1}\')',
        '    if [ -n "$PID" ]; then',
        '        echo "[*] $PKG found, PID=$PID"',
        "        break",
        "    fi",
        "    sleep 1",
        "done",
        "",
        'if [ -z "$PID" ]; then',
        f'    echo "[!] {target_pkg} not running, abort."',
        "    exit 1",
        "fi",
        "",
        "# 辅助函数: 在给定 PID 下按线程名 (comm) 查找 TID",
        "find_tid() {",
        "    # $1 = comm (线程名)",
        '    for t in /proc/$PID/task/*/comm; do',
        '        [ -r "$t" ] || continue',
        '        if [ "$(cat "$t" 2>/dev/null)" = "$1" ]; then',
        '            basename $(dirname "$t")',
        "            return 0",
        "        fi",
        "    done",
        '    echo "[!] Thread $1 not found in PID=$PID" >&2',
        "    return 1",
        "}",
        "",
        'echo "[*] Applying scheduling tunings..."',
        "",
    ]

    # 每条推荐转换为 shell 注释 + 实际命令
    for p in final:
        sh_lines.append(f"# --- TID:{p['tid']} {p['comm']} ({p['role']}) "
                        f"P95={p['p95_delay_ms']}ms conf={p['confidence']:.0%} ---")
        for r in p["recommendations"]:
            sh_lines.append(f"# {r['type']}: {r['rationale']}")
            for cmd in r.get("apply_commands", []):
                if cmd.strip():
                    sh_lines.append(cmd + " 2>/dev/null || true")

    sh_lines += [
        "",
        'echo "[✓] Tuning applied to $PKG (PID=$PID)"',
    ]

    with open(sh_path, 'w') as f:
        f.write("\n".join(sh_lines) + "\n")

    # ── 终端摘要 ──
    print(f"{'='*72}")
    print(f"  Tuning Profile — {len(final)} thread(s)")
    print(f"{'='*72}")
    print(f"  {'TID':<8} {'Role':<18} {'Comm':<22} {'Conf':>6} {'P95':>8}  Recommendation")
    print(f"  {'-'*72}")
    for p in final:
        for r in p["recommendations"]:
            print(f"  TID:{p['tid']:<4} {p['role']:<18} {p['comm']:<22} "
                  f"{p['confidence']:.0%}  {p['p95_delay_ms']:>5.1f}ms  "
                  f"[{r['type']}] {r['rationale']}")
    print(f"{'='*72}")
    print(f"\n[✓] JSON profile → {output_path}")
    print(f"[✓] Shell script → {sh_path}")
    print(f"\n  部署方式:")
    print(f"    临时 (进程重启失效):")
    print(f"      adb push {sh_path} /sdcard/ && adb shell sh /sdcard/apply_tuning.sh")
    print(f"    持久 (Magisk, 每次启动自动):")
    print(f"      adb shell su -c 'cp /sdcard/apply_tuning.sh /data/adb/service.d/'")
    print(f"    持久 (init.rc, 需修改 boot.img):")
    print(f"      将脚本内容合并到 /system/etc/init/hw/init.rc 的 on boot 段")


if __name__ == "__main__":
    main()
