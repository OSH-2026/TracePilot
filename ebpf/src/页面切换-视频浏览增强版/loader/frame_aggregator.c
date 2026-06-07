/* SPDX-License-Identifier: BSD-2-Clause */
/*
 * Frame-window aggregation engine — Enhanced with Interaction Critical Path Graph.
 *
 * New capabilities (v2):
 *   1. Binder dependency graph     — build BINDER_CALL edges
 *   2. Futex wait graph           — build FUTEX_WAIT edges
 *   3. CPU frequency / big-little — cpufreq-aware scoring
 *   4. Jank cause classifier      — per-frame root cause classification
 *   5. Heuristic strategy comparison — graph vs old heuristic baseline
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

#include "frame_aggregator.h"
#include "hint_engine.h"
#include "identity.h"
#include "inference_engine.h"
#include "thermal_profile.h"
#include "../bpf/tracepilot.bpf.h"

#define PRE_MARGIN_NS  20000000UL
#define POST_MARGIN_NS 10000000UL
#define MAX_CPUS        16
static struct frame_window  *g_frames = NULL;
static int                   g_num_frames = 0;
static int64_t               g_clock_offset_ns = 0;

/* ═════════════════════════════════════════════════════════════════════
 * Legacy thread stats (preserved for backward compat)
 * ═════════════════════════════════════════════════════════════════════ */

struct thread_stats {
    uint32_t tid;
    uint32_t pid;
    uint32_t uid;
    char     comm[16];
    char     package_name[256];
    int      role;

    uint64_t total_runnable_delay_ns;
    uint64_t total_wakeup_latency_ns;
    uint64_t total_running_time_ns;
    uint64_t wakeup_count;
    uint64_t jank_frame_count;
    int64_t  last_jank_token;

    uint64_t frame_system_overlap_ns;

    uint64_t *runnable_delay_samples;
    uint64_t *wakeup_latency_samples;
    int       rd_num_samples;
    int       wl_num_samples;
};

#define MAX_THREADS  4096
#define MAX_SAMPLES  1024

static struct thread_stats g_threads[MAX_THREADS];
static int                 g_num_threads = 0;

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

