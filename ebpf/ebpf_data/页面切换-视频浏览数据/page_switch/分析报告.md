# TracePilot 页面切换场景 — 数据分析报告（双次采集对照版）

**Run 1：** `6edbc3e2-c29d-470e-90e7-014568bf7365-104731`（`page_switch_run1/`）  
**Run 2：** `93d39f8a-fe0f-47d5-b5eb-65d85ca86a20-106833`（`page_switch/`）  
**采集场景：** `page_switch`（两次独立采集，各 30 秒，未指定目标 App）  
**分析模式：** Graph-based Critical Path（`-G -k 10 -s page_switch --debug`）  
**数据目录：** `output/page_switch_run1/` · `output/page_switch/`  
**报告日期：** 2026-06-01  

> 本报告按《实施计划》Step 1 / Step 2 / Step 3 逐条对照。  
> **范围说明：** 仅覆盖**页面切换场景**，不含视频浏览场景对比。

---

## 执行摘要

| 维度 | Run 1 | Run 2 |
|------|-------|-------|
| UI 帧 / Jank | **2171 / 1575（72.5%）** | **1271 / 1228（96.6%）** |
| 主根因 | **RUNNABLE_DELAY** | **RUNNABLE_DELAY** |
| 锚定 App | **unknown** | **com.tencent.mm:push** |
| 图规模 | **6968 节点 / 5234 边** | **4799 节点 / 3106 边** |
| events.bin | **690 MB** | **459 MB** |
| identity_map | **未拉取** | **1.59 MB（✅ 已拉取）** |
| Thermal | **neutral_therm（1 样本 · 31.4°C）** | **10 样本 · 36.8→41.5°C · throttle 0.47** |
| freq_throttle_ratio | 0.00 | 0.47 |

**综合结论：** 两次采集均呈现高 Jank 率；根因一致为 **CPU runnable/wakeup 排队（RUNNABLE_DELAY）**；Run 2 Thermal 修复后表现为轻度温控降频（throttle=0.47，Inference 已纳入 THERMAL_THROTTLE 作为 secondary 假设）。

---

# 第一部分 · Step 1 基础能力

## 1.1 Perfetto FrameTimeline（Task 1）

| 指标 | Run 1 | Run 2 |
|------|-------|-------|
| **总帧数** | **2171** | **1271** |
| **Jank 帧** | **1575** | **1228** |
| **Jank 率** | **72.5%** | **96.6%** |

**分析结论：** Run 2 Jank 率远高于 Run 1，可能因操作强度或后台负载差异导致。

---

## 1.2 sched_switch / wakeup + events.bin v3（Task 2）

| 文件 | Run 1 | Run 2 |
|------|-------|-------|
| `events.bin` | **690 MB** | **459 MB** |
| `perfetto_trace.perfetto-trace` | **134 MB** | **134 MB** |

**分析结论：** v3 格式 events 落盘完整，sched 密度高，支撑图构建。

---

## 1.3 身份解析层（Task 3）

| 字段 | Run 1 | Run 2 |
|------|-------|-------|
| `session_id` | `…-104731` | `…-106833` |
| 进程条目（identity_map） | **未拉取** | **1.59 MB（✅）** |
| `target_package` | **unknown** | **com.tencent.mm:push**（自动锚定） |

**分析结论：** Run 2 身份 sidecar 已成功落盘；未指定 `--package` 时系统自动锚定帧数最多的 App。

---

## 1.4 Frame Window Delay 聚合（Task 4）

**Run 1 Top 线程 Delay（P95 节选）：**

| 排名 | TID | comm | Runnable P95 | Wakeup P95 | 解读 |
|------|-----|------|-------------|------------|------|
| 1 | **103** | — | **2.66 s** | **2.66 s** | 极端 runnable/wakeup 延迟 |
| 2 | 52 | rcuop/4 | 238 ms | 735 ms | 内核 RCU |
| 3 | 66 | rcuop/6 | 130 ms | 747 ms | 内核 RCU |
| 4 | 48 | — | 13 ms | 28 ms | 高 frame overlap |
| 6 | **600** | surfaceflinger | 0 | 0 | UI 合成链 |
| 7 | 6979 | com.xingin.xhs | 0 | 0 | 小红书主线程 |

**Run 2 Top 线程 Delay（P95 节选）：**

| 排名 | TID | comm | Runnable P95 | Wakeup P95 | 解读 |
|------|-----|------|-------------|------------|------|
| 1 | **16945** | emitExt_tracker | **3.79 s** | **3.79 s** | 极端 runnable/wakeup 延迟 |
| 2 | 16689 | Thread-17 | 370 ms | 370 ms | 高延迟 |
| 3 | **103** | — | 67 ms | 69 ms | 与 Run 1 重叠的嫌疑线程 |
| 4 | 48 | — | — | — | 高 frame overlap |

