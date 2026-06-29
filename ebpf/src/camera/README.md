# Camera Scheduling Analyzer

基于 eBPF + Perfetto 的 Android 相机调度延迟分析工具。帧对齐、依赖感知，从"谁慢了"到"为什么慢了"。

## 架构

```
Perfetto (帧对齐)               eBPF (13 探针, 36MB buffer)
  │                                    │
  ├── actual_frame_timeline            ├── sched_events.csv (内核内计算)
  ├── camera/hal atrace                ├── binder_futex_events.csv
  └── threads_map                      ├── irq_events.csv (IRQ + SoftIRQ)
         │                             │
         ▼                             ▼
  ebpf_target_windows.json     (3 个 CSV, 合计 ~300万行/30s)
         │                             │
         └──────────────┬──────────────┘
                        ▼
              analyze_delays.py      (延迟聚合 + Binder配对 + Futex + CPU + Thermal + IRQ + Mem)
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
  critical_path.py  root_cause.py  camera_pipeline.py
  (DAG 4边类型)     (6信号归因)    (管线阶段聚合)
          │             │             │
          ├─────────────┼─────────────┤
          ▼             ▼             ▼
  jank_classifier.py  safe_hint_engine.py  graph_export.py
  (9维度分类)         (持久化调优)         (DOT可视化)
          │                            │
          ├────────────┬───────────────┤
          ▼            ▼               ▼
  session_compare.py  generate_report.py
  (跨会话对比)        → report_*.md
```

## 快速开始

```bash
# 全自动 (编译→部署→采集→拉取→分析→报告, 9步自动完成)
python auto_run.py

# 仅重跑分析 (已有 CSV)
python auto_run.py --only-analyze

# 指定包名 + 采集时长
python auto_run.py --package com.google.android.GoogleCamera --duration 60
```

## eBPF 探针 (13 个, 12 成功加载)

| 探针 | 类型 | 用途 |
|------|------|------|
| `tp/sched/sched_switch` | tracepoint | 线程切换 + 内核内计算 Runnable Delay (wakeup+preempt) |
| `tp/sched/sched_wakeup` | tracepoint | 唤醒时间戳 (仅写 map, 不占 ringbuf) |
| `raw_tp/binder_transaction` | raw_tp | IPC 调用发起 (debug_id 配对) |
| `raw_tp/binder_transaction_received` | raw_tp | IPC 调用接收 (debug_id 配对) |
| `raw_tp/sys_enter` | raw_tp | Futex WAIT (UID 预过滤减少 90%) |
| `raw_tp/sys_exit` | raw_tp | Futex WAKE (含 duration_ns) |
| `raw_tp/cpu_frequency` | raw_tp | CPU 频率变化 (kHz→MHz) |
| `raw_tp/thermal_temperature` | raw_tp | 温度检测 (⚠ 已跳过, Pixel 6a 不兼容) |
| `tp_btf/irq_handler_entry` | BTF tp | IRQ 开始 |
| `tp_btf/irq_handler_exit` | BTF tp | IRQ 退出 (含 duration) |
| `tp/irq/softirq_entry` | tracepoint | SoftIRQ 开始 |
| `tp/irq/softirq_exit` | tracepoint | SoftIRQ 退出 (含 duration) |
| `raw_tp/mm_vmscan_direct_reclaim_begin` | raw_tp | 直接内存回收 (order) |

**Ring Buffer:**
- 主通道 (sched/binder/futex/cpu/thermal/mem): **32MB**
- 系统通道 (IRQ/SoftIRQ): **4MB**
- 总计: **36MB**

**内核内计算:** wakeup + preempt 延迟在 BPF 内完成, wakeup 事件不写 ringbuf → 事件量减半

## 输出文件

