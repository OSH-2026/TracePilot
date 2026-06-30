#!/usr/bin/env python3
"""PageTurning模块 - 探针挂载点校验"""

PROBES = [
    {
        "name": "sched_switch",
        "type": "tracepoint",
        "func": "trace_event_raw_sched_switch",
    },
    {
        "name": "binder_transaction",
        "type": "kprobe",
        "func": "security_binder_transaction",
    },
]

REQUIRED_FIELDS = ["name", "type", "func"]


def validate_probes():
    """校验探针定义完整性"""
    for i, p in enumerate(PROBES):
        for field in REQUIRED_FIELDS:
            if field not in p:
                print(f"[FAIL] 探针 #{i} 缺少字段: {field}")
                return False
        print(f"[OK] 探针 {p['name']} ({p['type']}) -> {p['func']}")
    return True


def main():
    if validate_probes():
        print(f"\n[PASS] {len(PROBES)} 个探针定义校验通过")
    else:
        print("\n[FAIL] 探针定义不完整")
        exit(1)


if __name__ == "__main__":
    main()