#include <stdio.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <stdint.h>
#include <signal.h>

// 1. 强行补上 u32 的定义，伺候生成的骨架文件
typedef uint32_t u32;

#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include "camera_ebpf.skel.h" // 名字彻底对齐！

/* 与 BPF 侧 struct event_t 严格对齐 (扩展版) */
struct event_t {
    uint64_t ts;
    uint32_t tid;
    uint32_t prev_tid;
    uint32_t tgid;
    uint32_t uid;
    uint32_t debug_id;
    uint32_t extra;
    int32_t  ret;
    char comm[16];
    uint8_t  type;
};

static FILE *sched_file  = NULL;   // sched_events.csv
static FILE *binder_file = NULL;   // binder_futex_events.csv
static volatile int running = 1;
static int quiet = 0;              // -q: 静默模式, 不打印事件到终端
static unsigned long event_count = 0;  // 事件计数器, 用于定时 fflush

static void sig_handler(int sig) { running = 0; }

/* fflush 频率: 每 FLUSH_INTERVAL 条事件刷一次磁盘, 避免每事件刷盘阻塞 ring buffer */
#define FLUSH_INTERVAL 1000

static void maybe_flush(void) {
    event_count++;
    if (event_count % FLUSH_INTERVAL == 0) {
        if (sched_file)  fflush(sched_file);
        if (binder_file) fflush(binder_file);
    }
}

static const char *event_type_name(uint8_t type) {
    switch (type) {
        case 0: return "switch";
        case 1: return "wakeup";
        case 2: return "binder_transaction";
        case 3: return "binder_received";
        case 4: return "futex";
        case 5: return "cpu_frequency";
        case 6: return "thermal";
        default: return "unknown";
    }
}

// 2. 补全回调函数逻辑
static int handle_event(void *ctx, void *data, size_t data_sz) {
    struct event_t *e = data;

    switch (e->type) {
    case 0: /* sched_switch */
        if (sched_file) {
            fprintf(sched_file, "%llu,switch,%u,%u,%u,%u,%s\n",
                (unsigned long long)e->ts, e->tid, e->prev_tid, e->tgid, e->uid, e->comm);
            maybe_flush();
        }
        if (!quiet) printf("[SWITCH] Prev_TID:%d -> Next_TID:%d [%s]\n", e->prev_tid, e->tid, e->comm);
        break;

    case 1: /* sched_wakeup */
        if (sched_file) {
            fprintf(sched_file, "%llu,wakeup,%u,0,%u,%u,%s\n",
                (unsigned long long)e->ts, e->tid, e->tgid, e->uid, e->comm);
            maybe_flush();
        }
        if (!quiet) printf("[WAKEUP] TID:%d [%s]\n", e->tid, e->comm);
        break;

    case 2: /* binder_transaction */
    case 3: /* binder_received */
        if (binder_file) {
            fprintf(binder_file, "%llu,%s,%u,%u,%u,%u,%u,%u,%d,%s\n",
                (unsigned long long)e->ts,
                event_type_name(e->type),
                e->tid, e->prev_tid, e->tgid, e->uid,
                e->debug_id, e->extra, e->ret,
                e->comm);
            maybe_flush();
        }
        if (!quiet) printf("[BINDER] %s TID:%d debug_id:%u to_thread:%u [%s]\n",
            event_type_name(e->type), e->tid, e->debug_id, e->prev_tid, e->comm);
        break;

    case 4: /* futex */
        if (binder_file) {
            fprintf(binder_file, "%llu,futex,%u,0,%u,%u,%u,%u,%d,%s\n",
                (unsigned long long)e->ts,
                e->tid, e->tgid, e->uid,
                e->debug_id, e->extra, e->ret,
                e->comm);
            maybe_flush();
        }
        if (!quiet) printf("[FUTEX] TID:%d op:0x%x uaddr:0x%x [%s]\n",
            e->tid, e->extra, e->debug_id, e->comm);
        break;

    case 5: /* cpu_frequency */
    case 6: /* thermal */
        if (binder_file) {
            fprintf(binder_file, "%llu,%s,%u,%u,%u,%u,%u,%u,%d,%s\n",
                (unsigned long long)e->ts,
                event_type_name(e->type),
                e->tid, e->prev_tid, e->tgid, e->uid,
                e->debug_id, e->extra, e->ret,
                e->comm);
            maybe_flush();
        }
        break;
    }

    fflush(stdout);
    return 0;
}

