/* SPDX-License-Identifier: BSD-2-Clause */
/*
 * Frame-window aggregation engine (sched + system behavior).
 *
 * Parses Perfetto trace data (via trace_processor_shell SQL output)
 * and aligns eBPF sched_events + system_events to frame windows.
 *
 * Input:
 *   1. frame_data JSON  -- extracted from Perfetto via trace_processor_shell
 *   2. sched_events     -- collected from eBPF ring buffer (sched_switch/wakeup)
 *   3. system_events    -- collected from eBPF ring buffer (IRQ/SoftIRQ)
 *
 * Output:
 *   Per-frame thread statistics: runnable_delay, wakeup_latency,
 *   frame_window_overlap, system_overhead (IRQ/SoftIRQ).
 *
 * System overhead adjustment:
 *   Thread scores are discounted proportional to IRQ/SoftIRQ time
 *   that overlapped with jank frames the thread participated in.
 *
 * Frame window definition:
 *   [expected_start - pre_margin, actual_end + post_margin]
 *   pre_margin  = 20ms
 *   post_margin = 10ms
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "frame_aggregator.h"
#include "../bpf/tracepilot.bpf.h"

#define PRE_MARGIN_NS  20000000UL
#define POST_MARGIN_NS 10000000UL

static struct frame_window  *g_frames = NULL;
static int                   g_num_frames = 0;
static int64_t               g_clock_offset_ns = 0;

/*
 * Thread stats accumulated across all jank frames for one thread.
 */
struct thread_stats {
    uint32_t tid;
    uint32_t pid;
    uint32_t uid;
    char     comm[16];
    char     package_name[256];
    int      role;

    /* Aggregated across jank frames */
    uint64_t total_runnable_delay_ns;
    uint64_t total_wakeup_latency_ns;
    uint64_t total_running_time_ns;
    uint64_t wakeup_count;
    uint64_t jank_frame_count;
    int64_t  last_jank_token;          /* dedup: track which frame was last counted */

    /* System overhead from frames this thread participated in */
    uint64_t frame_system_overlap_ns;

    /* Per-frame samples (for p95 computation) */
    uint64_t *runnable_delay_samples;
    uint64_t *wakeup_latency_samples;
    int       rd_num_samples;
    int       wl_num_samples;
};

#define MAX_THREADS  4096
#define MAX_SAMPLES  1024

static struct thread_stats g_threads[MAX_THREADS];
static int                 g_num_threads = 0;

/*
 * Lookup or create a thread_stats entry.
 */
static struct thread_stats *get_thread(uint32_t tid, const char *comm)
{
    int i;
    for (i = 0; i < g_num_threads; i++) {
        if (g_threads[i].tid == tid)
            return &g_threads[i];
    }
    if (g_num_threads >= MAX_THREADS)
        return NULL;
    struct thread_stats *ts = &g_threads[g_num_threads++];
    memset(ts, 0, sizeof(*ts));
    ts->tid = tid;
    ts->last_jank_token = -1;
    if (comm)
        strncpy(ts->comm, comm, sizeof(ts->comm) - 1);
    return ts;
}

/*
 * Check if a timestamp falls within a frame window.
 */
static int in_frame_window(uint64_t ts_ns, const struct frame_window *fw)
{
    int64_t adjusted_ts = (int64_t)ts_ns + g_clock_offset_ns;
    if (adjusted_ts < 0)
        return 0;
    uint64_t win_start = (fw->expected_start_ns > PRE_MARGIN_NS)
                       ? fw->expected_start_ns - PRE_MARGIN_NS : 0;
    uint64_t win_end   = fw->actual_end_ns + POST_MARGIN_NS;
    return ((uint64_t)adjusted_ts >= win_start && (uint64_t)adjusted_ts <= win_end);
}

/*
 * Aggregate one sched_event into the matching frame window(s).
 */
