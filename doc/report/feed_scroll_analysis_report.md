# 信息流滚动场景 eBPF 数据处理报告

**场景说明**：本报告对应 Chrome 信息流滚动场景。实验在 Pixel 6a / Android 16 / Magisk root 环境下进行，打开固定 Chrome 信息流测试页面后连续向下滚动 30 次，通过 eBPF 采集内核调度事件，并在离线阶段生成秒级聚合表和线程级汇总表。

---

## 一、数据来源

### 1.1 原始数据文件

本场景数据位于：

```text
ebpf/ebpf_data/feed_scroll/
```

包含以下文件：

| 文件 | 含义 |
|------|------|
| `chrome_scroll_topdown.jsonl.gz` | 原始 eBPF 事件流，gzip 压缩，一行一个 JSON 事件 |
| `chrome_scroll_topdown_summary.json` | 后处理得到的总体指标摘要 |
| `feed_scroll_events_by_second.csv` | 按秒级窗口聚合后的事件统计表 |
| `feed_scroll_threads_summary.csv` | 按 Chrome 相关线程聚合后的线程指标表 |
| `chrome_scroll_topdown_framestats.txt` | 帧统计辅助数据，用于后续与 frame / jank 指标对齐 |

### 1.2 采集事件类型

本次采集主要关注以下内核事件：

| 事件 | 含义 | 用途 |
|------|------|------|
| `sched_switch` | CPU 从一个线程切换到另一个线程 | 计算线程 on-CPU 时间、runnable delay、CPU migration |
| `sched_waking` | 线程开始被唤醒 | 记录 wakeup 起点 |
| `sched_wakeup` | 线程唤醒完成 | 统计唤醒事件规模 |
| `cpu_frequency` | CPU 频率变化 | 辅助观察滚动过程中的调频行为 |

---

## 二、总体规模

### 2.1 原始事件规模

| 指标 | 数值 |
|------|------|
| 采集时长 | 34.232 s |
| 原始事件总数 | 2,614,133 |
| `sched_switch` | 1,274,776 |
| `sched_waking` | 654,062 |
| `sched_wakeup` | 654,056 |
| `cpu_frequency` | 31,239 |
| Chrome 相关线程数 | 34 |
| Chrome 相关线程 on-CPU 总时间 | 5331.07 ms |

可以看到，34 秒左右的滚动实验产生了约 261 万条原始事件。原始数据量较大，不能直接人工阅读，因此需要进一步聚合为时间窗口特征和线程级特征。

### 2.2 秒级聚合规模

原始事件经过秒级聚合后得到：

| 聚合方式 | 行数 |
|----------|------|
| 按秒聚合总事件 | 35 行 |
| 按 Chrome 相关线程聚合 | 34 行 |

也就是说，约 261 万条原始事件被压缩为 35 个秒级窗口和 34 条线程级摘要，便于后续画图、比较不同实验场景和输入模型。

---

## 三、秒级事件聚合分析

秒级聚合表为：

```text
feed_scroll_events_by_second.csv
```

字段含义如下：

| 字段 | 含义 |
|------|------|
| `window_sec` | 从采集开始算起的秒级窗口编号 |
| `window_start_ns` | 该窗口起始时间戳 |
| `window_end_ns` | 该窗口结束时间戳 |
| `total_events` | 该秒内全部 eBPF 事件数 |
| `sched_switch` | 该秒内线程切换次数 |
| `sched_waking` | 该秒内线程开始唤醒次数 |
| `sched_wakeup` | 该秒内线程唤醒完成次数 |
| `cpu_frequency` | 该秒内 CPU 频率变化次数 |

### 3.1 事件量最高的时间窗口

| window_sec | total_events | sched_switch | sched_waking | sched_wakeup | cpu_frequency |
|------------|--------------|--------------|--------------|--------------|---------------|
| 29 | 150,462 | 73,517 | 37,558 | 37,557 | 1,830 |
| 22 | 145,799 | 71,075 | 36,505 | 36,505 | 1,714 |
| 27 | 145,036 | 70,927 | 36,196 | 36,195 | 1,718 |
| 21 | 144,473 | 70,461 | 36,160 | 36,160 | 1,692 |
| 30 | 143,551 | 70,216 | 35,837 | 35,838 | 1,660 |

这些窗口集中在采集后半段，说明滚动操作持续进行时，线程切换、唤醒和 CPU 调频事件都保持在较高水平。后续如果有操作时间戳，可以将这些高事件窗口与具体滚动动作、快速回弹或停顿动作进一步对齐。

