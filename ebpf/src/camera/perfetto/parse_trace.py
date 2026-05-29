<<<<<<< HEAD
import os
import json
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

def analyze_trace(trace_path, target_package):
    """解析Perfetto Trace，提取app deadline missed的帧窗口"""

    print(f"[*] Analyzing trace: {trace_path} for app: {target_package}")
    # 指定预编译的 trace_processor 路径以跳过网络下载
    bin_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "linux-amd64", "trace_processor_shell")
    config = TraceProcessorConfig(bin_path=bin_path, load_timeout=15)
    tp = TraceProcessor(trace=trace_path, config=config)

    # 1. 找到目标进程 (App Session / UID / PID 语义层)
    print("\n--- [Step 1] Target Process Info ---")
    
    # 既然包名可能因为 Linux kernel 的 15 字符截断变成 "com.google.andr"，包含 "camera" 关键字都不行，
    # 我们不如直接反过来，去找所有存在 App Deadline Missed 帧的应用，通常就是我们的目标应用！
    app_query = """
        SELECT process.upid, process.pid, process.name, process.uid, COUNT(actual.id) as jank_count
        FROM process
        JOIN actual_frame_timeline_slice actual ON process.upid = actual.upid
        WHERE actual.jank_type LIKE 'App Deadline Missed'
        GROUP BY process.upid
        ORDER BY jank_count DESC
    """
    procs = tp.query(app_query).as_pandas_dataframe()
    
    if procs.empty:
        print(f"No process with 'App Deadline Missed' frames found in trace.")
        return
    print("Found janky processes:")
    print(procs)
    
    # 假设取丢帧最多的应用作为目标应用
    main_upid = procs.iloc[0]['upid']
    actual_name = procs.iloc[0]['name']
    actual_uid = procs.iloc[0]['uid']
    print(f"\n=> Selected target process: {actual_name} (UPID: {main_upid}, UID: {actual_uid})")

    
    # 2. 找到该进程的 jank 帧与帧窗口 (Frame-Centric 交互窗口定义)
    # 通过 expected_frame_timeline_slice 和 actual_frame_timeline_slice 进行交并获取 Timeline 边界
    print("\n--- [Step 2] Jank Frames & Interaction Windows ---")
    frame_query = f"""
        SELECT DISTINCT
            actual.name AS frame_token,
            actual.ts AS actual_start,
            actual.dur AS actual_dur,
            (actual.ts + actual.dur) AS actual_end,
            expected.ts AS expected_start,
            (expected.ts + expected.dur) AS expected_end,
            actual.jank_type
        FROM actual_frame_timeline_slice actual
        JOIN expected_frame_timeline_slice expected 
          ON actual.name = expected.name 
             AND actual.upid = expected.upid
        WHERE actual.upid = {main_upid}
          AND actual.jank_type LIKE 'App Deadline Missed' -- 锁定真正的 App 耗时丢帧
        ORDER BY actual.ts
        
    """
    frames = tp.query(frame_query).as_pandas_dataframe()
    print(frames)

    print("\n--- [Step 2.1] Extract ThreadKey Map ---")
    thread_query = f"""
        SELECT upid, utid, tid, name, start_ts 
        FROM thread 
        WHERE upid = {main_upid}
    """
    threads = tp.query(thread_query).as_pandas_dataframe()
    threads_map = {}
    for _, row in threads.iterrows():
        threads_map[int(row['tid'])] = {
            "name": str(row['name']) if row['name'] else "unknown",
            "start_ts": int(row['start_ts']) if row['start_ts'] else 0
        }
    print(f"Extracted {len(threads_map)} ThreadKeys for ProcessInstance.")

    # 3. 提取所有的 jank 帧生命周期窗口，并导出为 JSON 供 eBPF 采集使用
    if not frames.empty:
        export_data = {
            "target_package": actual_name,
            "pid": int(procs.iloc[0]['pid']),
            "uid": int(procs.iloc[0]['uid']) if procs.iloc[0]['uid'] is not None else 0,
            "threads_map": threads_map,
            "jank_frames": []
        }
        
        pre_margin, post_margin = 2_000_000, 2_000_000 # 前后2ms
        
        for _, row in frames.iterrows():
            ws = int(min(row['actual_start'], row['expected_start']) - pre_margin)
            we = int(max(row['actual_end'], row['expected_end']) + post_margin)
            export_data["jank_frames"].append({
                "frame_token": int(row['frame_token']),
                "jank_type": row['jank_type'],
                "window_start_ns": ws,
                "window_end_ns": we,
                "actual_duration_ns": int(row['actual_dur'])
            })
            
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(output_dir, exist_ok=True)
        out_file = os.path.join(output_dir, "ebpf_target_windows.json")
        with open(out_file, "w") as f:
            json.dump(export_data, f, indent=4)
            
        print(f"\n--- [Step 3] Exported EBPF Target Windows ---")
        print(f"[*] Successfully exported {len(frames)} App Deadline Missed frames to:")
        print(f"[*] {out_file}")
        print("\nNote: Next steps: Your eBPF program can read this JSON file to know exactly which PID/UID and which timestamp windows (window_start_ns to window_end_ns) to analyze for sched, futex, memory reclaim, etc.")

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("Usage: python parse_trace.py <trace_file> <package_name>")
        print("Example: python parse_trace.py camera_trace.perfetto com.google.android.GoogleCamera")
        sys.exit(1)
    
    trace_file = sys.argv[1]
    pkg_name = sys.argv[2]
    analyze_trace(trace_file, pkg_name)
