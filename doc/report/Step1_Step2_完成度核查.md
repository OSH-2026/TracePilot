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
| `feed_scroll` 信息流滚动 | 部分完成 | 部分完成 | 已完成 eBPF 调度采集、SurfaceFlinger 代理帧窗口、Binder/CPU 频率/策略选择离线分析；完整 Perfetto FrameTimeline 标签、futex 真实事件与实际 hint 干预仍需真机补强。 |
| `game_sgame` 王者荣耀 | 部分完成 | 部分完成 | 已完成 2026-06-07 同步采集后的 FrameTimeline fallback、帧窗口级 Perfetto sched/cpu 交叉分析、Binder/Futex 候选图和 dry-run hint；但游戏进程精确 FrameTimeline 绑定、TracePilot graph 内 sched 边恢复和真实干预对比仍需真机或 raw replay 修正。 |

一句话版本：**两条场景的离线分析链路已经跑通；现在缺的不是“有没有分析”，而是真实 hint 下发与更强因果验证。**

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
| Perfetto FrameTimeline ground truth | 部分完成 | `feed_scroll_step2_aligned_20260527_frame_summary.json`：126 个 `ChromeChildSurface` SurfaceFlinger interval，2 个异常长间隔候选。 | 网页滚动绘制提交到独立合成 surface，`gfxinfo` 主进程仅 1 frame，因此当前使用 SurfaceFlinger interval 作为可用代理帧证据；完整 app FrameTimeline 标签待真机补采。 |
| eBPF `sched_switch` / `sched_wakeup` | 已完成 | `feed_scroll_step2_aligned_20260527_summary.json` 和报告记录：4,328,470 条 eBPF 原始事件，`sched_switch` 2,106,384，`sched_waking/wakeup` 各 1,084,486。 | 基础调度采集完成，能支撑线程级 on-CPU、wakeup-to-run、runnable delay 统计。 |
| UID/package/session/process resolver | 部分完成 | 通过 Chrome 线程名、`ChromeChildSurface` layer、Chrome/渲染/system 线程集合建立场景身份。 | 已有场景级和线程角色级 resolver；还不是完整 UID/session/process instance 层次模型。 |
| frame window runnable delay 聚合 | 部分完成 | `feed_scroll_step2_aligned_20260527_frame_dependency_join.csv` 对 126 个呈现窗口聚合 Binder/display/block I/O；线程级 runnable delay 已在 `*_threads_summary.csv`。 | 已有帧窗口代理 join 与线程级调度指标，但还缺完整 Perfetto FrameTimeline 下的逐帧 runnable delay/on-CPU 明细。 |
| UI/RenderThread 角色识别 | 已完成 | `*_threads_classified.csv` 和 `*_threads_score_summary.json` 将 `Compositor`、`VizCompositorTh`、`CompositorGpuTh`、`CrRendererMain` 等识别为渲染链路关键线程。 | Chrome 渲染/合成链路角色识别完成基础版。 |
| top-k critical threads | 已完成 | `feed_scroll_step2_aligned_20260527_threads_score_summary.json` Top threads：`Compositor`、`VizCompositorTh`、`CompositorGpuTh`、`CrRendererMain`、`.android.chrome`。 | 已能输出候选关键线程排序。 |
| 用户态临时 hint | 待真机 | `feed_scroll_step2_aligned_20260527_heuristic_policy_comparison.csv` 仅做 `cpu_only` / `latency_only` / `pipeline_critical_score` 离线目标选择对比。 | 目标选择逻辑已完成；TTL/rollback 的真实下发和效果验证必须连手机。 |

### 2.2 `game_sgame` 王者荣耀

