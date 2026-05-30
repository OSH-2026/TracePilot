# TracePilot Enhanced — Makefile
# Builds BPF object + host loader (WSL) and cross-compiles aarch64 loader (NDK).
#
# New (Enhanced):
#   - Binder dependency graph tracking
#   - Futex wait graph tracking
#   - CPU frequency / big-little attribution
#   - Memory reclaim tracking
#   - Graph-based CriticalScore(tid) with 7-term formula
#   - Jank cause classifier
#   - Heuristic strategy comparison
#
# Prerequisites:
#   Host (WSL/Ubuntu): clang llvm libbpf-dev libelf-dev zlib1g-dev
#   NDK (Android): $(HOME)/android-ndk-r26b or set ANDROID_NDK
#
# Targets:
#   make all           Build BPF .o + host loader + skeleton
#   make bpf           Build BPF .o only
#   make loader        Build host loader (for WSL testing)
#   make android       Cross-compile aarch64 static loader for Pixel 6a
#   make skeleton      Generate BPF skeleton .h from .o via bpftool
#   make clean         Remove build artifacts

ARCH ?= arm64

# ── Tools ─────────────────────────────────────────────────────────────
CLANG           ?= /usr/bin/clang
CC              ?= gcc
ANDROID_NDK     ?= $(HOME)/pixel6a-bpf/third_party/android-ndk-r26b
NDK_BIN         := $(ANDROID_NDK)/toolchains/llvm/prebuilt/linux-x86_64/bin
ANDROID_CLANG   := $(NDK_BIN)/aarch64-linux-android34-clang
ANDROID_STRIP   := $(NDK_BIN)/llvm-strip
BPFTOOL         ?= bpftool

# ── Paths ─────────────────────────────────────────────────────────────
BPF_DIR         := bpf
LOADER_DIR      := loader
SCRIPTS_DIR     := scripts
OUTPUT_DIR      := output

# ── BPF compile flags ─────────────────────────────────────────────────
BPF_CFLAGS := -g -O2 -target bpf \
	-D__TARGET_ARCH_arm64 \
	-Wno-unknown-attributes \
	-Wno-unused-value \
	-Wno-pointer-sign \
	-Wno-compare-distinct-pointer-types \
	-I$(BPF_DIR)

# ── libbpf (host) ─────────────────────────────────────────────────────
LIBBPF_CFLAGS := $(shell pkg-config --cflags libbpf 2>/dev/null)
ifeq ($(strip $(LIBBPF_CFLAGS)),)
LIBBPF_CFLAGS := -I/usr/include
endif

LIBBPF_LIBS := $(shell pkg-config --libs libbpf 2>/dev/null)
ifeq ($(strip $(LIBBPF_LIBS)),)
LIBBPF_LIBS := -lbpf
endif

# ── libbpf (Android/NDK, static) ──────────────────────────────────────
ANDROID_LIBBPF_DIR  ?= $(HOME)/pixel6a-bpf/页面切换-基础版/libbpf
ANDROID_LIBBPF_SRC  ?= $(ANDROID_LIBBPF_DIR)/src
ANDROID_LIBBPF_A    ?= $(ANDROID_LIBBPF_SRC)/libbpf.a

ANDROID_CFLAGS := -static -O2 -g \
	-Iloader \
	-Ibpf \
	-I$(ANDROID_LIBBPF_DIR)/src \
	-D__ANDROID__

ANDROID_LIBS := -L$(ANDROID_LIBBPF_SRC) \
	-L$(HOME)/pixel6a-bpf/third_party/prebuilt/android-arm64/lib \
	-L$(HOME)/pixel6a-bpf/sysroot-aarch64/lib \
	-lbpf -lelf -lz -lm

# ── Source files ──────────────────────────────────────────────────────
BPF_SRC     := $(BPF_DIR)/tracepilot.bpf.c
BPF_OBJ     := $(OUTPUT_DIR)/tracepilot.bpf.o
SKEL_H      := $(LOADER_DIR)/tracepilot.skel.h

