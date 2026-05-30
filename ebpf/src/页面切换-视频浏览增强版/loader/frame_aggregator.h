/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef __FRAME_AGGREGATOR_H__
#define __FRAME_AGGREGATOR_H__

#include <stdint.h>
#include <stdio.h>

/* ── Scenario identifiers ───────────────────────────────────────────── */
#define SCENARIO_PAGE_SWITCH "page_switch"
#define SCENARIO_VIDEO       "video"

/* ── Frame window ───────────────────────────────────────────────────── */
struct frame_window {
    int64_t  frame_token;
    uint64_t expected_start_ns;
    uint64_t expected_end_ns;
    uint64_t actual_end_ns;
    int      is_jank;
    double   delay_ms;
    uint64_t system_overhead_ns;
    char     frame_type[4];              /* "SF"=UI, "VD"=video decode, "VF"=video fallback, "GS"=GPU stall, "AP"=audio pos */
    int      is_video_frame;             /* 0 = UI frame (SF), 1 = video decode frame */
    int      video_decoder_tid;          /* for video frames: which tid decoded this */
    uint64_t gpu_duration_ns;            /* for GS: GPU work duration */
    uint64_t audio_pos_ns;               /* for AP: audio presentation position */
};

/* ── Thread score (legacy) ──────────────────────────────────────────── */
struct thread_score {
    uint32_t tid;
    uint32_t pid;
    double   score;
    char     comm[16];
    char     package[256];
    uint64_t runnable_delay_p95_ns;
    uint64_t wakeup_latency_p95_ns;
    uint64_t system_overhead_ns;
};

/* ── Graph node types (extended for video) ──────────────────────────── */
typedef enum {
    GRAPH_NODE_FRAME              = 0,
    GRAPH_NODE_UI_THREAD          = 1,
    GRAPH_NODE_RENDER_THREAD      = 2,
    GRAPH_NODE_BINDER_CLIENT      = 3,
    GRAPH_NODE_BINDER_SERVER      = 4,
    GRAPH_NODE_SYSTEM_SERVER      = 5,
    GRAPH_NODE_SURFACEFLINGER     = 6,
    GRAPH_NODE_FUTEX_WAIT         = 7,
    GRAPH_NODE_CPU_RESOURCE       = 8,
    GRAPH_NODE_MEMORY_RECLAIM     = 9,
    GRAPH_NODE_IO_WAIT            = 10,
    /* ── Video scenario extensions ── */
    GRAPH_NODE_VIDEO_DECODER      = 11,
    GRAPH_NODE_AUDIO_THREAD       = 12,
    GRAPH_NODE_MEDIA_SERVER       = 13,
    GRAPH_NODE_NETWORK            = 14,
    GRAPH_NODE_THERMAL            = 15,
    GRAPH_NODE_BUFFER_QUEUE       = 16,
} graph_node_type_t;

/* ── Graph edge types (extended for video) ──────────────────────────── */
typedef enum {
    GRAPH_EDGE_WAKEUP             = 0,
    GRAPH_EDGE_RUNNABLE_WAIT      = 1,
    GRAPH_EDGE_BINDER_CALL        = 2,
    GRAPH_EDGE_FUTEX_WAIT         = 3,
    GRAPH_EDGE_CPU_RUN            = 4,
    GRAPH_EDGE_PREEMPTED_BY       = 5,
    GRAPH_EDGE_FRAME_DEPENDENCY   = 6,
    GRAPH_EDGE_RESOURCE_STALL     = 7,
    /* ── Video scenario extensions ── */
    GRAPH_EDGE_DECODE_DEPENDENCY  = 8,
    GRAPH_EDGE_BUFFER_QUEUE       = 9,
    GRAPH_EDGE_THERMAL_STALL      = 10,
    GRAPH_EDGE_NETWORK_WAIT       = 11,
    GRAPH_EDGE_TYPE_COUNT         = 12,
} graph_edge_type_t;

/* ── Jank cause categories (extended for video) ─────────────────────── */
typedef enum {
    JANK_CAUSE_CPU_CONTENTION    = 0,
    JANK_CAUSE_BINDER_BLOCKING   = 1,
    JANK_CAUSE_FUTEX_BLOCKING    = 2,
    JANK_CAUSE_IO_WAIT           = 3,
    JANK_CAUSE_MEMORY_RECLAIM    = 4,
    JANK_CAUSE_GPU_STALL         = 5,
    JANK_CAUSE_RUNNABLE_DELAY    = 6,
    JANK_CAUSE_UNKNOWN           = 7,
    /* ── Video scenario extensions ── */
    JANK_CAUSE_VIDEO_LATE_RENDER = 8,
    JANK_CAUSE_AUDIO_SYNC_DRIFT  = 9,
    JANK_CAUSE_THERMAL_THROTTLE  = 10,
    JANK_CAUSE_COUNT             = 11,
} jank_cause_t;

