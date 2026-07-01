# TracePilot 项目汇报 — 40 分钟详细讲稿

> 共 **16 页**，每页 2-3 分钟，合计约 40 分钟
> 每页含：**时间分配 / 布局建议 / 完整文案 / 演讲话术**

---

## 第 1 页 — 封面（1 分钟）

### 时间分配
- 展示封面：15 秒
- 团队介绍 + 项目一句话定位：45 秒

### 内容文案

**主标题：** TracePilot
**副标题：** 以帧为中心的 Android 调度辅助系统
**底部信息：** Frame-aligned, dependency-aware scheduling assistant for Android interaction workloads

**右下角：**
TracePilot 团队 · 潘智勇 李松茂 邵晨轩 贺小轩 杨子皓 · 2026 年 6 月

### 演讲话术
> "大家好，我们是 TracePilot 小组。今天汇报的题目是——以帧为中心的 Android 调度辅助系统。简单说就是：我们想知道用户滑动手机时为什么会卡，并且能精准定位到是哪个线程、因为什么原因卡了。我们的实验平台是 Pixel 6a，技术栈是 eBPF 加 Perfetto。"

---

## 第 2 页 — 项目背景与研究问题（3 分钟）

### 时间分配
- 问题引入：1 分钟
- PID-Centric 为什么不行：1 分钟
- Frame-Centric 方案：1 分钟

### 内容文案

**主标题：** Android 卡顿：从何而来，如何定位？

**上半部分：核心问题**

Android 交互卡顿（Jank）的根源是一个**跨进程等待链**：
```
UI Thread → RenderThread → Binder → system_server → SurfaceFlinger
    ↑___________________ 卡顿根源在此 ___________________↑
```

传统的 `systrace` / `Perfetto UI` 能告诉你"帧掉了"，但不能告诉你"为什么掉"——因为你需要同时看到内核调度、Binder IPC、锁竞争三个维度。

**中部：为什么 PID-Centric 不可行**

| 传统方法 | 问题 |
|---------|------|
| 看 PID 的 CPU 占用 | App 每次启动 PID 都变，一个 App 可能有多个进程 |
| 看单个进程的 trace | 卡顿是 UI→Render→Binder→SF 的链，不是单个进程的事 |
| 看 eBPF 原始事件 | eBPF 只能观测内核事件，无法回答"用户在经历什么" |

**底部大字对比：**
> ❌ PID 视角：`pid=1234 进程 CPU 占用高` → 不知道哪帧卡了
> ✅ Frame 视角：`f32 帧内 SurfaceFlinger 被 Binder 调用阻塞了 12ms` → 精准定位

**核心路径：**
```
FrameTimeline 定义问题 → eBPF 提供原因 → Graph 找关键路径 → Hint Engine 做受控干预
```

### 演讲话术
> "Android 卡顿是一个老问题。但我们发现，几乎所有现有工具都在用进程 ID 来看问题——看哪个进程 CPU 高、哪个线程在跑。但 PID 是不稳定的，App 重启就变了。
>
> 更重要的是，卡顿从来不是单个进程的问题。用户感觉到一帧卡了，可能是 UI 线程在等 RenderThread，RenderThread 在等 Binder 调用 system_server，system_server 在等 SurfaceFlinger。这是一个跨进程的等待链。
>
> 所以我们的核心思想是：不要以进程为单位，要以**帧**为单位。Perfetto 告诉我们哪些帧卡了，eBPF 告诉我们卡的那一帧里内核发生了什么，我们构建依赖图找出关键线程，最后 Hint Engine 给出调度建议。"

---

## 第 3 页 — 项目历程与开发路线图（2 分钟）

### 时间分配
- 路线图总览：1 分钟
- 老师指导与迭代：1 分钟

### 内容文案

**主标题：** 项目开发路线图

**阶段时间线：**

```
前期调研 ──→ Step 1 基础能力 ──→ Step 2 增强能力 ──→ Step 3 深化对比 ──→ Step 3+ 展望
(第1个月)    (Perfetto+eBPF+    (Binder/Futex图+  (Thermal+Inference  (sched_ext/
             Hint Engine)       Jank分类+视频)     +Multi-session)    Learned Policy)
── ✅ 已完成 ── ✅ 已完成 ── ✅ 已完成 ── ✅ 已完成 ── ⏸ 未做
```

**关键迭代点：**

| 时间 | 事件 |
|------|------|
| 第 1 个月 | 4 份调研报告确认技术路线：eBPF 为主、Perfetto 为辅、Frame-Centric 视角 |
| 中期 | **刑凯老师反馈**：从"全量采集"转向"场景驱动"，做好特征语义映射 |
| 调整后 | 收敛到页面切换+视频浏览两大核心场景，增加 Binder/Futex 图分析 |
| 后期 | 扩展相机场景全自动 Pipeline、游戏场景 Unity 引擎分析 |

