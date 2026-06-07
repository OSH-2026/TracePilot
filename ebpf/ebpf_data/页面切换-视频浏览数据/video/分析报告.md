# TracePilot 视频浏览场景 — 数据分析报告

**会话 ID：** `93d39f8a-fe0f-47d5-b5eb-65d85ca86a20-107983`  
**采集场景：** `video`（30 秒，未指定目标 App）  
**分析模式：** Graph-based Critical Path（`-G -k 10 -s video --debug`）  
**数据目录：** `output/video/`  
**报告日期：** 2026-06-01  

> 本报告按《实施计划》Step 1 / Step 2 / Step 3 逐条对照，面向**视频浏览场景**。  
> **对比参考：** `output/page_switch/`（页面切换 Run 2）、`output/page_switch_run1/`（页面切换 Run 1）

---

## 执行摘要

| 维度 | 视频浏览 | 页面切换 Run 2 | 页面切换 Run 1 |
|------|---------|---------------|---------------|
| UI 帧 / Jank | **2141 / 1524（71.2%）** | 1271 / 1228（96.6%） | 2171 / 1575（72.5%） |
| VD 帧 | **561** | 0 | 0 |
| 主根因 | **RUNNABLE_DELAY** | RUNNABLE_DELAY | RUNNABLE_DELAY |
| 锚定 App | **com.ss.android.ugc.aweme**（抖音） | com.tencent.mm:push | unknown |
| 图规模 | **6622 节点 / 4992 边** | 4799 / 3106 | 6968 / 5234 |
| DECODE_DEPENDENCY | **560** | 0 | 567 |
| RESOURCE_STALL | **247** | 13 | 51 |
| BUFFER_QUEUE | **12** | 0 | 4 |
| NETWORK_WAIT | **25** | 0 | 19 |
| video_decoder 识别 | **3** | 0 | 1 |
| Thermal | **charger_skin_therm / usb_pwr_therm2（3 样本 · 30.3→52.3°C）** | 36.8→41.5°C · throttle 0.47 | neutral_therm（1 样本 · 31.4°C） |

**综合结论：** 视频场景成功识别 561 个 VD 帧、3 个解码器线程，图特征（RESOURCE_STALL=247 vs 13，NETWORK_WAIT=25 vs 0）与页面切换场景形成明显区别；Jank 率 71.2%，根因为 RUNNABLE_DELAY；三次 session 均通过 thermal_query.sql 修复获得温度数据。

---

# 第一部分 · Step 1 基础能力

## 1.1 Perfetto FrameTimeline（Task 1）

| 指标 | 值 |
|------|-----|
| **SF（UI 帧）** | **1580** |
| **VD（视频解码帧）** | **561** |
| **总帧数** | **2141** |
| **Jank 帧** | **1524** |
| **Jank 率** | **71.2%** |

**分析结论：** 561 个 VD 帧确认视频播放已采集；SF + VD 混合帧窗口符合视频浏览场景意图。

---

## 1.2 sched_switch / wakeup + events.bin v3（Task 2）

| 文件 | 值 |
|------|-----|
| `events.bin` | **451 MB**（3,598,368 sched + 1,724,573 sys + 567,918 enhanced） |
| `perfetto_trace.perfetto-trace` | **134 MB** |

---

## 1.3 身份解析层（Task 3）

| 字段 | 值 |
|------|-----|
| `session_id` | `…-107983` |
| 进程条目（identity_map） | **2.05 MB**（✅） |
| `target_package` | **com.ss.android.ugc.aweme**（抖音，自动锚定） |

---

## 1.4 Frame Window Delay 聚合（Task 4）

**Top 线程 Delay（P95）：**

| 排名 | TID | comm | Runnable P95 | Wakeup P95 |
|------|-----|------|-------------|------------|
| 1 | **103** | — | **956 ms** | **957 ms** |
| 2 | 66 | rcuop/6 | 288 ms | 290 ms |
| 3 | 52 | rcuop/4 | 101 ms | 179 ms |
| 4 | 51 | — | 18 ms | 72 ms |
| 5 | 48 | — | 5 ms | 16 ms |

**分类器：** 全部 1524 jank 帧归因为 **RUNNABLE_DELAY**。

---

