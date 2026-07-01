# feed_scroll_live_20260701_134759 采集摘要

## 采集口径

- 场景：Chrome 信息流/网页滚动
- 包名：`com.android.chrome`
- 设备：Pixel 6a，Android 16，root 可用
- 采集时长：40 s
- 操作方式：ADB 自动滚动，约 32 次 `input swipe`
- 前台校验：采集开始和结束均为 `com.android.chrome/com.google.android.apps.chrome.Main`
- 采集内容：TracePilot `events.bin`、ftrace、gfxinfo、SurfaceFlinger layer/latency、Perfetto trace

## 主要产物

| 文件 | 说明 |
|---|---|
| `feed_scroll_live_20260701_134759_events.bin` | TracePilot 原始事件流，449 MB |
| `feed_scroll_live_20260701_134759.perfetto-trace` | Perfetto 原始 trace，122 MB |
| `feed_scroll_live_20260701_134759_perfetto_frametimeline_frames.csv` | Perfetto FrameTimeline 帧窗口 |
| `feed_scroll_live_20260701_134759_perfetto_sched_summary.json` | 帧窗口内 Running/Runnable 聚合摘要 |
| `feed_scroll_live_20260701_134759_perfetto_cpu_freq_summary.json` | 帧窗口内 CPU frequency / big-little 摘要 |
| `feed_scroll_live_20260701_134759_result.json` | TracePilot offline graph 结果 |
| `feed_scroll_live_20260701_134759_hints.json` | TracePilot dry-run hint 输出 |
| `feed_scroll_live_20260701_134759_ftrace_quick_summary.json` | ftrace 事件快速摘要 |

## 原始数据压缩包

本轮大文件属于原始/可重放数据：

- `feed_scroll_live_20260701_134759_events.bin`：TracePilot 原始事件流
- `feed_scroll_live_20260701_134759.perfetto-trace`：Perfetto 原始 trace

已将原始 replay 输入打包到：

| 文件 | 说明 |
|---|---|
| `../raw_packages/feed_scroll_live_20260701_134759_raw_replay_package.zip` | 原始数据压缩包，73,516,488 bytes |
| `../raw_packages/feed_scroll_live_20260701_134759_raw_replay_manifest.json` | 文件角色、大小和 SHA256 校验清单 |

上传策略建议：优先上传 zip；若平台限制单文件大小，则将 zip 放到外部 artifact/网盘，仓库只保留 manifest、README 和轻量分析产物。

## 关键结果

### FrameTimeline

- `source_filter = package_filter`，本轮成功按 `com.android.chrome` 命中 FrameTimeline，不再依赖 SurfaceFlinger fallback。
- Frame count：1768
- Deadline missed：24
- Deadline missed rate：1.36%
- Frame time avg / p50 / p95 / p99：3.215 / 2.930 / 4.069 / 16.815 ms

### Perfetto sched frame-window 聚合

Top 线程候选：

| Rank | Thread | on-CPU ms | Runnable wait ms | Runnable wait p95 ms |
|---:|---|---:|---:|---:|
| 1 | `surfaceflinger` | 5596.666 | 294.082 | 0.313 |
| 2 | `VizCompositorTh` | 2408.481 | 730.452 | 0.974 |
| 3 | `Compositor` | 2173.516 | 849.417 | 0.901 |
| 4 | `CompositorGpuTh` | 2273.160 | 357.278 | 0.468 |
| 5 | `CrRendererMain` | 1625.172 | 527.460 | 0.846 |
| 6 | `.android.chrome` | 1754.590 | 390.000 | 0.582 |

### CPU frequency / big-little

| Cluster | Avg freq kHz | Jank avg freq kHz | Max freq kHz |
|---|---:|---:|---:|
| big | 2,719,291.0 | 2,798,992.2 | 2,802,000 |
| middle | 1,192,249.9 | 1,192,511.4 | 2,253,000 |
| little | 1,484,302.1 | 1,558,555.7 | 1,803,000 |

Chrome 渲染/合成线程主要使用 middle/big cluster：

- `VizCompositorTh`：middle+big 占比 90.93%
- `CompositorGpuTh`：middle+big 占比 98.97%
- `Compositor`：middle+big 占比 98.22%
- `CrRendererMain`：middle+big 占比 88.25%

### ftrace 快速摘要

- ftrace event lines：2074
- `sched_switch`：293
- `sched_waking` / `sched_wakeup`：183 / 181
- `binder_wait_for_work`：34
- `binder_transaction` / `binder_transaction_received`：18 / 15
- `dma_fence_wait_start/end`：2 / 2
- `thermal_temperature`：1

Binder 相关任务主要包括 `CompositorGpuTh`、`VizCompositorTh`、`surfaceflinger`、`binder:606_2`、`binder:609_2`。

### TracePilot offline replay

- `target_package = com.android.chrome`
- Total frames：1768
- Jank frames：24
- Total nodes / edges：4270 / 1419
- Edge distribution：`BINDER_CALL=24`、`FUTEX_WAIT=692`、`FRAME_DEPENDENCY=695`、`BUFFER_QUEUE=2`、`NETWORK_WAIT=6`
- Jank cause candidate：24 个 frame 均候选为 `CPU_CONTENTION`
- Heuristic comparison：graph AP@K = 0.1，heuristic AP@K = 0.1，Top-K overlap = 1

Dry-run hint：

```json
{
  "type": "PROTECT_UI_CHAIN",
  "target": {
    "tid": 606,
    "pid": 606,
    "comm": "surfaceflinger",
    "package": "com.android.chrome"
  },
  "ttl_ms": 300,
  "rollback": {
    "action": "restore_affinity",
    "mask": "all"
  }
}
```

## 限制说明

- 本轮没有执行 `--hint-apply`，因此没有真实调度干预，也不能声称性能改善。
- `gfxinfo` 仍只报告 1 frame，Chrome 网页内容的主证据应使用 Perfetto FrameTimeline。
- SurfaceFlinger latency 文件只有 9 B，本轮不使用 SurfaceFlinger interval 作为主帧证据。
- TracePilot offline graph 中 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边仍为 0；调度侧证据应优先引用 Perfetto sched frame-window 聚合。
