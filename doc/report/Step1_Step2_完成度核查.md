# TracePilot Step1/Step2 完成度核查

本文按 `参考_总结..pdf` 中的实施路线核查两个当前重点场景：

- 信息流滚动：`feed_scroll`
- 游戏对局：`game_sgame`

状态口径：

- **已完成**：已有可审计文件产物，能支撑答辩展示。
- **部分完成**：模块已跑通或有代理证据，但证据粒度、因果归因或目标绑定仍需保守表述。
- **待真机**：必须重新连接手机采集、确认设备能力或实际执行干预。

## 一、总览结论

| 场景 | Step1 总体状态 | Step2 总体状态 | 答辩口径 |
|---|---|---|---|
| `feed_scroll` 信息流滚动 | 基本完成 | 基本完成 | 已完成 eBPF 调度采集、早期 SurfaceFlinger 代理帧窗口、2026-07-01 Chrome package-filtered Perfetto FrameTimeline、Perfetto sched/cpu frame-window 分析、TracePilot replay、dry-run hint 和 baseline/intervention 初跑；真实干预闭环已跑通，但效果 mixed，不能声称稳定改善。 |
| `game_sgame` 王者荣耀 | 部分完成 | 部分完成 | 已完成 2026-06-07 同步采集后的 FrameTimeline fallback、帧窗口级 Perfetto sched/cpu 交叉分析、Binder/Futex 候选图和 dry-run hint；2026-07-01 smoke 验证已确认 `com.tencent.tmgp.sgame` 前台包名/UID/PID guard 可用，但游戏未更新导致本轮不作为正式游戏性能场景，后续不再硬采。 |

一句话版本：**两条场景的离线分析链路已经跑通；feed_scroll 已有真实干预初跑但效果 mixed，game_sgame 只保留为 resolver/guard smoke，不再硬采正式游戏场景。**

## 二、Step1 基础管线逐项核查

参考 Step1 子项：

1. Perfetto FrameTimeline 采集 jank ground truth
2. eBPF 采集 `sched_switch` / `sched_wakeup`
3. UID/package/session/process resolver
4. frame window 内 runnable delay 聚合
5. UI/RenderThread 角色识别
6. 输出 top-k critical threads
7. 用户态临时 hint，带 TTL 和 rollback

### 2.1 `feed_scroll` 信息流滚动

| Step1 子项 | 状态 | 当前证据 | 答辩口径 / 待补 |
|---|---|---|---|
| Perfetto FrameTimeline ground truth | 已完成 | 早期 `feed_scroll_step2_aligned_20260527_frame_summary.json` 提供 126 个 `ChromeChildSurface` 代理 interval；2026-07-01 `feed_scroll_live_20260701_134759_perfetto_frametimeline_summary.json` 已通过 `source_filter=package_filter` 命中 `com.android.chrome`，1768 帧、24 个 deadline missed、missed rate 1.36%。 | 当前最终口径应优先使用 2026-07-01 Chrome package-filtered FrameTimeline；早期 SurfaceFlinger interval 作为设备/场景限制下的补充代理证据保留。 |
| eBPF `sched_switch` / `sched_wakeup` | 已完成 | `feed_scroll_step2_aligned_20260527_summary.json` 和报告记录：4,328,470 条 eBPF 原始事件，`sched_switch` 2,106,384，`sched_waking/wakeup` 各 1,084,486。 | 基础调度采集完成，能支撑线程级 on-CPU、wakeup-to-run、runnable delay 统计。 |
| UID/package/session/process resolver | 基本完成 | 2026-07-01 metadata 证明采集开始和结束均为 `com.android.chrome/com.google.android.apps.chrome.Main`；FrameTimeline package filter 命中 `com.android.chrome`；Perfetto sched 输出保留 Chrome 主进程、privileged process、sandboxed renderer process 的 pid/uid/process_name。 | 场景级和进程/线程级身份口径已经足够支撑 Chrome 滚动答辩；更细的 session instance 模型仍可作为工程增强。 |
| frame window runnable delay 聚合 | 已完成 | 2026-07-01 `feed_scroll_live_20260701_134759_perfetto_sched_summary.json`：1768 个 FrameTimeline 窗口、103343 条 frame-thread 聚合记录，Top 线程含 `surfaceflinger`、`VizCompositorTh`、`Compositor`、`CompositorGpuTh`、`CrRendererMain`。 | Perfetto `thread_state` 已按 Chrome FrameTimeline 窗口聚合 Running/Runnable；TracePilot graph 内 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边仍为 0，调度证据优先引用 Perfetto crosscheck。 |
| UI/RenderThread 角色识别 | 已完成 | `*_threads_classified.csv` 和 `*_threads_score_summary.json` 将 `Compositor`、`VizCompositorTh`、`CompositorGpuTh`、`CrRendererMain` 等识别为渲染链路关键线程。 | Chrome 渲染/合成链路角色识别完成基础版。 |
| top-k critical threads | 已完成 | `feed_scroll_step2_aligned_20260527_threads_score_summary.json` Top threads：`Compositor`、`VizCompositorTh`、`CompositorGpuTh`、`CrRendererMain`、`.android.chrome`。 | 已能输出候选关键线程排序。 |
| 用户态临时 hint | 部分完成 | 2026-07-01 `feed_scroll_live_20260701_134759_hints.json` 输出 dry-run `PROTECT_UI_CHAIN -> surfaceflinger`，TTL 300 ms，rollback 为 restore affinity；`feed_scroll_intervention_20260701_222452` 已完成 3 baseline + 3 intervention 初跑。 | schema、TTL、rollback、目标选择和真实 actuator audit 已跑通；但初跑结果 mixed，不能声称 hint 稳定改善性能。 |