### 演讲话术
> "我们的开发分了四个阶段。第一个月做调研，确认了 eBPF 加 Perfetto 的技术路线。中期老师给了关键反馈——不要什么都采，要聚焦场景。所以我们收敛到页面切换和视频浏览。后面两个月逐步增强，加入了 Binder 图、Futex 图、温控分析，最后扩展到相机和游戏场景。"

---

## 第 4 页 — 前期调研成果（2 分钟）

### 时间分配
- 四份报告概览：1 分钟
- 核心结论：1 分钟

### 内容文案

**主标题：** 前期调研 — 技术路线确立

**四份报告：**

| 报告 | 核心内容 |
|------|---------|
| **调研报告** | Android 16 + eBPF 预测调度可行性，LLM / 自研时序模型选型 |
| **可行性报告** | eBPF 数据采集 7 层模型设计（统一上下文→调度→Binder→Futex→CPU→内存→可扩展） |
| **补充调研报告** | 系统行为观测扩展（网络、传感器）、高频场景、关键/非关键线程识别 |
| **TracePilot 调研扩展** | Page Turning、Feed Scroll 场景的行为特征分析方法 |

**核心结论（三列强调）：**
1. **eBPF 适合内核观测**，最关键是调度事件 + Binder + Futex + CPU 频率
2. **一期聚焦页面切换**，不承诺网络/文件系统/IRQ 扩展观测
3. 采用 **Frame-Centric** 而非 PID-Centric 的观测视角

### 演讲话术
> "调研阶段我们写了四份报告——从 Android 内核的 eBPF 支持程度、到数据采集的七层模型设计、到关键线程的识别方法都做了论证。最终确定：一期就做页面切换，以帧为单位看问题，核心采集调度、Binder、Futex 三类事件。"

---

## 第 5 页 — 技术栈与系统架构（3 分钟）

### 时间分配
- 分层架构详解：2 分钟
- 各技术角色：1 分钟

### 内容文案

**主标题：** 技术栈与系统架构

**分层架构（五层）：**

```
┌──────────────────────────────────────────────────────────┐
│  ⑤ 分析层 (Python + sklearn + Graphviz)                  │
│     analyze_delays → critical_path → root_cause           │
│     → jank_classifier → safe_hint_engine → report        │
├──────────────────────────────────────────────────────────┤
│  ④ 用户态加载层 (C loader)                                │
│     bpf() 加载 → ringbuf 读取 → 身份解析                  │
│     → 帧窗口聚合 → 图构建 → Hint Engine                  │
├──────────────────────────────────────────────────────────┤
│  ③ 帧标定层 (Perfetto)                                   │
│     trace_processor_shell + SQL → frames.txt              │
│     SF/VD/VF/AP 帧类型 + jank 标记                        │
├──────────────────────────────────────────────────────────┤
│  ② 内核采集层 (eBPF)                                      │
│     13 探针 (tracepoint + kprobe + tp_btf)                │
│     ringbuf → 3 CSV (sched / binder_futex / irq)          │
├──────────────────────────────────────────────────────────┤
│  ① 硬件层                                                │
│     Pixel 6a / Android 14 / Magisk root / ARM64          │
└──────────────────────────────────────────────────────────┘
```

**五层数据流：**
```
设备端（①②）：Perfetto 守护进程 + eBPF loader 并行采集
       ↓ adb pull
宿主机端（③④⑤）：trace_processor_shell SQL 帧提取 → C loader 事件解析 → Python 分析链
```

**关键设计决策：**
- **双通道同步采集**：Perfetto 标定帧边界，eBPF 采集内核事件，两者通过 BootTime 时间戳对齐
- **离线分析**：采集和分析分离，设备端仅写入 CSV，所有重计算在宿主机完成
- **两条独立验证路径**：CriticalScore 排名 + 根因归因并行计算、互相校验

### 演讲话术
> "我们系统分五层。最底层是 Pixel 6a 硬件——必须 root 才能加载 eBPF。第二层是内核采集层，我们在内核挂了 13 个探针，通过 ringbuf 输出到三个 CSV 文件。第三层是 Perfetto 帧标定——我们用 trace_processor_shell 执行 SQL 查询，提取每一帧的开始、结束、是否 jank。第四层是 C loader 用户态程序，负责读 CSV、做帧对齐、构图。最上层是 Python 分析链——从延迟聚合到根因归因到报告生成，十几个脚本串联。"

---

## 第 6 页 — eBPF 探针设计（3 分钟）

### 时间分配
- 推导逻辑：1 分钟
- 探针表详解：1.5 分钟
- 编译部署：0.5 分钟

### 内容文案

**主标题：** eBPF 探针 — 为什么是这 13 个？

