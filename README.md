# TracePilot — Frame-Centric 的 Android 调度辅助系统

**Frame-aligned, dependency-aware scheduling assistant for Android interaction workloads**

## 团队信息

- **队名**：`TracePilot`
- **成员**：
  - 潘智勇
  - 李松茂
  - 邵晨轩
  - 贺小轩
  - 杨子皓

## 项目定位

TracePilot 是一个面向 Android 交互负载的、**以帧为对齐单位、具备依赖感知能力**的调度辅助系统。

核心路径：在 Pixel 6a 设备上，通过 **eBPF** 采集调度与进程间依赖事件，利用 **Perfetto FrameTimeline** 作为 ground truth，在帧窗口内构建依赖关键路径图，识别影响用户体验的关键线程，最终通过安全的用户态 hint 进行受控干预。

---

## 核心设计理念：从 PID-Centric 到 Frame-Centric

### 为什么 PID-Centric 不可行

1. **PID 不稳定**：同一 App 每次启动 PID 不同，一个 App 可能有多个进程，PID 还会被系统复用
2. **卡顿不是单个 PID 的问题**：Android jank 的根因通常是 UI thread、RenderThread、Binder、system_server、SurfaceFlinger 之间的等待链
3. **eBPF 只能观测内核事件**，无法直接回答"用户在经历什么"

### 正确路径：Frame-Centric + Dependency-Centric

```
FrameTimeline 定义问题 → eBPF 提供原因 → Graph 找关键路径 → Hint Engine 做受控干预
```

---

## 核心技术简介

### eBPF（Extended Berkeley Packet Filter）

eBPF 是 Linux 内核的一项沙箱技术，允许用户在不修改内核源码或加载内核模块的情况下，安全高效地注入自定义程序来观测和控制内核行为。

**在本项目中的角色：** TracePilot 在 Pixel 6a（Android 16，已 root）上加载 eBPF 探针，通过 kprobe 和 tracepoint 挂载点采集内核调度与进程间通信事件：

| 探针类型 | 采集事件 | 用途 |
|----------|----------|------|
| `sched_switch` | 线程切换（prev/next TID、运行时长、runnable delay） | 计算线程就绪等待延迟 |
| `sched_wakeup` | 线程唤醒延迟 | 计算 wakeup-to-run latency |
| `binder_transaction` kprobe | Binder 跨进程调用 | 分析进程间依赖与通信瓶颈 |
| `futex` wait/wake | 锁等待 | 识别锁竞争导致的阻塞 |
| `cpu_frequency` | CPU 频率与核调度 | 分析大小核负载均衡 |

探针源码位于 `ebpf/src/page_turning/page_turning.bpf.c`，使用 C 编写，通过 clang 交叉编译为 BPF 字节码（`.bpf.o`），再由用户态加载器（`page_turning.c`）通过 `bpf()` 系统调用加载到内核。采集的原始事件经 ringbuf 输出到文件（`events.bin`），供离线分析。

---

### Perfetto

Perfetto 是 Google 开发的 Android/Linux 平台高性能用户态与内核态追踪工具栈，是 Systrace 的继任者。

**在本项目中的角色：** Perfetto 作为 **ground truth（基准事实）** 层，用于标定帧边界和识别 jank（掉帧）。

**采集配置：**
- 以后台守护进程模式运行：`perfetto --background`
- 128MB ring buffer
- 启用 atrace categories：`gfx`、`view`、`am`、`wm`、`res`、`dalvik`
- 启用 ftrace events：`sched_switch`、`sched_wakeup`、`irq`、`softirq`
- 采集结束后通过 `adb pull` 拉取 `.perfetto-trace` 文件

**帧提取：**
使用 Perfetto 提供的 `trace_processor_shell`（预编译的 Linux x86_64 二进制，位于 `ebpf/交叉编译环境/perfetto编译工具linux-amd64/`）执行 SQL 查询从 trace 中提取帧信息：
- `beginFrame N vsyncIn Xms` → 帧号 N，期望 VSYNC 时间
- `presentFrameAndReleaseLayers` → 实际显示时间
- `incrementJankyFrames` → 标记 jank 帧

