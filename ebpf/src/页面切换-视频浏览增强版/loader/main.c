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
#include "identity.h"
#include "hint_engine.h"
#include "inference_engine.h"
#include "thermal_profile.h"
#include "session_compare.h"
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
static const char *g_graph_json   = NULL;
static const char *g_graph_dot    = NULL;
static const char *g_graph_subjson = NULL;
static char        g_graph_json_auto[512];
static char        g_graph_dot_auto[512];
static char        g_graph_subjson_auto[512];
static char        g_hints_json_auto[512];
static char        g_hints_audit_auto[512];
static const char *g_hints_json   = NULL;
static const char *g_hints_audit  = NULL;
static int         g_hint_dry_run   = 1;
static const char *g_thermal_data   = NULL;
static char        g_thermal_auto[512];
static const char *g_compare_dir    = NULL;
static const char *g_compare_out    = NULL;

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

static void setup_identity_from_events(void)
{
    uint64_t ts0 = g_events_cnt > 0 ? g_events[0].timestamp_ns : 0;

    identity_init_session(g_package, ts0);
    if (g_events_in)
        identity_auto_load_beside(g_events_in);
    identity_scan_sched_events(g_events, g_events_cnt);
}

static void save_identity_beside(const char *events_path)
{
    char path[512];
    const char *slash;

    if (!events_path)
        return;

    slash = strrchr(events_path, '/');
    if (!slash)
        slash = strrchr(events_path, '\\');

    if (slash) {
        size_t dlen = (size_t)(slash - events_path);
        if (dlen >= sizeof(path))
            dlen = sizeof(path) - 32;
        memcpy(path, events_path, dlen);
        path[dlen] = '\0';
        snprintf(path + dlen, sizeof(path) - dlen, "/identity_map.json");
    } else {
        snprintf(path, sizeof(path), "identity_map.json");
    }

    setup_identity_from_events();
    if (identity_save_json(path) == 0 && g_debug)
        fprintf(stderr, "[DBG] saved identity map → %s\n", path);
}

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
        "  --graph-json FILE        Export full graph topology (JSON)\n"
        "  --graph-subgraph FILE    Export Top-K subgraph (JSON, for viz)\n"
        "  --graph-dot FILE         Export Top-K subgraph (Graphviz DOT)\n"
        "  --hints-json FILE        Export scheduling hints (JSON)\n"
        "  --hint-apply             Apply hints on device (default: dry-run audit only)\n"
        "  --thermal-data FILE      Thermal profile (from thermal_query.sql)\n"
        "  --compare-dir DIR        Compare result.json under subdirs (Step 3)\n"
        "  --compare-out FILE       Multi-session compare report output\n"
        "  -s, --scenario MODE       Analysis scenario: page_switch (default) or video\n"
        "  -D, --debug              Enable debug output\n"
        "  -h, --help               Show this help\n"
        "\nOn-device collection:\n"
        "  %s -d 30 -e events.bin -G -D\n"
        "\nOffline analysis (host):\n"
        "  %s -i events.bin -f frames.txt -o result.json -G\n"
        "  %s -i events.bin -f frames.txt -o result.json -G -k 10 \\\n"
        "      --graph-json graph_topology.json --graph-dot graph_subgraph.dot\n"
        "\nMulti-session compare (Step 3):\n"
        "  %s --compare-dir output --compare-out output/compare_report.json\n"
        "\n",
        prog, DEFAULT_DURATION_S, DEFAULT_TOP_K, prog, prog, prog, prog);
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
        {"graph-json", required_argument, 0, 1001},
        {"graph-subgraph", required_argument, 0, 1002},
        {"graph-dot",  required_argument, 0, 1003},
        {"hints-json", required_argument, 0, 1004},
        {"hint-apply", no_argument,       0, 1005},
        {"thermal-data", required_argument, 0, 1006},
        {"compare-dir", required_argument, 0, 1007},
        {"compare-out", required_argument, 0, 1008},
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
        case 1001: g_graph_json = optarg; break;
        case 1002: g_graph_subjson = optarg; break;
        case 1003: g_graph_dot = optarg; break;
        case 1004: g_hints_json = optarg; break;
        case 1005: g_hint_dry_run = 0; break;
        case 1006: g_thermal_data = optarg; break;
        case 1007: g_compare_dir = optarg; break;
        case 1008: g_compare_out = optarg; break;
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
    save_identity_beside(path);
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