| Step1 子项 | 状态 | 当前证据 | 答辩口径 / 待补 |
|---|---|---|---|
| Perfetto FrameTimeline ground truth | 部分完成 | `game_match_sgame_20260607_170754_perfetto_frametimeline_summary.json`：923 帧、2 个 deadline missed、deadline missed rate 0.22%。 | FrameTimeline 已可用，但采用 `all_frametimeline_rows_fallback`，主要挂在 SurfaceFlinger 侧；可作为本轮游戏前台不变条件下的监督标签，不能说已按游戏进程精确绑定。 |
| eBPF `sched_switch` / `sched_wakeup` | 已完成 | 2026-06-01 对局窗口已有 JSONL 调度统计；2026-06-07 样本有 `events.bin` raw replay 输入和 `result.json`。 | 基础 eBPF 调度采集完成；但 2026-06-07 TracePilot graph 未恢复 `WAKEUP/RUNNABLE_WAIT/CPU_RUN` 边，需用 Perfetto sched 交叉补证。 |
| UID/package/session/process resolver | 部分完成 | `*_host_metadata.json` 和报告确认采集前后前台均为 `com.tencent.tmgp.sgame/.SGameActivity`；`step1_summary.json` 隔离了 TracePilot 自动误判的 `com.luna.music`。 | metadata 口径可用于答辩；TracePilot 内部 target package 误判需在后续 replay 或真机中修正。 |
| frame window runnable delay 聚合 | 部分完成 | `game_match_sgame_20260607_170754_perfetto_sched_summary.json`、`*_perfetto_frame_thread_sched.csv`、`*_perfetto_frame_sched_summary.csv`。 | Perfetto `thread_state` 已按 FrameTimeline 窗口聚合 Running/Runnable；TracePilot graph 内 sched 边仍待修。 |
| UI/RenderThread 角色识别 | 已完成 | 报告识别 `UnityMain`、`UnityGfxDeviceW`、`RenderThread`、`surfaceflinger`、`InputDispatcher` 等游戏/显示链路线程。 | 游戏主循环、图形线程和显示合成链路角色识别完成基础版。 |
| top-k critical threads | 已完成 | Perfetto sched crosscheck Top threads：`UnityMain`、`CoreThread`、`surfaceflinger`、`NativeThread`、`kswapd0`；增强事件候选包括 `UnityGfxDeviceW`。 | 已能输出候选关键线程，并有 Perfetto sched 交叉证据。 |
| 用户态临时 hint | 待真机 | `game_match_sgame_20260607_170754_hints.json` dry-run 产物：`PROTECT_UI_CHAIN` -> `surfaceflinger`，TTL 300 ms，但 package 误判为 `com.luna.music`。 | hint schema/TTL/rollback dry-run 可展示；真实下发前必须修正前台包名守卫并连手机执行。 |

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
| futex wait graph | 待真机 | 报告 9.3：当前 Pixel 6a `available_events` 无标准 futex wait/wake tracepoint，动态 kprobe 不可写。 | 这是设备观测能力限制，不是分析遗漏；需要连手机确认 futex tracepoint/kprobe/raw syscall 方案。 |
| CPU frequency / big-little | 已完成 | `feed_scroll_step2_aligned_20260527_cpu_cluster_summary.csv`：big cluster 目标 on-CPU 最高，6,715.341 ms。 | 已完成三档 cluster 的 on-CPU 与频率统计。 |
| jank cause classifier | 部分完成 | `feed_scroll_step2_aligned_20260527_frame_dependency_join.csv`：126 个代理帧窗口，95 个 `binder_dependency`，31 个 `scheduler_or_render_work`。 | 这是基于 SurfaceFlinger interval 的规则分类，可用于说明共现证据；完整 FrameTimeline 因果分类待真机补采。 |
| 干预模式对比实验 | 部分完成 | `feed_scroll_step2_aligned_20260527_heuristic_policy_comparison.csv` 比较 `cpu_only`、`latency_only`、`pipeline_critical_score`。 | 已完成离线候选目标选择对比；真实 baseline/intervention 效果对比必须连手机。 |

### 3.2 `game_sgame` 王者荣耀

