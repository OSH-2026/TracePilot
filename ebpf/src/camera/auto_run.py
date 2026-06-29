#!/usr/bin/env python3
"""
auto_run.py — 一键采集 + 分析自动化脚本
============================================
全流程: 编译 → 部署 → 采集 → 拉取 → 分析 → 报告

用法:
  python auto_run.py                          # 全自动流程
  python auto_run.py --package com.google.android.GoogleCamera  # 指定包名
  python auto_run.py --skip-build             # 跳过编译
  python auto_run.py --skip-pull              # 跳过拉取(使用已有数据)
  python auto_run.py --only-analyze           # 仅分析(使用已有数据)

注意: Windows 上请用 python, 不要用 python3 (那是 Microsoft Store 桩)
"""

import subprocess
import sys
import os
import time
import json
import argparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
EBPF_DIR     = os.path.join(PROJECT_ROOT, "ebpf")
PERFETTO_DIR = os.path.join(PROJECT_ROOT, "perfetto")
BUILD_DIR    = os.path.join(EBPF_DIR, "build")
PERFETTO_CFG = os.path.join(PERFETTO_DIR, "perfetto_camera.pbtx")

# ─── 环境自动检测: WSL 内 vs Windows 外 ───
def _detect_env():
    """检测运行环境, 返回 (in_wsl, wsl_root)"""
    # 检查是否在 WSL 内部 (Linux 内核 + /proc/version 包含 microsoft)
    if sys.platform == 'linux' or sys.platform.startswith('linux'):
        try:
            with open('/proc/version', 'r') as f:
                ver = f.read().lower()
                if 'microsoft' in ver or 'wsl' in ver:
                    return True, PROJECT_ROOT
        except Exception:
            pass
        return True, PROJECT_ROOT

    # Windows: 从 UNC 路径推导 WSL 路径
    # \\wsl.localhost\Ubuntu\home\yy\OSH-labs\TracePilot\ebpf\src\camera
    # → /home/yy/OSH-labs/TracePilot/ebpf/src/camera
    if PROJECT_ROOT.startswith('\\\\wsl.localhost\\'):
        parts = PROJECT_ROOT.replace('\\\\wsl.localhost\\', '').split('\\', 1)
        if len(parts) >= 2:
            return False, '/' + parts[1].replace('\\', '/')
    elif PROJECT_ROOT.startswith('\\\\wsl$\\'):
        parts = PROJECT_ROOT.replace('\\\\wsl$\\', '').split('\\', 1)
        if len(parts) >= 2:
            return False, '/' + parts[1].replace('\\', '/')

    return False, None

IN_WSL, WSL_ROOT = _detect_env()

# WSL 内各子目录路径
if IN_WSL:
    WSL_EBPF     = os.path.join(PROJECT_ROOT, "ebpf")
    WSL_SCRIPTS  = os.path.join(PROJECT_ROOT, "scripts")
    WSL_PERFETTO = os.path.join(PROJECT_ROOT, "perfetto")
    WSL_OUTPUT   = os.path.join(PROJECT_ROOT, "output")
else:
    WSL_EBPF     = f"{WSL_ROOT}/ebpf"
    WSL_SCRIPTS  = f"{WSL_ROOT}/scripts"
    WSL_PERFETTO = f"{WSL_ROOT}/perfetto"
    WSL_OUTPUT   = f"{WSL_ROOT}/output"

WSL_RAW      = f"{WSL_OUTPUT}/raw"
WSL_ANALYSIS = f"{WSL_OUTPUT}/analysis"

# 输出目录 (所有生成产物放在这里, 不与源码混杂)
OUTPUT_DIR     = os.path.join(PROJECT_ROOT, "output")
RAW_DIR        = os.path.join(OUTPUT_DIR, "raw")
ANALYSIS_DIR   = os.path.join(OUTPUT_DIR, "analysis")
REPORT_DIR     = os.path.join(OUTPUT_DIR, "reports")

# ─── 工具函数 ───

def run(cmd, cwd=None, check=True, capture=False, shell=False):
    """执行命令并打印"""
    desc = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    print(f"\n  $ {desc}")
    sys.stdout.flush()
    try:
        result = subprocess.run(
            [str(a) for a in cmd] if isinstance(cmd, (list, tuple)) else cmd,
            cwd=cwd, check=check, capture_output=capture,
            text=True, shell=shell, timeout=600,
            encoding='utf-8', errors='replace'
        )
        if capture:
            return result.stdout.strip()
        return result
    except subprocess.CalledProcessError as e:
        print(f"  [✗] Failed: {e}")
        if capture:
            print(f"  stderr: {e.stderr}")
        if check:
            sys.exit(1)
        return None
    except FileNotFoundError as e:
        print(f"  [✗] Command not found: {e}")
        if check:
            sys.exit(1)
        return None


