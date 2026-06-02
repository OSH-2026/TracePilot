#!/system/bin/sh
set -u

DURATION="${1:-25}"
PREFIX="${2:-/data/local/tmp/game_play}"
APP_PACKAGE="${3:-}"
TRACE_DIR="/sys/kernel/tracing"
TRACEPILOT="/data/local/tmp/tracepilot"
BPF_OBJ="/data/local/tmp/tracepilot.bpf.o"
SF_LAYER=""

if [ -z "$APP_PACKAGE" ]; then
    echo "usage: android_game_aligned_capture.sh <duration_s> <output_prefix> <app_package>" >&2
    exit 2
fi

enable_event() {
    if [ -e "$TRACE_DIR/events/$1/enable" ]; then
        echo 1 > "$TRACE_DIR/events/$1/enable"
    fi
}

disable_event() {
    if [ -e "$TRACE_DIR/events/$1/enable" ]; then
        echo 0 > "$TRACE_DIR/events/$1/enable"
    fi
}

cleanup() {
    echo 0 > "$TRACE_DIR/tracing_on" 2>/dev/null || true
    for e in $SELECTED_EVENTS; do
        disable_event "$e"
    done
}

SELECTED_EVENTS="
binder/binder_transaction
binder/binder_transaction_received
binder/binder_wait_for_work
vmscan/mm_vmscan_direct_reclaim_begin
vmscan/mm_vmscan_direct_reclaim_end
block/block_rq_issue
block/block_rq_complete
thermal/thermal_temperature
thermal_pressure/thermal_pressure_update
dma_fence/dma_fence_wait_start
dma_fence/dma_fence_wait_end
syscalls/sys_enter_futex
syscalls/sys_exit_futex
"

trap cleanup EXIT INT TERM

{
    echo "package=$APP_PACKAGE"
    echo "duration_s=$DURATION"
    echo "started_at=$(date '+%Y-%m-%dT%H:%M:%S%z')"
    /system/bin/dumpsys window | /system/bin/grep -E 'mCurrentFocus|mFocusedApp' | /system/bin/head -n 2
} > "${PREFIX}_metadata.txt"

/system/bin/dumpsys gfxinfo "$APP_PACKAGE" reset > /dev/null 2>&1 || true
SF_LAYER=$(/system/bin/dumpsys SurfaceFlinger --list | /system/bin/grep -F "$APP_PACKAGE" | /system/bin/head -n 1)
if [ -n "$SF_LAYER" ]; then
    /system/bin/dumpsys SurfaceFlinger --latency-clear "$SF_LAYER" > /dev/null 2>&1 || true
fi

echo 0 > "$TRACE_DIR/tracing_on"
for e in $SELECTED_EVENTS; do
    disable_event "$e"
done
echo > "$TRACE_DIR/trace"
echo nop > "$TRACE_DIR/current_tracer"
for e in $SELECTED_EVENTS; do
    enable_event "$e"
done

echo 1 > "$TRACE_DIR/tracing_on"
"$TRACEPILOT" --duration "$DURATION" --out "${PREFIX}.jsonl" --bpf "$BPF_OBJ"
echo 0 > "$TRACE_DIR/tracing_on"
cat "$TRACE_DIR/trace" > "${PREFIX}_ftrace.txt"
/system/bin/dumpsys gfxinfo "$APP_PACKAGE" framestats > "${PREFIX}_framestats.txt" 2>&1 || true
if [ -n "$SF_LAYER" ]; then
    echo "$SF_LAYER" > "${PREFIX}_surfaceflinger_layer.txt"
    /system/bin/dumpsys SurfaceFlinger --latency "$SF_LAYER" > "${PREFIX}_surfaceflinger_latency.txt" 2>&1 || true
fi

{
    echo "finished_at=$(date '+%Y-%m-%dT%H:%M:%S%z')"
    /system/bin/dumpsys window | /system/bin/grep -E 'mCurrentFocus|mFocusedApp' | /system/bin/head -n 2
} >> "${PREFIX}_metadata.txt"
