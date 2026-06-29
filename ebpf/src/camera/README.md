# Camera Scheduling Analyzer

基于 eBPF + Perfetto 的 Android 相机调度延迟分析工具。帧对齐、依赖感知，从"谁慢了"到"为什么慢了"。

## 模块定位

本模块回答 **三个核心问题**：

| 问题 | 答案来源 | 输出 |
|------|----------|------|
| **谁慢了？** | CriticalScore 排名（5 维加权） | 报告第一章 Top-K 表 |
| **怎么阻塞的？** | DAG 关键路径图（4 种边） | 报告第五章 + DOT 图 |
| **为什么卡？** | 6 信号根因归因 | 报告第六章 + `root_cause_analysis.json` |

最终产物是一份 **9 章 Markdown 报告**（~750 行），以及配套的 JSON 中间数据、DOT 图、调优配置。

**一次完整分析的规模：** 30 秒 Google Camera 拍照 → 约 460 万行 eBPF 事件 → 631 个线程评分 → 16 帧卡顿分析。

---

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                     采集层（Pixel 6a 端）                      │
├──────────────────────┬──────────────────────────────────────┤
│ Perfetto (128MB)     │ eBPF (13 探针, 36MB ringbuf)          │
│ • FrameTimeline      │ • sched_switch/wakeup → ~200 万行     │
│ • gfx/view/am atrace │ • binder_tx/rx        → ~16 万行      │
│ • camera/hal atrace  │ • futex_wait/wake     → ~27 万行      │
│ • irq/softirq ftrace │ • cpu_freq/thermal/mem→ ~1 万行       │
│                      │ • irq/softirq         → ~210 万行     │
└──────────┬───────────┴──────────┬───────────────────────────┘
           │ adb pull             │ adb pull
           ▼                      ▼
  .perfetto-trace        3 个 CSV (合计 ~460 万行/30s)
           │                      │
           ▼                      ▼
┌─────────────────────────────────────────────────────────────┐
│                    分析层（PC 端, 10 个脚本）                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  parse_trace.py ──→ ebpf_target_windows.json (帧窗口)        │
│                          │                                  │
│  analyze_delays.py ←────┘ + 3 CSV                           │
│       │                                                     │
│       ├──→ delay_analysis_result.json (每帧聚合)             │
│       │        │                                            │
│       │        ├─── root_cause.py ──→ root_cause_analysis   │
│       │        │                      (6 信号帧内占比归因)    │
│       │        │                                            │
│       │        └─── jank_classifier.py ──→ 9 维卡顿分类      │
│       │                                                     │
│       └──→ critical_path_graph.json (DAG + CriticalScore)   │
│                │                                            │
│                ├─── generate_report.py ──→ report_*.md       │
│                │     (读评分 + 读归因, 合成为最终报告)         │
│                │                                            │
│                ├─── safe_hint_engine.py ──→ tuning_profile   │
│                │     (评分 → 置信度 → 调优命令)               │
│                │                                            │
│                ├─── graph_export.py ──→ DOT/SVG 图          │
│                │                                            │
│                └─── session_compare.py ──→ 跨会话对比        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**两条独立的分析路径：**

| 路径 | 入口 | 方法 | 回答 |
|------|------|------|------|
| CriticalScore 路径 | `critical_path.py` | 5 维全局加权排名 | 哪个线程全局最可疑？ |
| 根因归因路径 | `root_cause.py` | 帧内时间占比直接判定 | 每帧卡顿的主因是什么？ |

两条路径**不互相依赖**，在报告中合并呈现。

---

## 快速开始

```bash
# 全自动 (编译→部署→采集→拉取→分析→报告, 9步自动完成)
python auto_run.py

# 仅重跑分析 (已有 CSV, 跳过采集)
python auto_run.py --only-analyze

# 指定包名 + 采集时长
python auto_run.py --package com.google.android.GoogleCamera --duration 60
```

---

## 数据来源与流向

### eBPF 探针 → 输出 CSV