def adb(var, *args, **kwargs):
    """adb 命令快捷方式 (var=子命令, 如 push/shell/pull)"""
    cmd = ["adb"] + [var] + list(args)
    return run(cmd, **kwargs)


def wsl(cmd_str, **kwargs):
    """在 WSL 中执行命令。若已在 WSL 内, 直接本地执行。
    失败时自动打印 stderr 以帮助诊断。"""
    if IN_WSL:
        cmd = ["bash", "-c", cmd_str]
    else:
        cmd = ["wsl", "-d", "Ubuntu", "--", "bash", "-c", cmd_str]

    desc = " ".join(str(c) for c in cmd)
    print(f"\n  $ {desc}")
    sys.stdout.flush()

    try:
        result = subprocess.run(
            cmd, check=False, capture_output=True,
            text=True, timeout=600,
            encoding='utf-8', errors='replace'
        )
    except FileNotFoundError as e:
        print(f"  [✗] Command not found: {e}")
        sys.exit(1)

    if result.returncode != 0:
        print(f"  [✗] Exit code: {result.returncode}")
        if result.stdout:
            print(f"  stdout:\n{result.stdout}")
        if result.stderr:
            print(f"  stderr:\n{result.stderr}")
        # 如果调用方允许失败, 不退出
        if kwargs.get('check', True):
            sys.exit(1)
    elif result.stdout:
        print(result.stdout)
    return result


def step(title):
    print(f"\n{'='*60}")
    print(f"  [{title}]")
    print(f"{'='*60}")


# ─── 主流程 ───