| Step2 子项 | 状态 | 当前证据 | 答辩口径 / 待补 |
|---|---|---|---|
| Binder dependency graph | 已完成 | `game_match_sgame_20260607_170754_step2_summary.json`：36 条 Binder graph edge，42,250 次 Binder call；debug ENH 中 18,806 次匹配游戏/显示相关线程。 | Binder 候选图和事件归属已可展示；与 2 个 missed frame 的严格因果绑定仍需保守。 |
| futex wait graph | 已完成 | `step2_summary.json`：399 条 Futex wait edge，528,421 次 Futex wait；debug ENH 中 316,186 次匹配游戏/显示相关线程。 | Futex 候选图可用；还不能证明这些等待解释了全部 missed frame。 |
| CPU frequency / big-little | 已完成 | `game_match_sgame_20260607_170754_perfetto_cpu_freq_summary.json`：Frame window 级 cluster 频率与线程 cluster runtime；`UnityMain`、`UnityGfxDeviceW` 均 100% 在 middle/big 运行。 | 帧窗口级 CPU 频率/大小核观测可用，但不是干预后的因果证明。 |
| jank cause classifier | 部分完成 | `step2_summary.json`：2 个 jank frame 候选分类为 `CPU_CONTENTION`，confidence=0.0。 | classifier 已跑通，但只能称为低置信度候选解释。 |
| 干预模式对比实验 | 部分完成 | `step2_summary.json`：graph AP@K = 0.2，heuristic AP@K = 0.2，Top-K overlap = 2。 | 只有 2 个 missed frame，适合作为 smoke test；真实干预效果对比待真机。 |

## 四、答辩关键数字摘要

| 场景 | 可直接使用的数字 |
|---|---|
| `game_sgame` | 923 个 FrameTimeline frame；2 个 deadline missed；Binder call 42,250；Futex wait 528,421；Top 线程候选包括 `UnityMain`、`UnityGfxDeviceW`、`surfaceflinger`。 |
| `feed_scroll` | 126 个 SurfaceFlinger interval；2 个异常长间隔候选；32 条 Binder 边；big cluster 目标线程 on-CPU 最高，为 6,715.341 ms；Top 线程候选包括 `Compositor`、`VizCompositorTh`、`CompositorGpuTh`。 |

## 五、不连手机可复现实验清单

| 脚本 | 输入 | 输出 | 当前离线复现状态 |
|---|---|---|---|
| `ebpf/scripts/parse_surfaceflinger_latency.py` | `feed_scroll_step2_aligned_20260527_surfaceflinger_latency.txt` | `feed_scroll_step2_aligned_20260527_frames.csv`、`*_frame_summary.json` | 可直接用已提交 feed_scroll 原始 SurfaceFlinger latency 文件重跑。 |
| `ebpf/scripts/analyze_perfetto_sched_windows.py` | `.perfetto-trace` + `*_perfetto_frametimeline_frames.csv` | `*_perfetto_frame_thread_sched.csv`、`*_perfetto_thread_sched_summary.csv`、`*_perfetto_frame_sched_summary.csv`、`*_perfetto_sched_summary.json` | 当前仓库保留了输出产物；重跑需要从 raw replay package 解出 `.perfetto-trace`。 |
| `ebpf/scripts/analyze_perfetto_cpu_freq_windows.py` | `.perfetto-trace` + `*_perfetto_frametimeline_frames.csv` | `*_perfetto_frame_cpu_freq.csv`、`*_perfetto_thread_cpu_cluster.csv`、`*_perfetto_cpu_freq_summary.json` | 当前仓库保留了输出产物；重跑需要从 raw replay package 解出 `.perfetto-trace`。 |
| `ebpf/scripts/extract_tracepilot_enhanced_events.py` | `*_tracepilot_stdout.txt` | `*_tracepilot_enhanced_events.csv`、`*_tracepilot_enhanced_events_summary.json` | 当前仓库保留了输出产物；重跑需要 raw replay package 内的 `tracepilot_stdout.txt`。 |
| `ebpf/scripts/build_tracepilot_offline_step_summary.py` | 2026-06-07 game dataset directory，需含 metadata、FrameTimeline summary、TracePilot result、hints、Perfetto sched/cpu/enhanced summaries | `*_step1_summary.json`、`*_step2_summary.json` | 可在 raw replay/分析产物齐全时离线重建 Step1/Step2 汇总，不需要重新打游戏。 |

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

## 六、必须连手机才能完成的边界

| 必须连手机的事项 | 原因 |
|---|---|
| 真正下发 `uclamp` / affinity / priority hint | 需要实时目标 TID、root 权限、前台包名守卫和 TTL rollback。 |
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
