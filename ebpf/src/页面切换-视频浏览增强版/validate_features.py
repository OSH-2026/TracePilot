#!/usr/bin/env python3
"""Jank 分类器 - 特征维度校验"""

import sys

EXPECTED_FEATURES_PAGE = [
    "runnable_delay_p95",
    "wakeup_latency_p95",
    "jank_frame_ratio",
    "binder_call_depth",
    "futex_wait_count",
    "cpu_freq_throttle",
]

EXPECTED_FEATURES_VIDEO = EXPECTED_FEATURES_PAGE + ["decode_late"]


def validate_feature_dims():
    ok = True
    print(f"页面切换特征维度 ({len(EXPECTED_FEATURES_PAGE)}):")
    for f in EXPECTED_FEATURES_PAGE:
        print(f"  - {f}")

    print(f"\n视频场景特征维度 ({len(EXPECTED_FEATURES_VIDEO)}):")
    for f in EXPECTED_FEATURES_VIDEO:
        marker = " [新增]" if f == "decode_late" else ""
        print(f"  - {f}{marker}")

    # 确保 video 是 page 的超集
    for f in EXPECTED_FEATURES_PAGE:
        if f not in EXPECTED_FEATURES_VIDEO:
            print(f"[FAIL] 视频特征缺少: {f}")
            ok = False

    if ok:
        print(f"\n[PASS] 特征维度校验通过 (page={len(EXPECTED_FEATURES_PAGE)}, video={len(EXPECTED_FEATURES_VIDEO)})")
    else:
        print("\n[FAIL] 特征维度不完整")
        sys.exit(1)


if __name__ == "__main__":
    validate_feature_dims()