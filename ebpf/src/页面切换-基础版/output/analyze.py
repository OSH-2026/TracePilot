import struct, json, os, sys, math

EVENTS_FILE = os.path.join(os.path.dirname(__file__), "tracepilot_events.bin")
FRAMES_FILE = os.path.join(os.path.dirname(__file__), "frames.txt")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "result_py.json")
DEBUG_FILE = os.path.join(os.path.dirname(__file__), "debug.txt")

log_lines = []

def log(s):
    log_lines.append(s)

def safe_decode(raw):
    null_idx = raw.find(b'\x00')
    if null_idx >= 0:
        raw = raw[:null_idx]
    return raw.decode('utf-8', errors='replace')

# ─── Read events.bin ────────────────────────────────────────────
log("=== events.bin header ===")
with open(EVENTS_FILE, "rb") as f:
    header = f.read(24)
    magic, version, sched_cnt, sys_cnt = struct.unpack_from("<IIQQ", header, 0)
    log(f"  magic=0x{magic:08X} (expected TPv2=0x32765054) version={version}")
    log(f"  sched_events={sched_cnt}  sys_events={sys_cnt}")

    SCHED_EVENT_SIZE = 96
    raw_sched = f.read(sched_cnt * SCHED_EVENT_SIZE)
    SYS_EVENT_SIZE = 32
    raw_sys = f.read(sys_cnt * SYS_EVENT_SIZE)

# ─── Debug: show first 20 events ───
log("\n=== Debug: first 20 sched events ===")
for i in range(min(20, sched_cnt)):
    off = i * SCHED_EVENT_SIZE
    evt = raw_sched[off:off + SCHED_EVENT_SIZE]
    ts = struct.unpack_from("<Q", evt, 0)[0]
    etype = struct.unpack_from("<I", evt, 8)[0]
    prev_pid = struct.unpack_from("<I", evt, 12)[0]
    prev_tid = struct.unpack_from("<I", evt, 16)[0]
    next_pid = struct.unpack_from("<I", evt, 20)[0]
    next_tid = struct.unpack_from("<I", evt, 24)[0]
    next_uid = struct.unpack_from("<I", evt, 28)[0]
    prev_state = struct.unpack_from("<Q", evt, 32)[0]
    cpu = struct.unpack_from("<I", evt, 72)[0]
    wl = struct.unpack_from("<Q", evt, 80)[0]
    rd = struct.unpack_from("<Q", evt, 88)[0]

    prev_comm_raw = evt[40:56]
    next_comm_raw = evt[56:72]
    prev_comm = safe_decode(prev_comm_raw)
    next_comm = safe_decode(next_comm_raw)

    etype_str = "SW" if etype == 0 else ("WK" if etype == 1 else f"UNK({etype})")
    log(f"  #{i}: ts={ts} type={etype_str}")
    log(f"    prev: pid={prev_pid} tid={prev_tid} comm='{prev_comm}' state={prev_state}")
    log(f"    next: pid={next_pid} tid={next_tid} uid={next_uid} comm='{next_comm}'")
    log(f"    cpu={cpu} wl={wl} rd={rd}")
    if i < 3:
        log(f"    prev_comm hex: {prev_comm_raw.hex()}")
        log(f"    next_comm hex: {next_comm_raw.hex()}")

# ─── Timestamp ranges ───
if sched_cnt > 0:
    first_ts = struct.unpack_from("<Q", raw_sched, 0)[0]
    last_ts = struct.unpack_from("<Q", raw_sched, (sched_cnt - 1) * SCHED_EVENT_SIZE)[0]
    log(f"\n=== Sched event timestamp range ===")
    log(f"  first: {first_ts} ns ({first_ts / 1e9:.3f}s)")
    log(f"  last: {last_ts} ns ({last_ts / 1e9:.3f}s)")
    log(f"  duration: {(last_ts - first_ts) / 1e9:.3f}s")

if sys_cnt > 0:
    first_sys_ts = struct.unpack_from("<Q", raw_sys, 0)[0]
    last_sys_ts = struct.unpack_from("<Q", raw_sys, (sys_cnt - 1) * SYS_EVENT_SIZE)[0]
    log(f"\n=== System event timestamp range ===")
    log(f"  first: {first_sys_ts} ns ({first_sys_ts / 1e9:.3f}s)")
    log(f"  last: {last_sys_ts} ns ({last_sys_ts / 1e9:.3f}s)")

# ─── Count non-zero wl and rd ───
wl_count = 0
rd_count = 0
wl_sum = 0
rd_sum = 0
for i in range(sched_cnt):
    off = i * SCHED_EVENT_SIZE
    evt = raw_sched[off:off + SCHED_EVENT_SIZE]
    etype = struct.unpack_from("<I", evt, 8)[0]
    if etype == 0:  # SWITCH
        wl = struct.unpack_from("<Q", evt, 80)[0]
        rd = struct.unpack_from("<Q", evt, 88)[0]
        if wl > 0:
            wl_count += 1
            wl_sum += wl
        if rd > 0:
            rd_count += 1
            rd_sum += rd
log(f"\n=== Non-zero metrics in SWITCH events ===")
log(f"  wakeup_latency > 0: {wl_count}/{sched_cnt} events, avg={wl_sum / max(1, wl_count) / 1e6:.3f}ms")
log(f"  runnable_delay > 0: {rd_count}/{sched_cnt} events, avg={rd_sum / max(1, rd_count) / 1e6:.3f}ms")

# ─── Unique comms ───
unique_comms = set()
unique_tids = set()
for i in range(sched_cnt):
    off = i * SCHED_EVENT_SIZE
    evt = raw_sched[off:off + SCHED_EVENT_SIZE]
    etype = struct.unpack_from("<I", evt, 8)[0]
    next_tid = struct.unpack_from("<I", evt, 24)[0]
    prev_tid = struct.unpack_from("<I", evt, 16)[0]
    next_comm = safe_decode(evt[56:72])
    prev_comm = safe_decode(evt[40:56])
    if next_tid > 0:
        unique_comms.add(next_comm)
        unique_tids.add((next_tid, next_comm))
    if etype == 0 and prev_tid > 0:
        unique_comms.add(prev_comm)
        unique_tids.add((prev_tid, prev_comm))

log(f"\n=== Unique comms ({len(unique_comms)}) ===")
for c in sorted(unique_comms):
    log(f"  '{c}'")

log(f"\n=== Unique TID+comm pairs ({len(unique_tids)}) [first 50] ===")
for i, (tid, comm) in enumerate(sorted(unique_tids, key=lambda x: x[0])):
    if i >= 50: break
    log(f"  tid={tid} comm='{comm}'")

# ─── Write debug output ───
with open(DEBUG_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(log_lines))
print(f"Debug output written to {DEBUG_FILE}")
print(f"Last few lines:")
for line in log_lines[-20:]:
    print(line)