**推导逻辑：**
> 用户感知卡顿 → 帧掉了 → 该帧内的线程没在跑 → 三种可能：
> ① **调度排队**（有其他线程占了 CPU）
> ② **等 Binder**（跨进程 RPC 调用没返回）
> ③ **等锁**（Futex 被其他线程持有）
> → 所以必须同时采 `sched + binder + futex`
> → 再加 `cpu_frequency + thermal + irq + mem` 解释环境因素

**探针一览：**

| 探针 | 挂载方式 | 写入 CSV | 30s 数据量 |
|------|---------|---------|:---:|
| `sched_switch` + `sched_wakeup` | tracepoint | `sched_events.csv` | ~200 万行 |
| `binder_transaction` + `binder_received` | **kprobe** ⚠️ | `binder_futex_events.csv` | ~16 万行 |
| `futex` (sys_enter/exit) | tracepoint | `binder_futex_events.csv` | ~27 万行 |
| `cpu_frequency` | tracepoint | `binder_futex_events.csv` | ~1 万行 |
| `thermal_temperature` | tracepoint | `binder_futex_events.csv` | — |
| `mem_reclaim` | tracepoint | `binder_futex_events.csv` | ~55 次 |
| `irq_handler` + `softirq` | **tp_btf** | `irq_events.csv` | ~210 万行 |

> ⚠️ **binder 为什么用 kprobe？** Android GKI 内核没有暴露 binder 的 tracepoint，只能通过 kprobe 动态挂载到 `binder_transaction` 函数入口
> **所有事件统一为 10 字段 CSV 格式**：ts/event/tid/prev_tid/tgid/uid/debug_id/extra/ret/comm，跨探针复用、分析脚本无需区分来源

**编译与部署：**
```
Docker(Ubuntu 22.04 + NDK r26b + clang) → 交叉编译 ARM64 → adb push → bpf() 加载 → ringbuf 输出
```

### 演讲话术
> "探针的选择不是拍脑袋的。我们的推导逻辑是：用户感觉到卡，一定是帧掉了；帧掉了，一定是该帧内的关键线程没在跑；线程没跑，只有三种原因——在排队等 CPU、在等 Binder 返回、在等锁释放。所以我们必须同时采集这三种信号。
>
> 注意 binder 用的是 kprobe——Android 内核没有 binder 的 tracepoint，只能通过动态挂载函数入口来实现。另外，irq 和 softirq 用的是 tp_btf，比普通 tracepoint 更底层，需要内核支持 BTF。
>
> 编译我们用 Docker 封装了 Android NDK，一键交叉编译出 ARM64 二进制，adb push 到手机上加载。"

---

## 第 7 页 — 五大场景覆盖（2 分钟）

### 时间分配
- 五个场景卡快速过：1.5 分钟
- 补充分析：0.5 分钟

### 内容文案

**主标题：** 五大采集场景覆盖

| 场景 | App | 数据规模 | 特点 |
|------|-----|---------|------|
| 页面切换（基础版） | QQ | 690MB events.bin | IRQ/softirq 辅助分析 |
| 页面切换+视频（增强版） | 微信/抖音 | 451~459MB | Binder/Futex 图 + Jank 分类 + Hint Engine |
| 信息流滚动 | Chrome | 261 万事件/34s | 秒级聚合 + 34 线程 + Step2 Binder/启发式 |
| 相机拍照 | Google Camera | 460 万事件/30s | 13 探针 + 内核内延迟 + 全自动 Pipeline |
| 游戏对局 | 王者荣耀 | 2.1GB/60s | Unity 引擎 + FrameTimeline + 图拓扑(60K) |

**场景多样性价值：**
- 页面切换 → 传统 UI 框架（Android View）
- 视频浏览 → 解码+渲染双管线
- 信息流滚动 → 高频事件压力测试
- 相机 → HAL 层密集调用
- 游戏 → Unity 引擎，非标准线程模型

### 演讲话术
> "我们采集了五个场景，从传统的 Android UI 到 Unity 游戏引擎。不同场景的数据量差异很大——页面切换 690MB，相机 30 秒就产生 460 万事件，游戏 60 秒 2.1GB。这验证了我们的 Pipeline 在不同负载下都能正常工作。更重要的是，游戏场景的线程模型完全不同——没有 UI Thread 和 RenderThread，取而代之的是 UnityMain 和 UnityGfxDeviceW。"

---

## 第 8 页 — Frame-Centric 对齐算法（3 分钟）

### 时间分配
- 问题阐述：1 分钟
- 对齐方法：1.5 分钟
- 关键指标：0.5 分钟

### 内容文案

**主标题：** 核心对齐算法 — eBPF 事件如何与帧对齐？

**问题：两个独立数据流**

Perfetto 产生的是帧边界（`expected_start` / `actual_end` / `jank_tag`），eBPF 产生的是纳秒级内核事件流。两者唯一的共同点是**时间戳**。对齐问题是整个系统的核心。

