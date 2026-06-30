#!/usr/bin/env python3
"""Camera模块 - Pipelinestage 依赖校验"""

import os
import sys

PIPELINE_STAGES = [
    "compile_ebpf",
    "deploy_binary",
    "collect_trace",
    "pull_data",
    "parse_trace",
    "analyze_delays",
    "critical_path",
    "root_cause",
    "generate_report",
]


def validate_stage_order():
    """校验 pipeline 阶段是否合理排列"""
    expected_order = {
        "compile_ebpf": 0,
        "deploy_binary": 1,
        "collect_trace": 2,
        "pull_data": 3,
        "parse_trace": 4,
        "analyze_delays": 5,
        "critical_path": 6,
        "root_cause": 7,
        "generate_report": 8,
    }
    for name, idx in expected_order.items():
        if name not in PIPELINE_STAGES:
            print(f"[FAIL] 阶段 {name} 未在定义列表中")
            return False
        if PIPELINE_STAGES.index(name) != idx:
            print(f"[FAIL] 阶段 {name} 顺序不正确")
            return False
    print(f"[OK] 共 {len(PIPELINE_STAGES)} 个阶段，顺序校验通过")
    return True


def main():
    all_ok = True
    if not validate_stage_order():
        all_ok = False
    if all_ok:
        print("[PASS] Pipeline 阶段校验通过")
    else:
        print("[FAIL] Pipeline 校验未通过")
        sys.exit(1)


if __name__ == "__main__":
    main()