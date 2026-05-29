/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef __FRAME_AGGREGATOR_H__
#define __FRAME_AGGREGATOR_H__

#include <stdint.h>
#include <stdio.h>

struct frame_window {
    int64_t  frame_token;
    uint64_t expected_start_ns;
    uint64_t expected_end_ns;
    uint64_t actual_end_ns;
    int      is_jank;
    double   delay_ms;
    uint64_t system_overhead_ns;  /* accumulated IRQ+softirq duration in window */
};

struct thread_score {
    uint32_t tid;
    uint32_t pid;
    double   score;
    char     comm[16];
    char     package[256];
    uint64_t runnable_delay_p95_ns;
    uint64_t wakeup_latency_p95_ns;
    uint64_t system_overhead_ns;  /* total IRQ+softirq time in frames this thread participated in */
};

/* Forward declarations from tracepilot.bpf.h */
struct sched_event;
struct system_event;

void frames_init(struct frame_window *frames, int num_frames);
void frames_set_clock_offset(int64_t offset_ns);
void aggregate_event(const struct sched_event *evt, const struct frame_window *fw);
void accumulate_system_event(const struct system_event *evt, struct frame_window *fw);
void frames_set_thread_info(uint32_t tid, uint32_t pid, const char *package);
int  output_topk(FILE *out, int top_k);

/* Re-parse frame JSON lines into frame_window array.
   Returns number of frames parsed, or -1 on error.
   Caller must free *out with free(). */
int  parse_frame_json(const char *filename, struct frame_window **out);

#endif /* __FRAME_AGGREGATOR_H__ */