/* ── Edge weight ────────────────────────────────────────────────────── */
typedef struct {
    uint64_t duration_ns;
    uint64_t p95_ns;
    uint32_t count;
    double   critical_window_overlap_ratio;
    double   jank_overlap_score;
} edge_weight_t;

/* ── Adjacency list entry ───────────────────────────────────────────── */
typedef struct adj_entry {
    uint32_t         target;
    graph_edge_type_t type;
    edge_weight_t    weight;
    struct adj_entry *next;
} adj_entry_t;

/* ── Graph node ─────────────────────────────────────────────────────── */
typedef struct graph_node {
    uint32_t          id;
    graph_node_type_t type;
    int32_t           tid;
    int32_t           pid;
    char              comm[16];
    char              pkg[128];
    uint64_t          total_runtime_ns;
    uint64_t          total_runnable_delay_ns;
    uint64_t          runnable_delay_p95_ns;
    uint64_t          total_wakeup_latency_ns;
    uint64_t          wakeup_latency_p95_ns;
    uint32_t          jank_frame_count;
    uint32_t          total_frame_count;

    /* Critical path graph metrics */
    double frame_window_overlap;
    double render_path_proximity;
    double binder_dependency_centrality;
    double futex_wait_contribution;
    double repeated_jank_cooccurrence;
    double background_penalty;
    double critical_score;

    /* ── Video scenario metrics ── */
    double decode_path_proximity;        /* BFS distance to VideoDecode nodes */
    double thermal_proximity;            /* how close to thermal throttling path */
    double buffer_underrun_contribution; /* buffer queue empty contribution */

    /* Dominant jank cause */
    jank_cause_t dominant_cause;
    double       cause_confidence;

    /* Adjacency list */
    adj_entry_t *out_edges;
    adj_entry_t *in_edges;
    uint32_t     out_degree;
    uint32_t     in_degree;
} graph_node_t;

/* ── Critical Path Graph ────────────────────────────────────────────── */
typedef struct {
    graph_node_t *nodes;
    uint32_t      node_count;
    uint32_t      node_capacity;

    uint64_t total_edges;
    uint64_t edge_type_counts[GRAPH_EDGE_TYPE_COUNT];

    /* Frame tracking */
    uint64_t total_frames;
    uint64_t ui_frames;                 /* SurfaceFlinger vsync frames */
    uint64_t video_frames;              /* decode-driven video frames */
    uint64_t jank_frames;
    uint64_t jank_frame_duration_sum_ns;
    double   avg_jank_duration_ns;

    /* System overhead */
    uint64_t total_irq_softirq_ns;
    uint64_t jank_system_overhead_ns;

    /* CPU frequency tracking */
    uint64_t avg_cpu_freq_little_khz;
    uint64_t avg_cpu_freq_big_khz;
    uint64_t min_cpu_freq_big_khz;      /* for thermal throttling detection */
    double   freq_throttle_ratio;
    uint64_t big_little_migration_count;

    /* Binder statistics */
    uint32_t binder_call_count;
    uint64_t total_binder_blocking_ns;

    /* Futex statistics */
    uint32_t futex_wait_count;
    uint64_t total_futex_wait_ns;

    /* ── Video statistics ── */
    uint32_t video_decoder_count;       /* number of decoder nodes detected */
    char     detected_scenario[32];     /* auto-detected: "page_switch" or "video" */
} critical_path_graph_t;

/* ── Thread ranking result ──────────────────────────────────────────── */
typedef struct {
    int32_t   rank;
    int32_t   tid;
    int32_t   pid;
    char      comm[16];
    char      pkg[128];
    double    critical_score;
    double    frame_window_overlap;
    double    binder_dependency_centrality;
    double    render_path_proximity;
    double    futex_wait_contribution;
    double    repeated_jank_cooccurrence;
    double    background_penalty;
    /* ── Video ── */
    double    decode_path_proximity;
    double    thermal_proximity;
    double    buffer_underrun_contribution;
    uint64_t  runnable_delay_p95_ns;
    uint64_t  wakeup_latency_p95_ns;
    uint64_t  total_runtime_ns;
    jank_cause_t dominant_cause;
    char      cause_name[32];
} thread_ranking_t;

