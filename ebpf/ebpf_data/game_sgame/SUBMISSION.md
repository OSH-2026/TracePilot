# Game SGame Submission Notes

This directory contains the game-scene Step1/Step2 analysis artifacts for
`game_match_sgame_20260607_170754`.

## Primary Dataset

- Dataset directory: `game_match_sgame_20260607_170754/`
- Target package: `com.tencent.tmgp.sgame`
- Capture window: 60 seconds
- Raw replay package:
  - `raw_packages/game_match_sgame_20260607_170754_raw_replay_package.zip`
  - Size: 233,540,901 bytes
  - SHA256: `44e3a3ed24f7c352e0bbdf5cf55d042dbccf293503edff216d8f044ca538f2a6`
- Raw replay manifest:
  - `raw_packages/game_match_sgame_20260607_170754_raw_replay_manifest.json`

The loose raw files in the dataset directory are intentionally ignored by git:
`*_events.bin`, `*.perfetto-trace`, `*_tracepilot_stdout.txt`, and small raw
capture side files are already covered by the raw replay package and should not
be submitted as separate files.

## Recommended Files To Submit

Submit these repository files/directories for the current game-scene Step1/Step2
sample:

- `doc/report/sgame_gameplay_analysis_report.md`
- `README.md`
- `.gitignore`
- `ebpf/scripts/collect_game_aligned.py`
- `ebpf/scripts/android_game_aligned_capture.sh`
- `ebpf/scripts/parse_perfetto_frametimeline.py`
- `ebpf/scripts/analyze_perfetto_sched_windows.py`
- `ebpf/scripts/analyze_perfetto_cpu_freq_windows.py`
- `ebpf/scripts/extract_tracepilot_enhanced_events.py`
- `ebpf/scripts/build_tracepilot_offline_step_summary.py`
- `ebpf/scripts/run_tracepilot_offline_device.py`
- `ebpf/scripts/package_game_raw_data.py`
- `ebpf/src/camera/perfetto/perfetto_game_frametimeline.pbtx`
- `ebpf/ebpf_data/game_sgame/SUBMISSION.md`
- `ebpf/ebpf_data/game_sgame/raw_packages/game_match_sgame_20260607_170754_raw_replay_manifest.json`
- `ebpf/ebpf_data/game_sgame/raw_packages/game_match_sgame_20260607_170754_raw_replay_package.zip`

Keep these analysis artifacts from `game_match_sgame_20260607_170754/`
available for review:

- `*_host_metadata.json`
- `*_metadata.txt`
- `*_perfetto.pbtx`
- `*_perfetto_frametimeline_summary.json`
- `*_perfetto_frametimeline_frames.csv`
- `*_perfetto_sched_summary.json`
- `*_perfetto_thread_sched_summary.csv`
- `*_perfetto_frame_sched_summary.csv`
- `*_perfetto_frame_thread_sched.csv`
- `*_perfetto_cpu_freq_summary.json`
- `*_perfetto_thread_cpu_cluster.csv`
- `*_perfetto_frame_cpu_freq.csv`
- `*_tracepilot_enhanced_events_summary.json`
- `*_tracepilot_enhanced_events.csv`
- `*_step1_summary.json`
- `*_step2_summary.json`

These TracePilot graph artifacts are optional review aids. They are kept because
they show the current TracePilot graph schema, but the core Step1/Step2
submission does not depend on them because of the package auto-detection
mismatch described below:

- `*_result.json`
- `*_graph_topology.json`
- `*_graph_subgraph.dot`
- `*_hints.json`

The report entry point is:

- `doc/report/sgame_gameplay_analysis_report.md`

The earlier `20260601` derived CSV/JSON files in `ebpf/ebpf_data/game_sgame/`
are historical comparison outputs, not required for the current 2026-06-07
submission unless the reviewer asks for the older baseline.

## Do Not Submit As Loose Files

Do not submit these loose raw files from `game_match_sgame_20260607_170754/`;
they are already represented in the raw replay package and manifest:

- `game_match_sgame_20260607_170754_events.bin`
- `game_match_sgame_20260607_170754.perfetto-trace`
- `game_match_sgame_20260607_170754_tracepilot_stdout.txt`
- `game_match_sgame_20260607_170754_device_stdout.txt`
- `game_match_sgame_20260607_170754_ftrace.txt`
- `game_match_sgame_20260607_170754_framestats.txt`
- `game_match_sgame_20260607_170754_surfaceflinger_layer.txt`
- `game_match_sgame_20260607_170754_surfaceflinger_latency.txt`

## Raw Package Contents

The raw replay package contains:

- `*_events.bin`: TracePilot raw event stream
- `*.perfetto-trace`: raw Perfetto trace
- `*_tracepilot_stdout.txt`: TracePilot debug stdout used to recover ENH Binder/Futex candidates
- `*_frames.txt`: frame input for TracePilot offline replay
- `*_perfetto.pbtx`: Perfetto config
- capture metadata and small raw dumpsys/ftrace side files

Use the manifest for per-file SHA256 checks.

If the submission platform rejects a single file around 233 MB, put the zip in
an external artifact store and submit only the manifest plus analysis outputs.

## Known Limitation And Optional Cleanup

The current `*_result.json` and `*_hints.json` were produced before the offline
TracePilot command was forced to use `-p com.tencent.tmgp.sgame`, so they still
show the auto-detected package `com.luna.music`. The report and summaries treat
that field as invalid and use host metadata as authoritative.

This does not block the current submission package. If a clean TracePilot-native
`result.json` / `hints.json` pair is needed later, regenerate those files with:

```powershell
& 'C:\Users\LEGION\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  ebpf\scripts\run_tracepilot_offline_device.py `
  ebpf\ebpf_data\game_sgame\game_match_sgame_20260607_170754 `
  --tag game_match_sgame_20260607_170754 `
  --package com.tencent.tmgp.sgame `
  --adb D:\platform-tools\adb.exe
```

No new gameplay capture is required for that optional replay step.