void aggregate_event(const struct sched_event *evt, const struct frame_window *fw)
{
    struct thread_stats *ts;
    if (!fw || !evt) return;
    if (!fw->is_jank) return;
    if (!in_frame_window(evt->timestamp_ns, fw)) return;

    if (evt->event_type == EVENT_SCHED_SWITCH) {
        ts = get_thread(evt->next_tid, evt->next_comm);
        if (ts) {
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

void accumulate_system_event(const struct system_event *evt, struct frame_window *fw)
{
    if (!fw || !evt) return;
    if (!fw->is_jank) return;
    if (!in_frame_window(evt->timestamp_ns, fw)) return;
    fw->system_overhead_ns += evt->duration_ns;
}

void frames_set_thread_info(uint32_t tid, uint32_t pid, const char *package)
{
    int i;
    for (i = 0; i < g_num_threads; i++) {
        if (g_threads[i].tid == tid) {
            g_threads[i].pid = pid;
            if (package)
                strncpy(g_threads[i].package_name, package, 256);
            return;
        }
    }
}

void frames_init(struct frame_window *frames, int num_frames)
{
    g_frames = frames;
    g_num_frames = num_frames;
}

void frames_set_clock_offset(int64_t offset_ns)
{
    g_clock_offset_ns = offset_ns;
}

int parse_frame_json(const char *filename, struct frame_window **out)
{
    FILE *fp;
    char line[512];
    int cap = 256, cnt = 0;
    struct frame_window *frames;

    fp = fopen(filename, "r");
    if (!fp) { perror("fopen frame-data"); return -1; }

    frames = calloc(cap, sizeof(*frames));
    if (!frames) { fclose(fp); return -1; }

    while (fgets(line, sizeof(line), fp)) {
        long long frame_token;
        unsigned long long intended_vsync, expected_start, expected_end, actual_end;
        int is_jank;
        double delay_ms;
        char ftype[4] = {0};
        int fields;

        if (strncmp(line, "\"frame_number\"", 14) == 0) continue;
        if (line[0] == '"' && strstr(line, "frame_number")) continue;
        if (line[0] == '\n' || line[0] == '-' || line[0] == '\r') continue;

        /* Parse format: frame_type frame_token intended expected_start expected_end actual_end is_jank delay_ms */
        /* Support both space, comma, and pipe delimiters */
        fields = sscanf(line, "%3s %lld %llu %llu %llu %llu %d %lf",
                        ftype, &frame_token, &intended_vsync,
                        &expected_start, &expected_end, &actual_end,
                        &is_jank, &delay_ms);
        if (fields < 6) {
            if (sscanf(line, "%lld,%llu,%llu,%llu,%llu,%d,%lf",
                       &frame_token, &intended_vsync, &expected_start, &expected_end,
                       &actual_end, &is_jank, &delay_ms) < 6) {
                if (sscanf(line, "%lld %llu %llu %llu %llu %d %lf",
                           &frame_token, &intended_vsync, &expected_start, &expected_end,
                           &actual_end, &is_jank, &delay_ms) < 6) {
                    if (sscanf(line, "%lld %llu %llu %llu %d %lf",
                               &frame_token, &intended_vsync, &expected_end,
                               &actual_end, &is_jank, &delay_ms) < 5) {
                        /* Try pipe-delimited format (trace_processor_shell default) */
                        char ftype_pipe[4] = {0};
                        if (sscanf(line, "%3[^|]|%lld|%llu|%llu|%llu|%llu|%d|%lf",
                                   ftype_pipe, &frame_token, &intended_vsync,
                                   &expected_start, &expected_end, &actual_end,
                                   &is_jank, &delay_ms) >= 6) {
                            memcpy(ftype, ftype_pipe, 3);
                            ftype[3] = 0;
                        } else {
                            /* Try CSV format: "XX",int,int,int,int,int,int,float */
                            char ftype_csv[4] = {0};
                            if (sscanf(line, "\"%3[^\"]\",%lld,%llu,%llu,%llu,%llu,%d,%lf",
                                       ftype_csv, &frame_token, &intended_vsync,
                                       &expected_start, &expected_end, &actual_end,
                                       &is_jank, &delay_ms) >= 6) {
                                memcpy(ftype, ftype_csv, 3);
                                ftype[3] = 0;
                            } else
                                continue;
                        }
                    } else {
                        expected_start = intended_vsync;
                    }
                }
            }
            /* Default to SF if no frame_type parsed via non-pipe paths */
            if (!ftype[0]) { ftype[0] = 'S'; ftype[1] = 'F'; ftype[2] = 0; }
        }

        if (cnt >= cap) {
            cap *= 2;
            struct frame_window *tmp = realloc(frames, cap * sizeof(*frames));
            if (!tmp) { free(frames); fclose(fp); return -1; }
            frames = tmp;
        }

        memset(&frames[cnt], 0, sizeof(frames[cnt]));
        frames[cnt].frame_token        = frame_token;
        frames[cnt].expected_start_ns  = expected_start;
        frames[cnt].expected_end_ns    = expected_end;
        frames[cnt].actual_end_ns      = actual_end;
        frames[cnt].is_jank            = is_jank;
        frames[cnt].delay_ms           = delay_ms;
        memcpy(frames[cnt].frame_type, ftype, 3);
        frames[cnt].is_video_frame     = (ftype[0] == 'V' && ftype[1] == 'D');
        frames[cnt].gpu_duration_ns    = (ftype[0] == 'G' && ftype[1] == 'S') ? actual_end - expected_start : 0;
        frames[cnt].system_overhead_ns = 0;
        cnt++;
    }
    fclose(fp);
    *out = frames;
    return cnt;
}

static int count_jank_frames(void)
{
    int count = 0;
    for (int i = 0; i < g_num_frames; i++)
        if (g_frames[i].is_jank) count++;
    return count;
}

static int cmp_u64(const void *a, const void *b)
{
    uint64_t va = *(const uint64_t *)a;
    uint64_t vb = *(const uint64_t *)b;
    if (va < vb) return -1;
    if (va > vb) return 1;
    return 0;
}

static uint64_t compute_p95(uint64_t *samples, int count)
{
    if (!samples || count == 0) return 0;
    uint64_t *sorted = malloc(count * sizeof(uint64_t));
    if (!sorted) return 0;
    memcpy(sorted, samples, count * sizeof(uint64_t));
    qsort(sorted, count, sizeof(uint64_t), cmp_u64);
    int idx = (int)(count * 0.95);
    if (idx >= count) idx = count - 1;
    uint64_t result = sorted[idx];
    free(sorted);
    return result;
}

#define SCHED_TRACK_MAP_CAP 65536
#define NODE_DELAY_SAMPLES  512

typedef struct {
    int32_t  keys[SCHED_TRACK_MAP_CAP];
    uint64_t vals[SCHED_TRACK_MAP_CAP];
} sched_track_map_t;

typedef struct {
    uint64_t rd[NODE_DELAY_SAMPLES];
    uint64_t wl[NODE_DELAY_SAMPLES];
    int rd_n;
    int wl_n;
} node_delay_buf_t;

static void track_map_clear(sched_track_map_t *m)
{
    for (int i = 0; i < SCHED_TRACK_MAP_CAP; i++)
        m->keys[i] = INT32_MIN;
}

static void track_map_put(sched_track_map_t *m, int32_t tid, uint64_t ts)
{
    uint32_t idx = (uint32_t)((tid * 2654435761U) % SCHED_TRACK_MAP_CAP);

    for (uint32_t p = 0; p < SCHED_TRACK_MAP_CAP; p++) {
        if (m->keys[idx] == INT32_MIN || m->keys[idx] == tid) {
            m->keys[idx] = tid;
            m->vals[idx] = ts;
            return;
        }
        idx = (idx + 1) % SCHED_TRACK_MAP_CAP;
    }
}

static uint64_t track_map_take(sched_track_map_t *m, int32_t tid)
{
    uint32_t idx = (uint32_t)((tid * 2654435761U) % SCHED_TRACK_MAP_CAP);

    for (uint32_t p = 0; p < SCHED_TRACK_MAP_CAP; p++) {
        if (m->keys[idx] == tid) {
            uint64_t v = m->vals[idx];
            m->keys[idx] = INT32_MIN;
            return v;
        }
        if (m->keys[idx] == INT32_MIN)
            return 0;
        idx = (idx + 1) % SCHED_TRACK_MAP_CAP;
    }
    return 0;
}

static void track_map_del(sched_track_map_t *m, int32_t tid)
{
    uint32_t idx = (uint32_t)((tid * 2654435761U) % SCHED_TRACK_MAP_CAP);

    for (uint32_t p = 0; p < SCHED_TRACK_MAP_CAP; p++) {
        if (m->keys[idx] == tid) {
            m->keys[idx] = INT32_MIN;
            return;
        }
        if (m->keys[idx] == INT32_MIN)
            return;
        idx = (idx + 1) % SCHED_TRACK_MAP_CAP;
    }
}

static int32_t sched_event_next_tid(const struct sched_event *ev)
{
    if (ev->next_tid > 0)
        return (int32_t)ev->next_tid;
    if (ev->next_pid > 0)
        return (int32_t)ev->next_pid;
    return 0;
}

static int32_t sched_event_prev_tid(const struct sched_event *ev)
{
    if (ev->prev_tid > 0)
        return (int32_t)ev->prev_tid;
    if (ev->prev_pid > 0)
        return (int32_t)ev->prev_pid;
    return 0;
}

static void node_delay_add(node_delay_buf_t *buf, uint64_t rd, uint64_t wl)
{
    if (!buf)
        return;
    if (rd > 0 && buf->rd_n < NODE_DELAY_SAMPLES)
        buf->rd[buf->rd_n++] = rd;
    if (wl > 0 && buf->wl_n < NODE_DELAY_SAMPLES)
        buf->wl[buf->wl_n++] = wl;
}

static int compare_score(const void *a, const void *b)
{
    const struct thread_score *ta = (const struct thread_score *)a;
    const struct thread_score *tb = (const struct thread_score *)b;
    if (ta->score < tb->score) return 1;
    if (ta->score > tb->score) return -1;
    return 0;
}

int output_topk(FILE *out, int top_k)
{
    int num_jank = count_jank_frames();
    int i, count = 0;
    struct thread_score *scores;

    scores = calloc(g_num_threads, sizeof(*scores));
    if (!scores) return -1;

    for (i = 0; i < g_num_threads; i++) {
        struct thread_stats *ts = &g_threads[i];
        if (ts->tid == 0) continue;

        double score = 0.0;
        uint64_t rd_p95 = compute_p95(ts->runnable_delay_samples, ts->rd_num_samples);
        uint64_t wl_p95 = compute_p95(ts->wakeup_latency_samples, ts->wl_num_samples);

        if (num_jank > 0)
            score += 0.35 * ((double)ts->jank_frame_count / num_jank);
        score += 0.35 * log1p((double)rd_p95 / 1e6);
        score += 0.15 * log1p((double)wl_p95 / 1e6);
        if (strstr(ts->comm, "RenderThread") || strstr(ts->comm, ".ui"))
            score += 0.15;

        double sys_ratio = 0.0;
        if (ts->jank_frame_count > 0) {
            double avg_overlap = (double)ts->frame_system_overlap_ns / ts->jank_frame_count;
            sys_ratio = avg_overlap / 16666666.0;
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
    fprintf(out, "  ]\n}\n");
    free(scores);
    return 0;
}

/* ═════════════════════════════════════════════════════════════════════
 * ENHANCED: Graph-based analysis engine
 * ═════════════════════════════════════════════════════════════════════ */

/* ── Graph helper functions ─────────────────────────────────────────── */

static int graph_tid_map_grow(critical_path_graph_t *g)
{
    uint32_t new_cap = g->tid_map_cap * 2;
    int32_t *new_map;
    uint32_t *new_node;
    uint32_t i;

    if (new_cap <= g->tid_map_cap)
        return -1;

    new_map = malloc(new_cap * sizeof(int32_t));
    new_node = malloc(new_cap * sizeof(uint32_t));
    if (!new_map || !new_node) {
        free(new_map);
        free(new_node);
        return -1;
    }
    for (i = 0; i < new_cap; i++)
        new_map[i] = INT32_MIN;

    for (i = 0; i < g->tid_map_cap; i++) {
        int32_t tid;
        uint32_t nid, idx;

        if (g->tid_map[i] == INT32_MIN)
            continue;
        tid = g->tid_map[i];
        nid = g->tid_map_node[i];
        idx = (uint32_t)((tid * 2654435761U) % new_cap);
        while (new_map[idx] != INT32_MIN)
            idx = (idx + 1) % new_cap;
        new_map[idx] = tid;
        new_node[idx] = nid;
    }

    free(g->tid_map);
    free(g->tid_map_node);
    g->tid_map = new_map;
    g->tid_map_node = new_node;
    g->tid_map_cap = new_cap;
    return 0;
}

/* Returns stable node ID (index into g->nodes). IDs never change even
 * after realloc. Use &g->nodes[id] to access the node struct. */
static uint32_t graph_find_or_create(critical_path_graph_t *g,
    graph_node_type_t type, int32_t tid, int32_t pid,
    const char *comm, const char *pkg)
{
    /* Fast hash map lookup (if available) */
    if (g->tid_map && tid != 0) {
        uint32_t idx, probes = 0;
        int use_hash = 1;

        /* Expand before load exceeds ~75% to keep probing cheap */
        if (g->node_count * 4 >= g->tid_map_cap * 3)
            graph_tid_map_grow(g);

        idx = (uint32_t)((tid * 2654435761U) % g->tid_map_cap);
        while (g->tid_map[idx] != INT32_MIN) {
            if (g->tid_map[idx] == tid)
                return g->tid_map_node[idx];
            idx = (idx + 1) % g->tid_map_cap;
            if (++probes >= g->tid_map_cap) {
                if (graph_tid_map_grow(g) < 0) {
                    use_hash = 0;
                    break;
                }
                idx = (uint32_t)((tid * 2654435761U) % g->tid_map_cap);
                probes = 0;
            }
        }

        if (use_hash) {
            uint32_t nid;

            if (g->node_count >= g->node_capacity) {
                uint32_t nc = g->node_capacity * 2;
                graph_node_t *nn = realloc(g->nodes, nc * sizeof(graph_node_t));
                if (!nn) return (uint32_t)-1;
                memset(nn + g->node_capacity, 0, (nc - g->node_capacity) * sizeof(graph_node_t));
                g->nodes = nn;
                g->node_capacity = nc;
            }

            nid = g->node_count;
            graph_node_t *n = &g->nodes[nid];
            memset(n, 0, sizeof(*n));
            n->id   = nid;
            n->type = type;
            n->tid  = tid;
            n->pid  = pid;
            if (comm) strncpy(n->comm, comm, sizeof(n->comm) - 1);
            if (pkg)  strncpy(n->pkg, pkg, sizeof(n->pkg) - 1);
            g->node_count++;

            g->tid_map[idx] = tid;
            g->tid_map_node[idx] = nid;
            return nid;
        }
    }

    /* Fallback: linear search (hash unavailable or table full) */
    for (uint32_t i = 0; i < g->node_count; i++) {
        if (g->nodes[i].tid == tid && tid > 0)
            return i;
    }

    /* Grow if needed */
    if (g->node_count >= g->node_capacity) {
        uint32_t nc = g->node_capacity * 2;
        graph_node_t *nn = realloc(g->nodes, nc * sizeof(graph_node_t));
        if (!nn) return (uint32_t)-1;
        memset(nn + g->node_capacity, 0, (nc - g->node_capacity) * sizeof(graph_node_t));
        g->nodes = nn;
        g->node_capacity = nc;
    }

    uint32_t id = g->node_count;
    graph_node_t *n = &g->nodes[id];
    memset(n, 0, sizeof(*n));
    n->id   = id;
    n->type = type;
    n->tid  = tid;
    n->pid  = pid;
    if (comm) strncpy(n->comm, comm, sizeof(n->comm) - 1);
    if (pkg)  strncpy(n->pkg, pkg, sizeof(n->pkg) - 1);
    g->node_count++;
    return id;
}

static void graph_add_edge_nodes(critical_path_graph_t *g,
    uint32_t from, uint32_t to, graph_edge_type_t type, uint64_t dur_ns)
{
    if (from >= g->node_count || to >= g->node_count) return;

    /* Fast path: use edge hash map if available */
    if (g->edge_hash_cap > 0) {
        uint64_t ek = ((uint64_t)from << 32) | ((uint64_t)to << 4) | (uint64_t)type;
        if (ek == UINT64_MAX) ek = UINT64_MAX - 1; /* avoid sentinel collision */
        uint32_t ei = (uint32_t)((ek * 11400714819323198485ULL) % g->edge_hash_cap);
        uint32_t probe_limit = 128;
        while (g->edge_hash_key[ei] != UINT64_MAX) {
            if (g->edge_hash_key[ei] == ek) {
                adj_entry_t *e = g->edge_hash_val[ei];
                e->weight.duration_ns += dur_ns;
                e->weight.count++;
                if (dur_ns > e->weight.p95_ns * 0.95)
                    e->weight.p95_ns = (e->weight.p95_ns * 3 + dur_ns) / 4;
                return;
            }
            if (--probe_limit == 0) break; /* fallback: table full, use linear scan */
            ei = (ei + 1) % g->edge_hash_cap;
        }
        if (probe_limit > 0) {
            /* Found empty slot — create new edge */
            adj_entry_t *oe = calloc(1, sizeof(*oe));
            if (!oe) return;
            oe->target = to;
            oe->type   = type;
            oe->weight.duration_ns = dur_ns;
            oe->weight.count = 1;
            oe->weight.p95_ns = dur_ns;
            oe->next = g->nodes[from].out_edges;
            g->nodes[from].out_edges = oe;
            g->nodes[from].out_degree++;

            adj_entry_t *ie = calloc(1, sizeof(*ie));
            if (!ie) { g->nodes[from].out_edges = oe->next; free(oe); return; }
            ie->target = from;
            ie->type   = type;
            ie->weight.duration_ns = dur_ns;
            ie->weight.count = 1;
            ie->weight.p95_ns = dur_ns;
            ie->next = g->nodes[to].in_edges;
            g->nodes[to].in_edges = ie;
            g->nodes[to].in_degree++;

            g->total_edges++;
            g->edge_type_counts[type]++;

            g->edge_hash_key[ei] = ek;
            g->edge_hash_val[ei] = oe;
            return;
        }
        /* Fall through to linear scan on hash table full */
    }

    /* Fallback: linear scan (when no edge hash map) */
    adj_entry_t *e = g->nodes[from].out_edges;
    while (e) {
        if (e->target == to && e->type == type) {
            e->weight.duration_ns += dur_ns;
            e->weight.count++;
            if (dur_ns > e->weight.p95_ns * 0.95)
                e->weight.p95_ns = (e->weight.p95_ns * 3 + dur_ns) / 4;
            return;
        }
        e = e->next;
    }

    /* Create out-edge */
    adj_entry_t *oe = calloc(1, sizeof(*oe));
    if (!oe) return;
    oe->target = to;
    oe->type   = type;
    oe->weight.duration_ns = dur_ns;
    oe->weight.count = 1;
    oe->weight.p95_ns = dur_ns;
    oe->next = g->nodes[from].out_edges;
    g->nodes[from].out_edges = oe;
    g->nodes[from].out_degree++;

    /* Create in-edge */
    adj_entry_t *ie = calloc(1, sizeof(*ie));
    if (!ie) {
        g->nodes[from].out_edges = oe->next;
        free(oe);
        return;
    }
    ie->target = from;
    ie->type   = type;
    ie->weight.duration_ns = dur_ns;
    ie->weight.count = 1;
    ie->weight.p95_ns = dur_ns;
    ie->next = g->nodes[to].in_edges;
    g->nodes[to].in_edges = ie;
    g->nodes[to].in_degree++;

    g->total_edges++;
    g->edge_type_counts[type]++;
}

static graph_node_type_t classify_thread(const char *comm)
{
    if (!comm) return GRAPH_NODE_UI_THREAD;
    if (strstr(comm, "RenderThread") || strstr(comm, "render"))
        return GRAPH_NODE_RENDER_THREAD;
    if (strstr(comm, "system_server"))
        return GRAPH_NODE_SYSTEM_SERVER;
    if (strstr(comm, "surfaceflinger") || strstr(comm, "SurfaceFlinger"))
        return GRAPH_NODE_SURFACEFLINGER;
    if (strstr(comm, "Binder:") || strstr(comm, "binder"))
        return GRAPH_NODE_BINDER_SERVER;
    if (strstr(comm, ".ui") || strstr(comm, "main") || strstr(comm, "Activity"))
        return GRAPH_NODE_UI_THREAD;
    if (strstr(comm, "VideoDecode") || strstr(comm, "MediaCodec") ||
        strstr(comm, "CCodec") || strstr(comm, "ACodec") ||
        strstr(comm, "codec") || strstr(comm, "decoder"))
        return GRAPH_NODE_VIDEO_DECODER;
    if (strstr(comm, "AudioTrack") || strstr(comm, "audio") ||
        strstr(comm, "AudioFlinger"))
        return GRAPH_NODE_AUDIO_THREAD;
    if (strstr(comm, "media.codec") || strstr(comm, "mediaserver"))
        return GRAPH_NODE_MEDIA_SERVER;
    return GRAPH_NODE_UI_THREAD;
}

static int is_background_comm(const char *comm, const char *target_pkg)
{
    if (!comm) return 0;
    /* Do NOT penalize video/audio/media threads */
    if (strstr(comm, "VideoDecode") || strstr(comm, "MediaCodec") ||
        strstr(comm, "CCodec") || strstr(comm, "ACodec") ||
        strstr(comm, "codec") || strstr(comm, "decoder") ||
        strstr(comm, "AudioTrack") || strstr(comm, "audio") ||
        strstr(comm, "media.codec") || strstr(comm, "mediaserver"))
        return 0;
    /* Known background/system threads */
    if (strstr(comm, "kworker") || strstr(comm, "ksoftirqd") ||
        strstr(comm, "rcu") || strstr(comm, "migration") ||
        strstr(comm, "watchdog") || strstr(comm, "swapper") ||
        strstr(comm, "irq/") || strstr(comm, "mmcqd") ||
        strstr(comm, "tracepilot") || strstr(comm, "adbd") ||
        strstr(comm, "logd") || strstr(comm, "healthd"))
        return 1;
    return 0;
}

/* ── Build the Critical Path Graph ──────────────────────────────────── */

critical_path_graph_t *build_critical_path_graph(
    struct sched_event  *sched_events,  size_t sched_count,
    struct system_event *sys_events,     size_t sys_count,
    struct enhanced_event *enh_events,   size_t enh_count,
    struct frame_window *frames,         int    frame_count)
{
    critical_path_graph_t *g = calloc(1, sizeof(*g));
    if (!g) return NULL;

    g->node_capacity = 2048;
    g->nodes = calloc(g->node_capacity, sizeof(graph_node_t));
    if (!g->nodes) { free(g); return NULL; }

    /* Init fast TID→node_idx hash map (grows automatically under load) */
    g->tid_map_cap = 32768;
    g->tid_map = malloc(g->tid_map_cap * sizeof(int32_t));
    g->tid_map_node = malloc(g->tid_map_cap * sizeof(uint32_t));
    if (!g->tid_map || !g->tid_map_node) { free(g->tid_map); free(g->tid_map_node); free(g->nodes); free(g); return NULL; }
    for (uint32_t i = 0; i < g->tid_map_cap; i++) g->tid_map[i] = INT32_MIN;

    g->total_frames = frame_count;
    struct timespec __t0; clock_gettime(CLOCK_MONOTONIC, &__t0);

    /* Count jank frames */
    for (int i = 0; i < frame_count; i++) {
        if (frames[i].is_jank) {
            g->jank_frames++;
            g->jank_frame_duration_sum_ns += frames[i].actual_end_ns - frames[i].expected_start_ns;
        }
    }
    if (g->jank_frames > 0)
        g->avg_jank_duration_ns = (double)g->jank_frame_duration_sum_ns / g->jank_frames;

    { struct timespec __t1; clock_gettime(CLOCK_MONOTONIC, &__t1); fprintf(stderr, "[TIME] init: %.3f s\n", (__t1.tv_sec-__t0.tv_sec)+(__t1.tv_nsec-__t0.tv_nsec)/1e9); clock_gettime(CLOCK_MONOTONIC, &__t0); }

    /* Phase 1: Create thread nodes from all events */
    for (size_t i = 0; i < sched_count; i++) {
        struct sched_event *ev = &sched_events[i];
        if (ev->prev_tid > 0)
            graph_find_or_create(g, classify_thread(ev->prev_comm),
                ev->prev_tid, ev->prev_pid, ev->prev_comm, NULL);
        if (ev->next_tid > 0)
            graph_find_or_create(g, classify_thread(ev->next_comm),
                ev->next_tid, ev->next_pid, ev->next_comm, NULL);
    }
    for (size_t i = 0; i < enh_count; i++) {
        struct enhanced_event *ev = &enh_events[i];
        if (ev->tid > 0)
            graph_find_or_create(g, classify_thread(ev->comm),
                ev->tid, ev->pid, ev->comm, NULL);
    }
    { struct timespec __t1; clock_gettime(CLOCK_MONOTONIC, &__t1); fprintf(stderr, "[TIME] Phase1 (thread nodes): %.3f s\n", (__t1.tv_sec-__t0.tv_sec)+(__t1.tv_nsec-__t0.tv_nsec)/1e9); clock_gettime(CLOCK_MONOTONIC, &__t0); }

    /* Phase 1b: Create frame nodes */
    uint32_t frame_base_id = g->node_count;
    for (int i = 0; i < frame_count; i++) {
        char fname[32];
        snprintf(fname, sizeof(fname), "frame_%lld", (long long)frames[i].frame_token);
        uint32_t fnid = graph_find_or_create(g, GRAPH_NODE_FRAME, -1, -1, fname, NULL);
        g->nodes[fnid].total_runtime_ns = frames[i].actual_end_ns - frames[i].expected_start_ns;
    }

    /* Phase 1c: Create resource nodes */
    graph_find_or_create(g, GRAPH_NODE_CPU_RESOURCE, -2, -1, "cpu_little", NULL);
    graph_find_or_create(g, GRAPH_NODE_CPU_RESOURCE, -3, -1, "cpu_big", NULL);
    graph_find_or_create(g, GRAPH_NODE_IO_WAIT, -4, -1, "io_wait", NULL);
    { struct timespec __t1; clock_gettime(CLOCK_MONOTONIC, &__t1); fprintf(stderr, "[TIME] Phase1b/1c (frame+res nodes): %.3f s  nodes=%u\n", (__t1.tv_sec-__t0.tv_sec)+(__t1.tv_nsec-__t0.tv_nsec)/1e9, g->node_count); clock_gettime(CLOCK_MONOTONIC, &__t0); }

    /* Init edge dedup hash map */
    g->edge_hash_cap = 1048576; /* 2^20 */
    g->edge_hash_key = malloc(g->edge_hash_cap * sizeof(uint64_t));
    g->edge_hash_val = calloc(g->edge_hash_cap, sizeof(adj_entry_t*));
    if (!g->edge_hash_key || !g->edge_hash_val) { free(g->edge_hash_key); free(g->edge_hash_val); g->edge_hash_cap = 0; }
    else { for (uint32_t _ei = 0; _ei < g->edge_hash_cap; _ei++) g->edge_hash_key[_ei] = UINT64_MAX; }

    /* Phase 2: Build edges from sched events */
    uint64_t prev_cpu_ts[MAX_CPUS] = {0};
    int32_t prev_cpu_tid[MAX_CPUS] = {0};
    int32_t prev_cpu_node[MAX_CPUS] = {0};

    for (size_t i = 0; i < sched_count; i++) {
        struct sched_event *ev = &sched_events[i];
        int cpu = ev->cpu;
        if (cpu < 0 || cpu >= MAX_CPUS) cpu = 0;

        if (ev->event_type == EVENT_SCHED_SWITCH) {
            int32_t prev_tid = ev->prev_tid;
            int32_t next_tid = ev->next_tid;

            /* CPU_RUN: prev task was running on this CPU */
            if (prev_tid > 0 && prev_cpu_tid[cpu] == prev_tid) {
                uint32_t npid = graph_find_or_create(g, classify_thread(""), prev_tid, 0, "", NULL);
                g->nodes[npid].total_runtime_ns += ev->timestamp_ns - prev_cpu_ts[cpu];
            }

            /* PREEMPTED_BY: next preempted prev — skip edge creation to avoid O(N²), just accumulate runtime */
            if (prev_tid > 0 && next_tid != prev_tid) {
                /* graph_add_edge_nodes call omitted for performance */
            }

            prev_cpu_ts[cpu] = ev->timestamp_ns;
            prev_cpu_tid[cpu] = next_tid;
        }
    }
    { struct timespec __t1; clock_gettime(CLOCK_MONOTONIC, &__t1); fprintf(stderr, "[TIME] Phase2 (sched edges): %.3f s\n", (__t1.tv_sec-__t0.tv_sec)+(__t1.tv_nsec-__t0.tv_nsec)/1e9); clock_gettime(CLOCK_MONOTONIC, &__t0); }

    /* Phase 3: Build Binder dependency graph from enhanced events */
    /*
     * Match by debug_id (value1) — exact, zero false positives.
     * CALL and RECEIVED with same debug_id belong to same transaction.
     */
    #define BINDER_HASH_SIZE 512
    struct { uint32_t tid; uint64_t ts; } binder_map[BINDER_HASH_SIZE];
    memset(binder_map, 0, sizeof(binder_map));

    for (size_t i = 0; i < enh_count; i++) {
        struct enhanced_event *ev = &enh_events[i];

        if (i > 0 && (i % 500000) == 0)
            fprintf(stderr, "[TIME] Phase3 progress: %zu / %zu enhanced events\n",
                    i, enh_count);

        if (ev->type == ENH_EV_BINDER_CALL) {
            int idx = (int)(ev->value1 % BINDER_HASH_SIZE);
            binder_map[idx].tid = ev->tid;
            binder_map[idx].ts  = ev->timestamp_ns;
            g->binder_call_count++;
        }
        else if (ev->type == ENH_EV_BINDER_RECEIVED) {
            int idx = (int)(ev->value1 % BINDER_HASH_SIZE);
            if (binder_map[idx].tid > 0) {
                uint32_t cid = graph_find_or_create(g,
                    GRAPH_NODE_BINDER_CLIENT, binder_map[idx].tid, 0, NULL, NULL);
                uint32_t sid = graph_find_or_create(g,
                    GRAPH_NODE_BINDER_SERVER, ev->tid, 0, ev->comm, NULL);

                uint64_t call_dur = ev->timestamp_ns - binder_map[idx].ts;
                graph_add_edge_nodes(g, cid, sid,
                    GRAPH_EDGE_BINDER_CALL, call_dur);
                g->total_binder_blocking_ns += call_dur;

                binder_map[idx].tid = 0; /* consume */
            }
        }
        else if (ev->type == ENH_EV_FUTEX_WAIT) {
            /* No action — WAKE handler creates the edge with duration */
        }
        else if (ev->type == ENH_EV_FUTEX_WAKE) {
            g->futex_wait_count++;
            uint32_t nid = graph_find_or_create(g,
                GRAPH_NODE_UI_THREAD, ev->tid, ev->pid, ev->comm, NULL);
            char fbuf[32];
            snprintf(fbuf, sizeof(fbuf), "futex_%u", ev->tid);
            uint32_t fnid = graph_find_or_create(g,
                GRAPH_NODE_FUTEX_WAIT, -(int32_t)(1000 + ev->tid), -1, fbuf, NULL);
            graph_add_edge_nodes(g, nid, fnid,
                GRAPH_EDGE_FUTEX_WAIT, ev->duration_ns);
            g->total_futex_wait_ns += ev->duration_ns;
        }
        else if (ev->type == ENH_EV_CPU_FREQ) {
            if (ev->value2 == 0)
                g->avg_cpu_freq_little_khz = (g->avg_cpu_freq_little_khz * 3 + ev->value1) / 4;
            else
                g->avg_cpu_freq_big_khz = (g->avg_cpu_freq_big_khz * 3 + ev->value1) / 4;
        }
        else if (ev->type == ENH_EV_MEM_RECLAIM) {
            uint32_t mnid = graph_find_or_create(g,
                GRAPH_NODE_MEMORY_RECLAIM, -(int32_t)(2000 + ev->tid), -1, "mem_reclaim", NULL);
            uint32_t vnid = graph_find_or_create(g,
                GRAPH_NODE_UI_THREAD, ev->tid, ev->pid, ev->comm, NULL);
            graph_add_edge_nodes(g, vnid, mnid,
                GRAPH_EDGE_RESOURCE_STALL, 500000);
        }
    }
    { struct timespec __t1; clock_gettime(CLOCK_MONOTONIC, &__t1); fprintf(stderr, "[TIME] Phase3 (enhanced events): %.3f s\n", (__t1.tv_sec-__t0.tv_sec)+(__t1.tv_nsec-__t0.tv_nsec)/1e9); clock_gettime(CLOCK_MONOTONIC, &__t0); }

    /* Phase 4: Frame dependency edges */
    for (int i = 1; i < frame_count; i++) {
        uint32_t prev = frame_base_id + i - 1;
        uint32_t curr = frame_base_id + i;
        if (prev < g->node_count && curr < g->node_count) {
            uint64_t weight = frames[i].is_jank && frames[i-1].is_jank ? 1 : 0;
            graph_add_edge_nodes(g, curr, prev, GRAPH_EDGE_FRAME_DEPENDENCY, weight);
        }
    }
    { struct timespec __t1; clock_gettime(CLOCK_MONOTONIC, &__t1); fprintf(stderr, "[TIME] Phase4 (frame deps): %.3f s\n", (__t1.tv_sec-__t0.tv_sec)+(__t1.tv_nsec-__t0.tv_nsec)/1e9); clock_gettime(CLOCK_MONOTONIC, &__t0); }

    /* Phase 5: Compute frame_window_overlap per thread (optimized: single sweep via TID hash) */
    if (g->jank_frames > 0) {
        /* ~~ Build TID→node_index hash map (open addressing) ~~ */
        uint32_t tid_map_sz = g->node_count * 2 + 64;
        int32_t *tid_to_node = calloc(tid_map_sz, sizeof(int32_t));
        int32_t *tid_to_key  = calloc(tid_map_sz, sizeof(int32_t));
        if (!tid_to_node || !tid_to_key) { free(tid_to_node); free(tid_to_key); goto skip_phase5; }
        for (uint32_t i = 0; i < tid_map_sz; i++) tid_to_key[i] = INT32_MIN;

        for (uint32_t i = 0; i < g->node_count; i++) {
            graph_node_t *n = &g->nodes[i];
            if (n->type >= GRAPH_NODE_UI_THREAD && n->type <= GRAPH_NODE_BUFFER_QUEUE) {
                n->jank_frame_count = 0;
                int32_t key = n->tid;
                uint32_t idx = (uint32_t)((key * 2654435761U) % tid_map_sz);
                while (tid_to_key[idx] != INT32_MIN && tid_to_key[idx] != key)
                    idx = (idx + 1) % tid_map_sz;
                tid_to_key[idx] = key;
                tid_to_node[idx] = (int32_t)i;
            }
        }

        /* ~~ Jank frame index ~~ */
        int *jank_idx = malloc(frame_count * sizeof(int));
        int nj = 0;
        for (int i = 0; i < frame_count; i++)
            if (frames[i].is_jank) jank_idx[nj++] = i;

        /* ~~ Per-node last-frame-seen tracking ~~ */
        int *last_seen = calloc(g->node_count, sizeof(int));
        if (!last_seen) { free(jank_idx); free(tid_to_node); free(tid_to_key); goto skip_phase5; }

        /* ~~ Two-pointer sweep: events are assumed sorted by timestamp ~~ */
        int fj = 0;
        for (size_t j = 0; j < sched_count && fj < nj; j++) {
            if (j > 0 && (j % 2000000) == 0)
                fprintf(stderr, "[TIME] Phase5 progress: %zu / %zu sched events\n",
                        j, sched_count);

            struct sched_event *ev = &sched_events[j];
            if (ev->event_type != EVENT_SCHED_SWITCH) continue;

            int64_t adj = (int64_t)ev->timestamp_ns + g_clock_offset_ns;

            /* Advance past frames that end before this event */
            while (fj < nj) {
                int ki = jank_idx[fj];
                if (adj > (int64_t)frames[ki].actual_end_ns + (int64_t)POST_MARGIN_NS)
                    fj++;
                else
                    break;
            }
            if (fj >= nj) break;

            int ki = jank_idx[fj];
            int64_t ws = (int64_t)frames[ki].expected_start_ns - (int64_t)PRE_MARGIN_NS;
            int64_t we = (int64_t)frames[ki].actual_end_ns + (int64_t)POST_MARGIN_NS;

            if (adj >= ws && adj <= we) {
                /* Check both prev_tid and next_tid */
                int32_t tids[2] = { ev->prev_tid, ev->next_tid };
                for (int t = 0; t < 2; t++) {
                    if (tids[t] <= 0) continue;
                    uint32_t h = (uint32_t)((tids[t] * 2654435761U) % tid_map_sz);
                    while (tid_to_key[h] != INT32_MIN && tid_to_key[h] != tids[t])
                        h = (h + 1) % tid_map_sz;
                    int32_t ni = (tid_to_key[h] == tids[t]) ? tid_to_node[h] : -1;
                    if (ni >= 0 && (uint32_t)ni < g->node_count) {
                        if (last_seen[ni] != fj) {
                            g->nodes[ni].jank_frame_count++;
                            last_seen[ni] = fj;
                        }
                    }
                }
            }
        }

        /* ~~ Compute overlap ratio ~~ */
        for (uint32_t i = 0; i < g->node_count; i++) {
            graph_node_t *n = &g->nodes[i];
            if (n->type >= GRAPH_NODE_UI_THREAD && n->type <= GRAPH_NODE_BUFFER_QUEUE)
                n->frame_window_overlap = (double)n->jank_frame_count / nj;
        }

        /* ~~ Phase 5b: runnable delay + wakeup latency in jank windows ~~ */
        {
            sched_track_map_t preempt_map, wakeup_map;
            node_delay_buf_t *delay_bufs = calloc(g->node_count, sizeof(*delay_bufs));
            track_map_clear(&preempt_map);
            track_map_clear(&wakeup_map);

            if (delay_bufs) {
                int fj2 = 0;
                for (size_t j = 0; j < sched_count; j++) {
                    struct sched_event *ev = &sched_events[j];
                    int32_t next_tid = sched_event_next_tid(ev);
                    int32_t prev_tid = sched_event_prev_tid(ev);
                    int64_t adj = (int64_t)ev->timestamp_ns + g_clock_offset_ns;
                    int in_jank = 0;

                    while (fj2 < nj) {
                        int ki = jank_idx[fj2];
                        if (adj > (int64_t)frames[ki].actual_end_ns + (int64_t)POST_MARGIN_NS)
                            fj2++;
                        else
                            break;
                    }
                    if (fj2 < nj) {
                        int ki = jank_idx[fj2];
                        int64_t ws = (int64_t)frames[ki].expected_start_ns - (int64_t)PRE_MARGIN_NS;
                        int64_t we = (int64_t)frames[ki].actual_end_ns + (int64_t)POST_MARGIN_NS;
                        if (adj >= ws && adj <= we)
                            in_jank = 1;
                    }

                    if (ev->event_type == EVENT_SCHED_WAKEUP && next_tid > 0) {
                        track_map_del(&preempt_map, next_tid);
                        track_map_put(&wakeup_map, next_tid, ev->timestamp_ns);
                    } else if (ev->event_type == EVENT_SCHED_SWITCH) {
                        uint64_t rd = 0, wl = 0;

                        rd = track_map_take(&preempt_map, next_tid);
                        if (rd > 0 && ev->timestamp_ns > rd)
                            rd = ev->timestamp_ns - rd;
                        else
                            rd = 0;

                        wl = track_map_take(&wakeup_map, next_tid);
                        if (wl > 0 && ev->timestamp_ns > wl)
                            wl = ev->timestamp_ns - wl;
                        else
                            wl = 0;

                        if (rd == 0 && ev->next_runnable_delay_ns > 0)
                            rd = ev->next_runnable_delay_ns;
                        if (wl == 0 && ev->wakeup_latency_ns > 0)
                            wl = ev->wakeup_latency_ns;

                        if (in_jank && next_tid > 0) {
                            uint32_t h = (uint32_t)((next_tid * 2654435761U) % tid_map_sz);
                            while (tid_to_key[h] != INT32_MIN && tid_to_key[h] != next_tid)
                                h = (h + 1) % tid_map_sz;
                            int32_t ni = (tid_to_key[h] == next_tid) ? tid_to_node[h] : -1;
                            if (ni >= 0 && (uint32_t)ni < g->node_count) {
                                node_delay_add(&delay_bufs[ni], rd, wl);
                                g->nodes[ni].total_runnable_delay_ns += rd;
                                g->nodes[ni].total_wakeup_latency_ns += wl;
                            }
                        }

                        if ((ev->prev_task_state & 0xFF) == 0 && prev_tid > 0)
                            track_map_put(&preempt_map, prev_tid, ev->timestamp_ns);
                    }
                }

                for (uint32_t i = 0; i < g->node_count; i++) {
                    graph_node_t *n = &g->nodes[i];
                    if (n->type < GRAPH_NODE_UI_THREAD || n->type > GRAPH_NODE_BUFFER_QUEUE)
                        continue;
                    n->runnable_delay_p95_ns =
                        compute_p95(delay_bufs[i].rd, delay_bufs[i].rd_n);
                    n->wakeup_latency_p95_ns =
                        compute_p95(delay_bufs[i].wl, delay_bufs[i].wl_n);
                }
                free(delay_bufs);
            }
        }

        free(last_seen);
        free(jank_idx);
        free(tid_to_node);
        free(tid_to_key);
    }
    skip_phase5:

    { struct timespec __t1; clock_gettime(CLOCK_MONOTONIC, &__t1); fprintf(stderr, "[TIME] Phase5 (frame overlap + delay): %.3f s\n", (__t1.tv_sec-__t0.tv_sec)+(__t1.tv_nsec-__t0.tv_nsec)/1e9); clock_gettime(CLOCK_MONOTONIC, &__t0); }

    /* Phase 6: repeated_jank_cooccurrence */
    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];
        if (g->jank_frames > 0 && n->jank_frame_count > 0) {
            n->repeated_jank_cooccurrence = (double)n->jank_frame_count / g->jank_frames;
        }
    }

    { struct timespec __t1; clock_gettime(CLOCK_MONOTONIC, &__t1); fprintf(stderr, "[TIME] Phase6 (jank co-occur): %.3f s\n", (__t1.tv_sec-__t0.tv_sec)+(__t1.tv_nsec-__t0.tv_nsec)/1e9); clock_gettime(CLOCK_MONOTONIC, &__t0); }

    /* Phase 7: background_penalty */
    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];
        if (is_background_comm(n->comm, NULL)) {
            n->background_penalty = 1.0;
        } else {
            n->background_penalty = 0.0;
        }
    }
    { struct timespec __t1; clock_gettime(CLOCK_MONOTONIC, &__t1); fprintf(stderr, "[TIME] Phase7 (bg penalty): %.3f s\n", (__t1.tv_sec-__t0.tv_sec)+(__t1.tv_nsec-__t0.tv_nsec)/1e9); clock_gettime(CLOCK_MONOTONIC, &__t0); }

    /* Cleanup hash maps */
    free(g->tid_map);
    g->tid_map = NULL;
    free(g->tid_map_node);
    g->tid_map_node = NULL;
    g->tid_map_cap = 0;
    free(g->edge_hash_key);
    g->edge_hash_key = NULL;
    free(g->edge_hash_val);
    g->edge_hash_val = NULL;
    g->edge_hash_cap = 0;

    return g;
}

/* ── Betweenness centrality for binder subgraph (Brandes + k-sampling) ── */

static int brandes_from_source(critical_path_graph_t *g, uint32_t n, uint32_t s, double *bc)
{
    int *stack = calloc(n, sizeof(int));
    int *sigma = calloc(n, sizeof(int));
    int *dist  = calloc(n, sizeof(int));
    double *delta = calloc(n, sizeof(double));
    int *pred = calloc(n * 32, sizeof(int));
    int *pred_cnt = calloc(n, sizeof(int));
    int *q = calloc(n, sizeof(int));
    if (!stack || !sigma || !dist || !delta || !pred || !pred_cnt || !q) {
        free(stack); free(sigma); free(dist); free(delta);
        free(pred); free(pred_cnt); free(q);
        return -1;
    }

    for (uint32_t i = 0; i < n; i++) dist[i] = -1;
    dist[s] = 0;
    sigma[s] = 1;
    int qh = 0, qt = 0;
    q[qt++] = s;
    int sp = 0;

    while (qh < qt) {
        int v = q[qh++];
        stack[sp++] = v;
        adj_entry_t *e = g->nodes[v].out_edges;
        while (e) {
            if (e->type == GRAPH_EDGE_BINDER_CALL ||
                e->type == GRAPH_EDGE_PREEMPTED_BY ||
                e->type == GRAPH_EDGE_WAKEUP) {
                int w = e->target;
                if (dist[w] < 0) {
                    dist[w] = dist[v] + 1;
                    q[qt++] = w;
                }
                if (dist[w] == dist[v] + 1) {
                    sigma[w] += sigma[v];
                    if (pred_cnt[w] < 32)
                        pred[w * 32 + pred_cnt[w]++] = v;
                }
            }
            e = e->next;
        }
    }

    for (int i = sp - 1; i >= 0; i--) {
        int w = stack[i];
        for (int j = 0; j < pred_cnt[w] && j < 32; j++) {
            int v = pred[w * 32 + j];
            delta[v] += ((double)sigma[v] / sigma[w]) * (1.0 + delta[w]);
        }
        if (w != (int)s) bc[w] += delta[w];
    }

    free(stack); free(sigma); free(dist); free(delta);
    free(pred); free(pred_cnt); free(q);
    return 0;
}

int compute_binder_centrality(critical_path_graph_t *g)
{
    if (!g || g->node_count == 0) return -1;

    uint32_t n = g->node_count;
    double *bc = calloc(n, sizeof(double));
    if (!bc) return -1;

    /* Collect all binder-relevant source nodes */
    uint32_t *srcs = malloc(n * sizeof(uint32_t));
    uint32_t src_count = 0;
    for (uint32_t s = 0; s < n; s++) {
        graph_node_type_t st = g->nodes[s].type;
        if (st >= GRAPH_NODE_UI_THREAD && st <= GRAPH_NODE_SURFACEFLINGER)
            srcs[src_count++] = s;
    }

    /* k-sampling: use sqrt(src_count) random sources instead of all */
    uint32_t k = (uint32_t)sqrt((double)src_count);
    if (k < 4) k = 4;           /* minimum 4 sources */
    if (k > src_count) k = src_count;

    /* Simple deterministic shuffle: pick k evenly-spaced indices */
    uint32_t step = src_count / k;
    if (step < 1) step = 1;

    double scale = (double)src_count / (double)k;
    for (uint32_t i = 0; i < k; i++) {
        uint32_t s = srcs[(i * step) % src_count];
        brandes_from_source(g, n, s, bc);
    }

    /* Scale back to approximate full betweenness */
    for (uint32_t i = 0; i < n; i++) bc[i] *= scale;

    free(srcs);

    /* Store in nodes */
    double max_bc = 1.0;
    for (uint32_t i = 0; i < n; i++)
        if (bc[i] > max_bc) max_bc = bc[i];

    for (uint32_t i = 0; i < n; i++) {
        if (max_bc > 0)
            g->nodes[i].binder_dependency_centrality = bc[i] / max_bc;
        else
            g->nodes[i].binder_dependency_centrality = 0.0;
    }

    free(bc);
    return 0;
}

/* ── Render path proximity via BFS ──────────────────────────────────── */

int compute_render_proximity(critical_path_graph_t *g)
{
    if (!g || g->node_count == 0) return -1;

    uint32_t n = g->node_count;
    int *dist = calloc(n, sizeof(int));
    int *q    = calloc(n, sizeof(int));
    if (!dist || !q) { free(dist); free(q); return -1; }

    for (uint32_t i = 0; i < n; i++) dist[i] = -1;

    int qh = 0, qt = 0;

    /* BFS from RenderThread and SurfaceFlinger nodes */
    for (uint32_t i = 0; i < n; i++) {
        if (g->nodes[i].type == GRAPH_NODE_RENDER_THREAD ||
            g->nodes[i].type == GRAPH_NODE_SURFACEFLINGER) {
            dist[i] = 0;
            q[qt++] = i;
        }
    }

    while (qh < qt) {
        int v = q[qh++];

        /* Check incoming edges (who depends on v) */
        adj_entry_t *e = g->nodes[v].in_edges;
        while (e) {
            int w = e->target;
            if (dist[w] < 0) {
                dist[w] = dist[v] + 1;
                q[qt++] = w;
            }
            e = e->next;
        }
        /* Also check outgoing edges */
        e = g->nodes[v].out_edges;
        while (e) {
            int w = e->target;
            if (dist[w] < 0) {
                dist[w] = dist[v] + 1;
                q[qt++] = w;
            }
            e = e->next;
        }
    }

    /* Store proximity: 1.0 / (1.0 + distance) */
    for (uint32_t i = 0; i < n; i++) {
        if (dist[i] >= 0)
            g->nodes[i].render_path_proximity = 1.0 / (1.0 + dist[i]);
        else
            g->nodes[i].render_path_proximity = 0.0;
    }

    free(dist); free(q);
    return 0;
}

/* ── CriticalScore computation ──────────────────────────────────────── */

int compute_critical_scores(critical_path_graph_t *g,
    double a, double b, double c, double d, double e, double f, double g_coeff)
{
    if (!g) return -1;

    /* Compute graph metrics first */
    compute_binder_centrality(g);
    compute_render_proximity(g);

    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];

        /* Only score thread-type nodes */
        if (n->type < GRAPH_NODE_UI_THREAD || n->type > GRAPH_NODE_BUFFER_QUEUE)
            continue;
        if (n->tid <= 0) continue;

        double rd_ms = (double)n->runnable_delay_p95_ns / 1e6;

        n->critical_score =
            a * n->frame_window_overlap
          + b * log1p(rd_ms)
          + c * n->binder_dependency_centrality
          + d * n->futex_wait_contribution
          + e * n->render_path_proximity
          + f * n->repeated_jank_cooccurrence
          - g_coeff * n->background_penalty;

        if (n->critical_score < 0.0) n->critical_score = 0.0;
    }

    return 0;
}

