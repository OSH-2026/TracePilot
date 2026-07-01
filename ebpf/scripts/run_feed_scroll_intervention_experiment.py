#!/usr/bin/env python3
"""
Run repeated Chrome feed-scroll baseline/intervention captures.

The current device build does not expose per-thread sched_boost/uclamp files for
SurfaceFlinger, and taskset/chrt are denied for system compositor threads on the
Pixel 6a. This script therefore uses a conservative, auditable intervention:
while the capture window is running, it repeatedly identifies Chrome render /
compositor threads, records their original nice/cpuset state, applies a temporary
renice + top-app cpuset guard when allowed, and rolls the state back at the end.
"""
import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
COLLECT_SCRIPT = ROOT / "scripts" / "collect_game_aligned.py"
PARSE_FRAMES = ROOT / "scripts" / "parse_perfetto_frametimeline.py"
ANALYZE_SCHED = ROOT / "scripts" / "analyze_perfetto_sched_windows.py"
ANALYZE_CPU = ROOT / "scripts" / "analyze_perfetto_cpu_freq_windows.py"
DEFAULT_TRACE_PROCESSOR = Path("/private/tmp/trace_processor")

TARGET_COMMS = {
    ".android.chrome",
    "Chrome_IOThread",
    "VizCompositorTh",
    "CompositorGpuTh",
    "Compositor",
    "CrRendererMain",
    "ThreadPoolForeg",
    "mali-cmar-backe",
}


