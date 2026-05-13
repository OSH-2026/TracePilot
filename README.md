# TracePilot — 基于 eBPF 的 Android 行为分析平台

## 团队信息

- **队名**：`TracePilot`
- **成员**：
  - 潘智勇
  - 李松茂
  - 邵晨轩
  - 贺小轩
  - 杨子皓

## 项目一句话

在 **Google Pixel 6a** 真机上，通过 **eBPF** 采集调度与进程间依赖相关事件，覆盖页面切换、信息流滚动、相机等多场景，经处理与分析提取用户及系统行为特征，为后续调度优化提供数据支撑。

---

## 背景与动机

移动端卡顿、响应慢等问题与 CPU 调度、线程唤醒、Binder 通信、锁等待等紧密相关。传统依赖人工调参或静态规则，难以随场景自适应。本项目走 **数据驱动** 路线：先可观测，再理解，后决策。

当前工作重点聚焦于 **观测与理解** 层——基于多场景的 eBPF 数据采集与行为分析。

---

## 采集场景与数据集

| 场景 | 数据路径 | 说明 |
|------|----------|------|
| 场景 | 数据路径 | 说明 |
|------|----------|------|
| 页面切换（QQ） | [ebpf/ebpf_data/QQ页面切换场景/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/QQ%E9%A1%B5%E9%9D%A2%E5%88%87%E6%8D%A2%E5%9C%BA%E6%99%AF) | QQ 聊天界面滑动、页面跳转等操作下的调度与帧数据 |
| 页面切换（基础版） | [ebpf/ebpf_data/页面切换-基础版数据/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/%E9%A1%B5%E9%9D%A2%E5%88%87%E6%8D%A2-%E5%9F%BA%E7%A1%80%E7%89%88%E6%95%B0%E6%8D%AE) | 基础页面切换场景的 perfetto trace 与事件记录 |
| 信息流滚动 | [ebpf/ebpf_data/feed_scroll/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/feed_scroll) | Chrome 信息流滚动场景的帧统计与线程分析 |
| 相机 | [ebpf/ebpf_data/camera/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/camera) | 相机启动与预览场景的行为特征 |

### 各场景数据类型

- **eBPF 原始事件**：sched_switch、Binder 事务、锁竞争等 ringbuf 输出
- **Perfetto trace**：用于帧边界对齐与 jank 标定
- **行为特征**：经聚合与特征工程提取的 CSV 特征表
- **分析报告**：场景级的行为分析、线程重要性排序、优化建议

---

## eBPF 探针

