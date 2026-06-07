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
#   ./scripts/deploy.sh --scenario page_switch --package com.example.app
#   ./scripts/deploy.sh --scenario video --duration 30 --package com.example.app
#
# Output layout (per scenario):
#   output/page_switch/  or  output/video/
#     events.bin
#     frames.txt
#     perfetto_trace.perfetto-trace
#     identity_map.json   (from device, if present)
#     result.json
#     hints.json          (from graph analysis)
#     graph_*.json/dot    (from -G mode)

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────
PUSH=true
DURATION=30
PACKAGE=""
SCENARIO="page_switch"
TRACE_PROCESSOR="${TRACE_PROCESSOR:-trace_processor_shell}"
PERFETTO_CONFIG="$(dirname "$0")/perfetto_config.pbtx"
THERMAL_SQL="$(dirname "$0")/thermal_query.sql"
PROJECT_ROOT="$(dirname "$0")/.."
OUTPUT_DIR="$PROJECT_ROOT/output"

# ── Parse args ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push)   PUSH=false; shift ;;
        --duration)  DURATION="$2"; shift 2 ;;
        --package)   PACKAGE="$2"; shift 2 ;;
        --scenario|-s)
            SCENARIO="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 --scenario {page_switch|video} [OPTIONS]"
            echo ""
            echo "  --scenario, -s   Collection scenario (required for clean separation)"
            echo "                   page_switch = tab/page switching only"
            echo "                   video       = video playback only"
            echo "  --no-push        Skip push step (binaries already on device)"
            echo "  --duration       Collection duration in seconds (default: 30)"
            echo "  --package        Target app package name"
            echo ""
            echo "Examples:"
            echo "  $0 --scenario page_switch --package com.example.app --duration 30"
            echo "  $0 --scenario video --package com.example.app --duration 30"
            exit 0
            ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [[ "$SCENARIO" != "page_switch" && "$SCENARIO" != "video" ]]; then
    echo "ERROR: invalid --scenario '$SCENARIO' (use page_switch or video)"
    exit 1
fi

SCENARIO_DIR="$OUTPUT_DIR/$SCENARIO"
DEVICE_DIR="/data/local/tmp"
DEVICE_SCENARIO_DIR="$DEVICE_DIR/$SCENARIO"
TRACE_FILE="/sdcard/perfetto_trace_${SCENARIO}.perfetto-trace"
EVENTS_FILE="$DEVICE_SCENARIO_DIR/events.bin"

mkdir -p "$SCENARIO_DIR"

echo "=== TracePilot Deploy ==="
echo "Scenario:   $SCENARIO"
echo "Duration:   ${DURATION}s"
echo "Package:    ${PACKAGE:-(all)}"
echo "Output dir: $SCENARIO_DIR"
echo ""

if [[ "$SCENARIO" == "page_switch" ]]; then
    ACTION_HINT=">>> Switch pages / tabs in your app NOW (do NOT play video) <<<"
else
    ACTION_HINT=">>> Play video continuously NOW (avoid page switching) <<<"
fi

# ── Step 1: Push binaries ─────────────────────────────────────────────
PERF_CFG="/data/misc/perfetto-configs/tracepilot.pbtx"

adb_su() {
    adb shell "su -c '$*'"
}

if ! adb shell "su -c 'id -u'" 2>/dev/null | grep -q '^0$'; then
    echo "ERROR: device root required (su failed)"
    exit 1
fi

if $PUSH; then
    echo "[1/6] Pushing binaries to device..."
    adb push "$OUTPUT_DIR/tracepilot-aarch64" "$DEVICE_DIR/tracepilot" 2>&1 | head -1
    adb push "$OUTPUT_DIR/tracepilot.bpf.o" "$DEVICE_DIR/" 2>&1 | head -1
    adb push "$PERFETTO_CONFIG" "$DEVICE_DIR/perfetto_config.pbtx" 2>&1 | head -1
    adb_su "mkdir -p /data/misc/perfetto-configs $DEVICE_SCENARIO_DIR"
    adb_su "cp $DEVICE_DIR/perfetto_config.pbtx $PERF_CFG && chmod 644 $PERF_CFG"
    adb_su "chmod 755 $DEVICE_DIR/tracepilot && chmod 644 $DEVICE_DIR/tracepilot.bpf.o"
    echo "  Done."
else
    echo "[1/6] Skipping push (--no-push)"
    adb_su "mkdir -p /data/misc/perfetto-configs $DEVICE_SCENARIO_DIR"
    adb_su "test -f $DEVICE_DIR/perfetto_config.pbtx && cp $DEVICE_DIR/perfetto_config.pbtx $PERF_CFG && chmod 644 $PERF_CFG || true"
fi

# ── Step 2: Start Perfetto ────────────────────────────────────────────
echo "[2/6] Starting Perfetto trace ($DURATION seconds)..."
adb_su "perfetto --txt -c $PERF_CFG -o $TRACE_FILE --background"
echo "  Perfetto tracing started (background, running in parallel with loader)."

# ── Step 3: Start tracepilot loader ───────────────────────────────────
echo "[3/6] Starting tracepilot loader..."
LOADER_CMD="$DEVICE_DIR/tracepilot --duration $DURATION --events-out $EVENTS_FILE --debug"
if [ -n "$PACKAGE" ]; then
    LOADER_CMD="$LOADER_CMD --package $PACKAGE"
