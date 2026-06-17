#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

#define TASK_COMM_LEN 16

/*
 * 传输到用户态的事件结构 (扩展版)
 * type: 0=switch, 1=wakeup, 2=binder_transaction, 3=binder_received,
 *       4=futex_wait, 5=cpu_frequency, 6=thermal_temperature,
 *       9=futex_wake (sys_exit 匹配, 含 duration_ns)
 */
struct event_t {
    u64 ts;                   // 时间戳 (ns, CLOCK_BOOTTIME)
    u32 tid;                  // 当前线程 TID
    u32 prev_tid;             // switch: 被切出的线程; binder: to_thread / futex: 0
    u32 tgid;                 // 进程组 ID
    u32 uid;                  // UID
    u32 debug_id;             // binder: debug_id (匹配 tx/rx 对); futex: 0
    u32 extra;                // binder: to_proc(高16) | code(低16); cpu_freq: MHz; thermal: temp_c
    int ret;                  // futex_wake: duration_ns (截断到 int)
    char comm[TASK_COMM_LEN]; // 线程名 / "cpu_freq" / thermal_zone
    u8 type;                  // 事件类型
};

/* ─── IRQ 专用事件 (轻量, 独立 ring buffer, 避免污染主 event_t) ─── */
struct irq_event_t {
    u64 ts;
    u32 irq_nr;
    u32 cpu;
    u64 duration_ns;
};

/* BPF Ring Buffer — 4MB 以承载高频 sched/binder/futex 事件 */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 4 * 1024 * 1024);
} rb SEC(".maps");

/* IRQ 专用 Ring Buffer — 256KB, 系统级事件量小, 独立通道 */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);
} sys_rb SEC(".maps");

/* IRQ 开始时间: key=(cpu<<32|irq), value=entry timestamp */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 256);
    __type(key, u64);
    __type(value, u64);
} irq_start_times SEC(".maps");

/* Futex wait→wake 配对: key=tid, value=sys_enter timestamp */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, u32);
    __type(value, u64);
} futex_wait_times SEC(".maps");

volatile const u32 target_uid = 0;   /* 0=不过滤, 非0=内核侧按 UID 过滤 futex 事件 */
volatile const u32 target_pid = 0;

/* UID 过滤辅助: 只对 futex 使用。
 * sched_switch 不能按 UID 过滤(切出线程的 UID ≠ 目标),
 * sched_wakeup/binder 需跨进程数据不过滤。
 */
static __always_inline int skip_by_uid(void) {
    if (target_uid == 0) return 0;
    return ((u32)bpf_get_current_uid_gid() != target_uid);
}

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

/* ═══════════════════════════════════════════════════════════
 * binder_transaction / binder_transaction_received
 * 升级: tp/ → raw_tp/ (零参数序列化开销)
 *
 * raw_tp args: args[0] = struct binder_transaction *bt
 * 字段通过 BPF_CORE_READ 读取, CO-RE 保证跨内核兼容
 * ═══════════════════════════════════════════════════════════ */

