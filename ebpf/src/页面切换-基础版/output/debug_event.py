import struct
f = open("/mnt/d/osh大作业/页面切换-基础版/output/tracepilot_events.bin", "rb")
f.read(24)
for i in range(200):
    evt = f.read(96)
    if len(evt) < 96: break
    etype = struct.unpack_from("<I", evt, 8)[0]
    if etype == 0:  # sched_switch
        ts = struct.unpack_from("<Q", evt, 0)[0]
        next_tid = struct.unpack_from("<I", evt, 24)[0]
        next_pid = struct.unpack_from("<I", evt, 20)[0]
        prev_tid = struct.unpack_from("<I", evt, 16)[0]
        nc = evt[56:72].split(b'\x00')[0].decode('utf-8', errors='replace')
        pc = evt[40:56].split(b'\x00')[0].decode('utf-8', errors='replace')
        cpu = struct.unpack_from("<I", evt, 72)[0]
        wl = struct.unpack_from("<Q", evt, 80)[0]
        rd = struct.unpack_from("<Q", evt, 88)[0]
        print(f"event#{i}: ts={ts} next_tid={next_tid} next_comm='{nc}' prev_comm='{pc}' cpu={cpu} wl={wl} rd={rd}")
        if i >= 5:
            break
f.close()