---

## 四、线程级指标分析

线程级汇总表为：

```text
feed_scroll_threads_summary.csv
```

字段含义如下：

| 字段 | 含义 |
|------|------|
| `tid` | 线程 ID |
| `comm` | 线程名 |
| `on_cpu_ms` | 该线程在采集期间累计运行在 CPU 上的时间 |
| `migration_count` | 该线程跨 CPU 核心迁移次数 |
| `wakeup_to_run_count` | 成功匹配到的 wakeup-to-run 样本数 |
| `wakeup_to_run_p95_ms` | 该线程从被唤醒到真正运行的 P95 延迟 |
| `wakeup_to_run_p99_ms` | 该线程从被唤醒到真正运行的 P99 延迟 |
| `runnable_delay_count` | 可运行队列等待样本数 |
| `runnable_delay_p95_ms` | runnable delay 的 P95 |
| `runnable_delay_p99_ms` | runnable delay 的 P99 |
| `sched_switch_in/out` | 该线程被切入/切出 CPU 的次数 |
| `sched_waking/wakeup` | 该线程相关唤醒事件数 |

### 4.1 on-CPU 时间最高的线程

| 线程 | on-CPU ms | migration_count | wakeup-to-run P95 ms | wakeup-to-run P99 ms |
|------|-----------|-----------------|----------------------|----------------------|
| `CompositorGpuTh` | 1109.560 | 2519 | 0.047 | 0.165 |
| `Compositor` | 955.978 | 3206 | 0.168 | 0.294 |
| `VizCompositorTh` | 800.063 | 2360 | 0.133 | 0.340 |
| `.android.chrome` | 762.779 | 1503 | 0.200 | 0.522 |
| `CrRendererMain` | 594.370 | 1350 | 0.231 | 0.481 |
| `CrGpuMain` | 355.309 | 804 | 0.085 | 0.231 |

这些线程主要集中在 Chrome 渲染、合成、GPU 和 Renderer 链路上，符合信息流滚动场景的预期。滚动过程中页面需要不断进行输入处理、布局/渲染、合成和提交显示，因此 `Compositor`、`CompositorGpuTh`、`VizCompositorTh` 等线程占据主要 on-CPU 时间。

---

## 五、调度延迟指标

### 5.1 wakeup-to-run latency

`wakeup-to-run latency` 表示线程被唤醒后，到真正被调度上 CPU 运行之间的等待时间。

本次整体结果：

| 指标 | 数值 |
|------|------|
| 样本数 | 24,025 |
| P95 | 0.152 ms |
| P99 | 0.371 ms |

这说明在所有成功匹配到的 wakeup-to-run 样本中，95% 的样本等待时间不超过 0.152 ms，99% 的样本等待时间不超过 0.371 ms。本次实验中，Chrome 相关线程从被唤醒到真正运行的尾部延迟较低，没有观察到明显的唤醒后长时间排队现象。

### 5.2 runnable delay

`runnable delay` 表示线程已经处于可运行状态后，在 run queue 中等待 CPU 的时间。

本次整体结果：

| 指标 | 数值 |
|------|------|
| 样本数 | 1,201 |
| P95 | 0.104 ms |
| P99 | 0.246 ms |

该指标同样较低，说明本次滚动采样中没有明显的调度排队尖峰。后续如果引入 CPU 重载/内存重载场景，可以比较重载前后该指标是否明显上升。

---

## 六、CPU 迁移分析

本次 Chrome 相关线程共发生：

```text
CPU migration count = 14,250
```

迁移次数较高，说明 Chrome 相关线程在滚动过程中频繁被调度到不同 CPU 核心上执行。跨核迁移不一定直接造成卡顿，但可能带来缓存局部性下降、大小核切换成本增加等影响。

从线程角度看，迁移次数较高的线程包括：

| 线程 | migration_count |
|------|-----------------|
| `Compositor` | 3206 |
| `CompositorGpuTh` | 2519 |
| `VizCompositorTh` | 2360 |
| `.android.chrome` | 1503 |
| `CrRendererMain` | 1350 |

这些线程本身也是主要渲染链路线程，因此后续可以重点观察：在重载场景下，这些关键线程的 migration count 是否进一步升高，以及是否与 frame time / jank rate 恶化同时出现。

---

## 七、结论

