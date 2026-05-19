# Android 交互级调度延迟分析系统 - 阶段性工作总结 5.19

## 1. 背景
本项目旨在针对相机功能，打造一个离线的 **帧对齐、依赖感知（Frame-aligned, dependency-aware）的 Android 调度辅助分析工具**。
。

## 2. 系统数据流与架构闭环

打通了从 **“系统采集 -> 特征提取 -> 数据聚合映射 -> 结构化分析输出”** 的全栈闭环：

1. **宏观视点：定义 Ground Truth (Perfetto)**
   - 抓取包含 UI 交互卡顿的 Perfetto Trace。
   - 使用 SQL 分析 `actual_frame_timeline_slice` 表，精确定位发生 `App Deadline Missed`（即真实导致画帧超时的严重掉帧）的具体纳秒级时间窗口 (`window_start_ns` 到 `window_end_ns`)。
   - 提取目标应用的静态线程血缘关系（ThreadKey 映射表）。
   - **输出**: `perfetto/output/ebpf_target_windows.json`

2. **微观视点：探测内核调度边界 (eBPF)**
   - 编译加载基于 libbpf 的 eBPF C 骨架探测程序。
   - 挂载挂载点 `tracepoint/sched/sched_switch` 与 `tracepoint/sched/sched_wakeup`。
   - 完全使用 BootTime (`bpf_ktime_get_boot_ns`) 进行时间戳记录，以与 Perfetto 绝对时间对齐。
   - 捕获各线程被唤醒以及实际上 CPU 运行的时间戳，通过 RingBuffer 推送至用户态。
   - **输出**: `ebpf/sched_events.csv`

3. **融合与推演：调度延迟聚合器 (Python Analyzer)**
   - 加载上述两方的输出产物。基于 Perfetto 的时间窗口框定 eBPF 的海量底层事件。
   - 处理 TID 复用与生命周期对准问题，计算 `Runnable Delay`（线程就绪但未获得 CPU 的时长）与 `Actual Run`（线程真正在 CPU 上的执行时长）。
   - 基于线程名进行“角色启发式识别”（分为 UI Thread, RenderThread, Binder RPC 等）。
   - **输出**: 详细的命令行对线报告，与供 Hint Engine 消费的结构化文件 `delay_analysis_result.json`。

## 3. 已攻克的关键技术难点

在调试并跑通这条链路的过程中，我们解决了一系列非常具有代表性的底层坑：

- **内核进程名 15 字符截断陷阱**
  - **问题**: 用户输入的完整包名（如 `com.google.android.GoogleCamera`），在内核 `task_struct->comm` 字段中会被截断成 `com.google.andr`。导致根据关键字匹配进程永远找不到对应的 UI 进程（反而匹配到了 HAL 层的服务）。
  - **解决**: 调整视角，以果推因。直接在 Perfetto SQLite 表中扫描具备 `App Deadline Missed` 标签的 UPID，反向锁定当前拥有焦点的 Janky 应用，完美绕过字符串截断。
- **PID 复用引发的“幽灵线程”乱入**
  - **问题**: 仅凭 TID 匹配，容易在时间流中遇到 PID 被回收后分配给别的进程的情况，导致分析错乱。
  - **解决**: 引入以 UID 与 TGID 为中心的“Identity Layer Filtering”，利用 Perfetto 的静态血缘作为字典，剔除非目标 UID 的杂音线程。
- **高频事件下的 eBPF I/O 阻塞丢失数据**
  - **问题**: `sched_events.csv` 只有头一两毫秒的事件，由于 `while(1)` 读取轮询或用户态退出的逻辑缺陷，导致大规模调度事件因底层缓冲未 `fflush` 或者提早 `fclose` 而丢失。
  - **解决**: 修正了 C 用户态代码的退出生命周期设计，通过实时 `fflush()` 将 RingBuffer 的高频数据落盘，保证 trace 不会由于瞬时突发而断片。
- **异构系统的时钟域对齐**
  - 使用 `bpf_ktime_get_boot_ns()` 成功保证了 eBPF 输出的时间戳可以和 Perfetto 中采用的 BootTime 基准相对齐，使微观事件能够无缝嵌入宏观掉帧窗口中。

## 4. 当前标准操作工作流 (SOP)

要在真机上执行一次完整的分析，目前的流程非常顺滑：

1. **开始 eBPF 记录**: 在 Android 终端启动后台采集程序 `./camera_ebpf`。
2. **收集卡顿 Trace**: 开启设备上的 Perfetto（或通过命令行），用户在 App 上执行产生卡顿的操作。
3. **结束记录**: 停止 Perfetto 形成 `.perfetto` 文件；`Ctrl+C` 中止 eBPF 程序生成 `sched_events.csv`。
4. **生成卡顿窗口**: 借由宿主机执行 `python3 perfetto/parse_trace.py trace.perfetto <app_name>` 生成窗口 JSON。
5. **计算调度延迟**: 借由宿主机执行 `python3 ebpf/analyze_delays.py`。
6. **读取成果**: 查看生成的 `delay_analysis_result.json` 获取精确到各个核心线程的毫秒级等待延迟。（当前hint仅以runnable_delay_ns作为依据）

相关指令：
```
adb push ./ebpf/camera_ebpf_android /data/local/tmp/
adb shell 
su
cd /data/local/tmp/
chmod +x camera_ebpf_android
./camera_ebpf_android


adb push perfetto_camera.pbtx /data/local/tmp/
adb shell "cat /data/local/tmp/perfetto_camera.pbtx | perfetto --txt -c - -o /data/misc/perfetto-traces/camera_jank.perfetto"

adb pull /data/misc/perfetto-traces/camera_jank.perfetto ./perfetto/
adb pull /data/local/tmp/sched_events.csv ./ebpf/

python3 parse_trace.py camera_jank.perfetto com.google.android.GoogleCamera

cd ../ebpf
python3 analyze_delays.py --json ../perfetto/output/ebpf_target_windows.json --csv sched_events.csv
```

## 5. 后续方向
- 目前收集全量sched_switch等事件对手机负荷过大，会感觉到手机明显发烫，同时30秒时间产生上百MB的结果，这一点需要后续商讨和优化
- 采集更多事件，如binder，futex相关
- 构建图特征