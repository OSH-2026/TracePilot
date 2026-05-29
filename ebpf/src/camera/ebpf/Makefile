<<<<<<< HEAD
# camera_ebpf Makefile
# Cross-compile for Android aarch64 (Pixel 6a)
#
# Environment variables (set by Dockerfile or manually):
#   ANDROID_NDK  - path to Android NDK r26b
#   LIBBPF_SRC   - path to libbpf source
#   ANDROID_LIBBPF_A - path to compiled libbpf.a
#   ANDROID_LIBS_DIR - path to aarch64 cross-compiled libraries (libelf.a, eu-search.o)

NDK_PATH       ?= /opt/android-ndk-r26b
LIBBPF_SRC     ?= /opt/libbpf/src
ANDROID_LIBBPF_A  ?= /opt/android-libs/libbpf.a
ANDROID_LIBS_DIR ?= /opt/android-libs

API            ?= 30
TOOLCHAIN      := $(NDK_PATH)/toolchains/llvm/prebuilt/linux-x86_64
CC_ANDROID     := $(TOOLCHAIN)/bin/aarch64-linux-android$(API)-clang
CC_BPF         := clang
AR             := llvm-ar

SRC_DIR        := ./src
BPF_SRC        := $(SRC_DIR)/camera_ebpf.bpf.c
LOADER_SRC     := $(SRC_DIR)/camera_ebpf.c
BPF_OBJ        := $(SRC_DIR)/camera_ebpf.bpf.o
SKEL_H         := $(SRC_DIR)/camera_ebpf.skel.h
BIN            := camera_ebpf_android

INCLUDE        := -I./include -I$(SRC_DIR) -I$(LIBBPF_SRC)/../include -I$(LIBBPF_SRC)/../include/uapi

.PHONY: all clean

all: $(BIN)

$(BPF_OBJ): $(BPF_SRC)
	$(CC_BPF) -g -O2 -target bpf -D__TARGET_ARCH_arm64 \
		$(INCLUDE) -I$(LIBBPF_SRC) \
		-c $< -o $@

$(SKEL_H): $(BPF_OBJ)
	bpftool gen skeleton $< > $@

$(BIN): $(LOADER_SRC) $(SKEL_H)
	$(CC_ANDROID) -O2 -g -static $< -o $@ \
		$(INCLUDE) \
		$(ANDROID_LIBBPF_A) \
		$(ANDROID_LIBS_DIR)/libelf.a \
		$(ANDROID_LIBS_DIR)/eu-search.o \
		-lz

clean:
	rm -f $(BPF_OBJ) $(SKEL_H) $(BIN)
=======
# 由于在本地配置的环境以文件夹的形式存在项目的目录，而没有上传到github，故直接make无法正常运行

NDK_PATH ?= /home/yy/android-ndk-r26b
API ?= 30

TOOLCHAIN := $(NDK_PATH)/toolchains/llvm/prebuilt/linux-x86_64
CLANG_ANDROID := $(TOOLCHAIN)/bin/aarch64-linux-android$(API)-clang

CLANG_HOST := clang
BPFTOOL := bpftool

LIBBPF_SRC := ./libbpf/src
INCLUDE_DIR := ./include
SRC_DIR := ./src

.PHONY: all clean

# 目标程序：只依赖内核态编译和 skel.h 生成
all: camera_ebpf_android

# 1. 编译内核态代码为 BPF 字节码
$(SRC_DIR)/camera_ebpf.bpf.o: $(SRC_DIR)/camera_ebpf.bpf.c
	$(CLANG_HOST) -g -O2 -target bpf -D__TARGET_ARCH_arm64 \
		-I$(INCLUDE_DIR) -I$(LIBBPF_SRC) \
		-c $< -o $@

# 2. 用你编译的原生 bpftool 生成骨架 skel.h
$(SRC_DIR)/camera_ebpf.skel.h: $(SRC_DIR)/camera_ebpf.bpf.o
	$(BPFTOOL) gen skeleton $< > $@

# 3. 编译用户态程序：直接链接现成的、完美的 libbpf.a
camera_ebpf_android: $(SRC_DIR)/camera_ebpf.c $(SRC_DIR)/camera_ebpf.skel.h
	$(CLANG_ANDROID) -O2 -g $< -o $@ \
		-I$(INCLUDE_DIR) \
		-I$(SRC_DIR) \
		-I$(LIBBPF_SRC) \
		-I./libbpf/include \
		-I./libbpf/include/uapi \
		$(LIBBPF_SRC)/libbpf.a \
		./lib_arm64/libelf.a \
		./lib_arm64/eu-search.o \
		-lz

clean:
	rm -f $(SRC_DIR)/*.o $(SRC_DIR)/*.skel.h camera_ebpf_android
>>>>>>> e021c6bfb877fb5165df1fee7abfb3908cb1bfd0
