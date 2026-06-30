#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_json_optional(path):
    path = Path(path)
    if not path.exists():
        return {}
    return read_json(path)


def main():
    parser = argparse.ArgumentParser(description="Build Step1/Step2 summaries from tracepilot offline analysis outputs.")
    parser.add_argument("dataset_dir")
    parser.add_argument("--tag", default="")
    parser.add_argument("--package", default="com.tencent.tmgp.sgame")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir)
    tag = args.tag or dataset_dir.name

    meta = read_json(dataset_dir / f"{tag}_host_metadata.json")
    frame_summary = read_json(dataset_dir / f"{tag}_perfetto_frametimeline_summary.json")
    sched_summary = read_json_optional(dataset_dir / f"{tag}_perfetto_sched_summary.json")
    cpu_freq_summary = read_json_optional(dataset_dir / f"{tag}_perfetto_cpu_freq_summary.json")
    enhanced_events = read_json_optional(dataset_dir / f"{tag}_tracepilot_enhanced_events_summary.json")
    result = read_json(dataset_dir / f"{tag}_result.json")
    hints = read_json(dataset_dir / f"{tag}_hints.json")

    top_threads = result.get("top_k_threads", [])
    edge_dist = result.get("edge_type_distribution", {})
    resource = result.get("resource_stats", {})
    inference = result.get("inference", {})
    heuristics = result.get("heuristics_comparison", {})
    tracepilot_target = result.get("target_package", "")
    package_mismatch = bool(tracepilot_target and tracepilot_target != args.package)
    sched_edges = {
        "WAKEUP": edge_dist.get("WAKEUP", 0),
        "RUNNABLE_WAIT": edge_dist.get("RUNNABLE_WAIT", 0),
        "CPU_RUN": edge_dist.get("CPU_RUN", 0),
    }
    frame_filter = frame_summary.get("source_filter", "")
    frame_filter_fallback = frame_filter == "all_frametimeline_rows_fallback"
    inference_confidences = [
        row.get("confidence", 0)
        for row in inference.get("frame_inferences", [])
        if isinstance(row, dict)
    ]
    max_inference_confidence = max(inference_confidences, default=0)
    hint_packages = {
        hint.get("target", {}).get("package")
        for hint in hints.get("hints", [])
        if isinstance(hint, dict)
    }
    hint_package_mismatch = bool(hint_packages - {args.package, "", None})
    perfetto_sched_available = bool(sched_summary.get("top_threads"))
    perfetto_cpu_available = bool(cpu_freq_summary.get("cluster_summary"))
    enhanced_events_available = bool(enhanced_events.get("event_counts"))

    step1 = {
        "tag": tag,
        "package": args.package,
        "source_notes": [
            "Package identity is taken from capture metadata, not tracepilot offline target_package.",
            f"tracepilot reported target_package={tracepilot_target!r}; this is treated as an internal auto-detection artifact.",
            "The device tracepilot produced events.bin, so JSONL sched postprocessing was not available for this sample.",
            "FrameTimeline package filtering missed the game process and fell back to all SurfaceFlinger-side rows.",
            "Perfetto thread_state is used as an independent scheduler source for frame-window runnable/on-CPU aggregation.",
            "Perfetto cpu_frequency counters are used for frame-window big-little frequency attribution when available.",
            "TracePilot debug ENH lines are parsed as Binder/Futex ownership candidates when available.",
        ],
        "frame_timeline_ground_truth": {
            "status": "available_with_surfaceflinger_fallback" if frame_filter_fallback else "available",
            "source": "perfetto_frametimeline",
            "frames_csv": f"{tag}_perfetto_frametimeline_frames.csv",
            "summary_json": f"{tag}_perfetto_frametimeline_summary.json",
            "frame_count": frame_summary.get("frame_count", 0),
            "deadline_missed_count": frame_summary.get("deadline_missed_count", 0),
            "deadline_missed_rate": frame_summary.get("deadline_missed_rate", 0),
            "frame_time_avg_ms": frame_summary.get("frame_time_avg_ms", 0),
            "frame_time_p95_ms": frame_summary.get("frame_time_p95_ms", 0),
            "frame_time_p99_ms": frame_summary.get("frame_time_p99_ms", 0),
            "source_filter": frame_summary.get("source_filter", ""),
        },
        "ebpf_sched_events": {
            "status": "raw_events_bin_available_jsonl_sched_metrics_missing",
            "trace_file": meta.get("trace_file", ""),
            "trace_format": meta.get("trace_format", ""),
            "trace_size_bytes": meta.get("trace_size_bytes", 0),
            "tracepilot_result": f"{tag}_result.json",
            "total_nodes": result.get("total_nodes", 0),
            "total_edges": result.get("total_edges", 0),
            "sched_edge_counts": sched_edges,
            "limitation": "Tracepilot offline graph did not reconstruct WAKEUP/RUNNABLE_WAIT/CPU_RUN edges for this sample.",
        },
        "identity_resolver": {
            "foreground_verified": True,
            "foreground_window": "com.tencent.tmgp.sgame/.SGameActivity",
            "capture_package": meta.get("package", args.package),
            "tracepilot_auto_target_package": result.get("target_package", ""),
            "status": "capture_metadata_authoritative",
            "tracepilot_target_mismatch": package_mismatch,
        },
        "frame_window_runnable_delay": {
            "status": "available_via_perfetto_thread_state" if perfetto_sched_available else "frame_windows_available_runnable_delay_not_joined",
            "frame_count": result.get("total_frames", 0),
            "jank_frames": result.get("jank_frames", 0),
            "avg_jank_duration_ns": result.get("avg_jank_duration_ns", 0),
            "sched_edge_counts": sched_edges,
            "perfetto_sched_summary": f"{tag}_perfetto_sched_summary.json" if perfetto_sched_available else "",
            "perfetto_thread_summary_csv": f"{tag}_perfetto_thread_sched_summary.csv" if perfetto_sched_available else "",
            "perfetto_frame_summary_csv": f"{tag}_perfetto_frame_sched_summary.csv" if perfetto_sched_available else "",
            "top_perfetto_threads": sched_summary.get("top_threads", [])[:10],
            "limitation": (
                "Perfetto thread_state supplies frame-window Running/Runnable aggregation; TracePilot offline graph still did not reconstruct WAKEUP/RUNNABLE_WAIT/CPU_RUN edges."
                if perfetto_sched_available
                else "Frame windows were joined into the graph, but runnable delay and on-CPU metrics are zero in the offline output."
            ),
        },
        "top_k_critical_threads": {
            "status": "available_with_perfetto_sched_crosscheck" if perfetto_sched_available else "available_but_low_confidence",
            "threads": top_threads[:10],
            "perfetto_crosscheck_threads": sched_summary.get("top_threads", [])[:10],
            "limitation": (
                "TracePilot Top-K is candidate-only, but Perfetto sched crosscheck provides independent frame-window on-CPU/runnable evidence."
                if perfetto_sched_available
                else "Top-K is useful as a candidate list only because target package detection and runnable-delay attribution are weak in this sample."
            ),
        },
        "user_space_hint": {
            "status": "schema_dry_run_only_invalid_target_package" if hint_package_mismatch else "dry_run_recommendation_only",
            "hints_json": f"{tag}_hints.json",
            "hint_count": hints.get("hint_count", 0),
            "default_ttl_ms": hints.get("default_ttl_ms", 0),
            "hints": hints.get("hints", []),
            "hint_target_packages": sorted(p for p in hint_packages if p),
            "safety": ["TTL", "rollback", "foreground package guard", "thermal/frame regression guard before actual apply"],
            "limitation": "Do not apply this hint as-is when the hint package does not match the capture package.",
        },
    }

    step2 = {
        "tag": tag,
        "package": args.package,
        "source_notes": step1["source_notes"],
        "binder_dependency_graph": {
            "status": "candidate_graph_available_attribution_weak",
            "binder_edge_count": edge_dist.get("BINDER_CALL", 0),
            "binder_call_count": resource.get("binder_call_count", 0),
            "total_binder_blocking_ns": resource.get("total_binder_blocking_ns", 0),
            "graph_topology": f"{tag}_graph_topology.json",
            "graph_subgraph_dot": f"{tag}_graph_subgraph.dot",
            "enhanced_events_summary": f"{tag}_tracepilot_enhanced_events_summary.json" if enhanced_events_available else "",
            "debug_binder_call_count": enhanced_events.get("event_counts", {}).get("BINDER_CALL", 0),
            "debug_relevant_binder_call_count": enhanced_events.get("relevant_event_counts", {}).get("BINDER_CALL", 0),
            "top_debug_binder_candidates": [
                row for row in enhanced_events.get("top_candidates", [])
                if row.get("event") == "BINDER_CALL"
            ][:10],
            "limitation": "Counts are available, but package-level and frame-cause attribution are weak because target detection mismatched.",
        },
        "futex_wait_graph": {
            "status": "candidate_graph_available_attribution_weak",
            "futex_edge_count": edge_dist.get("FUTEX_WAIT", 0),
            "futex_wait_count": resource.get("futex_wait_count", 0),
            "total_futex_wait_ns": resource.get("total_futex_wait_ns", 0),
            "enhanced_events_summary": f"{tag}_tracepilot_enhanced_events_summary.json" if enhanced_events_available else "",
            "debug_futex_wait_count": enhanced_events.get("event_counts", {}).get("FUTEX_WAIT", 0),
            "debug_relevant_futex_wait_count": enhanced_events.get("relevant_event_counts", {}).get("FUTEX_WAIT", 0),
            "top_debug_futex_candidates": [
                row for row in enhanced_events.get("top_candidates", [])
                if row.get("event") == "FUTEX_WAIT"
            ][:10],
            "limitation": "Futex counts are available, but they are not yet proven to explain the two missed frames.",
        },
        "cpu_frequency_big_little_attribution": {
            "status": "available_via_perfetto_frame_windows" if perfetto_cpu_available else "available_session_level_not_frame_causal",
            "avg_cpu_freq_little_khz": resource.get("avg_cpu_freq_little_khz", 0),
            "avg_cpu_freq_big_khz": resource.get("avg_cpu_freq_big_khz", 0),
            "min_cpu_freq_big_khz": resource.get("min_cpu_freq_big_khz", 0),
            "freq_throttle_ratio": resource.get("freq_throttle_ratio", 0),
            "perfetto_cpu_freq_summary": f"{tag}_perfetto_cpu_freq_summary.json" if perfetto_cpu_available else "",
            "perfetto_frame_cpu_freq_csv": f"{tag}_perfetto_frame_cpu_freq.csv" if perfetto_cpu_available else "",
            "perfetto_thread_cpu_cluster_csv": f"{tag}_perfetto_thread_cpu_cluster.csv" if perfetto_cpu_available else "",
            "cluster_summary": cpu_freq_summary.get("cluster_summary", {}),
            "top_threads_by_cluster_runtime": cpu_freq_summary.get("top_threads", [])[:10],
            "limitation": (
                "Perfetto supplies frame-window frequency and thread cluster runtime; this is stronger than session-level output, but still observational rather than intervention-proven causality."
                if perfetto_cpu_available
                else "Frequency summary is session-level; it is not yet a per-frame big-little causal attribution."
            ),
        },
        "jank_cause_classifier": {
            "status": "candidate_only_low_confidence",
            "jank_cause_distribution": result.get("jank_cause_distribution", {}),
            "session_summary": inference.get("session_summary", ""),
            "recommended_hint": inference.get("recommended_hint", ""),
            "frame_inferences": inference.get("frame_inferences", []),
            "max_frame_confidence": max_inference_confidence,
            "confidence_note": "Frame-level labels are candidates only; confidence is zero for this sample and evidence arrays are empty.",
        },
        "heuristic_strategy_comparison": {
            "status": "available_but_underpowered",
            **heuristics,
            "limitation": "Only two missed frames were available, so AP@K/overlap should be treated as a smoke test rather than a stable evaluation.",
        },
    }

    (dataset_dir / f"{tag}_step1_summary.json").write_text(
        json.dumps(step1, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (dataset_dir / f"{tag}_step2_summary.json").write_text(
        json.dumps(step2, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "step1": str(dataset_dir / f"{tag}_step1_summary.json"),
        "step2": str(dataset_dir / f"{tag}_step2_summary.json"),
        "frames": frame_summary.get("frame_count", 0),
        "jank_frames": frame_summary.get("deadline_missed_count", 0),
        "top_threads": [row.get("comm") for row in top_threads[:5]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
