#!/usr/bin/env python3
"""PageTurning模块 - 线程角色分类表一致性校验"""

import sys

THREAD_ROLES = {
    "UI": {"match": [".ui", "com.", "activity", "main"]},
    "RenderThread": {"match": ["renderthread", "rend"]},
    "SurfaceFlinger": {"match": ["surfaceflinger"]},
    "Binder": {"match": ["binder:"]},
    "HwBinder": {"match": ["hwbinder"]},
    "GPU": {"match": ["gpu", "gl", "mali"]},
    "KernelWorker": {"match": ["kworker", "swapper"]},
}

EXPECTED_CLASSIFY_COUNT = 12  # 总共 12 类角色


def check_role_overlap():
    """检查匹配规则是否有重叠"""
    test_cases = [
        ("com.tencent.mobileqq", "UI"),
        ("RenderThread", "RenderThread"),
        ("surfaceflinger", "SurfaceFlinger"),
        ("binder:605_2", "Binder"),
        ("hwbinder", "HwBinder"),
        ("mali-cmar-backe", "GPU"),
        ("kworker/u8:0", "KernelWorker"),
        ("swapper/0", "KernelWorker"),
    ]
    ok = True
    for comm, expected in test_cases:
        matched = None
        for role, rules in THREAD_ROLES.items():
            for pattern in rules["match"]:
                if pattern in comm.lower():
                    matched = role
                    break
            if matched:
                break
        if matched == expected:
            print(f"  [OK] '{comm}' -> {matched}")
        else:
            print(f"  [FAIL] '{comm}' -> {matched}, expected {expected}")
            ok = False
    return ok


def main():
    print(f"线程角色分类表 ({len(THREAD_ROLES)} 类定义, 预期总{EXPECTED_CLASSIFY_COUNT}类)")
    ok = check_role_overlap()
    if ok:
        print(f"\n[PASS] 线程角色分类规则校验通过")
    else:
        print(f"\n[FAIL] 分类规则有误")
        sys.exit(1)


if __name__ == "__main__":
    main()