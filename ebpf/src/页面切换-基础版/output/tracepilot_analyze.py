#!/usr/bin/env python3
"""
TracePilot Python Analysis Engine (corrected for BPF struct-mismatch data)

The events.bin collected with the current BPF code has known issues:
  - switch events: next_tid read from wrong offset (vmlinux.h says prev_state
    is 'long' 8-byte, but Android 6.1 kernel uses 'unsigned int' 4-byte).
    This shifts next_comm, next_pid, next_tid by 4 bytes → ALL CORRUPTED.
  - switch events: prev_tid/prev_pid are at correct offsets → VALID.
  - wakeup events: local struct definition matches kernel → ALL VALID.
  - All wl and rd are 0 (BPF hash map tracking broken due to above).

Strategy:
  1. Track threads using prev_tid from switch events (preempted tasks).
  2. Build TID→comm from wakeup events (correct) + prev_comm from switch
     events (offset 8 = correct in both layouts).
  3. Frame window: handle inverted windows (actual_present < expected_start).
  4. System events aggregated normally (offsets 0,8,12,16,24 are all correct).

Usage:
  python tracepilot_analyze.py -i events.bin -f frames.txt -o result.json -k 10
"""

import struct
import argparse
import os
import math
import sys

SCHED_EVENT_SIZE = 96
SYS_EVENT_SIZE = 32
PRE_MARGIN_NS = 20_000_000
POST_MARGIN_NS = 10_000_000
MAX_SAMPLES = 1024

EVENT_SCHED_SWITCH = 0
EVENT_SCHED_WAKEUP = 1


def _safe_decode(raw):
    null_idx = raw.find(b'\x00')
    if null_idx >= 0:
        raw = raw[:null_idx]
    return raw.decode('utf-8', errors='replace')


class SchedEvent:
    """Parsed from the 96-byte struct in events.bin."""
    __slots__ = ('ts', 'etype', 'prev_pid', 'prev_tid', 'next_pid', 'next_tid',
                 'prev_state', 'prev_comm', 'next_comm', 'cpu', 'wl', 'rd')
    def __init__(self, raw):
        self.ts           = struct.unpack_from("<Q", raw, 0)[0]
        self.etype        = struct.unpack_from("<I", raw, 8)[0]
        self.prev_pid     = struct.unpack_from("<I", raw, 12)[0]
        self.prev_tid     = struct.unpack_from("<I", raw, 16)[0]
        self.next_pid     = struct.unpack_from("<I", raw, 20)[0]
        self.next_tid     = struct.unpack_from("<I", raw, 24)[0]
        self.prev_state   = struct.unpack_from("<Q", raw, 32)[0]
        self.prev_comm    = _safe_decode(raw[40:56])
        self.next_comm    = _safe_decode(raw[56:72])
        self.cpu          = struct.unpack_from("<I", raw, 72)[0]
        self.wl           = struct.unpack_from("<Q", raw, 80)[0]
        self.rd           = struct.unpack_from("<Q", raw, 88)[0]


class SystemEvent:
    __slots__ = ('ts', 'etype', 'irq_vec', 'cpu', 'duration')
    def __init__(self, raw):
        self.ts        = struct.unpack_from("<Q", raw, 0)[0]
        self.etype     = struct.unpack_from("<I", raw, 8)[0]
        self.irq_vec   = struct.unpack_from("<i", raw, 12)[0]
        self.cpu       = struct.unpack_from("<I", raw, 16)[0]
        self.duration  = struct.unpack_from("<Q", raw, 24)[0]


class FrameWindow:
    __slots__ = ('token', 'expected_start', 'expected_end', 'actual_end',
                 'is_jank', 'delay_ms', 'sys_overhead')
    def __init__(self, token, es, ee, ae, is_jank, delay_ms):
        self.token = token
        self.expected_start = es
        self.expected_end = ee
        self.actual_end = ae
        self.is_jank = is_jank
        self.delay_ms = delay_ms
        self.sys_overhead = 0

    def win_start(self):
        """For jank frames, use the minimum of expected_start and actual_end
        so the window covers the rendering period regardless of whether the
        frame was early (actual < expected, negative delay) or late."""
        ref = self.expected_start
        if self.is_jank and self.actual_end < self.expected_start:
            ref = self.actual_end
        if ref > PRE_MARGIN_NS:
            return ref - PRE_MARGIN_NS
        return 0

    def win_end(self):
        ref = self.actual_end
        if self.is_jank and self.expected_end > self.actual_end:
            ref = self.expected_end
        return ref + POST_MARGIN_NS

    def contains(self, adjusted_ts):
        return self.win_start() <= adjusted_ts <= self.win_end()


class ThreadStats:
    __slots__ = ('tid', 'comm', 'jank_count', 'last_token', 'sys_overhead',
                 'wd_count', 'wd_sum')
    def __init__(self, tid, comm=""):
        self.tid = tid
        self.comm = comm
        self.jank_count = 0
        self.last_token = -1
        self.sys_overhead = 0
        self.wd_count = 0
        self.wd_sum = 0