SEC("raw_tp/binder_transaction")
int handle_binder_transaction(struct bpf_raw_tracepoint_args *ctx)
{
    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    struct binder_transaction *bt = (struct binder_transaction *)(long)ctx->args[0];

    int debug_id     = BPF_CORE_READ(bt, debug_id);
    u32 code         = BPF_CORE_READ(bt, code);
    struct binder_thread *to_thr = BPF_CORE_READ(bt, to_thread);
    int to_tid       = BPF_CORE_READ(to_thr, pid);
    struct binder_proc *to_prc = BPF_CORE_READ(bt, to_proc);
    int to_proc_pid  = BPF_CORE_READ(to_prc, pid);

    e->type     = 2; // binder_transaction
    e->ts       = bpf_ktime_get_boot_ns();
    e->tid      = (u32)bpf_get_current_pid_tgid();
    e->prev_tid = (u32)to_tid;
    e->debug_id = (u32)debug_id;
    e->extra    = ((u32)to_proc_pid << 16) | (code & 0xFFFF);
    e->ret      = 0; // raw_tp 不提供 reply 标志
    e->tgid     = (u32)(bpf_get_current_pid_tgid() >> 32);
    e->uid      = bpf_get_current_uid_gid();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

SEC("raw_tp/binder_transaction_received")
int handle_binder_transaction_received(struct bpf_raw_tracepoint_args *ctx)
{
    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    struct binder_transaction *bt = (struct binder_transaction *)(long)ctx->args[0];
    int debug_id = BPF_CORE_READ(bt, debug_id);

    e->type     = 3; // binder_received
    e->ts       = bpf_ktime_get_boot_ns();
    e->tid      = (u32)bpf_get_current_pid_tgid();
    e->prev_tid = 0;
    e->debug_id = (u32)debug_id;
    e->extra    = 0;
    e->ret      = 0;
    e->tgid     = (u32)(bpf_get_current_pid_tgid() >> 32);
    e->uid      = bpf_get_current_uid_gid();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ═══════════════════════════════════════════════════════════
 * sys_enter / sys_exit — futex 追踪 (raw_tp)
 *
 * ARM64 raw_tp/sys_enter: args[0]=regs, args[1]=syscall_id (__NR_futex=98)
 * raw_tp 无法直接读取 syscall 参数 (uaddr/op),
 * 改用 futex_wait_times map 记录 enter→exit 配对, 在 exit 端计算时长.
 * ═══════════════════════════════════════════════════════════ */

SEC("raw_tp/sys_enter")
int handle_sys_enter(struct bpf_raw_tracepoint_args *ctx)
{
    long sys_nr = ctx->args[1];
    if (sys_nr != 98)  /* __NR_futex ARM64 = 98 */
        return 0;

    /* UID 预过滤: 只追踪目标进程的 futex, 减少 90% 事件量 */
    if (skip_by_uid()) return 0;

    u64 now = bpf_ktime_get_boot_ns();
    u32 tid = (u32)bpf_get_current_pid_tgid();

    /* 记录 tid → enter_ts, 供 sys_exit 匹配并计算 futex 时长 */
    bpf_map_update_elem(&futex_wait_times, &tid, &now, BPF_ANY);

    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type     = 4; // futex_wait
    e->ts       = now;
    e->tid      = tid;
    e->prev_tid = 0;
    e->debug_id = 0;
    e->extra    = 0;
    e->ret      = 0;
    e->tgid     = (u32)(bpf_get_current_pid_tgid() >> 32);
    e->uid      = bpf_get_current_uid_gid();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ARM64 raw_tp/sys_exit: args[0]=regs, args[1]=retval
 * 无法按 syscall number 过滤 → 通过 futex_wait_times map 判断是否 futex 返回 */
SEC("raw_tp/sys_exit")
int handle_sys_exit(struct bpf_raw_tracepoint_args *ctx)
{
    u64 now = bpf_ktime_get_boot_ns();
    u32 tid = (u32)bpf_get_current_pid_tgid();

    u64 *start = bpf_map_lookup_elem(&futex_wait_times, &tid);
    if (!start)
        return 0;

    u64 dur = now - *start;

    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) {
        bpf_map_delete_elem(&futex_wait_times, &tid);
        return 0;
    }

    e->type     = 9; // futex_wake (exit 匹配, 区别于 type=4 的 enter)
    e->ts       = now;
    e->tid      = tid;
    e->prev_tid = 0;
    e->debug_id = 0;
    e->extra    = 1; // 标记 wake 侧
    e->ret      = (int)(dur & 0x7FFFFFFF); // duration_ns (截断到 int, 避免负数)
    e->tgid     = (u32)(bpf_get_current_pid_tgid() >> 32);
    e->uid      = bpf_get_current_uid_gid();
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    bpf_ringbuf_submit(e, 0);
    bpf_map_delete_elem(&futex_wait_times, &tid);
    return 0;
}

/* ═══════════════════════════════════════════════════════════
 * cpu_frequency — raw_tp 升级
 *
 * raw_tp args (kernel 6.1):
 *   args[0] = unsigned int freq_khz
 *   args[1] = unsigned int cpu_id
 * ═══════════════════════════════════════════════════════════ */

SEC("raw_tp/cpu_frequency")
int handle_cpu_frequency(struct bpf_raw_tracepoint_args *ctx)
{
    u32 freq_khz = (u32)ctx->args[0];
    if (freq_khz < 100000 || freq_khz > 5000000)
        return 0;

    u32 cpu = (u32)ctx->args[1];

    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type     = 5;
    e->ts       = bpf_ktime_get_boot_ns();
    e->tid      = cpu;                  // 复用 tid 存 cpu_id
    e->extra    = freq_khz / 1000;      // kHz → MHz
    e->tgid     = 0;
    e->uid      = 0;
    e->prev_tid = 0;
    e->debug_id = 0;
    e->ret      = 0;
    __builtin_memset(&e->comm, 0, sizeof(e->comm));
    bpf_probe_read_kernel_str(&e->comm, sizeof(e->comm), "cpu_freq");

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ═══════════════════════════════════════════════════════════
 * thermal_temperature — raw_tp 升级
 *
 * raw_tp args (kernel 6.1):
 *   args[0] = struct thermal_zone_device *tz
 *   args[1] = int temperature (millicelsius)
 * ═══════════════════════════════════════════════════════════ */

SEC("raw_tp/thermal_temperature")
int handle_thermal_temperature(struct bpf_raw_tracepoint_args *ctx)
{
    s32 temp_mc = (s32)(long)ctx->args[1]; // millicelsius

    struct thermal_zone_device *tz = (struct thermal_zone_device *)(long)ctx->args[0];

    struct event_t *e;
    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->type     = 6;
    e->ts       = bpf_ktime_get_boot_ns();
    e->tid      = BPF_CORE_READ(tz, id);           // zone id
    e->extra    = (u32)((u32)temp_mc / 1000);           // millicelsius → celsius (BPF 不支持有符号除法)
    e->ret      = 0;
    e->tgid     = 0;
    e->uid      = 0;
    e->prev_tid = 0;
    e->debug_id = 0;
    /* 读取 thermal zone type 名称 */
    __builtin_memset(&e->comm, 0, sizeof(e->comm));
    if (bpf_core_type_exists(struct thermal_zone_device)) {
        bpf_probe_read_kernel_str(&e->comm, sizeof(e->comm),
            __builtin_preserve_access_index(((struct thermal_zone_device *)0)->type));
    }
    if (e->comm[0] == 0)
        bpf_probe_read_kernel_str(&e->comm, sizeof(e->comm), "thermal");

    bpf_ringbuf_submit(e, 0);
    return 0;
}

/* ═══════════════════════════════════════════════════════════
 * IRQ 追踪 — 新增 (tp_btf, BTF-powered tracepoint)
 *
 * tp_btf 比 tp/ 更高效: 用 BTF 而非 tracepoint format string
 * 解析参数, layout 由内核 BTF 保证正确.
 * ═══════════════════════════════════════════════════════════ */

SEC("tp_btf/irq_handler_entry")
int handle_irq_entry(struct trace_event_raw_irq_handler_entry *ctx)
{
    u64 now = bpf_ktime_get_boot_ns();
    u32 cpu = bpf_get_smp_processor_id();
    int irq = BPF_CORE_READ(ctx, irq);

    u64 key = ((u64)cpu << 32) | (u32)(unsigned int)irq;
    bpf_map_update_elem(&irq_start_times, &key, &now, BPF_ANY);
    return 0;
}

SEC("tp_btf/irq_handler_exit")
int handle_irq_exit(struct trace_event_raw_irq_handler_exit *ctx)
{
    u64 now = bpf_ktime_get_boot_ns();
    u32 cpu = bpf_get_smp_processor_id();
    int irq = BPF_CORE_READ(ctx, irq);

    u64 key = ((u64)cpu << 32) | (u32)(unsigned int)irq;
    u64 *start = bpf_map_lookup_elem(&irq_start_times, &key);
    if (!start) return 0;

    struct irq_event_t *e;
    e = bpf_ringbuf_reserve(&sys_rb, sizeof(*e), 0);
    if (!e) {
        bpf_map_delete_elem(&irq_start_times, &key);
        return 0;
    }

    e->ts          = now;
    e->irq_nr      = (u32)(unsigned int)irq;
    e->cpu         = cpu;
    e->duration_ns = now - *start;

    bpf_map_delete_elem(&irq_start_times, &key);
    bpf_ringbuf_submit(e, 0);
    return 0;
}

char LICENSE[] SEC("license") = "Dual BSD/GPL";