1. **信息流滚动场景下内核调度事件量较大**：34.232 秒采集得到约 261 万条原始事件，其中 `sched_switch` 超过 127 万条，说明滚动过程中线程调度非常频繁。
2. **主要 CPU 消耗集中在渲染与合成线程**：`CompositorGpuTh`、`Compositor`、`VizCompositorTh`、`CrRendererMain`、`CrGpuMain` 等线程贡献主要 on-CPU 时间，符合 Chrome 信息流滚动的渲染链路预期。
3. **调度等待延迟整体较低**：整体 wakeup-to-run P95 为 0.152 ms，runnable delay P95 为 0.104 ms，本次样本没有出现明显调度排队尖峰。
4. **跨核迁移次数较高**：Chrome 相关线程共发生 14,250 次 CPU migration，后续应结合 CPU 核心类型、CPU 频率和 frame/jank 指标继续分析。
5. **CSV 特征表便于后续扩展**：`feed_scroll_events_by_second.csv` 可用于观察秒级负载变化，`feed_scroll_threads_summary.csv` 可用于区分关键/非关键线程并做重载/非重载对比。

---

## 八、补充采集：帧级 ground truth、Binder/ftrace 与关键线程评分

为对齐后续参考文档中提出的 frame-centric / dependency-centric 目标，在 2026-05-20 对同一 Chrome 信息流滚动场景做了一轮补充采集。补充数据仍放在同一数据目录 `ebpf/ebpf_data/feed_scroll/` 下，文件名前缀为 `feed_scroll_supplement_20260520`。

### 8.1 补充数据文件

原始数据：

- `feed_scroll_supplement_20260520.jsonl.gz`：eBPF 原始调度事件。
- `feed_scroll_supplement_20260520_ftrace.txt`：同一时间窗口内的 ftrace 原始事件，包含 Binder、dma_fence、block I/O 等事件。
- `feed_scroll_supplement_20260520_framestats.txt`：Chrome `dumpsys gfxinfo framestats` 原始输出。

处理后数据：

- `feed_scroll_supplement_20260520_summary.json`：eBPF 调度汇总。
- `feed_scroll_supplement_20260520_events_by_second.csv`：秒级事件聚合。
- `feed_scroll_supplement_20260520_threads_summary.csv`：线程级调度指标。
- `feed_scroll_supplement_20260520_threads_classified.csv`：关键/非关键线程分类。
- `feed_scroll_supplement_20260520_threads_scored.csv`：CriticalScore 关键线程评分。
- `feed_scroll_supplement_20260520_ftrace_summary.json`：ftrace/Binder 系统依赖事件摘要。
- `feed_scroll_supplement_20260520_frame_summary.json`：帧级 ground truth 摘要。
- `feed_scroll_supplement_20260520_frames.csv`：逐帧解析结果。

### 8.2 eBPF 调度补充结果

| 指标 | 数值 |
|------|------|
| 采集时长 | 39.664 s |
| eBPF 原始事件 | 1,204,411 |
| `sched_switch` | 572,666 |
| `sched_waking` | 305,378 |
| `sched_wakeup` | 305,376 |
| `cpu_frequency` | 20,991 |
| 相关线程数 | 17 |
| wakeup-to-run P95 / P99 | 0.390 / 0.669 ms |
| runnable delay P95 / P99 | 0.373 / 1.229 ms |
| CPU migration count | 9,263 |

### 8.3 帧级 ground truth

`gfxinfo` 原始摘要显示：

| 指标 | 数值 |
|------|------|
| Total frames rendered | 5,507 |
| Janky frames | 1 |
| Jank rate | 0.02% |
| Frame time P50 / P90 / P95 / P99 | 28 / 30 / 31 / 32 ms |
| Missed Vsync | 0 |
| Frame deadline missed | 1 |
| Slow UI thread | 0 |
| Slow issue draw commands | 1 |

逐帧表中解析到近期 118 行 frame 记录，`frame_time_p95_ms` 为 35.411 ms，`frame_time_p99_ms` 为 35.604 ms。需要注意的是，Android 16 的 `FrameTimeline` 中还包含 `FrameDeadline` 字段，因此单纯用 `FrameCompleted - IntendedVsync > 16.6 ms` 会偏保守；后续更适合以 `deadline_missed` 和 `gfxinfo` reported jank 作为 frame-centric ground truth。

### 8.4 Binder / 系统依赖证据

本轮同窗口 ftrace 共采集到 1,818 条系统事件：