/* ═════════════════════════════════════════════════════════════════════
 * VIDEO SCENARIO EXTENSIONS
 * ═════════════════════════════════════════════════════════════════════ */

static int is_video_thread(const char *comm)
{
    if (!comm) return 0;
    if (strstr(comm, "VideoDecode") || strstr(comm, "MediaCodec") ||
        strstr(comm, "CCodec") || strstr(comm, "ACodec") ||
        strstr(comm, "codec") || strstr(comm, "omx") ||
        strstr(comm, "decoder"))
        return 1;
    return 0;
}

static int is_audio_thread(const char *comm)
{
    if (!comm) return 0;
    if (strstr(comm, "AudioTrack") || strstr(comm, "audio") ||
        strstr(comm, "AudioFlinger") || strstr(comm, "AudioOut"))
        return 1;
    return 0;
}

static int is_media_server_thread(const char *comm)
{
    if (!comm) return 0;
    if (strstr(comm, "media.codec") || strstr(comm, "mediaserver") ||
        strstr(comm, "MediaServer") || strstr(comm, "cameraserver"))
        return 1;
    return 0;
}

const char *detect_scenario(critical_path_graph_t *g)
{
    if (!g) return SCENARIO_PAGE_SWITCH;
    for (uint32_t i = 0; i < g->node_count; i++) {
        if (g->nodes[i].type == GRAPH_NODE_VIDEO_DECODER)
            return SCENARIO_VIDEO;
        if (is_video_thread(g->nodes[i].comm))
            return SCENARIO_VIDEO;
    }
    return SCENARIO_PAGE_SWITCH;
}

