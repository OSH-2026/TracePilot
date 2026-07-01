#!/usr/bin/env python3
"""
collect_game_aligned.py — 游戏场景对齐采集器
采集王者荣耀对局场景的 Perfetto FrameTimeline + eBPF/TracePilot 事件，
输出帧对齐的调度数据用于 Step1/Step2 分析。

用法:
  python collect_game_aligned.py --package com.tencent.tmgp.sgame --duration 60
"""
import argparse
import base64
import gzip
import json
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEVICE_SCRIPT = Path(__file__).with_name("android_game_aligned_capture.sh")
PERFETTO_TEMPLATE = ROOT / "src" / "camera" / "perfetto" / "perfetto_game_frametimeline.pbtx"
LAUNCHER_PACKAGES = {
    "com.google.android.apps.nexuslauncher",
    "com.android.launcher",
    "com.android.launcher3",
}


def run(cmd, timeout=30, binary=False, check=True):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
    )
    if check and result.returncode != 0:
        output = result.stdout if not binary else result.stdout.decode("utf-8", errors="replace")
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(map(str, cmd))}\n{output}")
    return result.stdout


def adb(adb_path, args, timeout=30, binary=False, check=True):
    return run([str(adb_path), *args], timeout=timeout, binary=binary, check=check)


def shell(adb_path, command, timeout=30, root=False, check=True):
    if root:
        command = f"su -c {shlex.quote(command)}"
    return adb(adb_path, ["shell", command], timeout=timeout, check=check)


def foreground_package(adb_path):
    output = shell(adb_path, "dumpsys window", timeout=30)
    patterns = [
        r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/[A-Za-z0-9_.$/]+",
        r"mFocusedApp=.*?\s([A-Za-z0-9_.]+)/[A-Za-z0-9_.$/]+",
    ]
    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group(1)
    return ""


def command_package_regex(package):
    segments = [segment for segment in package.split(".") if segment]
    hints = [package, segments[-1] if segments else package]
    escaped = [re.escape(hint[:15]) for hint in hints if hint]
    escaped.extend(["RenderThread", "Unity", "GPU", "surfaceflinger"])
    return "|".join(dict.fromkeys(escaped))


def write_text(path, content):
    path.write_text(content, encoding="utf-8", errors="replace")


def optional_run(cmd, timeout=30):
    try:
        return run(cmd, timeout=timeout)
    except FileNotFoundError as exc:
        return json.dumps({"status": "skipped", "reason": str(exc)}, ensure_ascii=False, indent=2)
    except RuntimeError as exc:
        return json.dumps({"status": "failed", "reason": str(exc)}, ensure_ascii=False, indent=2)


def render_perfetto_config(package, duration_s):
    template = PERFETTO_TEMPLATE.read_text(encoding="utf-8")
    duration_ms = int((duration_s + 3) * 1000)
    return template.replace("__DURATION_MS__", str(duration_ms)).replace("__PACKAGE__", package)


def start_perfetto_capture(adb_path, out_dir, tag, package, duration_s):
    config_path = out_dir / f"{tag}_perfetto.pbtx"
    write_text(config_path, render_perfetto_config(package, duration_s))
    remote_config = f"/data/local/tmp/{tag}_perfetto.pbtx"
    remote_trace = f"/data/misc/perfetto-traces/{tag}.perfetto-trace"
    adb(adb_path, ["push", str(config_path), remote_config], timeout=60)
    shell(
        adb_path,
        f"mkdir -p /data/misc/perfetto-traces && rm -f {remote_trace} && "
        f"perfetto --txt -c {remote_config} -o {remote_trace} --background",
        timeout=30,
        root=True,
    )
    return {
        "perfetto_config": config_path.name,
        "perfetto_remote_config": remote_config,
        "perfetto_remote_trace": remote_trace,
    }


