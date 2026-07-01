#!/usr/bin/env python3
"""
package_game_raw_data.py — 游戏原始数据打包归档
将游戏场景采集的 Perfetto trace + eBPF events + 元数据打包为 zip，
生成 SHA256 校验和用于数据完整性验证。

用法:
  python package_game_raw_data.py <data_dir> <output_package.zip>
"""
import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


RAW_FILE_ROLES = {
    "{tag}_events.bin": "raw_tracepilot_events_bin",
    "{tag}.perfetto-trace": "raw_perfetto_trace",
    "{tag}_tracepilot_stdout.txt": "raw_tracepilot_debug_stdout",
    "{tag}_device_stdout.txt": "raw_capture_stdout",
    "{tag}_host_metadata.json": "capture_host_metadata",
    "{tag}_metadata.txt": "capture_device_metadata",
    "{tag}_perfetto.pbtx": "perfetto_config_used",
    "{tag}_ftrace.txt": "raw_ftrace_snapshot",
    "{tag}_framestats.txt": "raw_dumpsys_gfxinfo_framestats",
    "{tag}_surfaceflinger_layer.txt": "surfaceflinger_layer_name",
    "{tag}_surfaceflinger_latency.txt": "raw_surfaceflinger_latency",
    "{tag}_frames.txt": "tracepilot_replay_frame_input",
}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_relative(path):
    path = Path(path)
    try:
        return str(path.relative_to(Path.cwd())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def build_manifest(dataset_dir, tag, files):
    entries = []
    for path, role in files:
        rel = path.relative_to(dataset_dir)
        entries.append({
            "path": str(rel).replace("\\", "/"),
            "role": role,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return {
        "tag": tag,
        "relative_dataset_dir": repo_relative(dataset_dir),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Packaged raw/replay inputs for the game_sgame Step1/Step2 analysis. Large loose files should stay out of git.",
        "report_path": "doc/report/sgame_gameplay_analysis_report.md",
        "files": entries,
        "analysis_outputs": [
            f"{tag}_perfetto_frametimeline_summary.json",
            f"{tag}_perfetto_frametimeline_frames.csv",
            f"{tag}_perfetto_sched_summary.json",
            f"{tag}_perfetto_thread_sched_summary.csv",
            f"{tag}_perfetto_frame_sched_summary.csv",
            f"{tag}_perfetto_frame_thread_sched.csv",
            f"{tag}_perfetto_cpu_freq_summary.json",
            f"{tag}_perfetto_frame_cpu_freq.csv",
            f"{tag}_perfetto_thread_cpu_cluster.csv",
            f"{tag}_tracepilot_enhanced_events_summary.json",
            f"{tag}_tracepilot_enhanced_events.csv",
            f"{tag}_step1_summary.json",
            f"{tag}_step2_summary.json",
        ],
        "replay_commands": {
            "optional_tracepilot_offline_device_cleanup": (
                "python ebpf/scripts/run_tracepilot_offline_device.py "
                f"ebpf/ebpf_data/game_sgame/{tag} --tag {tag} "
                "--package com.tencent.tmgp.sgame --adb D:/platform-tools/adb.exe"
            ),
            "perfetto_frametimeline": (
                "python ebpf/scripts/parse_perfetto_frametimeline.py "
                f"{tag}.perfetto-trace --package com.tencent.tmgp.sgame "
                f"--csv-out {tag}_perfetto_frametimeline_frames.csv "
                f"--summary-out {tag}_perfetto_frametimeline_summary.json"
            ),
            "perfetto_sched_windows": (
                "python ebpf/scripts/analyze_perfetto_sched_windows.py "
                f"{tag}.perfetto-trace --frames-csv {tag}_perfetto_frametimeline_frames.csv "
                f"--frame-thread-csv-out {tag}_perfetto_frame_thread_sched.csv "
                f"--thread-summary-csv-out {tag}_perfetto_thread_sched_summary.csv "
                f"--frame-summary-csv-out {tag}_perfetto_frame_sched_summary.csv "
                f"--summary-out {tag}_perfetto_sched_summary.json"
            ),
            "perfetto_cpu_freq_windows": (
                "python ebpf/scripts/analyze_perfetto_cpu_freq_windows.py "
                f"{tag}.perfetto-trace --frames-csv {tag}_perfetto_frametimeline_frames.csv "
                f"--frame-cluster-csv-out {tag}_perfetto_frame_cpu_freq.csv "
                f"--thread-cluster-csv-out {tag}_perfetto_thread_cpu_cluster.csv "
                f"--summary-out {tag}_perfetto_cpu_freq_summary.json"
            ),
            "tracepilot_enhanced_events": (
                "python ebpf/scripts/extract_tracepilot_enhanced_events.py "
                f"{tag}_tracepilot_stdout.txt "
                f"--summary-out {tag}_tracepilot_enhanced_events_summary.json "
                f"--csv-out {tag}_tracepilot_enhanced_events.csv"
            ),
        },
        "known_limitations": [
            "The current result.json/hints.json were generated before forcing -p com.tencent.tmgp.sgame and still contain a TracePilot auto resolver package mismatch; the core submission uses capture metadata and derived summaries instead.",
            "Step2 Binder/Futex/Jank cause outputs are candidate observational evidence, not intervention-proven causality.",
            "Perfetto FrameTimeline used all_frametimeline_rows_fallback because the package filter did not match process names in this trace.",
        ],
        "do_not_commit_raw_files": [
            "*_events.bin",
            "*.perfetto-trace",
            "*_tracepilot_stdout.txt",
            "*_device_stdout.txt",
            "*_ftrace.txt",
            "*_framestats.txt",
            "*_surfaceflinger_layer.txt",
            "*_surfaceflinger_latency.txt",
        ],
        "archive_policy": (
            "The zip package is the submission-friendly raw replay artifact. If the target platform rejects files around 233 MB, "
            "place the zip in an external artifact store and commit only this manifest plus analysis outputs."
        ),
        "tool_versions": {
            "trace_processor_shell": "Perfetto trace_processor_shell from ebpf/dependencies/",
            "adb": "D:/platform-tools/adb.exe during capture",
            "device": "Pixel 6a rooted via Magisk, per capture metadata/root checks",
        },
        "notes": [
            "Analysis CSV/JSON summaries are intentionally not duplicated in this raw package.",
            "Use *_frames.txt with *_events.bin to replay TracePilot offline analysis.",
            "Use the .perfetto-trace with trace_processor_shell to regenerate FrameTimeline, sched, and CPU-frequency derived outputs.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Package large raw game capture files and emit a manifest.")
    parser.add_argument("dataset_dir")
    parser.add_argument("--tag", default="", help="Defaults to dataset directory name.")
    parser.add_argument("--out-dir", default="", help="Defaults to <game_sgame>/raw_packages.")
    parser.add_argument("--compression-level", type=int, default=6)
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir).resolve()
    tag = args.tag or dataset_dir.name
    out_dir = Path(args.out_dir).resolve() if args.out_dir else dataset_dir.parent / "raw_packages"
    out_dir.mkdir(parents=True, exist_ok=True)

    selected = []
    missing = []
    for template, role in RAW_FILE_ROLES.items():
        path = dataset_dir / template.format(tag=tag)
        if path.exists():
            selected.append((path, role))
        else:
            missing.append(path.name)
    if not selected:
        raise SystemExit(f"No raw files matched tag {tag!r} in {dataset_dir}")

    manifest = build_manifest(dataset_dir, tag, selected)
    manifest["missing_optional_files"] = missing

    archive_path = out_dir / f"{tag}_raw_replay_package.zip"
    manifest_path = out_dir / f"{tag}_raw_replay_manifest.json"
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=args.compression_level,
        allowZip64=True,
    ) as archive:
        for path, _role in selected:
            archive.write(path, arcname=f"{tag}/{path.name}")
        archive.writestr(
            f"{tag}/{tag}_raw_replay_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    manifest["archive"] = {
        "path": repo_relative(archive_path),
        "size_bytes": archive_path.stat().st_size,
        "sha256": sha256_file(archive_path),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "archive": repo_relative(archive_path),
        "archive_size_bytes": archive_path.stat().st_size,
        "manifest": repo_relative(manifest_path),
        "file_count": len(selected),
        "missing_optional_files": missing,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
