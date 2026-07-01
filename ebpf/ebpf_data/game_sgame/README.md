# game_sgame — 王者荣耀游戏场景数据

## 采集信息
- **目标应用**: com.tencent.tmgp.sgame (王者荣耀)
- **采集方式**: TracePilot eBPF 调度事件 + ftrace 补充事件

## 样本
| 样本 | 场景 | 采集窗口 |
|------|------|------|
| `game_play_sgame_20260601_1200` | 登录后短窗口 | 24.792 s |
| `game_match_sgame_20260601_120627` | 对局内操作窗口 | 59.178 s |
| `game_match_sgame_20260607_170754` | Step1/Step2 完整分析 | 60 s |

## 主要分析产物
- Perfetto FrameTimeline 帧数据
- eBPF/TracePilot 调度事件
- Binder/Futex 候选图
- CPU frequency big-little 帧窗口归因
- 图拓扑 (59K 节点) + 子图 DOT 导出
- Step1/Step2 汇总 JSON + Hint
- 原始数据回放包 (zip + manifest)

详见 [SUBMISSION.md](SUBMISSION.md) 和 [分析报告](../../../doc/report/sgame_gameplay_analysis_report.md)。
