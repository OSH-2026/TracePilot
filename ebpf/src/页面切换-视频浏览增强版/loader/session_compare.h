/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef __SESSION_COMPARE_H__
#define __SESSION_COMPARE_H__

#include <stddef.h>
#include <stdint.h>

#define SESSION_COMPARE_MAX 16
#define SESSION_LABEL_LEN     128
#define SESSION_PATH_LEN      512

typedef struct {
    char     label[SESSION_LABEL_LEN];
    char     path[SESSION_PATH_LEN];
    char     scenario[32];
    char     package[128];
    char     session_id[64];
    uint64_t total_frames;
    uint64_t jank_frames;
    double   jank_ratio;
    char     primary_cause[32];
    int      top_tid;
    char     top_comm[16];
    double   top_critical_score;
    double   graph_precision_at_k;
} session_snapshot_t;

typedef struct {
    session_snapshot_t sessions[SESSION_COMPARE_MAX];
    int                session_count;
    double             overlap_matrix[SESSION_COMPARE_MAX][SESSION_COMPARE_MAX];
} session_compare_report_t;

int session_compare_load_snapshot(const char *path, session_snapshot_t *snap);
int session_compare_build(const char **paths, int path_count,
    session_compare_report_t *report);
int session_compare_scan_directory(const char *output_dir,
    session_compare_report_t *report);
int session_compare_write_json(const char *out_path,
    const session_compare_report_t *report);

#endif /* __SESSION_COMPARE_H__ */
