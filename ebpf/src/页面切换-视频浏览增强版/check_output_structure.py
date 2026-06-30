#!/usr/bin/env python3
"""页面切换视频浏览增强版 - 输出目录结构校验"""

import os
import sys

OUTPUT_DIRS = [
    "output/page_switch",
    "output/video",
    "output/page_switch_run1",
]

REQUIRED_FILES = [
    "result.json",
    "frames.txt",
    "graph_subgraph.json",
    "graph_topology.json",
    "hints.json",
    "identity_map.json",
    "thermal_profile.txt",
]

def check_output_dir(base: str, name: str) -> bool:
    path = os.path.join(base, name)
    if not os.path.isdir(path):
        print(f"[SKIP] 目录不存在: {path}")
        return True
    ok = True
    for fname in REQUIRED_FILES:
        fpath = os.path.join(path, fname)
        if os.path.exists(fpath):
            size = os.path.getsize(fpath)
            print(f"  [OK] {name}/{fname} ({size} bytes)")
        else:
            print(f"  [MISS] {name}/{fname}")
            ok = False
    return ok

def main():
    base = os.path.join(os.path.dirname(__file__), "../../ebpf_data/页面切换-视频浏览数据")
    all_ok = True
    for d in OUTPUT_DIRS:
        if not check_output_dir(base, d):
            all_ok = False
    if all_ok:
        print("\n[PASS] 增强版输出结构校验通过")
    else:
        print("\n[WARN] 部分文件缺失，请检查数据目录")
        sys.exit(1)

if __name__ == "__main__":
    main()