```
output/
├── raw/
│   ├── sched_events.csv           # ~200万行/30s, 含预计算 runnable_delay_ns
│   ├── binder_futex_events.csv    # ~45万行/30s
│   └── irq_events.csv             # ~210万行/30s (IRQ + SoftIRQ)
├── analysis/
│   ├── ebpf_target_windows.json   # Perfetto 帧窗口 + Camera Pipeline 阶段
│   ├── delay_analysis_result.json # 每帧多维度聚合
│   ├── critical_path_graph.json   # 图结构 + 评分
│   ├── camera_pipeline_result.json# 相机管线阶段分析
│   ├── root_cause_analysis.json   # 6信号根因归因
│   ├── jank_classification.json   # 9维卡顿分类
│   ├── tuning_profile.json        # 调度调优配置
│   ├── apply_tuning.sh            # 可部署 shell 脚本
│   ├── compare_report.json        # 跨会话对比
│   ├── graph_topology.dot         # 全局拓扑 (Graphviz)
│   └── graph_frame_*.dot          # 每帧子图
└── reports/
    └── report_*.md                # 最终报告 (10章, ~750行)
```

## 分析管线 (9 步自动)

```
auto_run.py:
  0. 环境检查
  1. 编译 eBPF (WSL)
  2. 部署到设备 (adb push)
  3. 启动采集 (Perfetto + eBPF 并行)
  4. 停止采集 (SIGTERM → flush → 拉取)
  5. 拉取数据 (3 CSV + 1 trace)
  6a. Perfetto → 帧窗口 JSON
  6b. 多维分析 → Critical Path Graph
  6c. 相机管线阶段分析
  6d. 根因归因 + 调优配置 + 卡顿分类
  6e. 图可视化导出 (DOT)
  6f. 多会话对比
  7. 生成 Markdown 报告 + 摘要
```

## 报告内容 (10 章)

| 章节 | 内容 |
|------|------|
| 一、Top-K | 8维 CriticalScore + 温度惩罚, 全局排名 |
| 二、Binder | IPC 依赖边 + 延迟 |
| 三、Futex | 锁等待/唤醒统计 |
| 四、逐帧分析 | 每帧 sched/binder/futex/IRQ/CPU |
| 五、关键路径 | TOP-3 DAG 阻塞链 (含 SYSTEM_OVERHEAD/RESOURCE_STALL) |
| 六、根因归因 | 调度竞争 vs Binder vs Futex vs IRQ vs SoftIRQ |
| 七、管线分析 | 相机 Pipeline 阶段聚合 |
| 八、卡顿分类 | 9 维信号加权分类 (SCHED/BINDER/FUTEX/CPU/THERMAL/GPU/RENDER/IRQ/MEM) |
| 九、多会话对比 | 跨运行 Top-1 重叠 + 根因分布趋势 |
| 十、总结 | 首要关注线程 + 相机专项建议 |

## 评分公式 (8+1 维 CriticalScore)

```
CriticalScore(tid) =
    + 0.8 × frame_window_overlap_ratio
    + 2.0 × runnable_delay_p95_norm
    + 1.5 × binder_centrality_norm
    + 0.8 × futex_wait_norm
    + 1.2 × render_path_proximity
    + 0.4 × repeated_jank_cooccurrence
    + 1.8 × on_critical_path_ratio
    - 0.5 × background_penalty
    - 0.6 × thermal_factor  (T > 40°C 时激活)
```

## 图结构 (4 种边)

```
Frame ← Thread    RUNNABLE_WAIT   (线程调度延迟)
Frame ← HardIRQ   SYSTEM_OVERHEAD (硬件中断抢占)
Frame ← SoftIRQ   SYSTEM_OVERHEAD (软中断抢占)
Frame ← Resource  RESOURCE_STALL  (CPU降频/内存回收)
```

## 线程角色 (16 种)

| 角色 | 示例 |
|------|------|
| UI Thread | `id.GoogleCamera`, `com.*` |
| RenderThread | `RenderThread`, `RenderEngine` |
| CameraThread | `GcamTasks:0-4`, `GcaGeneric-1~4`, `smz-*`, `sabre`, `cvk-*`, `YUV_*`, `RAW*` |
| CameraHal | `lwis_I2C_Bus_*`, `android.hardwar*`, `CXCP-*`, `Cam0_*` |
| CameraService | `cameraserver` |
| SurfaceFlinger | `surfaceflinger` |
| GPU Worker | `mali-*`, `glide-*` |
| Binder RPC / HwBinder RPC | `binder:*` |
| SystemService | `system_server`, `systemui` |
| KernelWorker | `kworker/*`, `swapper/*`, `irq/*` |

