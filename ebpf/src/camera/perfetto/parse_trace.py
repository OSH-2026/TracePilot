import os
import json
import stat
import urllib.request
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig


def _ensure_trace_processor(bin_dir):
    """确保 trace_processor_shell 二进制存在且可执行。已有文件绝不覆盖。"""
    bin_path = os.path.join(bin_dir, "trace_processor_shell")
    
    # 文件已存在: 只需确保可执行, 绝不覆盖
    if os.path.isfile(bin_path):
        if not os.access(bin_path, os.X_OK):
            os.chmod(bin_path, os.stat(bin_path).st_mode | stat.S_IEXEC)
            print(f"[*] Fixed permissions on existing {bin_path}")
        else:
            print(f"[*] Using existing {bin_path}")
        return bin_path
    
    # 文件不存在: 尝试下载 (可能被墙, 建议手动放置)
    os.makedirs(bin_dir, exist_ok=True)
    url = "https://get.perfetto.dev/trace_processor"
    print(f"[*] Downloading trace_processor_shell from {url} ...")
    print("[!] If download fails due to network, manually place the binary at:")
    print(f"    {bin_path}")
    try:
        urllib.request.urlretrieve(url, bin_path)
        os.chmod(bin_path, os.stat(bin_path).st_mode | stat.S_IEXEC)
        # 验证是 ELF 二进制而非 Python 脚本
        with open(bin_path, 'rb') as f:
            header = f.read(4)
        if header[:4] != b'\x7fELF':
            print("[✗] Downloaded file is not an ELF binary (Python wrapper).")
            print("[!] Please manually download trace_processor_shell from:")
            print("    https://github.com/google/perfetto/releases")
            print(f"    and place it at: {bin_path}")
            return None
        print(f"[✓] Downloaded to {bin_path}")
        return bin_path
    except Exception as e:
        print(f"[✗] Failed to download: {e}")
        print(f"[!] Please manually place trace_processor_shell at: {bin_path}")
        return None


# ─── 相机 atrace 切片 → 管线阶段归类 ───
_CAMERA_STAGE_RULES = [
    # (name_keywords, category_label, priority) — 优先级越高越先匹配
    # 相机打开阶段
    ("openCamera",        "CameraOpen",        100),
    ("open",              "CameraOpen",         90),
    ("getCameraCharacteristics", "CameraOpen",  95),
    # 会话创建 / 流配置
    ("createCaptureSession", "CaptureSessionSetup", 100),
    ("configureStreams",  "CaptureSessionSetup",  95),
    ("createStream",      "CaptureSessionSetup",  90),
    ("startStream",       "CaptureSessionSetup",  90),
    # 拍照请求
    ("captureRequest",    "CaptureRequest",    100),
    ("processCaptureRequest", "CaptureRequest", 100),
    ("capture",           "CaptureRequest",     85),
    ("stillCapture",      "CaptureRequest",    100),
    ("takePicture",       "CaptureRequest",    100),
    # JPEG/HEIC 编码
    ("jpeg",              "JpegEncode",        100),
    ("encode",            "JpegEncode",         85),
    ("heic",              "JpegEncode",        100),
    # 预览
    ("preview",           "Preview",           100),
    ("onFrameAvailable",  "Preview",            95),
    ("streamBuffer",      "Preview",            90),
    ("dequeueBuffer",     "Preview",            90),
    # HAL 层通信
    ("HIDL",              "HalCommunication",   95),
    ("HAL::",             "HalCommunication",  100),
    ("ICameraDevice",     "HalCommunication",   90),
    ("ICameraDeviceSession", "HalCommunication", 95),
    # 人脸检测/对焦
    ("face",              "FaceDetection",      95),
    ("autoFocus",         "AutoFocus",         100),
    ("focus",             "AutoFocus",          85),
    # 闪光灯
    ("flash",             "Flash",             100),
    # 关闭
    ("close",             "CameraClose",        95),
    ("release",           "CameraClose",        90),
]

def _classify_camera_slices(camera_df):
    """将 camera/hal atrace 原始切片归类为管线阶段。
    
    输入: pandas DataFrame with columns [slice_name, slice_start, slice_dur, 
          slice_end, depth, track_name, process_name, utid, tid, thread_name]
    输出: list of dicts [{name, category, start_ns, end_ns, duration_ns, 
          tid, process_name, thread_name}]
    """
    if camera_df.empty:
        return []
    
    stages = []
    for _, row in camera_df.iterrows():
        name = str(row.get('slice_name', '') or '')
        if not name:
            continue
        
        # 匹配合适的类别
        category = "OtherCamera"
        for keyword, cat, pri in sorted(_CAMERA_STAGE_RULES, key=lambda x: -x[2]):
            if keyword.lower() in name.lower():
                category = cat
                break
        
        dur = int(row['slice_dur']) if row['slice_dur'] else 0
        if dur <= 0:
            continue
        
        stages.append({
            "name": name,
            "category": category,
            "start_ns": int(row['slice_start']),
            "end_ns": int(row['slice_end']),
            "duration_ns": dur,
            "tid": int(row['tid']) if row.get('tid') and str(row['tid']).isdigit() else 0,
            "process_name": str(row.get('process_name', '') or ''),
            "thread_name": str(row.get('thread_name', '') or ''),
        })
    
    # 按时间排序
    stages.sort(key=lambda s: s['start_ns'])
    return stages


