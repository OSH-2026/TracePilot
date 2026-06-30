#!/usr/bin/env python3
"""页面切换基础版 - 数据完整性校验"""

import json
import csv
import os
import sys

DATA_DIR = os.path.join(os.path.dirname(__file__), "../../../ebpf_data/页面切换-基础版数据")

def check_file_exists(path: str) -> bool:
    if not os.path.exists(path):
        print(f"[FAIL] 缺少文件: {path}")
        return False
    print(f"[OK]  文件存在: {path}")
    return True

def check_frames_file(path: str) -> bool:
    if not check_file_exists(path):
        return False
    with open(path) as f:
        lines = f.readlines()
    frame_count = len([l for l in lines if l.strip() and not l.startswith("#")])
    print(f"  -> 帧数量: {frame_count}")
    return frame_count > 0

def check_result_json(path: str) -> bool:
    if not check_file_exists(path):
        return False
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        print(f"  -> Top-K 条目数: {len(data)}")
    elif isinstance(data, dict):
        print(f"  -> JSON 键: {list(data.keys())[:5]}")
    return True

def main():
    all_ok = True
    checks = [
        ("frames.txt", check_frames_file),
        ("result_py.json", check_result_json),
        ("py_success.txt", check_file_exists),
    ]
    for filename, checker in checks:
        path = os.path.join(DATA_DIR, filename)
        if not checker(path):
            all_ok = False

    if all_ok:
        print("\n[PASS] 页面切换基础版数据完整性校验通过")
    else:
        print("\n[FAIL] 部分数据文件校验未通过")
        sys.exit(1)

if __name__ == "__main__":
    main()