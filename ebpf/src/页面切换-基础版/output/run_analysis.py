import struct, os, json, math

SCHED_EVENT_SIZE = 96
SYS_EVENT_SIZE = 32
EVENTS_MAGIC = 0x32765054
PRE_MARGIN_NS = 20_000_000
POST_MARGIN_NS = 10_000_000

BASE = r"D:\osh大作业\页面切换-基础版\output"
EVENTS_BIN = os.path.join(BASE, "tracepilot_events.bin")
FRAMES_TXT = os.path.join(BASE, "frames.txt")
OUT_JSON = os.path.join(BASE, "result_py.json")
ERR_FILE = os.path.join(BASE, "py_error.txt")
SUCCESS_FILE = os.path.join(BASE, "py_success.txt")

try:
    # ─── Load events ───
    with open(EVENTS_BIN, "rb") as f:
        header = f.read(24)
        magic, ver, sched_cnt, sys_cnt = struct.unpack_from("<IIQQ", header, 0)
        if magic != EVENTS_MAGIC:
            f.seek(0)
            sched_cnt = os.fstat(f.fileno()).st_size // SCHED_EVENT_SIZE
            sys_cnt = 0
        sched_raw = f.read(sched_cnt * SCHED_EVENT_SIZE)
        sys_raw = f.read(sys_cnt * SYS_EVENT_SIZE)

    # ─── Parse frames ───
    class Frame:
        __slots__ = ('token','es','ee','ae','is_jank','sys_oh','ws','we')
        def __init__(self, token, es, ee, ae, is_jank):
            self.token = token; self.es = es; self.ee = ee; self.ae = ae
            self.is_jank = is_jank; self.sys_oh = 0
            ref_s = self.ae if (is_jank and self.ae < self.es) else self.es
            self.ws = max(0, ref_s - PRE_MARGIN_NS)
            ref_e = self.ee if (is_jank and self.ee > self.ae) else self.ae
            self.we = ref_e + POST_MARGIN_NS
        def contains(self, adj): return self.ws <= adj <= self.we

    frames = []
    with open(FRAMES_TXT, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line[0] in ('"', '-'): continue
            parts = line.split(',')
            if len(parts) < 6: continue
            try:
                token = int(parts[0])
                es = int(parts[2]) if len(parts) > 2 else 0
                ee = int(parts[3]) if len(parts) > 3 else 0
                ae = int(parts[4])
                is_jank = int(parts[5])
            except: continue
            frames.append(Frame(token, es, ee, ae, is_jank))

    num_jank = sum(1 for f in frames if f.is_jank)
    jank_frames = [f for f in frames if f.is_jank]

    # ─── Clock offset ───
    min_ebpf = struct.unpack_from("<Q", sched_raw, 0)[0]
    min_frame = frames[0].es
    offset = int(min_frame) - int(min_ebpf)

    # ─── System overhead ───
    for i in range(sys_cnt):
        soff = i * SYS_EVENT_SIZE
        ts = struct.unpack_from("<Q", sys_raw, soff)[0]
        dur = struct.unpack_from("<Q", sys_raw, soff + 24)[0]
        adj = int(ts) + offset
        for fw in jank_frames:
            if fw.contains(adj):
                fw.sys_oh += dur

    total_sys = sum(fw.sys_oh for fw in jank_frames)

    # ─── Thread aggregation with set-based per-frame dedup ───
    threads = {}  # tid -> {comm, matched_frames: set, sys_oh, wl_sum, wl_cnt}

    for i in range(sched_cnt):
        off = i * SCHED_EVENT_SIZE
        raw = sched_raw[off:off + SCHED_EVENT_SIZE]
        etype = struct.unpack_from("<I", raw, 8)[0]
        prev_tid = struct.unpack_from("<I", raw, 16)[0]
        next_tid = struct.unpack_from("<I", raw, 24)[0]
        ts = struct.unpack_from("<Q", raw, 0)[0]
        wl = struct.unpack_from("<Q", raw, 80)[0]
        rd = struct.unpack_from("<Q", raw, 88)[0]
        adj = int(ts) + offset

        prev_comm = raw[40:56].split(b'\x00')[0].decode('utf-8', errors='replace')
        next_comm = raw[56:72].split(b'\x00')[0].decode('utf-8', errors='replace')

        if etype == 0:  # SWITCH
            tid = prev_tid; comm = prev_comm
        else:           # WAKEUP
            tid = next_tid; comm = next_comm

        if tid <= 0:
            continue

        if tid not in threads:
            threads[tid] = {'comm': comm, 'frames': set(), 'sys_oh': 0,
                           'wl_samples': [], 'rd_samples': []}
        t = threads[tid]
        if not t['comm'] and comm:
            t['comm'] = comm

        # Filter known collection noise by comm pattern
        _c = t['comm']
        if _c == 'tracepilot' or 'shell svc' in _c or _c == 'irq/354-dwc3' or _c == 'adbd' or 'UsbFfs' in _c:
            continue

        for fw in jank_frames:
            if fw.contains(adj):
                if fw.token not in t['frames']:
                    t['frames'].add(fw.token)
                    t['sys_oh'] += fw.sys_oh
                if wl > 0:
                    t['wl_samples'].append(wl)
                if rd > 0:
                    t['rd_samples'].append(rd)

    # ─── Scoring ───
    def p95(samples):
        if not samples: return 0
        s = sorted(samples)
        idx = int(len(s) * 0.95)
        if idx >= len(s): idx = len(s) - 1
        return s[idx]

    scores = []
    for tid, t in threads.items():
        jank_cnt = len(t['frames'])
        if jank_cnt == 0:
            continue

        rd_p95_ns = p95(t['rd_samples'])
        wl_p95_ns = p95(t['wl_samples'])

        j_ratio = jank_cnt / num_jank
        rd_ms = rd_p95_ns / 1e6
        wl_ms = wl_p95_ns / 1e6

        score = 0.0
        score += 0.35 * j_ratio
        score += 0.35 * math.log1p(rd_ms)
        score += 0.15 * math.log1p(wl_ms)
        if "RenderThread" in t['comm'] or ".ui" in t['comm']:
            score += 0.15

        avg_oh = t['sys_oh'] / max(1, jank_cnt)
        sys_ratio = min(avg_oh / 16666666.0, 0.9)
        score *= (1.0 - sys_ratio)

        scores.append({
            'tid': tid, 'comm': t['comm'], 'score': score,
            'frames': jank_cnt, 'sys_oh_ns': int(t['sys_oh']),
            'rd_p95': int(rd_p95_ns), 'wl_p95': int(wl_p95_ns)
        })

    scores.sort(key=lambda x: x['score'], reverse=True)
    top_k = scores[:20]

    # ─── Output JSON ───
    result = {
        "total_frames": len(frames),
        "jank_frames": num_jank,
        "jank_system_overhead_ns": int(total_sys),
        "top_k_threads": [
            {
                "rank": i+1, "tid": s['tid'], "pid": 0,
                "comm": s['comm'], "package": "",
                "score": round(s['score'], 4),
                "runnable_delay_p95_ns": s['rd_p95'],
                "wakeup_latency_p95_ns": s['wl_p95'],
                "system_overhead_ns": s['sys_oh_ns']
            }
            for i, s in enumerate(top_k)
        ]
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    with open(SUCCESS_FILE, "w", encoding="utf-8") as f:
        f.write(f"OK: {len(frames)} frames, {num_jank} jank, {len(top_k)} top threads\n")
        f.write(f"Total sys overhead: {total_sys / 1e6:.3f}ms\n")
        f.write(f"Total unique threads: {len(threads)}\n\n")
        for i, s in enumerate(top_k):
            f.write(f"  #{i+1}: tid={s['tid']} comm='{s['comm']}' "
                    f"score={s['score']:.4f} frames={s['frames']}/{num_jank} "
                    f"sys_oh={s['sys_oh_ns']/1e6:.1f}ms\n")

except Exception as e:
    import traceback
    with open(ERR_FILE, "w", encoding="utf-8") as f:
        f.write(f"ERROR: {e}\n{traceback.format_exc()}")