| 事件 | 数量 |
|------|------|
| `binder_wait_for_work` | 799 |
| `binder_transaction` | 425 |
| `binder_transaction_received` | 335 |
| `dma_fence_wait_start/end` | 103 / 104 |
| `block_rq_issue/complete` | 30 / 22 |

Binder 相关 top comm 包括 `.android.chrome`、`HwBinder:604_2`、`surfaceflinger`、`binder:600_1`、`RenderThread` 和 `binder:25132_3`。这说明信息流滚动期间，Chrome 主进程、RenderThread、SurfaceFlinger 与 Binder 线程之间存在可观测的系统服务依赖。当前数据已经能作为依赖证据，但还没有进一步重建完整 Binder client-server 调用链。

### 8.5 CriticalScore 关键线程评分

在原有关键/非关键分类基础上，本轮加入 CriticalScore 排名：

```text
score =
  render_path_role
+ wakeup_to_run_p95
+ runnable_delay_p95
+ log(on_cpu_ms)
+ log(migration_count)
+ log(wakeup_count)
- background_penalty
```

Top critical threads 如下：

| Rank | TID | comm | score | on-CPU ms | wakeup P95 ms | runnable P95 ms | migrations |
|---:|---:|---|---:|---:|---:|---:|---:|
| 1 | 25167 | RenderThread | 100.736 | 11078.226 | 0.454 | 0.263 | 3030 |
| 2 | 649 | RenderEngine | 93.999 | 1417.180 | 0.153 | 0.998 | 657 |
| 3 | 25132 | .android.chrome | 89.972 | 5031.003 | 0.573 | 0.348 | 3139 |
| 4 | 600 | surfaceflinger | 79.927 | 6250.098 | 0.253 | 0.115 | 1520 |
| 5 | 2067 | RenderThread | 77.240 | 115.032 | 0.325 | 0.234 | 77 |
| 6 | 702 | surfaceflinger | 72.289 | 57.855 | 0.777 | 0.302 | 725 |
| 7 | 1216 | system_server | 65.476 | 35.240 | 0.592 | 1.264 | 68 |

关键线程分类结果为：关键线程 9 个，非关键线程 8 个。关键线程承担了绝大多数调度负载，on-CPU 时间为 23,961.696 ms，非关键线程 on-CPU 时间为 49.602 ms。

### 8.6 仍需继续完善的部分

- futex：当前设备的 `available_events` 中未发现标准 futex tracepoint，本轮未采集 futex wait/wake 事件。
- Binder 调用链：已采到 Binder transaction/received/wait 事件，但尚未根据 transaction id 重建完整 client-server 调用链。
- Frame window join：已有 frame table、eBPF 事件和 ftrace 事件，但还没有按每个 frame window 将调度事件和 Binder 事件逐帧关联。

本轮补充采集后，信息流滚动场景已经从单纯线程调度统计推进到“帧级 ground truth + Binder/ftrace 依赖证据 + CriticalScore 关键线程排名”。后续最值得继续做的是将 frame window 与 eBPF/ftrace 事件按时间戳对齐，输出每个 frame 的 top blocking threads 和系统依赖链。

---

## 九、Step 2 同步对齐增强分析（2026-05-27）

为进一步完成 Binder dependency graph、CPU frequency / big-little 归因、规则版 jank cause 分类和启发式目标选择对比，本轮在固定长页面 `TracePilot 固定信息流` 上执行自动滚动，将 eBPF 调度事件、ftrace 系统依赖事件与屏幕合成帧置于同一个采集窗口中。正式数据文件名前缀为 `feed_scroll_step2_aligned_20260527`，仍统一存放在 `ebpf/ebpf_data/feed_scroll/` 下。

本机当前 Chrome 将网页滚动绘制提交给独立合成 surface，`dumpsys gfxinfo com.android.chrome` 在滚动窗口中仅报告 1 帧，不能作为网页内容的帧真值。因此本轮将帧证据切换为 SurfaceFlinger 的 `com.android.chrome/ChromeChildSurface` 呈现时间戳；`gfxinfo` 原始输出仍保留为该限制的辅助证据。

答辩口径上，这一项应表述为**网页滚动场景下的代理帧证据**：SurfaceFlinger interval 能覆盖实际被合成显示的网页 surface，适合做离线窗口对齐和异常呈现间隔筛查；但它不是完整的 app FrameTimeline 标签，不能单独证明某个 Binder 或调度事件就是 jank 根因。

### 9.1 同步采集数据概况