### 2.2 `game_sgame` 王者荣耀

| Step1 子项 | 状态 | 当前证据 | 答辩口径 / 待补 |
|---|---|---|---|
| Perfetto FrameTimeline ground truth | 部分完成 | `game_match_sgame_20260607_170754_perfetto_frametimeline_summary.json`：923 帧、2 个 deadline missed、deadline missed rate 0.22%。 | FrameTimeline 已可用，但采用 `all_frametimeline_rows_fallback`，主要挂在 SurfaceFlinger 侧；可作为本轮游戏前台不变条件下的监督标签，不能说已按游戏进程精确绑定。 |
| eBPF `sched_switch` / `sched_wakeup` | 已完成 | 2026-06-01 对局窗口已有 JSONL 调度统计；2026-06-07 样本有 `events.bin` raw replay 输入和 `result.json`。 | 基础 eBPF 调度采集完成；但 2026-06-07 TracePilot graph 未恢复 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边，需用 Perfetto sched 交叉补证。 |
| UID/package/session/process resolver | 部分完成 | `*_host_metadata.json` 和报告确认采集前后前台均为 `com.tencent.tmgp.sgame/.SGameActivity`；`step1_summary.json` 隔离了 TracePilot 自动误判的 `com.luna.music`；2026-07-01 两轮 smoke 均确认前台包名、UID 和 PID 为 `com.tencent.tmgp.sgame`。 | metadata 与 smoke 口径可用于答辩；本轮只证明 guard/resolver 可用，不作为正式游戏性能场景。 |
| frame window runnable delay 聚合 | 部分完成 | `game_match_sgame_20260607_170754_perfetto_sched_summary.json`、`*_perfetto_frame_thread_sched.csv`、`*_perfetto_frame_sched_summary.csv`。 | Perfetto `thread_state` 已按 FrameTimeline 窗口聚合 Running/Runnable；TracePilot graph 内 sched 边仍待修。 |
| UI/RenderThread 角色识别 | 已完成 | 报告识别 `UnityMain`、`UnityGfxDeviceW`、`RenderThread`、`surfaceflinger`、`InputDispatcher` 等游戏/显示链路线程。 | 游戏主循环、图形线程和显示合成链路角色识别完成基础版。 |
| top-k critical threads | 已完成 | Perfetto sched crosscheck Top threads：`UnityMain`、`CoreThread`、`surfaceflinger`、`NativeThread`、`kswapd0`；增强事件候选包括 `UnityGfxDeviceW`。 | 已能输出候选关键线程，并有 Perfetto sched 交叉证据。 |
| 用户态临时 hint | 部分完成 | `game_match_sgame_20260607_170754_hints.json` dry-run 产物：`PROTECT_UI_CHAIN` -> `surfaceflinger`，TTL 300 ms，但 package 误判为 `com.luna.music`；2026-07-01 smoke 已验证 `-p com.tencent.tmgp.sgame` 显式 guard 可用。 | hint schema/TTL/rollback dry-run 可展示；由于游戏未更新，本轮不继续做正式 SGame hint 下发，只保留 guard smoke 证据。 |

