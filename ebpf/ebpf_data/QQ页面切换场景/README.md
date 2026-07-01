# QQ页面切换场景 — 行为特征数据

## 采集信息
- **目标应用**: com.tencent.mobileqq (QQ)
- **场景**: QQ 聊天界面滑动、页面跳转等操作

## 主要文件
| 文件 | 说明 |
|------|------|
| `behavior_features.csv` | 按秒级窗口+包名聚合的特征表 (578行) |
| `behavior_analysis_summary.txt` | 行为分析摘要 |
| `behavior_analysis_top_packages.csv` | Top 包名统计 |
| `page_turning_events.log` | 页面切换原始事件日志 |
| `page_turning_raw.log` | 页面切换原始二进制日志 |

## 分析方法
基于 `event_cnt` 序列计算 P90 阈值标注突发窗 (`burst=1`)，按 `pkg` 聚合得到各包事件总量/峰值/均值/中位数，用于判断系统侧并发干扰。
