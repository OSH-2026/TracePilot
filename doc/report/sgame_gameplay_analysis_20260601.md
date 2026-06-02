# 王者荣耀游戏场景 eBPF 采集分析记录

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