## 三、Step2 增强逐项核查

参考 Step2 子项：

1. Binder dependency graph
2. futex wait graph
3. CPU frequency / big-little 分析
4. jank cause classifier
5. 干预模式对比实验

### 3.1 `feed_scroll` 信息流滚动

| Step2 子项 | 状态 | 当前证据 | 答辩口径 / 待补 |
|---|---|---|---|
| Binder dependency graph | 已完成 | `feed_scroll_step2_aligned_20260527_binder_dependency_edges.csv`：32 条 Binder 依赖边；主要路径 `VizCompositorTh / CompositorGpuTh -> surfaceflinger`。 | 已生成滚动合成链路 Binder 依赖边表。 |
| futex wait graph | 部分完成 | 2026-07-01 TracePilot offline replay 已生成 `FUTEX_WAIT=692` 候选边；同时设备侧标准 ftrace futex tracepoint 仍不可用。 | TracePilot 增强事件能给出 futex 候选图；标准 ftrace futex 能力仍是设备观测限制。 |
| CPU frequency / big-little | 已完成 | 2026-07-01 `feed_scroll_live_20260701_134759_perfetto_cpu_freq_summary.json`：big cluster 平均 2,719,291 kHz，jank 窗口 big 平均 2,798,992 kHz；`VizCompositorTh`、`CompositorGpuTh`、`Compositor`、`CrRendererMain` 的 middle+big 占比均较高。 | 已完成 Chrome FrameTimeline 窗口级 CPU frequency / big-little 观测。 |
| jank cause classifier | 部分完成 | 2026-07-01 TracePilot replay 对 24 个 jank frame 均给出 `CPU_CONTENTION` 候选；Perfetto sched/cpu 提供 frame-window 交叉证据。 | classifier 已能在真实 Chrome FrameTimeline 上运行，但仍是候选归因，不能替代 baseline/intervention 因果验证。 |
| 干预模式对比实验 | 部分完成 | 早期已有离线候选策略对比；2026-07-01 已完成 3 baseline + 3 intervention 初跑。baseline 平均 missed rate 0.22%，intervention 0.18%；但 p95/p99 从 3.735/4.780 ms 上升到 4.244/5.373 ms。 | 真实采集闭环已跑通，但效果 mixed；只能写作初跑和限制，不应写成性能改善结论。 |

### 3.2 `game_sgame` 王者荣耀

| Step2 子项 | 状态 | 当前证据 | 答辩口径 / 待补 |
|---|---|---|---|
| Binder dependency graph | 已完成 | `game_match_sgame_20260607_170754_step2_summary.json`：36 条 Binder graph edge，42,250 次 Binder call；debug ENH 中 18,806 次匹配游戏/显示相关线程。 | Binder 候选图和事件归属已可展示；与 2 个 missed frame 的严格因果绑定仍需保守。 |
| futex wait graph | 已完成 | `step2_summary.json`：399 条 Futex wait edge，528,421 次 Futex wait；debug ENH 中 316,186 次匹配游戏/显示相关线程。 | Futex 候选图可用；还不能证明这些等待解释了全部 missed frame。 |
| CPU frequency / big-little | 已完成 | `game_match_sgame_20260607_170754_perfetto_cpu_freq_summary.json`：Frame window 级 cluster 频率与线程 cluster runtime；`UnityMain`、`UnityGfxDeviceW` 均 100% 在 middle/big 运行。 | 帧窗口级 CPU 频率/大小核观测可用，但不是干预后的因果证明。 |
| jank cause classifier | 部分完成 | `step2_summary.json`：2 个 jank frame 候选分类为 `CPU_CONTENTION`，confidence=0.0。 | classifier 已跑通，但只能称为低置信度候选解释。 |
| 干预模式对比实验 | 部分完成 | `step2_summary.json`：graph AP@K = 0.2，heuristic AP@K = 0.2，Top-K overlap = 2；2026-07-01 两轮 smoke 确认 `com.tencent.tmgp.sgame` guard ready。 | 只有 2 个 missed frame，适合作为 smoke test；由于游戏未更新/场景不理想，停止继续硬采正式游戏干预。 |