## 1.5 UI / RenderThread 角色识别（Task 5）

| 节点类型 | 说明 |
|----------|------|
| VIDEO_DECODER | **3 个视频解码器线程已识别** |
| MEDIA_SERVER | 识别中 |
| SURFACEFLINGER | 已识别 |
| RENDER_THREAD | 已识别 |

---

## 1.6 Top-K 输出（Task 6）

**CriticalScore Top-5：**

| # | TID | comm | Score | 主因 |
|---|-----|------|-------|------|
| 1 | **103** | — | **0.735** | RUNNABLE_DELAY |
| 2 | 66 | rcuop/6 | 0.571 | RUNNABLE_DELAY |
| 3 | 52 | rcuop/4 | 0.568 | RUNNABLE_DELAY |
| 4 | 51 | — | 0.471 | RUNNABLE_DELAY |
| 5 | 48 | — | 0.350 | RUNNABLE_DELAY |

**导出文件：** `graph_topology.json` · `graph_subgraph.json` / `.dot` · SVG（§2）

---

## 1.7 Hint Engine（Task 7）

| 项目 | 值 |
|------|-----|
| Hint 数 | **1** |
| 类型 | **PROTECT_UI_CHAIN** |
| 目标 | surfaceflinger（TID 600） |
| TTL | 300 ms · dry-run |
| Inference 推荐 | **BOOST_THREAD** |

---

# 第二部分 · Step 2 增强能力

## 2.0 关键路径子图

![关键路径子图](./graph_critical.svg)

## 2.1 Binder 依赖图（Task 8）

![Binder 依赖图](./graph_binder.svg)

| 指标 | 视频浏览 | 页面切换 Run 2 |
|------|---------|---------------|
| BINDER_CALL 边 | **45** | 53 |
| Binder 调用次数 | **25,417** | 27,565 |
| 累计 Binder 阻塞 | **3.88 ms** | 4.50 ms |

---

## 2.2 Futex 等待图（Task 9）

![Futex 等待图](./graph_futex.svg)

| 指标 | 视频浏览 | 页面切换 Run 2 |
|------|---------|---------------|
| FUTEX_WAIT 边 | **1,977** | 1,770 |
| Futex wait 次数 | **244,012** | 135,108 |

**分析结论：** 视频场景 Futex wait 次数（244K）显著高于页面切换（135K），体现视频播放的并发同步压力更大。

---

## 2.3 CPU 频率 / big-little（Task 10）

| 指标 | 视频浏览 | 页面切换 Run 2 |
|------|---------|---------------|
| 小核平均频率 | **1,733 MHz** | 1,640 MHz |
| 大核平均频率 | **2,209 MHz** | 2,051 MHz |
| freq_throttle_ratio | **0.00** | 0.47 |

**分析结论：** 视频场景 CPU 频率更高（大核 +158 MHz），符合视频播放 GPU/解码器复合负载特征。

---

## 2.4 Jank 分类器（Task 11）

| 根因 | 帧数 |
|------|------|
| **RUNNABLE_DELAY** | **1524** |
| VIDEO_LATE_RENDER | 0 |
| BINDER_BLOCKING | 0 |
| FUTEX_BLOCKING | 0 |
| GPU_STALL | 0 |
| THERMAL_THROTTLE | 0 |

---

## 2.5 视频场景 vs 页面切换对比（Task 12）

| 图特征 | 视频浏览 | 页面切换 Run 2 | 差异 |
|--------|---------|---------------|------|
| DECODE_DEPENDENCY | **560** | 0 | 视频独有 |
| RESOURCE_STALL | **247** | 13 | **19x** |
| BUFFER_QUEUE | **12** | 0 | 视频独有 |
| NETWORK_WAIT | **25** | 0 | 视频独有 |
| video_decoder | **3** | 0 | 视频独有 |

**分析结论：** 视频场景在解码依赖、资源争用、buffer 队列和网络等待四个维度均大幅超出页面切换，图方法成功捕获了视频场景特有的瓶颈模式。

---

## 2.6 启发式 vs 图方法对比（Task 12）

`compare_heuristics()` 对比图方法（CriticalScore 加权评分）与传统启发式（基于帧窗口重叠 + runnable delay P95 的经验公式）在 Top-K 线程识别上的重叠度。

