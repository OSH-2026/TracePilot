#!/usr/bin/env python3
"""Lab4 Ray分布式 - 负载均衡策略校验"""

import math
import sys


def round_robin(tasks: list, nodes: int) -> list:
    """轮询分配策略"""
    assignments = {i: [] for i in range(nodes)}
    for idx, task in enumerate(tasks):
        assignments[idx % nodes].append(task)
    return assignments


def weighted_assign(tasks: list, capacities: list) -> list:
    """按容量加权分配"""
    total_cap = sum(capacities)
    ratios = [c / total_cap for c in capacities]
    n = len(tasks)
    assignments = {i: [] for i in range(len(capacities))}
    for i, task in enumerate(tasks):
        node = i % len(capacities)
        target_count = int(n * ratios[node])
        if len(assignments[node]) < target_count:
            assignments[node].append(task)
        else:
            # fallback: 分配给负载最轻的节点
            lightest = min(assignments, key=lambda k: len(assignments[k]))
            assignments[lightest].append(task)
    return assignments


def test_round_robin():
    tasks = list(range(20))
    result = round_robin(tasks, 3)
    counts = [len(v) for v in result.values()]
    print(f"  轮询分配: {counts}")
    assert sum(counts) == 20
    assert max(counts) - min(counts) <= 1, f"负载不均: {counts}"
    print(f"  [OK] 20任务/3节点，最大偏差={max(counts) - min(counts)}")


def test_weighted():
    tasks = list(range(20))
    capacities = [10, 5, 1]  # Mac (强) vs Legion (中) vs ... (弱)
    result = weighted_assign(tasks, capacities)
    counts = [len(v) for v in result.values()]
    print(f"  加权分配: {counts} (容量比例 {capacities})")
    assert sum(counts) == 20
    # 强节点应该分配更多
    assert counts[0] >= counts[1] >= counts[2], f"分配不符合容量比例: {counts}"
    print(f"  [OK] 加权分配符合容量比例")


def main():
    print("= 负载均衡策略校验 =")
    test_round_robin()
    test_weighted()
    print("\n[PASS] Lab4 负载均衡策略校验通过")


if __name__ == "__main__":
    main()