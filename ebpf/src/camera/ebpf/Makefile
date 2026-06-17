NDK_PATH ?= /home/yy/android-ndk-r26b
API ?= 30

TOOLCHAIN := $(NDK_PATH)/toolchains/llvm/prebuilt/linux-x86_64
CLANG_ANDROID := $(TOOLCHAIN)/bin/aarch64-linux-android$(API)-clang

CLANG_HOST := clang
BPFTOOL := bpftool

LIBBPF_SRC := ../env/libbpf/src
INCLUDE_DIR := ../env/include
SRC_DIR := ./src
BUILD_DIR := ./build

.PHONY: all clean

# 目标程序：所有构建产物统一放在 build/ 下
all: $(BUILD_DIR)/camera_ebpf_android

# 1. 编译 BPF 字节码 → build/
$(BUILD_DIR)/camera_ebpf.bpf.o: $(SRC_DIR)/camera_ebpf.bpf.c
	@mkdir -p $(BUILD_DIR)
	$(CLANG_HOST) -g -O2 -target bpf -D__TARGET_ARCH_arm64 \
		-I$(INCLUDE_DIR) -I$(LIBBPF_SRC) \
		-c $< -o $@

# 2. 生成骨架 skel.h → build/
$(BUILD_DIR)/camera_ebpf.skel.h: $(BUILD_DIR)/camera_ebpf.bpf.o
	$(BPFTOOL) gen skeleton $< > $@

# 3. 编译用户态程序 → build/
$(BUILD_DIR)/camera_ebpf_android: $(SRC_DIR)/camera_ebpf.c $(BUILD_DIR)/camera_ebpf.skel.h
	$(CLANG_ANDROID) -O2 -g $< -o $@ \
		-I$(INCLUDE_DIR) \
		-I$(BUILD_DIR) \
		-I$(LIBBPF_SRC) \
		-I../env/libbpf/include \
		-I../env/libbpf/include/uapi \
		$(LIBBPF_SRC)/libbpf.a \
		../env/lib_arm64/libelf.a \
		../env/lib_arm64/eu-search.o \
		-lz

clean:
	rm -rf $(BUILD_DIR)