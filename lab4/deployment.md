# Lab 4: llama.cpp 本地及分布式推理系统
---

# 部署说明文档

## 1. 性能指标列表

| 编号 | 指标名称 | 说明 | 合理性 |
|------|---------|------|--------|
| 1 | **输出速度 (Generation Speed)** | 模型生成文本的速率，单位 tokens/s | 直接影响用户体验，是衡量推理性能最核心的指标 |
| 2 | **首 Token 返回延迟 (TTFT)** | 从输入 prompt 到生成第一个 token 的时间 | 反映系统响应速度，对话场景下尤为重要 |
| 3 | **Prompt 处理速度 (Prompt Processing)** | 模型处理输入 prompt 的速率，单位 tokens/s | 影响 TTFT 和整体吞吐，反映模型对输入的编码效率 |
| 4 | **模型加载时间 (Load Time)** | 从启动到模型就绪的时间 | 影响服务部署和重启效率，大规模部署时需关注 |
| 5 | **内存占用 (Memory Usage)** | 模型运行时占用的 RAM/VRAM | 决定模型能否在给定硬件上运行，对资源受限设备至关重要 |
| 6 | **困惑度 (Perplexity)** | 语言模型对测试集的预测能力 | 衡量模型输出质量的核心指标，值越低表示预测越准确 |

---

## 2. 单机部署与环境记录

### 2.1 硬件环境

| 项目 | 内容 |
|------|------|
| 型号 | MacBook Air |
| 芯片 | Apple M5 |
| CPU 核心数 | 10 核 |
| 内存 | 16 GB |
| GPU | Apple M5 (统一内存, Metal 支持) |
| 存储 | SSD |

### 2.2 软件环境

| 项目 | 内容 |
|------|------|
| 操作系统 | macOS 26.5 (Darwin 25.5.0) |
| 内核 | arm64 |
| 编译器 | Apple Clang 21.0.0 |
| llama.cpp 版本 | v0.13.0 (build 9514, commit 21444c822) |
| 编译后端 | Metal + BLAS |
| 构建系统 | CMake 4.3.2 |

### 2.3 从机（Slave）硬件环境

| 项目 | 内容 |
|------|------|
| 型号 | 联想拯救者 |
| CPU | AMD Ryzen 7 7840H (8核/16线程) |
| 内存 | 16 GB |
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU (8GB GDDR6) |
| 存储 | SSD |

### 2.4 从机（Slave）软件环境

| 项目 | 内容 |
|------|------|
| 操作系统 | Windows 11 64-bit + WSL2 Ubuntu 24.04 |
| WSL2 内核 | 6.6.87.2-microsoft-standard-WSL2 |
| llama.cpp 编译后端 | CUDA (ggml-cuda, sm_89) + CPU + RPC |
| GPU 驱动 | NVIDIA 560.94, CUDA 12.6 |
| 构建系统 | CMake 3.28.3 + GCC 13.3.0 + NVCC 12.6.85 |
| WSL2 Tailscale IP | `100.107.8.1` |
| Windows Tailscale IP | `100.69.233.118` |

### 2.5 模型信息

| 项目 | 内容 |
|------|------|
| 模型名称 | Qwen2.5-1.5B-Instruct |
| 量化格式 | Q4_K_M (4-bit K-quant, Medium) |
| 参数量 | 1.78B |
| 文件大小 | 1.04 GiB |
| 来源 | Hugging Face: Qwen/Qwen2.5-1.5B-Instruct-GGUF |

### 2.6 部署方式

编译安装:
```bash
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build -DGGML_METAL=ON
cmake --build build --config Release -j 8
```

运行命令:
```bash
# 交互模式
./build/bin/llama-cli -m ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf

# 一次性推理
./build/bin/llama-cli -m ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  -p "<|im_start|>user\nHello<|im_end|>\n<|im_start|>assistant" \
  -n 100 --temp 0.7

# 性能基准测试
./build/bin/llama-bench -m ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  -p 512 -n 128 -t 4
```