查询结果输出为 `frames.txt`，包含每帧的帧号、期望 VSYNC 时间、实际显示时间和 jank 标志。

---

### 交叉编译技术

由于 eBPF 探针在 **Pixel 6a（ARM64 架构，Android 16）** 上运行，而开发环境为 **Windows + WSL（x86_64）**，无法直接在目标机上编译，因此采用交叉编译。

**工具链：**
- **编译器**：Android NDK r26b + clang（target `aarch64-linux-android`）
- **构建目标**：
  - `make bpf` → `tracepilot.bpf.o`（eBPF 字节码）
  - `make android` → `tracepilot-aarch64`（ARM64 用户态加载器）
- **部署**：`adb push tracepilot-aarch64 /data/local/tmp/` + `adb push tracepilot.bpf.o /data/local/tmp/`

**Perfetto 工具链交叉编译：**
Perfetto 官方提供预编译的 Linux 工具链二进制（位于 `ebpf/交叉编译环境/perfetto编译工具linux-amd64/`），包括：
- `trace_processor_shell` — SQL 查询引擎
- `traced` / `traced_probes` — 追踪守护进程
- `tracebox` — 一站式追踪工具
- `traceconv` — 格式转换工具

此外，`ebpf/交叉编译环境/pixel6a-bpf.zip` 中包含预编译的 Pixel 6a BPF 相关工具。

**工作流：**
```
开发机 (x86_64, WSL) 
  → NDK clang 交叉编译 
  → 生成 ARM64 二进制 
  → adb push 到 Pixel 6a (/data/local/tmp/) 
  → 加载 eBPF 探针并采集事件
```

---

### 帧对齐技术（Frame Alignment）

帧对齐是 TracePilot 的核心技术，用于将 **eBPF 采集的内核调度事件** 与 **Perfetto 采集的用户态帧信息** 在时间上精确匹配，从而回答"哪些线程导致了哪一帧卡顿"。

**对齐流程（三步）：**

**Step 1 — 时间域对齐**
eBPF 事件和 Perfetto 帧数据的时间戳均使用设备上的 `CLOCK_MONOTONIC`，因此两个数据源天然在同一时间域内，无需跨域转换。

**Step 2 — 帧窗口匹配**
对 Perfetto 提取的每一帧，定义时间窗口：
```
Frame Window = [expected_VSYNC, actual_presentation_time]
```
将所有落在此窗口内的 eBPF 调度事件（sched_switch、sched_wakeup）归因到该帧。帧窗口的跨度反映了该帧从期望显示到实际显示的延迟。

**Step 3 — 线程聚合与评分**
对每帧窗口内的线程，聚合以下指标并打分：

```
base_score = 0.35 × J + 0.35 × log1p(rd_ms) + 0.15 × log1p(wl_ms) + 0.15 × UI
```

| 指标 | 含义 |
|------|------|
| `J` = jank_frame_count / num_jank | 该线程参与的 jank 帧占比 |
| `rd_ms` = runnable_delay_p95 | 就绪等待延迟（p95，毫秒） |
| `wl_ms` = wakeup_latency_p95 | 唤醒延迟（p95，毫秒） |
| `UI` | 是否为 RenderThread 或 UI thread |

然后扣除系统开销折扣：
```
最终得分 = base_score × (1.0 - min(sys_overhead_ratio, 0.9))
```

**噪声过滤：**
自动排除 eBPF 采集器自身（`tracepilot`）、ADB 进程（`adbd`、`shell svc`）等无关线程。

**核心优势：** 传统的 PID-Centric 分析无法将内核事件与用户体验关联。帧对齐技术使得 TracePilot 能够回答"哪个线程、在多长时间窗口内、对哪一帧的卡顿负责"，实现从内核观测到用户体验的语义映射。

---