def run(cmd, timeout=60, check=True, cwd=REPO):
    result = subprocess.run(
        [str(part) for part in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(map(str, cmd))}\n{result.stdout}")
    return result.stdout


def adb(adb_path, args, timeout=60, check=True):
    return run([adb_path, *args], timeout=timeout, check=check)


def shell(adb_path, command, timeout=60, root=False, check=True):
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


def list_targets(adb_path, package):
    process_names = [
        package,
        f"{package}:privileged_process0",
        f"{package}:sandboxed_process0:org.chromium.content.app.SandboxedProcessService0:0",
    ]
    pid_expr = " ".join(f"$(pidof {name} 2>/dev/null)" for name in process_names)
    command = f"""
PIDS="{pid_expr}"
for p in $PIDS; do
  [ -d /proc/$p/task ] || continue
  for t in /proc/$p/task/*; do
    tid=${{t##*/}}
    comm=$(cat $t/comm 2>/dev/null)
    case "$comm" in
      .android.chrome|Chrome_IOThread|VizCompositorTh|CompositorGpuTh|Compositor|CrRendererMain|ThreadPoolForeg|mali-cmar-backe)
        cpuset=$(cat $t/cpuset 2>/dev/null)
        nice=$(awk '{{print $19}}' $t/stat 2>/dev/null)
        printf '%s\\t%s\\t%s\\t%s\\t%s\\n' "$p" "$tid" "$comm" "$cpuset" "$nice"
        ;;
    esac
  done
done
"""
    output = shell(adb_path, command, timeout=20, root=True, check=False)
    targets = []
    seen = set()
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        pid, tid, comm, cpuset, nice = parts
        if not pid.isdigit() or not tid.isdigit():
            continue
        key = (pid, tid)
        if key in seen:
            continue
        seen.add(key)
        try:
            nice_value = int(nice)
        except ValueError:
            nice_value = 0
        targets.append({
            "pid": int(pid),
            "tid": int(tid),
            "comm": comm,
            "cpuset": cpuset or "",
            "nice": nice_value,
        })
    return targets


def append_jsonl(path, obj):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def read_thread_nice(adb_path, tid):
    output = shell(
        adb_path,
        f"awk '{{print $19}}' /proc/{int(tid)}/stat 2>/dev/null",
        timeout=10,
        root=True,
        check=False,
    ).strip()
    try:
        return int(output.splitlines()[-1])
    except (IndexError, ValueError):
        return None


def set_thread_nice(adb_path, tid, nice_value):
    # Android toybox treats `renice -n` as a relative adjustment, so use the
    # absolute priority form and verify the value through /proc afterwards.
    command = f"renice {int(nice_value)} -p {int(tid)}"
    output = shell(adb_path, command, timeout=10, root=True, check=False).strip()
    observed = read_thread_nice(adb_path, tid)
    return {
        "command": command,
        "output": output,
        "observed_nice": observed,
        "ok": observed == int(nice_value),
    }


def apply_intervention_loop(adb_path, package, duration_s, log_path, target_nice, stop_event):
    originals = {}
    start = time.time()
    apply_count = 0
    apply_success = 0
    cpuset_success = 0

    append_jsonl(log_path, {
        "event": "intervention_start",
        "package": package,
        "target_nice": target_nice,
        "started_at": datetime.now().astimezone().isoformat(),
    })

    while time.time() - start < duration_s and not stop_event.is_set():
        for target in list_targets(adb_path, package):
            key = (target["pid"], target["tid"])
            originals.setdefault(key, target)
            apply_count += 1

            renice_out = ""
            renice_command = ""
            renice_after = None
            renice_ok = False
            if target["nice"] > target_nice:
                renice_result = set_thread_nice(adb_path, target["tid"], target_nice)
                renice_command = renice_result["command"]
                renice_out = renice_result["output"]
                renice_after = renice_result["observed_nice"]
                renice_ok = renice_result["ok"]
                if renice_ok:
                    apply_success += 1

            cpuset_out = ""
            cpuset_ok = False
            if target["cpuset"] != "/top-app":
                cpuset_out = shell(
                    adb_path,
                    f"echo {target['tid']} > /dev/cpuset/top-app/tasks",
                    timeout=10,
                    root=True,
                    check=False,
                ).strip()
                cpuset_ok = "Permission denied" not in cpuset_out and "No such" not in cpuset_out
                if cpuset_ok:
                    cpuset_success += 1

            append_jsonl(log_path, {
                "event": "apply_tick",
                "target": target,
                "renice_command": renice_command,
                "renice_output": renice_out,
                "renice_after": renice_after,
                "renice_ok": renice_ok,
                "cpuset_output": cpuset_out,
                "cpuset_ok": cpuset_ok,
            })
        time.sleep(0.75)

    rollback = []
    for target in originals.values():
        renice_result = set_thread_nice(adb_path, target["tid"], target["nice"])
        cpuset_out = ""
        cpuset = target.get("cpuset") or ""
        if cpuset:
            cpuset_path = "/dev/cpuset/tasks" if cpuset == "/" else f"/dev/cpuset{cpuset}/tasks"
            cpuset_out = shell(
                adb_path,
                f"test -e {shlex.quote(cpuset_path)} && echo {target['tid']} > {shlex.quote(cpuset_path)}",
                timeout=10,
                root=True,
                check=False,
            ).strip()
        rollback.append({
            "target": target,
            "renice_command": renice_result["command"],
            "renice_output": renice_result["output"],
            "renice_after": renice_result["observed_nice"],
            "renice_ok": renice_result["ok"],
            "cpuset_output": cpuset_out,
        })

    append_jsonl(log_path, {
        "event": "intervention_end",
        "finished_at": datetime.now().astimezone().isoformat(),
        "unique_targets": len(originals),
        "apply_count": apply_count,
        "renice_success_count": apply_success,
        "cpuset_success_count": cpuset_success,
        "rollback": rollback,
    })


def swipe_workload(adb_path, duration_s, stop_event, start_delay_s=2.0):
    time.sleep(start_delay_s)
    end = time.time() + max(0, duration_s - start_delay_s)
    while time.time() < end and not stop_event.is_set():
        adb(adb_path, ["shell", "input", "swipe", "540", "1950", "540", "450", "450"], timeout=10, check=False)
        time.sleep(0.7)


def run_capture(adb_path, tag, out_dir, package, duration_s, mode, target_nice):
    run_dir = out_dir / tag
    run_dir.mkdir(parents=True, exist_ok=False)
    capture_dir = run_dir / "capture"
    stop_event = threading.Event()
    workload = threading.Thread(target=swipe_workload, args=(adb_path, duration_s, stop_event), daemon=True)
    intervention_log = run_dir / f"{tag}_intervention_audit.jsonl"
    actuator = None
    if mode == "intervention":
        actuator = threading.Thread(
            target=apply_intervention_loop,
            args=(adb_path, package, duration_s, intervention_log, target_nice, stop_event),
            daemon=True,
        )

    cmd = [
        sys.executable,
        str(COLLECT_SCRIPT),
        "--adb",
        adb_path,
        "--package",
        package,
        "--duration",
        str(duration_s),
        "--tag",
        tag,
        "--out-dir",
        str(capture_dir),
        "--perfetto",
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    workload.start()
    if actuator:
        actuator.start()
    stdout, _ = proc.communicate(timeout=duration_s + 180)
    stop_event.set()
    workload.join(timeout=10)
    if actuator:
        actuator.join(timeout=30)
    (run_dir / f"{tag}_orchestrator_stdout.txt").write_text(stdout, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"capture failed for {tag}\n{stdout}")
    return capture_dir, intervention_log


def convert_frames_csv_to_tracepilot(frames_csv, frames_txt):
    with Path(frames_csv).open("r", encoding="utf-8-sig", newline="") as src, Path(frames_txt).open(
        "w", encoding="utf-8", newline=""
    ) as dst:
        reader = csv.DictReader(src)
        fieldnames = [
            "frame_number",
            "intended_vsync_ns",
            "expected_start_ns",
            "expected_end_ns",
            "actual_present_ns",
            "is_jank",
            "delay_ms",
        ]
        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()
        for idx, row in enumerate(reader):
            expected_start = int(row.get("expected_start_ns") or 0)
            expected_end = int(row.get("expected_end_ns") or 0)
            actual_end = int(row.get("actual_end_ns") or 0)
            token = row.get("frame_token") or str(idx)
            deadline = str(row.get("deadline_missed", "")).lower() in {"1", "true", "yes"}
            writer.writerow({
                "frame_number": token,
                "intended_vsync_ns": expected_start,
                "expected_start_ns": expected_start,
                "expected_end_ns": expected_end,
                "actual_present_ns": actual_end,
                "is_jank": 1 if deadline else 0,
                "delay_ms": round((actual_end - expected_end) / 1e6, 6),
            })


def analyze_run(run_dir, tag, package, trace_processor):
    trace = run_dir / f"{tag}.perfetto-trace"
    frames_csv = run_dir / f"{tag}_perfetto_frametimeline_frames.csv"
    ft_summary = run_dir / f"{tag}_perfetto_frametimeline_summary.json"
    sched_summary = run_dir / f"{tag}_perfetto_sched_summary.json"
    cpu_summary = run_dir / f"{tag}_perfetto_cpu_freq_summary.json"
    frames_txt = run_dir / f"{tag}_frames.txt"

    if not trace.exists() or trace.stat().st_size == 0:
        return {"tag": tag, "analysis_error": "missing perfetto trace"}

    run(
        [
            sys.executable,
            str(PARSE_FRAMES),
            str(trace),
            "--package",
            package,
            "--trace-processor",
            str(trace_processor),
            "--csv-out",
            str(frames_csv),
            "--summary-out",
            str(ft_summary),
        ],
        timeout=300,
    )
    run(
        [
            sys.executable,
            str(ANALYZE_SCHED),
            str(trace),
            "--frames-csv",
            str(frames_csv),
            "--trace-processor",
            str(trace_processor),
            "--frame-thread-csv-out",
            str(run_dir / f"{tag}_perfetto_frame_thread_sched.csv"),
            "--thread-summary-csv-out",
            str(run_dir / f"{tag}_perfetto_thread_sched_summary.csv"),
            "--frame-summary-csv-out",
            str(run_dir / f"{tag}_perfetto_frame_sched_summary.csv"),
            "--summary-out",
            str(sched_summary),
        ],
        timeout=300,
    )
    run(
        [
            sys.executable,
            str(ANALYZE_CPU),
            str(trace),
            "--frames-csv",
            str(frames_csv),
            "--trace-processor",
            str(trace_processor),
            "--frame-cluster-csv-out",
            str(run_dir / f"{tag}_perfetto_frame_cpu_freq.csv"),
            "--thread-cluster-csv-out",
            str(run_dir / f"{tag}_perfetto_thread_cpu_cluster.csv"),
            "--summary-out",
            str(cpu_summary),
        ],
        timeout=300,
    )
    convert_frames_csv_to_tracepilot(frames_csv, frames_txt)

    summary = json.loads(ft_summary.read_text(encoding="utf-8"))
    sched = json.loads(sched_summary.read_text(encoding="utf-8"))
    cpu = json.loads(cpu_summary.read_text(encoding="utf-8"))
    top_threads = sched.get("top_threads", [])[:6]
    summary.update({
        "top_threads": [
            {
                "rank": item.get("rank"),
                "comm": item.get("comm"),
                "on_cpu_ms": item.get("on_cpu_ms"),
                "runnable_wait_ms": item.get("runnable_wait_ms"),
                "runnable_wait_p95_ms": item.get("runnable_wait_p95_ms"),
            }
            for item in top_threads
        ],
        "cpu_cluster_summary": cpu.get("cluster_summary", cpu.get("clusters", {})),
    })
    return summary


def summarize_intervention_log(path):
    if not path.exists():
        return {"exists": False}
    end_event = {}
    apply_ticks = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event") == "apply_tick":
                apply_ticks += 1
            elif obj.get("event") == "intervention_end":
                end_event = obj
    return {
        "exists": True,
        "apply_ticks": apply_ticks,
        "unique_targets": end_event.get("unique_targets", 0),
        "renice_success_count": end_event.get("renice_success_count", 0),
        "cpuset_success_count": end_event.get("cpuset_success_count", 0),
    }


def aggregate(rows):
    grouped = {}
    for row in rows:
        mode = row["mode"]
        grouped.setdefault(mode, []).append(row)
    result = {}
    for mode, items in grouped.items():
        result[mode] = {"runs": len(items)}
        for key in [
            "frame_count",
            "deadline_missed_count",
            "deadline_missed_rate",
            "frame_time_p95_ms",
            "frame_time_p99_ms",
        ]:
            values = [item.get(key, 0) for item in items if isinstance(item.get(key), (int, float))]
            result[mode][f"avg_{key}"] = round(sum(values) / len(values), 4) if values else 0
    if {"baseline", "intervention"} <= set(result):
        b = result["baseline"]
        i = result["intervention"]
        result["delta_intervention_minus_baseline"] = {
            "deadline_missed_rate": round(i["avg_deadline_missed_rate"] - b["avg_deadline_missed_rate"], 4),
            "frame_time_p95_ms": round(i["avg_frame_time_p95_ms"] - b["avg_frame_time_p95_ms"], 4),
            "frame_time_p99_ms": round(i["avg_frame_time_p99_ms"] - b["avg_frame_time_p99_ms"], 4),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description="Run feed_scroll baseline vs intervention captures.")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.android.chrome")
    parser.add_argument("--duration", type=int, default=20)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--target-nice", type=int, default=-10)
    parser.add_argument("--trace-processor", default=str(DEFAULT_TRACE_PROCESSOR))
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    if args.duration < 8 or args.duration > 60:
        raise SystemExit("--duration must be between 8 and 60 seconds")
    if args.repetitions < 1 or args.repetitions > 5:
        raise SystemExit("--repetitions must be between 1 and 5")
    trace_processor = Path(args.trace_processor)
    if not trace_processor.exists():
        raise SystemExit(f"trace_processor not found: {trace_processor}")

    devices = adb(args.adb, ["devices"], timeout=30)
    if "\tdevice" not in devices:
        raise SystemExit("No adb device is online.")
    root_check = shell(args.adb, "id", timeout=30, root=True)
    if "uid=0" not in root_check:
        raise SystemExit("The connected device is not providing root through su.")
    active = foreground_package(args.adb)
    if active != args.package:
        raise SystemExit(f"Foreground app is {active!r}, not requested package {args.package!r}. Open Chrome first.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "ebpf_data" / "feed_scroll" / f"feed_scroll_intervention_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=False)

    rows = []
    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "package": args.package,
        "duration_s": args.duration,
        "repetitions": args.repetitions,
        "target_nice": args.target_nice,
        "trace_processor": str(trace_processor),
        "runs": [],
        "actuator_note": (
            "Uses repeated Chrome render-thread absolute renice/top-app cpuset guard during the capture window. "
            "SurfaceFlinger sched_boost/uclamp/taskset/chrt are not assumed available."
        ),
    }

    for rep in range(1, args.repetitions + 1):
        for mode in ["baseline", "intervention"]:
            tag = f"{out_dir.name}_{mode}_{rep}"
            print(f"== {tag} ==", flush=True)
            run_dir, intervention_log = run_capture(args.adb, tag, out_dir, args.package, args.duration, mode, args.target_nice)
            analysis = analyze_run(run_dir, tag, args.package, trace_processor)
            intervention = summarize_intervention_log(intervention_log)
            row = {
                "tag": tag,
                "mode": mode,
                "run_dir": str(run_dir.relative_to(REPO)),
                **analysis,
                "intervention": intervention,
            }
            rows.append(row)
            manifest["runs"].append(row)
            (out_dir / "experiment_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    manifest["aggregate"] = aggregate(rows)
    (out_dir / "experiment_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "experiment_summary.json").write_text(json.dumps(manifest["aggregate"], ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = out_dir / "experiment_summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "tag",
            "mode",
            "frame_count",
            "deadline_missed_count",
            "deadline_missed_rate",
            "frame_time_p95_ms",
            "frame_time_p99_ms",
            "intervention_unique_targets",
            "intervention_renice_success_count",
            "intervention_cpuset_success_count",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            intervention = row.get("intervention", {})
            writer.writerow({
                "tag": row.get("tag"),
                "mode": row.get("mode"),
                "frame_count": row.get("frame_count"),
                "deadline_missed_count": row.get("deadline_missed_count"),
                "deadline_missed_rate": row.get("deadline_missed_rate"),
                "frame_time_p95_ms": row.get("frame_time_p95_ms"),
                "frame_time_p99_ms": row.get("frame_time_p99_ms"),
                "intervention_unique_targets": intervention.get("unique_targets", 0),
                "intervention_renice_success_count": intervention.get("renice_success_count", 0),
                "intervention_cpuset_success_count": intervention.get("cpuset_success_count", 0),
            })

    readme = out_dir / "README.md"
    readme.write_text(
        "# feed_scroll baseline vs intervention experiment\n\n"
        f"- package: `{args.package}`\n"
        f"- duration per run: {args.duration}s\n"
        f"- repetitions: {args.repetitions} baseline + {args.repetitions} intervention\n"
        f"- actuator: Chrome render-thread absolute renice/top-app cpuset guard, target nice {args.target_nice}\n\n"
        "Primary outputs:\n\n"
        "- `experiment_manifest.json`: per-run metadata, metrics, actuator audit summary\n"
        "- `experiment_summary.json`: aggregate baseline/intervention comparison\n"
        "- `experiment_summary.csv`: compact table for reports\n\n"
        "Raw `.bin`, `.perfetto-trace`, and generated CSV files should follow the repository raw-artifact policy.\n",
        encoding="utf-8",
    )

    print(json.dumps(manifest["aggregate"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