int video_extend_graph(critical_path_graph_t *g,
    struct sched_event *sched_events, size_t sched_count,
    struct frame_window *frames, int frame_count)
{
    if (!g) return -1;

    /* Phase V1: Tag video/audio/media threads from sched events */
    for (size_t i = 0; i < sched_count; i++) {
        struct sched_event *ev = &sched_events[i];
        int32_t tids[] = {ev->prev_tid, ev->next_tid};
        const char *comms[] = {ev->prev_comm, ev->next_comm};

        for (int j = 0; j < 2; j++) {
            if (tids[j] <= 0) continue;
            uint32_t nid = graph_find_or_create(g,
                classify_thread(comms[j]), tids[j], 0, comms[j], NULL);
            graph_node_t *n = &g->nodes[nid];
            if (n->type <= GRAPH_NODE_IO_WAIT) { /* not yet upgraded */
                if (is_video_thread(comms[j])) {
                    n->type = GRAPH_NODE_VIDEO_DECODER;
                    g->video_decoder_count++;
                } else if (is_audio_thread(comms[j])) {
                    n->type = GRAPH_NODE_AUDIO_THREAD;
                } else if (is_media_server_thread(comms[j])) {
                    n->type = GRAPH_NODE_MEDIA_SERVER;
                }
            }
        }
    }

