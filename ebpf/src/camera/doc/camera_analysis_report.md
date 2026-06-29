# Google Camera 调度延迟深度分析报告

> **TracePilot — Frame-Centric Android Scheduling Assistant**
> 实验日期: 2026-06-29 | 设备: Pixel 6a (Android 15, Magisk root) | 目标: Google Camera v9.x

---

## 一、实验概述

### 1.1 采集配置

| 项目 | 配置 |
|------|------|
| 采集时长 | 30 秒 |
| eBPF 探针 | 13 个（12 成功加载, 1 跳过） |
| Ring Buffer | 主通道 32MB + IRQ 通道 4MB = 36MB |
| Perfetto 配置 | 128MB buffer, ftrace + atrace |
| 相机操作 | 拍照、切换模式（人像/普通/HDR） |

### 1.2 数据规模

| 数据源 | 事件量 | 说明 |
|--------|--------|------|
| 调度事件 (sched) | 2,051,704 | 含内核内计算的 runnable_delay |
| Binder/Futex/CPU/热控 | 452,572 | 含 binder 配对、futex 时长 |
| IRQ/SoftIRQ | 2,129,719 | 硬中断 + 软中断 |
| Perfetto 帧 | 16 jank 帧 | 总帧 896, jank 率 1.8% |

### 1.3 分析管线

```
eBPF 采集 (3 个 CSV, ~470 万行)
    ↓
帧对齐 (Perfetto FrameTimeline)
    ↓
多维聚合 (sched + binder + futex + IRQ + CPU + 热控 + 内存)
    ↓
关键路径图构建 (4 种边类型, 631 线程)
    ↓
根因归因 (6 信号: Sched/Binder/Futex/IRQ/SoftIRQ/环境)
    ↓
卡顿分类 (9 维度) → 调优建议
```

---

## 二、核心发现

### 2.1 卡顿根因: 100% CPU 调度竞争

全部 16 个 jank 帧的根因均为 **CPU Scheduling Contention**，每帧阻塞时延 200ms-632ms。

| 帧 ID | 总阻塞 | 调度排队 | IRQ | SoftIRQ | 环境 |
|--------|--------|---------|------|---------|------|
| 3070235 | 547ms | 502ms (92%) | 23ms | 5ms | CPU 1849MHz |
| 3081143 | 632ms | 589ms (93%) | 28ms | 3.5ms | CPU 1959MHz |
| 3081187 | 547ms | 533ms (97%) | 7ms | 1.3ms | CPU 2402MHz |
| 3078159 | 526ms | 498ms (95%) | 11ms | 1ms | — |
| 3078349 | 455ms | 435ms (96%) | 9ms | 1.5ms | CPU 2401MHz |

**关键结论**:
- Binder IPC 延迟为 **0ns**（全部为 0，说明跨进程通信不是瓶颈）
- IRQ 开销 5-28ms/帧，仅占 2-5%，非主要因素
- CPU 频率 1700-2750MHz，无降频（温度正常）
- **纯粹是太多线程竞争 CPU 时间**

### 2.2 最大 CPU 竞争者

| 排名 | 线程 | 角色 | 延迟总计 | 出现帧数 | 说明 |
|:----:|------|------|----------|:-------:|------|
| 1 | `s.nexuslauncher` | 系统桌面 | 349ms | 15/16 | **Pixel 桌面是最大干扰源** |
| 2 | `GcamTasks:2 p8` | GCam 处理 | 129ms | 7/16 | GCam 图像处理线程池 |
| 3 | `GcamTasks:4 p8` | GCam 处理 | 116ms | 6/16 | GCam 图像处理线程池 |
| 4 | `GcamTasks:3 p8` | GCam 处理 | 110ms | 5/16 | GCam 图像处理线程池 |
| 5 | `GcamTasks:1 p8` | GCam 处理 | 109ms | 6/16 | GCam 图像处理线程池 |
| 6 | `GcamTasks:0 p8` | GCam 处理 | 102ms | 5/16 | GCam 图像处理线程池 |
| 7 | `CriticalPath` | 系统进程 | 82ms | 4/16 | 系统关键路径 |
| 8 | `FinishThread` | 系统线程 | 77ms | 3/16 | 系统清理线程 |
| 9 | `mali_jd_thread` | GPU 驱动 | 77ms | 4/16 | Mali GPU 作业调度 |
| 10 | `MergeThread` | 系统线程 | 68ms | 3/16 | 系统合并线程 |

### 2.3 全局 CriticalScore 排名