**对齐三步走：**

```
Step ①: Perfetto SQL 查询
  SELECT frame_token, intended_vsync, actual_end, jank_tag
  FROM actual_frame_timeline_slice
  → frames.txt（每帧一行：帧号、预期VSYNC时间、实际显示时间、是否Jank）

Step ②: 时间戳交集开窗
  Perfetto frames:    f0[0ms-16ms]   f1[16ms-32ms]   f2[32ms-48ms] ...
                              ↓ 纳秒级时间戳匹配          ↓
  eBPF events:       ●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●...
                              ↓
  每个 Jank 帧独立开窗，收集窗口内所有 TID 的调度/binder/futex 事件

Step ③: 依赖继承
  跨帧事件（如 f0 发出的 Binder 调用在 f1 才收到回复）
  → "依赖继承机制"保留因果边，避免切帧时丢失上下文
```

**输出指标（每帧）：**
- `runnable_delay`：该帧窗口内所有线程就绪等待时间总和
- `binder_depth`：Binder 调用链的最大深度
- `futex_intensity`：Futex 竞争次数

### 演讲话术
> "这是整个项目最核心的对齐问题。Perfetto 和 eBPF 是两个完全独立的数据流，唯一的桥梁是时间戳。我们通过帧窗口开窗——取帧的 expected_start 到 actual_end，把这段时间内的所有 eBPF 事件聚合。
>
> 但有一个坑：Binder 调用是异步的。f0 这一帧发起的 Binder 调用，可能在 f1 才收到回复。如果我们简单切帧，就会丢失因果链。所以我们的解决方案是'依赖继承'——跨帧的 Binder 调用保留为跨帧边。
>
> 另外，我们统一用 BootTime 作为时间基准。eBPF 侧用 `bpf_ktime_get_boot_ns()`，Perfetto 侧也是 BootTime，天然对齐，不需要手动换算。"

---

## 第 9 页 — 图构建与 CriticalScore 算法（4 分钟）

### 时间分配
- 节点与边：1.5 分钟
- CriticalScore 公式与直觉：1.5 分钟
- 两条独立路径：1 分钟

### 内容文案

**主标题：** 图构建 — 从原始事件到依赖关键路径

**节点：每个节点 = 一条线程**

| 节点属性 | 取值 |
|---------|------|
| `id` | TID |
| `comm` | 线程名（内核 task_struct->comm，最多 15 字符） |
| `role` | classify_thread() 自动识别（12 类角色） |
| `frame_window_overlap` | 该线程在多少个 jank 帧窗口中出现 |

**角色识别（12 类）：**

| 角色 | 判定 | 图颜色 |
|------|------|:---:|
| UI Thread | TID == target_pid | 🟣 |
| RenderThread | comm 含 "renderthread" | 🔵 |
| SurfaceFlinger | comm 含 "surfaceflinger" | 🔴 |
| Binder RPC | comm 含 "binder" | 🩵 |
| GPU Worker | comm 含 "gpu"/"gl" | 🟢 |
| KernelWorker | comm "kworker"/"swapper" | ⚪ |

**边：5 种依赖关系**

| 边类型 | 来源 | 构建逻辑 |
|--------|------|---------|
| BINDER_CALL | binder_transaction | TX→RX（debug_id 配对） |
| FUTEX_WAIT | futex | 同 uaddr 的 wait→wake |
| SCHED_DEPENDENCY | sched_wakeup | waker→wakee |
| DECODE_DEPENDENCY | 视频帧 | 解码→渲染 |
| RESOURCE_STALL | cpu_frequency | CPU 资源不足 |

**CriticalScore 核心公式：**

$$Score(T) = \alpha \cdot Position + \beta \cdot \frac{RunnableDelay(T)}{TotalDelay} + \gamma \cdot ConnectionDegree(T)$$

**直觉解释：**

| 维度 | 直觉 |
|------|------|
| **位置权重** Position | 越靠近帧渲染末端（SurfaceFlinger）越关键——因为在渲染链上越往后，延迟越不可挽回 |
| **延迟占比** DelayShare | 该线程浪费的时间占整个帧延迟的比例——谁占的份额大谁就更可疑 |
| **连接度** Degree | 出度+入度——如果一条线程通过 Binder/Futex 影响了大量其他线程，它的嫌疑更大 |

默认 α=0.4, β=0.4, γ=0.2

**两条独立分析路径（架构解耦）：**

| 路径 | 方法 | 回答 |
|------|------|------|
| CriticalScore | 5 维全局加权排名 | 哪个线程**全局**最可疑？ |
| 根因归因 | 帧内时间占比直接判定 | 每帧卡顿的**主因**是什么？ |

