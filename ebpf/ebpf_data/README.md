# ebpf/ebpf_data — 采集数据目录

本目录存放各场景的 eBPF 采集原始数据与后处理产物。

## 场景子目录

| 目录 | 场景 | 数据量 | 主要文件 |
|------|------|--------|---------|
| `feed_scroll/` | Chrome 信息流滚动 | 261 万事件/34s | CSV/JSONL/framestats |
| `camera/` | Google Camera 拍照 | camera.txt + CSV | 行为特征 + 原始日志 |
| `QQ页面切换场景/` | QQ 页面切换 | CSV + 汇总 | behavior_features.csv |
| `页面切换-基础版数据/` | 页面切换基础版 | 690MB events.bin | Perfetto trace + frames.txt |
| `页面切换-视频浏览数据/` | 页面切换+视频增强版 | 451~690MB | page_switch/ + video/ + Task17/ |
| `game_sgame/` | 王者荣耀对局 | 2.1GB/60s | FrameTimeline + Step1/2 + 图拓扑 |

## 数据格式

- `.jsonl.gz` — eBPF 原始事件流 (gzip 压缩)
- `.csv` — 聚合后的秒级/线程级/特征表
- `.perfetto-trace` — Perfetto 帧采集文件
- `.txt` — 帧统计/分析辅助文件
- `.zip` — 打包归档数据包