```
+--------------------------------------------------+
| Ground Truth Layer                                |
| Perfetto FrameTimeline / jank / frame token       |
+--------------------------+-----------------------+
                           |
                           v
+--------------------------------------------------+
| Semantic Identity Layer                           |
| package / UID / session / process instance / role |
+--------------------------+-----------------------+
                           |
                           v
+--------------------------------------------------+
| eBPF Evidence Layer                               |
| sched / wakeup / binder / futex / freq / reclaim  |
+--------------------------+-----------------------+
                           |
                           v
+--------------------------------------------------+
| Critical Path Graph Builder                       |
| frame-window dependency graph                     |
| UI / Render / Binder / SurfaceFlinger / Resource  |
+--------------------------+-----------------------+
                           |
                           v
+--------------------------------------------------+
| ML / Heuristic Ranking Layer                      |
| critical thread score / jank cause classification |
+--------------------------+-----------------------+
                           |
                           v
+--------------------------------------------------+
| Safe Hint Engine                                  |
| whitelist / TTL / rollback / budget / audit       |
+--------------------------+-----------------------+
                           |
                           v
+--------------------------------------------------+
| User-space Actuator                               |
| uclamp / affinity / cgroup / priority hint        |
+--------------------------------------------------+
```

### 身份模型层次

```
User Interaction
  -> App package / UID
    -> App Session / Activity / Window
      -> Frame token
        -> Process instance
          -> PID / TID
```

---

## 采集场景与数据集

| 场景 | 数据路径 | 说明 |
|------|----------|------|
| 页面切换（QQ） | [ebpf/ebpf_data/QQ页面切换场景/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/QQ%E9%A1%B5%E9%9D%A2%E5%88%87%E6%8D%A2%E5%9C%BA%E6%99%AF) | QQ 聊天界面滑动、页面跳转等操作下的调度与帧数据 |
| 页面切换（基础版） | [ebpf/ebpf_data/页面切换-基础版数据/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/%E9%A1%B5%E9%9D%A2%E5%88%87%E6%8D%A2-%E5%9F%BA%E7%A1%80%E7%89%88%E6%95%B0%E6%8D%AE) | 基础页面切换场景的 perfetto trace 与事件记录 |
| 信息流滚动 | [ebpf/ebpf_data/feed_scroll/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/feed_scroll) | Chrome 信息流滚动场景的帧统计与线程分析 |
| 相机 | [ebpf/ebpf_data/camera/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/camera) | 相机启动与预览场景的行为特征 |

### 各场景数据类型

- **eBPF 原始事件**：sched_switch、Binder 事务、锁竞争等 ringbuf 输出
- **Perfetto trace**：用于帧边界对齐与 jank 标定
- **行为特征**：经聚合与特征工程提取的 CSV 特征表
- **分析报告**：场景级的行为分析、线程重要性排序、优化建议

### 数据报告