    /* Phase V2: Create resource nodes for video-specific resources */
    graph_find_or_create(g, GRAPH_NODE_NETWORK, -5, -1, "network", NULL);
    graph_find_or_create(g, GRAPH_NODE_THERMAL, -6, -1, "thermal", NULL);
    graph_find_or_create(g, GRAPH_NODE_BUFFER_QUEUE, -7, -1, "buffer_queue", NULL);

    /* Phase V3: Build DECODE_DEPENDENCY edges between consecutive video frames */
    uint32_t prev_video_node = (uint32_t)-1;
    for (int i = 0; i < frame_count; i++) {
        if (!frames[i].is_video_frame) continue;
        uint32_t fname = graph_find_or_create(g, GRAPH_NODE_FRAME,
            -(int32_t)(100 + i), -1, NULL, NULL);
        if (prev_video_node != (uint32_t)-1) {
            graph_add_edge_nodes(g, fname, prev_video_node,
                GRAPH_EDGE_DECODE_DEPENDENCY, 1);
        }
        prev_video_node = fname;
    }

    /* Phase V4: BUFFER_QUEUE edges from decoder threads to SF */
    uint32_t sf_id = 0, buf_id = 0;
    for (uint32_t i = 0; i < g->node_count; i++) {
        if (g->nodes[i].type == GRAPH_NODE_SURFACEFLINGER) sf_id = i;
        if (g->nodes[i].type == GRAPH_NODE_BUFFER_QUEUE)   buf_id = i;
    }
    for (uint32_t i = 0; i < g->node_count; i++) {
        if (g->nodes[i].type == GRAPH_NODE_VIDEO_DECODER) {
            graph_add_edge_nodes(g, i, buf_id, GRAPH_EDGE_BUFFER_QUEUE, 5000000);
        }
    }
    if (sf_id > 0 && buf_id > 0) {
        graph_add_edge_nodes(g, buf_id, sf_id, GRAPH_EDGE_BUFFER_QUEUE, 8000000);
    }

    /* Phase V5: THERMAL_STALL edges if freq dropped significantly */
    if (g->min_cpu_freq_big_khz > 0 && g->avg_cpu_freq_big_khz > 0) {
        double throttle_ratio = (double)g->min_cpu_freq_big_khz /
                                (double)g->avg_cpu_freq_big_khz;
        g->freq_throttle_ratio = 1.0 - throttle_ratio;

        if (g->freq_throttle_ratio > 0.3) {
            uint32_t tid = 0;
            for (uint32_t i = 0; i < g->node_count; i++) {
                if (g->nodes[i].type == GRAPH_NODE_THERMAL) tid = i;
                if (g->nodes[i].type == GRAPH_NODE_VIDEO_DECODER) {
                    graph_add_edge_nodes(g, tid, i, GRAPH_EDGE_THERMAL_STALL,
                        (uint64_t)(g->freq_throttle_ratio * 10000000));
                }
            }
        }
    }

    /* Phase V6: NETWORK_WAIT edges — detect net threads from BPF events */
    uint32_t net_id = 0;
    for (uint32_t i = 0; i < g->node_count; i++) {
        if (g->nodes[i].type == GRAPH_NODE_NETWORK) net_id = i;
    }
    for (size_t i = 0; i < sched_count; i++) {
        struct sched_event *ev = &sched_events[i];
        if (ev->next_runnable_delay_ns > 5000000) {
            int32_t tids[] = {ev->prev_tid, ev->next_tid};
            for (int j = 0; j < 2; j++) {
                if (tids[j] > 0) {
                    uint32_t tid = graph_find_or_create(g,
                        classify_thread(ev->next_comm), tids[j], 0, NULL, NULL);
                    if (g->nodes[tid].type == GRAPH_NODE_UI_THREAD ||
                        g->nodes[tid].type == GRAPH_NODE_VIDEO_DECODER) {
                        graph_add_edge_nodes(g, tid, net_id,
                            GRAPH_EDGE_NETWORK_WAIT, ev->next_runnable_delay_ns);
                    }
                }
            }
        }
    }

    /* Phase V7: GPU stall inference — if decoder has high delay AND frequency
       is low, it's likely GPU-bound (GPU can't feed decoder fast enough) */
    for (uint32_t i = 0; i < g->node_count; i++) {
        if (g->nodes[i].type == GRAPH_NODE_VIDEO_DECODER &&
            g->nodes[i].total_runnable_delay_ns > 50000000 && /* >50ms total delay */
            g->freq_throttle_ratio > 0.3) {
            uint32_t gpu_id = 0;
            for (uint32_t j = 0; j < g->node_count; j++) {
                if (g->nodes[j].type == GRAPH_NODE_THERMAL) { gpu_id = j; break; }
            }
            if (gpu_id > 0) {
                graph_add_edge_nodes(g, i, gpu_id, GRAPH_EDGE_RESOURCE_STALL,
                    g->nodes[i].total_runnable_delay_ns);
            }
        }
    }

    /* Phase V8: Audio sync drift detection */
    /*
     * To enable audio sync detection:
     *   1. Extract audio position from Perfetto trace:
     *      trace_processor_shell -q "
     *        SELECT ts, CAST(SUBSTR(name, INSTR(name, 'pos=')+4) AS INT) AS pos
     *        FROM slice WHERE name LIKE '%AudioTrack::getTimestamp%'
     *      " trace > audio_pos.txt
     *   2. Parse audio_pos.txt, compare audio position timestamps
     *      against video frame presentation times.
     *   3. If |audio_ts - video_ts| > 40ms, set JANK_CAUSE_AUDIO_SYNC_DRIFT.
     */
    for (uint32_t i = 0; i < g->node_count; i++) {
        if (g->nodes[i].type == GRAPH_NODE_AUDIO_THREAD) {
            g->nodes[i].buffer_underrun_contribution = 0.0;
        }
    }

    /* Update scenario label */
    snprintf(g->detected_scenario, sizeof(g->detected_scenario), "%s", SCENARIO_VIDEO);
    return 0;
}

int compute_decode_proximity(critical_path_graph_t *g)
{
    if (!g || g->node_count == 0) return -1;
    uint32_t n = g->node_count;
    int *dist = calloc(n, sizeof(int));
    int *q    = calloc(n, sizeof(int));
    if (!dist || !q) { free(dist); free(q); return -1; }

    for (uint32_t i = 0; i < n; i++) dist[i] = -1;

    int qh = 0, qt = 0;
    for (uint32_t i = 0; i < n; i++) {
        if (g->nodes[i].type == GRAPH_NODE_VIDEO_DECODER) {
            dist[i] = 0;
            q[qt++] = i;
        }
    }

    while (qh < qt) {
        int v = q[qh++];
        adj_entry_t *e = g->nodes[v].in_edges;
        while (e) {
            if (dist[e->target] < 0) {
                dist[e->target] = dist[v] + 1;
                q[qt++] = e->target;
            }
            e = e->next;
        }
        e = g->nodes[v].out_edges;
        while (e) {
            if (dist[e->target] < 0) {
                dist[e->target] = dist[v] + 1;
                q[qt++] = e->target;
            }
            e = e->next;
        }
    }

    for (uint32_t i = 0; i < n; i++) {
        if (dist[i] >= 0)
            g->nodes[i].decode_path_proximity = 1.0 / (1.0 + dist[i]);
        else
            g->nodes[i].decode_path_proximity = 0.0;
    }

    free(dist); free(q);
    return 0;
}

