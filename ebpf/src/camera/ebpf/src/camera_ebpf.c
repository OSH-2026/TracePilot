#include <stdio.h>
#include <unistd.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <stdint.h>

// 1. 强行补上 u32 的定义，伺候生成的骨架文件
typedef uint32_t u32;

#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include "camera_ebpf.skel.h" // 名字彻底对齐！

// 你的 RingBuffer 回调函数（保持你原本的业务逻辑）
struct event_t {
    uint64_t ts;
    uint32_t tid;
    uint32_t prev_tid;
    uint32_t tgid;
    uint32_t uid;
    char comm[16];
    uint8_t type;
};

static FILE *out_file = NULL;

// 2. 补全回调函数逻辑
static int handle_event(void *ctx, void *data, size_t data_sz) {
    // 强制类型转换，将 data 裸指针恢复为结构体指针
    struct event_t *e = data;
    
    if (out_file) {
        if (e->type == 1) {
            fprintf(out_file, "%llu,wakeup,%u,0,%u,%u,%s\n", (unsigned long long)e->ts, e->tid, e->tgid, e->uid, e->comm);
        } else if (e->type == 0) {
            fprintf(out_file, "%llu,switch,%u,%u,%u,%u,%s\n", (unsigned long long)e->ts, e->tid, e->prev_tid, e->tgid, e->uid, e->comm);
        }
    }

    // 转换时间戳为秒级调试（可选，这里先直接打印）
    if (e->type == 1) {
        // Wakeup 事件
        printf("[WAKEUP] Thread [%s] (TID: %d) was woken up!\n", 
               e->comm, e->tid);
    } else if (e->type == 0) {
        // Switch 事件
        printf("[SWITCH] Prev_TID: %d ===> Next_TID: %d [%s]\n", 
               e->prev_tid, e->tid, e->comm);
    }

    // 强制刷新标准输出缓冲区，保证高频数据能立刻刷到终端屏幕上和文件中
    if (out_file) fflush(out_file);
    fflush(stdout);
    return 0;
}

int main(int argc, char **argv) {
    struct camera_ebpf_bpf *skel; // 改为 camera_ebpf_bpf
    struct ring_buffer *rb = NULL;
    int err;

    out_file = fopen("sched_events.csv", "w");
    if (out_file) {
        fprintf(out_file, "ts,event,tid,prev_tid,tgid,uid,comm\n");
    } else {
        fprintf(stderr, "Warning: Failed to open sched_events.csv for writing\n");
    }

    // 1. 打开 eBPF 骨架
    skel = camera_ebpf_bpf__open();
    if (!skel) {
        fprintf(stderr, "Failed to open BPF skeleton\n");
        return 1;
    }

    // 2. 如果传入了 PID 参数，设置到 rodata 里面（对应你的 skel->rodata->target_pid）
    if (argc > 1) {
        skel->rodata->target_pid = strtol(argv[1], NULL, 10);
        printf("Target PID set to: %u\n", skel->rodata->target_pid);
    }

    // 3. 加载 eBPF 字节码
    err = camera_ebpf_bpf__load(skel);
    if (err) {
        fprintf(stderr, "Failed to load BPF skeleton\n");
        goto cleanup;
    }

    // 4. 挂载到内核
    err = camera_ebpf_bpf__attach(skel);
    if (err) {
        fprintf(stderr, "Failed to attach BPF skeleton\n");
        goto cleanup;
    }

    // 5. 初始化 RingBuffer 映射
    rb = ring_buffer__new(bpf_map__fd(skel->maps.rb), handle_event, NULL, NULL);
    if (!rb) {
        fprintf(stderr, "Failed to create ring buffer\n");
        goto cleanup;
    }

    printf("Camera eBPF tool runs successfully!\n");

    // 6. 循环消费数据
    while (1) {
        err = ring_buffer__poll(rb, 100 /* timeout ms */);
        if (err == -EINTR) continue;
        if (err < 0) {
            printf("Error polling ring buffer: %d\n", err);
            break;
        }
    }

cleanup:
    if (out_file) {
        fclose(out_file);
        out_file = NULL;
    }
    ring_buffer__free(rb);
    camera_ebpf_bpf__destroy(skel); // 名字彻底对齐！
    return err < 0 ? -err : 0;
}