void aggregate_event(const struct sched_event *evt, const struct frame_window *fw)
{
    struct thread_stats *ts;

    if (!fw || !evt)
        return;
    if (!fw->is_jank)
        return;
    if (!in_frame_window(evt->timestamp_ns, fw))
        return;

    /* Task switched in */
    if (evt->event_type == EVENT_SCHED_SWITCH) {
        ts = get_thread(evt->next_tid, evt->next_comm);
        if (ts) {
            /* Count jank frame involvement once per thread per frame */
            if (ts->last_jank_token != fw->frame_token) {
                ts->last_jank_token = fw->frame_token;
                ts->jank_frame_count++;
                ts->frame_system_overlap_ns += fw->system_overhead_ns;
            }
            ts->total_running_time_ns += 1000000;
            if (evt->wakeup_latency_ns > 0) {
                ts->wakeup_count++;
                ts->total_wakeup_latency_ns += evt->wakeup_latency_ns;
                if (ts->wl_num_samples < MAX_SAMPLES) {
                    if (!ts->wakeup_latency_samples)
                        ts->wakeup_latency_samples = calloc(MAX_SAMPLES, sizeof(uint64_t));
                    if (ts->wakeup_latency_samples)
                        ts->wakeup_latency_samples[ts->wl_num_samples++] = evt->wakeup_latency_ns;
                }
            }
            /* Actual runnable delay: time this task waited since last preempted */
            if (evt->next_runnable_delay_ns > 0) {
                ts->total_runnable_delay_ns += evt->next_runnable_delay_ns;
                if (ts->rd_num_samples < MAX_SAMPLES) {
                    if (!ts->runnable_delay_samples)
                        ts->runnable_delay_samples = calloc(MAX_SAMPLES, sizeof(uint64_t));
                    if (ts->runnable_delay_samples)
                        ts->runnable_delay_samples[ts->rd_num_samples++] =
                            evt->next_runnable_delay_ns;
                }
            }
        }

        /* Prev task was preempted — its actual runnable delay will be computed
           when it is scheduled back in (as next in a later sched_switch). */
        if (evt->prev_tid > 0) {
            ts = get_thread(evt->prev_tid, evt->prev_comm);
            if (ts) {
                if (ts->last_jank_token != fw->frame_token) {
                    ts->last_jank_token = fw->frame_token;
                    ts->jank_frame_count++;
                    ts->frame_system_overlap_ns += fw->system_overhead_ns;
                }
            }
        }
    }
}

/*
 * Accumulate a system_event (IRQ/SoftIRQ) into the frame windows it overlaps.
 * Adds the event's duration to the frame's system_overhead_ns.
 */
void accumulate_system_event(const struct system_event *evt, struct frame_window *fw)
{
    if (!fw || !evt)
        return;
    if (!fw->is_jank)
        return;
    if (!in_frame_window(evt->timestamp_ns, fw))
        return;

    fw->system_overhead_ns += evt->duration_ns;
}

/*
 * Set pid and package name for an existing thread (lookup by tid only).
 * Does not create a new entry — only updates if the tid was already tracked.
 */
void frames_set_thread_info(uint32_t tid, uint32_t pid, const char *package)
{
	int i;
	for (i = 0; i < g_num_threads; i++) {
		if (g_threads[i].tid == tid) {
			g_threads[i].pid = pid;
			if (package)
				strncpy(g_threads[i].package_name, package,
					sizeof(g_threads[i].package_name) - 1);
			return;
		}
	}
}

/*
 * Initialize frame windows from parsed data.
 */
void frames_init(struct frame_window *frames, int num_frames)
{
    g_frames = frames;
    g_num_frames = num_frames;
}

/*
 * Set the clock offset between eBPF (CLOCK_MONOTONIC) and Perfetto (CLOCK_BOOTTIME).
 * eBPF timestamps are adjusted by this offset before frame window checks.
 */
void frames_set_clock_offset(int64_t offset_ns)
{
    g_clock_offset_ns = offset_ns;
}

/*
 * Parse frame data from trace_processor_shell output.
 * Input format (space-separated, one line per frame):
 *   frame_number intended_vsync_ns expected_start_ns expected_end_ns actual_present_ns is_jank delay_ms
 * Returns number of frames parsed, or -1 on error.
 * Caller must free *out with free().
 */
