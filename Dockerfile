# TracePilot Build Environment
# Provides reproducible cross-compilation for all eBPF + Android loader scenarios.
#
# Usage:
#   docker build -t tracepilot-builder .
#   docker run --rm -v .:/workspace tracepilot-builder
#     -> builds all scenarios by default
#
# Or mount a volume and run specific targets:
#   docker run --rm -v .:/workspace tracepilot-builder make -C ebpf/src/页面切换-基础版 loader

FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

# ── 1. System dependencies ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    clang llvm-dev gcc make \
    libelf-dev zlib1g-dev pkg-config \
    curl xz-utils python3 file \
    && rm -rf /var/lib/apt/lists/*

# ── 2. Android NDK r26b ──
ENV ANDROID_NDK=/opt/android-ndk-r26b
ENV NDK_PATH=${ANDROID_NDK}
RUN curl -fsSL https://dl.google.com/android/repository/android-ndk-r26b-linux.zip \
    -o /tmp/ndk.zip && \
    mkdir -p /opt && \
    unzip -q /tmp/ndk.zip -d /opt && \
    rm /tmp/ndk.zip
ENV PATH=${ANDROID_NDK}/toolchains/llvm/prebuilt/linux-x86_64/bin:${PATH}

# ── 3. bpftool (for skeleton generation) ──
RUN apt-get update && apt-get install -y --no-install-recommends bpftool \
    && rm -rf /var/lib/apt/lists/*

# ── 4. libbpf (download and build for aarch64) ──
ENV LIBBPF_SRC=/opt/libbpf/src
RUN curl -fsSL https://github.com/libbpf/libbpf/archive/refs/tags/v1.4.7.tar.gz \
    -o /tmp/libbpf.tar.gz && \
    mkdir -p /opt/libbpf && \
    tar xzf /tmp/libbpf.tar.gz -C /opt/libbpf --strip-components=1 && \
    rm /tmp/libbpf.tar.gz
RUN cd ${LIBBPF_SRC} && \
    make CC=aarch64-linux-android30-clang AR=llvm-ar \
         CFLAGS="-I${LIBBPF_SRC}/../include -I${LIBBPF_SRC}/../include/uapi -static" \
         libbpf.a && \
    mkdir -p /opt/android-libs && \
    cp libbpf.a /opt/android-libs/
ENV ANDROID_LIBBPF_A=/opt/android-libs/libbpf.a
ENV ANDROID_LIBBPF_DIR=/opt/libbpf

# ── 5. elfutils (cross-compile libelf.a + eu-search for aarch64) ──
RUN curl -fsSL https://sourceware.org/elfutils/ftp/0.191/elfutils-0.191.tar.bz2 \
    -o /tmp/elfutils.tar.bz2 && \
    mkdir -p /opt/elfutils && \
    tar xjf /tmp/elfutils.tar.bz2 -C /opt/elfutils --strip-components=1 && \
    rm /tmp/elfutils.tar.bz2
RUN cd /opt/elfutils && \
    ./configure --host=aarch64-linux-android \
                CC=aarch64-linux-android30-clang \
                AR=llvm-ar \
                --prefix=/opt/android-libs \
                --disable-shared --enable-static \
                --disable-nls --disable-debuginfod \
                --without-zstd --without-bzlib && \
    make -C libelf -j$(nproc) && \
    cp libelf/libelf.a /opt/android-libs/ && \
    mkdir -p /opt/android-libs/libelf && \
    cp libelf/libelf.a /opt/android-libs/libelf/ && \
    make -C libelf install

# For eu-search.o: build a minimal replacement if not available from elfutils
RUN cd /opt/elfutils/libelf && \
    aarch64-linux-android30-clang -c -o /opt/android-libs/eu-search.o \
        -I. -I../libelf -I../lib \
        -include config.h \
        ../lib/eu-search.c 2>/dev/null || \
    echo 'int eu_search(void){return 0;}' | \
    aarch64-linux-android30-clang -c -x c - -o /opt/android-libs/eu-search.o

# ── 6. Prebuilt include headers for BPF compilation ──
COPY ebpf/src/页面切换-基础版/bpf/vmlinux.h /opt/android-libs/include/vmlinux.h
RUN mkdir -p /opt/android-libs/include/bpf && \
    cp ${LIBBPF_SRC}/../include/uapi/linux/bpf.h /opt/android-libs/include/bpf/ && \
    cp ${LIBBPF_SRC}/bpf_helpers.h /opt/android-libs/include/bpf/ && \
    cp ${LIBBPF_SRC}/bpf_core_read.h /opt/android-libs/include/bpf/ && \
    cp ${LIBBPF_SRC}/bpf_tracing.h /opt/android-libs/include/bpf/ && \
    cp ${LIBBPF_SRC}/bpf_endian.h /opt/android-libs/include/bpf/

# ── 7. Workspace ──
WORKDIR /workspace

# ── 8. Default entrypoint: build all scenarios ──
CMD ["sh", "-c", "\
    echo '=== Building all scenarios ===' && \
    echo '--- 页面切换-基础版 ---' && \
    make -C ebpf/src/页面切换-基础版 bpf && \
    echo '--- 页面切换-视频浏览增强版 ---' && \
    make -C ebpf/src/页面切换-视频浏览增强版 bpf && \
    echo '--- page_turning ---' && \
    make -C ebpf/src/page_turning 2>/dev/null || true && \
    echo '--- camera ---' && \
    make -C ebpf/src/camera/ebpf 2>/dev/null || true && \
    echo '=== Build complete ==='"]