LOADER_SRC  := $(LOADER_DIR)/main.c \
               $(LOADER_DIR)/frame_aggregator.c \
               $(LOADER_DIR)/resolver.c

LOADER_BIN  := $(OUTPUT_DIR)/tracepilot
ANDROID_BIN := $(OUTPUT_DIR)/tracepilot-aarch64

# ── Targets ───────────────────────────────────────────────────────────
.PHONY: all bpf loader skeleton android clean help

all: $(OUTPUT_DIR) bpf skeleton loader
	@echo "=== Build complete (Enhanced) ==="
	@echo "Host loader: $(LOADER_BIN)"
	@echo "BPF object:  $(BPF_OBJ)"

$(OUTPUT_DIR):
	mkdir -p $(OUTPUT_DIR)

# ── BPF object ────────────────────────────────────────────────────────
bpf: $(OUTPUT_DIR) $(BPF_OBJ)

$(BPF_OBJ): $(BPF_SRC) $(BPF_DIR)/vmlinux.h $(BPF_DIR)/tracepilot.bpf.h
	$(CLANG) $(BPF_CFLAGS) -I$(BPF_DIR) -c $(BPF_SRC) -o $@

# ── BPF skeleton (bpftool) ────────────────────────────────────────────
skeleton: $(BPF_OBJ)
	@if command -v $(BPFTOOL) >/dev/null 2>&1; then \
		$(BPFTOOL) gen skeleton $(BPF_OBJ) > $(SKEL_H); \
		echo "Skeleton written to $(SKEL_H)"; \
	else \
		echo "WARNING: bpftool not found, skeleton not generated."; \
	fi

# ── Host loader (WSL/Ubuntu, links apt libbpf) ────────────────────────
loader: $(OUTPUT_DIR) $(LOADER_SRC) $(LOADER_DIR)/frame_aggregator.h $(LOADER_DIR)/resolver.h $(BPF_DIR)/tracepilot.bpf.h
	$(CC) -O2 -g -I. -Iloader -Ibpf $(LIBBPF_CFLAGS) \
		$(LOADER_SRC) -o $(LOADER_BIN) \
		$(LIBBPF_LIBS) -lelf -lz -lm

# ── Android aarch64 static binary ─────────────────────────────────────
android: $(OUTPUT_DIR) $(LOADER_SRC) $(LOADER_DIR)/frame_aggregator.h $(LOADER_DIR)/resolver.h $(BPF_DIR)/tracepilot.bpf.h
	@if [ ! -f "$(ANDROID_LIBBPF_A)" ]; then \
		echo "ERROR: libbpf.a not found at $(ANDROID_LIBBPF_A)"; \
		exit 1; \
	fi
	$(ANDROID_CLANG) $(ANDROID_CFLAGS) $(LOADER_SRC) \
		$(HOME)/pixel6a-bpf/eu_stub_aarch64.o \
		-o $(ANDROID_BIN) $(ANDROID_LIBS)
	$(ANDROID_STRIP) --strip-all $(ANDROID_BIN)
	@echo "Android binary: $(ANDROID_BIN)"

# ── Clean ─────────────────────────────────────────────────────────────
clean:
	rm -rf $(OUTPUT_DIR)/*.o $(OUTPUT_DIR)/tracepilot $(OUTPUT_DIR)/tracepilot-aarch64
	rm -f $(SKEL_H)

# ── Help ──────────────────────────────────────────────────────────────
help:
	@echo "TracePilot Enhanced Build System"
	@echo "=================================="
	@echo "make bpf       Build BPF object (7 raw_tp hooks + 6 new enhanced hooks)"
	@echo "make skeleton  Generate BPF skeleton header (requires bpftool)"
	@echo "make loader    Build host loader (WSL test)"
	@echo "make android   Cross-compile aarch64 static loader for Pixel"
	@echo "make all       Build everything (bpf + skeleton + loader)"
	@echo "make clean     Remove build artifacts"
	@echo ""
	@echo "New -G flag enables graph-based critical path analysis."
	@echo "  ./tracepilot -i events.bin -f frames.txt -o result.json -G"