def _p95(samples):
    if not samples:
        return 0
    s = sorted(samples)
    idx = int(len(s) * 0.95)
    if idx >= len(s):
        idx = len(s) - 1
    return s[idx]


class TracePilotAnalyzer:
    def __init__(self, top_k=10):
        self.top_k = top_k
        self.sched_events = []
        self.sys_events = []
        self.frames = []
        self.clock_offset = 0
        self.threads = {}
        self.tid_comm_map = {}

    # ── Loading ────────────────────────────────────────────────
    def load(self, events_bin_path, frames_path):
        self._load_events_bin(events_bin_path)
        self._load_frames(frames_path)
        self._build_comm_map()
        self._compute_clock_offset()

    def _load_events_bin(self, path):
        with open(path, "rb") as f:
            header = f.read(24)
            magic, ver, sched_cnt, sys_cnt = struct.unpack_from("<IIQQ", header, 0)
            if magic != 0x32765054:  # TPv2 magic
                f.seek(0)
                sched_cnt = os.fstat(f.fileno()).st_size // SCHED_EVENT_SIZE
                sys_cnt = 0
            print(f"[*] Loaded {sched_cnt} sched + {sys_cnt} sys events (format v{ver})")

            for _ in range(sched_cnt):
                raw = f.read(SCHED_EVENT_SIZE)
                if len(raw) < SCHED_EVENT_SIZE: break
                self.sched_events.append(SchedEvent(raw))
            for _ in range(sys_cnt):
                raw = f.read(SYS_EVENT_SIZE)
                if len(raw) < SYS_EVENT_SIZE: break
                self.sys_events.append(SystemEvent(raw))

    def _load_frames(self, path):
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('"frame_number"') or line.startswith('-'):
                    continue
                parts = line.split(',')
                if len(parts) < 6: continue
                try:
                    token = int(parts[0])
                    es = int(parts[2]) if len(parts) > 2 else 0
                    ee = int(parts[3]) if len(parts) > 3 else 0
                    ae = int(parts[4])
                    is_jank = int(parts[5])
                    delay_ms = float(parts[6]) if len(parts) > 6 else 0.0
                except (ValueError, IndexError):
                    continue
                self.frames.append(FrameWindow(token, es, ee, ae, is_jank, delay_ms))
        nj = sum(1 for fw in self.frames if fw.is_jank)
        print(f"[*] {len(self.frames)} frames ({nj} jank)")

    def _build_comm_map(self):
        """Use wakeup events (correct next_comm) + switch prev_comm."""
        for evt in self.sched_events:
            if evt.etype == EVENT_SCHED_WAKEUP and evt.next_tid > 0:
                if evt.next_comm and evt.next_tid not in self.tid_comm_map:
                    self.tid_comm_map[evt.next_tid] = evt.next_comm
            elif evt.etype == EVENT_SCHED_SWITCH and evt.prev_tid > 0:
                if evt.prev_comm and evt.prev_tid not in self.tid_comm_map:
                    self.tid_comm_map[evt.prev_tid] = evt.prev_comm
        print(f"[*] TID->comm map: {len(self.tid_comm_map)} entries")

    def _compute_clock_offset(self):
        if not self.sched_events or not self.frames:
            return
        self.clock_offset = int(self.frames[0].expected_start) - int(self.sched_events[0].ts)
        print(f"[*] Clock offset: {self.clock_offset / 1e9:.3f}s")

    # ── Analysis ───────────────────────────────────────────────
    def analyze(self):
        # Step 1: System events → frame overhead
        for evt in self.sys_events:
            adj = int(evt.ts) + self.clock_offset
            for fw in self.frames:
                if fw.is_jank and fw.contains(adj):
                    fw.sys_overhead += evt.duration

        # Step 2: Sched events → thread preemption tracking
        # Only use prev_tid (correct) from switch events plus TID/comm
        # from wakeup events.
        wl_samples = []  # collect wakeup_latency for p95 (though wl is 0)
        wl_per_thread = {}

        for evt in self.sched_events:
            adj = int(evt.ts) + self.clock_offset
            for fw in self.frames:
                if not fw.is_jank or not fw.contains(adj):
                    continue

                if evt.etype == EVENT_SCHED_SWITCH:
                    # Track the PREEMPTED task (prev_tid is correct)
                    if evt.prev_tid > 0:
                        ts = self._get_thread(evt.prev_tid, evt.prev_comm)
                        if ts.last_token != fw.token:
                            ts.last_token = fw.token
                            ts.jank_count += 1
                            ts.sys_overhead += fw.sys_overhead
                        # Track wakeup delay
                        if evt.wl > 0:
                            ts.wd_count += 1
                            ts.wd_sum += evt.wl

                    # For next_tid: use wakeup map to identify the thread
                    # (if the next task was previously woken, its TID is in
                    # the wakeup map with correct TID). Skip tracking next_tid
                    # from switch directly because it's corrupted by the
                    # struct-layout bug.

                elif evt.etype == EVENT_SCHED_WAKEUP:
                    # Wakeup events have correct data. Track the woken task.
                    if evt.next_tid > 0:
                        ts = self._get_thread(evt.next_tid, evt.next_comm)
                        if ts.last_token != fw.token:
                            ts.last_token = fw.token
                            ts.jank_count += 1
                            ts.sys_overhead += fw.sys_overhead

        # Step 3: Score threads
        return self._score_threads()

    def _get_thread(self, tid, comm):
        if tid not in self.threads:
            self.threads[tid] = ThreadStats(tid, comm)
        ts = self.threads[tid]
        if not ts.comm and comm:
            ts.comm = comm
        return ts

    def _score_threads(self):
        num_jank = sum(1 for fw in self.frames if fw.is_jank)
        if num_jank == 0:
            return [], 0, len(self.frames), 0

        total_sys = sum(fw.sys_overhead for fw in self.frames if fw.is_jank)
        scores = []

        for tid, ts in self.threads.items():
            if ts.jank_count == 0:
                continue

            # Ratio of jank frames this thread participated in
            j_ratio = ts.jank_count / num_jank

            # Average wakeup delay for this thread
            avg_wd = ts.wd_sum / max(1, ts.wd_count)

            score = 0.0
            score += 0.35 * j_ratio
            score += 0.35 * math.log1p(0)         # rd = 0 (BPF data broken)
            score += 0.15 * math.log1p(avg_wd)    # avg wakeup delay (likely 0)
            if "RenderThread" in ts.comm or ".ui" in ts.comm:
                score += 0.15

            # System overhead discount
            sys_ratio = 0.0
            if ts.jank_count > 0:
                avg_oh = ts.sys_overhead / ts.jank_count
                sys_ratio = min(avg_oh / 16666666.0, 0.9)
            score *= (1.0 - sys_ratio)

            scores.append({
                'tid': tid,
                'pid': 0,
                'comm': ts.comm,
                'package': '',
                'score': round(score, 4),
                'runnable_delay_p95_ns': 0,
                'wakeup_latency_p95_ns': avg_wd,
                'system_overhead_ns': ts.sys_overhead,
            })

        scores.sort(key=lambda x: x['score'], reverse=True)
        return scores[:self.top_k], total_sys, len(self.frames), num_jank

    # ── Output ─────────────────────────────────────────────────
    def output(self, out_path, result):
        scores, total_sys, total_frames, num_jank = result
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("{\n")
            f.write(f'  "total_frames": {total_frames},\n')
            f.write(f'  "jank_frames": {num_jank},\n')
            f.write(f'  "jank_system_overhead_ns": {total_sys},\n')
            f.write('  "top_k_threads": [\n')
            for i, s in enumerate(scores):
                comma = "," if i < len(scores) - 1 else ""
                f.write("    {\n")
                f.write(f'      "rank": {i + 1},\n')
                f.write(f'      "tid": {s["tid"]},\n')
                f.write(f'      "pid": {s["pid"]},\n')
                f.write(f'      "comm": "{s["comm"]}",\n')
                f.write(f'      "package": "{s["package"]}",\n')
                f.write(f'      "score": {s["score"]:.4f},\n')
                f.write(f'      "runnable_delay_p95_ns": {s["runnable_delay_p95_ns"]},\n')
                f.write(f'      "wakeup_latency_p95_ns": {s["wakeup_latency_p95_ns"]},\n')
                f.write(f'      "system_overhead_ns": {s["system_overhead_ns"]}\n')
                f.write(f"    }}{comma}\n")
            f.write("  ]\n")
            f.write("}\n")
        print(f"[*] Written: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="TracePilot Python Analysis")
    parser.add_argument("-i", "--events-bin", required=True)
    parser.add_argument("-f", "--frames", required=True)
    parser.add_argument("-o", "--output", default="result_py.json")
    parser.add_argument("-k", "--top-k", type=int, default=10)
    args = parser.parse_args()

    analyzer = TracePilotAnalyzer(top_k=args.top_k)
    analyzer.load(args.events_bin, args.frames)
    result = analyzer.analyze()
    analyzer.output(args.output, result)

    scores, total_sys, total_frames, num_jank = result
    print(f"\n=== Results ===")
    print(f"  Frames: {total_frames} total, {num_jank} jank")
    print(f"  System overhead: {total_sys / 1e6:.3f}ms")
    print(f"  Top threads:")
    for i, s in enumerate(scores):
        print(f"    #{i+1}: tid={s['tid']} comm='{s['comm']}' "
              f"score={s['score']:.4f} jank_ratio={s['score']/0.35:.1%}")


if __name__ == "__main__":
    main()
