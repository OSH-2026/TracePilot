# Lab 4: llama.cpp 本地与分布式推理系统
---

## 提交文档

| 文档 | 说明 |
|------|------|
| [deployment.md](deployment.md) | 部署说明文档：性能指标、单机部署、RPC 部署、Ray 部署、拓扑图、测试脚本 |
| [testing.md](testing.md) | 性能测试与系统分析文档：指标测量、参数优化、输出质量评估、RPC 对比、量化对比 |
| [ray.md](ray.md) | Ray 多机批量推理任务说明：方案概述、部署、测试结果、分析、并发压力测试、异构节点分析 |

## 目录结构

```
lab4/
├── README.md                  # 本文件（索引）
├── deployment.md              # 部署说明文档
├── testing.md                 # 性能测试与系统分析文档
├── ray.md                     # Ray 选择性必做任务说明文档
├── scripts/                   # 实验脚本
│   ├── benchmark.sh
│   ├── test_prompts.sh
│   ├── ray_batch_inference.py
│   ├── rpc_setup.md
│   └── slave_setup_win.ps1
└── results/                   # 测试结果数据
    ├── benchmark_results.txt
    ├── prompts_output.txt
    ├── ray_batch_inference.txt
    ├── rpc_win_forward.txt
    ├── rpc_server_win_forward.log
    ├── concurrent_stress_test.txt
    ├── heterogeneous_analysis.txt
    ├── load_balance_result.txt
    ├── temp_comparison.txt
    └── screenshots/           # 结果截图
```
