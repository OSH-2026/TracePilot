# eBPF 模块

本模块负责 Android 系统的底层性能数据采集与实时特征聚合。

## 目录结构

```
ebpf/
├── Makefile                    # 顶层构建入口
├── README.md                   # 本文件
├── ebpf_data/                  # 采集的数据（压缩文件、特征表等）
├── src/                        # 源码目录
│   ├── camera/                 # 相机场景 eBPF + Perfetto
│   ├── page_turning/           # 页面切换场景 eBPF
│   ├── 页面切换-基础版/        # 页面切换完整项目（含 libbpf 源码、loader 等）
│   └── 页面切换-视频浏览增强版/ # 双场景分析系统（Binder/Futex 图、温控、Inference）
```

## 环境要求

### 方式一：Docker 构建（推荐）

所有编译环境已封装在 Docker 镜像中，无需手动安装 NDK/libbpf：

```bash
# 构建 Docker 镜像（包含 NDK r26b + libbpf + elfutils）
docker build -t tracepilot-builder .

# 编译全部场景（页面切换、page_turning、camera）
docker run --rm -v .:/workspace tracepilot-builder

# 或只编译特定场景：
docker run --rm -v .:/workspace tracepilot-builder make -C ebpf/src/页面切换-基础版 bpf
```

### 方式二：手动编译（WSL/Linux）

**构建环境：**
- WSL2 / Linux
- clang、llvm、gcc、make
- libelf-dev、zlib1g-dev、pkg-config

**交叉编译工具链：**
- Android NDK r26b + clang（target `aarch64-linux-android`）
- bpftool（用于生成 BPF skeleton）

**编译命令：**
```bash
# 编译 BPF 对象 + host loader
make bpf && make loader

# 或交叉编译 Android aarch64 loader
make android
```

**部署：**
```bash
adb push output/tracepilot-aarch64 /data/local/tmp/
adb push output/tracepilot.bpf.o /data/local/tmp/
adb shell "su -c '/data/local/tmp/tracepilot -d 60 -e /data/local/tmp/events.bin -D'"
```

## 场景说明

### 页面切换（基础版）
- 路径：`src/页面切换-基础版/`
- 探针：sched_switch、sched_wakeup、irq、softirq
- 目标：页面切换场景下的卡顿根因分析

### 页面切换-视频浏览增强版
- 路径：`src/页面切换-视频浏览增强版/`
- 探针：sched_switch、sched_wakeup、binder_transaction、futex、cpu_frequency
- 目标：双场景（页面切换 + 视频浏览）的交互关键路径图分析
- 特性：Binder/Futex 依赖图、温控深化、Inference 证据链融合、多会话对比、Learned Policy

### 相机
- 路径：`src/camera/`
- 探针：sched_switch、sched_wakeup
- 目标：相机启动与预览场景的调度延迟分析

### 页面切换（简化版）
- 路径：`src/page_turning/`
- 探针：sched_switch、binder_transaction
- 目标：页面切换场景的包名识别与事件采集

## 数据说明

采集的数据位于 `ebpf_data/` 目录，包括：
- **eBPF 原始事件**：sched_switch、Binder 事务等 ringbuf 输出
- **Perfetto trace**：用于帧边界对齐与 jank 标定
- **特征表**：经聚合与特征工程提取的 CSV 特征表
- **分析报告**：场景级的行为分析、线程重要性排序

## 注意事项

1. **eBPF 依赖 Root 权限**：需要已 Root 的 Android 真机（Pixel 6a）
2. **Docker 环境**：Docker 镜像包含所有编译依赖，推荐使用
3. **编译产物**：`.o`、`.skel.h`、`-android` 等文件由编译生成，不提交到仓库
4. **采集数据**：大的 `.bin`、`.perfetto-trace`、`.zip` 等文件不提交到仓库