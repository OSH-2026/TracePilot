#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

#define TASK_COMM_LEN 16

/*
 * 传输到用户态的事件结构 (扩展版)
 * type: 0=switch, 1=wakeup, 2=binder_transaction, 3=binder_received, 4=futex
 *        5=cpu_frequency, 6=thermal_temperature
 */
struct event_t {
    u64 ts;                   // 时间戳 (ns, CLOCK_BOOTTIME)
    u32 tid;                  // 当前线程 TID
    u32 prev_tid;             // switch: 被切出的线程; binder: to_thread / futex: 0
    u32 tgid;                 // 进程组 ID
    u32 uid;                  // UID
    u32 debug_id;             // binder: debug_id (匹配 tx/rx 对); futex: uaddr 低32位
    u32 extra;                // binder: to_proc(高16) | code(低16); futex: futex_op
    int ret;                  // syscall 返回值 (futex)
    char comm[TASK_COMM_LEN]; // 线程名
    u8 type;                  // 事件类型
};

/* BPF Ring Buffer — 4MB 以承载高频 sched/binder/futex 事件, 避免溢出丢数据 */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 4 * 1024 * 1024);
} rb SEC(".maps");

volatile const u32 target_uid = 0;   /* 0=不过滤, 非0=内核侧按 UID 过滤 futex 事件 */

/* UID 过滤辅助: 只对 futex 使用。
 * sched_switch 不能按 UID 过滤(切出线程的 UID ≠ 目标),
 * sched_wakeup/binder 需跨进程数据不过滤。
 */
static __always_inline int skip_by_uid(void) {
    if (target_uid == 0) return 0;
    /* bpf_get_current_uid_gid() 返回 uid|gid 打包的 u64, 只取低32位 */
    return ((u32)bpf_get_current_uid_gid() != target_uid);
}

volatile const u32 target_pid = 0;

/* ─── 原有的 sched tracepoint 结构 (vmlinux.h 中已定义) ─── */
struct trace_event_raw_sched_wakeup {
    struct trace_entry ent;
    char comm[16];
    pid_t pid;
    int prio;
    int success;
    int target_cpu;
};

