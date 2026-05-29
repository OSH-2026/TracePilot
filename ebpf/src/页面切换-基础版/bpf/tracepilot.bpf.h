/* SPDX-License-Identifier: GPL-2.0 OR BSD-2-Clause */
#ifndef __TRACEPILOT_BPF_H__
#define __TRACEPILOT_BPF_H__

/*
 * Types: use __u64/__u32/__s32 to stay compatible with both BPF
 * (where vmlinux.h provides them) and userspace (where libbpf.h or
 * linux/types.h provides them).  No <stdint.h> — that pulls in glibc
 * headers which don't exist in the BPF target.
 */

\
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
    __u64 wakeup_latency_ns;       /* SWITCH: next task's wakeup→switch delay */
    __u64 next_runnable_delay_ns;  /* SWITCH: next task's last-preempt→switch delay */
};

struct system_event {
    __u64 timestamp_ns;
    __u32 event_type;          /* enum sys_event_type */
    __s32 irq_vec;             /* irq number or softirq vector */
    __u32 cpu;
    __u64 duration_ns;         /* computed from entry/exit pair */
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