| 探针 | 写入 | 典型数据量 (30s) | 下游消费 |
|------|------|-----------------|----------|
| `sched_switch` + `sched_wakeup` | `sched_events.csv` | ~200 万行 | CriticalScore、DAG、根因归因、分类 |
| `binder_transaction` + `binder_received` | `binder_futex_events.csv` | ~16 万行 | binder_centrality、Binder IPC 依赖分析 |
| `futex_wait` + `futex_wake` (sys_enter/exit) | `binder_futex_events.csv` | ~27 万行 | futex_wait_norm、锁竞争分析 |
| `cpu_frequency` | `binder_futex_events.csv` | ~1 万行 | CPU 频率归因、降频检测 |
| `mem_reclaim` | `binder_futex_events.csv` | ~55 次 | 内存压力检测 |
| `irq_handler_entry/exit` + `softirq_entry/exit` | `irq_events.csv` | ~210 万行 | IRQ/SoftIRQ 抢占分析 |

> **注：** `binder_futex_events.csv` 实际包含 5 种事件类型（binder_tx, binder_rx, futex_wait, futex_wake, cpu_freq, mem_reclaim），由 C loader 统一写入。

### CSV 字段说明

**sched_events.csv:**
```
ts, event, tid, prev_tid, tgid, uid, extra, ret, comm
```
- `ret`: 内核内预计算的 runnable_delay_ns（wakeup→switch 的总等待时间）
- `extra`: CPU 核心号

**binder_futex_events.csv:**
```
ts, event, tid, prev_tid, tgid, uid, debug_id, extra, ret, comm
```
- `event` 取值: `binder_transaction`, `binder_received`, `futex_wait`, `futex_wake`, `cpu_frequency`, `mem_reclaim`
- `debug_id`: binder 事务配对 ID
- `extra`: binder → to_proc|code; futex → futex_addr; cpu_freq → MHz; mem → order

**irq_events.csv:**
```
ts, event, tid, prev_tid, tgid, uid, debug_id, extra, ret, comm
```
- `event` 取值: `irq_handler_entry`, `irq_handler_exit`, `softirq_entry`, `softirq_exit`
- `extra`: IRQ 号或 SoftIRQ 类型
- `ret`: duration_ns（entry→exit 配对计算）

---

## 分析脚本详解

| 脚本 | 输入 | 输出 | 做什么 |
|------|------|------|--------|
| `perfetto/parse_trace.py` | `.perfetto-trace` | `ebpf_target_windows.json` | Perfetto SQL 查询提取帧边界 + jank 标记 |
| `scripts/analyze_delays.py` | 3 CSV + 帧窗口 JSON | `delay_analysis_result.json` | 帧窗口内聚合 sched/binder/futex/IRQ/CPU，触发 critical_path.py |
| `scripts/critical_path.py` | `delay_analysis_result.json` | `critical_path_graph.json` | DAG 构建 + 5 维 CriticalScore 排名 |
| `scripts/root_cause.py` | `delay_analysis_result.json` + `critical_path_graph.json` | `root_cause_analysis.json` | 6 信号帧内时间占比 → 归因判定 |
| `scripts/jank_classifier.py` | `delay_analysis_result.json` + `critical_path_graph.json` | `jank_classification.json` | 9 维信号加权 → 卡顿类型标签 |
| `scripts/safe_hint_engine.py` | `critical_path_graph.json` | `tuning_profile.json` + `apply_tuning.sh` | 评分 → 置信度过滤 → uclamp/affinity 命令 |
| `scripts/graph_export.py` | `critical_path_graph.json` | `graph_topology.dot` + `graph_frame_*.dot` | DAG 转 Graphviz DOT 格式 |
| `scripts/session_compare.py` | 多次 `critical_path_graph.json` | `compare_report.json` | Top-1 重叠矩阵 + 根因趋势 |
| `scripts/generate_report.py` | 以上全部 JSON | `report_*.md` | 合成 9 章 Markdown 报告 |

---

## 评分公式

```
CriticalScore(tid) =
    + 0.30 × frame_window_overlap        # 线程出现在多少帧的窗口中
    + 0.10 × log1p(runnable_delay_p95_ms) # P95 就绪等待延迟（对数归一化）
    + 0.25 × binder_centrality_norm       # Binder 出入度中心性（归一化到 [0,1]）
    + 0.10 × futex_wait_norm              # Futex 等待次数（归一化到 [0,1]）
    - 0.05 × background_penalty           # 后台进程惩罚
    + 0.20 × render_path_proximity        # 与渲染路径的语义距离
```