> 两条路径不互相依赖、独立计算 → 一条出错另一条仍有效

### 演讲话术
> "这是项目的算法核心。我们把每条线程变成一个图节点——注意，是线程不是进程，因为卡顿发生在线程级别。边是五种依赖关系：Binder 调用、Futex 锁等待、调度唤醒、解码依赖和资源停顿。
>
> CriticalScore 为什么是这三个维度？位置权重——越靠近 SurfaceFlinger 越关键，因为渲染链上靠后的线程延迟更不可挽回。延迟占比——谁占的份额大谁就更可疑。连接度——如果一条线程通过 Binder 影响了大量其他线程，它就是瓶颈节点。
>
> 我们还设计了一个重要的架构决策：两条独立分析路径。CriticalScore 做全局排名回答'谁最可疑'，根因归因做帧内时间占比回答'每帧卡的主因是什么'。两条路径不互相依赖——如果一条路径算错了，另一条仍能给出有效结论。"

---

## 第 10 页 — 核心分析成果（页面切换 + 视频）（4 分钟）

### 时间分配
- 对照表讲解：1.5 分钟
- 根因推断：1 分钟
- 洞察解读：1 分钟
- 规模总览：0.5 分钟

### 内容文案

**主标题：** 核心分析成果 — 三次采集对照

**三次采集对照：**

| 维度 | 页面切换 Run 1 | 页面切换 Run 2 | 视频浏览 |
|------|:---:|:---:|:---:|
| 总帧 / Jank | 2171 / 1575 | 1271 / 1228 | 2141 / 1524 |
| **Jank 率** | **72.5%** | **96.6%** | **71.2%** |
| 图规模 | 6968 节点 / 5234 边 | 4799 节点 / 3106 边 | 6622 节点 / 4992 边 |
| **调频抑制比** | **0.00** | **0.47** | **1.00** |
| 最高温度 | 31.4°C | 41.5°C | 52.3°C |

**根因推断：**

| 场景 | 根因 | 推荐 Hint |
|------|------|-----------|
| Run 1 | RUNNABLE_DELAY | PROTECT_UI_CHAIN → surfaceflinger |
| Run 2 | RUNNABLE_DELAY | **UCLAMP_MIN_TEMPORARY** |
| 视频 | RUNNABLE_DELAY | BOOST_THREAD |

**三大洞察：**

| 数据 | 洞察 |
|------|------|
| Run1 无温控但 Jank 率 72.5% | → **调度竞争本身就是主要瓶颈**——不是 Binder、不是锁 |
| Run2 温控 0.47 后 Jank 率 96.6% | → 温度升高降频介入——**温控是雪上加霜** |
| 视频温控 1.00 但 Jank 率 71.2% | → 温控最严重但 Jank 率非最高——**视频有额外延迟容忍** |

**系统处理规模（以 Camera 为例）：**
> 30 秒 Google Camera 拍照 → ~460 万行 eBPF 事件 → 631 线程评分 → 16 帧卡顿分析 → 9 章约 750 行 Markdown 报告

### 演讲话术
> "我们做了三次采集，覆盖了不同的温度和降频条件。三次的根因都是 RUNNABLE_DELAY——线程在就绪队列里等太久。但三个场景的表现非常不同。
>
> Run 1 没有任何温控，但 Jank 率已经 72.5%。这说明即使在理想温度下，调度竞争本身就很严重。
> Run 2 温度升到 41 度，降频抑制比 0.47——Jank 率飙升到 96.6%。Hint Engine 自动把推荐策略从 PROTECT 升级成了 UCLAMP。
> 视频场景温度最高 52 度，完全降频，但 Jank 率只有 71.2%——跟 Run 1 差不多。为什么？因为视频帧的容忍度更高，解码延迟本身就是预期内的。
>
> 这三个数据点告诉我们：调度竞争是主因、温控是放大器、场景差异不能忽视。"

---

## 第 11 页 — 游戏场景发现（2 分钟）

### 时间分配
- 数据对比：1 分钟
- 解读：1 分钟

### 内容文案

**主标题：** 游戏场景 — Unity 引擎的调度特征

| 指标 | 短窗口 (24.8s) | 对局窗口 (59.2s) | 倍数 |
|------|:---:|:---:|:---:|
| CPU 负载 | 379.9 ms/s | 670.3 ms/s | **1.76x** |
| runnable delay p95 | 0.304 ms | 0.664 ms | **2.18x** |
| UnityGfxDeviceW p95 | 0.299 ms | 1.584 ms | **5.3x** |
| 线程迁移 | 258/s | 339/s | **1.31x** |

**与页面切换的关键区别：**

