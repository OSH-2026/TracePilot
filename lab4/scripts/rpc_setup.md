# RPC 分布式推理部署说明
# Lab 4 - OSH 2026

## 前置条件
- 至少 2 台机器，同一局域网内
- 每台机器编译 llama.cpp 时开启 RPC 支持

## 编译

### 主机（macOS/Linux, 有 GPU）
```bash
cd llama.cpp
cmake -B build-rpc -DGGML_METAL=ON -DGGML_RPC=ON    # macOS Metal
# 或
cmake -B build-rpc -DGGML_CUDA=ON -DGGML_RPC=ON      # Linux CUDA
cmake --build build-rpc --config Release -j 8
```

### 从机（WSL2 / Linux, CPU 或 GPU）
```bash
cd llama.cpp
cmake -B build-rpc -DGGML_RPC=ON                      # CPU only
# 或
cmake -B build-rpc -DGGML_CUDA=ON -DGGML_RPC=ON       # with CUDA
cmake --build build-rpc --config Release -j 16
```

## 部署步骤

### 0. [仅 WSL2/Win] 配置端口转发

如果从机使用 WSL2 (NAT 模式)，需将 Windows 端口转发到 WSL2：

```powershell
# 在 Windows PowerShell (管理员) 中执行
netsh interface portproxy add v4tov4 listenport=50052 listenaddress=0.0.0.0 connectport=50052 connectaddress=<WSL2_IP>

# 防火墙放行
netsh advfirewall firewall add rule name="llama-rpc-50052" dir=in action=allow protocol=TCP localport=50052
```

### 1. 从机启动 rpc-server
```bash
# 在从机（机器 B, C, ...）上执行
./build-rpc/bin/rpc-server -p 50052 --host 0.0.0.0
```

### 2. 主机启动 llama-cli（连接从机）
```bash
# 在主机（机器 A）上执行
./build-rpc/bin/llama-cli \
  -m ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  -p "<|im_start|>user\nHello<|im_end|>\n<|im_start|>assistant" \
  -n 100 --temp 0.7 \
  --rpc <从机IP>:50052
```

如果从机是 WSL2 模式，使用 **Windows 的局域网 IP**（而非 WSL2 内部 IP）。

### 3. 多从机（选做）
```bash
# 主机连接多台从机
./build-rpc/bin/llama-cli \
  -m ./models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  -p "Hello" -n 100 \
  --rpc 192.168.x.x:50052,192.168.y.y:50052
```


## 本实验实际部署拓扑

```
主机 (MacBook Air M5, Metal)         从机 (Windows 11 + WSL2 Ubuntu)
  ┌─────────────────┐                  ┌─────────────────────────────┐
  │ llama-cli        │ ─── RPC ──────→ │ Windows port 50052          │
  │ --rpc 114.214.x.x│    TCP:50052    │   ↓ portproxy                │
  └─────────────────┘                  │ WSL2 rpc-server :50052      │
                                       │   (CPU backend, 16线程)     │
                                       └─────────────────────────────┘
```

## 注意事项
- WSL2 NAT 模式下，外部无法直接访问 WSL2 的端口，必须用 Windows 端口转发
- 确保防火墙允许 50052 端口通信
- 各机器上的模型文件路径需一致
- 网络延迟会影响推理性能
- 建议先用 ping 测试机器间延迟
- 此功能目前仍在测试中，请谨慎使用