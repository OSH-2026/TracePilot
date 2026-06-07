/* SPDX-License-Identifier: BSD-2-Clause */
/*
 * Safe Hint Engine — generate temporary scheduling hints with TTL + rollback.
 * Offline mode uses dry-run actuator (audit log only).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "hint_engine.h"
#include "frame_aggregator.h"

static const char *hint_type_name(hint_type_t t)
{
    switch (t) {
    case HINT_BOOST_THREAD:          return "BOOST_THREAD";
    case HINT_UCLAMP_MIN_TEMPORARY:  return "UCLAMP_MIN_TEMPORARY";
    case HINT_PROTECT_UI_CHAIN:      return "PROTECT_UI_CHAIN";
    default:                         return "UNKNOWN";
    }
}

static void add_hint(hint_list_t *list, hint_type_t type,
                     int32_t tid, int32_t pid, const char *comm, const char *pkg,
                     double score, uint64_t rd_p95, const char *reason,
                     const char *rollback)
{
    tracepilot_hint_t *h;
    struct timespec ts;

    if (!list || list->count >= HINT_MAX)
        return;

    h = &list->hints[list->count++];
    memset(h, 0, sizeof(*h));

    clock_gettime(CLOCK_REALTIME, &ts);
    snprintf(h->hint_id, sizeof(h->hint_id), "hint-%d-%llu",
             list->count, (unsigned long long)ts.tv_sec);

    h->type = type;
    snprintf(h->type_name, sizeof(h->type_name), "%s", hint_type_name(type));
    h->target_tid = tid;
    h->target_pid = pid;
    h->created_at_ns = (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
    h->ttl_ms = HINT_DEFAULT_TTL_MS;
    h->critical_score = score;
    h->runnable_delay_p95_ns = rd_p95;

    if (comm)
        snprintf(h->target_comm, sizeof(h->target_comm), "%s", comm);
    if (pkg)
        snprintf(h->package, sizeof(h->package), "%s", pkg);
    if (reason)
        snprintf(h->reason, sizeof(h->reason), "%s", reason);
    if (rollback)
        snprintf(h->rollback, sizeof(h->rollback), "%s", rollback);
}

static int is_render_or_ui(const char *comm)
{
    if (!comm)
        return 0;
    return strstr(comm, "RenderThread") != NULL ||
           strstr(comm, "surfaceflinger") != NULL ||
           strstr(comm, ".ui") != NULL ||
           strstr(comm, "UIThread") != NULL;
}

hint_list_t hint_engine_generate(critical_path_graph_t *g,
                                 thread_ranking_t *ranking, int rank_count,
                                 int top_k, const app_session_t *session)
{
    hint_list_t list;
    int limit;
    int has_ui_chain = 0;

    memset(&list, 0, sizeof(list));
    if (!g || !ranking || rank_count <= 0)
        return list;

    limit = top_k < rank_count ? top_k : rank_count;
    if (limit > HINT_MAX)
        limit = HINT_MAX;

    for (int i = 0; i < limit && list.count < HINT_MAX; i++) {
        thread_ranking_t *r = &ranking[i];
        const char *pkg = r->pkg[0] ? r->pkg :
            (session ? session->package : "");

        if (r->runnable_delay_p95_ns >= 5000000 &&
            (r->render_path_proximity > 0.3 || is_render_or_ui(r->comm))) {
            add_hint(&list, HINT_UCLAMP_MIN_TEMPORARY,
                     r->tid, r->pid, r->comm, pkg,
                     r->critical_score, r->runnable_delay_p95_ns,
                     "High runnable delay on render/UI path during jank windows",
                     "{\"action\":\"restore_uclamp_min\",\"value\":0}");
        } else if (r->critical_score > 0.5 && is_render_or_ui(r->comm)) {
            add_hint(&list, HINT_BOOST_THREAD,
                     r->tid, r->pid, r->comm, pkg,
                     r->critical_score, r->runnable_delay_p95_ns,
                     "Top critical render/UI thread in jank window overlap",
                     "{\"action\":\"restore_nice\",\"value\":0}");
        }
    }

    for (int i = 0; i < limit && !has_ui_chain; i++) {
        if (is_render_or_ui(ranking[i].comm))
            has_ui_chain = 1;
    }

    if (has_ui_chain && list.count < HINT_MAX) {
        for (uint32_t ni = 0; ni < g->node_count && list.count < HINT_MAX; ni++) {
            graph_node_t *n = &g->nodes[ni];
            if (n->type != GRAPH_NODE_SURFACEFLINGER && n->type != GRAPH_NODE_RENDER_THREAD)
                continue;
            if (n->frame_window_overlap < 0.2)
                continue;

            add_hint(&list, HINT_PROTECT_UI_CHAIN,
                     n->tid, n->pid, n->comm,
                     n->pkg[0] ? n->pkg : (session ? session->package : ""),
                     n->critical_score, n->runnable_delay_p95_ns,
                     "Protect UI compositor chain during interaction jank burst",
                     "{\"action\":\"restore_affinity\",\"mask\":\"all\"}");
            break;
        }
    }

    return list;
}

int hint_engine_write_json(const char *path, const hint_list_t *hints,
                           const app_session_t *session)
{
    FILE *fp;
    const app_session_t *sess = session ? session : identity_get_session();

    fp = fopen(path, "w");
    if (!fp)
        return -1;

    fprintf(fp, "{\n");
    fprintf(fp, "  \"session_id\": \"%s\",\n", sess->session_id);
    fprintf(fp, "  \"package\": \"%s\",\n", sess->package);
    fprintf(fp, "  \"hint_count\": %d,\n", hints ? hints->count : 0);
    fprintf(fp, "  \"default_ttl_ms\": %u,\n", HINT_DEFAULT_TTL_MS);
    fprintf(fp, "  \"hints\": [\n");

    if (hints) {
        for (int i = 0; i < hints->count; i++) {
            const tracepilot_hint_t *h = &hints->hints[i];
            fprintf(fp, "    {\n");
            fprintf(fp, "      \"hint_id\": \"%s\",\n", h->hint_id);
            fprintf(fp, "      \"type\": \"%s\",\n", h->type_name);
            fprintf(fp, "      \"target\": {\n");
            fprintf(fp, "        \"tid\": %d,\n", h->target_tid);
            fprintf(fp, "        \"pid\": %d,\n", h->target_pid);
            fprintf(fp, "        \"comm\": \"%s\",\n", h->target_comm);
            fprintf(fp, "        \"package\": \"%s\"\n", h->package);
            fprintf(fp, "      },\n");
            fprintf(fp, "      \"ttl_ms\": %u,\n", h->ttl_ms);
            fprintf(fp, "      \"rollback\": %s,\n", h->rollback);
            fprintf(fp, "      \"reason\": \"%s\",\n", h->reason);
            fprintf(fp, "      \"critical_score\": %.4f,\n", h->critical_score);
            fprintf(fp, "      \"runnable_delay_p95_ns\": %llu,\n",
                    (unsigned long long)h->runnable_delay_p95_ns);
            fprintf(fp, "      \"created_at_ns\": %llu\n",
                    (unsigned long long)h->created_at_ns);
            fprintf(fp, "    }%s\n", (i + 1 < hints->count) ? "," : "");
        }
    }

    fprintf(fp, "  ]\n");
    fprintf(fp, "}\n");
    fclose(fp);
    return 0;
}

int hint_engine_write_audit(const char *path, const hint_list_t *hints,
                            const char *action)
{
    FILE *fp;
    struct timespec ts;

    fp = fopen(path, "a");
    if (!fp)
        return -1;

    clock_gettime(CLOCK_REALTIME, &ts);
    fprintf(fp, "[%lld.%09ld] action=%s count=%d\n",
            (long long)ts.tv_sec, ts.tv_nsec,
            action ? action : "unknown", hints ? hints->count : 0);

    if (hints) {
        for (int i = 0; i < hints->count; i++) {
            const tracepilot_hint_t *h = &hints->hints[i];
            fprintf(fp, "  %s type=%s tid=%d ttl_ms=%u applied=%d rolled_back=%d reason=%s\n",
                    h->hint_id, h->type_name, h->target_tid, h->ttl_ms,
                    h->applied, h->rolled_back, h->reason);
        }
    }
    fprintf(fp, "\n");
    fclose(fp);
    return 0;
}

static int apply_uclamp_hint(const tracepilot_hint_t *h, int dry_run)
{
    char path[128];
    FILE *fp;

    snprintf(path, sizeof(path),
             "/proc/%d/task/%d/uclamp.min",
             h->target_pid, h->target_tid);

    if (dry_run)
        return 0;

    fp = fopen(path, "w");
    if (!fp)
        return -1;
    fprintf(fp, "512\n");
    fclose(fp);
    return 0;
}

static int apply_boost_hint(const tracepilot_hint_t *h, int dry_run)
{
    char path[128];
    FILE *fp;

    snprintf(path, sizeof(path),
             "/proc/%d/task/%d/sched_boost",
             h->target_pid, h->target_tid);

    if (dry_run)
        return 0;

    fp = fopen(path, "w");
    if (!fp)
        return -1;
    fprintf(fp, "1\n");
    fclose(fp);
    return 0;
}

int hint_engine_apply(hint_list_t *hints, int dry_run, const char *audit_path)
{
    if (!hints)
        return -1;

    for (int i = 0; i < hints->count; i++) {
        tracepilot_hint_t *h = &hints->hints[i];
        int rc = 0;

        switch (h->type) {
        case HINT_UCLAMP_MIN_TEMPORARY:
            rc = apply_uclamp_hint(h, dry_run);
            break;
        case HINT_BOOST_THREAD:
        case HINT_PROTECT_UI_CHAIN:
            rc = apply_boost_hint(h, dry_run);
            break;
        default:
            rc = -1;
            break;
        }

        if (rc == 0)
            h->applied = 1;
    }

    hint_engine_write_audit(audit_path ? audit_path : "hints_audit.log", hints,
                            dry_run ? "apply_dry_run" : "apply");
    return 0;
}

int hint_engine_rollback(hint_list_t *hints, int dry_run, const char *audit_path)
{
    if (!hints)
        return -1;

    for (int i = 0; i < hints->count; i++) {
        tracepilot_hint_t *h = &hints->hints[i];
        if (!h->applied)
            continue;

        switch (h->type) {
        case HINT_UCLAMP_MIN_TEMPORARY:
            if (!dry_run) {
                char path[128];
                FILE *fp;
                snprintf(path, sizeof(path),
                         "/proc/%d/task/%d/uclamp.min",
                         h->target_pid, h->target_tid);
                fp = fopen(path, "w");
                if (fp) {
                    fprintf(fp, "0\n");
                    fclose(fp);
                }
            }
            break;
        case HINT_BOOST_THREAD:
        case HINT_PROTECT_UI_CHAIN:
            if (!dry_run) {
                char path[128];
                FILE *fp;
                snprintf(path, sizeof(path),
                         "/proc/%d/task/%d/sched_boost",
                         h->target_pid, h->target_tid);
                fp = fopen(path, "w");
                if (fp) {
                    fprintf(fp, "0\n");
                    fclose(fp);
                }
            }
            break;
        default:
            break;
        }
        h->rolled_back = 1;
    }

    hint_engine_write_audit(audit_path ? audit_path : "hints_audit.log", hints,
                            dry_run ? "rollback_dry_run" : "rollback");
    return 0;
}

void hint_engine_print_json_section(FILE *out, const hint_list_t *hints)
{
    if (!out || !hints)
        return;

    fprintf(out, "  \"hints\": {\n");
    fprintf(out, "    \"count\": %d,\n", hints->count);
    fprintf(out, "    \"default_ttl_ms\": %u,\n", HINT_DEFAULT_TTL_MS);
    fprintf(out, "    \"items\": [\n");

    for (int i = 0; i < hints->count; i++) {
        const tracepilot_hint_t *h = &hints->hints[i];
        fprintf(out, "      {\n");
        fprintf(out, "        \"hint_id\": \"%s\",\n", h->hint_id);
        fprintf(out, "        \"type\": \"%s\",\n", h->type_name);
        fprintf(out, "        \"tid\": %d,\n", h->target_tid);
        fprintf(out, "        \"pid\": %d,\n", h->target_pid);
        fprintf(out, "        \"comm\": \"%s\",\n", h->target_comm);
        fprintf(out, "        \"package\": \"%s\",\n", h->package);
        fprintf(out, "        \"ttl_ms\": %u,\n", h->ttl_ms);
        fprintf(out, "        \"rollback\": %s,\n", h->rollback);
        fprintf(out, "        \"reason\": \"%s\",\n", h->reason);
        fprintf(out, "        \"runnable_delay_p95_ns\": %llu\n",
                (unsigned long long)h->runnable_delay_p95_ns);
        fprintf(out, "      }%s\n", (i + 1 < hints->count) ? "," : "");
    }

    fprintf(out, "    ]\n");
    fprintf(out, "  },\n");
}