**权重设计原则：**
- `overlap`(0.30) 最高 — 线程出现在越多帧中，与卡顿的关联越强
- `binder`(0.25) 次高 — Android 中 Binder 是主要 IPC 瓶颈来源
- `log1p(p95)`(0.10) — 用对数抑制极端值，避免单次大延迟主导排名
- `futex`(0.10) — 锁竞争通常是并发问题而非调度问题
- `render_proximity`(0.20) — 越靠近渲染管线，对用户体验影响越大
- `bg_penalty`(-0.05) — 后台线程的延迟通常不直接影响前台体验

**render_path_proximity 分级：**

| 角色 | rpp 值 | 原因 |
|------|--------|------|
| UI Thread, RenderThread | 1.0 | 直接决定帧渲染 |
| SurfaceFlinger, HwComposer | 0.8 | 帧合成与显示 |
| Binder RPC, HwBinder RPC | 0.6 | IPC 传输路径 |
| GPU Worker | 0.5 | 间接影响渲染 |
| SystemService | 0.4 | 系统服务 |
| 其他 | 0.1 | 与渲染无关 |

**CriticalScore 的下游消费：**

```
critical_path_graph.json
  ├── generate_report.py  → 报告第一章 Top-K 排名表
  ├── safe_hint_engine.py → 置信度过滤 → 调优命令
  ├── graph_export.py     → DOT 节点标注分数
  └── session_compare.py  → 跨会话 Top-1 重叠
```

---

## 两条分析路径（重要）

### 路径一：CriticalScore（全局排名）

**方法：** 对 631 个线程的 5 个维度加权求和，全局排名。

**适用场景：** 想知道"在所有帧中，哪些线程最值得关注"。

**局限：** 它是启发式排名，不直接回答"帧 X 为什么卡"——那是路径二的事。

### 路径二：根因归因（逐帧判定）

**方法：** 每帧内部，直接计算各信号的时间占比，超过 20% 即标记为候选，取最高分。

```
每帧内:
  runnable_ns / total_ns > 20% → "CPU Scheduling Contention"
  binder_ns   / total_ns > 20% → "Binder IPC Blocking"
  futex_est_ns/ total_ns > 20% → "Futex Lock Contention"
  irq_hard_ns / total_ns > 20% → "Hard IRQ Overhead"
  irq_soft_ns / total_ns > 20% → "SoftIRQ Overhead"
  
环境信号（辅助）:
  thermal > 45°C → "Thermal Throttling"
  cpu_mhz < 1200 → "CPU Freq Throttling"
  mem_reclaim ≥ 3 → "Memory Pressure"
```

**与 CriticalScore 的关系：** 根因归因**不依赖** CriticalScore。它读取 `delay_analysis_result.json` 的帧级时间分解，直接做占比判定。两条路径独立运行，在报告中合并呈现。

---

## 图结构

DAG 包含 3 种节点（Frame / Thread / Resource）和 4 种边：

| 边类型 | 含义 | 来源 |
|--------|------|------|
| `RUNNABLE_WAIT` | 线程就绪但未分配到 CPU | sched_switch 的 runnable_delay |
| `BINDER_CALL` | 跨进程 Binder 调用 | binder_transaction → binder_received 配对 |
| `FUTEX_WAIT` | 锁等待阻塞 | futex_wait → futex_wake 配对 |
| `SYSTEM_OVERHEAD` | IRQ/SoftIRQ 抢占 | irq_handler_entry/exit + softirq_entry/exit |
| `RESOURCE_STALL` | CPU 降频或内存回收 | cpu_frequency + mem_reclaim |

---

## 线程角色

基于线程名正则匹配，自动归类为 16 种角色。角色影响 `render_path_proximity` 评分和调优安全过滤。