| 指标 | 数值 |
|------|------|
| 采集时长 | 39.353 s |
| eBPF 原始事件 | 4,328,470 |
| `sched_switch` | 2,106,384 |
| `sched_waking` | 1,084,486 |
| `sched_wakeup` | 1,084,486 |
| `cpu_frequency` | 53,114 |
| 相关线程数 | 53 |
| wakeup-to-run P95 / P99 | 0.197 / 0.442 ms |
| runnable delay P95 / P99 | 0.150 / 0.297 ms |
| CPU migration count | 30,134 |
| 关键 / 非关键线程数 | 20 / 33 |

合成层帧窗口摘要如下。SurfaceFlinger 的 `--latency` 为环形窗口，因此这里覆盖的是采集期间最近的一段有效滚动呈现记录，而不是完整 40 秒的逐帧全集。

| 指标 | 数值 |
|------|------|
| 帧证据层 | `ChromeChildSurface` |
| 刷新周期 | 16.667 ms |
| 保留的呈现间隔 | 126 |
| P50 / P95 / P99 interval | 16.723 / 16.816 / 33.570 ms |
| 异常长间隔候选（`interval > 1.5 x refresh`） | 2（1.59%） |
| 与 eBPF/ftrace 时间窗对齐的记录 | 126 |
| `gfxinfo` 主进程辅助记录 | 1 frame，不作为滚动真值 |

### 9.2 Binder dependency graph

通过 `binder_transaction`、`binder_transaction_received` 和 `binder_wait_for_work` 构建 Binder 边表，得到 32 条可观测依赖边。主要依赖边如下：

| Source | Target | transaction count | matched latency P95 ms |
|--------|--------|------------------:|-----------------------:|
| `VizCompositorTh` | `surfaceflinger` | 112 | - |
| `CompositorGpuTh` | `surfaceflinger` | 98 | - |
| `binder:600_1` | `proc_5364` | 52 | - |
| `HwBinder:602_2` | `surfaceflinger` | 40 | 0.149 |
| `surfaceflinger` | `proc_602` | 38 | - |
| `BckgrndExec HP` | `proc_5364` | 33 | - |

其中最清晰的滚动合成依赖路径为：

```text
VizCompositorTh / CompositorGpuTh -> surfaceflinger -> display composition
```

这说明固定信息流滚动负载中，Chrome 的 Viz/GPU 合成线程持续向系统合成服务提交工作。完整边表见 `feed_scroll_step2_aligned_20260527_binder_dependency_edges.csv`。

### 9.3 futex wait graph 可用性结论

本轮再次核查了 Pixel 6a 当前内核提供的事件源：

- `available_events` 中没有标准 futex wait/wake tracepoint。
- 动态 `kprobe_events` 在设备上不可写，无法安全补挂 `futex_wait` / `futex_wake`。

因此，本场景的 futex wait graph 在当前设备内核配置下无法用真实事件构建。该项记录为**设备观测能力限制**，而不是将缺失数据替换为推断结果。

这条结论本身也是工程结果：当前采集链路已经完成了设备能力探测，并明确 futex wait/wake 在该 Pixel 6a 配置下不能通过标准 tracepoint 获得。后续如果要补 futex，需要在真机上验证 raw syscall tracepoint、可写 kprobe 或 TracePilot 增强事件方案，而不是在离线报告中用推断值代替真实事件。

### 9.4 CPU frequency / big-little 归因

依据 Pixel 6a Tensor CPU 拓扑，将 CPU 0-3 视为 little cluster，CPU 4-5 视为 middle cluster，CPU 6-7 视为 big cluster。本轮 Chrome/渲染/system 相关线程的聚合结果如下：

| Cluster | Target on-CPU ms | Frequency events | Avg frequency kHz | P95 frequency kHz | Max frequency kHz |
|---------|-----------------:|-----------------:|------------------:|------------------:|------------------:|
| little_0_3 | 4,017.744 | 31,844 | 1,243,992.6 | 1,803,000 | 1,803,000 |
| middle_4_5 | 5,238.898 | 12,340 | 1,190,027.1 | 2,130,000 | 2,253,000 |
| big_6_7 | 6,715.341 | 8,930 | 949,893.2 | 1,745,000 | 2,802,000 |

结果显示本轮渲染链路明显使用了性能核：big cluster 的目标线程 on-CPU 时间最高（6,715.341 ms），并观察到最高 2,802,000 kHz 的频率采样。

### 9.5 规则版 jank cause classifier