探针源码位于 [ebpf/src/](https://github.com/OSH-2026/TracePilot/tree/main/ebpf/src)，当前覆盖：

- `sched_switch` — 跟踪线程切换与运行时长
- Binder 相关 kprobe — 跟踪跨进程通信链路
- 锁竞争事件 — 识别等待与唤醒延迟
- 页面切换场景 eBPF 程序 — 位于 `page_turning/` 子目录
- **页面切换-基础版** 完整项目 — `页面切换-基础版.zip`，包含 eBPF 探针源码（`tracepilot.bpf.c`）、C 加载器、Perfetto 工具链、Python 分析脚本（行为分析、特征提取）、原始采集事件及分析报告

---

## 目标（做什么）

| 层级 | 内容 | 状态 |
|------|------|------|
| **观测** | eBPF 探针采集多场景调度与通信事件 | ✅ 已完成 |
| **理解（工作重点）** | 数据处理、行为分析、特征提取、报告生成 | ✅ 进行中 |
| **决策** | GBDT / Bandit 输出关键线程评分与策略候选 | 📅 后续 |
| **执行** | 用户态策略注入（亲和、优先级、uclamp 等） | 📅 后续 |
| **评估** | 与默认/传统策略对照，报告效果与开销 | 📅 后续 |

---

## 技术要点

- **实验机**：Pixel 6a（解锁 + root，支持自定义 eBPF 加载）
- **主数据流**：eBPF → ringbuf → 落盘 → 离线处理 → 特征工程 → 行为分析
- **辅助工具**：Perfetto 用于帧/jank 对齐与对照验证
- **分析框架**：Python 数据处理 + 行为特征提取 + 统计分析

---

## 交付物

- eBPF 探针源码与采集配置说明
- 多场景数据集（原始事件 + 特征表 + 分析报告）
- 行为分析工具链与报告生成脚本
- 实验报告：场景分析、特征重要性、优化建议

---

## 风险与前提

- 自定义 eBPF 依赖 **解锁与 root**，需排除运营商锁机器
- 多场景数据的标注与对齐依赖人工校验

---

# 项目进度

**注：** 有些刚开始的阶段没有会议记录，是因为没有进行相关的记录，不是该阶段没有进行会议

| 项目阶段 | 日期 | 项目进展 | 工作安排 |
|----------|------|----------|----------|
| **选题调研** | 3/9 ~ 3/15（第二周） | 研读往年项目 | 潘智勇：文件系统相关 贺小轩：rust改写相关 李松茂：任务调度相关 杨子皓，邵晨轩：rust改写 |
| **选题调研** | 3/16 ~ 3/22（第三周） | 开会通过自适应AI任务调度系统选题并向老师报告 | 李松茂：该方向的提出者；潘智勇：在树莓派上实现最小闭环并验证可行性并向老师汇报；贺小轩，杨子皓，邵晨轩：继续调研相关技术并完善报告 |
| **选题调研** | 3/23 ~ 3/29（第四周） | 由潘智勇向老师汇报后，答复为过于简单，很难做深。遂决定改变选题，将rust改写选题提交，被评价为过于简单。会议记录：https://github.com/OSH-2026/TracePilot/blob/main/minutes%20of%20meetings/3-28%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md| 贺小轩：提出rust改写方案 杨子皓：完善调研报告 潘智勇：向老师汇报 |
| **选题调研** | 3/30 ~ 4/3（第五周） | 提交六个选题，评价为四个深度不够，剩余两个可行性不高，开会决定继续调研。会议记录：https://github.com/OSH-2026/TracePilot/blob/main/minutes%20of%20meetings/4-1%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md | 李松茂：鸿蒙系统的LLM调优 贺小轩：鸿蒙异构内存 潘智勇：mini-VFS，fuse文件系统 杨子皓：rust改写NuttX的VFS 邵晨轩：AIOS的智能体操作系统 |
| **选题调研** | 4/3 ~ 4/5（第五周） | 潘智勇提交使用eBPF技术来优化linux调度器；邵晨轩提交面向AI agent的安全沙盒调研  老师认为潘智勇提出的使用eBPF技术方案可以，但是不要考虑linux，考虑鸿蒙或安卓，因为预调用对移动端帮助较大 | 潘智勇：调研了eBPF技术用于对linux的调度器优化 邵晨轩：调研了AIOS的相关技术和需求 |
| **选题调研** | 4/6 ~ 4/12（第六周） | 老师基本认可了在安卓系统的方案，但是数据来源有问题，虚拟环境体现不了真实用户数据，需要改为实体真机 | 李松茂，杨子皓：数据集调研 潘智勇，贺小轩：手机型号选择调研 |
| **立项** | 4/13 ~ 4/19（第七周） | 与老师对齐实验设备：pixel 6A(安卓16，root用magisk）并完成可行性报告。会议记录：https://github.com/OSH-2026/TracePilot/blob/main/minutes%20of%20meetings/4-13%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md | 潘智勇：购买真机并进行真机测试 邵晨轩，杨子皓：eBPF采集技术可行性报告 贺小轩：模型可行性报告 李松茂：将决策建议写入内核可行性报告 |
| **准备汇报** | 4/20 ~ 4/26（第八周） | 准备可行性汇报。会议记录：https://github.com/OSH-2026/TracePilot/blob/main/doc/minutes%20of%20meetings/4-21%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md | 调研：李松茂，贺小轩 实现一个特定场景下的数据采集和处理：邵晨轩，潘智勇，杨子皓 交叉编译环境的配置：潘智勇 |
| **初步工作** | 4/27 ~ 5/1（第九周） |完成交叉编译环境的搭建和对三个常见的场景进行eBPF信息采集和处理。页面切换场景：https://github.com/OSH-2026/TracePilot/tree/main/ebpf/src/page_turning ，对应数据处理报告：https://github.com/OSH-2026/TracePilot/blob/main/doc/report/behavior_analysis_report.md ；信息流滚动场景数据：https://github.com/OSH-2026/TracePilot/tree/main/ebpf/ebpf_data/feed_scroll ，对应数据处理报告：https://github.com/OSH-2026/TracePilot/blob/main/doc/report/feed_scroll_analysis_report.md | 中期汇报：潘智勇 |
| **数据进一步采集与处理** | 5/4 ~ 5/10（第十周） |对中期汇报得到的反馈进行调研和进一步工作，会议记录：https://github.com/OSH-2026/TracePilot/blob/main/doc/minutes%20of%20meetings/5-6%E4%BC%9A%E8%AE%AE%E8%AE%B0%E5%BD%95.md | 调研：李松茂，贺小轩 实现观测和处理：潘智勇，邵晨轩，杨子皓 |
