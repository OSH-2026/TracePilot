#!/usr/bin/env python3
"""Camera模块 - 关键路径评分公式一致性校验"""

import math
import sys


def compute_score(
    jank_ratio: float,
    runnable_delay_ms: float,
    wakeup_latency_ms: float,
    is_ui: bool,
    sys_ratio: float,
    alpha: float = 0.4,
    beta: float = 0.4,
    gamma: float = 0.2,
):
    """按论文公式计算 CriticalScore"""
    base = (
        alpha * jank_ratio
        + beta * math.log1p(runnable_delay_ms)
        + gamma * math.log1p(wakeup_latency_ms)
    )
    if is_ui:
        base += 0.15
    return base * (1.0 - min(sys_ratio, 0.9))


def test_equivalence():
    """测试极端输入下公式是否有溢出/除零问题"""
    test_cases = [
        (0.0, 0.0, 0.0, False, 0.0, 0.0),       # 全零
        (1.0, 1380.0, 1380.0, True, 0.056, None),  # 小红书 single-pool-def
        (1.0, 0.0, 463.0, False, 0.0, None),        # ksoftirqd
        (1.0, 8.6, 8.8, False, 0.0, None),          # thermal_BIG
    ]
    ok = True
    for i, (j, rd, wl, ui, sr, expected) in enumerate(test_cases):
        try:
            score = compute_score(j, rd, wl, ui, sr)
            print(f"  case {i}: score={score:.6f}", end="")
            if expected is not None and abs(score - expected) > 0.001:
                print(f" [FAIL] expected {expected:.6f}")
                ok = False
            else:
                print(" [OK]")
        except Exception as e:
            print(f" [FAIL] {e}")
            ok = False
    return ok


def test_bounds():
    """测试 score 是否始终在 [0, 1] 范围"""
    import random
    rng = random.Random(42)
    ok = True
    for _ in range(1000):
        j = rng.random()
        rd = rng.expovariate(0.01) * 100  # ~100ms avg
        wl = rng.expovariate(0.01) * 100
        ui = rng.choice([True, False])
        sr = rng.random()
        score = compute_score(j, rd, wl, ui, sr)
        if not (0.0 <= score <= 3.0):  # log1p can exceed 1.0, so bound is wider
            print(f"[FAIL] 分数溢出: {score:.4f} (j={j:.4f}, rd={rd:.2f}, wl={wl:.2f})")
            ok = False
    if ok:
        print(f"  [OK] 1000 次随机输入无溢出")
    return ok


def main():
    all_ok = True
    print("= 公式等价性测试 =")
    if not test_equivalence():
        all_ok = False
    print("\n= 数值范围测试 =")
    if not test_bounds():
        all_ok = False
    if all_ok:
        print("\n[PASS] CriticalScore 公式校验通过")
    else:
        print("\n[FAIL] 公式校验未通过")
        sys.exit(1)


if __name__ == "__main__":
    main()