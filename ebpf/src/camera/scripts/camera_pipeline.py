#!/usr/bin/env python3
"""
camera_pipeline.py — 相机管线阶段分析器
==========================================
基于 Perfetto atrace 提取的相机管线阶段 (CameraOpen/CaptureRequest/JpegEncode/Preview 等)，
将 eBPF 采集的调度延迟、Binder IPC、Futex 活动按管线阶段聚合分析。

输入: ebpf_target_windows.json (含 camera_pipeline 字段)
      sched_events.csv + binder_futex_events.csv
输出: camera_pipeline_result.json

用法:
  python camera_pipeline.py
  python camera_pipeline.py --json output/analysis/ebpf_target_windows.json \\
                            --csv output/raw/sched_events.csv \\
                            --binder output/raw/binder_futex_events.csv
"""

import json
import csv
import os
import sys
from collections import defaultdict


# ═══════════════════════════════════════════════════════════
#  角色识别 (扩展版: 增加相机特有线程)
# ═══════════════════════════════════════════════════════════

def identify_role(comm, tid, target_pid):
    """基于线程名推断线程角色，包含相机特有线程"""
    cl = (comm or "").lower()

    # 内核线程
    if cl.startswith("swapper/") or cl.startswith("kworker") or cl == "kswapd0":
        return "KernelWorker"

    if tid == target_pid:
        return "UI Thread"

    # ── 相机 / HAL 层特有线程 ──
    if "camera" in cl:
        if "provider" in cl or "service" in cl:
            return "CameraService"
        if "hal" in cl:
            return "CameraHal"
        return "CameraThread"
    if cl.startswith("lwis_"):
        return "CameraHal"          # Pixel 相机 HAL 的 I2C/总线线程
    if "jpeg" in cl or "heic" in cl or "encoder" in cl:
        return "JpegEncoder"
    if "media" in cl and ("codec" in cl or "decode" in cl or "encode" in cl):
        return "MediaCodec"
    if "mm-qcamera" in cl or "qCamera" in cl:
        return "CameraHal"
    if "cameraserver" in cl:
        return "CameraService"
    if "isp" in cl or "csi" in cl:
        return "CameraHal"
    if cl.startswith("android.hardwar") or "hardware.camera" in cl:
        return "CameraHal"
    if "gca" in cl and "generic" in cl:
        return "CameraThread"
    if cl.startswith("gcam") or "gcam" in cl:
        return "CameraThread"
    if "smz-" in cl or cl.startswith("smz_"):
        return "CameraThread"
    if cl.startswith("catcher-"):
        return "CameraThread"
    if cl.startswith("frame-quality") or cl.startswith("frame-store"):
        return "CameraThread"
    if cl.startswith("meta-store") or cl.startswith("trk-"):
        return "CameraThread"
    if cl.startswith("mv-") and ("gyro" in cl or "ctrl" in cl or "vid" in cl):
        return "CameraThread"
    if cl.startswith("ois-"):
        return "CameraThread"
    if cl.startswith("pck-"):
        return "CameraThread"
    if cl == "sabre" or cl.startswith("sabre"):
        return "CameraThread"
    if cl.startswith("cvk-"):
        return "CameraThread"
    if cl.startswith("c2node"):
        return "CameraThread"
    if cl.startswith("raw") and ("w" in cl):
        return "CameraThread"
    if cl.startswith("yuv") and ("w" in cl):
        return "CameraThread"
    if cl.startswith("private") and ("w" in cl):
        return "CameraThread"
    if "mali" in cl:
        return "GPU Worker"
    if cl.startswith("glide-"):
        return "GPU Worker"
    if cl.startswith("dhd_") or cl.startswith("bcm"):
        return "I/O Worker"
    if cl.startswith("irq/") or cl.startswith("thermal_"):
        return "KernelWorker"

    # ── 渲染 / 图形 ──
    if "renderthread" in cl or cl.startswith("rend"):
        return "RenderThread"
    if "surfaceflinger" in cl:
        return "SurfaceFlinger"
    if "hwc" in cl or "composer" in cl:
        return "HwComposer"
    if "gpu" in cl or "gl" in cl:
        return "GPU Worker"

    # ── IPC ──
    if "binder" in cl:
        return "HwBinder RPC" if "hw" in cl else "Binder RPC"

    # ── 系统服务 ──
    if "system_server" in cl or "systemui" in cl:
        return "SystemService"
    if "servicemanager" in cl:
        return "SystemService"

    # ── I/O / 存储 ──
    if "io" in cl or "disk" in cl or "mmcqd" in cl:
        return "I/O Worker"
    if "dm-" in cl or "loop" in cl:
        return "I/O Worker"

    # ── 应用层 ──
    if cl.startswith("com.") or cl.startswith("android."):
        return "UI Thread"

    return "UnknownWorker"