/* Derive default graph export paths from -o output path */
static void resolve_graph_export_paths(void)
{
    char dir[512];
    const char *slash;

    if (!g_output)
        return;

    slash = strrchr(g_output, '/');
    if (!slash)
        slash = strrchr(g_output, '\\');

    if (slash) {
        size_t dlen = (size_t)(slash - g_output);
        if (dlen >= sizeof(dir))
            dlen = sizeof(dir) - 1;
        memcpy(dir, g_output, dlen);
        dir[dlen] = '\0';
    } else {
        strncpy(dir, ".", sizeof(dir) - 1);
        dir[sizeof(dir) - 1] = '\0';
    }

    if (!g_graph_json) {
        snprintf(g_graph_json_auto, sizeof(g_graph_json_auto),
            "%s/graph_topology.json", dir);
        g_graph_json = g_graph_json_auto;
    }
    if (!g_graph_subjson) {
        snprintf(g_graph_subjson_auto, sizeof(g_graph_subjson_auto),
            "%s/graph_subgraph.json", dir);
        g_graph_subjson = g_graph_subjson_auto;
    }
    if (!g_graph_dot) {
        snprintf(g_graph_dot_auto, sizeof(g_graph_dot_auto),
            "%s/graph_subgraph.dot", dir);
        g_graph_dot = g_graph_dot_auto;
    }
    if (!g_hints_json) {
        snprintf(g_hints_json_auto, sizeof(g_hints_json_auto),
            "%s/hints.json", dir);
        g_hints_json = g_hints_json_auto;
    }
    if (!g_hints_audit) {
        snprintf(g_hints_audit_auto, sizeof(g_hints_audit_auto),
            "%s/hints_audit.log", dir);
        g_hints_audit = g_hints_audit_auto;
    }
}