## 四、答辩关键数字摘要

| 场景 | 可直接使用的数字 |
|---|---|
| `game_sgame` | 923 个 FrameTimeline frame；2 个 deadline missed；Binder call 42,250；Futex wait 528,421；Top 线程候选包括 `UnityMain`、`UnityGfxDeviceW`、`surfaceflinger`；2026-07-01 smoke 两轮均确认前台包名/UID/PID 为 `com.tencent.tmgp.sgame`，但不作为正式游戏性能场景。 |
| `feed_scroll` | 2026-07-01 Chrome package-filtered FrameTimeline：1768 帧、24 个 deadline missed、missed rate 1.36%、p95/p99 frame time 4.069/16.815 ms；Perfetto sched Top 线程包括 `surfaceflinger`、`VizCompositorTh`、`Compositor`、`CompositorGpuTh`、`CrRendererMain`；TracePilot replay 输出 `BINDER_CALL=24`、`FUTEX_WAIT=692`、`FRAME_DEPENDENCY=695` 和 dry-run `PROTECT_UI_CHAIN` hint；真实 baseline/intervention 初跑为 mixed。 |

## 五、不连手机可复现实验清单

| 脚本 | 输入 | 输出 | 当前离线复现状态 |
|---|---|---|---|
| `ebpf/scripts/parse_surfaceflinger_latency.py` | `feed_scroll_step2_aligned_20260527_surfaceflinger_latency.txt` | `feed_scroll_step2_aligned_20260527_frames.csv`、`*_frame_summary.json` | 可直接用已提交 feed_scroll 原始 SurfaceFlinger latency 文件重跑。 |
| `ebpf/scripts/analyze_perfetto_sched_windows.py` | `.perfetto-trace` + `*_perfetto_frametimeline_frames.csv` | `*_perfetto_frame_thread_sched.csv`、`*_perfetto_thread_sched_summary.csv`、`*_perfetto_frame_sched_summary.csv`、`*_perfetto_sched_summary.json` | 当前仓库保留了输出产物；重跑需要从 raw replay package 解出 `.perfetto-trace`。 |
| `ebpf/scripts/analyze_perfetto_cpu_freq_windows.py` | `.perfetto-trace` + `*_perfetto_frametimeline_frames.csv` | `*_perfetto_frame_cpu_freq.csv`、`*_perfetto_thread_cpu_cluster.csv`、`*_perfetto_cpu_freq_summary.json` | 当前仓库保留了输出产物；重跑需要从 raw replay package 解出 `.perfetto-trace`。 |
| `ebpf/scripts/extract_tracepilot_enhanced_events.py` | `*_tracepilot_stdout.txt` | `*_tracepilot_enhanced_events.csv`、`*_tracepilot_enhanced_events_summary.json` | 当前仓库保留了输出产物；重跑需要 raw replay package 内的 `tracepilot_stdout.txt`。 |
| `ebpf/scripts/build_tracepilot_offline_step_summary.py` | 2026-06-07 game dataset directory，需含 metadata、FrameTimeline summary、TracePilot result、hints、Perfetto sched/cpu/enhanced summaries | `*_step1_summary.json`、`*_step2_summary.json` | 可在 raw replay/分析产物齐全时离线重建 Step1/Step2 汇总，不需要重新打游戏。 |
| `ebpf/scripts/run_feed_scroll_intervention_experiment.py` | 已连接 Pixel 6a、Chrome 前台、`/private/tmp/trace_processor` | `feed_scroll_intervention_<timestamp>/experiment_manifest.json`、`experiment_summary.json`、每轮 Perfetto sched/cpu summary 和 intervention audit | 用于执行 3 baseline + 3 intervention 的 feed_scroll 真实对比；intervention 采用可回滚的 Chrome 渲染线程 priority/cpuset guard，并记录 actuator 成功率。 |
| `ebpf/scripts/validate_sgame_resolver_guard.py` | 已完成 2026-07-01 两轮 smoke；如需复核才连接 Pixel 6a 与王者荣耀 | `sgame_resolver_guard_report.json`、可选短采样目录和 FrameTimeline summary | 只作为 `com.tencent.tmgp.sgame` 前台包名守卫和 PID/UID resolver 复核模板；当前不再作为正式游戏场景采集任务。 |

