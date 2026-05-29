import struct, os

BASE = r"D:\osh大作业\页面切换-基础版\output"
EVENTS_BIN = os.path.join(BASE, "tracepilot_events.bin")
FRAMES_TXT = os.path.join(BASE, "frames.txt")

SCHED_EVENT_SIZE = 96
PRE_MARGIN_NS = 20_000_000
POST_MARGIN_NS = 10_000_000

# Load frames
frames = []
with open(FRAMES_TXT, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        line = line.strip()
        if not line or line[0] in ('"', '-'): continue
        parts = line.split(',')
        if len(parts) < 6: continue
        token = int(parts[0])
        es = int(parts[2]) if len(parts) > 2 else 0
        ee = int(parts[3]) if len(parts) > 3 else 0
        ae = int(parts[4])
        is_jank = int(parts[5])
        # Compute window
        ref_s = ae if (is_jank and ae < es) else es
        ws = ref_s - PRE_MARGIN_NS if ref_s > PRE_MARGIN_NS else 0
        ref_e = ee if (is_jank and ee > ae) else ae
        we = ref_e + POST_MARGIN_NS
        frames.append((token, ws, we, is_jank))

jank_frames = [(t, ws, we) for t, ws, we, jk in frames if jk]
print(f"Frames: {len(frames)} total, {len(jank_frames)} jank")

# Load events
with open(EVENTS_BIN, "rb") as f:
    header = f.read(24)
    magic, ver, sched_cnt, sys_cnt = struct.unpack_from("<IIQQ", header, 0)
    EVENTS_MAGIC = 0x32765054
    if magic != EVENTS_MAGIC:
        sched_cnt = os.fstat(f.fileno()).st_size // SCHED_EVENT_SIZE
    sched_raw = f.read(sched_cnt * SCHED_EVENT_SIZE)

# Clock offset
min_ebpf = struct.unpack_from("<Q", sched_raw, 0)[0]
offset = int(frames[0][1]) - int(min_ebpf)  # frames[0].es = frames[0][1]... wait

# Actually need raw frame data too
frames_raw = []
with open(FRAMES_TXT, "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        line = line.strip()
        if not line or line[0] in ('"', '-'): continue
        parts = line.split(',')
        if len(parts) < 6: continue
        token = int(parts[0])
        es = int(parts[2]) if len(parts) > 2 else 0
        ee = int(parts[3]) if len(parts) > 3 else 0
        ae = int(parts[4])
        is_jank = int(parts[5])
        frames_raw.append((token, es, ee, ae, is_jank))

min_frame = frames_raw[0][1]  # es
offset = int(min_frame) - int(min_ebpf)
print(f"Offset: {offset / 1e9:.3f}s")

# Recompute frame windows with proper data
class FW:
    def __init__(self, token, es, ee, ae, jk):
        self.t = token; self.jk = jk
        ref_s = ae if (jk and ae < es) else es
        self.ws = ref_s - PRE_MARGIN_NS if ref_s > PRE_MARGIN_NS else 0
        ref_e = ee if (jk and ee > ae) else ae
        self.we = ref_e + POST_MARGIN_NS

jfw = [FW(t, es, ee, ae, jk) for t, es, ee, ae, jk in frames_raw if jk]

# Track tid=1
matched_tokens = set()
total_events = 0
events_w_tid1 = 0
total_matches = 0

for i in range(sched_cnt):
    off = i * SCHED_EVENT_SIZE
    raw = sched_raw[off:off + SCHED_EVENT_SIZE]
    etype = struct.unpack_from("<I", raw, 8)[0]
    prev_tid = struct.unpack_from("<I", raw, 16)[0]
    next_tid = struct.unpack_from("<I", raw, 24)[0]
    ts = struct.unpack_from("<Q", raw, 0)[0]
    adj = int(ts) + offset
    
    total_events += 1
    
    if etype == 0: tid = prev_tid
    else: tid = next_tid
    if tid <= 0: continue
    
    if tid == 1:
        events_w_tid1 += 1
    
    for fw in jfw:
        if fw.ws <= adj <= fw.we:
            total_matches += 1
            if tid == 1:
                matched_tokens.add(fw.t)

print(f"Total events: {total_events}")
print(f"Events with tid=1: {events_w_tid1}")
print(f"Total event-frame matches: {total_matches}")
print(f"Unique frames matched by tid=1: {len(matched_tokens)}")
print(f"Matched tokens: {sorted(matched_tokens)[:10]}...")

# Now simulate dedup
last_token = -1
count = 0
for i in range(sched_cnt):
    off = i * SCHED_EVENT_SIZE
    raw = sched_raw[off:off + SCHED_EVENT_SIZE]
    etype = struct.unpack_from("<I", raw, 8)[0]
    prev_tid = struct.unpack_from("<I", raw, 16)[0]
    next_tid = struct.unpack_from("<I", raw, 24)[0]
    ts = struct.unpack_from("<Q", raw, 0)[0]
    adj = int(ts) + offset
    
    if etype == 0: tid = prev_tid
    else: tid = next_tid
    if tid != 1: continue
    
    for fw in jfw:
        if fw.ws <= adj <= fw.we:
            if last_token != fw.t:
                last_token = fw.t
                count += 1

print(f"\nDedup count for tid=1: {count}")
print(f"Expected max: {len(jfw)}")

# Show first few events with tid=1
print("\nFirst 5 events with tid=1:")
shown = 0
for i in range(sched_cnt):
    if shown >= 5: break
    off = i * SCHED_EVENT_SIZE
    raw = sched_raw[off:off + SCHED_EVENT_SIZE]
    etype = struct.unpack_from("<I", raw, 8)[0] 
    prev_tid = struct.unpack_from("<I", raw, 16)[0]
    ts = struct.unpack_from("<Q", raw, 0)[0]
    adj = int(ts) + offset
    
    if etype == 0: tid = prev_tid
    else: tid = struct.unpack_from("<I", raw, 24)[0]
    if tid != 1: continue
    
    matching = [fw.t for fw in jfw if fw.ws <= adj <= fw.we]
    print(f"  event#{i}: etype={etype} prev_tid={prev_tid} adj={adj} matches={matching}")
    shown += 1
