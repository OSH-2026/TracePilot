# page_turning Makefile
# Cross-compile for Android aarch64 (Pixel 6a)
#
# Environment variables (set by Dockerfile or manually):
#   ANDROID_NDK  - path to Android NDK r26b
#   LIBBPF_SRC   - path to libbpf source
#   ANDROID_LIBBPF_A - path to compiled libbpf.a

NDK_PATH      ?= /opt/android-ndk-r26b
LIBBPF_SRC    ?= /opt/libbpf/src
ANDROID_LIBBPF_A ?= /opt/android-libs/libbpf.a

API           ?= 30
TOOLCHAIN     := $(NDK_PATH)/toolchains/llvm/prebuilt/linux-x86_64
CC_ANDROID    := $(TOOLCHAIN)/bin/aarch64-linux-android$(API)-clang
CC_BPF        := clang
AR            := llvm-ar

SRC_DIR       := .
BPF_SRC       := $(SRC_DIR)/page_turning.bpf.c
LOADER_SRC    := $(SRC_DIR)/page_turning.c
BPF_OBJ       := $(SRC_DIR)/page_turning.bpf.o
SKEL_H        := $(SRC_DIR)/page_turning.skel.h
BIN           := page_turning-android

INCLUDE       := -I$(SRC_DIR) -I$(SRC_DIR)/bpf

.PHONY: all clean

all: $(BIN)

$(BPF_OBJ): $(BPF_SRC)
	$(CC_BPF) -g -O2 -target bpf -D__TARGET_ARCH_arm64 \
		$(INCLUDE) -I$(LIBBPF_SRC) -I$(LIBBPF_SRC)/../include/uapi \
		-c $< -o $@

$(SKEL_H): $(BPF_OBJ)
	bpftool gen skeleton $< > $@

$(BIN): $(LOADER_SRC) $(SKEL_H)
	$(CC_ANDROID) -O2 -g -static $< -o $@ \
		$(INCLUDE) -I$(LIBBPF_SRC) -I$(LIBBPF_SRC)/../include/uapi \
		$(ANDROID_LIBBPF_A) -lelf -lz -lm

clean:
	rm -f $(BPF_OBJ) $(SKEL_H) $(BIN)