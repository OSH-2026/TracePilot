#!/usr/bin/env python3
"""
Validate the SGame package resolver / foreground guard before any real hint apply.

This is intentionally conservative: it proves whether the connected device can
identify com.tencent.tmgp.sgame as the foreground package and whether TracePilot
should be invoked with an explicit -p guard. If requested and the game is in the
foreground, it performs a short aligned capture as a smoke sample.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
COLLECT_SCRIPT = ROOT / "scripts" / "collect_game_aligned.py"
PARSE_FRAMES = ROOT / "scripts" / "parse_perfetto_frametimeline.py"
DEFAULT_TRACE_PROCESSOR = Path("/private/tmp/trace_processor")


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
            return match.group(1), output
    return "", output


def package_uid(adb_path, package):
    output = shell(adb_path, f"pm list packages -U | grep -F {shlex.quote(package)}", timeout=30, check=False)
    match = re.search(r"uid:(\d+)", output)
    return int(match.group(1)) if match else None, output.strip()


def pid_snapshot(adb_path, package):
    command = f"""
for p in $(pidof {package} 2>/dev/null); do
  cmd=$(tr '\\0' ' ' < /proc/$p/cmdline 2>/dev/null)
  uid=$(awk '/^Uid:/ {{print $2}}' /proc/$p/status 2>/dev/null)
  printf '%s\\t%s\\t%s\\n' "$p" "$uid" "$cmd"
done
"""
    output = shell(adb_path, command, timeout=30, root=True, check=False)
    rows = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        rows.append({"pid": int(parts[0]), "uid": parts[1], "cmdline": parts[2].strip()})
    return rows


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Validate SGame foreground/package guard and optional short capture.")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--package", default="com.tencent.tmgp.sgame")
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--launch", action="store_true", help="Launch the package with monkey before checking foreground.")
    parser.add_argument("--capture", action="store_true", help="Run a short aligned capture if the package is foreground.")
    parser.add_argument("--trace-processor", default=str(DEFAULT_TRACE_PROCESSOR))
    args = parser.parse_args()

    devices = adb(args.adb, ["devices"], timeout=30)
    if "\tdevice" not in devices:
        raise SystemExit("No adb device is online.")
    root_check = shell(args.adb, "id", timeout=30, root=True)
    if "uid=0" not in root_check:
        raise SystemExit("The connected device is not providing root through su.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "ebpf_data" / "game_sgame" / f"sgame_resolver_smoke_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=False)

    uid, pm_output = package_uid(args.adb, args.package)
    if args.launch:
        shell(args.adb, f"monkey -p {shlex.quote(args.package)} 1", timeout=30, check=False)
        time.sleep(8)

    foreground, window_dump = foreground_package(args.adb)
    pids = pid_snapshot(args.adb, args.package)

    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "package": args.package,
        "package_installed": uid is not None,
        "package_uid": uid,
        "pm_output": pm_output,
        "foreground_package": foreground,
        "foreground_matches_target": foreground == args.package,
        "pid_snapshot": pids,
        "explicit_tracepilot_guard": f"-p {args.package}",
        "hint_guard_status": "ready" if foreground == args.package and uid is not None else "blocked_foreground_or_install",
        "notes": [
            "Use the explicit -p package guard for any future SGame replay/intervention.",
            "Do not apply hints if foreground_package differs from the requested package.",
        ],
    }
    (out_dir / "window_dump.txt").write_text(window_dump, encoding="utf-8", errors="replace")

    if args.capture and report["hint_guard_status"] == "ready":
        tag = out_dir.name
        capture_dir = out_dir / tag
        cmd = [
            sys.executable,
            str(COLLECT_SCRIPT),
            "--adb",
            args.adb,
            "--package",
            args.package,
            "--duration",
            str(args.duration),
            "--tag",
            tag,
            "--out-dir",
            str(capture_dir),
            "--perfetto",
        ]
        result = run(cmd, timeout=args.duration + 180, check=False)
        (out_dir / "capture_stdout.txt").write_text(result, encoding="utf-8")
        report["capture"] = {
            "attempted": True,
            "tag": tag,
            "path": str(capture_dir.relative_to(REPO)),
            "stdout_path": "capture_stdout.txt",
            "success": "trace_file" in result or (capture_dir / f"{tag}_host_metadata.json").exists(),
        }
        trace = capture_dir / f"{tag}.perfetto-trace"
        if trace.exists() and Path(args.trace_processor).exists():
            frames_csv = capture_dir / f"{tag}_perfetto_frametimeline_frames.csv"
            summary_json = capture_dir / f"{tag}_perfetto_frametimeline_summary.json"
            parse_out = run(
                [
                    sys.executable,
                    str(PARSE_FRAMES),
                    str(trace),
                    "--package",
                    args.package,
                    "--trace-processor",
                    args.trace_processor,
                    "--csv-out",
                    str(frames_csv),
                    "--summary-out",
                    str(summary_json),
                ],
                timeout=300,
                check=False,
            )
            report["capture"]["frametimeline_parse_stdout"] = parse_out[-4000:]
            if summary_json.exists():
                report["capture"]["frametimeline_summary"] = json.loads(summary_json.read_text(encoding="utf-8"))
    else:
        report["capture"] = {
            "attempted": bool(args.capture),
            "success": False,
            "reason": "target package is not foreground or package is not installed",
        }

    write_json(out_dir / "sgame_resolver_guard_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