def pull_perfetto_trace(adb_path, out_dir, tag, remote_trace):
    local_trace = out_dir / f"{tag}.perfetto-trace"
    wait_cmd = f"for i in $(seq 1 20); do test -s {remote_trace} && exit 0; sleep 1; done; test -s {remote_trace}"
    wait_output = shell(adb_path, wait_cmd, timeout=30, root=True, check=False)
    result = adb(adb_path, ["pull", remote_trace, str(local_trace)], timeout=240, check=False)
    if (not local_trace.exists()) or local_trace.stat().st_size == 0 or "Permission denied" in result:
        cmd = [str(adb_path), "exec-out", "su", "-c", f"base64 {remote_trace}"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        carry = b""
        with local_trace.open("wb") as handle:
            while True:
                chunk = proc.stdout.read(1024 * 1024)
                if not chunk:
                    break
                data = re.sub(br"[^A-Za-z0-9+/=]", b"", carry + chunk)
                usable = (len(data) // 4) * 4
                if usable:
                    handle.write(base64.b64decode(data[:usable], validate=False))
                carry = data[usable:]
            if carry:
                handle.write(base64.b64decode(carry, validate=False))
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
        proc.wait()
        result = f"{result.strip()}\nbase64 exec-out fallback stderr={stderr.strip()}"
    return {
        "perfetto_trace_file": local_trace.name if local_trace.exists() else "",
        "perfetto_trace_size_bytes": local_trace.stat().st_size if local_trace.exists() else 0,
        "perfetto_wait_output": wait_output.strip(),
        "perfetto_pull_output": result.strip(),
    }


def postprocess(out_dir, tag, package, trace_path, ftrace_path, framestats_path, sf_latency_path):
    scripts = ROOT / "scripts"
    comm_regex = command_package_regex(package)
    notes = []

    postprocess_trace = scripts / "postprocess_trace.py"
    if postprocess_trace.exists():
        post_summary = optional_run(
            [sys.executable, str(postprocess_trace), str(trace_path), "--comm-regex", comm_regex],
            timeout=300,
        )
        write_text(out_dir / f"{tag}_scheduler_summary.json", post_summary)
    else:
        notes.append("postprocess_trace.py missing; scheduler_summary was skipped.")

    export_summary = run(
        [sys.executable, str(scripts / "export_trace_csv.py"), str(trace_path),
         "--out-dir", str(out_dir), "--comm-regex", comm_regex,
         "--file-prefix", tag],
        timeout=300,
    )
    write_text(out_dir / f"{tag}_export_summary.json", export_summary)

    summarize_ftrace = scripts / "summarize_ftrace.py"
    if summarize_ftrace.exists():
        ftrace_summary = optional_run(
            [sys.executable, str(summarize_ftrace), str(ftrace_path),
             "--out", str(out_dir / f"{tag}_ftrace_summary.json")],
            timeout=120,
        )
        write_text(out_dir / f"{tag}_ftrace_summary_stdout.json", ftrace_summary)
    else:
        notes.append("summarize_ftrace.py missing; ftrace_summary was skipped.")

    frame_source = "gfxinfo"
    parse_framestats = scripts / "parse_framestats.py"
    if parse_framestats.exists():
        optional_run(
            [sys.executable, str(parse_framestats), str(framestats_path),
             "--csv-out", str(out_dir / f"{tag}_frames.csv"),
             "--summary-out", str(out_dir / f"{tag}_frame_summary.json")],
            timeout=120,
        )
    else:
        notes.append("parse_framestats.py missing; gfxinfo frame parsing was skipped.")
    if sf_latency_path.exists() and sf_latency_path.stat().st_size > 0:
        frame_source = "surfaceflinger"
        layer_path = out_dir / f"{tag}_surfaceflinger_layer.txt"
        layer = layer_path.read_text(encoding="utf-8", errors="replace").strip() if layer_path.exists() else ""
        run(
            [sys.executable, str(scripts / "parse_surfaceflinger_latency.py"), str(sf_latency_path),
             "--csv-out", str(out_dir / f"{tag}_surfaceflinger_frames.csv"),
             "--summary-out", str(out_dir / f"{tag}_surfaceflinger_frame_summary.json"),
             "--layer", layer],
            timeout=120,
        )
    return {"comm_regex": comm_regex, "frame_sources": ["gfxinfo", frame_source], "postprocess_notes": notes}


def main():
    parser = argparse.ArgumentParser(description="Capture an aligned manual gameplay window from the rooted Pixel device.")
    parser.add_argument("--adb", default="adb", help="Path to adb executable.")
    parser.add_argument("--package", default="", help="Game package name; defaults to the current foreground app.")
    parser.add_argument("--duration", type=int, default=25, help="Capture duration in seconds.")
    parser.add_argument("--tag", default="", help="Dataset tag; defaults to game_play_<timestamp>.")
    parser.add_argument("--out-dir", default="", help="Local output directory.")
    parser.add_argument("--screenshots", action="store_true", help="Also save before/after screenshots.")
    parser.add_argument("--perfetto", action="store_true", help="Also capture a Perfetto FrameTimeline trace.")
    args = parser.parse_args()

    if args.duration < 5 or args.duration > 120:
        raise SystemExit("--duration must be between 5 and 120 seconds")
    adb_path = Path(args.adb) if args.adb != "adb" else args.adb
    devices = adb(adb_path, ["devices"], timeout=30)
    if "\tdevice" not in devices:
        raise SystemExit("No adb device is online.")
    root_check = shell(adb_path, "id", timeout=30, root=True)
    if "uid=0" not in root_check:
        raise SystemExit("The connected device is not providing root through su.")

    active_package = foreground_package(adb_path)
    package = args.package or active_package
    if not package:
        raise SystemExit("Cannot identify the foreground package. Open the game first or pass --package.")
    if package in LAUNCHER_PACKAGES:
        raise SystemExit("The launcher is in the foreground. Open the game at a playable scene, then start capture.")
    if active_package != package:
        raise SystemExit(f"Foreground app is {active_package!r}, not requested game package {package!r}.")

    tag = args.tag or datetime.now().strftime("game_play_%Y%m%d_%H%M%S")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", tag):
        raise SystemExit("--tag may contain only letters, digits, dot, underscore, and dash.")
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "data" / "raw" / tag
    out_dir.mkdir(parents=True, exist_ok=False)

    device_script = "/data/local/tmp/android_game_aligned_capture.sh"
    device_prefix = f"/data/local/tmp/{tag}"
    adb(adb_path, ["push", str(DEVICE_SCRIPT), device_script], timeout=60)
    shell(adb_path, f"chmod 755 {device_script}", timeout=30, root=True)
    dependency_check = shell(
        adb_path,
        "test -x /data/local/tmp/tracepilot && test -r /data/local/tmp/tracepilot.bpf.o && echo ready",
        timeout=30,
        root=True,
    )
    if "ready" not in dependency_check:
        raise SystemExit("tracepilot or tracepilot.bpf.o is missing on the device.")

    metadata = {
        "tag": tag,
        "package": package,
        "duration_s": args.duration,
        "captured_at": datetime.now().astimezone().isoformat(),
        "foreground_at_start": active_package,
        "device_script": device_script,
    }
    perfetto_info = {}
    if args.perfetto:
        if not PERFETTO_TEMPLATE.exists():
            raise SystemExit(f"Perfetto config template is missing: {PERFETTO_TEMPLATE}")
        perfetto_info = start_perfetto_capture(adb_path, out_dir, tag, package, args.duration)
        metadata.update(perfetto_info)

    write_text(out_dir / f"{tag}_host_metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    if args.screenshots:
        (out_dir / f"{tag}_before.png").write_bytes(adb(adb_path, ["exec-out", "screencap", "-p"], binary=True, timeout=30))

    print(f"Capturing {package} for {args.duration}s. Keep playing during this window.", flush=True)
    output = shell(
        adb_path,
        f"sh {device_script} {args.duration} {device_prefix} {package}",
        timeout=args.duration + 45,
        root=True,
    )
    write_text(out_dir / f"{tag}_device_stdout.txt", output)
    if args.perfetto:
        perfetto_pull = pull_perfetto_trace(adb_path, out_dir, tag, perfetto_info["perfetto_remote_trace"])
        metadata.update(perfetto_pull)

    if args.screenshots:
        (out_dir / f"{tag}_after.png").write_bytes(adb(adb_path, ["exec-out", "screencap", "-p"], binary=True, timeout=30))

    suffixes = [
        ".jsonl",
        "_events.bin",
        "_metadata.txt",
        "_tracepilot_stdout.txt",
        "_ftrace.txt",
        "_framestats.txt",
        "_surfaceflinger_layer.txt",
        "_surfaceflinger_latency.txt",
    ]
    for suffix in suffixes:
        remote = f"{device_prefix}{suffix}"
        local = out_dir / f"{tag}{suffix}"
        result = adb(adb_path, ["pull", remote, str(local)], timeout=240, check=False)
        if local.exists():
            print(result.strip())

    raw_trace = out_dir / f"{tag}.jsonl"
    events_bin = out_dir / f"{tag}_events.bin"
    if raw_trace.exists():
        compressed_trace = raw_trace.with_suffix(raw_trace.suffix + ".gz")
        with raw_trace.open("rb") as source, gzip.open(compressed_trace, "wb", compresslevel=6) as target:
            shutil.copyfileobj(source, target)
        raw_delete_status = "deleted"
        try:
            raw_trace.unlink()
        except OSError as exc:
            raw_delete_status = f"kept: {exc}"

        processing = postprocess(
            out_dir,
            tag,
            package,
            compressed_trace,
            out_dir / f"{tag}_ftrace.txt",
            out_dir / f"{tag}_framestats.txt",
            out_dir / f"{tag}_surfaceflinger_latency.txt",
        )
        metadata["trace_file"] = compressed_trace.name
        metadata["trace_size_bytes"] = compressed_trace.stat().st_size
        metadata["raw_trace_cleanup"] = raw_delete_status
        metadata.update(processing)
    elif events_bin.exists():
        metadata["trace_file"] = events_bin.name
        metadata["trace_format"] = "tracepilot_events_bin"
        metadata["trace_size_bytes"] = events_bin.stat().st_size
        metadata["postprocess_notes"] = [
            "The device tracepilot produced events.bin instead of JSONL; JSONL postprocessing was skipped."
        ]
    else:
        metadata["trace_file"] = ""
        metadata["trace_size_bytes"] = 0
        metadata["postprocess_notes"] = [
            "No eBPF JSONL or events.bin was produced; inspect tracepilot stdout and use Perfetto/ftrace only."
        ]
    write_text(out_dir / f"{tag}_host_metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
