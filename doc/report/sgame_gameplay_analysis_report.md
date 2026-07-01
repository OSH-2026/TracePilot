# 王者荣耀游戏场景 eBPF 采集分析记录

## 目录
- [1. 采集对象与数据目录](#1-采集对象与数据目录)
- [2. 原始数据与后处理产物](#2-原始数据与后处理产物)
- [3. 全局调度指标对比](#3-全局调度指标对比)
- [4. 关键线程分析](#4-关键线程分析)

## 1. 采集对象与数据目录

本次分析对象为 Pixel 6a 上运行的王者荣耀进程：

- 应用包名：`com.tencent.tmgp.sgame`
- Activity：`com.tencent.tmgp.sgame.SGameActivity`
- 采集方式：TracePilot eBPF 调度事件 + ftrace 补充事件
- 截图：未保存，避免采集登录后的个人信息

本次包含两轮样本：

| 样本 | 场景 | 采集窗口 | 数据目录 |
|---|---:|---:|---|
| `game_play_sgame_20260601_1200` | 登录后/游戏内短窗口 | 24.792 s | `TracePilot_work/data/raw/game_play_sgame_20260601_1200` |
| `game_match_sgame_20260601_120627` | 对局内操作窗口 | 59.178 s | `TracePilot_work/data/raw/game_match_sgame_20260601_120627` |

两轮采集开始和结束时，前台窗口均保持为 `com.tencent.tmgp.sgame/.SGameActivity`，因此样本可以视为目标游戏场景数据。

## 2. 原始数据与后处理产物

未压缩的 `.jsonl` 文件是手机端 `tracepilot` 直接输出并通过 `adb pull` 拉回的 eBPF 原始事件流。每一行是一个 JSON 事件，主要包括：

- `sched_switch`
- `sched_waking`
- `sched_wakeup`
- `cpu_frequency`

对应的 `.jsonl.gz` 是同一原始事件流的 gzip 压缩版，内容等价，适合长期保存和后续分析。

| 样本 | 未压缩 `.jsonl` | 压缩 `.jsonl.gz` | 说明 |
|---|---:|---:|---|
| `game_play_sgame_20260601_1200` | 918,846,253 B | 50,221,877 B | 第一轮压缩成功，但删除未压缩文件时被 Windows 拒绝访问 |
| `game_match_sgame_20260601_120627` | 1,221,299,254 B | 73,374,954 B | 第二轮压缩成功，脚本已记录未压缩副本保留状态 |

当前这些数据仍在本地工作区内，`TracePilot_work/` 整体处于 Git 未跟踪状态；没有被加入暂存区、没有 commit，也没有上传到 GitHub。

## 3. 全局调度指标对比

| 指标 | 短窗口 | 对局窗口 | 观察 |
|---|---:|---:|---|
| 有效采集时长 | 24.792 s | 59.178 s | 第二轮覆盖真实对局操作 |
| 目标线程数 | 35 | 31 | 目标线程集合规模接近 |
| 目标线程总 on-CPU | 9,417.834 ms | 39,670.538 ms | 对局内 CPU 消耗显著更高 |
| on-CPU / 秒 | 379.9 ms/s | 670.3 ms/s | 对局负载约为短窗口的 1.76 倍 |
| wakeup-to-run p95 | 0.105 ms | 0.153 ms | 对局中唤醒到运行延迟略升高 |
| wakeup-to-run p99 | 0.362 ms | 0.589 ms | 尾延迟上升 |
| runnable delay p95 | 0.304 ms | 0.664 ms | 对局中可运行等待更明显 |
| runnable delay p99 | 0.816 ms | 1.995 ms | 尾部等待放大 |
| migration count | 6,416 | 20,098 | 对局中线程迁移更频繁 |
| migration / 秒 | 258.8/s | 339.6/s | 单位时间迁移也升高 |

结论：对局内样本相较短窗口表现出更高的 CPU 使用、更高的 runnable delay，以及更密集的线程迁移，说明 60 秒样本更适合代表真实游戏负载。

## 4. 关键线程分析

### 4.1 短窗口 Top 线程

| 线程 | on-CPU | 迁移次数 | wakeup p95 | runnable p95 |
|---|---:|---:|---:|---:|
| `UnityMain` | 5,176.440 ms | 1,087 | 0.059 ms | 0.298 ms |
| `UnityGfxDeviceW` | 2,271.451 ms | 2,308 | 0.081 ms | 0.299 ms |
| `surfaceflinger` | 1,472.890 ms | 704 | 0.129 ms | 0.046 ms |
| `RenderThread` | 236.376 ms | 204 | 0.172 ms | 0.434 ms |
| `cent.tmgp.sgame` | 53.582 ms | 183 | 0.104 ms | 0.000 ms |

### 4.2 对局窗口 Top 线程

| 线程 | on-CPU | 迁移次数 | wakeup p95 | runnable p95 |
|---|---:|---:|---:|---:|
| `UnityMain` | 22,042.669 ms | 1,183 | 0.098 ms | 0.572 ms |
| `UnityGfxDeviceW` | 10,231.310 ms | 4,411 | 0.096 ms | 1.584 ms |
| `surfaceflinger` | 4,527.546 ms | 1,879 | 0.170 ms | 0.079 ms |
| `cent.tmgp.sgame` | 2,047.678 ms | 6,113 | 0.142 ms | 1.962 ms |
| `RenderThread` | 257.200 ms | 238 | 0.148 ms | 0.756 ms |
| `RenderThread` | 124.964 ms | 1,817 | 0.153 ms | 0.227 ms |

对局内 `UnityMain` 和 `UnityGfxDeviceW` 是最主要的 CPU 消耗线程。其中 `UnityGfxDeviceW` 的 runnable p95 从短窗口的 0.299 ms 增至 1.584 ms，说明图形相关工作在对局内更容易进入可运行等待状态。`cent.tmgp.sgame` 主进程线程在对局中迁移次数达到 6,113，runnable p95 达到 1.962 ms，是后续调度策略候选目标之一。

## 5. ftrace 补充事件

| 事件类型 | 短窗口 | 对局窗口 | 观察 |
|---|---:|---:|---|
| `binder_wait_for_work` | 541 | 807 | 对局中 Binder 等待更多 |
| `binder_transaction` | 271 | 464 | IPC 交互增加 |
| `binder_transaction_received` | 251 | 455 | 与 transaction 增长一致 |
| `block_rq_issue` | 168 | 20 | 短窗口中 IO 活动更明显 |
| `block_rq_complete` | 154 | 21 | 对局窗口 IO 事件较少 |
| `thermal_temperature` | 50 | 45 | 两轮均有温度采样 |
| `dma_fence_wait_start` | 40 | 30 | 两轮均出现显示同步等待 |
| `dma_fence_wait_end` | 40 | 32 | 与 wait_start 基本匹配 |

ftrace 结果说明：对局窗口中 Binder 相关事件增加，符合游戏运行期间与系统服务、图形栈、输入/网络等组件持续交互的预期；短窗口的 block IO 更多，可能与登录后资源加载或后台数据刷新有关。

## 6. 帧级数据现状

两轮样本的 `gfxinfo framestats` 均未给出有效逐帧表：

- `frame_count = 0`
- `reported_total_frames = 0`
- SurfaceFlinger latency 输出仅 9 B，无法提供有效 presentation interval

因此，当前报告不对“某一帧卡顿由某个调度原因导致”下结论。现有数据可以支持调度侧、线程侧、Binder/IO/thermal/dma_fence 侧分析；若需要帧级 ground truth，下一步应补 Perfetto 采集。

## 7. 初步结论

1. TracePilot 已经可以在真实王者荣耀对局中稳定采集 eBPF 调度事件，并拉回可分析的 JSONL/CSV/JSON 摘要。
2. 60 秒对局窗口比短窗口更能代表真实游戏负载：on-CPU/秒、runnable delay p95/p99、线程迁移率均明显升高。
3. `UnityMain`、`UnityGfxDeviceW`、`surfaceflinger`、`RenderThread` 是当前最值得关注的关键线程集合。
4. 对局内 `UnityGfxDeviceW` 与 `cent.tmgp.sgame` 的 runnable delay 和迁移次数较高，后续可以作为调度提示或关键线程识别模型的候选目标。
5. 当前帧级数据缺失，不能直接建立 jank 与调度事件的监督标签；需要引入 Perfetto 或其他帧源补齐。

## 8. 后续建议

1. 保留 `.jsonl.gz` 作为长期原始数据，确认可读后可以清理未压缩 `.jsonl` 以节省空间。
2. 将 `.jsonl`、`.jsonl.gz`、大体积 ftrace 文件加入 `.gitignore`，避免误提交。
3. 下一轮补充 Perfetto，对齐 `sched`、`freq`、`binder`、`surfaceflinger/frame timeline`，建立帧级 ground truth。
4. 基于 `threads_summary.csv` 进一步做关键线程评分，将 `UnityMain`、`UnityGfxDeviceW`、`surfaceflinger`、`RenderThread` 作为正样本候选。

## 9. Step1：基础部分补齐（2026-06-01 旧样本状态）

本节记录 2026-06-01 两组游戏样本的 Step1 补齐情况，属于早期旧样本状态。第 12 节已经用 2026-06-07 同步采集样本补充 Perfetto FrameTimeline、Perfetto sched/cpu frame-window 分析和 TracePilot dry-run 结果；因此当前最终口径以第 12.5 和第 12.6 节为准。

本轮在现有两组游戏样本上补充生成了 Step1 汇总产物：

| 样本 | Step1 汇总 | 关键线程评分 |
|---|---|---|
| `game_play_sgame_20260601_1200` | `game_play_sgame_20260601_1200_step1_summary.json` | `game_play_sgame_20260601_1200_threads_scored.csv` |
| `game_match_sgame_20260601_120627` | `game_match_sgame_20260601_120627_step1_summary.json` | `game_match_sgame_20260601_120627_threads_scored.csv` |

对照 Step1 的 7 项要求，当时状态如下：

| Step1 项 | 游戏场景当前状态 | 说明 |
|---|---|---|
| Perfetto FrameTimeline 采集 jank ground truth | 未完成 | 现有 `gfxinfo` 逐帧表为空，SurfaceFlinger latency 只有刷新周期，无法形成监督式 jank 标签。 |
| eBPF `sched_switch` / `sched_wakeup` | 已完成 | 对局窗口采集到 `sched_switch` 3,293,166 条、`sched_waking` 1,716,093 条、`sched_wakeup` 1,716,058 条。 |
| UID/package/session/process resolver | 已完成基础版 | metadata 确认采集前后前台均为 `com.tencent.tmgp.sgame/.SGameActivity`，并用 `comm_regex` 约束游戏、Unity、RenderThread、GPU、surfaceflinger 相关线程。 |
| frame window 内 runnable delay 聚合 | 部分完成 | 目前只能做样本级 runnable delay 聚合；对局窗口 runnable p95 为 0.664 ms，p99 为 1.995 ms。缺少帧窗口后不能归因到具体帧。 |
| UI/RenderThread 角色识别 | 已完成基础版 | 识别出 `game_main`、`game_gfx`、`android_render_thread`、`display_compositor`、`game_process` 等角色。 |
| 输出 top-k critical threads | 已完成 | 已生成 `*_threads_scored.csv` 与 `*_threads_score_summary.json`。 |
| 用户态短时 hint，带 TTL 和 rollback | 设计完成，未实际执行 | 汇总 JSON 中记录了建议策略：对 Top 线程短时施加 `uclamp_min` 或 affinity hint，TTL 2s，前台切换、温度压力或帧指标变差时 rollback。 |

对局窗口的 Top critical threads 如下：

| Rank | TID | Thread | Role | CriticalScore | on-CPU | runnable p95 |
|---:|---:|---|---|---:|---:|---:|
| 1 | 31394 | `UnityGfxDeviceW` | `game_gfx` | 100.000 | 10,231.310 ms | 1.584 ms |
| 2 | 31334 | `UnityMain` | `game_main` | 96.639 | 22,042.669 ms | 0.572 ms |
| 3 | 31215 | `cent.tmgp.sgame` | `game_process` | 84.936 | 2,047.678 ms | 1.962 ms |
| 4 | 31416 | `UnityMain` | `game_main` | 83.980 | 56.722 ms | 5.395 ms |
| 5 | 600 | `surfaceflinger` | `display_compositor` | 79.434 | 4,527.546 ms | 0.079 ms |

解释：`UnityGfxDeviceW` 在对局窗口中同时具备较高 CPU 消耗、较高迁移次数和较明显的 runnable delay，因此在归一化评分中超过 `UnityMain`，成为当前最强的调度候选目标。`cent.tmgp.sgame` 的 runnable p95 和迁移数也偏高，适合作为游戏主进程侧候选线程。

## 10. Step2：增强分析补齐（2026-06-01 旧样本状态）

本轮新增脚本 `ebpf/scripts/build_game_step_analysis.py`，用于对游戏样本生成 Step1/Step2 分析产物。对每个样本新增的 Step2 文件包括：

- `*_binder_dependency_edges.csv`
- `*_futex_wait_summary.json`
- `*_cpu_cluster_summary.csv`
- `*_cpu_cluster_threads.csv`
- `*_heuristic_policy_comparison.csv`
- `*_step2_summary.json`

### 10.1 Binder dependency graph

对局窗口从 ftrace 中重建出 52 条 Binder 依赖边。Top 边如下：

| Source | Target | Count | Matched | p95 latency |
|---|---|---:|---:|---:|
| `android.hardwar` | `InputProcessor` | 82 | 82 | 0.103 ms |
| `InputProcessor` | `android.hardwar` | 80 | 79 | 0.133 ms |
| `HwBinder:910_4` | `binder:981_A` | 23 | 23 | 0.210 ms |
| `binder:602_2` | `surfaceflinger` | 19 | 19 | 0.218 ms |
| `surfaceflinger` | `binder:602_2` | 18 | 18 | 0.212 ms |
| `UnityGfxDeviceW` | `surfaceflinger` | 13 | 0 | 0.000 ms |
| `cent.tmgp.sgame` | `surfaceflinger` | 12 | 0 | 0.000 ms |
| `cent.tmgp.sgame` | `binder:600_1` | 11 | 11 | 0.124 ms |

结论：游戏窗口中 Binder 证据主要集中在输入链路、hardware/system service、SurfaceFlinger，以及游戏渲染线程到 SurfaceFlinger 的交互。`UnityGfxDeviceW -> surfaceflinger` 和 `cent.tmgp.sgame -> surfaceflinger` 的事务可作为图形提交链路的依赖证据，但这部分在当前 ftrace 中没有全部匹配到 received 事件，因此暂不计算完整端到端等待。

### 10.2 futex wait graph

当前 ftrace 没有捕获到 `sys_enter_futex` / `sys_exit_futex` 事件，因此 futex wait graph 状态为：

```
not_available_in_captured_tracepoints
```

这不是证明游戏没有锁等待，而是说明当前内核 tracepoint / 采集配置没有给出足够证据。下一轮如果要补这一项，需要先确认设备上是否暴露 futex tracepoint，或者改用可用的 syscall/raw tracepoint 方案。

### 10.3 CPU frequency / big-little 归因

按 Pixel 6a Tensor 的 CPU 拓扑假设：CPU 0-3 为 little，CPU 4-5 为 middle，CPU 6-7 为 big。对局窗口目标线程 on-CPU 分布如下：

| Cluster | target on-CPU | freq events | avg freq | p95 freq | max freq |
|---|---:|---:|---:|---:|---:|
| little_0_3 | 5,614.381 ms | 50,789 | 1,143,351.5 kHz | 1,803,000 kHz | 2,802,000 kHz |
| middle_4_5 | 17,770.453 ms | 1,850 | 1,092,387.0 kHz | 2,253,000 kHz | 2,802,000 kHz |
| big_6_7 | 16,285.705 ms | 1,402 | 1,106,428.0 kHz | 2,252,000 kHz | 2,704,000 kHz |

线程级分布显示：

- `UnityMain`：约 22,042.669 ms，主要落在 middle/big 核，分别约 11,446.601 ms 和 10,596.068 ms。
- `UnityGfxDeviceW`：约 10,231.310 ms，也主要落在 middle/big 核。
- `surfaceflinger`：约 4,527.546 ms，其中 little 核占比较高，约 4,056.121 ms。

结论：游戏主循环和图形线程已经明显使用 middle/big 核；如果后续做 hint，更应优先考虑“短 TTL 稳定关键线程在高性能核或提高 uclamp 下限”，而不是对所有游戏线程长期加速。

### 10.4 jank cause classifier

当前状态为：

```
blocked_without_frame_ground_truth
```

原因是缺少有效帧级 ground truth，不能把调度事件标注到具体 jank frame，也不能严谨地区分 scheduler、binder、GPU/display、IO/thermal 等 cause 类别。当前只能输出 scheduler-only candidate：

| Candidate | Role | CriticalScore | runnable p95 | on-CPU |
|---|---|---:|---:|---:|
| `UnityGfxDeviceW` | `game_gfx` | 100.000 | 1.584 ms | 10,231.310 ms |
| `UnityMain` | `game_main` | 96.639 | 0.572 ms | 22,042.669 ms |
| `cent.tmgp.sgame` | `game_process` | 84.936 | 1.962 ms | 2,047.678 ms |
| `surfaceflinger` | `display_compositor` | 79.434 | 0.079 ms | 4,527.546 ms |

下一轮补 Perfetto 后，可将 classifier 从“候选线程排序”升级为“按帧窗口归因”：scheduler delay、Binder dependency、display/GPU fence、IO、thermal。

### 10.5 与启发式策略对比

对局窗口的离线候选策略对比如下：

| Policy | Top5 threads | pipeline roles | selected on-CPU | max runnable p95 |
|---|---|---:|---:|---:|
| `cpu_only` | `UnityMain, UnityGfxDeviceW, surfaceflinger, cent.tmgp.sgame, RenderThread` | 5 | 39,106.403 ms | 1.962 ms |
| `latency_only` | `UnityMain, cent.tmgp.sgame, UnityGfxDeviceW, RenderThread, UnityMain` | 5 | 34,635.579 ms | 5.395 ms |
| `pipeline_critical_score` | `UnityGfxDeviceW, UnityMain, cent.tmgp.sgame, UnityMain, surfaceflinger` | 5 | 38,905.925 ms | 5.395 ms |

结论：`cpu_only` 会稳定选到主负载线程，但不关注尾部 runnable delay；`latency_only` 容易偏向短时尾延迟线程；`pipeline_critical_score` 更符合游戏渲染链路，能同时覆盖 `UnityGfxDeviceW`、`UnityMain`、游戏主进程线程与 `surfaceflinger`。因此当前建议将 `pipeline_critical_score` 作为后续 hint target selection 的默认策略。

## 11. 2026-06-01 旧样本 Step1/Step2 完成状态

下表只描述 2026-06-01 两个早期游戏样本的状态，保留它的目的在于说明项目如何从“缺帧级 ground truth”推进到第 12 节的同步采集版本。答辩时应优先使用第 12 节的当前最终状态。

| 阶段 | 状态 | 可用于答辩的说法 |
|---|---|---|
| Step1 基础调度采集 | 已完成 | 已在真实王者荣耀对局中采集 eBPF 调度事件，并输出线程级 runnable/wakeup/on-CPU 指标。 |
| Step1 帧级 ground truth | 待补采 | 当前游戏样本缺 Perfetto FrameTimeline，不能做逐帧 jank 监督标签。 |
| Step1 top-k critical threads | 已完成 | 当前候选为 `UnityGfxDeviceW`、`UnityMain`、`cent.tmgp.sgame`、`surfaceflinger`、`RenderThread`。 |
| Step2 Binder graph | 已完成基础版 | 已从 ftrace 重建 Binder dependency edges。 |
| Step2 futex graph | 受限 | 当前 trace 未提供 futex 事件。 |
| Step2 CPU frequency / big-little | 已完成 | 已生成 cluster summary 和线程级 cluster 分布。 |
| Step2 jank cause classifier | 框架完成，等待帧标签 | 已能输出 scheduler-only candidate，补 Perfetto 后可升级为按帧 cause 分类。 |
| Step2 启发式策略对比 | 已完成 | 已比较 `cpu_only`、`latency_only` 和 `pipeline_critical_score` 三种离线候选策略。 |

## 12. 新增同步采集样本：Perfetto FrameTimeline + events.bin 离线图分析（2026-06-07）

为补齐前述“缺少帧级 ground truth”的限制，新增一轮同步采集样本：

| 项目 | 内容 |
|---|---|
| 样本 | `game_match_sgame_20260607_170754` |
| 目标包名 | `com.tencent.tmgp.sgame` |
| 前台窗口 | `com.tencent.tmgp.sgame/.SGameActivity` |
| 采集方式 | TracePilot `events.bin` + ftrace/gfxinfo/SurfaceFlinger + Perfetto FrameTimeline |
| 数据目录 | `ebpf/ebpf_data/game_sgame/game_match_sgame_20260607_170754/` |

本轮采集期间 metadata 显示开始和结束时均保持王者荣耀前台，因此目标身份以采集 metadata 为准。需要注意：TracePilot 离线分析结果中的 `target_package` 自动识别为 `com.luna.music`，这是工具内部自动检测误判；本报告不使用该字段作为目标包名证据。

### 12.1 新增产物

| 文件 | 说明 |
|---|---|
| `game_match_sgame_20260607_170754_events.bin` | TracePilot 设备端 eBPF 事件二进制流，约 1.03 GB |
| `game_match_sgame_20260607_170754.perfetto-trace` | Perfetto 原始 trace，约 134 MB |
| `game_match_sgame_20260607_170754_perfetto_frametimeline_frames.csv` | 从 Perfetto 导出的全量 FrameTimeline 帧窗口 |
| `game_match_sgame_20260607_170754_frames.txt` | 转换给 TracePilot 离线模式使用的帧文件 |
| `game_match_sgame_20260607_170754_result.json` | `events.bin + frames.txt` 离线图分析结果 |
| `game_match_sgame_20260607_170754_graph_topology.json` | 完整依赖图拓扑 |
| `game_match_sgame_20260607_170754_graph_subgraph.dot` | Top-K 子图 Graphviz DOT |
| `game_match_sgame_20260607_170754_hints.json` | TracePilot dry-run hint 输出 |
| `game_match_sgame_20260607_170754_step1_summary.json` | 修正目标包名口径后的 Step1 汇总 |
| `game_match_sgame_20260607_170754_step2_summary.json` | 修正目标包名口径后的 Step2 汇总 |

### 12.2 Step1 补齐结果

Perfetto FrameTimeline 已成功导出：

| 指标 | 数值 |
|---|---:|
| FrameTimeline 帧数 | 923 |
| deadline missed 帧数 | 2 |
| deadline missed rate | 0.22% |
| frame time 平均值 | 16.316 ms |
| frame time p95 | 16.318 ms |
| frame time p99 | 16.389 ms |

这意味着 Step1 中的 “Perfetto FrameTimeline 采集 jank ground truth” 已从待补采变为可用，但证据等级要写清楚：由于目标包名过滤没有命中 FrameTimeline 的 process name，本次导出使用了 `all_frametimeline_rows_fallback`，帧主要挂在 SurfaceFlinger 侧。结合采集期间前台窗口不变，它可以作为本轮游戏场景的帧级监督标签；但还不能说已经完成了“按游戏进程精确绑定”的 FrameTimeline 解析。

TracePilot 离线模式使用：

```text
tracepilot -i game_match_sgame_20260607_170754_events.bin \
  -f game_match_sgame_20260607_170754_frames.txt \
  -o game_match_sgame_20260607_170754_result.json \
  -G -k 10
```

输出图规模：

| 指标 | 数值 |
|---|---:|
| total frames | 923 |
| jank frames | 2 |
| total nodes | 4,115 |
| total edges | 915 |

Top-K 中与游戏/显示链路最相关的候选线程包括 `surfaceflinger`、`RenderThread`、`UnityGfxDeviceW`、`InputDispatcher` 以及 Binder/HwBinder 线程。TracePilot 给出的最高分线程是 `surfaceflinger`，这说明离线图当前更偏向显示/合成链路候选；但由于本轮 `WAKEUP`、`RUNNABLE_WAIT`、`CPU_RUN` 边均为 0，且 `target_package` 自动识别误判为 `com.luna.music`，Top-K 只能作为候选排序，不能作为严格因果结论。

为补上 TracePilot 离线图没有恢复 sched 边的问题，新增脚本 `ebpf/scripts/analyze_perfetto_sched_windows.py`，直接从 Perfetto `thread_state` 表按 FrameTimeline 窗口聚合 `Running` 和 `R/R+` 状态。新增产物包括：

| 文件 | 说明 |
|---|---|
| `game_match_sgame_20260607_170754_perfetto_frame_thread_sched.csv` | 每帧、每线程的 on-CPU / runnable wait 明细 |
| `game_match_sgame_20260607_170754_perfetto_thread_sched_summary.csv` | 过滤采集器后的线程级 Top-K 调度摘要 |
| `game_match_sgame_20260607_170754_perfetto_frame_sched_summary.csv` | 每帧调度聚合摘要 |
| `game_match_sgame_20260607_170754_perfetto_sched_summary.json` | 汇总 JSON，已被 Step1 summary 引用 |

过滤 `tracepilot` / `traced_probes` 采集器自身开销后，Perfetto sched crosscheck 的 Top 线程为：

| Rank | Thread | TID | on-CPU | runnable wait | runnable p95 |
|---:|---|---:|---:|---:|---:|
| 1 | `UnityMain` | 15498 | 3709.035 ms | 244.652 ms | 1.954 ms |
| 2 | `CoreThread` | 15577 | 3663.408 ms | 191.062 ms | 0.970 ms |
| 3 | `surfaceflinger` | 600 | 2697.533 ms | 133.722 ms | 0.233 ms |
| 4 | `NativeThread` | 15800 | 2191.632 ms | 141.387 ms | 0.656 ms |
| 5 | `kswapd0` | 92 | 1575.398 ms | 26.847 ms | 1.239 ms |

因此，Step1 的 frame window 内 runnable/on-CPU 聚合现在有 Perfetto 侧证据；仍需修的是 TracePilot `events.bin` 离线图内部没有生成 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边。

### 12.3 Step2 增强结果

TracePilot 离线图中 Step2 相关证据如下。这里的数字证明“模块跑通并产生候选图”，但 Binder/futex/frequency 与 2 个 missed frame 的因果绑定仍然偏弱：

| Step2 项 | 本轮结果 | 证据等级 |
|---|---|---|
| Binder dependency graph | `BINDER_CALL` 边 36 条，Binder call 42,250 次，总 Binder blocking 6.625 ms | 候选图可用，归因偏弱 |
| futex wait graph | `FUTEX_WAIT` 边 399 条，futex wait 528,201 次，总 futex wait 6,161.650 s | 候选图可用，尚未证明解释 missed frame |
| CPU frequency / big-little | Perfetto `cpu_frequency` 已按 FrameTimeline 窗口聚合，输出 little/middle/big 频率和线程 cluster runtime | 帧窗口级观测可用，但仍是观测归因 |
| jank cause classifier | 2 个 jank frame 均被候选分类为 `CPU_CONTENTION` | 低置信度候选，confidence=0.0 |
| 启发式策略对比 | graph AP@K = 0.2，heuristic AP@K = 0.2，Top-K overlap = 2 | 只有 2 个 missed frame，适合 smoke test |

新增脚本 `ebpf/scripts/analyze_perfetto_cpu_freq_windows.py` 从 Perfetto `cpu_frequency` counter 与 `thread_state Running` 切片中输出帧窗口级 CPU 频率 / big-little 归因：

| 文件 | 说明 |
|---|---|
| `game_match_sgame_20260607_170754_perfetto_frame_cpu_freq.csv` | 每帧、每个 CPU cluster 的 time-weighted 平均频率 |
| `game_match_sgame_20260607_170754_perfetto_thread_cpu_cluster.csv` | 每个线程落在 little/middle/big 上的 on-CPU 时间 |
| `game_match_sgame_20260607_170754_perfetto_cpu_freq_summary.json` | cluster summary 与 Top 线程 cluster runtime |

Frame window 内的 cluster 频率如下：

| Cluster | 平均频率 | jank 帧平均频率 | min | max |
|---|---:|---:|---:|---:|
| little | 1,357,043 kHz | 1,446,501 kHz | 930,000 kHz | 1,803,000 kHz |
| middle | 1,687,924 kHz | 1,997,836 kHz | 400,000 kHz | 2,253,000 kHz |
| big | 2,296,805 kHz | 2,187,589 kHz | 851,000 kHz | 2,630,000 kHz |

线程 cluster runtime 显示，`UnityMain` 和 `UnityGfxDeviceW` 在本轮帧窗口内全部运行在 middle/big cluster 上，`surfaceflinger` 主要运行在 little cluster 上：

| Thread | TID | total on-CPU | little | middle | big | middle+big 占比 |
|---|---:|---:|---:|---:|---:|---:|
| `UnityMain` | 15498 | 3709.035 ms | 0.000 ms | 2477.024 ms | 1232.011 ms | 100.00% |
| `UnityGfxDeviceW` | 15551 | 2757.563 ms | 0.000 ms | 2036.023 ms | 721.540 ms | 100.00% |
| `surfaceflinger` | 600 | 2697.533 ms | 2472.642 ms | 169.854 ms | 55.037 ms | 8.34% |

同时新增脚本 `ebpf/scripts/extract_tracepilot_enhanced_events.py`，将 `tracepilot_stdout.txt` 中的 debug ENH 行压缩成 Binder/Futex 候选摘要。该结果不提供完整阻塞时长，但能证明增强事件确实集中在游戏/显示链路线程上：

| 指标 | 数值 |
|---|---:|
| ENH BINDER_CALL 总数 | 42,250 |
| 游戏/显示相关 BINDER_CALL | 18,806 |
| ENH FUTEX_WAIT 总数 | 528,421 |
| 游戏/显示相关 FUTEX_WAIT | 316,186 |

Top 候选如下：

| Event | Thread | TID | Count |
|---|---|---:|---:|
| `FUTEX_WAIT` | `UnityMain` | 15498 | 221,670 |
| `FUTEX_WAIT` | `UnityGfxDeviceW` | 15551 | 61,861 |
| `FUTEX_WAIT` | `surfaceflinger` | 600 | 7,716 |
| `BINDER_CALL` | `InputProcessor` | 2285 | 7,312 |
| `BINDER_CALL` | `cent.tmgp.sgame` | 15364 | 2,948 |
| `BINDER_CALL` | `surfaceflinger` | 600 | 1,878 |

因此，Step2 的 Binder/futex 现在有两层证据：`result.json` / graph 给出图级候选边，`*_tracepilot_enhanced_events_summary.json` 给出 debug 事件归属候选。限制仍然是：这些候选还没有被严格绑定到两个 missed frame 的因果链上。

Jank cause classifier 当前输出为：

| Cause | Count |
|---|---:|
| `CPU_CONTENTION` | 2 |
| `BINDER_BLOCKING` | 0 |
| `FUTEX_BLOCKING` | 0 |
| `GPU_STALL` | 0 |
| `IO_WAIT` | 0 |
| `THERMAL_THROTTLE` | 0 |

解释：本轮已经具备逐帧 jank 标签，因此 classifier 不再是 `blocked_without_frame_ground_truth`。但两个 jank frame 的推理 confidence 为 0.0，说明当前 tracepilot 的因果判定仍应视为“候选解释”，不能把 `CPU_CONTENTION` 说成已严格证明的唯一原因。

### 12.4 Hint 结果与安全口径

TracePilot dry-run hint 输出 1 条建议：

| Hint | Target | TTL | Reason |
|---|---|---:|---|
| `PROTECT_UI_CHAIN` | `surfaceflinger` TID 600 | 300 ms | Protect UI compositor chain during interaction jank burst |

该 hint 尚未实际应用到设备，而且 `hints.json` 中的 package 也继承了 `com.luna.music` 的自动识别误判，因此只能作为 schema / dry-run 产物，不能直接下发。后续若做真实干预实验，应加入：

- 前台包名守卫：只在 `com.tencent.tmgp.sgame` 前台时生效；
- TTL 自动回滚；
- 温度/频率保护；
- 与 baseline 对比 frame p95、deadline missed rate、功耗/温度副作用。

### 12.5 更新后的完成状态

| 阶段 | 状态 | 证据与限制 |
|---|---|---|
| Step1 Perfetto FrameTimeline | 采集成功，SurfaceFlinger fallback 可用 | 923 帧、2 个 deadline missed；目标包过滤未命中，因此不是游戏进程精确绑定 |
| Step1 eBPF 调度采集 | raw events 采集成功 | `*_events.bin` 约 1.03 GB；本轮没有 JSONL sched 后处理，离线图中 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边为 0 |
| Step1 identity resolver | metadata 口径可用 | metadata 证明王者荣耀前台；tracepilot 自动包名误判已在 summary 中隔离 |
| Step1 frame window 聚合 | Perfetto 侧已完成，TracePilot graph 侧待修 | `*_perfetto_sched_summary.json` 已输出帧窗口内 on-CPU / runnable wait；但 `result.json` 中 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边仍为 0 |
| Step1 top-k critical threads | 候选排序可用，并有 Perfetto sched crosscheck | TracePilot Top-K 仍是候选；Perfetto 侧 Top 线程为 `UnityMain`、`CoreThread`、`surfaceflinger` 等 |
| Step1 user-space hint | schema dry-run 可用，不可直接执行 | `hints.json` 输出 1 条 TTL 300 ms hint，但 package 为误判值 `com.luna.music` |
| Step2 Binder graph | 候选图 + debug 归属可用，帧因果待修 | 36 条 Binder graph edge；debug ENH 中 42,250 次 Binder call，其中 18,806 次匹配游戏/显示相关线程 |
| Step2 futex graph | 候选图 + debug 归属可用，帧因果待修 | 399 条 futex wait edge；debug ENH 中 528,421 次 futex wait，其中 316,186 次匹配游戏/显示相关线程 |
| Step2 CPU frequency / big-little | 帧窗口级观测归因可用 | `*_perfetto_cpu_freq_summary.json` 输出每帧 cluster 频率与线程 cluster runtime；仍不是干预验证后的因果结论 |
| Step2 jank cause classifier | 低置信度候选分类 | 2 个 jank frame 均候选为 `CPU_CONTENTION`，但 confidence=0.0 且 evidence 为空 |
| Step2 启发式策略对比 | smoke test 可用 | graph/heuristic AP@K 与 Top-K overlap 已输出，但样本只有 2 个 missed frame |

### 12.6 当前最终答辩口径

当前王者荣耀场景可以表述为：**Step1/Step2 的离线分析链路已经跑通，且 2026-06-07 样本补齐了帧窗口级证据；但真实 hint 下发和严格因果证明仍未完成。**

可直接展示的完成项：

- Step1 已有 923 个 Perfetto FrameTimeline frame 和 2 个 deadline missed，可以作为本轮游戏前台窗口不变条件下的帧级标签。
- Step1 已通过 Perfetto `thread_state` 完成 frame-window 内 Running/Runnable 聚合，Top 线程包括 `UnityMain`、`CoreThread`、`surfaceflinger`、`NativeThread`。
- Step2 已生成 Binder/Futex 候选图和增强事件归属：Binder call 42,250 次，Futex wait 528,421 次。
- Step2 已完成 frame-window 级 CPU frequency / big-little 归因，`UnityMain` 和 `UnityGfxDeviceW` 在本轮帧窗口内全部运行在 middle/big cluster。
- Hint Engine 已有 dry-run schema：`PROTECT_UI_CHAIN -> surfaceflinger`，TTL 300 ms，包含 rollback 字段。

必须保守说明的限制：

- FrameTimeline 使用 `all_frametimeline_rows_fallback`，不是按游戏进程精确绑定。
- TracePilot 离线 graph 的 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边仍为 0，调度归因主要依赖 Perfetto crosscheck。
- `hints.json` 的 package 继承了 `com.luna.music` 自动误判，不能直接用于真实下发。
- Jank cause classifier 目前只是低置信度候选，2 个 jank frame 均为 `CPU_CONTENTION` 且 confidence=0.0。
- 尚未做 baseline vs intervention 的真实干预效果对比。

### 12.7 提交数据说明

为避免将几个 GB 的原始散文件直接提交，本轮将 raw/replay 输入打包为单个归档，并用 manifest 记录内容和 SHA256：

| 文件 | 说明 |
|---|---|
| `ebpf/ebpf_data/game_sgame/raw_packages/game_match_sgame_20260607_170754_raw_replay_package.zip` | 原始 replay 包，233,540,901 bytes，SHA256=`44e3a3ed24f7c352e0bbdf5cf55d042dbccf293503edff216d8f044ca538f2a6` |
| `ebpf/ebpf_data/game_sgame/raw_packages/game_match_sgame_20260607_170754_raw_replay_manifest.json` | 归档 manifest，包含每个原始文件的 role、size、SHA256、重放命令和已知限制 |
| `ebpf/ebpf_data/game_sgame/SUBMISSION.md` | 待提交清单与 raw package 使用说明 |

归档中包含 `events.bin`、`.perfetto-trace`、`tracepilot_stdout.txt`、`frames.txt`、Perfetto 配置和少量原始 dumpsys/ftrace 侧文件。仓库中不应以散文件形式提交：

- `*_events.bin`
- `*.perfetto-trace`
- `*_tracepilot_stdout.txt`

这些模式已写入 `.gitignore`。分析产物如 `*_perfetto_sched_summary.json`、`*_perfetto_cpu_freq_summary.json`、`*_tracepilot_enhanced_events_summary.json`、`*_step1_summary.json`、`*_step2_summary.json` 和对应 CSV 可以作为轻量可审计数据提交。

如果提交平台不接受约 233 MB 的 zip，可将 zip 放到外部 artifact/网盘/Release，仓库只提交 manifest、分析产物、脚本和报告。