int compute_thermal_proximity(critical_path_graph_t *g)
{
    if (!g) return -1;
    double t = g->freq_throttle_ratio;
    for (uint32_t i = 0; i < g->node_count; i++) {
        g->nodes[i].thermal_proximity = t * g->nodes[i].decode_path_proximity;
    }
    return 0;
}

void get_scenario_weights(const char *scenario, scenario_weights_t *w)
{
    memset(w, 0, sizeof(*w));
    if (strcmp(scenario, SCENARIO_VIDEO) == 0) {
        w->scenario_name = SCENARIO_VIDEO;
        w->a = 0.15;  /* frame_window_overlap — less important in video (decode-driven) */
        w->b = 0.10;  /* runnable_delay_p95 */
        w->c = 0.15;  /* binder_dependency_centrality */
        w->d = 0.05;  /* futex_wait_contribution */
        w->e = 0.10;  /* render_path_proximity */
        w->f = 0.05;  /* repeated_jank_cooccurrence */
        w->g = 0.05;  /* background_penalty */
        w->h = 0.20;  /* decode_path_proximity — most important for video */
        w->i = 0.10;  /* thermal_proximity — throttling hurts sustained playback */
        w->j = 0.15;  /* buffer_underrun_contribution — buffer starvation */
        w->k = 0.05;  /* network_penalty — distinguish net vs compute bottleneck */
    } else {
        w->scenario_name = SCENARIO_PAGE_SWITCH;
        w->a = 0.30;
        w->b = 0.10;
        w->c = 0.25;
        w->d = 0.10;
        w->e = 0.20;
        w->f = 0.05;
        w->g = 0.05;
        w->h = 0.0;
        w->i = 0.0;
        w->j = 0.0;
        w->k = 0.0;
    }
}

int run_video_scenario(critical_path_graph_t *g,
    struct sched_event *sched_events, size_t sched_count,
    struct frame_window *frames, int frame_count)
{
    if (!g) return -1;

    int ret = video_extend_graph(g, sched_events, sched_count, frames, frame_count);
    if (ret != 0) return ret;

    compute_binder_centrality(g);
    compute_render_proximity(g);
    compute_decode_proximity(g);
    compute_thermal_proximity(g);

    scenario_weights_t w;
    get_scenario_weights(SCENARIO_VIDEO, &w);

    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];
        if (n->type < GRAPH_NODE_UI_THREAD || n->type > GRAPH_NODE_BUFFER_QUEUE) continue;
        if (n->tid <= 0) continue;

        double rd_ms = (double)n->runnable_delay_p95_ns / 1e6;

        n->critical_score =
            w.a * n->frame_window_overlap
          + w.b * log1p(rd_ms)
          + w.c * n->binder_dependency_centrality
          + w.d * n->futex_wait_contribution
          + w.e * n->render_path_proximity
          + w.f * n->repeated_jank_cooccurrence
          - w.g * n->background_penalty
          + w.h * n->decode_path_proximity
          + w.i * n->thermal_proximity
          + w.j * n->buffer_underrun_contribution
          - w.k * (n->type == GRAPH_NODE_NETWORK ? 1.0 : 0.0);

        if (n->critical_score < 0.0) n->critical_score = 0.0;
    }

    return 0;
}

/* ── Jank cause classifier (extended for video) ─────────────────────── */

int classify_jank_causes(critical_path_graph_t *g,
    struct frame_window *frames, int frame_count,
    frame_classification_t **out_classifications, int *out_count)
{
    frame_classification_t *fc = calloc(frame_count, sizeof(*fc));
    if (!fc) return -1;

    for (int i = 0; i < frame_count; i++) {
        fc[i].frame_id         = frames[i].frame_token;
        fc[i].frame_duration_ns= frames[i].actual_end_ns - frames[i].expected_start_ns;
        fc[i].is_jank          = frames[i].is_jank;
        fc[i].primary_cause    = JANK_CAUSE_UNKNOWN;
        fc[i].secondary_cause  = JANK_CAUSE_UNKNOWN;

        if (!frames[i].is_jank) continue;

        /*
         * Analyze the frame window to determine cause.
         * Check which patterns dominate:
         *   1. Binder blocking: many BINDER_CALL edges in window
         *   2. Futex blocking: many FUTEX_WAIT edges in window
         *   3. CPU contention: many PREEMPTED_BY edges
         *   4. Memory reclaim: presence of RESOURCE_STALL (mem)
         *   5. Runnable delay: high runnable_delay_p95
         */

        uint64_t win_start = (frames[i].expected_start_ns > PRE_MARGIN_NS)
                           ? frames[i].expected_start_ns - PRE_MARGIN_NS : 0;
        uint64_t win_end   = frames[i].actual_end_ns + POST_MARGIN_NS;

        double binder_score = 0.0, futex_score = 0.0;
        double cpu_score = 0.0, mem_score = 0.0;

        /* Accumulate scores from graph edges */
        for (uint32_t j = 0; j < g->node_count; j++) {
            adj_entry_t *e = g->nodes[j].out_edges;
            while (e) {
                if (e->weight.jank_overlap_score > 0) {
                    switch (e->type) {
                        case GRAPH_EDGE_BINDER_CALL:
                            binder_score += e->weight.jank_overlap_score;
                            break;
                        case GRAPH_EDGE_FUTEX_WAIT:
                            futex_score += e->weight.jank_overlap_score;
                            break;
                        case GRAPH_EDGE_PREEMPTED_BY:
                            cpu_score += e->weight.jank_overlap_score;
                            break;
                        case GRAPH_EDGE_RESOURCE_STALL:
                            mem_score += e->weight.jank_overlap_score;
                            break;
                        default: break;
                    }
                }
                e = e->next;
            }
        }

        /* Check system overhead */
        fc[i].io_wait_score = (double)frames[i].system_overhead_ns / 16666666.0;
        fc[i].memory_reclaim_score = mem_score;
        fc[i].binder_blocking_score = binder_score;
        fc[i].futex_blocking_score = futex_score;
        fc[i].cpu_contention_score = cpu_score;
        fc[i].runnable_delay_score = 0.0;
        fc[i].video_late_render_score = 0.0;
        fc[i].audio_sync_drift_score = 0.0;
        fc[i].thermal_throttle_score = 0.0;

        if (g->freq_throttle_ratio > 0.15)
            fc[i].thermal_throttle_score += g->freq_throttle_ratio;

        for (uint32_t j = 0; j < g->node_count; j++) {
            graph_node_t *n = &g->nodes[j];
            if (n->frame_window_overlap < 0.05)
                continue;
            if (n->runnable_delay_p95_ns > 1000000)
                fc[i].runnable_delay_score +=
                    log1p((double)n->runnable_delay_p95_ns / 1e6) * n->frame_window_overlap;
        }

        /* ── Video-specific detection ── */
        if (frames[i].is_video_frame) {
            double decode_score = 0.0, thermal_score = 0.0;
            for (uint32_t j = 0; j < g->node_count; j++) {
                adj_entry_t *e = g->nodes[j].out_edges;
                while (e) {
                    if (e->weight.jank_overlap_score > 0) {
                        switch (e->type) {
                            case GRAPH_EDGE_DECODE_DEPENDENCY:
                                decode_score += e->weight.jank_overlap_score;
                                break;
                            case GRAPH_EDGE_THERMAL_STALL:
                                thermal_score += e->weight.jank_overlap_score;
                                break;
                            default: break;
                        }
                    }
                    e = e->next;
                }
            }
            fc[i].video_late_render_score = decode_score;
            fc[i].thermal_throttle_score  = thermal_score;
        }

        /* ── GPU stall from gpu_work_period (frame_type='GS') ── */
        if (frames[i].frame_type[0] == 'G' && frames[i].frame_type[1] == 'S') {
            fc[i].gpu_stall_score = (double)frames[i].gpu_duration_ns / 16666666.0;
        }

        /* ── Audio sync drift (frame_type='AP') ── */
        if (frames[i].frame_type[0] == 'A' && frames[i].frame_type[1] == 'P') {
            /* delay_ms = audio position from getTimestamp (in ms).
               Compare with elapsed time since start to detect drift.
               drift > 40ms = out of sync. */
            static uint64_t first_ap_ts = 0;
            if (first_ap_ts == 0) first_ap_ts = frames[i].expected_start_ns;
            double elapsed_ms = (double)(frames[i].expected_start_ns - first_ap_ts) / 1e6;
            double audio_ms = frames[i].delay_ms;
            double drift_ms = fabs(elapsed_ms - audio_ms);
            if (drift_ms > 40.0) {
                fc[i].audio_sync_drift_score = drift_ms / 100.0;
            }
        }

        /* Determine primary cause: highest score */
        double scores[] = {
            cpu_score, binder_score, futex_score,
            fc[i].io_wait_score, mem_score, 0.0,
            fc[i].runnable_delay_score, 0.0, /* UNKNOWN = 7 */
            fc[i].video_late_render_score,
            fc[i].audio_sync_drift_score,
            fc[i].thermal_throttle_score
        };
        double max_s = 0.0;
        int max_idx = 0;
        for (int k = 0; k < JANK_CAUSE_COUNT; k++) {
            if (scores[k] > max_s) {
                max_s = scores[k];
                max_idx = k;
            }
        }

        /* Find second highest */
        double snd_s = 0.0;
        int snd_idx = 0;
        for (int k = 0; k < JANK_CAUSE_COUNT; k++) {
            if (k != max_idx && scores[k] > snd_s) {
                snd_s = scores[k];
                snd_idx = k;
            }
        }

        fc[i].primary_cause   = (jank_cause_t)max_idx;
        fc[i].secondary_cause = (jank_cause_t)snd_idx;
        fc[i].cause_confidence = max_s / (max_s + snd_s + 0.001);
    }

    /* Build cause distribution summary */
    int cause_counts[JANK_CAUSE_COUNT] = {0};
    for (int i = 0; i < frame_count; i++) {
        if (frames[i].is_jank)
            cause_counts[fc[i].primary_cause]++;
    }

    /* Assign dominant cause to thread nodes based on which frames they overlap */
    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];
        if (n->jank_frame_count == 0) continue;

        int tc[JANK_CAUSE_COUNT] = {0};
        for (int j = 0; j < JANK_CAUSE_COUNT; j++) tc[j] = cause_counts[j];

        int best = 0, best_cnt = 0;
        for (int j = 0; j < 8; j++) {
            if (tc[j] > best_cnt) { best_cnt = tc[j]; best = j; }
        }
        n->dominant_cause = (jank_cause_t)best;
    }

    *out_classifications = fc;
    *out_count = frame_count;
    return 0;
}

/* ── Heuristic strategy comparison ──────────────────────────────────── */

typedef struct { int32_t tid; double score; } pair_t;

static int compare_pair_score(const void *a, const void *b)
{
    const pair_t *ta = (const pair_t *)a;
    const pair_t *tb = (const pair_t *)b;
    if (ta->score < tb->score) return 1;
    if (ta->score > tb->score) return -1;
    return 0;
}

heuristics_comparison_t compare_heuristics(critical_path_graph_t *g,
    struct frame_window *frames, int frame_count, int top_k)
{
    heuristics_comparison_t hc;
    memset(&hc, 0, sizeof(hc));

    if (!g || g->node_count == 0) return hc;

    /* Build sorted list of graph scores */
    pair_t *graph_ranks = calloc(g->node_count, sizeof(pair_t));
    pair_t *heur_ranks  = calloc(g->node_count, sizeof(pair_t));
    int gc = 0, hc2 = 0;

