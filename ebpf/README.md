# eBPF模块(具体架构待完善)

本模块负责 Android 系统的底层性能数据采集与实时特征聚合。

## 目录结构
- `bpf/`: 内核态 eBPF 源码，用于挂载内核钩子。
- `src/`: 用户态 Loader 与特征聚合逻辑，负责与内核态通信。
- `include/`: 模块内部辅助头文件。
- `third_party/`: 外部依赖库 (如 libbpf)。

## 环境要求
- **构建环境**: WSL2 / Linux
- **交叉编译工具链**: Android NDK (Clang) ,交叉编译的环境目前网络上没有配好的，本次实验所需编译环境已经放在https://github.com/OSH-2026/TracePilot/blob/main/ebpf/pixel6a-bpf.zip，注意要在wsl等linux环境下解压缩，否则会丢包。
- **目标设备**: 已 Root 的 Android 真机 (ARM64)

## 快速开始(To do)

### 1. 编译
在根目录下执行以下命令以完成交叉编译：
```bash
make
```
### 2. 部署
将编译生成的二进制文件推送到手机并运行：