int parse_frame_json(const char *filename, struct frame_window **out)
{
	FILE *fp;
	char line[512];
	int cap = 256, cnt = 0;
	struct frame_window *frames;

	fp = fopen(filename, "r");
	if (!fp) {
		perror("fopen frame-data");
		return -1;
	}

	frames = calloc(cap, sizeof(*frames));
	if (!frames) {
		fclose(fp);
		return -1;
	}

	while (fgets(line, sizeof(line), fp)) {
		long long frame_token;
		unsigned long long intended_vsync, expected_start, expected_end, actual_end;
		int is_jank;
		double delay_ms;

		/* Skip column headers (CSV: "frame_number",...) */
		if (strncmp(line, "\"frame_number\"", 14) == 0)
			continue;
		if (line[0] == '"' && strstr(line, "frame_number"))
			continue;
		/* Skip empty lines and separator lines */
		if (line[0] == '\n' || line[0] == '-' || line[0] == '\r')
			continue;

		/* Parse CSV format: 7 comma-separated fields */
		if (sscanf(line, "%lld,%llu,%llu,%llu,%llu,%d,%lf",
			   &frame_token, &intended_vsync, &expected_start, &expected_end,
			   &actual_end, &is_jank, &delay_ms) < 6) {
			/* Retry with space-separated format (legacy) */
			if (sscanf(line, "%lld %llu %llu %llu %llu %d %lf",
				   &frame_token, &intended_vsync, &expected_start, &expected_end,
				   &actual_end, &is_jank, &delay_ms) < 6) {
				/* Retry with 6-field variant */
				if (sscanf(line, "%lld %llu %llu %llu %d %lf",
					   &frame_token, &intended_vsync, &expected_end,
					   &actual_end, &is_jank, &delay_ms) < 5)
					continue;
				expected_start = intended_vsync;
			}
		}

		if (cnt >= cap) {
			cap *= 2;
			struct frame_window *tmp = realloc(frames, cap * sizeof(*frames));
			if (!tmp) {
				free(frames);
				fclose(fp);
				return -1;
			}
			frames = tmp;
		}

		frames[cnt].frame_token        = frame_token;
		frames[cnt].expected_start_ns  = expected_start;
		frames[cnt].expected_end_ns    = expected_end;
		frames[cnt].actual_end_ns      = actual_end;
		frames[cnt].is_jank            = is_jank;
		frames[cnt].delay_ms           = delay_ms;
		frames[cnt].system_overhead_ns = 0;
		cnt++;
	}

	fclose(fp);
	*out = frames;
	return cnt;
}

/*
 * Get jank frame count.
 */
static int count_jank_frames(void)
{
    int count = 0;
    for (int i = 0; i < g_num_frames; i++)
        if (g_frames[i].is_jank)
            count++;
    return count;
}

/*
 * Compare two uint64_t values for qsort.
 */
static int cmp_u64(const void *a, const void *b)
{
    uint64_t va = *(const uint64_t *)a;
    uint64_t vb = *(const uint64_t *)b;
    if (va < vb) return -1;
    if (va > vb) return 1;
    return 0;
}

/*
 * Compute p95 from samples. Sorts a copy to avoid destroying original order.
 */
static uint64_t compute_p95(uint64_t *samples, int count)
{
    if (!samples || count == 0)
        return 0;

    uint64_t *sorted = malloc(count * sizeof(uint64_t));
    if (!sorted)
        return 0;
    memcpy(sorted, samples, count * sizeof(uint64_t));
    qsort(sorted, count, sizeof(uint64_t), cmp_u64);

    int idx = (int)(count * 0.95);
    if (idx >= count)
        idx = count - 1;
    uint64_t result = sorted[idx];
    free(sorted);
    return result;
}

/*
 * Compare thread_score entries by score (descending).
 */
static int compare_score(const void *a, const void *b)
{
    const struct thread_score *ta = (const struct thread_score *)a;
    const struct thread_score *tb = (const struct thread_score *)b;
    if (ta->score < tb->score) return 1;
    if (ta->score > tb->score) return -1;
    return 0;
}