=======
import os
import json
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

def analyze_trace(trace_path, target_package):
    """解析Perfetto Trace，提取app deadline missed的帧窗口"""

    print(f"[*] Analyzing trace: {trace_path} for app: {target_package}")
    # 指定预编译的 trace_processor 路径以跳过网络下载
    bin_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "linux-amd64", "trace_processor_shell")
    config = TraceProcessorConfig(bin_path=bin_path, load_timeout=15)
    tp = TraceProcessor(trace=trace_path, config=config)

    # 1. 找到目标进程 (App Session / UID / PID 语义层)
    print("\n--- [Step 1] Target Process Info ---")
    
    # 既然包名可能因为 Linux kernel 的 15 字符截断变成 "com.google.andr"，包含 "camera" 关键字都不行，
    # 我们不如直接反过来，去找所有存在 App Deadline Missed 帧的应用，通常就是我们的目标应用！
    app_query = """
        SELECT process.upid, process.pid, process.name, process.uid, COUNT(actual.id) as jank_count
        FROM process
        JOIN actual_frame_timeline_slice actual ON process.upid = actual.upid
        WHERE actual.jank_type LIKE 'App Deadline Missed'
        GROUP BY process.upid
        ORDER BY jank_count DESC
    """
    procs = tp.query(app_query).as_pandas_dataframe()
    
    if procs.empty:
        print(f"No process with 'App Deadline Missed' frames found in trace.")
        return
    print("Found janky processes:")
    print(procs)
    
    # 假设取丢帧最多的应用作为目标应用
    main_upid = procs.iloc[0]['upid']
    actual_name = procs.iloc[0]['name']
    actual_uid = procs.iloc[0]['uid']
    print(f"\n=> Selected target process: {actual_name} (UPID: {main_upid}, UID: {actual_uid})")

    
    # 2. 找到该进程的 jank 帧与帧窗口 (Frame-Centric 交互窗口定义)
    # 通过 expected_frame_timeline_slice 和 actual_frame_timeline_slice 进行交并获取 Timeline 边界
    print("\n--- [Step 2] Jank Frames & Interaction Windows ---")
    frame_query = f"""
        SELECT DISTINCT
            actual.name AS frame_token,
            actual.ts AS actual_start,
            actual.dur AS actual_dur,
            (actual.ts + actual.dur) AS actual_end,
            expected.ts AS expected_start,
            (expected.ts + expected.dur) AS expected_end,
            actual.jank_type
        FROM actual_frame_timeline_slice actual
        JOIN expected_frame_timeline_slice expected 
          ON actual.name = expected.name 
             AND actual.upid = expected.upid
        WHERE actual.upid = {main_upid}
          AND actual.jank_type LIKE 'App Deadline Missed' -- 锁定真正的 App 耗时丢帧
        ORDER BY actual.ts
        
    """
    frames = tp.query(frame_query).as_pandas_dataframe()
    print(frames)

    print("\n--- [Step 2.1] Extract ThreadKey Map ---")
    thread_query = f"""
        SELECT upid, utid, tid, name, start_ts 
        FROM thread 
        WHERE upid = {main_upid}
    """
    threads = tp.query(thread_query).as_pandas_dataframe()
    threads_map = {}
    for _, row in threads.iterrows():
        threads_map[int(row['tid'])] = {
            "name": str(row['name']) if row['name'] else "unknown",
            "start_ts": int(row['start_ts']) if row['start_ts'] else 0
        }
    print(f"Extracted {len(threads_map)} ThreadKeys for ProcessInstance.")

    # 3. 提取所有的 jank 帧生命周期窗口，并导出为 JSON 供 eBPF 采集使用
    if not frames.empty:
        export_data = {
            "target_package": actual_name,
            "pid": int(procs.iloc[0]['pid']),
            "uid": int(procs.iloc[0]['uid']) if procs.iloc[0]['uid'] is not None else 0,
            "threads_map": threads_map,
            "jank_frames": []
        }
        
        pre_margin, post_margin = 2_000_000, 2_000_000 # 前后2ms
        
        for _, row in frames.iterrows():
            ws = int(min(row['actual_start'], row['expected_start']) - pre_margin)
            we = int(max(row['actual_end'], row['expected_end']) + post_margin)
            export_data["jank_frames"].append({
                "frame_token": int(row['frame_token']),
                "jank_type": row['jank_type'],
                "window_start_ns": ws,
                "window_end_ns": we,
                "actual_duration_ns": int(row['actual_dur'])
            })
            
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(output_dir, exist_ok=True)
        out_file = os.path.join(output_dir, "ebpf_target_windows.json")
        with open(out_file, "w") as f:
            json.dump(export_data, f, indent=4)
            
        print(f"\n--- [Step 3] Exported EBPF Target Windows ---")
        print(f"[*] Successfully exported {len(frames)} App Deadline Missed frames to:")
        print(f"[*] {out_file}")
        print("\nNote: Next steps: Your eBPF program can read this JSON file to know exactly which PID/UID and which timestamp windows (window_start_ns to window_end_ns) to analyze for sched, futex, memory reclaim, etc.")

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print("Usage: python parse_trace.py <trace_file> <package_name>")
        print("Example: python parse_trace.py camera_trace.perfetto com.google.android.GoogleCamera")
        sys.exit(1)
    
    trace_file = sys.argv[1]
    pkg_name = sys.argv[2]
    analyze_trace(trace_file, pkg_name)
>>>>>>> e021c6bfb877fb5165df1fee7abfb3908cb1bfd0