示例命令口径：

```bash
python3 ebpf/scripts/parse_surfaceflinger_latency.py \
  ebpf/ebpf_data/feed_scroll/feed_scroll_step2_aligned_20260527_surfaceflinger_latency.txt \
  --csv-out ebpf/ebpf_data/feed_scroll/feed_scroll_step2_aligned_20260527_frames.csv \
  --summary-out ebpf/ebpf_data/feed_scroll/feed_scroll_step2_aligned_20260527_frame_summary.json \
  --layer 'com.android.chrome/ChromeChildSurface'
```

```bash
python3 ebpf/scripts/build_tracepilot_offline_step_summary.py \
  ebpf/ebpf_data/game_sgame/game_match_sgame_20260607_170754 \
  --tag game_match_sgame_20260607_170754 \
  --package com.tencent.tmgp.sgame
```

```bash
python3 ebpf/scripts/run_feed_scroll_intervention_experiment.py \
  --adb adb \
  --duration 20 \
  --repetitions 3 \
  --target-nice -10 \
  --trace-processor /private/tmp/trace_processor
```

SGame 命令只保留为 guard 复核模板，不作为当前下一步必跑项：

```bash
python3 ebpf/scripts/validate_sgame_resolver_guard.py \
  --adb adb \
  --package com.tencent.tmgp.sgame \
  --launch \
  --capture \
  --duration 10 \
  --trace-processor /private/tmp/trace_processor
```

## 六、必须连手机才能完成的边界

当前不再把 SGame 正式游戏干预列为必做项；以下边界主要面向 feed_scroll 复测或未来需要重新打开游戏实验时使用。

| 必须连手机的事项 | 原因 |
|---|---|
| 真正下发 `uclamp` / affinity / priority hint | 需要实时目标 TID、root 权限、前台包名守卫和 TTL rollback；2026-07-01 设备探测显示 SurfaceFlinger 的 `sched_boost`/`uclamp` 文件不存在，`taskset`/`chrt` 对系统线程被拒绝，因此 feed_scroll 真实干预优先采用可回滚的 Chrome 渲染线程 priority/cpuset actuator，并记录 actuator 成功率。 |
| baseline vs intervention 对比 | 必须在同一设备、同一场景、相近温度/电量下重复采集，才能比较 frame p95、deadline missed rate、温度和副作用。 |
| 新一轮 Perfetto FrameTimeline 精确包名绑定 | 需要现场调整 Perfetto/TraceProcessor 过滤口径，确认 FrameTimeline row 能精确绑定到目标 app/process。 |
| 温度/功耗副作用验证 | 需要设备实时 thermal、frequency、电量或功耗代理信号。 |
| futex tracepoint 能力确认 | 需要在设备上检查 `available_events`、kprobe/raw syscall 可用性和权限状态。 |

## 七、六个补充任务完成性核查

| 用户要求 | 是否已补充 | 位置 |
|---|---|---|
| Step1/Step2 完成度总表 | 已补充 | 本文第二、三节 |
| 统一 `sgame` 旧状态 | 已补充 | `sgame_gameplay_analysis_report.md` 中 2026-06-01 旧样本状态和 2026-06-07 当前最终状态已区分 |
| `feed_scroll` 限制写成工程成果边界 | 已补充 | `feed_scroll_analysis_report.md` 的 Step2 章节和本文 3.1 |
| 不连手机可复现实验清单 | 已补充 | 本文第五节 |
| 答辩关键数字摘要 | 已补充 | 本文第四节 |
| 必须连手机的边界说明 | 已补充 | 本文第六节 |