/* ─── sched_wakeup ─── */
SEC("tp/sched/sched_wakeup")
int handle_sched_wakeup(struct trace_event_raw_sched_wakeup *ctx)
{
    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type = 1;
    e->ts   = bpf_ktime_get_boot_ns();
    e->tid  = ctx->pid;
    e->prev_tid = 0;
    e->debug_id = 0;
    e->extra = 0;
    e->ret   = 0;

    u64 id = bpf_get_current_pid_tgid();
    e->tgid = id >> 32;
    e->uid  = bpf_get_current_uid_gid();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ─── sched_switch ─── */
SEC("tp/sched/sched_switch")
int handle_sched_switch(struct trace_event_raw_sched_switch *ctx)
{
    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type = 0;
    e->ts   = bpf_ktime_get_boot_ns();
    e->tid  = ctx->next_pid;
    e->prev_tid = ctx->prev_pid;
    e->debug_id = 0;
    e->extra = 0;
    e->ret   = 0;

    u64 id = bpf_get_current_pid_tgid();
    e->tgid = id >> 32;
    e->uid  = bpf_get_current_uid_gid();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ─── binder_transaction (客户端发起 Binder 调用) ─── */
SEC("tp/binder/binder_transaction")
int handle_binder_transaction(struct trace_event_raw_binder_transaction *ctx)
{
    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type = 2; // binder_transaction
    e->ts   = bpf_ktime_get_boot_ns();
    e->tid  = (u32)bpf_get_current_pid_tgid();  // 当前线程 = 调用方
    e->prev_tid = ctx->to_thread;               // 目标服务线程
    e->debug_id = ctx->debug_id;                // 用于匹配 received
    e->extra    = ((u32)ctx->to_proc << 16) | (ctx->code & 0xFFFF);
    e->ret      = ctx->reply;                   // 0=call, 1=reply (正负号)
    e->tgid     = (u32)(bpf_get_current_pid_tgid() >> 32);
    e->uid      = bpf_get_current_uid_gid();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ─── binder_transaction_received (服务端收到 Binder 调用) ─── */
SEC("tp/binder/binder_transaction_received")
int handle_binder_transaction_received(struct trace_event_raw_binder_transaction_received *ctx)
{
    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type = 3; // binder_received
    e->ts   = bpf_ktime_get_boot_ns();
    e->tid  = (u32)bpf_get_current_pid_tgid();  // 当前线程 = 服务端
    e->prev_tid = 0;
    e->debug_id = ctx->debug_id;                // 用于匹配 transaction
    e->extra = 0;
    e->ret   = 0;
    e->tgid  = (u32)(bpf_get_current_pid_tgid() >> 32);
    e->uid   = bpf_get_current_uid_gid();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ─── raw_syscalls/sys_enter (捕获所有系统调用, 过滤 futex) ───
 * 注意: 不能用 tp/syscalls/sys_enter_futex → 这颗内核没有这个跟踪点。
 * raw_syscalls/sys_enter 提供 syscall id + args[6], 我们在内核侧过滤。
 */
SEC("tp/raw_syscalls/sys_enter")
int handle_raw_sys_enter(struct trace_event_raw_sys_enter *ctx)
{
    /* UID 预过滤: futex 只在目标进程内分析, 减少 90% 系统调用事件 */
    if (skip_by_uid()) return 0;

    struct event_t *e;

    /* arm64 上 __NR_futex == 98, 只处理 futex 系统调用 */
    if (ctx->id != 98)
        return 0;

    /* 只关注 FUTEX_WAIT / FUTEX_WAKE (含 PRIVATE 变体) */
    int op = (int)ctx->args[1];
    int op_clean = op & 0x7F;
    if (op_clean != 0 && op_clean != 1) // 0=FUTEX_WAIT, 1=FUTEX_WAKE
        return 0;

    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type = 4; // futex
    e->ts   = bpf_ktime_get_boot_ns();
    e->tid  = (u32)bpf_get_current_pid_tgid();
    e->prev_tid = 0;
    /* uaddr 低 32 位作为匹配 key */
    e->debug_id = (u32)(ctx->args[0] & 0xFFFFFFFF);
    e->extra    = (u32)op;
    e->ret      = 0;
    e->tgid     = (u32)(bpf_get_current_pid_tgid() >> 32);
    e->uid      = bpf_get_current_uid_gid();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ─── cpu_frequency (记录 CPU 频率变化) ─── */
struct cpu_frequency_args {
    u64 __do_not_use__;
    u32 state;
    u32 cpu_id;
};

SEC("tp/power/cpu_frequency")
int handle_cpu_frequency(struct cpu_frequency_args *ctx)
{
    struct event_t *e;
    u32 freq_khz = ctx->state;
    if (freq_khz < 100000 || freq_khz > 5000000)
        return 0;

    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type = 5;
    e->ts   = bpf_ktime_get_boot_ns();
    e->tid  = ctx->cpu_id;
    e->extra = freq_khz / 1000;  /* kHz→MHz */
    e->tgid = 0; e->uid = 0; e->prev_tid = 0; e->debug_id = 0; e->ret = 0;
    __builtin_memset(&e->comm, 0, sizeof(e->comm));
    bpf_probe_read_kernel_str(&e->comm, sizeof(e->comm), "cpu_freq");

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ─── thermal_temperature (检测热降频) ─── */
struct thermal_temperature_args {
    u64 __do_not_use__;
    int id;
    int temp;
    int temp_crit;
    char thermal_zone[20];
};

SEC("tp/thermal/thermal_temperature")
int handle_thermal_temperature(struct thermal_temperature_args *ctx)
{
    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type = 6;
    e->ts   = bpf_ktime_get_boot_ns();
    e->tid  = ctx->id;
    e->extra = (u32)((u32)ctx->temp / 1000);  /* millicelsius→celsius */
    e->ret   = (int)((u32)ctx->temp_crit / 1000);
    e->tgid = 0; e->uid = 0; e->prev_tid = 0; e->debug_id = 0;
    bpf_probe_read_kernel_str(&e->comm, sizeof(e->comm), ctx->thermal_zone);

    bpf_ringbuf_submit(e, 0);
    return 0;
}

char LICENSE[] SEC("license") = "Dual BSD/GPL";