# ═══════════════════════════════════════════════════════════
#  管线阶段分析核心
# ═══════════════════════════════════════════════════════════

def analyze_camera_pipeline():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # ── 参数解析 ──
    args = {'json': None, 'csv': None, 'binder': None, 'irq': None}
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '--json' and i + 1 < len(sys.argv):
            args['json'] = sys.argv[i + 1]; i += 2
        elif sys.argv[i] == '--csv' and i + 1 < len(sys.argv):
            args['csv'] = sys.argv[i + 1]; i += 2
        elif sys.argv[i] == '--binder' and i + 1 < len(sys.argv):
            args['binder'] = sys.argv[i + 1]; i += 2
        elif sys.argv[i] == '--irq' and i + 1 < len(sys.argv):
            args['irq'] = sys.argv[i + 1]; i += 2
        else:
            i += 1

    # ── 1. 读入 ebpf_target_windows.json ──
    json_path = args['json'] or os.path.join(base_dir, "..", "output", "analysis",
                                              "ebpf_target_windows.json")
    if not os.path.exists(json_path):
        print(f"[✗] {json_path} not found. Run parse_trace.py first.")
        sys.exit(1)

    with open(json_path, 'r') as f:
        target_info = json.load(f)

    target_pid = target_info.get('pid', 0)
    target_uid = target_info.get('uid', 0)
    threads_map = target_info.get('threads_map', {})
    camera_stages = target_info.get('camera_pipeline', [])
    jank_frames   = target_info.get('jank_frames', [])

    # ── 回退: 若无 camera atrace 数据, 用 jank 帧窗口作为分析阶段 ──
    if not camera_stages:
        if jank_frames:
            print(f"[!] No camera atrace slices found (Google Camera uses private stack).")
            print(f"[*] Falling back to jank frame windows ({len(jank_frames)} frames) as analysis stages.")
            camera_stages = [
                {
                    "name": f"JankFrame_{f.get('frame_token','?')}",
                    "category": "JankFrame",
                    "start_ns": f["window_start_ns"],
                    "end_ns": f["window_end_ns"],
                    "duration_ns": f.get("actual_duration_ns", f["window_end_ns"] - f["window_start_ns"]),
                    "tid": 0,
                    "process_name": target_info.get('target_package', ''),
                    "thread_name": "",
                }
                for f in jank_frames
            ]
        else:
            print("[✗] No camera pipeline stages or jank frames found.")
            print("    Google Camera (Pixel) does not emit standard camera atrace.")
            print("    Consider using a different Perfetto config or AOSP camera app.")
            sys.exit(0)

    print(f"[*] Target: {target_info.get('target_package')}  PID={target_pid}  UID={target_uid}")
    print(f"[*] Camera pipeline stages: {len(camera_stages)}")
    categories = set(s['category'] for s in camera_stages)
    print(f"[*] Stage categories: {', '.join(sorted(categories))}")

    # ── 2. 读入 sched CSV ──
    csv_path = args['csv'] or os.path.join(base_dir, "..", "output", "raw", "sched_events.csv")
    sched_events = []
    if os.path.exists(csv_path):
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            sched_events = list(csv.DictReader(f))
    print(f"[*] Loaded {len(sched_events)} sched events.")

    # ── 3. 读入 binder/futex CSV ──
    binder_path = args['binder'] or os.path.join(base_dir, "..", "output", "raw",
                                                  "binder_futex_events.csv")
    binder_events = []
    if os.path.exists(binder_path):
        with open(binder_path, 'r', encoding='utf-8', errors='replace') as f:
            binder_events = list(csv.DictReader(f))
    print(f"[*] Loaded {len(binder_events)} binder/futex events.")

    # ── 读入 irq CSV ──
    irq_path = args['irq'] or os.path.join(base_dir, "..", "output", "raw", "irq_events.csv")
    irq_events = []
    if os.path.exists(irq_path):
        with open(irq_path, 'r', encoding='utf-8', errors='replace') as f:
            irq_events = list(csv.DictReader(f))
    print(f"[*] Loaded {len(irq_events)} irq/softirq events.")

    # ── 4. 线程信息字典 ──
    thread_info = {}
    for tid_str, info in threads_map.items():
        thread_info[int(tid_str)] = {'comm': info['name']}

    # ── 5. 按管线阶段聚合 ──
    # 按类别汇总
    category_stats = defaultdict(lambda: {
        "stage_count": 0,
        "total_duration_ns": 0,
        "threads": defaultdict(lambda: {
            "runnable_delay_ns": 0,
            "actual_run_ns": 0,
            "delay_events": [],
            "comm": "",
            "role": "",
        }),
        "binder_calls": [],
        "futex_waits": defaultdict(int),
        "futex_wakes": defaultdict(int),
        "cpu_freqs": defaultdict(list),
        "thermals": [],
        "stages": [],
    })

    # 预处理 sched: 按时间窗口索引
    # 为每个 stage 找到对应的 sched 事件
    for stage in camera_stages:
        cat = stage['category']
        ws = stage['start_ns']
        we = stage['end_ns']
        frame_dur = max(1, we - ws)
        category_stats[cat]['stage_count'] += 1
        category_stats[cat]['total_duration_ns'] += stage['duration_ns']
        category_stats[cat]['stages'].append(stage)

        # ── 5a. Sched 延迟 (BPF 内核内计算, 直接读取) ──
        running_start = {}

        for row in sched_events:
            if not row.get('ts') or not row.get('tid'):
                continue
            try:
                ts = int(row['ts'])
            except (ValueError, TypeError):
                continue
            if not (ws <= ts <= we):
                continue

            evt_type = row.get('event', '')
            if evt_type != 'switch':
                continue  # wakeup 不再出现在 CSV 中
            try:
                tid = int(row['tid'])
                prev_tid = int(row['prev_tid']) if row.get('prev_tid') else 0
                uid = int(row.get('uid', 0))
                rd = int(row.get('runnable_delay_ns', 0) or 0)
            except (ValueError, TypeError):
                continue
            comm = row.get('comm', '')

            # UID 过滤
            if target_uid > 0 and uid != target_uid and uid != 0:
                continue

            # 登记线程信息
            if tid not in thread_info:
                thread_info[tid] = {'comm': comm}
            if prev_tid and prev_tid not in thread_info:
                thread_info[prev_tid] = {'comm': comm}

            t = category_stats[cat]['threads']
            if prev_tid in running_start:
                run_dur = ts - running_start[prev_tid]
                if run_dur > 0:
                    t[prev_tid]['actual_run_ns'] += run_dur
                del running_start[prev_tid]

            running_start[tid] = ts
            if 0 < rd <= frame_dur:
                t[tid]['runnable_delay_ns'] += rd
                t[tid]['delay_events'].append(rd)

        # 标记线程角色
        for tid in category_stats[cat]['threads']:
            comm = thread_info.get(tid, {}).get('comm', '?')
            category_stats[cat]['threads'][tid]['comm'] = comm
            category_stats[cat]['threads'][tid]['role'] = identify_role(comm, tid, target_pid)

        # ── 5b. Binder 调用 ──
        pending_tx = {}
        for row in binder_events:
            try:
                ts = int(row['ts'])
            except (ValueError, TypeError):
                continue
            if not (ws <= ts <= we):
                continue

            evt = row.get('event', '')
            try:
                tid = int(row['tid'])
                uid = int(row.get('uid') or 0)
                debug_id = int(row.get('debug_id') or 0)
                extra = int(row.get('extra') or 0)
                prev_tid = int(row.get('prev_tid') or 0)
                ret = int(row.get('ret') or 0)
            except (ValueError, TypeError):
                continue
            comm = row.get('comm', '')

            if target_uid > 0 and uid != target_uid and uid != 0:
                if evt not in ('binder_transaction', 'binder_received'):
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
                        category_stats[cat]['binder_calls'].append({
                            'debug_id': debug_id,
                            'tx_ts': tx['ts'], 'rx_ts': ts,
                            'tx_tid': tx['tid'], 'rx_tid': tid,
                            'tx_comm': tx['comm'], 'rx_comm': comm,
                            'to_thread': tx['to_thread'],
                            'code': tx['code'],
                            'latency_ns': latency,
                            'is_reply': tx['is_reply']
                        })

            elif evt.startswith('futex'):  # futex_wait/futex_wake/futex (兼容所有变体)
                op = extra
                op_base = op & 0x7F
                if op_base == 0:  # FUTEX_WAIT
                    category_stats[cat]['futex_waits'][tid] += 1
                elif op_base == 1:  # FUTEX_WAKE
                    category_stats[cat]['futex_wakes'][tid] += 1

            elif evt == 'cpu_frequency':
                freq_mhz = extra
                cpu_id = tid
                category_stats[cat]['cpu_freqs'][cpu_id].append(freq_mhz)

            elif evt == 'thermal':
                temp_c = extra
                zone = comm
                category_stats[cat]['thermals'].append({
                    'ts': ts, 'zone': zone, 'temp_c': temp_c
                })

    # ── 6. 构建输出 ──
    result = {
        "target_package": target_info.get('target_package'),
        "pid": target_pid,
        "uid": target_uid,
        "pipeline_analysis": {}
    }

    print("\n" + "=" * 80)
    print("  Camera Pipeline Stage Analysis Report")
    print("=" * 80)

    stage_order = ["CameraOpen", "CaptureSessionSetup", "Preview",
                   "CaptureRequest", "JpegEncode", "AutoFocus",
                   "HalCommunication", "Flash", "CameraClose",
                   "FaceDetection", "OtherCamera"]

    for cat in stage_order:
        if cat not in category_stats:
            continue
        stats = category_stats[cat]
        total_dur_ms = stats['total_duration_ns'] / 1_000_000
        n_stages = stats['stage_count']

        print(f"\n{'─' * 60}")
        print(f"  [{cat}]  {n_stages} stages, total {total_dur_ms:.1f}ms")
        print(f"{'─' * 60}")

        stage_result = {
            "stage_count": n_stages,
            "total_duration_ns": stats['total_duration_ns'],
            "top_threads": [],
            "binder_summary": {},
            "futex_summary": {},
        }

        # ── Top 线程 (按 Runnable Delay 排序) ──
        threads_by_delay = sorted(
            stats['threads'].items(),
            key=lambda x: -x[1]['runnable_delay_ns']
        )

        print(f"\n  Top-5 Threads by Runnable Delay:")
        for tid, tdata in threads_by_delay[:5]:
            rd_ms = tdata['runnable_delay_ns'] / 1_000_000
            run_ms = tdata['actual_run_ns'] / 1_000_000
            role = tdata['role']
            comm = tdata['comm']
            p95 = 0
            if tdata['delay_events']:
                ev = sorted(tdata['delay_events'])
                p95 = ev[int(len(ev) * 0.95)] if len(ev) >= 20 else max(ev)
            print(f"    TID:{tid:<6} [{role:<18}] {comm:<24} "
                  f"Delay={rd_ms:.2f}ms  Run={run_ms:.2f}ms  P95={p95/1e6:.3f}ms")

            stage_result['top_threads'].append({
                "tid": tid,
                "comm": comm,
                "role": role,
                "runnable_delay_ns": tdata['runnable_delay_ns'],
                "actual_run_ns": tdata['actual_run_ns'],
                "p95_delay_ns": p95,
                "delay_samples": len(tdata['delay_events']),
            })

        # ── Binder 总结 ──
        if stats['binder_calls']:
            total_binder_lat = sum(b['latency_ns'] for b in stats['binder_calls'])
            n_binder = len(stats['binder_calls'])
            print(f"\n  [Binder] {n_binder} calls, total latency={total_binder_lat/1e6:.2f}ms")
            stage_result['binder_summary'] = {
                "total_calls": n_binder,
                "total_latency_ns": total_binder_lat,
                "avg_latency_ns": total_binder_lat // n_binder if n_binder > 0 else 0,
            }

        # ── Futex 总结 ──
        total_waits = sum(stats['futex_waits'].values())
        total_wakes = sum(stats['futex_wakes'].values())
        if total_waits > 0 or total_wakes > 0:
            print(f"  [Futex] WAIT={total_waits}, WAKE={total_wakes}")
            stage_result['futex_summary'] = {
                "total_waits": total_waits,
                "total_wakes": total_wakes,
            }

        # ── CPU 频率 ──
        if stats['cpu_freqs']:
            all_freqs = [f for fl in stats['cpu_freqs'].values() for f in fl]
            if all_freqs:
                print(f"  [CPU] {len(all_freqs)} changes, "
                      f"min={min(all_freqs)}MHz max={max(all_freqs)}MHz")
                stage_result['cpu_freq'] = {
                    "min_mhz": min(all_freqs),
                    "max_mhz": max(all_freqs),
                    "avg_mhz": sum(all_freqs) // len(all_freqs),
                }

        # ── Thermal ──
        if stats['thermals']:
            temps = [t['temp_c'] for t in stats['thermals']]
            print(f"  [Thermal] {len(stats['thermals'])} readings, "
                  f"{min(temps)}~{max(temps)}°C")
            stage_result['thermal'] = {
                "min_c": min(temps),
                "max_c": max(temps),
            }

        result['pipeline_analysis'][cat] = stage_result

    # ── 7. 导出 ──
    out_path = os.path.join(base_dir, "..", "output", "analysis",
                            "camera_pipeline_result.json")
    with open(out_path, 'w') as f:
        json.dump(result, f, indent=4)
    print(f"\n[*] Camera pipeline analysis exported to: {out_path}")

    # ── 8. 总览摘要 ──
    print("\n" + "=" * 80)
    print("  Pipeline Stage Summary")
    print("=" * 80)
    print(f"  {'Stage':<28} {'Count':>6} {'TotalDur':>10} {'TopThread':<30} {'Delay':>10}")
    print(f"  {'-'*84}")
    for cat in stage_order:
        if cat not in category_stats:
            continue
        stats = category_stats[cat]
        dur_str = f"{stats['total_duration_ns']/1e6:.1f}ms"
        top = sorted(stats['threads'].items(), key=lambda x: -x[1]['runnable_delay_ns'])
        if top:
            top_tid, top_data = top[0]
            top_str = f"{top_data['comm']}(TID:{top_tid})"
            top_dly = f"{top_data['runnable_delay_ns']/1e6:.2f}ms"
        else:
            top_str = "—"
            top_dly = "—"
        print(f"  {cat:<28} {stats['stage_count']:>6} {dur_str:>10} {top_str:<30} {top_dly:>10}")


if __name__ == "__main__":
    analyze_camera_pipeline()