| 对比维度 | 页面切换 (QQ/微信) | 游戏对局 (王者荣耀) |
|---------|-----------------|-------------------|
| 线程模型 | UI Thread + RenderThread | **UnityMain + UnityGfxDeviceW** |
| 主要瓶颈 | Runnable Delay（就绪等待） | **Runnable Delay + 高频迁移** |
| 图规模 | ~7000 节点 | **~60K 节点** |

**核心发现：** 游戏场景验证了 Frame-Centric 方法的通用性——即使在完全不同的线程模型下，同样的 CriticalScore 公式仍然能正确识别瓶颈线程。

### 演讲话术
> "游戏场景给了我们一个完全不同的测试视角。王者荣耀用的是 Unity 引擎，线程模型跟 Android UI 完全不同——没有 UI Thread 和 RenderThread，取而代之的是 UnityMain 和 UnityGfxDeviceW。
>
> 对局中 UnityGfxDeviceW 的 runnable delay 飙升了 5 倍，从 0.3 毫秒到 1.58 毫秒。线程迁移也从 258 次每秒升到 339 次。图规模达到了六万个节点。这说明游戏场景的调度压力远超普通应用。
>
> 但重要的是——我们的 CriticalScore 公式在完全不同的线程模型下仍然有效。这说明 Frame-Centric 方法是通用的，不局限于特定的 UI 框架。"

---

## 第 12 页 — Step 1：基础能力 + Hint Engine（3 分钟）

### 时间分配
- 七个模块：1 分钟
- Hint Engine 详解：2 分钟

### 内容文案

**主标题：** Step 1 — 基础能力实现

**七个核心模块：**

| # | 功能 | 关键实现 |
|---|------|---------|
| 1 | Perfetto 帧提取 | `frame_query.sql` → 四种帧类型（SF/VD/VF/AP） |
| 2 | eBPF sched 采集 | events.bin v3 格式（sched + sys + enhanced 三类事件） |
| 3 | 身份解析 | `identity.c`：Session / ProcessInstanceId / ThreadKey + 静态血缘 |
| 4 | Frame window delay | Phase 5b：wakeup+preempt 在 BPF 内核内完成，事件量减半 |
| 5 | 角色识别 | `classify_thread()`：12 类角色 + 30+ Camera 特有模式 |
| 6 | Top-K 输出 | `-G -k N` 关键线程排名 |
| 7 | Hint Engine | BOOST_THREAD / UCLAMP_MIN / PROTECT_UI_CHAIN |

**Hint Engine 安全机制（五层防护）：**

| 安全机制 | 设计 | 防止什么 |
|---------|------|---------|
| **TTL** | 每个 hint 默认 16ms（=1 帧），超时自动回滚 | 避免 hint 长期生效影响后续帧 |
| **自旋保护** | 5ms 内连续 3 次 on CPU 无 sleep → 撤销 UCLAMP | 避免提升后自旋浪费 CPU |
| **线程黑名单** | kworker / rcuop / swapper / system_server 等不可干预 | 系统线程出问题会导致内核崩溃 |
| **置信度阈值** | >0.7 才提交执行，0.5-0.7 仅记录建议，<0.5 不输出 | 低置信度不做实际干预 |
| **audit 日志** | 每条 hint 的 TID / 操作 / 时间戳 / 预期+实际持续时长 | 事后可审计、可回溯 |

**Hint 生成示例：**
```
帧 f32 → inference: RUNNABLE_DELAY (confidence=0.9999)
  → Top-1 线程: surfaceflinger (TID=686, CriticalScore=0.825)
  → Hint: PROTECT_UI_CHAIN → surfaceflinger（提升 UI 渲染链优先级 16ms）
```

### 演讲话术
> "Step 1 搭建了整个系统的地基。特别讲一下 Hint Engine——因为我们要做的是调度干预，安全是第一优先级。我们设计了五层防护。
>
> 第一，TTL——每个 hint 只活 16 毫秒，一帧结束就自动回滚。第二，自旋保护——如果线程连续在 CPU 上自旋超过 5 毫秒，我们自动撤销 hint，避免浪费算力。第三，黑名单——内核线程像 kworker、swapper 绝对不能碰。第四，置信度阈值——只有 inference 打分超过 0.7 的才真的执行。第五，完整 audit 日志——每条 hint 的来龙去脉全记录。"

---

## 第 13 页 — Step 2：增强能力（3 分钟）

### 时间分配
- 三问题框架：1 分钟
- Binder/Futex 图 + 分类器：1.5 分钟
- Camera 深度分析：0.5 分钟

### 内容文案

**主标题：** Step 2 — 增强能力实现

**三问题框架：**

| 问题 | 方法 | 输出 |
|------|------|------|
| **谁慢了？** | CriticalScore 5 维加权排名 | Top-K 表 → 报告第一章 |
| **怎么阻塞的？** | DAG 关键路径图（4 种边） | Binder/Futex/关键路径 SVG → 报告第五章 |
| **为什么卡？** | 6 信号根因归因 | root_cause_analysis.json → 报告第六章 |

