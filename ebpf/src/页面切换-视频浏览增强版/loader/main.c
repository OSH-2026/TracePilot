/* SPDX-License-Identifier: BSD-2-Clause */
/*
 * TracePilot — Enhanced: Frame-Centric eBPF + Interaction Critical Path Graph
 *
 * New in v2 (enhanced):
 *   - Binder transaction/reply tracking (binder dependency graph)
 *   - Futex wait/wake tracking (futex wait graph)
 *   - CPU frequency tracking (big-little attribution)
 *   - Memory reclaim tracking
 *   - Graph-based CriticalScore(tid) with 7-term formula
 *   - Jank cause classifier
 *   - Heuristic strategy comparison
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <errno.h>
#include <getopt.h>
#include <time.h>
#include <pthread.h>
#include <sys/resource.h>

#ifdef __ANDROID__
#include <libbpf.h>
#else
#include <bpf/libbpf.h>
#endif
#include "frame_aggregator.h"
#include "resolver.h"
#include "../bpf/tracepilot.bpf.h"

#define DEFAULT_DURATION_S  30
#define DEFAULT_TOP_K       10
#define EVENTS_CHUNK        65536
#define EVENTS_MAGIC        0x32765054U
#define EVENTS_MAGIC_V3     0x33657054U  /* "TPv3" — enhanced format */

struct events_file_header {
    uint32_t magic;
    uint32_t version;
    uint64_t sched_count;
    uint64_t sys_count;
};

/* V3 extended header */
struct events_file_header_v3 {
    uint32_t magic;           /* EVENTS_MAGIC_V3 */
    uint32_t version;         /* 3 */
    uint64_t sched_count;
    uint64_t sys_count;
    uint64_t enh_count;       /* NEW: enhanced event count */
};

static volatile sig_atomic_t g_exiting = 0;

/* ── Event storage ──────────────────────────────────────────────────── */
static struct sched_event   *g_events       = NULL;
static size_t                g_events_cap   = 0;
static size_t                g_events_cnt   = 0;

static struct system_event  *g_sys_events   = NULL;
static size_t                g_sys_events_cap = 0;
static size_t                g_sys_events_cnt = 0;

static struct enhanced_event *g_enh_events  = NULL;
static size_t                 g_enh_events_cap = 0;
static size_t                 g_enh_events_cnt = 0;

/* ── CLI flags ──────────────────────────────────────────────────────── */
static const char *g_package      = NULL;
static int         g_duration_s   = DEFAULT_DURATION_S;
static const char *g_frame_data   = NULL;
static const char *g_output       = NULL;
static const char *g_events_out   = NULL;
static const char *g_events_in    = NULL;
static int         g_top_k        = DEFAULT_TOP_K;
static int         g_debug        = 0;
static int         g_graph_mode   = 0;
static const char *g_scenario     = SCENARIO_PAGE_SWITCH;

/* ── CPU freq polling ────────────────────────────────────────────────── */
#define MAX_CPUS 16
static volatile uint64_t g_polled_freq_min[MAX_CPUS] = {0};
static volatile uint64_t g_polled_freq_sum[MAX_CPUS] = {0};
static volatile int      g_polled_freq_cnt[MAX_CPUS] = {0};

static void *poll_cpu_freq_thread(void *arg)
{
    (void)arg;
    char path[64];
    uint64_t end_time = (uint64_t)time(NULL) + (uint64_t)(uintptr_t)arg;

    while (time(NULL) < (time_t)end_time && !g_exiting) {
        for (int cpu = 0; cpu < MAX_CPUS; cpu++) {
            snprintf(path, sizeof(path),
                "/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq", cpu);
            FILE *f = fopen(path, "r");
            if (!f) continue;
            unsigned long khz = 0;
            if (fscanf(f, "%lu", &khz) == 1 && khz > 0) {
                if (g_polled_freq_min[cpu] == 0 || khz < g_polled_freq_min[cpu])
                    g_polled_freq_min[cpu] = khz;
                g_polled_freq_sum[cpu] += khz;
                g_polled_freq_cnt[cpu]++;
            }
            fclose(f);
        }
        usleep(100000); /* 100ms */
    }
    return NULL;
}

