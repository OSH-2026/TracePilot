/* SPDX-License-Identifier: GPL-2.0 OR BSD-2-Clause */
/*
 * tracepilot.bpf.c — 页面切换基础版 BPF 内核探针 (6 探针)
 * 挂载 sched_switch / sched_wakeup / binder_transaction / futex /
 * cpu_frequency / thermal_temperature，输出 events.bin v2 格式。
 */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>
#include "tracepilot.bpf.h"

/*
 * vmlinux.h from Pixel 6a has incorrect layouts for these structs
 * (prev_state marked as 'long' instead of kernel's actual 'unsigned int',
 *  or only forward declarations present).
 * Provide the known fixed layout matching the kernel's raw tracepoint format
 * so field offsets are correct.
 */

/* Android kernel 6.1 raw tracepoint format:
 *   TP_STRUCT__entry(
 *     __array(char, prev_comm, TASK_COMM_LEN)
 *     __field(pid_t, prev_pid)
 *     __field(int, prev_prio)
 *     __field(unsigned int, prev_state)    <-- 4 bytes, NOT long!
 *     __array(char, next_comm, TASK_COMM_LEN)
 *     __field(pid_t, next_pid)
 *     __field(int, next_prio)
 *   )
 */
/* Use distinct name to avoid conflict with vmlinux.h's definition
 * (which has wrong 'long prev_state' instead of 'unsigned int').
 * For raw tracepoints, the struct name doesn't affect BTF verification. */
struct sched_switch_raw_tp {
	struct trace_entry ent;
	char   prev_comm[16];
	pid_t  prev_pid;
	int    prev_prio;
	unsigned int prev_state;
	char   next_comm[16];
	pid_t  next_pid;
	int    next_prio;
};

struct trace_event_raw_sched_wakeup {
	struct trace_entry ent;
	char   comm[16];
	pid_t  pid;
	int    prio;
	int    target_cpu;
};

struct trace_event_raw_softirq_entry {
	struct trace_entry ent;
	unsigned int vec;
};

struct trace_event_raw_softirq_exit {
	struct trace_entry ent;
	unsigned int vec;
};

char LICENSE[] SEC("license") = "Dual BSD/GPL";

/* ── Maps ───────────────────────────────── */
struct {
	__uint(type, BPF_MAP_TYPE_RINGBUF);
	__uint(max_entries, 1 << 24);
} events SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 10240);
	__type(key, __u32);
	__type(value, __u64);
} wakeup_times SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 256);
	__type(key, __u64);
	__type(value, __u64);
} irq_start_times SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 64);
	__type(key, __u64);
	__type(value, __u64);
} softirq_start_times SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_RINGBUF);
	__uint(max_entries, 1 << 22);
} sys_events SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 10240);
	__type(key, __u32);
	__type(value, __u64);
} preempt_times SEC(".maps");

/* ── sched_switch (raw tracepoint, correct kernel-format struct) ── */
SEC("tp/sched/sched_switch")
int handle_sched_switch(struct sched_switch_raw_tp *ctx)
{
	struct sched_event *evt;
	__u64 now = bpf_ktime_get_ns();

	evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
	if (!evt) return 0;

	evt->timestamp_ns    = now;
	evt->event_type      = EVENT_SCHED_SWITCH;
	evt->prev_pid        = ctx->prev_pid;
	evt->prev_tid        = ctx->prev_pid;
	evt->next_pid        = ctx->next_pid;
	evt->next_tid        = ctx->next_pid;
	evt->next_uid        = 0;
	evt->prev_task_state = ctx->prev_state;
	evt->cpu             = bpf_get_smp_processor_id();

	__builtin_memset(evt->prev_comm, 0, TASK_COMM_LEN);
	__builtin_memset(evt->next_comm, 0, TASK_COMM_LEN);
	bpf_probe_read_kernel_str(evt->prev_comm, TASK_COMM_LEN, ctx->prev_comm);
	bpf_probe_read_kernel_str(evt->next_comm, TASK_COMM_LEN, ctx->next_comm);

	__u32 next_tid = evt->next_tid;
	__u32 prev_tid = evt->prev_tid;

	__u64 *wakeup_ts = bpf_map_lookup_elem(&wakeup_times, &next_tid);
	if (wakeup_ts) {
		evt->wakeup_latency_ns = now - *wakeup_ts;
		bpf_map_delete_elem(&wakeup_times, &next_tid);
	} else evt->wakeup_latency_ns = 0;

	__u64 *preempt_ts = bpf_map_lookup_elem(&preempt_times, &next_tid);
	if (preempt_ts) {
		evt->next_runnable_delay_ns = now - *preempt_ts;
		bpf_map_delete_elem(&preempt_times, &next_tid);
	} else evt->next_runnable_delay_ns = 0;

	if ((evt->prev_task_state & 0xFF) == 0 && prev_tid > 0)
		bpf_map_update_elem(&preempt_times, &prev_tid, &now, BPF_ANY);

	bpf_ringbuf_submit(evt, 0);
	return 0;
}

