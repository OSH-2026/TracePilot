import struct

SCHED_EVENT_SIZE = 96
EVENTS_BIN = r"D:\osh大作业\页面切换-基础版\output\tracepilot_events.bin"

with open(EVENTS_BIN, "rb") as f:
    header = f.read(24)
    magic, ver, sched_cnt, sys_cnt = struct.unpack_from("<IIQQ", header, 0)
    if magic != 0x32765054:
        sched_cnt = 319753432 // SCHED_EVENT_SIZE  # approximate
    print(f"sched events: {sched_cnt}")
    
    # Read first 5000 switch events, analyze prev_state distribution
    state_counts = {}
    rd_nonzero = 0
    wl_nonzero = 0
    count = 0
    
    for i in range(min(10000, sched_cnt)):
        raw = f.read(SCHED_EVENT_SIZE)
        if len(raw) < SCHED_EVENT_SIZE: break
        etype = struct.unpack_from("<I", raw, 8)[0]
        if etype != 0: continue  # only SWITCH events
        
        prev_state = struct.unpack_from("<Q", raw, 32)[0]
        wl = struct.unpack_from("<Q", raw, 80)[0]
        rd = struct.unpack_from("<Q", raw, 88)[0]
        
        # Just track the low 16 bits for state type
        state_low = prev_state & 0xFFFF
        state_counts[state_low] = state_counts.get(state_low, 0) + 1
        
        if rd > 0: rd_nonzero += 1
        if wl > 0: wl_nonzero += 1
        count += 1

    print(f"\nTotal SWITCH events checked: {count}")
    print(f"rd > 0: {rd_nonzero} ({100*rd_nonzero/max(1,count):.1f}%)")
    print(f"wl > 0: {wl_nonzero} ({100*wl_nonzero/max(1,count):.1f}%)")
    print(f"\nprev_state distribution (low 16 bits):")
    for state, cnt in sorted(state_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  state={state} (0x{state:04X}): {cnt} ({100*cnt/count:.1f}%)")
