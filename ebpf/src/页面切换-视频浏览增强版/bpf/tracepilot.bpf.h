/* SPDX-License-Identifier: GPL-2.0 OR BSD-2-Clause */
/*
 * tracepilot.bpf.h — BPF 侧共享结构体定义 (增强版)
 * 定义 events.bin v3 事件结构体和 BPF maps。
 */
#ifndef __TRACEPILOT_BPF_H__
#define __TRACEPILOT_BPF_H__

/*
 * Types: use __u64/__u32/__s32 to stay compatible with both BPF
 * (where vmlinux.h provides them) and userspace (where libbpf.h or
 * linux/types.h provides them).  No <stdint.h> — that pulls in glibc
 * headers which don't exist in the BPF target.
 */

#ifndef __bpf__
#include <linux/types.h>
#endif

#define TASK_COMM_LEN 16

enum sched_event_type {
    EVENT_SCHED_SWITCH = 0,
    EVENT_SCHED_WAKEUP = 1,
};

enum sys_event_type {
    SYS_EVENT_IRQ     = 0,
    SYS_EVENT_SOFTIRQ = 1,
};

/* ── Enhanced event types (new) ────────────────────────────────────── */
enum enhanced_event_type {
    ENH_EV_BINDER_CALL     = 0,   /* client → server binder transaction */
    ENH_EV_BINDER_RECEIVED = 1,   /* server receives binder transaction */
    ENH_EV_FUTEX_WAIT      = 2,   /* thread enters FUTEX_WAIT */
    ENH_EV_FUTEX_WAKE      = 3,   /* thread wakes from FUTEX_WAIT */
    ENH_EV_CPU_FREQ        = 4,   /* CPU frequency change */
    ENH_EV_MEM_RECLAIM     = 5,   /* direct memory reclaim */
};

struct sched_event {
    __u64 timestamp_ns;
    __u32 event_type;          /* enum sched_event_type */
    __u32 prev_pid;
    __u32 prev_tid;
    __u32 next_pid;
    __u32 next_tid;
    __u32 next_uid;
    __u64 prev_task_state;
    char  prev_comm[TASK_COMM_LEN];
    char  next_comm[TASK_COMM_LEN];
    __u32 cpu;
    __u64 wakeup_latency_ns;
    __u64 next_runnable_delay_ns;
};

struct system_event {
    __u64 timestamp_ns;
    __u32 event_type;          /* enum sys_event_type */
    __s32 irq_vec;             /* irq number or softirq vector */
    __u32 cpu;
    __u64 duration_ns;
};

/* ── Enhanced event struct (new, common format with type discriminator) ── */
struct enhanced_event {
    __u32 type;               /* enum enhanced_event_type */
    __u32 padding;
    __u64 timestamp_ns;

    /* fields shared across types */
    __u32 tid;                /* thread id */
    __u32 pid;
    char  comm[TASK_COMM_LEN];

    /* type-specific data */
    __u32 peer_tid;           /* for binder: peer thread id */
    __u32 peer_pid;
    char  peer_comm[TASK_COMM_LEN];

    __u64 value1;             /* futex: addr/cpufreq: khz/mem: order */
    __u64 value2;             /* futex: op/cpufreq: cluster/mem: gfp */
    __u64 duration_ns;        /* futex wait duration / IRQ duration etc */
};

/* SoftIRQ vector names (for reference) */
#define SOFTIRQ_VEC_HI      0
#define SOFTIRQ_VEC_TIMER   1
#define SOFTIRQ_VEC_NET_TX  2
#define SOFTIRQ_VEC_NET_RX  3
#define SOFTIRQ_VEC_BLOCK   4
#define SOFTIRQ_VEC_IRQ_POLL 5
#define SOFTIRQ_VEC_TASKLET 6
#define SOFTIRQ_VEC_SCHED   7
#define SOFTIRQ_VEC_HRTIMER 8
#define SOFTIRQ_VEC_RCU     9

#endif /* __TRACEPILOT_BPF_H__ */
