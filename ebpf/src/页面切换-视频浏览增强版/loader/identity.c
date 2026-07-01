/* SPDX-License-Identifier: BSD-2-Clause */
/*
 * identity.c — Thread identity resolver
 * Maps TID to Session / ProcessInstanceId / ThreadKey using
 * /proc filesystem and Perfetto static lineage data.
 */
/*
 * Identity layer: App Session, ProcessInstanceId, ThreadKey, FrameKey.
 * Supports offline sidecar JSON beside events.bin.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "identity.h"
#include "resolver.h"
#include "frame_aggregator.h"
#include "../bpf/tracepilot.bpf.h"

#define MAX_PROCESSES 4096
#define MAX_THREADS   16384

static app_session_t g_session;
static int         g_session_ready = 0;

static process_instance_t g_processes[MAX_PROCESSES];
static int                  g_process_count = 0;

static thread_identity_t g_threads[MAX_THREADS];
static int               g_thread_count = 0;

static char g_cached_boot_id[IDENTITY_BOOT_ID_LEN];

int identity_read_boot_id(char *out, size_t out_len)
{
    FILE *fp;

    if (!out || out_len == 0)
        return -1;

    if (g_cached_boot_id[0]) {
        strncpy(out, g_cached_boot_id, out_len - 1);
        out[out_len - 1] = '\0';
        return 0;
    }

    fp = fopen("/proc/sys/kernel/random/boot_id", "r");
    if (!fp) {
        snprintf(out, out_len, "offline-%llu",
                 (unsigned long long)time(NULL));
        return -1;
    }

    if (!fgets(out, (int)out_len, fp)) {
        fclose(fp);
        snprintf(out, out_len, "offline-%llu",
                 (unsigned long long)time(NULL));
        return -1;
    }
    fclose(fp);

    out[strcspn(out, "\r\n")] = '\0';
    strncpy(g_cached_boot_id, out, sizeof(g_cached_boot_id) - 1);
    g_cached_boot_id[sizeof(g_cached_boot_id) - 1] = '\0';
    return 0;
}

uint64_t identity_read_process_start_time(pid_t pid)
{
    char path[64];
    char line[512];
    FILE *fp;
    unsigned long long starttime = 0;

    snprintf(path, sizeof(path), "/proc/%d/stat", pid);
    fp = fopen(path, "r");
    if (!fp)
        return 0;

    if (fgets(line, sizeof(line), fp)) {
        char *rp = strrchr(line, ')');
        if (rp) {
            int dummy;
            if (sscanf(rp + 2,
                       "%d %d %d %d %d %d %d %llu",
                       &dummy, &dummy, &dummy, &dummy, &dummy, &dummy, &dummy,
                       &starttime) >= 8) {
                fclose(fp);
                return starttime;
            }
        }
    }
    fclose(fp);
    return 0;
}

void identity_init_session(const char *target_package, uint64_t collection_start_ns)
{
    char boot_id[IDENTITY_BOOT_ID_LEN];

    memset(&g_session, 0, sizeof(g_session));
    identity_read_boot_id(boot_id, sizeof(boot_id));

    if (target_package && target_package[0])
        strncpy(g_session.package, target_package, sizeof(g_session.package) - 1);
    else
        strncpy(g_session.package, "unknown", sizeof(g_session.package) - 1);

    g_session.collection_start_ns = collection_start_ns;
    if (g_session.collection_start_ns == 0)
        g_session.collection_start_ns = (uint64_t)time(NULL) * 1000000000ULL;

    snprintf(g_session.session_id, sizeof(g_session.session_id),
             "%s-%llu", boot_id,
             (unsigned long long)(g_session.collection_start_ns / 1000000000ULL));

    g_session_ready = 1;
}

const app_session_t *identity_get_session(void)
{
    if (!g_session_ready)
        identity_init_session(NULL, 0);
    return &g_session;
}

static process_instance_t *find_process(pid_t pid)
{
    for (int i = 0; i < g_process_count; i++) {
        if (g_processes[i].pid == pid)
            return &g_processes[i];
    }
    return NULL;
}

static void format_process_instance_id(process_instance_t *p)
{
    snprintf(p->instance_id, sizeof(p->instance_id),
             "%s:%d:%llu:%u:%s",
             p->boot_id, (int)p->pid,
             (unsigned long long)p->start_time_ns,
             (unsigned)p->uid, p->process_name);
}

process_instance_t *identity_get_or_create_process(pid_t pid)
{
    char pkg[IDENTITY_PKG_LEN];
    char boot_id[IDENTITY_BOOT_ID_LEN];
    process_instance_t *p;

    if (pid <= 0)
        return NULL;

    p = find_process(pid);
    if (p)
        return p;

    if (g_process_count >= MAX_PROCESSES)
        return NULL;

    p = &g_processes[g_process_count++];
    memset(p, 0, sizeof(*p));
    p->pid = pid;
    identity_read_boot_id(boot_id, sizeof(boot_id));
    strncpy(p->boot_id, boot_id, sizeof(p->boot_id) - 1);
    p->start_time_ns = identity_read_process_start_time(pid);

    if (resolve_pid(pid, pkg, sizeof(pkg)) == 0) {
        strncpy(p->package, pkg, sizeof(p->package) - 1);
        strncpy(p->process_name, pkg, sizeof(p->process_name) - 1);
    } else {
        snprintf(p->process_name, sizeof(p->process_name), "pid_%d", (int)pid);
        if (g_session.package[0] && strcmp(g_session.package, "unknown") != 0)
            strncpy(p->package, g_session.package, sizeof(p->package) - 1);
    }

    if (get_uid_from_pid(pid, &p->uid) != 0)
        p->uid = 0;

    format_process_instance_id(p);
    return p;
}

static thread_identity_t *find_thread(pid_t tid)
{
    for (int i = 0; i < g_thread_count; i++) {
        if (g_threads[i].tid == tid)
            return &g_threads[i];
    }
    return NULL;
}

thread_identity_t *identity_get_or_create_thread(pid_t tid, pid_t pid, const char *comm)
{
    char boot_id[IDENTITY_BOOT_ID_LEN];
    process_instance_t *proc;
    thread_identity_t *t;

    if (tid <= 0)
        return NULL;

    t = find_thread(tid);
    if (t)
        return t;

    if (g_thread_count >= MAX_THREADS)
        return NULL;

    proc = identity_get_or_create_process(pid > 0 ? pid : tid);
    t = &g_threads[g_thread_count++];
    memset(t, 0, sizeof(*t));
    t->tid = tid;
    t->pid = pid > 0 ? pid : tid;
    identity_read_boot_id(boot_id, sizeof(boot_id));
    strncpy(t->boot_id, boot_id, sizeof(t->boot_id) - 1);
    t->start_time_ns = proc ? proc->start_time_ns : 0;
    if (comm)
        strncpy(t->comm, comm, sizeof(t->comm) - 1);

    snprintf(t->thread_key, sizeof(t->thread_key),
             "%s:%d:%llu:%d",
             t->boot_id, (int)t->tid,
             (unsigned long long)t->start_time_ns, (int)t->pid);
    return t;
}

frame_key_t identity_build_frame_key(const struct frame_window *fw,
                                     const process_instance_t *proc)
{
    frame_key_t fk;
    const app_session_t *sess = identity_get_session();

    memset(&fk, 0, sizeof(fk));
    if (proc) {
        strncpy(fk.package, proc->package, sizeof(fk.package) - 1);
        fk.uid = proc->uid;
        strncpy(fk.process_instance_id, proc->instance_id,
                sizeof(fk.process_instance_id) - 1);
    } else if (sess) {
        strncpy(fk.package, sess->package, sizeof(fk.package) - 1);
    }

    if (fw)
        fk.frame_token = fw->frame_token;

    snprintf(fk.frame_key, sizeof(fk.frame_key),
             "%s:%u:%s:%lld",
             fk.package[0] ? fk.package : "unknown",
             (unsigned)fk.uid,
             fk.process_instance_id[0] ? fk.process_instance_id : "unknown",
             (long long)fk.frame_token);
    return fk;
}

int identity_scan_sched_events(struct sched_event *events, size_t count)
{
    if (!events)
        return 0;

    for (size_t i = 0; i < count; i++) {
        struct sched_event *ev = &events[i];
        int32_t prev_tid = ev->prev_tid > 0 ? ev->prev_tid : (int32_t)ev->prev_pid;
        int32_t next_tid = ev->next_tid > 0 ? ev->next_tid : (int32_t)ev->next_pid;

        if (prev_tid > 0)
            identity_get_or_create_thread(prev_tid, ev->prev_pid, ev->prev_comm);
        if (next_tid > 0)
            identity_get_or_create_thread(next_tid, ev->next_pid, ev->next_comm);
    }
    return g_process_count;
}

const char *identity_package_for_pid(pid_t pid)
{
    process_instance_t *p = find_process(pid);
    if (p && p->package[0])
        return p->package;
    p = identity_get_or_create_process(pid);
    return p && p->package[0] ? p->package : NULL;
}

const char *identity_package_for_tid(pid_t tid)
{
    thread_identity_t *t = find_thread(tid);
    if (!t)
        return NULL;
    return identity_package_for_pid(t->pid);
}

int identity_apply_packages_to_graph(critical_path_graph_t *g)
{
    if (!g)
        return -1;

    for (uint32_t i = 0; i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];
        const char *pkg;

        if (n->tid <= 0)
            continue;
        if (n->pkg[0])
            continue;

        pkg = identity_package_for_tid(n->tid);
        if (!pkg && n->pid > 0)
            pkg = identity_package_for_pid(n->pid);
        if (pkg)
            strncpy(n->pkg, pkg, sizeof(n->pkg) - 1);
    }
    return 0;
}

int identity_save_json(const char *path)
{
    FILE *fp;
    const app_session_t *sess = identity_get_session();

    fp = fopen(path, "w");
    if (!fp)
        return -1;

    fprintf(fp, "{\n");
    fprintf(fp, "  \"session_id\": \"%s\",\n", sess->session_id);
    fprintf(fp, "  \"package\": \"%s\",\n", sess->package);
    fprintf(fp, "  \"uid\": %u,\n", (unsigned)sess->uid);
    fprintf(fp, "  \"collection_start_ns\": %llu,\n",
            (unsigned long long)sess->collection_start_ns);
    fprintf(fp, "  \"processes\": [\n");
    for (int i = 0; i < g_process_count; i++) {
        process_instance_t *p = &g_processes[i];
        fprintf(fp, "    {\n");
        fprintf(fp, "      \"instance_id\": \"%s\",\n", p->instance_id);
        fprintf(fp, "      \"boot_id\": \"%s\",\n", p->boot_id);
        fprintf(fp, "      \"pid\": %d,\n", (int)p->pid);
        fprintf(fp, "      \"start_time_ns\": %llu,\n",
                (unsigned long long)p->start_time_ns);
        fprintf(fp, "      \"uid\": %u,\n", (unsigned)p->uid);
        fprintf(fp, "      \"process_name\": \"%s\",\n", p->process_name);
        fprintf(fp, "      \"package\": \"%s\"\n", p->package);
        fprintf(fp, "    }%s\n", (i + 1 < g_process_count) ? "," : "");
    }
    fprintf(fp, "  ],\n");
    fprintf(fp, "  \"threads\": [\n");
    for (int i = 0; i < g_thread_count; i++) {
        thread_identity_t *t = &g_threads[i];
        fprintf(fp, "    {\n");
        fprintf(fp, "      \"thread_key\": \"%s\",\n", t->thread_key);
        fprintf(fp, "      \"boot_id\": \"%s\",\n", t->boot_id);
        fprintf(fp, "      \"tid\": %d,\n", (int)t->tid);
        fprintf(fp, "      \"pid\": %d,\n", (int)t->pid);
        fprintf(fp, "      \"start_time_ns\": %llu,\n",
                (unsigned long long)t->start_time_ns);
        fprintf(fp, "      \"comm\": \"%s\"\n", t->comm);
        fprintf(fp, "    }%s\n", (i + 1 < g_thread_count) ? "," : "");
    }
    fprintf(fp, "  ]\n");
    fprintf(fp, "}\n");
    fclose(fp);
    return 0;
}

static int parse_json_string_field(const char *line, const char *key, char *out, size_t out_len)
{
    char pattern[64];
    char tmp[IDENTITY_KEY_LEN];
    const char *p;

    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    p = strstr(line, pattern);
    if (!p)
        return -1;
    p = strchr(p, ':');
    if (!p)
        return -1;
    if (sscanf(p + 1, " \"%511[^\"]\"", tmp) != 1)
        return -1;
    strncpy(out, tmp, out_len - 1);
    out[out_len - 1] = '\0';
    return 0;
}

int identity_load_json(const char *path)
{
    FILE *fp;
    char line[1024];
    process_instance_t *cur_proc = NULL;
    thread_identity_t *cur_thread = NULL;

    fp = fopen(path, "r");
    if (!fp)
        return -1;

    g_process_count = 0;
    g_thread_count = 0;
    identity_init_session(NULL, 0);

    while (fgets(line, sizeof(line), fp)) {
        if (strstr(line, "\"session_id\""))
            parse_json_string_field(line, "session_id", g_session.session_id,
                                    sizeof(g_session.session_id));
        else if (strstr(line, "\"package\"") && !strstr(line, "process_name"))
            parse_json_string_field(line, "package", g_session.package,
                                    sizeof(g_session.package));
        else if (strstr(line, "\"instance_id\"")) {
            if (g_process_count < MAX_PROCESSES) {
                cur_proc = &g_processes[g_process_count++];
                memset(cur_proc, 0, sizeof(*cur_proc));
                parse_json_string_field(line, "instance_id", cur_proc->instance_id,
                                        sizeof(cur_proc->instance_id));
            }
        } else if (strstr(line, "\"thread_key\"")) {
            if (g_thread_count < MAX_THREADS) {
                cur_thread = &g_threads[g_thread_count++];
                memset(cur_thread, 0, sizeof(*cur_thread));
                parse_json_string_field(line, "thread_key", cur_thread->thread_key,
                                        sizeof(cur_thread->thread_key));
            }
        } else if (cur_proc && strstr(line, "\"boot_id\""))
            parse_json_string_field(line, "boot_id", cur_proc->boot_id, sizeof(cur_proc->boot_id));
        else if (cur_proc && strstr(line, "\"pid\"")) {
            int pid = 0;
            sscanf(strchr(line, ':') + 1, " %d", &pid);
            cur_proc->pid = pid;
        } else if (cur_proc && strstr(line, "\"process_name\""))
            parse_json_string_field(line, "process_name", cur_proc->process_name,
                                    sizeof(cur_proc->process_name));
        else if (cur_proc && strstr(line, "\"package\""))
            parse_json_string_field(line, "package", cur_proc->package,
                                    sizeof(cur_proc->package));
        else if (cur_thread && strstr(line, "\"tid\"")) {
            int tid = 0;
            sscanf(strchr(line, ':') + 1, " %d", &tid);
            cur_thread->tid = tid;
        } else if (cur_thread && strstr(line, "\"pid\"") && cur_thread->pid == 0) {
            int pid = 0;
            sscanf(strchr(line, ':') + 1, " %d", &pid);
            cur_thread->pid = pid;
        } else if (cur_thread && strstr(line, "\"comm\""))
            parse_json_string_field(line, "comm", cur_thread->comm, sizeof(cur_thread->comm));
    }

    fclose(fp);
    g_session_ready = 1;
    return 0;
}

int identity_auto_load_beside(const char *events_path)
{
    char path[512];
    const char *slash;

    if (!events_path)
        return -1;

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

    return identity_load_json(path);
}
