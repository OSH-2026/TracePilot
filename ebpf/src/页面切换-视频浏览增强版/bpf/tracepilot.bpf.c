/* SPDX-License-Identifier: GPL-2.0 OR BSD-2-Clause */
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
	__uint(max_entries, 512);
	__type(key, __u64);
	__type(value, __u64);
} irq_start_times SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 128);
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

/* ══════ New maps for enhanced features ══════ */

struct {
	__uint(type, BPF_MAP_TYPE_RINGBUF);
	__uint(max_entries, 1 << 22);  /* 4MB for binder + futex + cpufreq + mem */
} enhanced_events SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 4096);
	__type(key, __u32);            /* tid */
	__type(value, __u64);          /* futex wait start timestamp */
} futex_wait_times SEC(".maps");

/* ── Helper: emit enhanced_event to ringbuffer ──────────────────────── */
static __always_inline void emit_enhanced(__u32 type, __u32 tid, __u32 pid,
                                          const char *comm,
                                          __u32 peer_tid, __u32 peer_pid,
                                          const char *peer_comm,
                                          __u64 val1, __u64 val2, __u64 dur)
{
	struct enhanced_event *e;
	e = bpf_ringbuf_reserve(&enhanced_events, sizeof(*e), 0);
	if (!e) return;

	__builtin_memset(e, 0, sizeof(*e));
	e->timestamp_ns = bpf_ktime_get_ns();
	e->type         = type;
	e->tid          = tid;
	e->pid          = pid;
	e->peer_tid     = peer_tid;
	e->peer_pid     = peer_pid;
	e->value1       = val1;
	e->value2       = val2;
	e->duration_ns  = dur;

	if (comm) {
		__builtin_memcpy(e->comm, comm, TASK_COMM_LEN);
		e->comm[TASK_COMM_LEN - 1] = 0;
	}
	if (peer_comm) {
		__builtin_memcpy(e->peer_comm, peer_comm, TASK_COMM_LEN);
		e->peer_comm[TASK_COMM_LEN - 1] = 0;
	}

	bpf_ringbuf_submit(e, 0);
}

/* ── sched_switch ── */
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

/* ── sched_wakeup ── */
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

/* ── irq_handler_entry ── */
SEC("tp_btf/irq_handler_entry")
int handle_irq_entry(struct trace_event_raw_irq_handler_entry *ctx)
{
	__u64 now = bpf_ktime_get_ns();
	__u64 key = ((__u64)bpf_get_smp_processor_id() << 32) | (__u32)(unsigned int)BPF_CORE_READ(ctx, irq);
	bpf_map_update_elem(&irq_start_times, &key, &now, BPF_ANY);
	return 0;
}

/* ── irq_handler_exit ── */
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

/* ── softirq_entry ── */
SEC("tp/irq/softirq_entry")
int handle_softirq_entry(struct trace_event_raw_softirq_entry *ctx)
{
	__u64 now = bpf_ktime_get_ns();
	__u64 key = ((__u64)bpf_get_smp_processor_id() << 32) | (__u64)ctx->vec;
	bpf_map_update_elem(&softirq_start_times, &key, &now, BPF_ANY);
	return 0;
}

/* ── softirq_exit ── */
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

/* ═══════════════════════════════════════════════════════════════
 * NEW: Enhanced BPF hooks for graph-based analysis
 * ═══════════════════════════════════════════════════════════════ */

/* ── binder_transaction: client side of binder call ────────────
 * raw_tp args:
 *   args[0] = struct binder_transaction *t
 *   args[1] = struct binder_node *target_node
 * Capture: from_tid (current), debug_id (exact match key)
 */
SEC("raw_tp/binder_transaction")
int handle_binder_xact(struct bpf_raw_tracepoint_args *ctx)
{
	__u32 tid = (__u32)(bpf_get_current_pid_tgid() & 0xFFFFFFFF);
	__u32 pid = (__u32)(bpf_get_current_pid_tgid() >> 32);
	char comm[TASK_COMM_LEN] = {};
	bpf_get_current_comm(comm, TASK_COMM_LEN);

	/* Read debug_id from binder_transaction struct for exact call matching */
	int debug_id = 0;
	struct binder_transaction *bt = (struct binder_transaction *)(long)ctx->args[0];
	bpf_probe_read_kernel(&debug_id, sizeof(debug_id), &bt->debug_id);

	emit_enhanced(ENH_EV_BINDER_CALL, tid, pid, comm,
	              0, 0, NULL, (__u64)debug_id, 0, 0);
	return 0;
}

/* ── binder_transaction_received: server side receives binder ─
 *    Fires when the binder server thread picks up a transaction.
 * raw_tp args:
 *   args[0] = struct binder_transaction *t
 * Capture: to_tid (current server thread), timestamp
 */
SEC("raw_tp/binder_transaction_received")
int handle_binder_received(struct bpf_raw_tracepoint_args *ctx)
{
	__u32 tid = (__u32)(bpf_get_current_pid_tgid() & 0xFFFFFFFF);
	__u32 pid = (__u32)(bpf_get_current_pid_tgid() >> 32);
	char comm[TASK_COMM_LEN] = {};
	bpf_get_current_comm(comm, TASK_COMM_LEN);

	/* Same debug_id as the original binder_transaction CALL */
	int debug_id = 0;
	struct binder_transaction *bt = (struct binder_transaction *)(long)ctx->args[0];
	bpf_probe_read_kernel(&debug_id, sizeof(debug_id), &bt->debug_id);

	emit_enhanced(ENH_EV_BINDER_RECEIVED, tid, pid, comm,
	              0, 0, NULL, (__u64)debug_id, 0, 0);
	return 0;
}