## 典型分析结果 (30s 拍照场景)

| 指标 | 数值 |
|------|------|
| sched 事件 | ~2,050,000 |
| binder/futex 事件 | ~450,000 |
| IRQ/SoftIRQ 事件 | ~2,130,000 |
| Jank 帧 | 16 |
| 评分的线程 | 631 |
| **根因** | **100% CPU Scheduling Contention** |
| 次要因素 | IRQ 5-28ms/帧, Futex 大量竞争 |
| CPU 频率 | 1800-2750MHz (稳定, 无降频) |
| 主要竞争者 | `s.nexuslauncher` (桌面), `logd.writer` (日志), GCam 线程池 |


## 手动命令

```bash
# 编译 eBPF (仅改 C 代码后需要)
cd ebpf && make

# 手动 Perfetto
adb push perfetto/perfetto_camera.pbtx /data/local/tmp/
adb shell "cat /data/local/tmp/perfetto_camera.pbtx | perfetto --txt -c - -o /data/misc/perfetto-traces/camera_jank.perfetto -d 2>&1"

# 手动 eBPF (Perfetto 先启动!)
adb push ebpf/build/camera_ebpf_android /data/local/tmp/
adb shell su -c "/data/local/tmp/camera_ebpf_android -q -u 10162"

# 拉取数据
adb pull /data/misc/perfetto-traces/camera_jank.perfetto ./perfetto/
adb pull /data/local/tmp/sched_events.csv ./output/raw/
adb pull /data/local/tmp/binder_futex_events.csv ./output/raw/
adb pull /data/local/tmp/irq_events.csv ./output/raw/

# 分步分析
cd perfetto && python3 parse_trace.py camera_jank.perfetto com.google.android.GoogleCamera
cd ../ebpf
python3 analyze_delays.py --json ../output/analysis/ebpf_target_windows.json --csv ../output/raw/sched_events.csv --binder ../output/raw/binder_futex_events.csv --irq ../output/raw/irq_events.csv
python3 camera_pipeline.py --json ../output/analysis/ebpf_target_windows.json --csv ../output/raw/sched_events.csv --binder ../output/raw/binder_futex_events.csv --irq ../output/raw/irq_events.csv
python3 root_cause.py
python3 safe_hint_engine.py
python3 jank_classifier.py
python3 graph_export.py
python3 session_compare.py
python3 generate_report.py
```

# 手动 eBPF (Perfetto 先启动!)
adb push ebpf/build/camera_ebpf_android /data/local/tmp/
adb shell su -c "/data/local/tmp/camera_ebpf_android -q -u 10162"

# 拉取数据
adb pull /data/misc/perfetto-traces/camera_jank.perfetto ./perfetto/
adb pull /data/local/tmp/sched_events.csv ./output/raw/
adb pull /data/local/tmp/binder_futex_events.csv ./output/raw/

# PC 端分析
cd perfetto && python3 parse_trace.py camera_jank.perfetto com.google.android.GoogleCamera
cd ../ebpf
python3 analyze_delays.py --json ../output/analysis/ebpf_target_windows.json --csv ../output/raw/sched_events.csv --binder ../output/raw/binder_futex_events.csv
python3 root_cause.py
python3 generate_report.py
```

## 调优部署

```bash
# 生成调优配置
cd ebpf && python3 safe_hint_engine.py

# 临时应用 (进程重启失效)
adb push output/analysis/apply_tuning.sh /sdcard/ && adb shell sh /sdcard/apply_tuning.sh

# 持久化 (Magisk)
adb shell su -c 'cp /sdcard/apply_tuning.sh /data/adb/service.d/'
```

## 设备要求

- Pixel 6a (kernel 5.10, arm64)
- Root (su)
- Perfetto 已安装
- WSL Ubuntu (编译 arm64) + Windows adb