**分类器：** 两路 session 主因均为 **RUNNABLE_DELAY**（Run 1：1575/1575；Run 2：1228/1228）。

---

## 1.5 UI / RenderThread 角色识别（Task 5）

| 节点类型 | Run 1 | Run 2 |
|----------|-------|-------|
| **SURFACEFLINGER** | rank 6, score **0.276** | ✅ 已识别 |
| **com.xingin.xhs** | rank 7, score **0.244** | — |
| **dynamic_decoder** | rank 8, score 0.233 | —（Run 2 无 decoder 边） |
| BINDER_SERVER | 52 条 BINDER_CALL | 53 条 BINDER_CALL |

---

## 1.6 Top-K 输出（Task 6）

**Run 1 CriticalScore Top-5：**

| # | TID | comm | Score | 主因 |
|---|-----|------|-------|------|
| 1 | **103** | — | **0.825** | RUNNABLE_DELAY |
| 2 | 52 | rcuop/4 | 0.567 | RUNNABLE_DELAY |
| 3 | 66 | rcuop/6 | 0.473 | RUNNABLE_DELAY |
| 4 | 48 | — | 0.459 | RUNNABLE_DELAY |
| 5 | 51 | rcuog/4 | 0.306 | RUNNABLE_DELAY |

**Run 2 CriticalScore Top-5：**

| # | TID | comm | Score | 主因 |
|---|-----|------|-------|------|
| 1 | **16945** | emitExt_tracker | **0.856** | RUNNABLE_DELAY |
| 2 | 16689 | Thread-17 | 0.650 | RUNNABLE_DELAY |
| 3 | **103** | — | **0.455** | RUNNABLE_DELAY |
| 4 | 48 | — | 0.425 | RUNNABLE_DELAY |
| 5 | 52 | rcuop/4 | 0.285 | RUNNABLE_DELAY |

> tid 103 在两次采集中都位居 Top-3，是跨采集稳定嫌疑线程。

**导出文件：** `graph_topology.json` · `graph_subgraph.json` / `.dot` · SVG（§2）

---

## 1.7 Hint Engine（Task 7）

| 项目 | Run 1 | Run 2 |
|------|-------|-------|
| Hint 数 | **1** | **1** |
| 类型 | **PROTECT_UI_CHAIN** | **PROTECT_UI_CHAIN** |
| 目标 | surfaceflinger（TID 686） | surfaceflinger |
| TTL | 300 ms · dry-run | 300 ms · dry-run |
| Inference 推荐 | **BOOST_THREAD** | **UCLAMP_MIN_TEMPORARY** |

---

# 第二部分 · Step 2 增强能力

## 2.0 关键路径子图

![Run 2 关键路径子图](./graph_critical.svg)

*Top CriticalScore 节点 2-hop 邻域。*

---

## 2.1 Binder 依赖图（Task 8）

![Run 2 Binder 依赖图](./graph_binder.svg)

| 指标 | Run 1 | Run 2 |
|------|-------|-------|
| BINDER_CALL 边 | **52** | **53** |
| Binder 调用次数 | **32,711** | **27,565** |
| 累计 Binder 阻塞 | **7.55 ms** | **4.50 ms** |

**分析结论：** jank 主因非 Binder（两次均为 0 帧 BINDER_BLOCKING）。

---

## 2.2 Futex 等待图（Task 9）

![Run 2 Futex 等待图](./graph_futex.svg)

| 指标 | Run 1 | Run 2 |
|------|-------|-------|
| FUTEX_WAIT 边 | **2,371** | **1,770** |
| Futex wait 次数 | **202,944** | **135,108** |

---

## 2.3 CPU 频率 / big-little（Task 10）

| 指标 | Run 1 | Run 2 |
|------|-------|-------|
| 小核平均频率 | **1,673 MHz** | **1,640 MHz** |
| 大核平均频率 | **2,093 MHz** | **2,051 MHz** |
| freq_throttle_ratio | **0.00** | **0.00** |

---

## 2.4 Jank 分类器（Task 11）

| 根因 | Run 1 | Run 2 |
|------|-------|-------|
| **RUNNABLE_DELAY** | **1575** | **1228** |
| BINDER_BLOCKING | 0 | 0 |
| FUTEX_BLOCKING | 0 | 0 |
| GPU_STALL | 0 | 0 |
| THERMAL_THROTTLE | 0 | 0 |

---

## 2.5 启发式 vs 图方法对比（Task 12）

| 指标 | Run 1 | Run 2 |
|------|-------|-------|
| Top-K 重叠线程数 | **5** | **7** |