static int export_graph_files(critical_path_graph_t *g)
{
    FILE *fp;
    struct timespec t0, t1;

    if (!g_graph_json && !g_graph_subjson && !g_graph_dot)
        return 0;

    if (g_graph_json) {
        clock_gettime(CLOCK_MONOTONIC, &t0);
        fp = fopen(g_graph_json, "w");
        if (!fp) {
            perror("fopen graph-json");
            return -1;
        }
        export_graph_topology_json(fp, g);
        fclose(fp);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (g_debug)
            fprintf(stderr, "[TIME] export_graph_topology_json: %.3f s  → %s\n",
                (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9,
                g_graph_json);
    }

    if (g_graph_subjson) {
        clock_gettime(CLOCK_MONOTONIC, &t0);
        fp = fopen(g_graph_subjson, "w");
        if (!fp) {
            perror("fopen graph-subgraph");
            return -1;
        }
        export_graph_subgraph_json(fp, g, g_top_k);
        fclose(fp);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (g_debug)
            fprintf(stderr, "[TIME] export_graph_subgraph_json: %.3f s  → %s\n",
                (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9,
                g_graph_subjson);
    }

    if (g_graph_dot) {
        clock_gettime(CLOCK_MONOTONIC, &t0);
        fp = fopen(g_graph_dot, "w");
        if (!fp) {
            perror("fopen graph-dot");
            return -1;
        }
        export_graph_subgraph_dot(fp, g, g_top_k);
        fclose(fp);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (g_debug)
            fprintf(stderr, "[TIME] export_graph_subgraph_dot: %.3f s  → %s\n",
                (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9,
                g_graph_dot);
    }

    return 0;
}

static int run_session_compare(void)
{
    session_compare_report_t report;
    char out_path[512];

    memset(&report, 0, sizeof(report));
    if (!g_compare_dir) {
        fprintf(stderr, "Missing --compare-dir\n");
        return -1;
    }

    if (session_compare_scan_directory(g_compare_dir, &report) != 0) {
        fprintf(stderr, "No result.json found under %s\n", g_compare_dir);
        return -1;
    }

    if (g_compare_out)
        snprintf(out_path, sizeof(out_path), "%s", g_compare_out);
    else
        snprintf(out_path, sizeof(out_path), "%s/compare_report.json", g_compare_dir);

    if (session_compare_write_json(out_path, &report) != 0) {
        fprintf(stderr, "Failed to write %s\n", out_path);
        return -1;
    }

    fprintf(stderr, "[OK] compared %d sessions → %s\n",
            report.session_count, out_path);
    return 0;
}

static void resolve_thermal_data_path(void)
{
    if (g_thermal_data)
        return;
    if (!g_frame_data)
        return;
    thermal_profile_auto_path(g_frame_data, g_thermal_auto, sizeof(g_thermal_auto));
    g_thermal_data = g_thermal_auto;
}

/* ── Run graph-based analysis ───────────────────────────────────────── */
static int run_graph_analysis(struct frame_window *frames, int num_frames)
{
    critical_path_graph_t *g;
    frame_classification_t *classifications = NULL;
    inference_report_t *inference = NULL;
    thermal_profile_t thermal;
    int class_count = 0;
    heuristics_comparison_t comparison;
    memset(&comparison, 0, sizeof(comparison));
    memset(&thermal, 0, sizeof(thermal));
    FILE *out;
    struct timespec t0, t1;
    int64_t clock_offset = 0;

    clock_gettime(CLOCK_MONOTONIC, &t0);
    g = build_critical_path_graph(
        g_events, g_events_cnt,
        g_sys_events, g_sys_events_cnt,
        g_enh_events, g_enh_events_cnt,
        frames, num_frames);
    if (!g) {
        fprintf(stderr, "Failed to build critical path graph\n");
        return -1;
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double t_build = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;

    if (g_debug)
        fprintf(stderr, "[TIME] build_critical_path_graph: %.3f s  graph: %u nodes, %llu edges\n",
            t_build, g->node_count, (unsigned long long)g->total_edges);

    identity_apply_packages_to_graph(g);

    resolve_thermal_data_path();
    if (g_thermal_data && thermal_profile_load(g_thermal_data, &thermal) == 0) {
        if (g_events_cnt > 0 && num_frames > 0) {
            uint64_t min_ebpf = g_events[0].timestamp_ns;
            uint64_t min_frame = frames[0].expected_start_ns;
            clock_offset = (int64_t)(min_frame - min_ebpf);
        }
        thermal_profile_apply_to_graph(&thermal, g, frames, num_frames, clock_offset);
        if (g_debug)
            fprintf(stderr, "[DBG] thermal profile: %zu samples peak=%d mc throttle=%.2f\n",
                    thermal.count, thermal.peak_temp_mc, thermal.throttle_score);
    } else if (g_debug && g_thermal_data) {
        fprintf(stderr, "[DBG] no thermal profile at %s\n", g_thermal_data);
    }

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
        clock_gettime(CLOCK_MONOTONIC, &t0);
        compute_critical_scores(g, w.a, w.b, w.c, w.d, w.e, w.f, w.g);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        fprintf(stderr, "[TIME] compute_critical_scores: %.3f s\n",
            (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9);
    }

    clock_gettime(CLOCK_MONOTONIC, &t0);
    classify_jank_causes(g, frames, num_frames, &classifications, &class_count);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    fprintf(stderr, "[TIME] classify_jank_causes: %.3f s  %d classes\n",
        (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9, class_count);

    clock_gettime(CLOCK_MONOTONIC, &t0);
    comparison = compare_heuristics(g, frames, num_frames, g_top_k);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    fprintf(stderr, "[TIME] compare_heuristics: %.3f s  overlap=%d\n",
        (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9, comparison.overlap_count);

    inference = inference_build(g, classifications, class_count,
                                thermal.count > 0 ? &thermal : NULL, g_scenario);

    resolve_graph_export_paths();
    if (export_graph_files(g) != 0) {
        graph_destroy(g);
        free(classifications);
        return -1;
    }

    /* Output */
    if (g_output) {
        out = fopen(g_output, "w");
        if (!out) { perror("fopen output"); graph_destroy(g); free(classifications); return -1; }
    } else {
        out = stdout;
    }

    hint_list_t hints;
    memset(&hints, 0, sizeof(hints));

    clock_gettime(CLOCK_MONOTONIC, &t0);
    output_enhanced_topk(out, g, g_top_k, classifications, class_count, &comparison,
                         &hints, inference, thermal.count > 0 ? &thermal : NULL);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    fprintf(stderr, "[TIME] output_enhanced_topk: %.3f s\n",
        (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9);

    resolve_graph_export_paths();
    if (g_hints_json) {
        hint_engine_write_json(g_hints_json, &hints, identity_get_session());
        if (hints.count > 0)
            hint_engine_apply(&hints, g_hint_dry_run, g_hints_audit);
        if (g_debug)
            fprintf(stderr, "[DBG] wrote %d hints → %s (dry_run=%d)\n",
                    hints.count, g_hints_json, g_hint_dry_run);
    }

    if (g_output) fclose(out);

    inference_free(inference);
    thermal_profile_free(&thermal);
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

    if (g_compare_dir)
        return run_session_compare() != 0 ? 1 : 0;

    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);

    /* ── Offline mode: load events from file ── */
    if (g_events_in) {
        if (load_events(g_events_in) != 0) return 1;
        setup_identity_from_events();
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