**图分析：**
- Binder 图 → 跨进程 IPC 依赖关系（`graph_binder.svg`）
- Futex 图 → 锁竞争热点（`graph_futex.svg`）
- 关键路径图 → 全链路瓶颈（`graph_critical.svg`）
- 自动导出 SVG，颜色编码角色

**Jank 分类器（全自动 Pipeline）：**

```
auto_label.py → suspect_frames.py → 人工复核 → train_jank_model.py
     ↓                ↓                               ↓
  启发式自动标注    筛选标注可疑的帧           sklearn DecisionTree
  (6维特征空间)    (binder/futex/thermal       LeaveOneOut 交叉验证
                   偏高但标为RUNNABLE)         → learned_model.h (C)
```

**Camera 模块特点：**
- 13 探针 + 36MB ringbuf：比基础版多 7 个探针（irq/softirq/mem_reclaim 等）
- 内核内延迟计算：wakeup→switch 配对在 BPF 内完成，输出 events.bin 事件量减半
- 6 信号归因：Sched + Binder + Futex + IRQ + SoftIRQ + 环境
- 30s 采集 → 460 万事件 → 631 线程评分 → 16 jank 帧

**聚合策略对比：**

| 策略 | 适用场景 | 压缩效果 |
|------|---------|---------|
| 帧级窗口 | 页面/视频/游戏（需帧精确对齐） | ~2000 行 |
| 秒级窗口 | 信息流（快速宏观趋势观察） | ~35 行 |

### 演讲话术
> "Step 2 的核心是用三个问题来驱动：谁慢了、怎么阻塞的、为什么卡。图分析让我们能可视化 Binder 瓶颈和锁竞争——这些 SVG 图是自动生成的，每种线程角色有固定颜色。
>
> Jank 分类器是一个完整的机器学习 Pipeline——先自动标注、再筛选可疑帧让人类复核、最后训练决策树，导出 C 头文件嵌入 loader。
>
> Camera 模块是最深度的场景——13 个探针、内核内完成延迟计算、6 信号归因。一次 30 秒采集就产生 460 万事件。我们做了全自动一键 Pipeline，从编译部署到 9 步分析到报告生成。"

---

## 第 14 页 — Step 3：深化与对比（3 分钟）

### 时间分配
- Thermal 分析：1 分钟
- Multi-session 对比：1 分钟
- Cross-scenario 验证：1 分钟

### 内容文案

**主标题：** Step 3 — 深化与对比

**Thermal 深化：**
- `thermal_temperature` + `cpu_frequency` 联动 → **freq_throttle_ratio**
  - 0.0 = 无降频 | 1.0 = 完全限频
- 视频：1.00（52.3°C） | 页面 Run2：0.47（41.5°C） | 页面 Run1：0.00（31.4°C）
- `THERMAL_THROTTLE` 为独立 Jank 类别，温差 21°C → 抑制比从 0 → 1

**Multi-session 对比发现：**

| 发现 | 证据 |
|------|------|
| Jank 率与降频正相关 | 72.5%(0.00) → 96.6%(0.47) → 71.2%(1.00) |
| 调度竞争本身已是瓶颈 | Run1 无温控但 Jank 率 72.5% |
| 视频有额外延迟容忍 | 温控 1.00 但 Jank 率 ≈ Run1 |

**Cross-scenario 验证：**
- 页面切换：6 维特征 → 视频场景需增加第 7 维 `decode_late`
- 缺少则系统将视频解码延迟误归因为 RUNNABLE_DELAY
- 验证了**特征空间需要随场景扩展**

**Inference Engine 证据链：**

| 信号 | 来源 | 作用 |
|------|------|------|
| runnable_delay | sched_switch | 调度竞争——最强信号 |
| binder_centrality | binder_transaction | IPC 瓶颈 |
| futex_wait | futex | 锁竞争 |
| thermal_throttle | thermal + cpufreq | 温控降频 |
| decode_late | 视频解码 | 解码延迟（仅视频场景） |
| system_irq | irq/softirq | 中断扰动 |

多信号加权融合 → hypothesis + confidence → 映射为 BOOST / UCLAMP / PROTECT

### 演讲话术
> "Step 3 是研究的深化。Thermal 分析让我们能量化温控降频——从 31 度到 52 度，21 度的温差让抑制比从 0 升到 1。Multi-session 对比最有意思的发现是：Run1 没有温控但 Jank 率已经 72.5%——这说明调度竞争本身就是瓶颈，温度只是放大器。
>
> Cross-scenario 验证中我们发现一个关键问题：视频场景如果只用 6 维特征，系统会把解码延迟误判为 RUNNABLE_DELAY。必须有第 7 维 decode_late。这验证了特征空间需要随场景扩展的设计原则。"

