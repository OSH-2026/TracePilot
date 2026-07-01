# feed_scroll — Chrome 信息流滚动场景数据

## 采集信息
- **设备**: Pixel 6a / Android 16 / Magisk root
- **场景**: 打开 Chrome 信息流测试页面，连续向下滚动 30 次
- **采集时长**: 34.2 秒
- **原始事件数**: 2,614,133

## 事件类型
| 事件 | 数量 |
|------|------|
| `sched_switch` | 1,274,776 |
| `sched_waking` | 654,062 |
| `sched_wakeup` | 654,056 |
| `cpu_frequency` | 31,239 |

## 主要文件
| 文件 | 说明 |
|------|------|
| `chrome_scroll_topdown_framestats.txt` | 帧统计辅助数据 |
| `chrome_scroll_topdown_summary.json` | 总体指标摘要 |
| `feed_scroll_events_by_second.csv` | 秒级聚合事件统计 (35行) |
| `feed_scroll_threads_summary.csv` | Chrome 相关线程聚合指标 (34行) |
| `feed_scroll_supplement_*` | 补充 ftrace 采集与线程分类评分 |
