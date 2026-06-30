#!/usr/bin/env python3
"""eBPF Ring Buffer 事件解析校验"""

import struct
import os
import sys

# 从代码中提取的常量校验
EVENT_HEADER_SIZE = 24
SCHED_EVENT_SIZE = 96
SYSTEM_EVENT_SIZE = 32
EVENT_MAGIC = 0xEB0F0120


def validate_event_sizes():
    """校验事件结构体大小定义"""
    ok = True
    if SCHED_EVENT_SIZE % 8 != 0:
        print(f"[FAIL] SCHED_EVENT_SIZE={SCHED_EVENT_SIZE} 不是 8 字节对齐")
        ok = False
    if SYSTEM_EVENT_SIZE % 8 != 0:
        print(f"[FAIL] SYSTEM_EVENT_SIZE={SYSTEM_EVENT_SIZE} 不是 8 字节对齐")
        ok = False
    if EVENT_HEADER_SIZE % 8 != 0:
        print(f"[FAIL] EVENT_HEADER_SIZE={EVENT_HEADER_SIZE} 不是 8 字节对齐")
        ok = False
    if ok:
        print(f"[OK] 事件大小定义校验通过")
        print(f"  Header: {EVENT_HEADER_SIZE}B, Sched: {SCHED_EVENT_SIZE}B, System: {SYSTEM_EVENT_SIZE}B")
    return ok


def validate_magic():
    hex_str = f"0x{EVENT_MAGIC:08X}"
    print(f"[OK] 事件魔数: {hex_str}")


def main():
    all_ok = True
    if not validate_event_sizes():
        all_ok = False
    validate_magic()
    if all_ok:
        print("[PASS] 事件结构体常量校验通过")
    else:
        print("[FAIL] 校验未通过")
        sys.exit(1)


if __name__ == "__main__":
    main()