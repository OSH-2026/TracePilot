// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

enum event_type {
    EVT_EXEC = 1,
    EVT_BINDER_TX = 2,
};

struct event {
    __u64 ts_ns;
    __u32 pid;
    __u32 tgid;
    __u32 uid;
    __u32 cpu;
    __u32 type;
    char comm[16];
};

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 1 << 24); // 16MB
} events SEC(".maps");

static __always_inline int submit_event(__u32 type)
{
    struct event *e;
    __u64 pid_tgid = bpf_get_current_pid_tgid();
    __u64 uid_gid = bpf_get_current_uid_gid();

    e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e)
        return 0;

    e->ts_ns = bpf_ktime_get_ns();
    e->pid = (__u32)pid_tgid;
    e->tgid = (__u32)(pid_tgid >> 32);
    e->uid = (__u32)uid_gid;
    e->cpu = bpf_get_smp_processor_id();
    e->type = type;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

// 进程执行（冷启动相关）
SEC("tp/sched/sched_process_exec")
int handle_sched_exec(void *ctx)
{
    (void)ctx;
    return submit_event(EVT_EXEC);
}

// Binder 事务（页面跳转/跨进程依赖相关）
SEC("tp/binder/binder_transaction")
int handle_binder_tx(void *ctx)
{
    (void)ctx;
    return submit_event(EVT_BINDER_TX);
}