    int num_jank = 0;
    for (int i = 0; i < frame_count; i++)
        if (frames[i].is_jank) num_jank++;

    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];
        if (n->type < GRAPH_NODE_UI_THREAD || n->type > GRAPH_NODE_BUFFER_QUEUE) continue;
        if (n->tid <= 0) continue;

        /* Graph-based score */
        graph_ranks[gc].tid   = n->tid;
        graph_ranks[gc].score = n->critical_score;
        gc++;

        /* Heuristic score (old formula) */
        double hs = 0.0;
        if (num_jank > 0) hs += 0.35 * ((double)n->jank_frame_count / num_jank);
        hs += 0.35 * log1p((double)n->runnable_delay_p95_ns / 1e6);
        if (strstr(n->comm, "RenderThread") || strstr(n->comm, ".ui")) hs += 0.15;
        heur_ranks[hc2].tid   = n->tid;
        heur_ranks[hc2].score = hs;
        hc2++;
    }

    /* Sort both by score descending using qsort */
    qsort(graph_ranks, gc, sizeof(pair_t), compare_pair_score);
    qsort(heur_ranks, hc2, sizeof(pair_t), compare_pair_score);

    int limit = top_k;
    if (limit > gc) limit = gc;
    if (limit > hc2) limit = hc2;

    /* Count overlap in top-k */
    for (int i = 0; i < limit && i < gc; i++) {
        for (int j = 0; j < limit && j < hc2; j++) {
            if (graph_ranks[i].tid == heur_ranks[j].tid) {
                hc.overlap_count++;
                break;
            }
        }
    }

    /* Unique culprits */
    /* Count graph top-k not in heuristic top-k */
    int graph_unique = 0;
    for (int i = 0; i < limit && i < gc; i++) {
        int found = 0;
        for (int j = 0; j < limit && j < hc2; j++) {
            if (graph_ranks[i].tid == heur_ranks[j].tid) {
                found = 1;
                break;
            }
        }
        if (!found) graph_unique++;
    }
    
    /* Count heuristic top-k not in graph top-k */
    int heur_unique = 0;
    for (int i = 0; i < limit && i < hc2; i++) {
        int found = 0;
        for (int j = 0; j < limit && j < gc; j++) {
            if (heur_ranks[i].tid == graph_ranks[j].tid) {
                found = 1;
                break;
            }
        }
        if (!found) heur_unique++;
    }
    
    hc.graph_unique_culprits = graph_unique;
    hc.heuristic_unique_culprits = heur_unique;

    /* Precision at K */
    hc.graph_avg_precision_at_k = limit > 0
        ? (double)(limit - hc.graph_unique_culprits) / limit : 0.0;
    hc.heuristic_avg_precision_at_k = limit > 0
        ? (double)(limit - hc.heuristic_unique_culprits) / limit : 0.0;

    /* Signal-to-noise ratio (simplified) */
    hc.graph_mean_signal_noise_ratio = 1.0;
    if (gc > 0) {
        double total = 0.0;
        for (int i = 0; i < gc; i++) total += graph_ranks[i].score;
        hc.graph_mean_signal_noise_ratio = total / gc;
    }
    hc.heuristic_mean_signal_noise_ratio = 0.5;

    free(graph_ranks);
    free(heur_ranks);
    return hc;
}

/* ── Enhanced JSON output ───────────────────────────────────────────── */

static int compare_ranking(const void *a, const void *b)
{
    const thread_ranking_t *ta = (const thread_ranking_t *)a;
    const thread_ranking_t *tb = (const thread_ranking_t *)b;
    if (ta->critical_score < tb->critical_score) return 1;
    if (ta->critical_score > tb->critical_score) return -1;
    return 0;
}

static const char *cause_to_str(jank_cause_t c)
{
    static const char *names[] = {
        "CPU_CONTENTION", "BINDER_BLOCKING", "FUTEX_BLOCKING",
        "IO_WAIT", "MEMORY_RECLAIM", "GPU_STALL",
        "RUNNABLE_DELAY", "UNKNOWN",
        "VIDEO_LATE_RENDER", "AUDIO_SYNC_DRIFT", "THERMAL_THROTTLE"
    };
    return (c < JANK_CAUSE_COUNT) ? names[c] : "UNKNOWN";
}

int output_enhanced_topk(FILE *out, critical_path_graph_t *g, int top_k,
    frame_classification_t *classifications, int class_count,
    heuristics_comparison_t *comparison,
    void *hints_out_ptr,
    void *inference_report_ptr,
    const thermal_profile_t *thermal)
{
    hint_list_t *hints_out = (hint_list_t *)hints_out_ptr;
    inference_report_t *inference_report = (inference_report_t *)inference_report_ptr;
    if (!g || !out) return -1;

    /* Collect thread rankings */
    thread_ranking_t *ranking = calloc(g->node_count, sizeof(*ranking));
    hint_list_t hints;
    const app_session_t *sess = identity_get_session();
    if (!ranking) return -1;
    int rcount = 0;

    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];
        if (n->type < GRAPH_NODE_UI_THREAD || n->type > GRAPH_NODE_BUFFER_QUEUE) continue;
        if (n->tid <= 0) continue;

        thread_ranking_t *r = &ranking[rcount++];
        r->tid            = n->tid;
        r->pid            = n->pid;
        r->critical_score = n->critical_score;
        r->frame_window_overlap       = n->frame_window_overlap;
        r->binder_dependency_centrality = n->binder_dependency_centrality;
        r->render_path_proximity      = n->render_path_proximity;
        r->futex_wait_contribution    = n->futex_wait_contribution;
        r->repeated_jank_cooccurrence = n->repeated_jank_cooccurrence;
        r->background_penalty         = n->background_penalty;
        r->decode_path_proximity       = n->decode_path_proximity;
        r->thermal_proximity           = n->thermal_proximity;
        r->buffer_underrun_contribution = n->buffer_underrun_contribution;
        r->runnable_delay_p95_ns      = n->runnable_delay_p95_ns;
        r->wakeup_latency_p95_ns        = n->wakeup_latency_p95_ns;
        r->total_runtime_ns           = n->total_runtime_ns;
        r->dominant_cause             = n->dominant_cause;
        snprintf(r->comm, sizeof(r->comm), "%s", n->comm);
        snprintf(r->pkg, sizeof(r->pkg), "%s", n->pkg);
        snprintf(r->cause_name, sizeof(r->cause_name), "%s", cause_to_str(n->dominant_cause));
    }

    qsort(ranking, rcount, sizeof(*ranking), compare_ranking);

    /* Assign ranks */
    for (int i = 0; i < rcount; i++) ranking[i].rank = i + 1;

    hints = hint_engine_generate(g, ranking, rcount, top_k, sess);
    if (hints_out)
        *hints_out = hints;

    /* ── Output JSON ── */
    fprintf(out, "{\n");
    fprintf(out, "  \"analysis_mode\": \"graph_based_critical_path\",\n");
    fprintf(out, "  \"session_id\": \"%s\",\n", sess->session_id);
    fprintf(out, "  \"target_package\": \"%s\",\n", sess->package);
    fprintf(out, "  \"total_frames\": %llu,\n", (unsigned long long)g->total_frames);
    fprintf(out, "  \"jank_frames\": %llu,\n", (unsigned long long)g->jank_frames);
    fprintf(out, "  \"avg_jank_duration_ns\": %.0f,\n", g->avg_jank_duration_ns);
    fprintf(out, "  \"total_nodes\": %u,\n", g->node_count);
    fprintf(out, "  \"total_edges\": %llu,\n", (unsigned long long)g->total_edges);

    /* Edge type distribution */
    fprintf(out, "  \"edge_type_distribution\": {\n");
    static const char *edge_names[] = {
        "WAKEUP", "RUNNABLE_WAIT", "BINDER_CALL", "FUTEX_WAIT",
        "CPU_RUN", "PREEMPTED_BY", "FRAME_DEPENDENCY", "RESOURCE_STALL",
        "DECODE_DEPENDENCY", "BUFFER_QUEUE", "THERMAL_STALL", "NETWORK_WAIT"
    };
    for (int i = 0; i < GRAPH_EDGE_TYPE_COUNT; i++) {
        fprintf(out, "    \"%s\": %llu%s\n",
            edge_names[i], (unsigned long long)g->edge_type_counts[i],
            (i < GRAPH_EDGE_TYPE_COUNT - 1) ? "," : "");
    }
    fprintf(out, "  },\n");

    /* Resource statistics */
    fprintf(out, "  \"resource_stats\": {\n");
    fprintf(out, "    \"avg_cpu_freq_little_khz\": %llu,\n",
        (unsigned long long)g->avg_cpu_freq_little_khz);
    fprintf(out, "    \"avg_cpu_freq_big_khz\": %llu,\n",
        (unsigned long long)g->avg_cpu_freq_big_khz);
    fprintf(out, "    \"min_cpu_freq_big_khz\": %llu,\n",
        (unsigned long long)g->min_cpu_freq_big_khz);
    fprintf(out, "    \"binder_call_count\": %u,\n", g->binder_call_count);
    fprintf(out, "    \"total_binder_blocking_ns\": %llu,\n",
        (unsigned long long)g->total_binder_blocking_ns);
    fprintf(out, "    \"futex_wait_count\": %u,\n", g->futex_wait_count);
    fprintf(out, "    \"total_futex_wait_ns\": %llu,\n",
        (unsigned long long)g->total_futex_wait_ns);
    fprintf(out, "    \"video_decoder_count\": %u,\n", g->video_decoder_count);
    fprintf(out, "    \"video_frames\": %llu,\n",
        (unsigned long long)g->video_frames);
    fprintf(out, "    \"detected_scenario\": \"%s\",\n", g->detected_scenario);
    fprintf(out, "    \"freq_throttle_ratio\": %.4f,\n", g->freq_throttle_ratio);
    if (thermal && thermal->count > 0) {
        fprintf(out, "    \"thermal_baseline_mc\": %d,\n", thermal->baseline_temp_mc);
        fprintf(out, "    \"thermal_peak_mc\": %d,\n", thermal->peak_temp_mc);
        fprintf(out, "    \"thermal_jank_peak_mc\": %d,\n", thermal->jank_window_peak_mc);
        fprintf(out, "    \"thermal_jank_delta_mc\": %d,\n", thermal->jank_window_delta_mc);
        fprintf(out, "    \"thermal_throttle_score\": %.4f,\n", thermal->throttle_score);
    }
    fprintf(out, "    \"jank_system_overhead_ns\": %llu\n",
        (unsigned long long)g->jank_system_overhead_ns);
    fprintf(out, "  },\n");

    /* Top-k threads */
    int limit = (top_k < rcount) ? top_k : rcount;
    fprintf(out, "  \"top_k_threads\": [\n");
    for (int i = 0; i < limit; i++) {
        thread_ranking_t *r = &ranking[i];
        fprintf(out, "    {\n");
        fprintf(out, "      \"rank\": %d,\n", r->rank);
        fprintf(out, "      \"tid\": %d,\n", r->tid);
        fprintf(out, "      \"pid\": %d,\n", r->pid);
        fprintf(out, "      \"comm\": \"%s\",\n", r->comm);
        fprintf(out, "      \"package\": \"%s\",\n", r->pkg);
        fprintf(out, "      \"critical_score\": %.4f,\n", r->critical_score);
        fprintf(out, "      \"score_components\": {\n");
        fprintf(out, "        \"frame_window_overlap\": %.4f,\n", r->frame_window_overlap);
        fprintf(out, "        \"binder_dependency_centrality\": %.4f,\n",
            r->binder_dependency_centrality);
        fprintf(out, "        \"render_path_proximity\": %.4f,\n", r->render_path_proximity);
        fprintf(out, "        \"futex_wait_contribution\": %.4f,\n", r->futex_wait_contribution);
        fprintf(out, "        \"repeated_jank_cooccurrence\": %.4f,\n",
            r->repeated_jank_cooccurrence);
        fprintf(out, "        \"background_penalty\": %.4f,\n", r->background_penalty);
        fprintf(out, "        \"decode_path_proximity\": %.4f,\n", r->decode_path_proximity);
        fprintf(out, "        \"thermal_proximity\": %.4f,\n", r->thermal_proximity);
        fprintf(out, "        \"buffer_underrun_contribution\": %.4f\n",
            r->buffer_underrun_contribution);
        fprintf(out, "      },\n");
        fprintf(out, "      \"runnable_delay_p95_ns\": %llu,\n",
            (unsigned long long)r->runnable_delay_p95_ns);
        fprintf(out, "      \"wakeup_latency_p95_ns\": %llu,\n",
            (unsigned long long)r->wakeup_latency_p95_ns);
        fprintf(out, "      \"total_runtime_ns\": %llu,\n",
            (unsigned long long)r->total_runtime_ns);
        fprintf(out, "      \"dominant_cause\": \"%s\"\n", r->cause_name);
        fprintf(out, "    }%s\n", (i < limit - 1) ? "," : "");
    }
    fprintf(out, "  ],\n");

    /* Jank cause classification summary */
    int cause_counts[JANK_CAUSE_COUNT] = {0};
    if (classifications) {
        for (int i = 0; i < class_count; i++) {
            if (classifications[i].is_jank)
                cause_counts[classifications[i].primary_cause]++;
        }
    }
    fprintf(out, "  \"jank_cause_distribution\": {\n");
    for (int i = 0; i < JANK_CAUSE_COUNT; i++) {
        fprintf(out, "    \"%s\": %d%s\n",
            cause_to_str((jank_cause_t)i), cause_counts[i],
            (i < JANK_CAUSE_COUNT - 1) ? "," : "");
    }
    fprintf(out, "  },\n");

    hint_engine_print_json_section(out, &hints);

    if (inference_report)
        inference_print_json_section(out, inference_report);

    /* Heuristic comparison */
    fprintf(out, "  \"heuristics_comparison\": {\n");
    fprintf(out, "    \"graph_avg_precision_at_k\": %.4f,\n",
        comparison ? comparison->graph_avg_precision_at_k : 0.0);
    fprintf(out, "    \"heuristic_avg_precision_at_k\": %.4f,\n",
        comparison ? comparison->heuristic_avg_precision_at_k : 0.0);
    fprintf(out, "    \"top_k_overlap_count\": %d,\n",
        comparison ? comparison->overlap_count : 0);
    fprintf(out, "    \"graph_mean_signal_noise_ratio\": %.4f,\n",
        comparison ? comparison->graph_mean_signal_noise_ratio : 0.0);
    fprintf(out, "    \"heuristic_mean_signal_noise_ratio\": %.4f\n",
        comparison ? comparison->heuristic_mean_signal_noise_ratio : 0.0);
    fprintf(out, "  }\n");

    fprintf(out, "}\n");

    free(ranking);
    return 0;
}

