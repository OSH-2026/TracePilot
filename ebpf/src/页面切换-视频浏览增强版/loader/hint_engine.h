/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef __HINT_ENGINE_H__
#define __HINT_ENGINE_H__

#include <stdint.h>
#include <stdio.h>

#include "identity.h"
#include "frame_aggregator.h"

#define HINT_REASON_LEN 512
#define HINT_ROLLBACK_LEN 256
#define HINT_ID_LEN 64
#define HINT_TYPE_NAME_LEN 32
#define HINT_MAX 32

typedef enum {
    HINT_BOOST_THREAD = 0,
    HINT_UCLAMP_MIN_TEMPORARY = 1,
    HINT_PROTECT_UI_CHAIN = 2,
} hint_type_t;

typedef struct {
    char hint_id[HINT_ID_LEN];
    hint_type_t type;
    char type_name[HINT_TYPE_NAME_LEN];
    int32_t target_tid;
    int32_t target_pid;
    char target_comm[16];
    char package[128];
    uint32_t ttl_ms;
    char rollback[HINT_ROLLBACK_LEN];
    char reason[HINT_REASON_LEN];
    uint64_t created_at_ns;
    double critical_score;
    uint64_t runnable_delay_p95_ns;
    int applied;
    int rolled_back;
} tracepilot_hint_t;

typedef struct {
    tracepilot_hint_t hints[HINT_MAX];
    int count;
} hint_list_t;

#define HINT_DEFAULT_TTL_MS 300

hint_list_t hint_engine_generate(critical_path_graph_t *g,
                                 thread_ranking_t *ranking, int rank_count,
                                 int top_k, const app_session_t *session);

int hint_engine_write_json(const char *path, const hint_list_t *hints,
                           const app_session_t *session);
int hint_engine_write_audit(const char *path, const hint_list_t *hints,
                            const char *action);
int hint_engine_apply(hint_list_t *hints, int dry_run, const char *audit_path);
int hint_engine_rollback(hint_list_t *hints, int dry_run, const char *audit_path);
void hint_engine_print_json_section(FILE *out, const hint_list_t *hints);

#endif /* __HINT_ENGINE_H__ */