/* ── sys_enter: filter for futex FUTEX_WAIT ───────────────────
 *
 * ARM64 raw_tp/sys_enter layout:
 *   ctx->args[0] = struct pt_regs *regs
 *   ctx->args[1] = long id (syscall number)
 *
 * __NR_futex on ARM64 = 98
 *
 * We cannot directly read individual syscall arguments from raw_tp.
 * Instead, record the tid at sys_enter (if futex), and report
 * duration at sys_exit (when we find this tid in futex_wait_times).
 * The futex op matching is done by uaddr[1] check (futex addr),
 * but for simplicity we track all futex calls and use the
 * map presence to identify paired enter/exit.
 */
SEC("raw_tp/sys_enter")
int handle_sys_enter(struct bpf_raw_tracepoint_args *ctx)
{
	long sys_nr = ctx->args[1];
	if (sys_nr != 98)  /* __NR_futex ARM64 = 98 */
		return 0;

	__u64 now = bpf_ktime_get_ns();
	__u32 tid = (__u32)(bpf_get_current_pid_tgid() & 0xFFFFFFFF);
	__u32 pid = (__u32)(bpf_get_current_pid_tgid() >> 32);
	char comm[TASK_COMM_LEN] = {};
	bpf_get_current_comm(comm, TASK_COMM_LEN);

	bpf_map_update_elem(&futex_wait_times, &tid, &now, BPF_ANY);

	emit_enhanced(ENH_EV_FUTEX_WAIT, tid, pid, comm,
	              0, 0, NULL, 0, 0, 0);
	return 0;
}

/* ── sys_exit: detect futex return (wake from wait) ──────
 *
 * ARM64 raw_tp/sys_exit layout:
 *   ctx->args[0] = struct pt_regs *regs
 *   ctx->args[1] = long ret (return value)
 *
 * We cannot filter by syscall number here. Instead, check if
 * this tid was tracked by sys_enter (futex_wait_times map).
 * If present, it was a matching futex WAIT exit.
 */
SEC("raw_tp/sys_exit")
int handle_sys_exit(struct bpf_raw_tracepoint_args *ctx)
{
	__u64 now = bpf_ktime_get_ns();
	__u32 tid = (__u32)(bpf_get_current_pid_tgid() & 0xFFFFFFFF);
	__u32 pid = (__u32)(bpf_get_current_pid_tgid() >> 32);

	__u64 *start = bpf_map_lookup_elem(&futex_wait_times, &tid);
	if (!start)
		return 0;

	__u64 dur = now - *start;
	char comm[TASK_COMM_LEN] = {};
	bpf_get_current_comm(comm, TASK_COMM_LEN);

	emit_enhanced(ENH_EV_FUTEX_WAKE, tid, pid, comm,
	              0, 0, NULL, 0, 0, dur);

	bpf_map_delete_elem(&futex_wait_times, &tid);
	return 0;
}

/* ── cpu_frequency: track CPU frequency changes ───────────────
 * raw_tp args (kernel 6.1):
 *   ctx->args[0] = unsigned int freq (kHz)
 *   ctx->args[1] = unsigned int cpu_id
 */
SEC("raw_tp/cpu_frequency")
int handle_cpu_freq(struct bpf_raw_tracepoint_args *ctx)
{
	__u32 freq  = (__u32)ctx->args[0];
	__u32 cpu   = (__u32)ctx->args[1];

	/* Infer cluster: Pixel 6a: CPU0-3=little(A55), CPU4-5=big(A76), CPU6-7=prime(X1) */
	__u64 cluster = 0;
	if (cpu >= 6)       cluster = 2;
	else if (cpu >= 4)  cluster = 1;

	emit_enhanced(ENH_EV_CPU_FREQ, 0, 0, NULL, 0, 0, NULL,
	              freq, cluster, 0);
	return 0;
}

/* ── mm_vmscan_direct_reclaim_begin: memory reclaim ──────────
 * raw_tp args:
 *   args[0] = order
 *   args[1] = gfp_flags
 *   args[2] = node_id (not on all kernels, may be 0)
 */
SEC("raw_tp/mm_vmscan_direct_reclaim_begin")
int handle_mem_reclaim(struct bpf_raw_tracepoint_args *ctx)
{
	__u32 tid = (__u32)(bpf_get_current_pid_tgid() & 0xFFFFFFFF);
	__u32 pid = (__u32)(bpf_get_current_pid_tgid() >> 32);
	char comm[TASK_COMM_LEN] = {};
	bpf_get_current_comm(comm, TASK_COMM_LEN);

	__u64 order  = ctx->args[0];
	__u64 gfp    = ctx->args[1];

	emit_enhanced(ENH_EV_MEM_RECLAIM, tid, pid, comm,
	              0, 0, NULL, order, gfp, 0);
	return 0;
}