| 报告 | 路径 |
|------|------|
| 页面切换场景数据分析报告 | [behavior_analysis_report.md](https://github.com/OSH-2026/TracePilot/blob/main/doc/report/behavior_analysis_report.md) |
| 页面切换-基础版数据分析报告 | [页面切换-基础版数据分析报告.md](https://github.com/OSH-2026/TracePilot/blob/main/doc/report/%E9%A1%B5%E9%9D%A2%E5%88%87%E6%8D%A2-%E5%9F%BA%E7%A1%80%E7%89%88%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90%E6%8A%A5%E5%91%8A.md) |
| 信息流滚动场景数据分析报告 | [feed_scroll_analysis_report.md](https://github.com/OSH-2026/TracePilot/blob/main/doc/report/feed_scroll_analysis_report.md) |

---

## eBPF 探针与采集内容

探针源码位于 [ebpf/src/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/src)

### 基础观察（Step 1）

- `sched_switch` / `sched_wakeup` — wakeup-to-run latency、runnable delay
- `binder_transaction` / `binder_transaction_received` — Binder 通信延迟
- `futex` wait / wake — 锁等待分析
- `cpu_frequency` — CPU 频率与大中小核信息

### 增强观察（Step 2，后续）

- memory reclaim（`mm_vmscan_direct_reclaim_begin/end`）
- page fault、block I/O、thermal throttling
- SurfaceFlinger / RenderEngine 调度

### 项目源码

- 页面切换场景 eBPF 程序 — 位于 `page_turning/` 子目录
- **页面切换-基础版** 完整项目 — `页面切换-基础版.zip`，包含 eBPF 探针源码（`tracepilot.bpf.c`）、C 加载器、Perfetto 工具链、Python 分析脚本、原始采集事件及分析报告

---

## 交互关键路径图（Interaction Critical Path Graph）

### 图节点

`Frame node` → `UI thread` → `RenderThread` → `Binder client/server` → `system_server` → `SurfaceFlinger` → `futex wait` → `CPU resource` → `memory reclaim` → `I/O wait`

### 边类型与权重

| 边类型 | 含义 |
|--------|------|
| WAKEUP | 线程唤醒关系 |
| RUNNABLE_WAIT | 就绪但未分配到 CPU |
| BINDER_CALL | Binder 跨进程调用 |
| FUTEX_WAIT | 锁等待 |
| CPU_RUN | 在 CPU 上运行 |
| PREEMPTED_BY | 被抢占 |
| FRAME_DEPENDENCY | 帧依赖关系 |
| RESOURCE_STALL | 资源瓶颈 |

### 关键线程评分模型

```
CriticalScore(tid) =
    a × frame_window_overlap
  + b × runnable_delay_p95
  + c × binder_dependency_centrality
  + d × futex_wait_contribution
  + e × render_path_proximity
  + f × repeated_jank_cooccurrence
  - g × background_penalty
```

---

## 目标与实施路线

| 层级 | 内容 | 状态 |
|------|------|------|
| **Step 1：基础管线** | eBPF 采集 + Perfetto ground truth + frame window 聚合 + 角色识别 + 临时 hint | ✅ 进行中 |
| **Step 2：增强** | Binder/futex 依赖图 + CPU 频率分析 + jank 分类 + 对比实验 | 📅 后续 |
| **Step 3：前沿** | inference-aware 调度 + memory/I/O/thermal + 多窗口竞争 + bandit 策略 | 📅 后续 |

### 当前已完成的 Step 1 子任务

- ✅ eBPF 探针采集 sched_switch / sched_wakeup
- ✅ Perfetto FrameTimeline 采集 jank ground truth
- ✅ 多场景数据采集（页面切换、信息流、相机）
- ✅ 行为特征提取与数据分析报告

---

## 安全 Hint 引擎

### Hint 类型

| 类型 | 说明 |
|------|------|
| BOOST_THREAD | 提升线程优先级 |
| PIN_TO_BIG_CORE_SHORT | 短暂固定到大核 |
| PROTECT_UI_CHAIN | 保护 UI 依赖链 |
| LOWER_BACKGROUND_COMPETITOR | 降低后台竞争 |
| UCLAMP_MIN_TEMPORARY | 临时 uclamp 下限 |

### 安全边界

**允许：** 前台 App UID、RenderThread/UI thread/确认的 Binder 依赖链、带 TTL、可回滚、记录审计日志

**禁止：** 直呼 PID boost、无 TTL hint、模糊/无差别调度、直接杀进程、修改 system_server 全局参数

---

## 技术要点

- **实验机**：Pixel 6a（解锁 + root，支持自定义 eBPF 加载）
- **主数据流**：eBPF → ringbuf → 落盘 → 离线处理 → 特征工程 → 行为分析
- **辅助工具**：Perfetto FrameTimeline 用于 ground truth 标定
- **分析框架**：Python 数据处理 + 依赖图构建 + 关键线程评分

---

## 交付物

- eBPF 探针源码与采集配置说明
- 多场景数据集（原始事件 + 特征表 + 分析报告）
- 行为分析工具链与报告生成脚本
- 依赖关键路径图构建与评分工具
- 实验报告：场景分析、特征重要性、优化建议

---

## 风险与前提

- 自定义 eBPF 依赖 **解锁与 root**，需排除运营商锁机器
- 多场景数据的标注与对齐依赖人工校验

---

# 项目进度

**注：** 有些刚开始的阶段没有会议记录，是因为没有进行相关的记录，不是该阶段没有进行会议

| 项目阶段 | 日期 | 项目进展 | 工作安排 |
|----------|------|----------|----------|
| **选题调研** | 3/9 ~ 3/15（第二周） | 研读往年项目 | 潘智勇：文件系统相关 贺小轩：rust改写相关 李松茂：任务调度相关 杨子皓，邵晨轩：rust改写 |
| **选题调研** | 3/16 ~ 3/22（第三周） | 开会通过自适应AI任务调度系统选题并向老师报告 | 李松茂：该方向的提出者；潘智勇：在树莓派上实现最小闭环并验证可行性并向老师汇报；贺小轩，杨子皓，邵晨轩：继续调研相关技术并完善报告 |
| **选题调研** | 3/23 ~ 3/29（第四周） | 由潘智勇向老师汇报后，答复为过于简单，很难做深。遂决定改变选题，将rust改写选题提交，被评价为过于简单。[会议记录](https://github.com/OSH-2026/TracePilot/blob/main/minutes%20of%20meetings/3-28%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md)| 贺小轩：提出rust改写方案 杨子皓：完善调研报告 潘智勇：向老师汇报 |
| **选题调研** | 3/30 ~ 4/3（第五周） | 提交六个选题，评价为四个深度不够，剩余两个可行性不高，开会决定继续调研。[会议记录](https://github.com/OSH-2026/TracePilot/blob/main/minutes%20of%20meetings/4-1%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md) | 李松茂：鸿蒙系统的LLM调优 贺小轩：鸿蒙异构内存 潘智勇：mini-VFS，fuse文件系统 杨子皓：rust改写NuttX的VFS 邵晨轩：AIOS的智能体操作系统 |
| **选题调研** | 4/3 ~ 4/5（第五周） | 潘智勇提交使用eBPF技术来优化linux调度器；邵晨轩提交面向AI agent的安全沙盒调研  老师认为潘智勇提出的使用eBPF技术方案可以，但是不要考虑linux，考虑鸿蒙或安卓，因为预调用对移动端帮助较大 | 潘智勇：调研了eBPF技术用于对linux的调度器优化 邵晨轩：调研了AIOS的相关技术和需求 |
| **选题调研** | 4/6 ~ 4/12（第六周） | 老师基本认可了在安卓系统的方案，但是数据来源有问题，虚拟环境体现不了真实用户数据，需要改为实体真机 | 李松茂，杨子皓：数据集调研 潘智勇，贺小轩：手机型号选择调研 |
| **立项** | 4/13 ~ 4/19（第七周） | 与老师对齐实验设备：pixel 6A(安卓16，root用magisk）并完成可行性报告。[会议记录](https://github.com/OSH-2026/TracePilot/blob/main/minutes%20of%20meetings/4-13%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md) | 潘智勇：购买真机并进行真机测试 邵晨轩，杨子皓：eBPF采集技术可行性报告 贺小轩：模型可行性报告 李松茂：将决策建议写入内核可行性报告 |
| **准备汇报** | 4/20 ~ 4/26（第八周） | 准备可行性汇报。[会议记录](https://github.com/OSH-2026/TracePilot/blob/main/doc/minutes%20of%20meetings/4-21%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md) | 调研：李松茂，贺小轩 实现一个特定场景下的数据采集和处理：邵晨轩，潘智勇，杨子皓 交叉编译环境的配置：潘智勇 |
| **初步工作** | 4/27 ~ 5/1（第九周） |完成交叉编译环境的搭建和对三个常见的场景进行eBPF信息采集和处理。页面切换场景：[ebpf/src/page_turning](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/src/page_turning)，对应数据处理报告：[behavior_analysis_report.md](https://github.com/OSH-2026/TracePilot/blob/main/doc/report/behavior_analysis_report.md)；信息流滚动场景数据：[ebpf/ebpf_data/feed_scroll](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/feed_scroll)，对应数据处理报告：[feed_scroll_analysis_report.md](https://github.com/OSH-2026/TracePilot/blob/main/doc/report/feed_scroll_analysis_report.md) | 中期汇报：潘智勇 |
| **数据进一步采集与处理** | 5/4 ~ 5/10（第十周） |对中期汇报得到的反馈进行调研和进一步工作，[会议记录](https://github.com/OSH-2026/TracePilot/blob/main/doc/minutes%20of%20meetings/5-6%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md) | 调研：李松茂，贺小轩 实现观测和处理：潘智勇，邵晨轩，杨子皓 |