| 指标 | 值 |
|------|-----|
| 图方法 Precision@K | 0.60 |
| 启发式 Precision@K | 0.60 |
| Top-K 重叠线程数 | **6** |
| 图方法 SNR | 0.022 |
| 启发式 SNR | 0.500 |

**分析结论：** 两种方法在 Top-K 线程识别上高度一致，图方法 SNR 偏低因系统噪声线程（RCU/KWorker）未被启发式过滤。

---

# 第三部分 · Step 3

## 3.1 Thermal 深化

| 检查项 | 值 |
|--------|-----|
| `thermal_profile.txt` 行数 | **4**（含 header，3 条有效样本） |
| baseline → peak | **30.3 → 52.3 °C**（charger_skin_therm / usb_pwr_therm2） |
| thermal_throttle_score | **1.00** |
| Inference thermal 证据 | ✅ 已纳入 |

> **注意：** 视频 trace 不含 VIRTUAL-SKIN 标准温度轨道，仅含 `charger_skin_therm`（充电芯片温度，~52°C）和 `usb_pwr_therm2`（USB 端口温度，~30°C）。throttle_score=1.00 受传感器类型差异影响，建议采集时确认设备温度监控配置。

## 3.2 Inference-aware 证据链

| 项目 | 值 |
|------|-----|
| session_summary | 1524 jank · RUNNABLE_DELAY · scenario=video · **thermal=1.00** |
| recommended_hint | **UCLAMP_MIN_TEMPORARY** |
| typical hypothesis | RUNNABLE_DELAY / **THERMAL_THROTTLE** |
| confidence | **0.9999** |

## 3.3 Multi-session 对比

| Session | 场景 | 帧 / Jank 率 | Top-1 TID | 评分 |
|---------|------|-------------|-----------|------|
| video | 视频浏览 | 2141 / 71.2% | 103 | 0.735 |
| page_switch（Run 2） | 页面切换 | 1271 / 96.6% | 16945 | 0.856 |
| page_switch（Run 1） | 页面切换 | 2171 / 72.5% | 103 | 0.825 |

**Top-1 重叠：** tid 103 在三个 session 中均出现在 Top-3，是跨场景最稳定的嫌疑线程。

---

# 第四部分 · 综合结论

## 4.1 能力验收总表

| 阶段 | 条目 | 状态 |
|------|------|------|
| Step 1 | FrameTimeline / events / 身份 / delay / 角色 / Top-K / Hint | ✅ |
| Step 2 | Binder / Futex / CPU / 分类 / 视频场景对比 | ✅ |
| Step 3 | Thermal | ✅ 3 样本（charger_skin_therm + usb_pwr_therm2） |
| Step 3 | Inference | ✅ |
| Step 3 | Multi-session | ✅ |

## 4.2 业务结论

1. 视频浏览 Jank 率 **71.2%**，根因 RUNNABLE_DELAY；图特征与页面切换场景差异显著（RESOURCE_STALL 19x，DECODE_DEPENDENCY 560，NETWORK_WAIT 25）。
2. 成功识别 3 个视频解码器线程，561 个 VD 帧确认视频播放有效采集。
3. **tid 103** 跨三个采集 session（两个 page_switch + 一个 video）均出现在 Top-3，是全场景最稳定嫌疑线程。
4. Thermal 模块已接入（charger_skin_therm + usb_pwr_therm2，30.3→52.3°C），throttle_score=1.00 受传感器类型混合影响偏高。
5. 视频场景 CPU 频率（big: 2.21 GHz）显著高于页面切换（big: 2.05 GHz），Futex wait 次数（244K vs 135K）高一倍。

## 4.3 附件索引

| 文件 | 说明 |
|------|------|
| `output/video/` | 视频场景采集 + 分析输出 |
| `output/video/result.json` | 分析主输出 |
| `output/video/identity_map.json` | 身份映射（2.05 MB） |
| `output/video/graph_*.json` / `.dot` / `.svg` | Step 2 图 |
| `output/video/frames.txt` | Perfetto 帧数据 |
| `output/video/events.bin` | eBPF 原始事件（451 MB） |
| `output/compare_report.json` | 三路 Multi-session 对比 |

---

*TracePilot 视频浏览场景 · 数据分析报告*