本轮基于 SurfaceFlinger 呈现间隔建立同步 frame window，对窗口内的 Binder、display fence 和 block I/O 事件进行聚合，生成 `feed_scroll_step2_aligned_20260527_frame_dependency_join.csv`。

126 条呈现间隔记录均能够与 eBPF/ftrace 窗口对齐，其分类结果为：

| 证据类别 | Frame windows |
|----------|--------------:|
| Binder dependency | 95 |
| scheduler / render work | 31 |

其中 2 个异常长呈现间隔候选分别为 33.570 ms 与 300.984 ms，规则分类均落在 `binder_dependency` 窗口，窗口内主要可见 `binder:600_1` 活动。需要强调：这表示“异常长呈现间隔与 Binder 活动共现”，并不能证明 Binder 是卡顿原因；尤其 300.984 ms 还可能包含相邻手势之间的无新帧空档。若要形成真实 jank 标签及因果归因，仍需引入完整 Perfetto FrameTimeline。

### 9.6 启发式目标选择对比

本轮对三种离线线程选择规则进行比较。该对比用于判断不同规则会挑选哪些候选关键线程，**没有实际执行调度 hint，也不声称改善帧性能**。

| Policy | Top-5 selected threads | Pipeline-role count | System-thread count |
|--------|------------------------|--------------------:|--------------------:|
| CPU only | `CompositorGpuTh`, `Compositor`, `VizCompositorTh`, `.android.chrome`, `CrRendererMain` | 5 | 0 |
| Latency only | `CronetInit`, `VizWebView`, `Chrome_ProcessL`, `Chrome_IOThread`, `scroll-bg-task` | 0 | 0 |
| Pipeline CriticalScore | `CrRendererMain`, `.android.chrome`, `Compositor`, `VizCompositorTh`, `CompositorGpuTh` | 5 | 0 |

对比结果表明，仅按延迟排序容易挑中低负载、与主渲染链路关系较弱的线程；结合渲染角色、CPU 时间、迁核和延迟的 `Pipeline CriticalScore` 更适合输出信息流滚动场景下的候选保护线程。

### 9.7 Step 2 完成状态

| Step 2 项目 | 当前结果 |
|-------------|----------|
| Binder dependency graph | 已生成 Binder 依赖边表和主要合成依赖链 |
| futex wait graph | 设备内核无可用事件源，记录为观测限制 |
| CPU frequency / big-little 归因 | 已完成三档 cluster 的 on-CPU 与频率统计 |
| jank cause classifier | 已实现 SurfaceFlinger 呈现窗口规则分类；真实 jank 根因仍需 Perfetto FrameTimeline 标签 |
| 与启发式策略对比 | 已完成离线候选线程选择对比；执行效果实验需后续 actuator/hint 支持 |

---

### 9.8 当前答辩口径

信息流滚动场景在 2026-05-27 样本中可以表述为：**Step1/Step2 的离线观测和候选分析已经完成到代理帧窗口级别；当时的缺口集中在完整 FrameTimeline 标签、futex 真机能力和真实 hint 干预实验。**

可直接展示的完成项：

- Step1 已完成 eBPF 调度事件采集：39.353 s 内采集 4,328,470 条原始事件，其中 `sched_switch` 2,106,384 条，`sched_waking/wakeup` 各 1,084,486 条。
- Step1 已完成 Chrome 渲染链路关键线程识别和 CriticalScore 排序，Top 线程包括 `Compositor`、`VizCompositorTh`、`CompositorGpuTh`、`CrRendererMain`、`.android.chrome`。
- Step2 已生成 32 条 Binder 依赖边，能说明 `VizCompositorTh / CompositorGpuTh -> surfaceflinger -> display composition` 的合成链路。
- Step2 已完成 CPU frequency / big-little 归因，big cluster 的目标线程 on-CPU 时间最高，为 6,715.341 ms。
- Step2 已完成离线候选策略对比，`Pipeline CriticalScore` 相比纯 latency 更贴近 Chrome 渲染链路。

必须保守说明的限制：

- 当前帧证据是 `ChromeChildSurface` 的 SurfaceFlinger interval，不是完整 Perfetto FrameTimeline。
- 2 个异常长间隔候选只能说明与 Binder 活动共现，不能证明 Binder 是唯一原因。
- futex wait graph 是设备观测能力限制，当前没有真实 futex 事件源。
- 离线策略对比只比较候选目标，没有实际下发 hint，也不能声称改善帧性能。

---