fi
echo "  Command: su -c '$LOADER_CMD'"
adb shell "su -c '$LOADER_CMD'" &
LOADER_PID=$!
echo "  Loader PID: $LOADER_PID"

# ── Step 4: Wait ──────────────────────────────────────────────────────
echo "[4/6] Collecting for ${DURATION}s..."
echo "  $ACTION_HINT"
sleep "$DURATION"
echo "  Collection period ended."

# ── Step 5: Pull results ──────────────────────────────────────────────
echo "[5/6] Pulling results..."
wait $LOADER_PID 2>/dev/null || true
sleep 1

PERFETTO_LOCAL="$SCENARIO_DIR/perfetto_trace.perfetto-trace"
EVENTS_LOCAL="$SCENARIO_DIR/events.bin"
FRAMES_TXT="$SCENARIO_DIR/frames.txt"
THERMAL_TXT="$SCENARIO_DIR/thermal_profile.txt"
RESULT_JSON="$SCENARIO_DIR/result.json"
IDENTITY_JSON="$SCENARIO_DIR/identity_map.json"

adb pull "$TRACE_FILE" "$PERFETTO_LOCAL" 2>&1 | head -1
adb pull "$EVENTS_FILE" "$EVENTS_LOCAL" 2>&1 | head -1 || echo "  (no events file)"
adb pull "$DEVICE_SCENARIO_DIR/identity_map.json" "$IDENTITY_JSON" 2>/dev/null \
    || adb pull "$DEVICE_DIR/identity_map.json" "$IDENTITY_JSON" 2>/dev/null \
    || true

# ── Step 6: Extract frame data & analyze (host-side) ────────────────
echo "[6/6] Extracting frame data & running analysis..."
if command -v "$TRACE_PROCESSOR" >/dev/null 2>&1; then
    "$TRACE_PROCESSOR" -q "$FRAME_SQL" "$PERFETTO_LOCAL" > "$FRAMES_TXT" 2>/dev/null
    echo "  Frame data written to: $FRAMES_TXT"

    if [ -f "$THERMAL_SQL" ]; then
        "$TRACE_PROCESSOR" -q "$THERMAL_SQL" "$PERFETTO_LOCAL" > "$THERMAL_TXT" 2>/dev/null || true
        if [ -s "$THERMAL_TXT" ]; then
            echo "  Thermal profile written to: $THERMAL_TXT"
        else
            echo "  (no thermal samples in trace — freq-based throttle still available)"
        fi
    fi

    SF_COUNT=$(grep -c '"SF"' "$FRAMES_TXT" 2>/dev/null || echo 0)
    VD_COUNT=$(grep -c '"VD"' "$FRAMES_TXT" 2>/dev/null || echo 0)
    echo "  Frame types: SF=$SF_COUNT  VD=$VD_COUNT"
    if [[ "$SCENARIO" == "page_switch" && "$VD_COUNT" -gt 10 ]]; then
        echo "  WARNING: many VD frames in page_switch capture — did you play video?"
    fi
    if [[ "$SCENARIO" == "video" && "$VD_COUNT" -eq 0 ]]; then
        echo "  WARNING: no VD frames — check video playback / frame_query.sql"
    fi

    HOST_BIN="$OUTPUT_DIR/tracepilot"
    if [ -x "$HOST_BIN" ] && [ -f "$EVENTS_LOCAL" ]; then
        ANALYZE_CMD=(
            "$HOST_BIN"
            --events-in "$EVENTS_LOCAL"
            --frame-data "$FRAMES_TXT"
            --output "$RESULT_JSON"
            --top-k 10
            -G
            -s "$SCENARIO"
            --debug
        )
        if [ -n "$PACKAGE" ]; then
            ANALYZE_CMD+=(--package "$PACKAGE")
        fi
        if [ -s "$THERMAL_TXT" ]; then
            ANALYZE_CMD+=(--thermal-data "$THERMAL_TXT")
        fi
        echo "  Running: ${ANALYZE_CMD[*]}"
        "${ANALYZE_CMD[@]}" || true
        echo "  Result written to: $RESULT_JSON"
    else
        echo "  Host binary or events missing; skipping analysis."
        echo "  Build with: make loader"
        echo "  Then run:"
        echo "    $HOST_BIN -i $EVENTS_LOCAL -f $FRAMES_TXT -o $RESULT_JSON -G -s $SCENARIO"
    fi
else
    echo "  WARNING: trace_processor_shell not found."
    echo "  Manually extract frames, then run:"
    echo "    ./output/tracepilot -i $EVENTS_LOCAL -f $FRAMES_TXT -o $RESULT_JSON -G -s $SCENARIO"
fi

echo ""
echo "=== Done ($SCENARIO) ==="
echo "Output in $SCENARIO_DIR/:"
ls -la "$SCENARIO_DIR" 2>/dev/null || true
echo ""
echo "Next: collect the other scenario, e.g.:"
if [[ "$SCENARIO" == "page_switch" ]]; then
    echo "  $0 --scenario video ${PACKAGE:+--package $PACKAGE}"
    echo ""
    echo "After both scenarios collected, compare:"
    echo "  $OUTPUT_DIR/tracepilot --compare-dir $OUTPUT_DIR --compare-out $OUTPUT_DIR/compare_report.json"
else
    echo "  $0 --scenario page_switch ${PACKAGE:+--package $PACKAGE}"
fi
