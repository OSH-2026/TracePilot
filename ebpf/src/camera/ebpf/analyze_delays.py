import json
import csv
import os
import sys
from collections import defaultdict

# ─── 角色识别 (增强版) ───
def identify_role(comm, tid, target_pid):
    """基于线程名和 TID 推断线程角色"""
    cl = comm.lower()
    # 内核线程: CPU idle / worker — 标记为资源竞争而非关键线程
    if cl.startswith("swapper/") or cl.startswith("kworker") or cl == "kswapd0":
        return "KernelWorker"
    if tid == target_pid:
        return "UI Thread"
    if "renderthread" in cl or cl.startswith("rend"):
        return "RenderThread"
    if "binder" in cl:
        if "hw" in cl:
            return "HwBinder RPC"
        return "Binder RPC"
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


# ─── 命令行参数解析 ───
def parse_args():
    """简单参数：--json perfetto输出 --csv sched_csv [--binder binder_csv]"""
    args = {'json': None, 'csv': None, 'binder': None}
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '--json' and i+1 < len(sys.argv):
            args['json'] = sys.argv[i+1]; i += 2
        elif sys.argv[i] == '--csv' and i+1 < len(sys.argv):
            args['csv'] = sys.argv[i+1]; i += 2
        elif sys.argv[i] == '--binder' and i+1 < len(sys.argv):
            args['binder'] = sys.argv[i+1]; i += 2
        else:
            i += 1
    return args


