# TracePilot — 页面切换与视频浏览增强版

基于 eBPF + Perfetto 的 **交互关键路径图（Interaction Critical Path Graph）** Android 卡顿观测与 Hint 系统。支持双场景分析 + Step 3 扩展（温控深化、Inference 证据链、多会话对比）。

- **页面切换**（`--scenario page_switch`）— UI 帧 vsync 超时
- **视频播放**（`--scenario video`）— 解码丢帧 + 温控降频 + GPU stall

## 核心能力

| 层级 | 功能 |
|------|------|
| **Step 1** | Perfetto 帧 + eBPF sched + 身份解析 + runnable delay + Hint Engine（TTL/rollback） |
| **Step 2** | Binder/Futex 图、CPU 频率、Jank 分类器、启发式对比 |
| **Step 3** | Thermal 深化、Inference-aware 证据融合、Multi-session 对比 |

## 目录结构

```
output/
├── page_switch/          # 页面切换采集（deploy --scenario page_switch）
│   ├── events.bin
│   ├── frames.txt
│   ├── thermal_profile.txt
│   └── result.json
├── video/                # 视频浏览采集
│   └── ...
└── compare_report.json   # 双场景/多 App 对比报告
```

## 快速开始

```bash
make bpf && make loader && make android

# 采集（分两次，不要混采）
./scripts/deploy.sh --scenario page_switch --package com.your.app --duration 30
./scripts/deploy.sh --scenario video       --package com.your.app --duration 30

# 离线分析（deploy 已自动跑，也可手动）
./output/tracepilot -i output/page_switch/events.bin \
  -f output/page_switch/frames.txt \
  --thermal-data output/page_switch/thermal_profile.txt \
  -o output/page_switch/result.json -G -s page_switch -k 10

# Step 3：多会话对比
./output/tracepilot --compare-dir output \
  --compare-out output/compare_report.json
```

详细说明见 [`使用说明.md`](使用说明.md)，实施计划见 [`实施计划.md`](实施计划.md)。
