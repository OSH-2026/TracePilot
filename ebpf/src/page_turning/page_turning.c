// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <sys/resource.h>
#include "bpf/libbpf.h"
#include "page_turning.skel.h"

static volatile sig_atomic_t exiting = 0;

struct event {
    __u64 ts_ns;
    __u32 pid;
    __u32 tgid;
    __u32 uid;
    __u32 cpu;
    __u32 type;
    char comm[16];
};

#define MAX_UID_MAP 8192
struct uid_pkg {
    __u32 uid;
    char pkg[128];
};

static struct uid_pkg g_uid_map[MAX_UID_MAP];
static int g_uid_cnt = 0;

static char g_last_pkg[128];
static __u64 g_last_ts_ns = 0;

/* 1=打印调试日志，0=只打印结果 */
static int g_debug = 1;

static void sig_int(int signo)
{
    (void)signo;
    exiting = 1;
}

static int is_noise_comm(const char *comm)
{
    static const char *noise[] = {
        "sh", "ip", "logcat", "timeout", "cat", "grep", "toybox",
        "getprop", "du", "wm", "cmd", "ping", "ps"
    };
    for (size_t i = 0; i < sizeof(noise) / sizeof(noise[0]); i++) {
        if (strcmp(comm, noise[i]) == 0)
            return 1;
    }
    return 0;
}

/* 放宽版包名判断：有点号且不是路径 */
static int looks_like_pkg_relaxed(const char *pkg)
{
    if (!pkg || pkg[0] == '\0')
        return 0;
    if (strchr(pkg, '/') != NULL) /* /system/bin/sh 这类路径 */
        return 0;
    return strchr(pkg, '.') != NULL;
}

static void ns_to_time(__u64 ns, char *out, size_t n)
{
    (void)ns;
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    snprintf(out, n, "%ld.%03ld", ts.tv_sec, ts.tv_nsec / 1000000);
}

static void load_uid_pkg_map(void)
{
    FILE *fp = popen("pm list packages -U 2>/dev/null", "r");
    if (!fp)
        return;

    char line[512];
    while (fgets(line, sizeof(line), fp)) {
        /* 常见格式：package:com.xxx uid:10123 */
        char pkg[128] = {0};
        unsigned uid = 0;

        char *p_pkg = strstr(line, "package:");
        char *p_uid = strstr(line, "uid:");
        if (!p_pkg || !p_uid)
            continue;

        p_pkg += strlen("package:");
        if (sscanf(p_pkg, "%127s", pkg) != 1)
            continue;
        if (sscanf(p_uid, "uid:%u", &uid) != 1)
            continue;

        if (g_uid_cnt < MAX_UID_MAP) {
            g_uid_map[g_uid_cnt].uid = uid;
            strncpy(g_uid_map[g_uid_cnt].pkg, pkg, sizeof(g_uid_map[g_uid_cnt].pkg) - 1);
            g_uid_map[g_uid_cnt].pkg[sizeof(g_uid_map[g_uid_cnt].pkg) - 1] = '\0';
            g_uid_cnt++;
        }
    }
    pclose(fp);

    if (g_debug)
        fprintf(stderr, "[DBG] uid->pkg map loaded: %d entries\n", g_uid_cnt);
}

static const char *uid_to_pkg(__u32 uid)
{
    for (int i = 0; i < g_uid_cnt; i++) {
        if (g_uid_map[i].uid == uid)
            return g_uid_map[i].pkg;
    }
    return NULL;
}

static int pid_to_cmdline(__u32 pid, char *buf, size_t n)
{
    char path[64];
    snprintf(path, sizeof(path), "/proc/%u/cmdline", pid);

    FILE *fp = fopen(path, "rb");
    if (!fp)
        return -1;

    size_t r = fread(buf, 1, n - 1, fp);
    fclose(fp);
    if (r == 0)
        return -1;

    buf[r] = '\0';
    return 0;
}

static int handle_event(void *ctx, void *data, size_t data_sz)
{
    (void)ctx;
    (void)data_sz;

    const struct event *e = data;
    char ts[64], cmdline[256] = {0};
    const char *pkg = NULL;

    /* 只看应用 uid */
    if (e->uid < 10000)
        return 0;

    ns_to_time(e->ts_ns, ts, sizeof(ts));

    /*
     * 关键逻辑：
     * 1) 先读 cmdline
     * 2) cmdline 不是包名（sh/ip 等）就回退 uid->pkg
     * 3) 不再用 comm 兜底，避免噪声刷屏
     */
    if (pid_to_cmdline(e->tgid, cmdline, sizeof(cmdline)) == 0 && cmdline[0] != '\0') {
        if (looks_like_pkg_relaxed(cmdline)) {
            pkg = cmdline;
        } else {
            pkg = uid_to_pkg(e->uid);
            if (g_debug && is_noise_comm(e->comm)) {
                fprintf(stderr,
                        "[DBG] fallback uid->pkg: uid=%u comm=%s tgid=%u cmdline=%s mapped=%s\n",
                        e->uid, e->comm, e->tgid, cmdline, pkg ? pkg : "(null)");
            }
        }
    } else {
        pkg = uid_to_pkg(e->uid);
        if (g_debug && is_noise_comm(e->comm)) {
            fprintf(stderr,
                    "[DBG] cmdline miss, uid->pkg: uid=%u comm=%s tgid=%u mapped=%s\n",
                    e->uid, e->comm, e->tgid, pkg ? pkg : "(null)");
        }
    }

    if (!pkg)
        return 0;

    if (!looks_like_pkg_relaxed(pkg))
        return 0;

    /* 去重：同包 300ms 内重复不打印 */
    if (strcmp(pkg, g_last_pkg) == 0 &&
        (e->ts_ns - g_last_ts_ns) < 300000000ULL) {
        return 0;
    }

    strncpy(g_last_pkg, pkg, sizeof(g_last_pkg) - 1);
    g_last_pkg[sizeof(g_last_pkg) - 1] = '\0';
    g_last_ts_ns = e->ts_ns;

    printf("%s APP_START pkg=%s pid=%u tgid=%u uid=%u comm=%s cpu=%u\n",
           ts, pkg, e->pid, e->tgid, e->uid, e->comm, e->cpu);
    fflush(stdout);
    return 0;
}

int main(void)
{
    struct ring_buffer *rb = NULL;
    struct page_turning_bpf *skel;
    int err;

    struct rlimit r = {RLIM_INFINITY, RLIM_INFINITY};
    setrlimit(RLIMIT_MEMLOCK, &r);

    signal(SIGINT, sig_int);
    signal(SIGTERM, sig_int);

    load_uid_pkg_map();

    skel = page_turning_bpf__open_and_load();
    if (!skel) {
        fprintf(stderr, "open/load failed\n");
        return 1;
    }

    err = page_turning_bpf__attach(skel);
    if (err) {
        fprintf(stderr, "attach failed: %d\n", err);
        page_turning_bpf__destroy(skel);
        return 1;
    }

    rb = ring_buffer__new(bpf_map__fd(skel->maps.events), handle_event, NULL, NULL);
    if (!rb) {
        fprintf(stderr, "ring_buffer__new failed\n");
        page_turning_bpf__destroy(skel);
        return 1;
    }

    if (g_debug)
        fprintf(stderr, "[DBG] started. waiting for events...\n");

    while (!exiting) {
        err = ring_buffer__poll(rb, 200);
        if (err < 0 && errno != EINTR) {
            fprintf(stderr, "poll failed: %d\n", err);
            break;
        }
    }

    ring_buffer__free(rb);
    page_turning_bpf__destroy(skel);
    return 0;
}