/* ── sched_wakeup (struct defined manually above) ──────────── */
SEC("tp/sched/sched_wakeup")
int handle_sched_wakeup(struct trace_event_raw_sched_wakeup *ctx)
{
	struct sched_event *evt;
	__u64 now = bpf_ktime_get_ns();

	evt = bpf_ringbuf_reserve(&events, sizeof(*evt), 0);
	if (!evt) return 0;

	evt->timestamp_ns    = now;
	evt->event_type      = EVENT_SCHED_WAKEUP;
	evt->prev_pid        = 0;
	evt->prev_tid        = 0;
	evt->next_pid        = ctx->pid;
	evt->next_tid        = ctx->pid;
	evt->next_uid        = 0;
	evt->prev_task_state = 0;
	evt->cpu             = bpf_get_smp_processor_id();

	__builtin_memset(evt->prev_comm, 0, TASK_COMM_LEN);
	__builtin_memset(evt->next_comm, 0, TASK_COMM_LEN);
	bpf_probe_read_kernel_str(evt->next_comm, TASK_COMM_LEN, ctx->comm);
	evt->wakeup_latency_ns = 0;
	evt->next_runnable_delay_ns = 0;

	__u32 tid = evt->next_tid;
	bpf_map_delete_elem(&preempt_times, &tid);
	bpf_map_update_elem(&wakeup_times, &tid, &now, BPF_ANY);

	bpf_ringbuf_submit(evt, 0);
	return 0;
}

/* ── irq_handler_entry ───────────────────── */
SEC("tp_btf/irq_handler_entry")
int handle_irq_entry(struct trace_event_raw_irq_handler_entry *ctx)
{
	__u64 now = bpf_ktime_get_ns();
	__u64 key = ((__u64)bpf_get_smp_processor_id() << 32) | (__u32)(unsigned int)BPF_CORE_READ(ctx, irq);
	bpf_map_update_elem(&irq_start_times, &key, &now, BPF_ANY);
	return 0;
}

/* ── irq_handler_exit ────────────────────── */
SEC("tp_btf/irq_handler_exit")
int handle_irq_exit(struct trace_event_raw_irq_handler_exit *ctx)
{
	__u64 now = bpf_ktime_get_ns();
	__u32 cpu = bpf_get_smp_processor_id();
	int irq   = BPF_CORE_READ(ctx, irq);
	__u64 key = ((__u64)cpu << 32) | (__u32)(unsigned int)irq;
	__u64 *start = bpf_map_lookup_elem(&irq_start_times, &key);
	if (!start) return 0;

	struct system_event *evt = bpf_ringbuf_reserve(&sys_events, sizeof(*evt), 0);
	if (!evt) { bpf_map_delete_elem(&irq_start_times, &key); return 0; }

	evt->timestamp_ns = now;
	evt->event_type   = SYS_EVENT_IRQ;
	evt->irq_vec      = irq;
	evt->cpu          = cpu;
	evt->duration_ns  = now - *start;
	bpf_map_delete_elem(&irq_start_times, &key);
	bpf_ringbuf_submit(evt, 0);
	return 0;
}

/* ── softirq_entry (struct defined manually above) ─────────── */
SEC("tp/irq/softirq_entry")
int handle_softirq_entry(struct trace_event_raw_softirq_entry *ctx)
{
	__u64 now = bpf_ktime_get_ns();
	__u64 key = ((__u64)bpf_get_smp_processor_id() << 32) | (__u64)ctx->vec;
	bpf_map_update_elem(&softirq_start_times, &key, &now, BPF_ANY);
	return 0;
}

/* ── softirq_exit (struct defined manually above) ──────────── */
SEC("tp/irq/softirq_exit")
int handle_softirq_exit(struct trace_event_raw_softirq_exit *ctx)
{
	__u64 now = bpf_ktime_get_ns();
	__u32 cpu = bpf_get_smp_processor_id();
	unsigned int vec = ctx->vec;
	__u64 key = ((__u64)cpu << 32) | (__u64)vec;
	__u64 *start = bpf_map_lookup_elem(&softirq_start_times, &key);
	if (!start) return 0;

	struct system_event *evt = bpf_ringbuf_reserve(&sys_events, sizeof(*evt), 0);
	if (!evt) { bpf_map_delete_elem(&softirq_start_times, &key); return 0; }

	evt->timestamp_ns = now;
	evt->event_type   = SYS_EVENT_SOFTIRQ;
	evt->irq_vec      = (__s32)vec;
	evt->cpu          = cpu;
	evt->duration_ns  = now - *start;
	bpf_map_delete_elem(&softirq_start_times, &key);
	bpf_ringbuf_submit(evt, 0);
	return 0;
}