static void sig_handler(int sig) { (void)sig; g_exiting = 1; }

static void print_usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [OPTIONS]\n"
        "Options:\n"
        "  -p, --package NAME       Target app package name\n"
        "  -d, --duration SEC       Collection duration in seconds (default: %d)\n"
        "  -f, --frame-data FILE    Frame data from Perfetto\n"
        "  -o, --output FILE        Output JSON file\n"
        "  -e, --events-out FILE    Save raw events to binary file\n"
        "  -i, --events-in FILE     Load raw events (offline mode)\n"
        "  -k, --top-k N            Number of top threads (default: %d)\n"
        "  -G, --graph              Enable graph-based critical path analysis\n"
        "  -s, --scenario MODE       Analysis scenario: page_switch (default) or video\n"
        "  -D, --debug              Enable debug output\n"
        "  -h, --help               Show this help\n"
        "\nOn-device collection:\n"
        "  %s -d 30 -e events.bin -G -D\n"
        "\nOffline analysis (host):\n"
        "  %s -i events.bin -f frames.txt -o result.json -G\n"
        "\n",
        prog, DEFAULT_DURATION_S, DEFAULT_TOP_K, prog, prog);
}

static int parse_args(int argc, char **argv)
{
    static struct option long_opts[] = {
        {"package",    required_argument, 0, 'p'},
        {"duration",   required_argument, 0, 'd'},
        {"frame-data", required_argument, 0, 'f'},
        {"output",     required_argument, 0, 'o'},
        {"events-out", required_argument, 0, 'e'},
        {"events-in",  required_argument, 0, 'i'},
        {"top-k",      required_argument, 0, 'k'},
        {"graph",      no_argument,       0, 'G'},
        {"scenario",   required_argument, 0, 's'},
        {"debug",      no_argument,       0, 'D'},
        {"help",       no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };

    int c;
    while ((c = getopt_long(argc, argv, "p:d:f:o:e:i:k:Ghs:D", long_opts, NULL)) != -1) {
        switch (c) {
        case 'p': g_package    = optarg; break;
        case 'd': g_duration_s = atoi(optarg); break;
        case 'f': g_frame_data = optarg; break;
        case 'o': g_output     = optarg; break;
        case 'e': g_events_out = optarg; break;
        case 'i': g_events_in  = optarg; break;
        case 'k': g_top_k      = atoi(optarg); break;
        case 'G': g_graph_mode = 1; break;
        case 's': g_scenario   = optarg; break;
        case 'D': g_debug      = 1; break;
        case 'h': print_usage(argv[0]); return -1;
        default:  print_usage(argv[0]); return -1;
        }
    }
    if (g_duration_s <= 0) { fprintf(stderr, "Invalid duration: %d\n", g_duration_s); return -1; }
    if (g_top_k <= 0)      { fprintf(stderr, "Invalid top-k: %d\n", g_top_k); return -1; }
    return 0;
}

/* ── Sched event storage ────────────────────────────────────────────── */
static void store_event(const struct sched_event *evt)
{
    if (g_events_cnt >= g_events_cap) {
        size_t new_cap = g_events_cap + EVENTS_CHUNK;
        struct sched_event *tmp = realloc(g_events, new_cap * sizeof(*g_events));
        if (!tmp) { fprintf(stderr, "realloc events failed\n"); return; }
        g_events = tmp;
        g_events_cap = new_cap;
    }
    g_events[g_events_cnt++] = *evt;
}

/* ── System event storage ───────────────────────────────────────────── */
static void store_sys_event(const struct system_event *evt)
{
    if (g_sys_events_cnt >= g_sys_events_cap) {
        size_t new_cap = g_sys_events_cap + EVENTS_CHUNK;
        struct system_event *tmp = realloc(g_sys_events, new_cap * sizeof(*g_sys_events));
        if (!tmp) return;
        g_sys_events = tmp;
        g_sys_events_cap = new_cap;
    }
    g_sys_events[g_sys_events_cnt++] = *evt;
}

/* ── Enhanced event storage ─────────────────────────────────────────── */
static void store_enhanced_event(const struct enhanced_event *evt)
{
    if (g_enh_events_cnt >= g_enh_events_cap) {
        size_t new_cap = g_enh_events_cap + EVENTS_CHUNK;
        struct enhanced_event *tmp = realloc(g_enh_events, new_cap * sizeof(*g_enh_events));
        if (!tmp) return;
        g_enh_events = tmp;
        g_enh_events_cap = new_cap;
    }
    g_enh_events[g_enh_events_cnt++] = *evt;
}

/* ── Ring buffer callbacks ──────────────────────────────────────────── */
static int handle_event(void *ctx, void *data, size_t data_sz)
{
    (void)ctx;
    if (data_sz != sizeof(struct sched_event)) {
        fprintf(stderr, "WARN: unexpected sched event size %zu\n", data_sz);
        return 0;
    }
    const struct sched_event *evt = data;
    if (g_package) {
        char pkg[256] = {0};
        uint32_t pid = evt->next_pid;
        if (pid > 0 && resolve_pid((pid_t)pid, pkg, sizeof(pkg)) == 0) {
            if (strcmp(pkg, g_package) != 0) {
                if (evt->event_type == EVENT_SCHED_SWITCH && evt->prev_pid > 0) {
                    char pkg2[256] = {0};
                    if (resolve_pid((pid_t)evt->prev_pid, pkg2, sizeof(pkg2)) != 0 ||
                        strcmp(pkg2, g_package) != 0) return 0;
                } else return 0;
            }
        }
    }
    store_event(evt);
    if (g_debug) {
        fprintf(stderr, "[DBG] %s ts=%llu prev=%u:%s next=%u:%s cpu=%u\n",
            evt->event_type == EVENT_SCHED_SWITCH ? "SW" : "WK",
            (unsigned long long)evt->timestamp_ns,
            evt->prev_tid, evt->prev_comm,
            evt->next_tid, evt->next_comm, evt->cpu);
    }
    return 0;
}

static int handle_sys_event(void *ctx, void *data, size_t data_sz)
{
    (void)ctx;
    if (data_sz != sizeof(struct system_event)) return 0;
    const struct system_event *evt = data;
    store_sys_event(evt);
    if (g_debug) {
        fprintf(stderr, "[DBG] %s vec=%d cpu=%u dur=%llu\n",
            evt->event_type == SYS_EVENT_IRQ ? "IRQ" : "SFT",
            evt->irq_vec, evt->cpu, (unsigned long long)evt->duration_ns);
    }
    return 0;
}

static int handle_enhanced_event(void *ctx, void *data, size_t data_sz)
{
    (void)ctx;
    if (data_sz != sizeof(struct enhanced_event)) return 0;
    const struct enhanced_event *evt = data;
    store_enhanced_event(evt);
    if (g_debug) {
        static const char *names[] = {
            "BINDER_CALL", "BINDER_RECV", "FUTEX_WAIT",
            "FUTEX_WAKE",  "CPU_FREQ",    "MEM_RECLAIM"
        };
        const char *nm = (evt->type < 6) ? names[evt->type] : "?";
        fprintf(stderr, "[DBG] ENH %s ts=%llu tid=%u comm=%s\n",
            nm, (unsigned long long)evt->timestamp_ns, evt->tid, evt->comm);
    }
    return 0;
}

/* ── Save events to binary (v3 enhanced format) ─────────────────────── */
static int save_events(const char *path)
{
    struct events_file_header_v3 hdr = {
        .magic       = EVENTS_MAGIC_V3,
        .version     = 3,
        .sched_count = g_events_cnt,
        .sys_count   = g_sys_events_cnt,
        .enh_count   = g_enh_events_cnt,
    };
    FILE *fp = fopen(path, "wb");
    if (!fp) { perror("fopen events-out"); return -1; }

    if (fwrite(&hdr, sizeof(hdr), 1, fp) != 1) goto write_err;

    if (g_events_cnt > 0) {
        if (fwrite(g_events, sizeof(struct sched_event), g_events_cnt, fp) != g_events_cnt)
            goto write_err;
    }
    if (g_sys_events_cnt > 0) {
        if (fwrite(g_sys_events, sizeof(struct system_event), g_sys_events_cnt, fp) != g_sys_events_cnt)
            goto write_err;
    }
    if (g_enh_events_cnt > 0) {
        if (fwrite(g_enh_events, sizeof(struct enhanced_event), g_enh_events_cnt, fp) != g_enh_events_cnt)
            goto write_err;
    }

    fclose(fp);
    if (g_debug)
        fprintf(stderr, "[DBG] saved %zu sched + %zu sys + %zu enh events (v3)\n",
            g_events_cnt, g_sys_events_cnt, g_enh_events_cnt);
    return 0;

write_err:
    perror("fwrite events");
    fclose(fp);
    return -1;
}

/* ── Load events from binary (v1/v2/v3) ─────────────────────────────── */
static int load_events(const char *path)
{
    FILE *fp = fopen(path, "rb");
    if (!fp) { perror("fopen events-in"); return -1; }

    /* Try v3 header first */
    struct events_file_header_v3 hdr3;
    rewind(fp);
    if (fread(&hdr3, sizeof(hdr3), 1, fp) == 1 && hdr3.magic == EVENTS_MAGIC_V3) {
        if (hdr3.sched_count > 0) {
            g_events_cnt = hdr3.sched_count;
            g_events_cap = hdr3.sched_count;
            g_events = malloc(g_events_cnt * sizeof(struct sched_event));
            if (!g_events) { fclose(fp); return -1; }
            if (fread(g_events, sizeof(struct sched_event), g_events_cnt, fp) != g_events_cnt) {
                fprintf(stderr, "Failed to read sched events\n"); fclose(fp); return -1;
            }
        }
        if (hdr3.sys_count > 0) {
            g_sys_events_cnt = hdr3.sys_count;
            g_sys_events_cap = hdr3.sys_count;
            g_sys_events = malloc(g_sys_events_cnt * sizeof(struct system_event));
            if (!g_sys_events) { fclose(fp); return -1; }
            if (fread(g_sys_events, sizeof(struct system_event), g_sys_events_cnt, fp) != g_sys_events_cnt) {
                fprintf(stderr, "Failed to read sys events\n"); fclose(fp); return -1;
            }
        }
        if (hdr3.enh_count > 0) {
            g_enh_events_cnt = hdr3.enh_count;
            g_enh_events_cap = hdr3.enh_count;
            g_enh_events = malloc(g_enh_events_cnt * sizeof(struct enhanced_event));
            if (!g_enh_events || fread(g_enh_events, sizeof(struct enhanced_event),
                    g_enh_events_cnt, fp) != g_enh_events_cnt) {
                fprintf(stderr, "WARN: failed to read enhanced events, continuing without\n");
                free(g_enh_events);
                g_enh_events = NULL;
                g_enh_events_cnt = 0;
            }
        }
        fclose(fp);
        if (g_debug)
            fprintf(stderr, "[DBG] loaded %zu sched + %zu sys + %zu enh events (v3)\n",
                g_events_cnt, g_sys_events_cnt, g_enh_events_cnt);
        return 0;
    }

    /* Try v2 header */
    rewind(fp);
    struct events_file_header hdr2;
    if (fread(&hdr2, sizeof(hdr2), 1, fp) == 1 && hdr2.magic == EVENTS_MAGIC) {
        if (hdr2.sched_count > 0) {
            g_events_cnt = hdr2.sched_count;
            g_events_cap = hdr2.sched_count;
            g_events = malloc(g_events_cnt * sizeof(struct sched_event));
            if (!g_events) { fclose(fp); return -1; }
            if (fread(g_events, sizeof(struct sched_event), g_events_cnt, fp) != g_events_cnt) {
                fprintf(stderr, "Failed to read sched events\n"); fclose(fp); return -1;
            }
        }
        if (hdr2.sys_count > 0) {
            g_sys_events_cnt = hdr2.sys_count;
            g_sys_events_cap = hdr2.sys_count;
            g_sys_events = malloc(g_sys_events_cnt * sizeof(struct system_event));
            if (!g_sys_events) { fclose(fp); return -1; }
            if (fread(g_sys_events, sizeof(struct system_event), g_sys_events_cnt, fp) != g_sys_events_cnt) {
                fprintf(stderr, "Failed to read sys events\n"); fclose(fp); return -1;
            }
        }
        fclose(fp);
        if (g_debug)
            fprintf(stderr, "[DBG] loaded %zu sched + %zu sys events (v2, no enhanced)\n",
                g_events_cnt, g_sys_events_cnt);
        return 0;
    }

    /* v1 fallback: raw sched events only */
    fseek(fp, 0, SEEK_END);
    long sz = ftell(fp);
    rewind(fp);

    if (sz <= 0 || sz % (long)sizeof(struct sched_event) != 0) {
        fprintf(stderr, "Invalid events file size: %ld\n", sz);
        fclose(fp); return -1;
    }

    g_events_cnt = sz / sizeof(struct sched_event);
    g_events_cap = g_events_cnt;
    g_events = malloc(sz);
    if (!g_events) { fclose(fp); return -1; }
    if (fread(g_events, sizeof(struct sched_event), g_events_cnt, fp) != g_events_cnt) {
        fprintf(stderr, "Failed to read events\n"); fclose(fp); return -1;
    }
    fclose(fp);
    if (g_debug)
        fprintf(stderr, "[DBG] loaded %zu events (v1, no sys/enhanced)\n", g_events_cnt);
    return 0;
}

/* ── Run graph-based analysis ───────────────────────────────────────── */
static int run_graph_analysis(struct frame_window *frames, int num_frames)
{
    critical_path_graph_t *g;
    frame_classification_t *classifications = NULL;
    int class_count = 0;
    heuristics_comparison_t comparison;
    memset(&comparison, 0, sizeof(comparison));
    FILE *out;

    g = build_critical_path_graph(
        g_events, g_events_cnt,
        g_sys_events, g_sys_events_cnt,
        g_enh_events, g_enh_events_cnt,
        frames, num_frames);
    if (!g) {
        fprintf(stderr, "Failed to build critical path graph\n");
        return -1;
    }

    if (g_debug)
        fprintf(stderr, "[DBG] graph: %u nodes, %llu edges\n",
            g->node_count, (unsigned long long)g->total_edges);

    /* Determine scenario: CLI flag or auto-detect */
    const char *scenario = g_scenario;
    if (strcmp(scenario, SCENARIO_VIDEO) == 0 ||
        detect_scenario(g) == SCENARIO_VIDEO) {
        if (g_debug)
            fprintf(stderr, "[DBG] detected video scenario (%s)\n", g->detected_scenario);

        run_video_scenario(g, g_events, g_events_cnt, frames, num_frames);
    } else {
        if (g_debug)
            fprintf(stderr, "[DBG] detected page_switch scenario\n");

        scenario_weights_t w;
        get_scenario_weights(SCENARIO_PAGE_SWITCH, &w);
        compute_critical_scores(g, w.a, w.b, w.c, w.d, w.e, w.f, w.g);
    }

    if (g_debug)
        fprintf(stderr, "[DBG] computed critical scores for %u nodes\n", g->node_count);

    /* Classify jank causes */
    classify_jank_causes(g, frames, num_frames, &classifications, &class_count);

    if (g_debug)
        fprintf(stderr, "[DBG] classified %d frames\n", class_count);

    /* Compare heuristics */
    comparison = compare_heuristics(g, frames, num_frames, g_top_k);

    if (g_debug)
        fprintf(stderr, "[DBG] heuristic comparison: overlap=%d\n", comparison.overlap_count);

    /* Output */
    if (g_output) {
        out = fopen(g_output, "w");
        if (!out) { perror("fopen output"); graph_destroy(g); free(classifications); return -1; }
    } else {
        out = stdout;
    }

    output_enhanced_topk(out, g, g_top_k, classifications, class_count, &comparison);

    if (g_output) fclose(out);

    free(classifications);
    graph_destroy(g);
    return 0;
}

/* ── Legacy + enhanced analysis pipeline ────────────────────────────── */
static int run_analysis(void)
{
    struct frame_window *frames = NULL;
    int num_frames;
    size_t i;

    if (!g_frame_data) {
        fprintf(stderr, "No --frame-data provided, skipping analysis.\n");
        return 0;
    }

    num_frames = parse_frame_json(g_frame_data, &frames);
    if (num_frames < 0) {
        fprintf(stderr, "Failed to parse frame JSON: %s\n", g_frame_data);
        return -1;
    }
    if (g_debug)
        fprintf(stderr, "[DBG] parsed %d frames\n", num_frames);

    frames_init(frames, num_frames);

    /* Auto-detect clock offset */
    if (g_events_cnt > 0 && num_frames > 0) {
        uint64_t min_ebpf  = g_events[0].timestamp_ns;
        uint64_t min_frame = frames[0].expected_start_ns;
        int64_t offset = (int64_t)(min_frame - min_ebpf);
        frames_set_clock_offset(offset);
        if (g_debug)
            fprintf(stderr, "[DBG] clock offset: %lld ns\n", (long long)offset);
    }

    /* ── Run enhanced graph-based analysis if enabled ── */
    if (g_graph_mode) {
        if (g_debug)
            fprintf(stderr, "[DBG] running graph-based critical path analysis...\n");
        int ret = run_graph_analysis(frames, num_frames);
        free(frames);
        return ret;
    }

    /* ── Legacy analysis ── */
    for (i = 0; i < g_sys_events_cnt; i++) {
        const struct system_event *evt = &g_sys_events[i];
        for (int j = 0; j < num_frames; j++)
            accumulate_system_event(evt, &frames[j]);
    }

    for (i = 0; i < g_events_cnt; i++) {
        const struct sched_event *evt = &g_events[i];
        for (int j = 0; j < num_frames; j++)
            aggregate_event(evt, &frames[j]);
    }

    for (i = 0; i < g_events_cnt; i++) {
        const struct sched_event *evt = &g_events[i];
        char pkg[256] = {0};
        if (evt->event_type == EVENT_SCHED_SWITCH) {
            if (evt->next_pid > 0 && resolve_pid((pid_t)evt->next_pid, pkg, sizeof(pkg)) == 0)
                frames_set_thread_info(evt->next_tid, evt->next_pid, pkg);
            if (evt->prev_pid > 0 && resolve_pid((pid_t)evt->prev_pid, pkg, sizeof(pkg)) == 0)
                frames_set_thread_info(evt->prev_tid, evt->prev_pid, pkg);
        }
    }

    FILE *out;
    if (g_output) {
        out = fopen(g_output, "w");
        if (!out) { perror("fopen output"); free(frames); return -1; }
    } else {
        out = stdout;
    }
    output_topk(out, g_top_k);
    if (g_output) fclose(out);
    free(frames);
    return 0;
}

/* ── main ───────────────────────────────────────────────────────────── */
int main(int argc, char **argv)
{
    struct ring_buffer *rb = NULL;
    struct bpf_object *obj = NULL;
    struct bpf_program *prog;
    int err;

    if (parse_args(argc, argv) != 0)
        return 1;

    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);

    /* ── Offline mode: load events from file ── */
    if (g_events_in) {
        if (load_events(g_events_in) != 0) return 1;
        if (g_frame_data) run_analysis();
        else if (g_debug)
            fprintf(stderr, "[DBG] no --frame-data, skipping analysis\n");
        free(g_events); free(g_sys_events); free(g_enh_events);
        return 0;
    }

    /* ── Live collection mode ── */
    struct rlimit rlim = {RLIM_INFINITY, RLIM_INFINITY};
    setrlimit(RLIMIT_MEMLOCK, &rlim);

    {
        char dummy[256];
        resolve_pid(1, dummy, sizeof(dummy));
    }

    if (g_debug)
        fprintf(stderr, "[DBG] loading BPF object...\n");

    obj = bpf_object__open_file("/data/local/tmp/tracepilot.bpf.o", NULL);
    if (!obj) {
        fprintf(stderr, "Failed to open BPF object: %d\n", errno);
        return 1;
    }

    err = bpf_object__load(obj);
    if (err) {
        fprintf(stderr, "Failed to load BPF object: %d\n", err);
        bpf_object__close(obj);
        return 1;
    }

    bpf_object__for_each_program(prog, obj) {
        struct bpf_link *link = bpf_program__attach(prog);
        if (!link && g_debug)
            fprintf(stderr, "[DBG] attach failed for '%s' (may be optional)\n",
                bpf_program__name(prog));
    }

    /* Set up ring buffers */
    struct bpf_map *m_events = bpf_object__find_map_by_name(obj, "events");
    if (!m_events) {
        fprintf(stderr, "Failed to find 'events' map\n");
        bpf_object__close(obj); return 1;
    }
    rb = ring_buffer__new(bpf_map__fd(m_events), handle_event, NULL, NULL);
    if (!rb) {
        fprintf(stderr, "Failed to create ring buffer\n");
        bpf_object__close(obj);
        return 1;
    }

    struct bpf_map *m_sys = bpf_object__find_map_by_name(obj, "sys_events");
    if (m_sys)
        ring_buffer__add(rb, bpf_map__fd(m_sys), handle_sys_event, NULL);

    struct bpf_map *m_enh = bpf_object__find_map_by_name(obj, "enhanced_events");
    if (m_enh)
        ring_buffer__add(rb, bpf_map__fd(m_enh), handle_enhanced_event, NULL);

    /* Start CPU freq polling thread (supplements BPF cpufreq events) */
    pthread_t freq_thread;
    uint64_t end_arg = (uint64_t)(time(NULL) + g_duration_s + 2);
    pthread_create(&freq_thread, NULL, poll_cpu_freq_thread, (void *)(uintptr_t)end_arg);

    time_t end = time(NULL) + g_duration_s;
    if (g_debug)
        fprintf(stderr, "[DBG] collecting for %d seconds...\n", g_duration_s);

    while (time(NULL) < end && !g_exiting)
        ring_buffer__poll(rb, 100);

    g_exiting = 1;
    pthread_join(freq_thread, NULL);

    /* Merge polled freq into enhanced_events for graph analysis */
    for (int cpu = 0; cpu < MAX_CPUS; cpu++) {
        if (g_polled_freq_cnt[cpu] > 0) {
            uint64_t avg = g_polled_freq_sum[cpu] / g_polled_freq_cnt[cpu];
            struct enhanced_event ee = {
                .type = ENH_EV_CPU_FREQ,
                .timestamp_ns = (uint64_t)time(NULL) * 1000000000ULL,
                .value1 = avg,
                .value2 = (cpu >= 4 && cpu < 6) ? 1 : (cpu >= 6 ? 2 : 0),
            };
            store_enhanced_event(&ee);
            if (g_debug)
                fprintf(stderr, "[DBG] freq poll cpu%d: avg=%llu min=%llu\n",
                    cpu, (unsigned long long)avg,
                    (unsigned long long)g_polled_freq_min[cpu]);
        }
    }

    if (g_debug) {
        fprintf(stderr, "[DBG] collection done: %zu sched, %zu sys, %zu enhanced events\n",
            g_events_cnt, g_sys_events_cnt, g_enh_events_cnt);
    }

    /* Save events to file */
    if (g_events_out && (g_events_cnt > 0 || g_sys_events_cnt > 0 || g_enh_events_cnt > 0))
        save_events(g_events_out);

    /* Run analysis */
    if (g_frame_data)
        run_analysis();
    else if (g_debug)
        fprintf(stderr, "[DBG] no --frame-data, skipping analysis\n");

    ring_buffer__free(rb);
    bpf_object__close(obj);
    free(g_events);
    free(g_sys_events);
    free(g_enh_events);
    return 0;
}
