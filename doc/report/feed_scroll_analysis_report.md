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
