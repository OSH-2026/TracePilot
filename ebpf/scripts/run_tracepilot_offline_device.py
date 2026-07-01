#!/usr/bin/env python3
"""
run_tracepilot_offline_device.py — 离线设备端 TracePilot 运行器
在宿主机上通过 adb 远程控制 Pixel 6a 设备端 tracepilot 采集流程，
自动完成 Perfetto 启动→eBPF 采集→数据拉取的全流程编排。

用法:
  python run_tracepilot_offline_device.py --scenario page_switch --duration 30
"""
import argparse
import json
import subprocess
from pathlib import Path


def run(cmd, timeout=60, check=True):
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(map(str, cmd))}\n{result.stdout}")
    return result.stdout


def adb(adb_path, args, timeout=60, check=True):
    return run([str(adb_path), *args], timeout=timeout, check=check)


def shell(adb_path, command, timeout=60, root=True, check=True):
    if root:
        return adb(adb_path, ["shell", "su", "-c", command], timeout=timeout, check=check)
    return adb(adb_path, ["shell", command], timeout=timeout, check=check)


def main():
    parser = argparse.ArgumentParser(
        description="Run the device TracePilot binary in offline mode with an explicit package guard."
    )
    parser.add_argument("dataset_dir")
    parser.add_argument("--tag", default="", help="Defaults to dataset directory name.")
    parser.add_argument("--package", default="com.tencent.tmgp.sgame")
    parser.add_argument("--scenario", default="page_switch")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--remote-dir", default="/data/local/tmp/tracepilot_offline")
    parser.add_argument("--tracepilot", default="/data/local/tmp/tracepilot")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir)
    tag = args.tag or dataset_dir.name
    events = dataset_dir / f"{tag}_events.bin"
    frames = dataset_dir / f"{tag}_frames.txt"
    if not events.exists():
        raise SystemExit(f"events.bin not found: {events}")
    if not frames.exists():
        raise SystemExit(f"frames.txt not found: {frames}")

    adb_path = Path(args.adb) if args.adb != "adb" else args.adb
    devices = adb(adb_path, ["devices"], timeout=30)
    if "\tdevice" not in devices:
        raise SystemExit("No adb device is online.")
    root_check = shell(adb_path, "id", timeout=30)
    if "uid=0" not in root_check:
        raise SystemExit("The connected device is not providing root through su.")

    remote_dir = args.remote_dir.rstrip("/")
    shell(adb_path, f"mkdir -p {remote_dir}", timeout=30)
    remote_events = f"{remote_dir}/{tag}_events.bin"
    remote_frames = f"{remote_dir}/{tag}_frames.txt"
    remote_result = f"{remote_dir}/{tag}_result.json"
    remote_graph = f"{remote_dir}/{tag}_graph_topology.json"
    remote_dot = f"{remote_dir}/{tag}_graph_subgraph.dot"
    remote_hints = f"{remote_dir}/{tag}_hints.json"

    adb(adb_path, ["push", str(events), remote_events], timeout=600)
    adb(adb_path, ["push", str(frames), remote_frames], timeout=120)

    command = (
        f"{args.tracepilot} "
        f"-p {args.package} "
        f"-s {args.scenario} "
        f"-i {remote_events} "
        f"-f {remote_frames} "
        f"-o {remote_result} "
        f"-G -k {args.top_k} "
        f"--graph-json {remote_graph} "
        f"--graph-dot {remote_dot} "
        f"--hints-json {remote_hints}"
    )
    stdout = shell(adb_path, command, timeout=900)
    (dataset_dir / f"{tag}_tracepilot_offline_stdout.txt").write_text(stdout, encoding="utf-8")

    pulled = {}
    for remote, suffix in [
        (remote_result, "_result.json"),
        (remote_graph, "_graph_topology.json"),
        (remote_dot, "_graph_subgraph.dot"),
        (remote_hints, "_hints.json"),
    ]:
        local = dataset_dir / f"{tag}{suffix}"
        output = adb(adb_path, ["pull", remote, str(local)], timeout=240, check=False)
        pulled[local.name] = {
            "exists": local.exists(),
            "size_bytes": local.stat().st_size if local.exists() else 0,
            "pull_output": output.strip(),
        }

    summary = {
        "tag": tag,
        "package": args.package,
        "scenario": args.scenario,
        "remote_dir": remote_dir,
        "command": command,
        "pulled": pulled,
        "note": "Offline TracePilot was run with an explicit -p package to avoid auto target resolver pollution.",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