/*
 * Output top-k critical threads as JSON.
 */
int output_topk(FILE *out, int top_k)
{
    int num_jank = count_jank_frames();
    int i, count = 0;
    struct thread_score *scores;

    scores = calloc(g_num_threads, sizeof(*scores));
    if (!scores)
        return -1;

    for (i = 0; i < g_num_threads; i++) {
        struct thread_stats *ts = &g_threads[i];
        if (ts->tid == 0)
            continue;

        double score = 0.0;
        uint64_t rd_p95 = compute_p95(ts->runnable_delay_samples, ts->rd_num_samples);
        uint64_t wl_p95 = compute_p95(ts->wakeup_latency_samples, ts->wl_num_samples);

        if (num_jank > 0)
            score += 0.35 * ((double)ts->jank_frame_count / num_jank);
        score += 0.35 * log1p((double)rd_p95 / 1e6);
        score += 0.15 * log1p((double)wl_p95 / 1e6);
        if (strstr(ts->comm, "RenderThread") || strstr(ts->comm, ".ui"))
            score += 0.15;

        /* Discount score when system overhead (IRQ/SoftIRQ) consumed frame time */
        double sys_ratio = 0.0;
        if (ts->jank_frame_count > 0) {
            double avg_overlap = (double)ts->frame_system_overlap_ns / ts->jank_frame_count;
            sys_ratio = avg_overlap / 16666666.0; /* fraction of one 16.67ms frame */
            if (sys_ratio > 0.9) sys_ratio = 0.9;
        }
        score *= (1.0 - sys_ratio);

        scores[count].tid   = ts->tid;
        scores[count].pid   = ts->pid;
        scores[count].score = score;
        snprintf(scores[count].comm, sizeof(scores[count].comm), "%s", ts->comm);
        snprintf(scores[count].package, sizeof(scores[count].package), "%s", ts->package_name);
        scores[count].runnable_delay_p95_ns = rd_p95;
        scores[count].wakeup_latency_p95_ns = wl_p95;
        scores[count].system_overhead_ns = ts->frame_system_overlap_ns;
        count++;
    }

    qsort(scores, count, sizeof(*scores), compare_score);

	uint64_t total_sys_overhead = 0;
	for (int j = 0; j < g_num_frames; j++)
		total_sys_overhead += g_frames[j].system_overhead_ns;

	fprintf(out, "{\n");
	fprintf(out, "  \"total_frames\": %d,\n", g_num_frames);
	fprintf(out, "  \"jank_frames\": %d,\n", num_jank);
	fprintf(out, "  \"jank_system_overhead_ns\": %llu,\n",
	        (unsigned long long)total_sys_overhead);
    fprintf(out, "  \"top_k_threads\": [\n");

    int limit = (top_k < count) ? top_k : count;
    for (i = 0; i < limit; i++) {
        struct thread_score *s = &scores[i];
        fprintf(out, "    {\n");
        fprintf(out, "      \"rank\": %d,\n", i + 1);
        fprintf(out, "      \"tid\": %u,\n", s->tid);
        fprintf(out, "      \"pid\": %u,\n", s->pid);
        fprintf(out, "      \"comm\": \"%s\",\n", s->comm);
        fprintf(out, "      \"package\": \"%s\",\n", s->package);
        fprintf(out, "      \"score\": %.4f,\n", s->score);
        fprintf(out, "      \"runnable_delay_p95_ns\": %llu,\n",
                (unsigned long long)s->runnable_delay_p95_ns);
        fprintf(out, "      \"wakeup_latency_p95_ns\": %llu,\n",
                (unsigned long long)s->wakeup_latency_p95_ns);
        fprintf(out, "      \"system_overhead_ns\": %llu\n",
                (unsigned long long)s->system_overhead_ns);
        fprintf(out, "    }%s\n", (i < limit - 1) ? "," : "");
    }

    fprintf(out, "  ]\n");
    fprintf(out, "}\n");

    free(scores);
    return 0;
}