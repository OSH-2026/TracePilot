<# 
.SYNOPSIS
    Lab 4 - 从机 (Windows Slave) 一键部署脚本
.DESCRIPTION
    在 Windows 从机上通过 WSL2 启动 llama.cpp RPC server，
    配置 CUDA 后端并开放指定端口供 Mac 主机调用。
.NOTES
    要求：WSL2 Ubuntu 24.04 + NVIDIA GPU + CUDA 12.6
    在 Windows PowerShell (管理员) 中运行
#>

$WSL2_USER="pzy"
$WSL2_TAILSCALE="100.107.8.1"     # WSL2 Tailscale IP (推荐)
$WSL2_NAT_IP="172.29.84.167"      # WSL2 NAT IP (备选,需端口转发)
$MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"

Write-Host "=== 1. 检查 CUDA Toolkit ===" -ForegroundColor Cyan
wsl -d Ubuntu -u $WSL2_USER bash -c "which nvcc && nvcc --version | tail -1"
if ($LASTEXITCODE -ne 0) {
    Write-Host "警告: nvcc 未找到，请先安装 CUDA Toolkit 12.6" -ForegroundColor Red
    Write-Host "  https://developer.nvidia.com/cuda-downloads?target_os=Linux&target_arch=x86_64&target_distro=WSLUbuntu" -ForegroundColor Yellow
}

Write-Host "=== 2. 编译 llama.cpp (CUDA + RPC) ===" -ForegroundColor Cyan
wsl -d Ubuntu -u $WSL2_USER bash -c "export PATH=/usr/local/cuda-12.6/bin:\$PATH && cd ~/llama.cpp && cmake -B build-cuda -DGGML_CUDA=ON -DGGML_RPC=ON -DCMAKE_BUILD_TYPE=Release && cmake --build build-cuda -j 16"
Write-Host "编译完成" -ForegroundColor Green

Write-Host "=== 3. 下载模型 (如需) ===" -ForegroundColor Cyan
wsl -d Ubuntu -u $WSL2_USER bash -c "test -f ~/llama.cpp/models/qwen2.5-1.5b-instruct-q4_k_m.gguf || (mkdir -p ~/llama.cpp/models && wget -O ~/llama.cpp/models/qwen2.5-1.5b-instruct-q4_k_m.gguf $MODEL_URL)"
Write-Host "模型就绪" -ForegroundColor Green

Write-Host "=== 4. (备选) Windows端口转发 ===" -ForegroundColor Cyan
Write-Host "如果使用 Tailscale 直连 WSL2，无需端口转发" -ForegroundColor Yellow
Write-Host "如果需要通过 Windows IP 访问，执行:" -ForegroundColor Yellow
Write-Host "  netsh interface portproxy add v4tov4 listenport=50052 listenaddress=0.0.0.0 connectport=50052 connectaddress=$WSL2_NAT_IP"
Write-Host "  netsh interface portproxy add v4tov4 listenport=8080 listenaddress=0.0.0.0 connectport=8080 connectaddress=$WSL2_NAT_IP"

Write-Host "=== 5. 防火墙放行 ===" -ForegroundColor Cyan
netsh advfirewall firewall add rule name="llama-rpc-50052" dir=in action=allow protocol=TCP localport=50052
netsh advfirewall firewall add rule name="llama-server-8080" dir=in action=allow protocol=TCP localport=8080
Write-Host "防火墙规则添加完成" -ForegroundColor Green

Write-Host "=== 6. 启动 rpc-server (CUDA 后端) ===" -ForegroundColor Cyan
wsl -d Ubuntu bash -c "export PATH=/usr/local/cuda-12.6/bin:\$PATH && export LD_LIBRARY_PATH=/usr/local/cuda-12.6/lib64:\$LD_LIBRARY_PATH && setsid ~/llama.cpp/build-cuda/bin/rpc-server -p 50052 --host 0.0.0.0 &> ~/rpc-server-cuda.log &"
Write-Host "rpc-server 启动完成 (端口 50052, CUDA 后端)" -ForegroundColor Green

Write-Host "=== 7. 启动 llama-server (CUDA 加速, 用于 Ray) ===" -ForegroundColor Cyan
wsl -d Ubuntu bash -c "export PATH=/usr/local/cuda-12.6/bin:\$PATH && export LD_LIBRARY_PATH=/usr/local/cuda-12.6/lib64:\$LD_LIBRARY_PATH && setsid ~/llama.cpp/build-cuda/bin/llama-server -m ~/llama.cpp/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --port 8080 --host 0.0.0.0 --threads 8 -c 2048 -ngl 999 &> ~/llama-server-cuda.log &"
Write-Host "llama-server 启动完成 (端口 8080, GPU offload)" -ForegroundColor Green

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  从机部署完成 (CUDA 加速)!" -ForegroundColor Yellow
Write-Host "  WSL2 Tailscale IP: $WSL2_TAILSCALE (推荐连接地址)" -ForegroundColor Yellow
Write-Host "  RPC 端口: 50052" -ForegroundColor Yellow
Write-Host "  llama-server: 8080" -ForegroundColor Yellow
Write-Host "  请在主机上运行:" -ForegroundColor Yellow
Write-Host "  llama-cli --rpc $WSL2_TAILSCALE`:50052" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow