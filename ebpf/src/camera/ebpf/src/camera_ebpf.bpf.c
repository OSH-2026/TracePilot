#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

#define TASK_COMM_LEN 16

/* 传输到用户态的事件结构 */
struct event_t {
    u64 ts;                   // 时间戳(ns)
    u32 tid;                  // 对于 wakeup 是醒来的线程，对 switch 是即将执行的线程
    u32 prev_tid;             // 仅 switch 事件有意义：被切出的线程
    u32 tgid;                 // 当前进程组ID
    u32 uid;                  // 当前UID
    char comm[TASK_COMM_LEN]; // 线程名
    u8 type;                  // 0: switch, 1: wakeup
};

/* BPF Ring Buffer 用于替代 Perf Buffer，性能更好且适合 CO-RE */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024); /* 256 KB */
} rb SEC(".maps");

/* 
 * 用户态可以通过 skeleton 直接修改这里的 rodata，
 * 从而实现动态过滤指定的 PID/TGID，无需重新编译。
 */
volatile const u32 target_pid = 0;

struct trace_event_raw_sched_wakeup {
    struct trace_entry ent;
    char comm[16];
    pid_t pid;
    int prio;
    int success;
    int target_cpu;
};

/* sched_wakeup 抓取 */
SEC("tp/sched/sched_wakeup")
int handle_sched_wakeup(struct trace_event_raw_sched_wakeup *ctx)
{
    // 如果设置了目标PID且想在内核做严格过滤（比如只抓特定进程的线程），
    // 可以在这里通过 bpf_task_from_pid 等方式找到 tgid，
    // 或者在用户态进行过滤。为了简单和兼容性，先记录事件，让用户态去严格筛选。
    
    struct event_t *e;

    // 申请 Ring Buffer 空间
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e)
        return 0;

    e->type = 1;
    // 使用 CLOCK_BOOTTIME 对齐 Android Perfetto 和 SurfaceFlinger 的时间戳
    e->ts = bpf_ktime_get_boot_ns();
    e->tid = ctx->pid;
    e->prev_tid = 0;
    
    // 获取当前上下文身份 (唤醒动作的主体)
    u64 id = bpf_get_current_pid_tgid();
    e->tgid = id >> 32;
    e->uid = bpf_get_current_uid_gid();
    
    // BPF CO-RE 字符串读取
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    // 提交数据到用户态
    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* sched_switch 抓取 */
SEC("tp/sched/sched_switch")
int handle_sched_switch(struct trace_event_raw_sched_switch *ctx)
{
    struct event_t *e;

    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e)
        return 0;

    e->type = 0;
    // 获取当前上下文身份 (切出动作的主体，即prev)
    u64 id = bpf_get_current_pid_tgid();
    e->tgid = id >> 32;
    e->uid = bpf_get_current_uid_gid();
    
    // 使用 CLOCK_BOOTTIME 对齐 Android Perfetto 和 SurfaceFlinger 的时间戳
    e->ts = bpf_ktime_get_boot_ns();
    e->tid = ctx->next_pid;
    e->prev_tid = ctx->prev_pid;
    
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

char LICENSE[] SEC("license") = "Dual BSD/GPL";
