# ebpf/src — eBPF 源码目录

本目录包含各场景的 BPF 探针和用户态 loader 源码。

## 子目录

| 目录 | 场景 | 说明 |
|------|------|------|
| `页面切换-基础版/` | QQ 页面切换 | 基础版 BPF 探针 (6 探针) + loader |
| `页面切换-视频浏览增强版/` | 微信/抖音 | 增强版 BPF 探针 (13 探针) + 完整 loader 模块 |
| `camera/` | Google Camera | Camera Scheduling Analyzer 全自动 Pipeline |
| `page_turning/` | Page Turning | 页面翻页场景 BPF 探针 |
