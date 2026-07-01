/* SPDX-License-Identifier: BSD-2-Clause */
/*
 * session_compare.c — Multi-session comparison engine
 * Reads multiple result.json snapshots, computes jank rate,
 * root-cause distribution, Top-5 overlap matrix,
 * outputs compare_report.json.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <sys/stat.h>

#include "session_compare.h"

static int parse_json_string(const char *blob, const char *key, char *out, size_t out_len)
{
    char pattern[64];
    const char *p;
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    p = strstr(blob, pattern);
    if (!p)
        return -1;
    p = strchr(p, ':');
    if (!p)
        return -1;
    if (sscanf(p + 1, " \"%127[^\"]\"", out) != 1)
        return -1;
    return 0;
}

static unsigned long long parse_json_ull(const char *blob, const char *key)
{
    char pattern[64];
    const char *p;
    unsigned long long v = 0;
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    p = strstr(blob, pattern);
    if (!p)
        return 0;
    p = strchr(p, ':');
    if (!p)
        return 0;
    sscanf(p + 1, " %llu", &v);
    return v;
}

static double parse_json_double(const char *blob, const char *key)
{
    char pattern[64];
    const char *p;
    double v = 0.0;
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    p = strstr(blob, pattern);
    if (!p)
        return 0.0;
    p = strchr(p, ':');
    if (!p)
        return 0.0;
    sscanf(p + 1, " %lf", &v);
    return v;
}

static int parse_top_thread(const char *blob, session_snapshot_t *snap)
{
    const char *top = strstr(blob, "\"top_k_threads\"");
    const char *tid_p, *comm_p, *score_p;
    if (!top)
        return -1;

    tid_p = strstr(top, "\"tid\"");
    comm_p = strstr(top, "\"comm\"");
    score_p = strstr(top, "\"critical_score\"");
    if (!tid_p || !comm_p || !score_p)
        return -1;

    sscanf(strchr(tid_p, ':') + 1, " %d", &snap->top_tid);
    sscanf(strchr(comm_p, ':') + 1, " \"%15[^\"]\"", snap->top_comm);
    sscanf(strchr(score_p, ':') + 1, " %lf", &snap->top_critical_score);
    return 0;
}

static int parse_primary_cause(const char *blob, session_snapshot_t *snap)
{
    const char *dist = strstr(blob, "\"jank_cause_distribution\"");
    const char *p;
    int best = 0, best_cnt = 0;
    static const char *causes[] = {
        "CPU_CONTENTION", "BINDER_BLOCKING", "FUTEX_BLOCKING",
        "IO_WAIT", "MEMORY_RECLAIM", "GPU_STALL",
        "RUNNABLE_DELAY", "UNKNOWN",
        "VIDEO_LATE_RENDER", "AUDIO_SYNC_DRIFT", "THERMAL_THROTTLE"
    };

    if (!dist)
        return -1;

    for (int i = 0; i < 11; i++) {
        char pattern[64];
        int cnt = 0;
        snprintf(pattern, sizeof(pattern), "\"%s\"", causes[i]);
        p = strstr(dist, pattern);
        if (!p)
            continue;
        sscanf(strchr(p, ':') + 1, " %d", &cnt);
        if (cnt > best_cnt) {
            best_cnt = cnt;
            best = i;
        }
    }

    snprintf(snap->primary_cause, sizeof(snap->primary_cause), "%s", causes[best]);
    return 0;
}

int session_compare_load_snapshot(const char *path, session_snapshot_t *snap)
{
    FILE *fp;
    long sz;
    char *buf;

    if (!path || !snap)
        return -1;
    memset(snap, 0, sizeof(*snap));
    strncpy(snap->path, path, sizeof(snap->path) - 1);

    if (strstr(path, "page_switch"))
        snprintf(snap->label, sizeof(snap->label), "page_switch");
    else if (strstr(path, "video/") || strstr(path, "video\\"))
        snprintf(snap->label, sizeof(snap->label), "video");
    else {
        const char *slash = strrchr(path, '/');
        if (!slash) slash = strrchr(path, '\\');
        if (slash) {
            char parent[SESSION_PATH_LEN];
            size_t plen = (size_t)(slash - path);
            if (plen >= sizeof(parent)) plen = sizeof(parent) - 1;
            memcpy(parent, path, plen);
            parent[plen] = '\0';
            slash = strrchr(parent, '/');
            if (!slash) slash = strrchr(parent, '\\');
            if (slash && slash[1])
                snprintf(snap->label, sizeof(snap->label), "%s", slash + 1);
        }
    }
    if (!snap->label[0])
        snprintf(snap->label, sizeof(snap->label), "%s", path);

    fp = fopen(path, "rb");
    if (!fp)
        return -1;
    fseek(fp, 0, SEEK_END);
    sz = ftell(fp);
    rewind(fp);
    if (sz <= 0 || sz > (10 << 20)) {
        fclose(fp);
        return -1;
    }

    buf = malloc((size_t)sz + 1);
    if (!buf) {
        fclose(fp);
        return -1;
    }
    if (fread(buf, 1, (size_t)sz, fp) != (size_t)sz) {
        free(buf);
        fclose(fp);
        return -1;
    }
    buf[sz] = '\0';
    fclose(fp);

    parse_json_string(buf, "target_package", snap->package, sizeof(snap->package));
    parse_json_string(buf, "session_id", snap->session_id, sizeof(snap->session_id));
    if (parse_json_string(buf, "detected_scenario", snap->scenario, sizeof(snap->scenario)) != 0)
        parse_json_string(buf, "analysis_mode", snap->scenario, sizeof(snap->scenario));

    snap->total_frames = parse_json_ull(buf, "total_frames");
    snap->jank_frames = parse_json_ull(buf, "jank_frames");
    if (snap->total_frames > 0)
        snap->jank_ratio = (double)snap->jank_frames / (double)snap->total_frames;

    parse_primary_cause(buf, snap);
    parse_top_thread(buf, snap);

    {
        const char *hc = strstr(buf, "\"heuristics_comparison\"");
        if (hc)
            snap->graph_precision_at_k =
                parse_json_double(hc, "graph_avg_precision_at_k");
    }

    free(buf);
    return 0;
}

static int overlap_top_tid(const session_snapshot_t *a, const session_snapshot_t *b)
{
    return (a->top_tid > 0 && a->top_tid == b->top_tid) ? 1 : 0;
}

int session_compare_build(const char **paths, int path_count,
    session_compare_report_t *report)
{
    if (!report || !paths || path_count <= 0)
        return -1;
    if (path_count > SESSION_COMPARE_MAX)
        path_count = SESSION_COMPARE_MAX;

    memset(report, 0, sizeof(*report));

    for (int i = 0; i < path_count; i++) {
        if (session_compare_load_snapshot(paths[i], &report->sessions[i]) != 0)
            return -1;
        report->session_count++;
    }

    for (int i = 0; i < report->session_count; i++) {
        for (int j = 0; j < report->session_count; j++) {
            if (i == j)
                report->overlap_matrix[i][j] = 1.0;
            else
                report->overlap_matrix[i][j] =
                    overlap_top_tid(&report->sessions[i], &report->sessions[j]) ? 1.0 : 0.0;
        }
    }
    return 0;
}

int session_compare_scan_directory(const char *output_dir,
    session_compare_report_t *report)
{
    const char *paths[SESSION_COMPARE_MAX];
    char path_bufs[SESSION_COMPARE_MAX][SESSION_PATH_LEN];
    int count = 0;

    if (!output_dir || !report)
        return -1;

    {
        DIR *d = opendir(output_dir);
        struct dirent *ent;
        if (!d)
            return -1;
        while ((ent = readdir(d)) != NULL && count < SESSION_COMPARE_MAX) {
            char sub[SESSION_PATH_LEN];
            struct stat st;
            if (ent->d_name[0] == '.')
                continue;
            snprintf(sub, sizeof(sub), "%s/%s/result.json", output_dir, ent->d_name);
            if (stat(sub, &st) == 0 && S_ISREG(st.st_mode)) {
                strncpy(path_bufs[count], sub, SESSION_PATH_LEN - 1);
                path_bufs[count][SESSION_PATH_LEN - 1] = '\0';
                paths[count] = path_bufs[count];
                count++;
            }
        }
        closedir(d);
    }

    if (count == 0)
        return -1;

    return session_compare_build(paths, count, report);
}

int session_compare_write_json(const char *out_path,
    const session_compare_report_t *report)
{
    FILE *fp;

    if (!out_path || !report)
        return -1;

    fp = fopen(out_path, "w");
    if (!fp)
        return -1;

    fprintf(fp, "{\n");
    fprintf(fp, "  \"compare_mode\": \"multi_session_step3\",\n");
    fprintf(fp, "  \"session_count\": %d,\n", report->session_count);
    fprintf(fp, "  \"sessions\": [\n");

    for (int i = 0; i < report->session_count; i++) {
        const session_snapshot_t *s = &report->sessions[i];
        fprintf(fp, "    {\n");
        fprintf(fp, "      \"label\": \"%s\",\n", s->label);
        fprintf(fp, "      \"path\": \"%s\",\n", s->path);
        fprintf(fp, "      \"scenario\": \"%s\",\n", s->scenario);
        fprintf(fp, "      \"package\": \"%s\",\n", s->package);
        fprintf(fp, "      \"session_id\": \"%s\",\n", s->session_id);
        fprintf(fp, "      \"total_frames\": %llu,\n", (unsigned long long)s->total_frames);
        fprintf(fp, "      \"jank_frames\": %llu,\n", (unsigned long long)s->jank_frames);
        fprintf(fp, "      \"jank_ratio\": %.4f,\n", s->jank_ratio);
        fprintf(fp, "      \"primary_cause\": \"%s\",\n", s->primary_cause);
        fprintf(fp, "      \"top_tid\": %d,\n", s->top_tid);
        fprintf(fp, "      \"top_comm\": \"%s\",\n", s->top_comm);
        fprintf(fp, "      \"top_critical_score\": %.4f,\n", s->top_critical_score);
        fprintf(fp, "      \"graph_precision_at_k\": %.4f\n", s->graph_precision_at_k);
        fprintf(fp, "    }%s\n", (i + 1 < report->session_count) ? "," : "");
    }

    fprintf(fp, "  ],\n");
    fprintf(fp, "  \"top1_overlap_matrix\": [\n");
    for (int i = 0; i < report->session_count; i++) {
        fprintf(fp, "    [");
        for (int j = 0; j < report->session_count; j++) {
            fprintf(fp, "%.0f%s", report->overlap_matrix[i][j],
                    (j + 1 < report->session_count) ? ", " : "");
        }
        fprintf(fp, "]%s\n", (i + 1 < report->session_count) ? "," : "");
    }
    fprintf(fp, "  ]\n");
    fprintf(fp, "}\n");
    fclose(fp);
    return 0;
}