| 排名 | TID | 线程 | 角色 | Score | 帧覆盖 | 说明 |
|:----:|:---:|------|------|:-----:|:------:|------|
| 1 | 4475 | `id.GoogleCamera` | UI Thread | 3.24 | 100% | App 主线程 |
| 2 | 11547 | `RenderThread` | RenderThread | 2.92 | 100% | 渲染管线 |
| 3 | 555 | `mali_jd_thread` | GPU Worker | 2.69 | 100% | GPU 驱动 |
| 4 | 6482 | `binder:1261_1D` | Binder RPC | 2.51 | 31% | 单帧 P95=14.2ms |
| 5 | 27108 | `s.nexuslauncher` | UnknownWorker | 2.44 | 100% | Pixel 桌面 |
| 6 | 11892 | `RenderThread` | RenderThread | 2.40 | 62% | 多实例渲染 |
| 7 | 644 | `binder:1261_18` | Binder RPC | 2.23 | 100% | Binder 线程 |
| 8 | 1307 | `binder:1261_1D` | Binder RPC | 2.14 | 100% | Binder 线程 |
| 9 | 16106 | `binder:4475_B` | Binder RPC | 2.10 | 69% | 相机 Binder |
| 10 | 4515 | `GcaGeneric-2` | CameraThread | 2.08 | 94% | GCam 通用 |

### 2.4 卡顿分类

| 分类 | 帧数 | 占比 |
|------|:----:|:----:|
| **Futex 锁竞争** | 16 | **100%** |
| CPU 降频 | 4 | 36% |
| Sched 调度 | 1 | 9% |

> 虽然每帧都有大量 Futex 等待（最高 1,518 次），但实际阻塞时间主要由调度排队贡献。Futex 竞争是"症状"而非"根因"。

---

## 三、关键路径分析

每帧的关键路径均为**单跳**模式：`Frame ← Thread (RUNNABLE_WAIT)`，未形成多跳依赖链。这是因为 Binder 事件未落入 jank 帧窗口内。

代表性关键路径:

```
Frame 3081143 (总阻塞 632ms):
  ┌────────────────────────────┐
  │  Frame (App Deadline Missed) │
  └────────────────────────────┘
         ↑ RUNNABLE_WAIT 60.6ms
  ┌──────────────────┐
  │ s.nexuslauncher   │  ← 系统桌面 (最大干扰)
  │ TID:27108         │
  └──────────────────┘
         ↑ RUNNABLE_WAIT 41.1ms
  ┌──────────────────┐
  │ GcamTasks:4 p8    │  ← GCam 图像处理
  │ TID:11938         │
  └──────────────────┘
         ↑ RUNNABLE_WAIT 34.7ms
  ┌──────────────────┐
  │ FinishThread      │  ← 系统清理
  │ TID:11627         │
  └──────────────────┘
```

---

## 四、相机管线阶段分析

Google Camera (Pixel) 使用私有 GSLCamera 栈，标准 `camera`/`hal` atrace 类别不产生数据。通过 Perfetto SurfaceView 切片间接捕获到 6 个 FaceDetection 阶段（总计 78ms）:

| 线程 | 延迟 | 说明 |
|------|------|------|
| `lowpool[772]` | 61.5ms | 系统线程池（最大单一延迟源） |
| `GcamTasks:3 p8` | 46.1ms | GCam 处理 |
| `YUV_420_888w144` | 45.3ms | YUV 格式转换 |
| `GcamTasks:2 p8` | 36.8ms | GCam 处理 |
| `GcamTasks:0 p8` | 34.3ms | GCam 处理 |

- Futex: 1,518 WAIT / 1,514 WAKE（GCam 内部锁竞争激烈）
- CPU: 2630MHz（稳定，无降频）

---

## 五、调优建议

### 5.1 即时可行

| 优先级 | 建议 | 预期效果 |
|:------:|------|----------|
| 🔴 | **限制后台进程 CPU 占用** — `s.nexuslauncher`(Pixel 桌面) 是 15/16 帧的最大干扰源 | 减少 ~20% 调度竞争 |
| 🔴 | **适当提升 GCam 线程优先级** — GcamTasks:0-4 在 13/16 帧中高延迟 | 降低拍照延迟感知 |
| 🟡 | **GCam 内部锁优化** — GcaGeneric 系列线程 Futex 等待 >100 次/帧 | 减少锁竞争 |
| 🟡 | **Mali GPU 调度关注** — `mali_jd_thread` 100% 帧覆盖 + P95=4.4ms | 考虑 GPU 驱动优先级调整 |

### 5.2 实验验证方向

- **对比实验**: 拍照前清理后台（`adb shell am force-stop` 关闭非必要应用）→ 对比 jank 帧数和延迟分布
- **Binder 窗口扩大**: 将帧前后 margin 从 2ms 扩至 10ms → 可能捕获跨帧 Binder 依赖边
- **不同相机 App**: 使用 AOSP Camera 对比 Google Camera → 验证 GCam 的私有栈是否引入额外调度开销

---

## 六、附录: 技术指标

| 指标 | 数值 |
|------|------|
| eBPF 探针 | 13 (12 成功) |
| Ring Buffer 总容量 | 36MB |
| 采集数据总量 | ~470 万行 CSV |
| 分析帧数 | 16 |
| 评分线程数 | 631 |
| 图节点类型 | 3 (Frame/Thread/Resource) |
| 图边类型 | 4 (RUNNABLE_WAIT/BINDER_CALL/FUTEX_WAIT/SYSTEM_OVERHEAD/RESOURCE_STALL) |
| CriticalScore 维度 | 8 + 1 (温度惩罚) |
| 根因归因信号 | 6 (Sched/Binder/Futex/IRQ/SoftIRQ/环境) |
| 卡顿分类维度 | 9 |

---

