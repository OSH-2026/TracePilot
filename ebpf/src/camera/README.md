# Camera Scheduling Analyzer

基于 eBPF + Perfetto 的 Android 相机调度延迟分析工具。帧对齐、依赖感知，从"谁慢了"到"为什么慢了"。

## 架构

```
Perfetto (帧对齐)  +  eBPF (5+2 tracepoints)
        │                    │
        ▼                    ▼
 ebpf_target_windows.json   sched/binder/futex/cpu_freq/thermal CSV
        │                    │
        └────────┬───────────┘
                 ▼
         analyze_delays.py      (延迟聚合 + Binder配对 + Futex统计)
                 │
        ┌────────┼────────┐
        ▼        ▼        ▼
 critical_path.py   root_cause.py   safe_hint_engine.py
 (DAG+评分)         (延迟归因)       (调优配置+shell脚本)
        │            │               │
        ▼            ▼               ▼
  critical_path.json  root_cause.json  tuning_profile.json + apply_tuning.sh
        │            │
        └─────┬──────┘
              ▼
      generate_report.py  →  report_*.md
```

## 快速开始

```bash
# 全自动 (编译→推送→采集→拉取→分析→报告)
python auto_run.py

# 仅重跑分析 (已有 CSV)
python auto_run.py --only-analyze
```

## eBPF 探针

| 探针 | 类型 | 用途 |
|------|------|------|
| `sched/sched_switch` | sched | 线程切换 + Runnable Delay |
| `sched/sched_wakeup` | sched | 唤醒时间戳 |
| `binder/binder_transaction` | binder | IPC 调用发起 |
| `binder/binder_transaction_received` | binder | IPC 调用接收 |
| `raw_syscalls/sys_enter` | futex | FUTEX_WAIT/WAKE (id==98) |
| `power/cpu_frequency` | cpu | CPU 频率变化 |
| `thermal/thermal_temperature` | thermal | 温度检测 |

## 输出文件

```
output/
├── raw/
│   ├── sched_events.csv          # eBPF 调度事件
│   └── binder_futex_events.csv   # eBPF binder/futex/cpu_freq/thermal
├── analysis/
│   ├── ebpf_target_windows.json  # Perfetto 帧窗口
│   ├── delay_analysis_result.json
│   ├── critical_path_graph.json
│   ├── root_cause_analysis.json
│   ├── tuning_profile.json       # 调优配置
│   └── apply_tuning.sh           # 可部署的 shell 脚本
└── reports/
    └── report_*.md               # 最终报告
```

## 报告内容

| 章节 | 内容 |
|------|------|
| 一、Top-K | CriticalScore 8维评分排名 |
| 二、Binder | IPC 依赖边 + 延迟 |
| 三、Futex | 锁等待/唤醒统计 |
| 四、逐帧分析 | 每帧 sched/binder/futex/CPU频率 |
| 五、关键路径 | TOP-3 DAG 阻塞链 |
| 六、归因分析 | 调度竞争 vs Binder vs Futex 分解 |
| 七、总结 | 首要关注线程 + 建议 |

## 手动命令

```bash
# 编译 eBPF (仅改 C 代码后需要)
cd ebpf && make

# 手动 Perfetto
adb push perfetto/perfetto_camera.pbtx /data/local/tmp/
adb shell "cat /data/local/tmp/perfetto_camera.pbtx | perfetto --txt -c - -o /data/misc/perfetto-traces/camera_jank.perfetto"

# 手动 eBPF (Perfetto 先启动!)
adb push ebpf/build/camera_ebpf_android /data/local/tmp/
adb shell su -c "/data/local/tmp/camera_ebpf_android -q -u 10162"

# 拉取数据
adb pull /data/misc/perfetto-traces/camera_jank.perfetto ./perfetto/
adb pull /data/local/tmp/sched_events.csv ./output/raw/
adb pull /data/local/tmp/binder_futex_events.csv ./output/raw/

# PC 端分析
cd perfetto && python3 parse_trace.py camera_jank.perfetto com.google.android.GoogleCamera
cd ../ebpf
python3 analyze_delays.py --json ../output/analysis/ebpf_target_windows.json --csv ../output/raw/sched_events.csv --binder ../output/raw/binder_futex_events.csv
python3 root_cause.py
python3 generate_report.py
```

## 调优部署

```bash
# 生成调优配置
cd ebpf && python3 safe_hint_engine.py

# 临时应用 (进程重启失效)
adb push output/analysis/apply_tuning.sh /sdcard/ && adb shell sh /sdcard/apply_tuning.sh

# 持久化 (Magisk)
adb shell su -c 'cp /sdcard/apply_tuning.sh /data/adb/service.d/'
```

## 设备要求

- Pixel 6a (kernel 5.10, arm64)
- Root (su)
- Perfetto 已安装
- WSL Ubuntu (编译 arm64) + Windows adb