def main():
    parser = argparse.ArgumentParser(description="一键采集 + 分析")
    parser.add_argument("--package", default="com.google.android.GoogleCamera",
                        help="目标 App 包名 (默认: com.google.android.GoogleCamera)")
    parser.add_argument("--skip-build", action="store_true", help="跳过编译")
    parser.add_argument("--skip-pull", action="store_true", help="跳过拉取数据")
    parser.add_argument("--only-analyze", action="store_true", help="仅分析(使用已有数据)")
    parser.add_argument("--duration", type=int, default=30,
                        help="采集持续时间(秒, 默认30)")
    args = parser.parse_args()

    pkg = args.package

    print(f"  TracePilot Auto Runner")
    print(f"  Target: {pkg}")
    print(f"  Time:   ~{args.duration}s")

    # ────────────────────────────────────────────
    # Stage 0: 环境检查
    # ────────────────────────────────────────────
    step("环境检查")

    # 检查 adb: 直接运行 adb version 看是否在 PATH 中
    adb_ok = run(["adb", "version"], check=False, capture=True)
    if not adb_ok:
        print("  [✗] adb not found in PATH. Install Android platform-tools.")
        sys.exit(1)
    print(f"  [✓] adb found")

    # 检查 python
    py_ok = run(["python3", "--version"], check=False, capture=True) or \
            run(["python", "--version"], check=False, capture=True)
    if not py_ok:
        print("  [✗] python not found in PATH (tried 'python3' and 'python').")
        sys.exit(1)
    print(f"  [✓] Python found")

    # 检查必要文件
    if not os.path.exists(PERFETTO_CFG) and not args.only_analyze:
        print(f"  [✗] Perfetto config not found: {PERFETTO_CFG}")
        sys.exit(1)

    # ────────────────────────────────────────────
    # Stage 1: 编译
    # ────────────────────────────────────────────
    if not args.skip_build and not args.only_analyze:
        step("编译 eBPF 程序")
        wsl(f"cd {WSL_EBPF} && make clean && make")
        print("  [✓] Build complete")
    else:
        print("  [-] Skipping build")

    # ─── 采集阶段用到的变量 ───
    perf_pid = "0"
    ebpf_proc = None
    trace_out = "/data/misc/perfetto-traces/camera_jank.perfetto"

    # ────────────────────────────────────────────
    # Stage 2: 部署到设备
    # ────────────────────────────────────────────
    if not args.only_analyze:
        step("部署到设备")

        # ① 清理设备上的旧文件
        print("  [*] Cleaning old files on device...")
        adb("shell", "su", "-c", "killall -9 camera_ebpf_android 2>/dev/null", check=False)
        adb("shell", "rm", "-f", "/data/local/tmp/camera_ebpf_android", check=False)
        adb("shell", "rm", "-f", "/data/local/tmp/sched_events.csv", check=False)
        adb("shell", "rm", "-f", "/data/local/tmp/binder_futex_events.csv", check=False)

        # ② 从 WSL push 新二进制 (WSL 路径可靠, 避免 UNC 路径问题)
        wsl_bin = f"{WSL_EBPF}/build/camera_ebpf_android"
        win_bin = os.path.join(BUILD_DIR, "camera_ebpf_android")
        print(f"  [*] Pushing new binary...")
        # 用 Windows adb + UNC 路径 (不要用 wsl 内的 adb, 它看不见设备)
        if os.path.exists(win_bin):
            ret = adb("push", win_bin, "/data/local/tmp/", check=False)
        else:
            print(f"  [*] (Windows path not found, trying WSL path via wsl adb)")
            ret = wsl(f"adb push {wsl_bin} /data/local/tmp/", check=False)
        if not ret or ret.returncode != 0:
            print("  [✗] adb push failed")
            sys.exit(1)

        # ③ 验证部署: 确认文件大小 > 1MB (新二进制约 2MB)
        adb("shell", "chmod", "+x", "/data/local/tmp/camera_ebpf_android")
        size_check = adb("shell", "ls", "-l", "/data/local/tmp/camera_ebpf_android",
                         check=False, capture=True)
        if size_check:
            print(f"  [✓] Deployed: {size_check.strip()}")
        else:
            print("  [⚠] Binary not confirmed on device, continuing...")

        # ────────────────────────────────────────────
        # Stage 3: 启动采集
        # ────────────────────────────────────────────
        step("启动采集")

        # 3a. 推送 Perfetto 配置 + 启动 Perfetto (先启动, 用文件缓冲)
        print("  [*] Pushing Perfetto config...")
        adb("push", PERFETTO_CFG, "/data/local/tmp/")

        trace_out = "/data/misc/perfetto-traces/camera_jank.perfetto"
        print("  [*] Starting Perfetto trace (background)...")
        adb("shell",
            f"cat /data/local/tmp/perfetto_camera.pbtx | "
            f"perfetto --txt -c - -o {trace_out} -d 2>&1")
        time.sleep(1)

        # 记下 Perfetto PID (用于停止)
        perf_pid = adb("shell", f"cat /data/misc/perfetto-traces/camera_jank.perfetto.pid 2>/dev/null || "
                       f"pidof traced 2>/dev/null || "
                       f"echo 0", check=False, capture=True)
        if perf_pid and perf_pid != "0":
            print(f"  [*] Perfetto PID: {perf_pid}")

        # 3b. 再启动 eBPF (后启动, RingBuffer 干净的)
        print("  [*] Starting eBPF collector...")

        # 获取目标 UID → 内核侧 futex 过滤, 减少 90% 系统调用事件
        # 用 su -c 因为 /data/data/ 需要 root 权限
        app_uid = adb("shell", "su", "-c", f"stat -c %u /data/data/{pkg}",
                      check=False, capture=True)
        uid_filter = ""
        if app_uid and app_uid.strip().isdigit():
            uid_val = app_uid.strip()
            uid_filter = f"-u {uid_val}"
            print(f"  [*] UID filter: {uid_val} ({pkg})")
        else:
            print("  [⚠] Could not determine UID, collecting all futex events")

        # 手动模式: adb shell → stdin 发送 su → cd → ./binary
        ebpf_proc = subprocess.Popen(
            ["adb", "shell"],
            stdin=subprocess.PIPE,
        )
        ebpf_proc.stdin.write(b"su\n")
        ebpf_proc.stdin.write(b"cd /data/local/tmp\n")
        ebpf_proc.stdin.write(f"./camera_ebpf_android -q {uid_filter}\n".encode())
        ebpf_proc.stdin.flush()
        time.sleep(3)

        # 验证进程
        pid_check = adb("shell", "su", "-c", "pidof camera_ebpf_android",
                        check=False, capture=True)
        if pid_check:
            print(f"  [✓] eBPF PID: {pid_check}")
        else:
            print(f"  [⚠] eBPF may not be running (check kernel BPF support)")

        # 验证 binder 文件
        has_binder = adb("shell", "ls", "-l", "/data/local/tmp/binder_futex_events.csv",
                        check=False, capture=True)
        if has_binder:
            print(f"  [✓] binder_futex support confirmed (new binary)")
        else:
            print(f"  [⚠] binder_futex.csv not created yet, continuing...")

        print(f"\n  {'─'*50}")
        print(f"  采集已启动! 请在设备上操作 App 模拟卡顿场景")
        print(f"  操作完成后等待自动停止 (超时 {args.duration}s)")
        print(f"  {'─'*50}")

        # 带超时的等待
        deadline = time.time() + args.duration
        stopped_early = False
        try:
            while time.time() < deadline:
                print(f"  \r  剩余 {int(deadline - time.time())}s  ... Ctrl+C 提前停止",
                      end="", flush=True)
                time.sleep(1)
            print("\n  [*] Timeout reached, stopping...")
        except KeyboardInterrupt:
            print("\n  [*] Interrupted, stopping...")
            stopped_early = True

        # ────────────────────────────────────────────
        # Stage 4: 停止采集
        # ────────────────────────────────────────────
        step("停止采集")

        # 4a. 停止 Perfetto (用 PID kill, --stop 需要 --attach 才能用)
        print("  [*] Stopping Perfetto...")
        adb("shell", f"kill -TERM {perf_pid} 2>/dev/null", check=False)
        adb("shell", "kill -TERM $(pidof perfetto 2>/dev/null) 2>/dev/null", check=False)
        time.sleep(2)

        # 4b. 停止 eBPF: 关闭 stdin → shell 退出 → SIGHUP 杀子进程
        print("  [*] Stopping eBPF...")
        if ebpf_proc and ebpf_proc.poll() is None:
            try:
                ebpf_proc.stdin.close()
            except Exception:
                pass
            time.sleep(0.5)
            ebpf_proc.kill()
            ebpf_proc.wait(timeout=3)
        # 远程二次确认
        adb("shell", "su", "-c", "killall -9 camera_ebpf_android 2>/dev/null",
            check=False)

        # ────────────────────────────────────────────
        # Stage 5: 拉取数据
        # ────────────────────────────────────────────
        if not args.skip_pull:
            step("拉取数据")
            adb("pull", trace_out, f"{PERFETTO_DIR}/", check=False)

            # 直接从 /data/local/tmp/ pull → output/raw/
            os.makedirs(RAW_DIR, exist_ok=True)
            sched_ok = adb("pull", "/data/local/tmp/sched_events.csv",
                          f"{RAW_DIR}/", check=False, capture=True)
            binder_ok = adb("pull", "/data/local/tmp/binder_futex_events.csv",
                           f"{RAW_DIR}/", check=False, capture=True)
            irq_ok = adb("pull", "/data/local/tmp/irq_events.csv",
                        f"{RAW_DIR}/", check=False, capture=True)

            # 验证文件
            sched_file = os.path.join(RAW_DIR, "sched_events.csv")
            binder_file = os.path.join(RAW_DIR, "binder_futex_events.csv")
            if os.path.exists(sched_file):
                lines = sum(1 for _ in open(sched_file, encoding='utf-8', errors='replace'))
                print(f"  [✓] sched_events.csv: {lines} lines")
            if os.path.exists(binder_file):
                lines = sum(1 for _ in open(binder_file, encoding='utf-8', errors='replace'))
                print(f"  [✓] binder_futex_events.csv: {lines} lines (新二进制确认!)")
            else:
                print(f"  [⚠] binder_futex_events.csv missing → 设备上仍是旧版二进制")
        else:
            print("  [-] Skipping pull")

    else:
        print("  [-] Skipping deploy + collect (--only-analyze)")

    # ────────────────────────────────────────────
    # Stage 6: 分析
    # ────────────────────────────────────────────
    step("分析流程")

    # 安装 perfetto Python 依赖 (WSL)
    print("  [*] Checking perfetto Python package in WSL...")
    has_perfetto = wsl("python3 -c 'from perfetto.trace_processor import TraceProcessor; print(1)'",
                       check=False)
    if has_perfetto.returncode != 0:
        print("  [*] perfetto not found, attempting to install...")
        # 先看 pip 是否可用
        has_pip = wsl("python3 -m pip --version 2>/dev/null", check=False)
        if has_pip.returncode != 0:
            print("  [⚠] pip3 not installed in WSL. Installing python3-pip...")
            wsl("sudo apt-get install -y -qq python3-pip 2>/dev/null || "
                "apt-get install -y -qq python3-pip 2>/dev/null", check=False)
        wsl("python3 -m pip install perfetto pandas numpy -q --break-system-packages 2>&1", check=False)
        # 最终验证
        final_check = wsl("python3 -c 'from perfetto.trace_processor import TraceProcessor; print(1)'",
                          check=False)
        if final_check.returncode != 0:
            print("  [✗] Failed to install perfetto. Run manually:")
            print("      wsl -d Ubuntu sudo apt-get install -y python3-pip")
            print("      wsl -d Ubuntu python3 -m pip install perfetto")
            sys.exit(1)
    print("  [✓] perfetto package ready")

    # 6a. Perfetto → 窗口 JSON
    step("6a. Perfetto Trace → Jank Frame Windows")
    wsl(f"cd {WSL_PERFETTO} && python3 parse_trace.py camera_jank.perfetto {pkg}")

    # 6b. 多维分析 → Critical Path Graph
    step("6b. eBPF 多维分析 + Critical Path Graph")
    window_json = f"{WSL_ANALYSIS}/ebpf_target_windows.json"
    sched_csv   = f"{WSL_RAW}/sched_events.csv"
    binder_csv  = f"{WSL_RAW}/binder_futex_events.csv"
    irq_csv     = f"{WSL_RAW}/irq_events.csv"

    wsl(f"cd {WSL_SCRIPTS} && python3 analyze_delays.py "
        f"--json {window_json} --csv {sched_csv} --binder {binder_csv} --irq {irq_csv}")

    # 6c. 根因归因 + 调优配置 + 卡顿分类
    step("6c. Root Cause + Tuning + Jank Classification")
    wsl(f"cd {WSL_SCRIPTS} && python3 root_cause.py", check=False)
    wsl(f"cd {WSL_SCRIPTS} && python3 safe_hint_engine.py", check=False)
    wsl(f"cd {WSL_SCRIPTS} && python3 jank_classifier.py", check=False)

    # 6d. 图可视化导出
    step("6d. Graph Visualization Export")
    wsl(f"cd {WSL_SCRIPTS} && python3 graph_export.py", check=False)

    # 6e. 多会话对比 (有历史数据时)
    step("6e. Multi-Session Comparison")
    wsl(f"cd {WSL_SCRIPTS} && python3 session_compare.py", check=False)

    # ────────────────────────────────────────────
    # Stage 7: 输出摘要
    # ────────────────────────────────────────────
    step("分析完成")
    delay_json    = os.path.join(ANALYSIS_DIR, "delay_analysis_result.json")
    cp_graph      = os.path.join(ANALYSIS_DIR, "critical_path_graph.json")
    root_json     = os.path.join(ANALYSIS_DIR, "root_cause_analysis.json")
    tuning_json   = os.path.join(ANALYSIS_DIR, "tuning_profile.json")
    jank_json     = os.path.join(ANALYSIS_DIR, "jank_classification.json")
    compare_json  = os.path.join(ANALYSIS_DIR, "compare_report.json")
    dot_file      = os.path.join(ANALYSIS_DIR, "graph_topology.dot")

    print(f"  产物列表:")
    if os.path.exists(delay_json):
        with open(delay_json, encoding='utf-8') as f:
            d = json.load(f)
        print(f"    • delay_analysis_result.json: {len(d.get('frames',[]))} frames analyzed")
    if os.path.exists(cp_graph):
        with open(cp_graph, encoding='utf-8') as f:
            c = json.load(f)
        print(f"    • critical_path_graph.json: {len(c.get('global_critical_scores',[]))} threads scored")
        print(f"\n    Top-3 Critical Threads:")
        for i, s in enumerate(c.get("global_critical_scores", [])[:3], 1):
            print(f"      #{i} TID:{s['tid']} [{s['role']}] score={s['score']:.4f}")
    if os.path.exists(root_json):
        with open(root_json, encoding='utf-8') as f:
            rc = json.load(f)
        attribs = rc.get('attributions', [])
        if attribs:
            print(f"\n    • root_cause_analysis.json: {len(attribs)} threads attributed")
            for a in attribs[:3]:
                print(f"      {a.get('comm','?')}: {a.get('root_cause','?')}")

    if os.path.exists(tuning_json):
        with open(tuning_json, encoding='utf-8') as f:
            tp = json.load(f)
        actions = tp.get('actions', [])
        if actions:
            print(f"\n    • tuning_profile.json: {len(actions)} tuning actions generated")
            for a in actions[:3]:
                print(f"      [{a.get('type','?')}] {a.get('description','?')}")

    # 生成报告文档
    print("\n  [*] Generating markdown report...")
    wsl(f"cd {WSL_SCRIPTS} && python3 generate_report.py", check=False)

    print(f"\n  [✓] All done!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n  [✗] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