/* ── Frame classification result ────────────────────────────────────── */
typedef struct {
    uint64_t     frame_id;
    uint64_t     frame_duration_ns;
    int          is_jank;
    int          is_video_frame;
    jank_cause_t primary_cause;
    jank_cause_t secondary_cause;
    double       cause_confidence;
    int32_t      culprit_tid;
    char         culprit_comm[16];
    /* Per-cause scores */
    double cpu_contention_score;
    double binder_blocking_score;
    double futex_blocking_score;
    double io_wait_score;
    double memory_reclaim_score;
    double gpu_stall_score;
    double runnable_delay_score;
    /* ── Video per-cause scores ── */
    double video_late_render_score;
    double audio_sync_drift_score;
    double thermal_throttle_score;
} frame_classification_t;

/* ── Heuristic comparison ───────────────────────────────────────────── */
typedef struct {
    double graph_avg_precision_at_k;
    double heuristic_avg_precision_at_k;
    int    graph_unique_culprits;
    int    heuristic_unique_culprits;
    int    overlap_count;
    double graph_mean_signal_noise_ratio;
    double heuristic_mean_signal_noise_ratio;
} heuristics_comparison_t;

/* ── Scoring weights (per-scenario) ─────────────────────────────────── */
typedef struct {
    const char *scenario_name;
    double a;  /* frame_window_overlap */
    double b;  /* runnable_delay_p95 */
    double c;  /* binder_dependency_centrality */
    double d;  /* futex_wait_contribution */
    double e;  /* render_path_proximity */
    double f;  /* repeated_jank_cooccurrence */
    double g;  /* background_penalty */
    double h;  /* decode_path_proximity (video) */
    double i;  /* thermal_proximity (video) */
    double j;  /* buffer_underrun_contribution (video) */
    double k;  /* network_penalty (video, subtracted) */
} scenario_weights_t;

/* ── Forward declarations ───────────────────────────────────────────── */
struct sched_event;
struct system_event;
struct enhanced_event;

/* ── Existing API ───────────────────────────────────────────────────── */
void frames_init(struct frame_window *frames, int num_frames);
void frames_set_clock_offset(int64_t offset_ns);
void aggregate_event(const struct sched_event *evt, const struct frame_window *fw);
void accumulate_system_event(const struct system_event *evt, struct frame_window *fw);
void frames_set_thread_info(uint32_t tid, uint32_t pid, const char *package);
int  output_topk(FILE *out, int top_k);
int  parse_frame_json(const char *filename, struct frame_window **out);

/* ── Enhanced API ───────────────────────────────────────────────────── */

critical_path_graph_t *build_critical_path_graph(
    struct sched_event  *sched_events,  size_t sched_count,
    struct system_event *sys_events,     size_t sys_count,
    struct enhanced_event *enh_events,   size_t enh_count,
    struct frame_window *frames,         int    frame_count);

int compute_critical_scores(critical_path_graph_t *g,
    double a, double b, double c, double d, double e, double f, double g_coeff);
int compute_binder_centrality(critical_path_graph_t *g);
int compute_render_proximity(critical_path_graph_t *g);

/* ── Video scenario extensions ──────────────────────────────────────── */

/* Auto-detect scenario from thread names. Returns "page_switch" or "video". */
const char *detect_scenario(critical_path_graph_t *g);

/* Extend graph with video-specific nodes and edges.
   Must be called AFTER build_critical_path_graph(). */
int video_extend_graph(critical_path_graph_t *g,
    struct sched_event *sched_events, size_t sched_count,
    struct frame_window *frames, int frame_count);

/* Compute BFS proximity to VideoDecode nodes */
int compute_decode_proximity(critical_path_graph_t *g);

/* Compute thermal proximity based on CPU frequency drop ratio */
int compute_thermal_proximity(critical_path_graph_t *g);

/* Get default weights for a scenario */
void get_scenario_weights(const char *scenario, scenario_weights_t *w);

/* Run full video scenario analysis: detect video threads, extend graph,
   compute video-specific metrics, then score with video weights. */
int run_video_scenario(critical_path_graph_t *g,
    struct sched_event *sched_events, size_t sched_count,
    struct frame_window *frames, int frame_count);

/* ── Classification + output ────────────────────────────────────────── */

int classify_jank_causes(critical_path_graph_t *g,
    struct frame_window *frames, int frame_count,
    frame_classification_t **out, int *out_count);

heuristics_comparison_t compare_heuristics(critical_path_graph_t *g,
    struct frame_window *frames, int frame_count, int top_k);

int output_enhanced_topk(FILE *out, critical_path_graph_t *g, int top_k,
    frame_classification_t *classifications, int class_count,
    heuristics_comparison_t *comparison);

void graph_destroy(critical_path_graph_t *g);

#endif /* __FRAME_AGGREGATOR_H__ */