---
## 6. RPC 分布式推理

### 6.1 环境准备

RPC 分布式推理需要至少 2 台机器之间网络互通（同一局域网或 Tailscale/ZeroTier 组网）。以下为部署方案:

**主机编译 (macOS, Metal + RPC):**
```bash
cd llama.cpp
cmake -B build-rpc -DGGML_METAL=ON -DGGML_RPC=ON
cmake --build build-rpc --config Release -j 8
```

**从机编译 (WSL2 Ubuntu, CUDA + RPC):**
```bash
cd llama.cpp
cmake -B build-cuda -DGGML_CUDA=ON -DGGML_RPC=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build-cuda --config Release -j 16
```

**从机启动 (WSL2, CUDA 加速):**
```bash
# 启动 rpc-server (CUDA 后端)
./build-cuda/bin/rpc-server -p 50052 --host 0.0.0.0

# 启动 llama-server (GPU offload)
./build-cuda/bin/llama-server -m ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --port 8080 --host 0.0.0.0 --threads 8 -c 2048 -ngl 999
```

**主机启动 (macOS):**
```bash
./build-rpc/bin/llama-server \
  -m ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --host 0.0.0.0 --port 8083 \
  --rpc <从机Tailscale_IP>:50052
```

### 6.2 部署命令记录

| 步骤 | 机器 | 命令 |
|------|------|------|
| 1 | 主机(Mac) | `cmake -B build-rpc -DGGML_METAL=ON -DGGML_RPC=ON && cmake --build build-rpc -j 8` |
| 2 | 从机(WSL2) | `cmake -B build-cuda -DGGML_CUDA=ON -DGGML_RPC=ON && cmake --build build-cuda -j 16` |
| 3 | 从机(WSL2) | `CUDA Toolkit 12.6 安装 + nvidia-smi 验证驱动` |
| 4 | 从机(WSL2) | `export PATH=/usr/local/cuda-12.6/bin:$PATH && ./build-cuda/bin/rpc-server --host 0.0.0.0 --port 50052` |
| 5 | 从机(Windows) | `netsh interface portproxy add v4tov4 listenport=50052 listenaddress=0.0.0.0 connectport=50052 connectaddress=172.x.x.x`（WSL2 IP，端口转发） |
| 6 | 主机(Mac) | `./build-rpc/bin/llama-server -m model.gguf --host 0.0.0.0 --port 8083 --rpc 100.69.233.118:50052`（`llama-cli --rpc` 该版本有 bug，改用等效的 `llama-server --rpc`） |

### 6.3 网络环境

| 项目 | 内容 |
|------|------|
| 网络类型 | Tailscale 虚拟局域网 (WireGuard VPN) |
| 拓扑 | 主机(MacBook Air M5, 100.101.168.109) ↔ 从机(Windows 100.69.233.118 → WSL2 rpc-server) |
| 主机 Tailscale IP | 100.101.168.109 |
| 从机 Windows Tailscale IP | 100.69.233.118 |
| 从机 WSL2 Tailscale IP | 100.107.8.1 |
| RPC 服务端口 | 50052 |
| RTT (主机↔从机 Windows) | ~10 ms (Tailscale 直连) |
| 数据通路 | Mac → Tailscale → Windows → netsh端口转发 → WSL2 rpc-server (CUDA) |

### 6.4 集群拓扑图

```
                    Mac M5 (100.101.168.109)
            ┌───────────────────────────────────┐
            │  Ray Head (:6379)                 │
            │  llama-server (:8080, Metal)      │
            │  ray_batch_inference.py           │
            └──────────┬────────────────────────┘
                       │
                   Tailscale (WireGuard, ~10ms RTT)
                       │
            ┌──────────▼────────────────────────┐
            │  Legion - Windows 11              │
            │  Tailscale IP: 100.69.233.118     │
            │  netsh portproxy (:8080/:50052)   │
            │  ┌────────────────────────────┐   │
            │  │  WSL2 Ubuntu 24.04         │   │
            │  │  llama-server (:8080)      │   │
            │  │  rpc-server (:50052)       │   │
            │  │  RTX 4060 CUDA (sm_89)    │   │
            │  └────────────────────────────┘   │
            └───────────────────────────────────┘
```

