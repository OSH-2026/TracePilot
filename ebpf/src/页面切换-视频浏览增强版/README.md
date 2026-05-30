# TracePilot — 页面切换与视频浏览增强版

基于 eBPF + Perfetto 的 **交互关键路径图（Interaction Critical Path Graph）** Android 卡顿观测系统。支持双场景分析：

- **页面切换**（默认）— 检测 UI 帧 vsync 超时
- **视频播放**（`--scenario video`）— 检测解码丢帧 + 温控降频 + GPU stall + 音画同步

所有已知局限性均已解决（Binder 精确匹配、CPU 频率轮询补充、betweenness 30x 加速、GPU stall 检测、音画同步检测、视频帧 fallback、hash map 扩容）。

## 核心能力

| 功能 | 说明 |
|------|------|
| **Binder dependency graph** | debug_id 精确匹配 CALL↔RECEIVED，Brandes betweenness centrality（k-sampling 加速） |
| **Futex wait graph** | 追踪 sys_enter/exit futex WAIT，构建 FUTEX_WAIT 边 |
| **CPU frequency / big-little** | BPF + 用户态轮询双通道采集，区分大小核频率 + 降频检测 |
| **Memory reclaim** | 追踪 mm_vmscan_direct_reclaim_begin，识别内存压力 |
| **CriticalScore(tid)** | 11-term 图算法加权公式（page_switch 和 video 双权重模板） |
| **Jank cause classifier** | 逐帧 11 分类（CPU/Binder/Futex/IO/Memory/GPU/Runnable/VideoLateRender/AudioSyncDrift/ThermalThrottle） |
| **GPU stall** | Perfetto `gpu_work_period` ftrace + 降频推断双通道 |
| **音画同步** | Perfetto AudioTrack `getTimestamp` atrace，>40ms drift 标记 |
| **启发式对比** | 旧线性加权 vs 新图算法 top-k 精度与信噪比 |
| **视频场景** | `-s video` 启用解码帧模型 + GPU stall + 温控归因 + buffer 饥饿 + 音画同步 |

## 架构

```
设备端（Pixel 6a Android 15 kernel 6.1）     宿主机（Windows / WSL）
─────────────────                              ─────────────────────
Perfetto ──→ trace ──→ trace_processor_shell ──→ frames.txt
eBPF loader ──→ events.bin (v3) ──→ tracepilot -G [-s video] ──→ result.json
  ├─ sched_switch/wakeup              (events ringbuf)
  ├─ irq/softirq entry/exit           (sys_events ringbuf)
  ├─ binder_transaction/received      (enhanced_events ringbuf, debug_id match)
  ├─ futex sys_enter/sys_exit         (enhanced_events ringbuf)
  ├─ cpu_frequency (+ 100ms sysfs polling) (enhanced_events ringbuf)
  └─ mm_vmscan_direct_reclaim_begin   (enhanced_events ringbuf)
```

- **eBPF**: 13 hooks，9 maps（3 RINGBUF + 6 HASH），hash map 均已扩容
- **图算法**: Brandes betweenness（k-sampling sqrt(V)）、BFS render/decode proximity
- **节点/边**: 17 种节点 × 12 种边（基本 11×8 + 视频扩展 6×4）
- **帧数据**: frames.txt 包含 5 种类型 — SF(UI帧) / VD(解码帧) / VF(fallback) / GS(GPU) / AP(音频)

## 快速开始

```bash
# 编译
make bpf && make android

# 采集
adb push output/tracepilot-aarch64 /data/local/tmp/tracepilot
adb push output/tracepilot.bpf.o /data/local/tmp/
adb push scripts/perfetto_config.pbtx /data/local/tmp/
adb shell "su -c 'chmod +x /data/local/tmp/tracepilot'"
adb shell "su -c '/data/local/tmp/tracepilot -d 60 -e /data/local/tmp/events.bin -D'"

# 提取帧数据（包含 SF/VD/VF/GS/AP 全部类型）
trace_processor_shell -q scripts/frame_query.sql trace > frames.txt

# 分析
./tracepilot -i events.bin -f frames.txt -o result.json -G -k 10              # 页面切换
./tracepilot -i events.bin -f frames.txt -o result.json -G -s video -k 10     # 视频场景
```

详细使用说明见 [`使用说明.md`](使用说明.md)，实施计划见 [`实施计划.md`](实施计划.md)。