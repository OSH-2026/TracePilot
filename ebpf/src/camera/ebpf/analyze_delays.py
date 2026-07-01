import json
import csv
import os
from collections import defaultdict

def analyze_ebpf_delays():
    # 1. 读入 Perfetto 的 Jank ground truth 窗口
    base_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(base_dir, "perfetto", "output", "ebpf_target_windows.json")
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return

    with open(json_path, 'r') as f:
        target_info = json.load(f)
    
    target_pid = target_info['pid']
    target_uid = target_info['uid']
    threads_map = target_info.get('threads_map', {})
    jank_frames = target_info['jank_frames']
    
    print(f"[*] Loaded Top-level Target: Package={target_info['target_package']}, PID={target_pid}, UID={target_uid}")
    print(f"[*] Loaded {len(threads_map)} ThreadKeys from ground truth.")
    print(f"[*] Found {len(jank_frames)} jank frame windows.")

    # 2. 读入 eBPF 的日志 (由 camera_ebpf.c 输出为 CSV)
    csv_path = os.path.join(base_dir, "ebpf","sched_events.csv")
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} not found. Please run your eBPF program to generate events.")
        return

    # 结构: tid -> { 'comm': name, 'role': unknown }
    thread_info = {}
    
    # 结构: tid -> list of wakeup ts (为了计算 runnable delay)
    pending_wakeups = defaultdict(list)
    
    # 结构: frame_token -> { tid -> runnable_delay_sum }
    frame_delays = defaultdict(lambda: defaultdict(int))
    
    # 结构: frame_token -> { tid -> run_time_sum } (计算真正运行的时长)
    frame_runtimes = defaultdict(lambda: defaultdict(int))
    
    events = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        events = list(reader)

    # 3. Step 3 & 4: Resolver & 聚合 frame window 内的 runnable delay
    running_start = {} # tid -> start_ts
    
    for row in events:
        ts = int(row['ts'])
        evt_type = row['event']
        # tid是需要关注的执行实体，switch的时候是切入(next)，wakeup的时候是被唤醒的目标
        tid = int(row['tid'])
        prev_tid = int(row['prev_tid']) if row['prev_tid'] else 0
        tgid = int(row.get('tgid', 0))
        uid = int(row.get('uid', 0))
        comm = row['comm']
        
        # Identity Layer Filtering (基于 UID 防止 PID 复用引发的幽灵线程)
        if uid != target_uid and uid != 0: 
            continue
            
        # 使用 ThreadKey (来自 Perfetto 的静态血缘) 解析真正的角色
        if str(tid) in threads_map:
            thread_info[tid] = {'comm': threads_map[str(tid)]['name']}
        else:
            thread_info[tid] = {'comm': comm}
            
        if evt_type == 'switch':
            if str(prev_tid) in threads_map:
                thread_info[prev_tid] = {'comm': threads_map[str(prev_tid)]['name']}
            elif prev_tid not in thread_info:
                thread_info[prev_tid] = {'comm': comm}
        
        # 判断事件处于哪个 jank frame 窗口内
        active_frame = None
        for frame in jank_frames:
            if frame["window_start_ns"] <= ts <= frame["window_end_ns"]:
                active_frame = frame["frame_token"]
                break
                
        if not active_frame:
            continue
            
        if evt_type == "wakeup":
            pending_wakeups[tid].append(ts)
        elif evt_type == "switch":
            # 相当于原来的 SWITCH_IN(tid) 和 SWITCH_OUT(prev_tid) 合成一条
            # 1. 记录切出的那个线程的 runtime
            if prev_tid in running_start:
                run_dur = ts - running_start[prev_tid]
                frame_runtimes[active_frame][prev_tid] += run_dur
                del running_start[prev_tid]
                
            # 2. 记录刚刚切入的这个线程，并计算其 runnable delay
            running_start[tid] = ts
            if pending_wakeups[tid]:
                w_ts = pending_wakeups[tid].pop(0)
                runnable_delay = ts - w_ts
                if runnable_delay > 0:
                    frame_delays[active_frame][tid] += runnable_delay

    # 4. Step 5: 角色识别与报告
    print("\n--- [Step 5] Thread Role Identification & Delay Report ---")
    
    analysis_result = {
        "target_package": target_info.get('target_package'),
        "pid": target_pid,
        "uid": target_uid,
        "frames": []
    }

    for frame_token, delays in frame_delays.items():
        print(f"\nFrame Token: {frame_token}")
        frame_report = {
            "frame_token": frame_token,
            "threads_delay": []
        }
        for tid, d_sum in delays.items():
            comm = thread_info[tid]['comm']
            role = "UnknownWorker"
            
            # Heuristic 角色识别 (基于 Comm 和行为特征)
            if "UI" in comm or "ndroid" in comm or tid == target_pid:
                role = "UI Thread"
            elif "RenderThread" in comm or "Render" in comm:
                role = "RenderThread"
            elif "binder" in comm.lower() or "hwbinder" in comm.lower():
                role = "Binder RPC"

            r_sum = frame_runtimes[frame_token].get(tid, 0)
            print(f"  [TID: {tid:<5} | Role: {role:<15} ({comm})] -> Runnable Delay: {d_sum / 1_000_000:.2f} ms | Actual Run: {r_sum / 1_000_000:.2f} ms")
            
            is_critical = d_sum > 2_000_000
            if is_critical: # 超过2ms的调度延迟，标记为调度介入目标
                print(f"      => [!] HIGH DELAY ALERT: Critical for Hint Engine (Action: BOOST_THREAD)")

            frame_report["threads_delay"].append({
                "tid": tid,
                "comm": comm,
                "role": role,
                "runnable_delay_ns": d_sum,
                "actual_run_ns": r_sum,
                "critical_for_hint": is_critical
            })
            
        analysis_result["frames"].append(frame_report)

    # 导出到 JSON 文件
    out_json_path = os.path.join(base_dir, "delay_analysis_result.json")
    with open(out_json_path, "w") as f:
        json.dump(analysis_result, f, indent=4)
    print(f"\n[*] Analysis results exported to: {out_json_path}")

if __name__ == "__main__":
    analyze_ebpf_delays()