## 十、真机 Chrome FrameTimeline 补采与当前最终口径（2026-07-01）

2026-07-01 在 Pixel 6a / Android 16 上补充了一轮 Chrome 信息流滚动真机采集，数据目录为 `ebpf/ebpf_data/feed_scroll/feed_scroll_live_20260701_134759/`。本轮由 ADB 自动执行约 32 次滚动手势，采集 40 s，同步保存 TracePilot `events.bin`、ftrace、gfxinfo、SurfaceFlinger、Perfetto trace，并完成 Perfetto FrameTimeline、sched frame-window、CPU frequency / big-little 和 TracePilot offline replay 分析。

本轮最重要的变化是：Perfetto FrameTimeline 已经通过 `source_filter=package_filter` 命中 `com.android.chrome`，因此当前 feed_scroll 最终口径应优先使用 Chrome package-filtered FrameTimeline；第 9 节的 SurfaceFlinger interval 保留为早期代理帧证据和设备限制说明。

### 10.1 FrameTimeline ground truth

| 指标 | 数值 |
|---|---:|
| FrameTimeline source filter | `package_filter` |
| 目标包名 | `com.android.chrome` |
| Frame count | 1768 |
| Deadline missed | 24 |
| Deadline missed rate | 1.36% |
| Frame time avg / p50 / p95 / p99 | 3.215 / 2.930 / 4.069 / 16.815 ms |

这说明 Step1 中 “Perfetto FrameTimeline 采集 jank ground truth” 已从代理帧补证升级为 Chrome 包名过滤下的真实 FrameTimeline 证据。`gfxinfo` 在本设备上仍只报告 1 frame，因此本场景后续不再把 `gfxinfo` 作为网页内容滚动的主帧源。

### 10.2 Perfetto sched frame-window 聚合

基于 1768 个 FrameTimeline 窗口，`analyze_perfetto_sched_windows.py` 从 Perfetto `thread_state` 表聚合 Running 与 Runnable 状态，共输出 103343 条 frame-thread 记录。Top 线程如下：

| Rank | Thread | on-CPU ms | Runnable wait ms | Runnable wait p95 ms |
|---:|---|---:|---:|---:|
| 1 | `surfaceflinger` | 5596.666 | 294.082 | 0.313 |
| 2 | `VizCompositorTh` | 2408.481 | 730.452 | 0.974 |
| 3 | `Compositor` | 2173.516 | 849.417 | 0.901 |
| 4 | `CompositorGpuTh` | 2273.160 | 357.278 | 0.468 |
| 5 | `CrRendererMain` | 1625.172 | 527.460 | 0.846 |
| 6 | `.android.chrome` | 1754.590 | 390.000 | 0.582 |

因此 feed_scroll 的 Step1 frame-window runnable/on-CPU 聚合现在有 Perfetto 侧直接证据；TracePilot offline graph 中 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边仍为 0，调度侧答辩应优先引用 Perfetto crosscheck。

### 10.3 CPU frequency / big-little

| Cluster | Avg freq kHz | Jank avg freq kHz | Max freq kHz |
|---|---:|---:|---:|
| big | 2,719,291.0 | 2,798,992.2 | 2,802,000 |
| middle | 1,192,249.9 | 1,192,511.4 | 2,253,000 |
| little | 1,484,302.1 | 1,558,555.7 | 1,803,000 |

Chrome 渲染/合成线程主要运行在 middle/big cluster：`VizCompositorTh` 为 90.93%，`CompositorGpuTh` 为 98.97%，`Compositor` 为 98.22%，`CrRendererMain` 为 88.25%。

### 10.4 TracePilot offline replay 与 dry-run hint

TracePilot offline replay 使用 `com.android.chrome` 显式包名运行，输出如下：

| 指标 | 数值 |
|---|---:|
| Total frames | 1768 |
| Jank frames | 24 |
| Total nodes / edges | 4270 / 1419 |
| `BINDER_CALL` edges | 24 |
| `FUTEX_WAIT` edges | 692 |
| `FRAME_DEPENDENCY` edges | 695 |
| Jank cause candidate | 24 个 frame 均为 `CPU_CONTENTION` |
| Graph AP@K / heuristic AP@K | 0.1 / 0.1 |

Dry-run hint 输出 `PROTECT_UI_CHAIN -> surfaceflinger`，TTL 300 ms，rollback 为 restore affinity。该结果证明 hint schema、目标选择和审计字段已经跑通，但本轮没有执行真实 `--hint-apply`，不能声称已经改善帧性能。