| 角色 | 匹配规则 | render_proximity | 可调优？ |
|------|----------|-----------------|----------|
| UI Thread | `id.GoogleCamera`, 主线程 `com.*` | 1.0 | ✅ |
| RenderThread | `RenderThread`, `RenderEngine` | 1.0 | ✅ |
| CameraThread | `GcamTasks:*`, `GcaGeneric*`, `smz-*`, `sabre`, `cvk-*`, `YUV_*`, `RAW*` | 0.1 | ✅ |
| CameraHal | `lwis_I2C*`, `android.hardwar*`, `CXCP-*`, `Cam0_*` | 0.1 | ⚠️ 谨慎 |
| CameraService | `cameraserver` | 0.4 | ⚠️ 谨慎 |
| SurfaceFlinger | `surfaceflinger` | 0.8 | ❌ 黑名单 |
| HwComposer | `hwcomposer` | 0.8 | ❌ 黑名单 |
| GPU Worker | `mali-*`, `glide-*` | 0.5 | ⚠️ 谨慎 |
| Binder RPC | `binder:*` (不含 `android.hardwar`) | 0.6 | ⚠️ 谨慎 |
| HwBinder RPC | `hwbinder:*` 或 comm 含 `android.hardwar` | 0.6 | ❌ 黑名单 |
| SystemService | `system_server`, `systemui` 等 | 0.4 | ❌ 黑名单 |
| KernelWorker | `kworker/*`, `swapper/*`, `irq/*` | — | ❌ 排除 |
| I/O Worker | `kswapd0`, `mmcqd/*`, `dm-*` | 0.1 | ❌ 排除 |
| UnknownWorker | 未匹配到以上规则 | 0.1 | 视情况 |

> **调优安全原则：** `safe_hint_engine.py` 通过黑名单（system_server/surfaceflinger/kworker 等）和角色白名单双重过滤，确保只向 RenderThread/CameraThread 等安全目标发出调优建议。

---

## 报告内容（9 章）与诊断价值

| 章节 | 回答的问题 | 诊断价值 | 数据来源 |
|------|-----------|----------|----------|
| 一、Top-K | 全局最可疑的线程是谁？ | ⭐⭐⭐ 入口排名 | CriticalScore |
| 二、Binder | IPC 调用链有瓶颈吗？ | ⭐⭐ 排除 IPC 问题 | binder 配对 |
| 三、Futex | 锁竞争严重吗？ | ⭐⭐ 排除锁问题 | futex 配对 |
| 四、逐帧 | 每帧内部发生了什么？ | ⭐⭐ 原始证据 | 帧聚合 |
| 五、关键路径 | 阻塞链是什么结构？ | ⭐⭐⭐⭐ **核心** — 唯一回答"怎么被拖慢" | DAG |
| 六、根因归因 | 卡顿的根本原因是什么？ | ⭐⭐⭐⭐ **核心** — 唯一回答"为什么卡" | 6 信号 |
| 七、卡顿分类 | 卡顿可以归类吗？ | ⭐⭐ 交叉验证 | 9 维信号 |
| 八、多会话对比 | 结论可复现吗？ | ⭐⭐ 可信度 | 多次运行 |
| 九、总结 | 最终该怎么办？ | ⭐⭐⭐ 可执行建议 | 汇总 |

**核心章节是五（关键路径）和六（根因归因）**。第一章 Top-K 提供入口排名，第九章提供落地建议。其余章节是支撑证据。

---

## 典型分析结果（30s Google Camera 拍照）

| 指标 | 数值 | 解读 |
|------|------|------|
| sched 事件 | ~2,050,000 | 调度活动密集 |
| binder 事件 | ~165,000 (tx+rx) | Binder 活动活跃 |
| futex 事件 | ~276,000 (wait+wake) | 存在大量锁竞争 |
| IRQ/SoftIRQ 事件 | ~2,130,000 | 中断频繁但每帧仅 5-28ms |
| Jank 帧 | 16 | 30s 内 16 帧掉帧 |
| 参与评分的线程 | 631 | 系统线程总量 |
| **根因** | **100% CPU Scheduling Contention** | 不是 Binder、不是温控、不是降频 |
| Binder 归因占比 | 0% | Binder 延迟在所有帧的 total_ns 中均 < 20% |
| CPU 频率 | 1800-2750MHz（稳定） | 无降频，排除 thermal throttling |
| 温度 | 未触发 >45°C | 排除 thermal |
| 内存回收 | 55 次（分布在 30s 内） | 影响可忽略 |
| 调优建议 | **0 条** | 631 个线程无一过置信度阈值 |

**结论：** 卡顿根因是 GCam 线程池 + 桌面进程（`s.nexuslauncher`）+ 日志进程（`logd.writer`）之间的 **全局 CPU 调度竞争**，而非单点瓶颈。无线程需要调优——问题在于 Android CFS 调度器在密集多线程场景下的公平性策略。

---

## 当前局限