int main(int argc, char **argv) {
    struct camera_ebpf_bpf *skel; // 改为 camera_ebpf_bpf
    struct ring_buffer *rb = NULL;
    int err;

    signal(SIGINT,  sig_handler);
    signal(SIGTERM, sig_handler);

    /* 解析参数: -q 静默, -u <uid> 目标 UID 过滤 (仅 futex) */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-q") == 0) {
            quiet = 1;
        } else if (strcmp(argv[i], "-u") == 0 && i + 1 < argc) {
            unsigned long uid = strtoul(argv[++i], NULL, 10);
            /* 注: target_uid 在 load() 后写入 rodata, 稍后设置 */
        }
    }

    /* 打开两个输出 CSV */
    sched_file = fopen("sched_events.csv", "w");
    if (sched_file) {
        fprintf(sched_file, "ts,event,tid,prev_tid,tgid,uid,comm\n");
    } else {
        fprintf(stderr, "Warning: Failed to open sched_events.csv for writing\n");
    }

    binder_file = fopen("binder_futex_events.csv", "w");
    if (binder_file) {
        fprintf(binder_file, "ts,event,tid,prev_tid,tgid,uid,debug_id,extra,ret,comm\n");
    } else {
        fprintf(stderr, "Warning: Failed to open binder_futex_events.csv for writing\n");
    }

    // 提升 RLIMIT_MEMLOCK, 否则 eBPF 加载因权限失败 (Android 常见问题)
    {
        struct rlimit rlim_new = {
            .rlim_cur = RLIM_INFINITY,
            .rlim_max = RLIM_INFINITY,
        };
        if (setrlimit(RLIMIT_MEMLOCK, &rlim_new)) {
            // 有些内核可能不允许无限, 尝试 128MB
            struct rlimit rlim_128 = {
                .rlim_cur = 128 * 1024 * 1024,
                .rlim_max = 128 * 1024 * 1024,
            };
            if (setrlimit(RLIMIT_MEMLOCK, &rlim_128)) {
                fprintf(stderr, "Warning: Could not set RLIMIT_MEMLOCK (%d)\n", errno);
            }
        }
    }

    // 1. 打开 eBPF 骨架
    skel = camera_ebpf_bpf__open();
    if (!skel) {
        fprintf(stderr, "Failed to open BPF skeleton\n");
        return 1;
    }

    // 2. 解析 -u <uid>: 内核侧 UID 过滤 (仅 futex, 减少系统调用事件量)
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-u") == 0 && i + 1 < argc) {
            unsigned long uid = strtoul(argv[++i], NULL, 10);
            if (uid > 0) {
                skel->rodata->target_uid = (uint32_t)uid;
                printf("Target UID set to: %u\n", (uint32_t)uid);
            }
        }
    }

    // 3. 加载 eBPF 字节码
    err = camera_ebpf_bpf__load(skel);
    if (err) {
        fprintf(stderr, "Failed to load BPF skeleton\n");
        goto cleanup;
    }

    // 4. 手动逐个挂载 tracepoint
    //    不用 camera_ebpf_bpf__attach() — 单个失败不影响其他
    //    例如这颗内核没有 sys_enter_futex, 但 sched/binder 仍能工作
    {
        struct bpf_program *prog;
        int attached = 0, skipped = 0;

        bpf_object__for_each_program(prog, skel->obj) {
            const char *name = bpf_program__name(prog);
            struct bpf_link *link = bpf_program__attach(prog);
            if (libbpf_get_error(link)) {
                long e = libbpf_get_error(link);
                fprintf(stderr, "  [⚠] Skip '%s' (err=%ld)\n", name, -e);
                skipped++;
            } else {
                printf("  [✓] Attached '%s'\n", name);
                attached++;
            }
        }
        if (attached == 0) {
            fprintf(stderr, "Failed to attach any BPF program\n");
            err = -1;
            goto cleanup;
        }
        printf("  [✓] %d BPF programs running (%d skipped)\n", attached, skipped);
    }

    // 5. 初始化 RingBuffer 映射
    rb = ring_buffer__new(bpf_map__fd(skel->maps.rb), handle_event, NULL, NULL);
    if (!rb) {
        fprintf(stderr, "Failed to create ring buffer\n");
        goto cleanup;
    }

    printf("Camera eBPF running! Capturing sched + binder + futex events.\n");
    printf("Press Ctrl+C to stop.\n");

    // 6. 循环消费数据
    while (running) {
        err = ring_buffer__poll(rb, 100 /* timeout ms */);
        if (err == -EINTR) continue;
        if (err < 0) {
            printf("Error polling ring buffer: %d\n", err);
            break;
        }
    }

    printf("\nShutting down... (processed %lu events)\n", event_count);

cleanup:
    if (sched_file)  { fflush(sched_file);  fclose(sched_file);  sched_file = NULL; }
    if (binder_file) { fclose(binder_file); binder_file = NULL; }
    ring_buffer__free(rb);
    camera_ebpf_bpf__destroy(skel); // 名字彻底对齐！
    return err < 0 ? -err : 0;
}