/* ── Graph topology export ──────────────────────────────────────────── */

#define MAX_SUBGRAPH_NODES 250

static const char *node_type_name(graph_node_type_t t)
{
    static const char *names[] = {
        "FRAME", "UI_THREAD", "RENDER_THREAD", "BINDER_CLIENT",
        "BINDER_SERVER", "SYSTEM_SERVER", "SURFACEFLINGER", "FUTEX_WAIT",
        "CPU_RESOURCE", "MEMORY_RECLAIM", "IO_WAIT", "VIDEO_DECODER",
        "AUDIO_THREAD", "MEDIA_SERVER", "NETWORK", "THERMAL", "BUFFER_QUEUE",
    };
    if (t >= 0 && t < (int)(sizeof(names) / sizeof(names[0])))
        return names[t];
    return "UNKNOWN";
}

static const char *edge_type_name(graph_edge_type_t t)
{
    static const char *names[] = {
        "WAKEUP", "RUNNABLE_WAIT", "BINDER_CALL", "FUTEX_WAIT",
        "CPU_RUN", "PREEMPTED_BY", "FRAME_DEPENDENCY", "RESOURCE_STALL",
        "DECODE_DEPENDENCY", "BUFFER_QUEUE", "THERMAL_STALL", "NETWORK_WAIT",
    };
    if (t >= 0 && t < GRAPH_EDGE_TYPE_COUNT)
        return names[t];
    return "UNKNOWN";
}

static void json_escape(FILE *out, const char *s)
{
    if (!s) s = "";
    fputc('"', out);
    for (; *s; s++) {
        if (*s == '"' || *s == '\\')
            fputc('\\', out);
        if (*s == '\n' || *s == '\r')
            continue;
        fputc(*s, out);
    }
    fputc('"', out);
}

static void dot_escape(FILE *out, const char *s)
{
    if (!s) s = "";
    for (; *s; s++) {
        if (*s == '"' || *s == '\\' || *s == ']' || *s == '[')
            fputc('\\', out);
        if (*s == '\n' || *s == '\r')
            continue;
        fputc(*s, out);
    }
}

typedef struct {
    uint32_t id;
    double   score;
} scored_node_t;

static int compare_scored_node(const void *a, const void *b)
{
    const scored_node_t *sa = a;
    const scored_node_t *sb = b;
    if (sb->score > sa->score) return 1;
    if (sb->score < sa->score) return -1;
    return 0;
}

static uint8_t *select_subgraph_nodes(critical_path_graph_t *g, int top_k,
                                      uint32_t *selected_count)
{
    uint8_t *sel;
    scored_node_t *threads;
    int nt = 0, pick, round;
    uint32_t count = 0;

    sel = calloc(g->node_count, 1);
    threads = malloc(g->node_count * sizeof(*threads));
    if (!sel || !threads) {
        free(sel);
        free(threads);
        return NULL;
    }

    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];
        if (n->type >= GRAPH_NODE_UI_THREAD && n->type <= GRAPH_NODE_BUFFER_QUEUE &&
            n->tid > 0) {
            threads[nt].id = i;
            threads[nt].score = n->critical_score;
            nt++;
        }
    }
    qsort(threads, nt, sizeof(*threads), compare_scored_node);

    pick = top_k < nt ? top_k : nt;
    if (pick < 1 && nt > 0)
        pick = nt < 5 ? nt : 5;

    for (int i = 0; i < pick; i++)
        sel[threads[i].id] = 1;

    for (uint32_t i = 0; i < g->node_count; i++)
        if (sel[i]) count++;

    for (round = 0; round < 2 && count < MAX_SUBGRAPH_NODES; round++) {
        for (uint32_t i = 0; i < g->node_count && count < MAX_SUBGRAPH_NODES; i++) {
            adj_entry_t *e;
            if (!sel[i]) continue;
            e = g->nodes[i].out_edges;
            while (e && count < MAX_SUBGRAPH_NODES) {
                uint32_t t = e->target;
                if (!sel[t] && g->nodes[t].type != GRAPH_NODE_FRAME) {
                    sel[t] = 1;
                    count++;
                }
                e = e->next;
            }
            e = g->nodes[i].in_edges;
            while (e && count < MAX_SUBGRAPH_NODES) {
                uint32_t t = e->target;
                if (!sel[t] && g->nodes[t].type != GRAPH_NODE_FRAME) {
                    sel[t] = 1;
                    count++;
                }
                e = e->next;
            }
        }
    }

    free(threads);
    *selected_count = count;
    return sel;
}

static void write_node_json(FILE *out, critical_path_graph_t *g, uint32_t i)
{
    graph_node_t *n = &g->nodes[i];
    fprintf(out, "    {\n");
    fprintf(out, "      \"id\": %u,\n", i);
    fprintf(out, "      \"type\": \"%s\",\n", node_type_name(n->type));
    fprintf(out, "      \"tid\": %d,\n", n->tid);
    fprintf(out, "      \"pid\": %d,\n", n->pid);
    fprintf(out, "      \"comm\": ");
    json_escape(out, n->comm);
    fprintf(out, ",\n");
    fprintf(out, "      \"critical_score\": %.4f,\n", n->critical_score);
    fprintf(out, "      \"frame_window_overlap\": %.4f,\n", n->frame_window_overlap);
    fprintf(out, "      \"render_path_proximity\": %.4f,\n", n->render_path_proximity);
    fprintf(out, "      \"decode_path_proximity\": %.4f,\n", n->decode_path_proximity);
    fprintf(out, "      \"out_degree\": %u,\n", n->out_degree);
    fprintf(out, "      \"in_degree\": %u\n", n->in_degree);
    fprintf(out, "    }");
}

static int write_edges_json(FILE *out, critical_path_graph_t *g,
                            const uint8_t *sel, int first_edge)
{
    int first = first_edge;
    for (uint32_t i = 0; i < g->node_count; i++) {
        if (sel && !sel[i]) continue;
        for (adj_entry_t *e = g->nodes[i].out_edges; e; e = e->next) {
            if (sel && (!sel[e->target])) continue;
            if (!first) fprintf(out, ",\n");
            first = 0;
            fprintf(out, "    {\n");
            fprintf(out, "      \"from\": %u,\n", i);
            fprintf(out, "      \"to\": %u,\n", e->target);
            fprintf(out, "      \"type\": \"%s\",\n", edge_type_name(e->type));
            fprintf(out, "      \"count\": %u,\n", e->weight.count);
            fprintf(out, "      \"duration_ns\": %llu\n",
                (unsigned long long)e->weight.duration_ns);
            fprintf(out, "    }");
        }
    }
    return first;
}

int export_graph_topology_json(FILE *out, critical_path_graph_t *g)
{
    int first_node = 1, first_edge = 1;
    if (!out || !g) return -1;

    fprintf(out, "{\n");
    fprintf(out, "  \"format\": \"tracepilot_graph_v1\",\n");
    fprintf(out, "  \"directed\": true,\n");
    fprintf(out, "  \"total_nodes\": %u,\n", g->node_count);
    fprintf(out, "  \"total_edges\": %llu,\n", (unsigned long long)g->total_edges);
    fprintf(out, "  \"detected_scenario\": ");
    json_escape(out, g->detected_scenario);
    fprintf(out, ",\n  \"nodes\": [\n");

    for (uint32_t i = 0; i < g->node_count; i++) {
        if (!first_node) fprintf(out, ",\n");
        first_node = 0;
        write_node_json(out, g, i);
    }

    fprintf(out, "\n  ],\n  \"edges\": [\n");
    first_edge = write_edges_json(out, g, NULL, 1);
    fprintf(out, "\n  ]\n}\n");
    return 0;
}

int export_graph_subgraph_json(FILE *out, critical_path_graph_t *g, int top_k)
{
    uint32_t selected = 0;
    uint8_t *sel;
    int first_node = 1;
    if (!out || !g || top_k <= 0) return -1;

    sel = select_subgraph_nodes(g, top_k, &selected);
    if (!sel) return -1;

    fprintf(out, "{\n");
    fprintf(out, "  \"format\": \"tracepilot_subgraph_v1\",\n");
    fprintf(out, "  \"directed\": true,\n");
    fprintf(out, "  \"top_k\": %d,\n", top_k);
    fprintf(out, "  \"selected_nodes\": %u,\n", selected);
    fprintf(out, "  \"nodes\": [\n");

    for (uint32_t i = 0; i < g->node_count; i++) {
        if (!sel[i]) continue;
        if (!first_node) fprintf(out, ",\n");
        first_node = 0;
        write_node_json(out, g, i);
    }

    fprintf(out, "\n  ],\n  \"edges\": [\n");
    write_edges_json(out, g, sel, 1);
    fprintf(out, "\n  ]\n}\n");
    free(sel);
    return 0;
}

static const char *node_dot_color(graph_node_type_t t)
{
    switch (t) {
    case GRAPH_NODE_SURFACEFLINGER: return "#e45756";
    case GRAPH_NODE_RENDER_THREAD:  return "#4c78a8";
    case GRAPH_NODE_VIDEO_DECODER:  return "#f58518";
    case GRAPH_NODE_BINDER_CLIENT:
    case GRAPH_NODE_BINDER_SERVER:  return "#72b7b2";
    case GRAPH_NODE_FUTEX_WAIT:     return "#bab0ac";
    case GRAPH_NODE_CPU_RESOURCE:
    case GRAPH_NODE_MEMORY_RECLAIM:
    case GRAPH_NODE_IO_WAIT:        return "#54a24b";
    default:                        return "#b279a2";
    }
}

int export_graph_subgraph_dot(FILE *out, critical_path_graph_t *g, int top_k)
{
    uint32_t selected = 0;
    uint8_t *sel;
    if (!out || !g || top_k <= 0) return -1;

    sel = select_subgraph_nodes(g, top_k, &selected);
    if (!sel) return -1;

    fprintf(out, "digraph critical_path_subgraph {\n");
    fprintf(out, "  rankdir=LR;\n");
    fprintf(out, "  graph [fontname=\"Arial\", fontsize=11, label=\"Top-%d Critical Path Subgraph (%u nodes)\"];\n",
        top_k, selected);
    fprintf(out, "  node [fontname=\"Arial\", fontsize=9, style=filled, shape=box];\n");
    fprintf(out, "  edge [fontname=\"Arial\", fontsize=8, color=\"#666666\"];\n");

    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n;
        if (!sel[i]) continue;
        n = &g->nodes[i];
        fprintf(out, "  n%u [label=\"", i);
        if (n->comm[0])
            dot_escape(out, n->comm);
        else
            dot_escape(out, node_type_name(n->type));
        if (n->tid > 0)
            fprintf(out, "\\nTID %d", n->tid);
        fprintf(out, "\\nscore %.3f\", fillcolor=\"%s\"];\n",
            n->critical_score, node_dot_color(n->type));
    }

    for (uint32_t i = 0; i < g->node_count; i++) {
        if (!sel[i]) continue;
        for (adj_entry_t *e = g->nodes[i].out_edges; e; e = e->next) {
            if (!sel[e->target]) continue;
            fprintf(out, "  n%u -> n%u [label=\"%s", i, e->target,
                edge_type_name(e->type));
            if (e->weight.count > 1)
                fprintf(out, " x%u", e->weight.count);
            fprintf(out, "\"];\n");
        }
    }

    fprintf(out, "}\n");
    free(sel);
    return 0;
}

/* ── Graph cleanup ──────────────────────────────────────────────────── */

void graph_destroy(critical_path_graph_t *g)
{
    if (!g) return;
    for (uint32_t i = 0; i < g->node_count; i++) {
        adj_entry_t *e = g->nodes[i].out_edges;
        while (e) { adj_entry_t *next = e->next; free(e); e = next; }
        e = g->nodes[i].in_edges;
        while (e) { adj_entry_t *next = e->next; free(e); e = next; }
    }
    free(g->nodes);
    free(g->edge_hash_key);
    free(g->edge_hash_val);
    free(g);
}