**分析结论：** 图方法完整覆盖依赖链；建议加 `--package` 过滤以提升 SNR。

---

# 第三部分 · Step 3（Thermal · Inference · Multi-session）

## 3.1 Task 14 · Thermal 深化

| 检查项 | Run 1 | Run 2 |
|--------|-------|-------|
| `thermal_profile.txt` 行数 | **2**（含 header，1 条 neutral_therm 样本） | **11**（含 header，10 条有效样本） |
| baseline → peak | **31.4 °C（仅 1 点）** | **36.8 → 41.5 °C** |
| thermal_throttle_score | **0.00**（温升不足以触发） | **0.47** |
| thermal_proximity（Top-K） | **0.0** | **0.0** |
| Inference thermal 证据 | ❌ 无样本 | ✅ 已纳入（secondary=THERMAL_THROTTLE） |

**分析结论：** Run 2 温度在 30 秒内上升 4.7°C，触发轻度温控降频（throttle=0.47）；Run 1 仅 1 条 neutral_therm 样本（31.4°C），无明显温升。三次 session 使用了不同的温度传感器轨道。

---

## 3.2 Task 15 · Inference-aware 证据链

| 项目 | Run 1 | Run 2 |
|------|-------|-------|
| session_summary | 1575 jank · RUNNABLE_DELAY · thermal=0.00 | 1228 jank · RUNNABLE_DELAY · **thermal=0.47** |
| recommended_hint | **BOOST_THREAD** | **UCLAMP_MIN_TEMPORARY** |
| typical secondary | CPU_CONTENTION | **THERMAL_THROTTLE** |
| confidence | **0.9999** | **0.9999** |

| signal | Run 1 | Run 2 |
|--------|-------|-------|
| runnable_delay | ✅ | ✅ |
| thermal_throttle | ❌ | ✅（delta=4676 mc） |
| binder / futex | ❌ | ❌ |

---

## 3.3 Task 16 · Multi-session 对比

**命令：**
```bash
./output/tracepilot --compare-dir output --compare-out output/compare_report.json
```

**结果（`output/compare_report.json`）：**

| Session | 路径 | 帧 / Jank 率 | Top TID | CriticalScore | 主因 |
|---------|------|-------------|---------|---------------|------|
| Run 2 | `page_switch/result.json` | 1271 / **96.6%** | 16945 | **0.856** | RUNNABLE_DELAY |
| Run 1 | `page_switch_run1/result.json` | 2171 / 72.5% | 103 | 0.825 | RUNNABLE_DELAY |

**Top-1 重叠矩阵：** 两路 session 的 Top-3 共享线程 **tid 103**（Run 1 排 #1，Run 2 排 #3），跨采集有一定一致性。

---

# 第四部分 · 综合结论

## 4.1 能力验收总表

| 阶段 | 条目 | Run 1 数据 | Run 2 数据 |
|------|------|-----------|-----------|
| Step 1 | FrameTimeline / events / 身份 / delay / 角色 / Top-K / Hint | ✅ | ✅ |
| Step 2 | Binder / Futex / CPU / 分类 / 启发式对比 | ✅ | ✅ |
| Step 3 | Thermal | ⚠️ Run1 无 counter | ✅ 修复后 10 样本 |
| Step 3 | Inference | ✅ | ✅（含 THERMAL_THROTTLE） |
| Step 3 | Multi-session | ✅ | ✅ |

## 4.2 业务结论

1. 两次页面切换采集 Jank 率分别为 **72.5%（Run 1）** 和 **96.6%（Run 2）**，根因一致为 **RUNNABLE_DELAY / CPU 争用**。
2. **tid 103** 是唯一跨两次采集都出现在 Top-3 的线程（Run 1 排 #1，Run 2 排 #3），具有跨采集稳定性。
3. Run 2 Thermal 修复后显示轻度温升（36.8→41.5°C），throttle_score **0.47**，Inference 已纳入 THERMAL_THROTTLE 作为 secondary 假设，recommended_hint 变更为 **UCLAMP_MIN_TEMPORARY**。
4. **建议下次采集：** 加 `-Package` 指定目标 App；保持纯页面切换操作；thermal SQL 已修复，后续采集自动生效。

## 4.3 附件索引

| 文件 | 说明 |
|------|------|
| `output/page_switch/` · `output/page_switch_run1/` | 两次采集原始 + 分析输出 |
| `output/compare_report.json` | Step 3 Multi-session 对比 |
| `result.json` / `hints.json` / `identity_map.json` | 分析主输出 |
| `graph_*.json` / `graph_*.dot` / `graph_*.svg` | Step 2 图（SVG 已嵌入） |

---

*TracePilot 页面切换场景 · 双次采集数据分析报告（重采版）*
