import struct
f = open("/mnt/d/osh大作业/页面切换-基础版/output/tracepilot_events.bin", "rb")
f.read(24)

# Find first sched_switch (type=0)
for i in range(200):
    evt = f.read(96)
    if len(evt) < 96: break
    etype = struct.unpack_from("<I", evt, 8)[0]
    if etype == 0:
        print(f"=== Sched_switch event #{i} at file offset {24 + i*96} ===")
        print("--- Raw bytes ---")
        for j in range(0, 96, 8):
            chunk = evt[j:j+8]
            ascii_repr = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            print(f"  {j:3d}: {chunk.hex():16s}  {ascii_repr}")
        
        # Check if there's readable text at different offsets
        print("\n--- Searching for readable strings ---")
        for j in range(0, 90):
            try:
                s = evt[j:j+16].split(b'\x00')[0].decode('ascii')
                if len(s) >= 2 and s.isprintable():
                    print(f"  offset {j}: '{s}'")
            except:
                pass
        
        print(f"\n--- Parsed fields (96-byte struct) ---")
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
        print(f"  ts={ts}, type={etype}")
        print(f"  prev_pid={prev_pid}, prev_tid={prev_tid}")
        print(f"  next_pid={next_pid}, next_tid={next_tid}, next_uid={next_uid}")
        print(f"  prev_state={prev_state}, cpu={cpu}")
        print(f"  wl={wl}, rd={rd}")
        
        # Try reading comm as fixed-length at various offsets
        print(f"\n  prev_comm[40:56]: '{evt[40:56]}'")
        print(f"  next_comm[56:72]: '{evt[56:72]}'")
        
        # Check if BPF might be storing comm at different offsets (like right after header fields)
        print(f"  raw[0:16]: '{evt[0:16]}'")  # timestamp + event_type + pids
        print(f"  raw[16:32]: '{evt[16:32]}'") # pids area
        
        break
f.close()