RPC 数据通路: Mac → Tailscale → Windows netsh → WSL2 rpc-server (CUDA)
Ray 数据通路: Mac Ray Head → 分发 prompt → 各节点 llama-server HTTP API

---
## 9. 选做：Ray 多机批量推理

### 9.1 方案概述

使用 Ray 框架将 20 个推理任务分发到多台 llama-server 节点并行执行，对比串行与并行的吞吐量差异。

**架构:**
- Ray Head: Mac (Apple M5, Tailscale IP: 100.101.168.109)
- Server 1: Mac 本地 llama-server (Metal, port 8080)
- Server 2: Legion 本地 llama-server (RTX 4060 CUDA, via Windows Tailscale 100.69.233.118:8080)

### 9.2 部署步骤

| 步骤 | 机器 | 命令 |
|------|------|------|
| 1 | 主机(Mac) | `ray start --head --port=6379 --node-ip-address=100.101.168.109` |
| 2 | 主机(Mac) | `./build-rpc/bin/llama-server -m model.gguf --host 0.0.0.0 --port 8080` |
| 3 | 从机(WSL2) | `./build-cuda/bin/llama-server -m model.gguf --host 0.0.0.0 --port 8080 --threads 8 -c 2048 -ngl 999` |
| 4 | 从机(Windows) | `netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=8080 connectaddress=<WSL2_IP>` |
| 5 | 主机(Mac) | `python3 ray_batch_inference.py --server1 http://127.0.0.1:8080 --server2 http://100.69.233.118:8080` |

## 附录 A: 测试脚本

### benchmark.sh
```bash
#!/bin/bash
# llama.cpp benchmark script

MODEL="models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
BIN="./build/bin"

# Baseline
$BIN/llama-bench -m $MODEL -p 512 -n 128 -t 4

# Compare threads
$BIN/llama-bench -m $MODEL -p 512 -n 128 -t 4,8,10

# Compare batch sizes
$BIN/llama-bench -m $MODEL -p 512 -n 128 -t 4 -b 128,256,512
```

### prompts.txt
```
请介绍一下大语言模型（LLM）的原理和应用。
请用一句话总结以下内容：'Transformer 架构由 Vaswani 等人在 2017 年提出...'
请解释以下 Python 代码的功能：
def fibonacci(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
如果 3 只猫 3 分钟捉 3 只老鼠，那么 100 只猫捉 100 只老鼠需要多少分钟？
在操作系统中，什么是上下文切换？它为什么会带来性能开销？
```

### Prompt 数据集说明

Ray 批量推理使用的 20 个 prompt 覆盖以下 6 类任务:

| 类别 | 数量 | 示例 |
|------|------|------|
| 中文知识问答 | 4 | LLM 原理、量子计算、Docker、RESTful API |
| 技术概念解释 | 5 | 虚拟内存、死锁、上下文切换、GPU 加速、缓存 |
| 代码解释 | 2 | Python 斐波那契函数、C vs Python 对比 |
| 逻辑推理 | 2 | 猫捉老鼠、TCP 三次握手 |
| 摘要/总结 | 2 | Transformer 架构总结、排序算法对比 |
| 系统/网络 | 5 | HTTPS、进程线程、编译器解释器、RAID、TCP |

选择依据: 覆盖多种任务类型和输出长度（短回答 ~50 tokens 到长解释 ~150 tokens），避免单一任务导致的性能偏差。prompt 来源包括课程知识、编程常见问题和通用技术问答。