### 10.5 当前最终答辩口径

信息流滚动场景当前可以表述为：**Step1/Step2 的观测与离线分析链路已经跑通，并且已补齐 Chrome package-filtered FrameTimeline；baseline vs intervention 真实采集闭环已经初跑，但效果是 mixed，不能声称 hint 已稳定改善帧性能。**

可直接展示的完成项：

- Step1 已有 Chrome package-filtered Perfetto FrameTimeline：1768 帧，24 个 deadline missed，missed rate 1.36%。
- Step1 已完成 frame-window 内 Running/Runnable 聚合，Top 线程覆盖 `surfaceflinger`、Chrome Viz/GPU/Compositor/Renderer 链路。
- Step2 已完成 CPU frequency / big-little 归因，Chrome 渲染线程主要使用 middle/big cluster。
- Step2 已有 TracePilot replay 图：`BINDER_CALL=24`、`FUTEX_WAIT=692`、`FRAME_DEPENDENCY=695`。
- Hint Engine 已输出 dry-run `PROTECT_UI_CHAIN`，具备 TTL 与 rollback 字段。

必须保守说明的限制：

- 2026-07-01 的 3 baseline + 3 intervention 初跑没有形成稳定改善结论，不能声称 jank rate 或 p95/p99 已因 hint 改善。
- TracePilot graph 内 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边仍为 0，调度归因主要依赖 Perfetto sched crosscheck。
- 设备上 SurfaceFlinger 的 `sched_boost`/`uclamp` actuator 不可用，真实干预需要采用可回滚的 Chrome 渲染线程 priority/cpuset 方案，并单独报告 actuator 成功率。

### 10.6 Baseline vs intervention 初跑结果

2026-07-01 晚间补做了一轮真实 actuator 初跑，目录为 `ebpf/ebpf_data/feed_scroll/feed_scroll_intervention_20260701_222452/`。实验设置为 3 轮 baseline + 3 轮 intervention，每轮约 20 s；intervention 目标为 Chrome 渲染/合成相关线程，尝试临时调整 nice priority，并记录 actuator audit。

聚合结果如下：

| 模式 | Runs | Avg frames | Avg deadline missed | Avg missed rate | Avg p95 ms | Avg p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 3 | 962.0 | 2.0000 | 0.22% | 3.7353 | 4.7803 |
| intervention | 3 | 892.0 | 1.6667 | 0.18% | 4.2443 | 5.3727 |
| intervention - baseline | - | -70.0 | -0.3333 | -0.04 pp | +0.5090 | +0.5924 |

逐轮 actuator audit：

| Run | Mode | Frames | Missed rate | p95 / p99 ms | Unique targets | Renice success | Cpuset success |
|---|---|---:|---:|---:|---:|---:|---:|
| baseline 1 | baseline | 1010 | 0.40% | 3.728 / 5.047 | 0 | 0 | 0 |
| intervention 1 | intervention | 1009 | 0.20% | 4.873 / 6.770 | 25 | 21 | 0 |
| baseline 2 | baseline | 802 | 0.25% | 3.716 / 4.137 | 0 | 0 | 0 |
| intervention 2 | intervention | 658 | 0.15% | 3.970 / 4.594 | 25 | 0 | 0 |
| baseline 3 | baseline | 1074 | 0.00% | 3.762 / 5.157 | 0 | 0 | 0 |
| intervention 3 | intervention | 1009 | 0.20% | 3.890 / 4.754 | 25 | 0 | 0 |

结论要保守写：

- 这轮已经证明真实干预实验的采集、滚动 workload、Perfetto 解析、FrameTimeline 指标聚合和 actuator audit 闭环可以跑通。
- 指标本身不支持“稳定改善”结论：deadline missed rate 只有很小下降，但 p95/p99 frame time 反而上升。
- `renice_success_count` 在第 2/3 轮变为 0，是因为第 1 轮后目标线程已经处在更高优先级状态；旧脚本使用 `renice -n`，在 Android toybox 上表现为相对调整，可能导致 nice 值累积下降。脚本已改为绝对 `renice <priority> -p <tid>` 并回查 `/proc/<tid>/stat`。
- 因此本轮更适合作为“真实 actuator 初跑与限制说明”，不适合作为最终性能改善证据。若还需要更强结论，应先重启/强停 Chrome 清空线程状态，再用修正后的脚本复测；如果时间不够，答辩中直接说真实干预闭环已跑通但效果 mixed。
