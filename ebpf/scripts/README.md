# ebpf/scripts — 顶层脚本工具集

本目录存放项目级跨场景复用的 Python/Bash 脚本。

## 分析类脚本
| 脚本 | 功能 |
|------|------|
| `analyze_perfetto_cpu_freq_windows.py` | CPU 大小核帧窗口频率归因 |
| `analyze_perfetto_sched_windows.py` | 帧窗口内调度事件分析 |
| `parse_perfetto_frametimeline.py` | Perfetto FrameTimeline 帧提取 |
| `parse_surfaceflinger_latency.py` | SurfaceFlinger 延迟解析 |
| `export_trace_csv.py` | Trace 原始事件导出为 CSV |

## 采集类脚本
| 脚本 | 功能 |
|------|------|
| `collect_game_aligned.py` | 游戏场景对齐采集器 |
| `android_game_aligned_capture.sh` | 游戏场景一键采集部署 |
| `package_game_raw_data.py` | 游戏原始数据打包归档 |

## 工具类脚本
| 脚本 | 功能 |
|------|------|
| `extract_tracepilot_enhanced_events.py` | Enhanced 事件提取 |
| `build_tracepilot_offline_step_summary.py` | Step1/Step2 离线汇总 |
| `run_tracepilot_offline_device.py` | 离线设备端运行器 |