---

## 第 15 页 — 技术挑战与解决方案（2 分钟）

### 时间分配
- 四个难点快速过：2 分钟

### 内容文案

**主标题：** 工程实践中攻克的关键技术难点

| 难点 | 问题 | 解决方案 |
|------|------|---------|
| **进程名截断** | 内核 `task_struct->comm` 仅 15 字符，`com.google.android.GoogleCamera` 被截断为 `com.google.andr`，无法匹配目标进程 | 反其道而行——在 Perfetto 中扫描 `App Deadline Missed` 的 UPID，反向锁定 Janky 应用 |
| **PID 复用** | PID 回收后分配给别的进程，仅凭 TID 匹配导致"幽灵线程"污染数据 | 引入 Identity Layer：以 UID+TGID 为过滤键，用 Perfetto 静态血缘做白名单 |
| **Ringbuf 丢数据** | `while(1)` 轮询退出过早，底层 fflush 未完成导致 trace 截断 | 修正退出生命周期，实时 fflush 落盘，确保高频事件完整 |
| **时钟域对齐** | eBPF 用 `bpf_ktime_get_boot_ns()`，Perfetto 用 BootTime——必须一致才能对齐 | 在 BPF 侧统一使用 `bpf_ktime_get_boot_ns()`，与 Perfetto 天然对齐 |

### 演讲话术
> "在实际工程中我们遇到了四个典型坑。最有意思的是进程名截断——Android 内核的 task_struct->comm 只有 15 个字符。Google Camera 的完整包名是 com.google.android.GoogleCamera，被截断成了 com.google.andr。我们怎么都匹配不到目标进程。
>
> 最后我们的解决方案是反向思维——不去匹配进程名，直接在 Perfetto 里找那些出现了 App Deadline Missed 的帧，反向锁定目标应用。这是一个典型的'以果推因'的工程智慧。"

---

## 第 16 页 — 总结与展望（2 分钟）

### 时间分配
- 做了什么 + 发现了什么：1 分钟
- 核心贡献 + 展望：1 分钟

### 内容文案

**主标题：** 总结与展望

**我们做了什么：**

| # | 成果 |
|:-:|------|
| ✅ | eBPF + Perfetto 双通道采集，覆盖五大场景（页面/视频/信息流/相机/游戏） |
| ✅ | 帧对齐 + 依赖图构建：最高 60K 节点图，两条独立验证路径 |
| ✅ | 6 维特征 + 决策树分类：端到端 Jank 根因自动分类 |
| ✅ | Hint Engine：五层安全防护（TTL/自旋/黑名单/阈值/审计） |
| ✅ | Camera 全自动 Pipeline：30s 采集 → 9 步分析 → 750 行报告 |
| ✅ | 10 个测试脚本：覆盖 eBPF 探针 → 决策树全链路校验 |

**我们发现了什么：**

| 发现 | 证据 |
|------|------|
| **调度竞争是卡顿主因** | 无温控 Jank 率 72.5%，非 Binder、非锁 |
| 温控降频雪上加霜 | 温度升高后 Jank 率升至 96.6% |
| 场景差异显著 | 视频有额外容忍度，游戏 Unity 线程模型完全不同 |
| Hint Engine 自适应 | 根据温控自动切换 PROTECT → UCLAMP |

**核心贡献：**
> **证明了 Frame-Centric（帧对齐 + 依赖感知）比传统 PID-Centric 更有效地定位 Android 交互卡顿根因**

**下一步：**

| 方向 | 说明 |
|------|------|
| 🔲 sched_ext | Linux 6.12+ 可编程调度器 |
| 🔲 Learned Policy | 强化学习 + 多 trace 训练 |
| 🔲 场景扩展 | 游戏深化、支付、传感器融合 |

### 演讲话术
> "总结一下。我们做了五件事——双通道采集、帧对齐构图、决策树分类、Hint Engine 安全建议、全链路校验。
>
> 我们发现了四件事——调度竞争是主因、温控是放大器、场景差异不能忽视、Hint Engine 能自适应。
>
> 核心贡献一句话：证明了以帧为中心、以依赖图为工具的方法，比传统的以 PID 为中心的方法，更有效地定位 Android 卡顿的根因。
>
> 未来方向中，sched_ext 最值得关注——它是 Linux 6.12 引入的可编程调度器框架，能让我们在不修改内核的情况下，把我们的 CriticalScore 评分直接注入调度决策。
>
> 谢谢大家，欢迎提问。"

---

> **时间总计：封面 1 + 背景 3 + 路线图 2 + 调研 2 + 架构 3 + 探针 3 + 场景 2 + 对齐 3 + 图构建 4 + 成果 4 + 游戏 2 + Step1 3 + Step2 3 + Step3 3 + 技术挑战 2 + 总结 2 = 42 分钟**