def analyze_ebpf_delays():
    args = parse_args()
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # ─── 1. 读入 Perfetto Ground Truth ───
    json_path = args['json'] or os.path.join(base_dir, "..", "output", "analysis", "ebpf_target_windows.json")
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return

    with open(json_path, 'r') as f:
        target_info = json.load(f)

    target_pid = target_info['pid']
    target_uid = target_info['uid']
    threads_map = target_info.get('threads_map', {})
    jank_frames = target_info['jank_frames']

    print(f"[*] Target: {target_info['target_package']}  PID={target_pid}  UID={target_uid}")
    print(f"[*] ThreadKeys: {len(threads_map)}  Jank frames: {len(jank_frames)}")

    # ─── 2. 读入 sched CSV ───
    csv_path = args['csv'] or os.path.join(base_dir, "..", "output", "raw", "sched_events.csv")
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} not found.")
        return

    thread_info = {}
    pending_wakeups = defaultdict(list)
    frame_delays   = defaultdict(lambda: defaultdict(int))
    frame_runtimes = defaultdict(lambda: defaultdict(int))
    frame_delay_events = defaultdict(lambda: defaultdict(list))  # 每个 wakeup→switch 的延迟样本

    with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
        sched_events = list(csv.DictReader(f))

    print(f"[*] Loaded {len(sched_events)} sched events.")

    # ─── 3. 逐帧独立处理 sched 事件 (Runnable Delay) ───
    # 每帧有独立的 pending_wakeups 和 running_start,
    # 杜绝前一帧的 wakeup 穿越到后一帧匹配 switch 导致的虚假大延迟.
    for frame in jank_frames:
        ft = frame["frame_token"]
        ws = frame["window_start_ns"]
        we = frame["window_end_ns"]
        frame_dur = max(1, we - ws)

        pending_wakeups = defaultdict(list)
        running_start   = {}

        for row in sched_events:
            if not row.get('ts') or not row.get('tid'):
                continue
            ts = int(row['ts'])
            evt_type = row['event']
            tid = int(row['tid'])
            prev_tid = int(row['prev_tid']) if row.get('prev_tid') else 0
            uid = int(row.get('uid', 0))
            comm = row.get('comm', '')

            if not (ws <= ts <= we):
                continue

            if target_uid > 0 and uid != target_uid and uid != 0:
                continue

            if str(tid) in threads_map:
                thread_info[tid] = {'comm': threads_map[str(tid)]['name']}
            else:
                thread_info[tid] = {'comm': comm}

            if evt_type == 'switch' and str(prev_tid) in threads_map:
                thread_info[prev_tid] = {'comm': threads_map[str(prev_tid)]['name']}
            elif evt_type == 'switch' and prev_tid not in thread_info:
                thread_info[prev_tid] = {'comm': comm}

            if evt_type == "wakeup":
                pending_wakeups[tid].append(ts)
            elif evt_type == "switch":
                if prev_tid in running_start:
                    run_dur = ts - running_start[prev_tid]
                    if run_dur > 0:
                        frame_runtimes[ft][prev_tid] += run_dur
                    del running_start[prev_tid]

                running_start[tid] = ts
                if pending_wakeups[tid]:
                    w_ts = pending_wakeups[tid].pop(0)
                    rd = ts - w_ts
                    if 0 < rd <= frame_dur:
                        frame_delays[ft][tid] += rd
                        frame_delay_events[ft][tid].append(rd)  # 记录每个样本用于 P95

    print(f"[*] Processed sched events for {len(frame_delays)} frame windows.")

    # ─── 4. 读入 binder/futex CSV ───
    binder_path = args['binder'] or os.path.join(base_dir, "..", "output", "raw", "binder_futex_events.csv")
    binder_events = []
    if os.path.exists(binder_path):
        with open(binder_path, 'r', encoding='utf-8', errors='replace') as f:
            binder_events = list(csv.DictReader(f))
        print(f"[*] Loaded {len(binder_events)} binder/futex events.")
    else:
        print(f"[*] No binder_futex_events.csv found, skipping binder/futex analysis.")

    # binder 匹配: debug_id -> { 'tx_ts', 'tx_tid', 'tx_comm', 'tx_to_thread', 'tx_code' }
    pending_tx = {}
    # frame -> { debug_id -> { tx... rx... latency_ns } }
    frame_binder_calls = defaultdict(lambda: defaultdict(dict))
    # frame -> { tid -> futex_wait_count }
    frame_futex_waits = defaultdict(lambda: defaultdict(int))
    frame_futex_wakes = defaultdict(lambda: defaultdict(int))
    frame_cpu_freqs = {}     # frame_token -> {cpu_id: [freq_mhz, ...]}
    frame_thermal   = {}     # frame_token -> [(ts, zone, temp_c), ...]

    for row in binder_events:
        # 跳过损坏行
        try:
            ts = int(row['ts'])
            tid = int(row['tid'])
        except (ValueError, TypeError):
            continue

        evt = row['event']
        try:
            uid = int(row.get('uid', 0))
            debug_id = int(row.get('debug_id', 0))
            extra = int(row.get('extra', 0))
            prev_tid = int(row.get('prev_tid', 0))
            ret = int(row.get('ret', 0))
        except (ValueError, TypeError):
            continue
        comm = row.get('comm', '')

        # UID 过滤: binder 豁免(跨进程), futex/cpu_freq/thermal 按目标 UID 过滤
        # 若 target_uid==0 (未知), 不过滤
        if target_uid > 0 and uid != target_uid and uid != 0:
            if evt not in ('binder_transaction', 'binder_received'):
                continue

        if str(tid) in threads_map:
            thread_info[tid] = {'comm': threads_map[str(tid)]['name']}
        elif tid not in thread_info:
            thread_info[tid] = {'comm': comm}

        active_frame = None
        for frame in jank_frames:
            if frame["window_start_ns"] <= ts <= frame["window_end_ns"]:
                active_frame = frame["frame_token"]
                break
        if not active_frame:
            continue

        if evt == 'binder_transaction':
            pending_tx[debug_id] = {
                'ts': ts, 'tid': tid, 'comm': comm,
                'to_thread': prev_tid,
                'to_proc': (extra >> 16) & 0xFFFF,
                'code': extra & 0xFFFF,
                'is_reply': ret == 1
            }
        elif evt == 'binder_received':
            if debug_id in pending_tx:
                tx = pending_tx.pop(debug_id)
                latency = ts - tx['ts']
                if latency > 0:
                    key = debug_id
                    frame_binder_calls[active_frame][key] = {
                        'tx_ts': tx['ts'], 'rx_ts': ts,
                        'tx_tid': tx['tid'], 'rx_tid': tid,
                        'tx_comm': tx['comm'], 'rx_comm': comm,
                        'to_thread': tx['to_thread'],
                        'code': tx['code'],
                        'latency_ns': latency,
                        'is_reply': tx['is_reply']
                    }
        elif evt == 'futex':
            op = extra
            op_base = op & 0x7F
            if op_base == 0:  # FUTEX_WAIT
                frame_futex_waits[active_frame][tid] += 1
            elif op_base == 1:  # FUTEX_WAKE
                frame_futex_wakes[active_frame][tid] += 1

        elif evt == 'cpu_frequency':
            # extra = 频率 (MHz), tid = cpu_id
            freq_mhz = extra
            cpu_id = tid
            if active_frame not in frame_cpu_freqs:
                frame_cpu_freqs[active_frame] = defaultdict(list)
            frame_cpu_freqs[active_frame][cpu_id].append(freq_mhz)

        elif evt == 'thermal':
            # extra = 温度 (°C), comm = thermal_zone 名
            temp_c = extra
            zone = comm
            if active_frame not in frame_thermal:
                frame_thermal[active_frame] = []
            frame_thermal[active_frame].append((ts, zone, temp_c))

    # ─── 5. 综合分析 & 输出报告 ───
    print("\n" + "=" * 80)
    print("  Frame-Centric Scheduling + Dependency Analysis Report")
    print("=" * 80)

    analysis_result = {
        "target_package": target_info.get('target_package'),
        "pid": target_pid,
        "uid": target_uid,
        "frames": []
    }

    for frame_token, delays in frame_delays.items():
        print(f"\n{'─' * 60}")
        print(f"  Frame Token: {frame_token}")
        print(f"{'─' * 60}")

        frame_report = {
            "frame_token": frame_token,
            "threads": [],
            "binder_edges": [],
            "futex_activity": []
        }

        # ── 5a. Sched 延迟 ──
        print("\n  [Sched] Runnable Delays:")
        for tid, d_sum in sorted(delays.items(), key=lambda x: -x[1]):
            comm = thread_info.get(tid, {}).get('comm', '?')
            role = identify_role(comm, tid, target_pid)
            r_sum = frame_runtimes[frame_token].get(tid, 0)
            is_critical = d_sum > 2_000_000

            # 从各唤醒→switch样本计算该帧 P95
            events = frame_delay_events.get(frame_token, {}).get(tid, [])
            frame_p95 = 0
            if events:
                sorted_ev = sorted(events)
                frame_p95 = sorted_ev[int(len(sorted_ev) * 0.95)] if len(sorted_ev) >= 20 else max(sorted_ev)

            mark = " ⚡CRITICAL" if is_critical else ""
            print(f"    TID:{tid:<6} [{role:<16}] {comm:<20} "
                  f"TotalDelay={d_sum/1e6:.2f}ms  P95={frame_p95/1e6:.3f}ms  Run={r_sum/1e6:.2f}ms{mark}")

            frame_report["threads"].append({
                "tid": tid,
                "comm": comm,
                "role": role,
                "runnable_delay_ns": d_sum,
                "runnable_delay_p95_ns": frame_p95,
                "delay_events": events,          # 各次唤醒→switch的延迟样本 (用于全局 P95)
                "actual_run_ns": r_sum,
                "critical_for_hint": is_critical
            })

        # ── 5b. Binder 依赖边 ──
        binder_for_frame = frame_binder_calls.get(frame_token, {})
        if binder_for_frame:
            print("\n  [Binder] Dependency Edges (TX→RX latency):")
            for dbg_id, call in sorted(binder_for_frame.items(),
                                       key=lambda x: -x[1]['latency_ns']):
                lat_ms = call['latency_ns'] / 1e6
                reply_mark = " [REPLY]" if call['is_reply'] else ""
                to_thread_str = f"TID:{call['to_thread']}" if call['to_thread'] else "any"
                print(f"    debug_id={dbg_id:<6} "
                      f"{call['tx_comm']}(TID:{call['tx_tid']}) "
                      f"─[{lat_ms:.3f}ms]→ "
                      f"{call['rx_comm']}(TID:{call['rx_tid']})"
                      f"  to_thread={to_thread_str} code=0x{call['code']:x}{reply_mark}")

                frame_report["binder_edges"].append({
                    "debug_id": dbg_id,
                    "tx_tid": call['tx_tid'],
                    "rx_tid": call['rx_tid'],
                    "tx_comm": call['tx_comm'],
                    "rx_comm": call['rx_comm'],
                    "latency_ns": call['latency_ns'],
                    "code": call['code'],
                    "is_reply": call['is_reply']
                })

        # ── 5c. Futex 活动 ──
        futex_w = frame_futex_waits.get(frame_token, {})
        futex_k = frame_futex_wakes.get(frame_token, {})
        if futex_w or futex_k:
            print("\n  [Futex] Activity:")
            all_futex_tids = set(futex_w.keys()) | set(futex_k.keys())
            for tid in sorted(all_futex_tids):
                comm = thread_info.get(tid, {}).get('comm', '?')
                role = identify_role(comm, tid, target_pid)
                wc = futex_w.get(tid, 0)
                kc = futex_k.get(tid, 0)
                print(f"    TID:{tid:<6} [{role:<16}] {comm:<20} "
                      f"FUTEX_WAIT={wc}  FUTEX_WAKE={kc}")

                frame_report["futex_activity"].append({
                    "tid": tid,
                    "comm": comm,
                    "role": role,
                    "futex_wait_count": wc,
                    "futex_wake_count": kc
                })

        # ── 5d. CPU 频率 ──
        freq_data = frame_cpu_freqs.get(frame_token, {})
        if freq_data:
            all_freqs = [f for flist in freq_data.values() for f in flist]
            if all_freqs:
                print(f"\n  [CPU Freq] {len(all_freqs)} changes, "
                      f"min={min(all_freqs)}MHz max={max(all_freqs)}MHz "
                      f"avg={sum(all_freqs)//len(all_freqs)}MHz")
                frame_report["cpu_freq"] = {
                    "min_mhz": min(all_freqs),
                    "max_mhz": max(all_freqs),
                    "avg_mhz": sum(all_freqs) // len(all_freqs),
                    "per_cpu": {str(c): {"min": min(v), "max": max(v)}
                                for c, v in freq_data.items()},
                }

        # ── 5e. Thermal ──
        thermals = frame_thermal.get(frame_token, [])
        if thermals:
            temps = [t[2] for t in thermals]
            zones = set(t[1] for t in thermals)
            print(f"  [Thermal] {len(thermals)} readings, temp={min(temps)}~{max(temps)}°C, "
                  f"zones={','.join(sorted(zones)[:3])}")
            frame_report["thermal"] = {
                "min_c": min(temps),
                "max_c": max(temps),
                "zones": sorted(zones),
            }

        analysis_result["frames"].append(frame_report)

    # ─── 6. 导出 JSON ───
    out_json_path = os.path.join(base_dir, "..", "output", "analysis", "delay_analysis_result.json")
    with open(out_json_path, "w") as f:
        json.dump(analysis_result, f, indent=4)
    print(f"\n[*] Full analysis exported to: {out_json_path}")

    # ─── 7. 自动构建 Critical Path Graph ───
    try:
        from critical_path import CriticalPathBuilder
        window_json = args['json'] or os.path.join(base_dir, "..", "output", "analysis", "ebpf_target_windows.json")
        if os.path.exists(window_json):
            print("\n" + "=" * 60)
            print("  Building Critical Path Graph & Computing CriticalScores...")
            print("=" * 60)
            builder = CriticalPathBuilder(out_json_path, window_json)
            builder.build()
            graph_out = os.path.join(base_dir, "..", "output", "analysis", "critical_path_graph.json")
            builder.export(graph_out)
    except ImportError:
        print("\n[*] critical_path.py not found, skipping Critical Path Graph build.")
    except Exception as exc:
        print(f"\n[!] Critical Path Graph build failed: {exc}")


if __name__ == "__main__":
    analyze_ebpf_delays()