def analyze_trace(trace_path, target_package):
    """解析Perfetto Trace，提取app deadline missed的帧窗口"""

    print(f"[*] Analyzing trace: {trace_path} for app: {target_package}")
    # 确保 trace_processor_shell 可用, 不存在则自动下载
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "linux-amd64")
    bin_path = _ensure_trace_processor(tools_dir)
    config = TraceProcessorConfig(bin_path=bin_path, load_timeout=15) if bin_path else TraceProcessorConfig(load_timeout=15)
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
    
    # ═══════════════════════════════════════════════════════
    # 3. 提取 Camera ATrace 管线切片 (无论有没有 jank 帧都要做!)
    # ═══════════════════════════════════════════════════════
    print("\n--- [Step 3] Camera Pipeline Stages (camera/hal atrace) ---")
    camera_slices_query = """
        SELECT
            slice.name AS slice_name,
            slice.ts AS slice_start,
            slice.dur AS slice_dur,
            (slice.ts + slice.dur) AS slice_end,
            slice.depth,
            track.name AS track_name,
            process.name AS process_name,
            thread.utid,
            thread.tid,
            thread.name AS thread_name
        FROM slice
        JOIN track ON slice.track_id = track.id
        LEFT JOIN thread_track ON track.id = thread_track.id
        LEFT JOIN thread ON thread_track.utid = thread.id
        LEFT JOIN process ON thread.upid = process.upid
        WHERE (
            track.name LIKE '%atrace%camera%' OR
            track.name LIKE '%atrace%hal%' OR
            track.name LIKE '%camera.%' OR
            track.name LIKE '%hal.%' OR
            track.name GLOB '*camera*' OR
            track.name GLOB '*hal*'
        )
        AND slice.dur > 0
        ORDER BY slice.ts ASC
    """
    camera_slices = tp.query(camera_slices_query).as_pandas_dataframe()
    print(f"Found {len(camera_slices)} camera/hal atrace slices.")
    
    camera_pipeline_stages = _classify_camera_slices(camera_slices)
    print(f"Classified into {len(camera_pipeline_stages)} pipeline stages.")
    for stage in camera_pipeline_stages:
        dur_ms = (stage['end_ns'] - stage['start_ns']) / 1_000_000
        print(f"  [{stage['category']}] {stage['name'][:80]} "
              f"({dur_ms:.1f}ms) TID:{stage.get('tid','?')}")

    # ── 如果没找到 jank 进程, 尝试按包名查找 ──
    if procs.empty:
        print(f"\n[*] No 'App Deadline Missed' frames found. Searching by package name: {target_package}")
        pkg_query = f"""
            SELECT process.upid, process.pid, process.name, process.uid
            FROM process
            WHERE process.name LIKE '%{target_package.split('.')[-1]}%'
               OR process.name = '{target_package}'
            LIMIT 1
        """
        procs = tp.query(pkg_query).as_pandas_dataframe()
    
    # ── 如果还是找不到, 仍然导出 camera_pipeline 数据 ──
    if procs.empty:
        print(f"[!] Cannot find target process. Exporting camera pipeline data only.")
        # 用占位符导出
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "output", "analysis")
        os.makedirs(output_dir, exist_ok=True)
        out_file = os.path.join(output_dir, "ebpf_target_windows.json")
        export_data = {
            "target_package": target_package,
            "pid": 0, "uid": 0,
            "threads_map": {},
            "jank_frames": [],
            "camera_pipeline": camera_pipeline_stages,
            "_note": "No jank frames or target process found in this trace."
        }
        with open(out_file, "w") as f:
            json.dump(export_data, f, indent=4)
        print(f"[*] Exported camera pipeline data ({len(camera_pipeline_stages)} stages) to: {out_file}")
        return
    
    # 假设取丢帧最多的应用作为目标应用
    main_upid = procs.iloc[0]['upid']
    actual_name = procs.iloc[0]['name'] or target_package
    actual_uid = int(procs.iloc[0]['uid']) if procs.iloc[0]['uid'] is not None and not (isinstance(procs.iloc[0]['uid'], float)) else 0
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

    # 4. 提取所有的 jank 帧生命周期窗口 + camera 管线阶段，并导出为 JSON
    export_data = {
        "target_package": actual_name,
        "pid": int(procs.iloc[0]['pid']),
        "uid": int(procs.iloc[0]['uid']) if procs.iloc[0]['uid'] is not None else 0,
        "threads_map": threads_map,
        "jank_frames": [],
        "camera_pipeline": camera_pipeline_stages
    }
    
    if not frames.empty:
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
    
    output_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "output", "analysis")
    os.makedirs(output_dir, exist_ok=True)
    out_file = os.path.join(output_dir, "ebpf_target_windows.json")
    with open(out_file, "w") as f:
        json.dump(export_data, f, indent=4)
        
    print(f"\n--- [Step 4] Exported EBPF Target Windows ---")
    print(f"[*] Successfully exported {len(export_data['jank_frames'])} App Deadline Missed frames + "
          f"{len(camera_pipeline_stages)} camera pipeline stages to:")
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