| 局限 | 影响 | 缓解方案 |
|------|------|----------|
| Pixel 6a 不支持 thermal_temperature 探针 | 无法获取实时温度 | 环境信号由 Perfetto 补充 |
| Google Camera 用私有 GSLCamera 栈 | camera atrace 无法提取 Pipeline 阶段 | 已从管线中移除 |
| Binder 边可能落在帧窗口外 | 部分 IPC 依赖未被捕获 | 扩大帧窗口 margin |
| CriticalScore 为启发式权重 | 排名可能不完美 | 多条分析路径交叉验证 |
| `tuning_profile.json` 可能为空 | 无调优建议输出 | 结论本身即为有效诊断 |

---

## eBPF 探针详情

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

**Ring Buffer:** 主通道 32MB + 系统通道 4MB = **36MB**

**内核内计算:** wakeup + preempt 延迟在 BPF 内完成, wakeup 事件不写 ringbuf → 事件量减半

---

## 输出文件

```
output/
├── raw/
│   ├── sched_events.csv           # ~200万行/30s, 含预计算 runnable_delay_ns
│   ├── binder_futex_events.csv    # ~45万行/30s (binder+futex+cpu_freq+mem)
│   └── irq_events.csv             # ~210万行/30s (IRQ + SoftIRQ)
├── analysis/
│   ├── ebpf_target_windows.json   # Perfetto 帧窗口
│   ├── delay_analysis_result.json # 每帧多维度聚合 (2.2MB)
│   ├── critical_path_graph.json   # DAG 图结构 + CriticalScore 排名 (1.8MB)
│   ├── root_cause_analysis.json   # 6信号帧级根因归因 (29KB)
│   ├── jank_classification.json   # 9维卡顿分类 (3.8KB)
│   ├── tuning_profile.json        # 调度调优配置 (可能为空)
│   ├── apply_tuning.sh            # 可部署 shell 脚本
│   ├── compare_report.json        # 跨会话对比
│   ├── graph_topology.dot         # 全局拓扑 (Graphviz)
│   └── graph_frame_*.dot          # 每帧子图
└── reports/
    └── report_*.md                # 最终报告 (9章)
```

---

## 分析管线 (auto_run.py, 9 步自动)

```
 0. 环境检查          → WSL/Windows 自适应
 1. 编译 eBPF         → clang ARM64 交叉编译
 2. 部署到设备        → adb push (bpf.o + loader)
 3. 启动采集          → Perfetto + eBPF 并行
 4. 停止采集          → SIGTERM → flush ringbuf → adb pull
 5. 拉取数据          → 3 CSV + 1 perfetto-trace
 6a. Perfetto 分析    → trace_processor_shell SQL → 帧窗口 JSON
 6b. 多维分析         → analyze_delays.py → delay_analysis + CriticalScore
 6c. 诊断三步         → root_cause + tuning + jank_classifier
 6d. 图导出           → graph_export.py → DOT
 6e. 多会话对比       → session_compare.py
 7. 生成报告          → generate_report.py → report_*.md
```

---

## 调优部署

```bash
# 生成调优配置
cd ebpf && python3 safe_hint_engine.py

# 临时应用 (进程重启失效)
adb push output/analysis/apply_tuning.sh /sdcard/ && adb shell sh /sdcard/apply_tuning.sh

# 持久化 (Magisk)
adb shell su -c 'cp /sdcard/apply_tuning.sh /data/adb/service.d/'
```

> 注意：`safe_hint_engine.py` 有严格的置信度阈值（默认 0.6）和黑白名单过滤。当无线程通过过滤时，`tuning_profile.json` 为空——**这本身是一个有效的诊断结论**，表明系统不存在可安全调优的单点瓶颈。

---

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
cd ../scripts
python3 analyze_delays.py --json ../output/analysis/ebpf_target_windows.json --csv ../output/raw/sched_events.csv --binder ../output/raw/binder_futex_events.csv --irq ../output/raw/irq_events.csv
python3 root_cause.py
python3 safe_hint_engine.py
python3 jank_classifier.py
python3 graph_export.py
python3 session_compare.py
python3 generate_report.py
```

---

## 设备要求

- **手机：** Pixel 6a (Android 14, kernel 5.10, arm64), 已 root (Magisk)
- **PC：** WSL Ubuntu 或原生 Linux, 已安装 Android NDK + clang
- **Perfetto：** 设备端已安装 `perfetto`、`traced`、`traced_probes`
- **adb：** PC 端可正常连接设备
