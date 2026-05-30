#!/bin/bash
# TracePilot deploy script — push binaries, start Perfetto + eBPF loader, pull results
#
# Prerequisites:
#   - adb connected to rooted Pixel 6a
#   - tracepilot-aarch64 in output/ (built via: make android)
#   - tracepilot.bpf.o in output/ (built via: make bpf)
#   - tracepilot host binary in output/ (built via: make loader)
#   - Perfetto trace_processor_shell on PATH or set TRACE_PROCESSOR
#
# Usage:
#   ./scripts/deploy.sh [--no-push] [--duration 30] [--package com.example.app]
#
# Workflow:
#  1. Push binaries to /data/local/tmp/ on device
#  2. Start Perfetto tracing (background)
#  3. Start tracepilot eBPF loader (collects sched + IRQ/SoftIRQ events)
#  4. Manually switch pages in app during collection window
#  5. Pull Perfetto trace + eBPF events to host
#  6. Extract frame data via trace_processor_shell + run offline analysis

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────
PUSH=true
DURATION=30
PACKAGE=""
TRACE_PROCESSOR="${TRACE_PROCESSOR:-trace_processor_shell}"
PERFETTO_CONFIG="$(dirname "$0")/perfetto_config.pbtx"
FRAME_SQL="$(dirname "$0")/frame_query.sql"
PROJECT_ROOT="$(dirname "$0")/.."
OUTPUT_DIR="$PROJECT_ROOT/output"

# ── Parse args ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push)   PUSH=false; shift ;;
        --duration)  DURATION="$2"; shift 2 ;;
        --package)   PACKAGE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--no-push] [--duration SEC] [--package PKG]"
            echo ""
            echo "  --no-push    Skip push step (binaries already on device)"
            echo "  --duration   Collection duration in seconds (default: 30)"
            echo "  --package    Target app package name"
            exit 0
            ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

DEVICE_DIR="/data/local/tmp"
TRACE_FILE="/sdcard/perfetto_trace.perfetto-trace"
EVENTS_FILE="$DEVICE_DIR/tracepilot_events.bin"
RESULT_FILE="$DEVICE_DIR/tracepilot_result.json"

echo "=== TracePilot Deploy ==="
echo "Duration:   ${DURATION}s"
echo "Package:    ${PACKAGE:-(all)}"
echo ""

# ── Step 1: Push binaries ─────────────────────────────────────────────
if $PUSH; then
    echo "[1/6] Pushing binaries to device..."
    adb push "$OUTPUT_DIR/tracepilot-aarch64" "$DEVICE_DIR/tracepilot" 2>&1 | head -1
    adb push "$OUTPUT_DIR/tracepilot.bpf.o" "$DEVICE_DIR/" 2>&1 | head -1
    adb push "$PERFETTO_CONFIG" "$DEVICE_DIR/" 2>&1 | head -1
    adb shell "chmod +x $DEVICE_DIR/tracepilot"
    echo "  Done."
else
    echo "[1/6] Skipping push (--no-push)"
fi

# ── Step 2: Start Perfetto ────────────────────────────────────────────
echo "[2/6] Starting Perfetto trace ($DURATION seconds)..."
adb shell "perfetto --txt -c $DEVICE_DIR/perfetto_config.pbtx -o $TRACE_FILE --background"
echo "  Perfetto tracing started (background, running in parallel with loader)."

# ── Step 3: Start tracepilot loader ───────────────────────────────────
echo "[3/6] Starting tracepilot loader..."
LOADER_CMD="$DEVICE_DIR/tracepilot --duration $DURATION --events-out $EVENTS_FILE --debug"
if [ -n "$PACKAGE" ]; then
    LOADER_CMD="$LOADER_CMD --package $PACKAGE"
fi
echo "  Command: $LOADER_CMD"
adb shell "$LOADER_CMD" &
LOADER_PID=$!
echo "  Loader PID: $LOADER_PID"

# ── Step 4: Wait ──────────────────────────────────────────────────────
echo "[4/6] Collecting for ${DURATION}s..."
echo "  >>> Switch pages in your app NOW <<<"
sleep "$DURATION"
echo "  Collection period ended."

# ── Step 5: Pull results ──────────────────────────────────────────────
echo "[5/6] Pulling results..."
wait $LOADER_PID 2>/dev/null || true
sleep 1

# Pull Perfetto trace
adb pull "$TRACE_FILE" "$OUTPUT_DIR/perfetto_trace.perfetto-trace" 2>&1 | head -1

# Pull eBPF events
adb pull "$EVENTS_FILE" "$OUTPUT_DIR/tracepilot_events.bin" 2>&1 | head -1 || echo "  (no events file)"

# ── Step 6: Extract frame data & analyze (host-side) ────────────────
echo "[6/6] Extracting frame data & running analysis..."
if command -v "$TRACE_PROCESSOR" >/dev/null 2>&1; then
    FRAMES_TXT="$OUTPUT_DIR/frames.txt"
    # Run frame_query.sql through trace_processor_shell
    "$TRACE_PROCESSOR" -q "$FRAME_SQL" "$OUTPUT_DIR/perfetto_trace.perfetto-trace" > "$FRAMES_TXT" 2>/dev/null
    echo "  Frame data written to: $FRAMES_TXT"

    # Run analysis on host using the host binary with --events-in
    echo "Running frame analysis..."
    HOST_BIN="$OUTPUT_DIR/tracepilot"
    if [ -x "$HOST_BIN" ]; then
        "$HOST_BIN" \
            --events-in "$OUTPUT_DIR/tracepilot_events.bin" \
            --frame-data "$FRAMES_TXT" \
            --output "$OUTPUT_DIR/result.json" \
            --top-k 10 \
            --debug 2>/dev/null || true
        echo "  Result written to: $OUTPUT_DIR/result.json"
    else
        echo "  Host binary not found at $HOST_BIN, skipping analysis."
        echo "  Build it with: make loader"
    fi
else
    echo "  WARNING: trace_processor_shell not found."
    echo "  Manually extract frames, then run:"
    echo "    ./output/tracepilot -i output/tracepilot_events.bin -f frames.txt -o result.json"
fi

echo ""
echo "=== Done ==="
echo "Output files in $OUTPUT_DIR/:"
ls -la "$OUTPUT_DIR"/{perfetto_trace.perfetto-trace,*.json,*.bin} 2>/dev/null || true
echo ""
echo "To view top-k threads:"
echo "  cat $OUTPUT_DIR/result.json"