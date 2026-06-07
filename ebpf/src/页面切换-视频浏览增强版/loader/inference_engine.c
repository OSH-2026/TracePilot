/* SPDX-License-Identifier: BSD-2-Clause */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "inference_engine.h"

static const char *cause_name(jank_cause_t c)
{
    static const char *names[] = {
        "CPU_CONTENTION", "BINDER_BLOCKING", "FUTEX_BLOCKING",
        "IO_WAIT", "MEMORY_RECLAIM", "GPU_STALL",
        "RUNNABLE_DELAY", "UNKNOWN",
        "VIDEO_LATE_RENDER", "AUDIO_SYNC_DRIFT", "THERMAL_THROTTLE"
    };
    return (c < JANK_CAUSE_COUNT) ? names[c] : "UNKNOWN";
}

static void add_evidence(frame_inference_t *fi, const char *signal,
                         double weight, const char *detail)
{
    if (!fi || fi->evidence_count >= INFERENCE_MAX_EVIDENCE)
        return;
    inference_evidence_t *e = &fi->evidence[fi->evidence_count++];
    snprintf(e->signal, sizeof(e->signal), "%s", signal ? signal : "");
    e->weight = weight;
    if (detail)
        snprintf(e->detail, sizeof(e->detail), "%s", detail);
}

static void infer_one_frame(frame_inference_t *fi,
    const frame_classification_t *fc,
    critical_path_graph_t *g,
    const thermal_profile_t *thermal,
    const char *scenario)
{
    double scores[JANK_CAUSE_COUNT];
    int best = 0, second = 0;
    double best_s = 0.0, second_s = 0.0;

    memset(fi, 0, sizeof(*fi));
    fi->frame_id = fc->frame_id;
    fi->is_jank = fc->is_jank;
    if (!fc->is_jank)
        return;

    scores[JANK_CAUSE_CPU_CONTENTION]    = fc->cpu_contention_score;
    scores[JANK_CAUSE_BINDER_BLOCKING]   = fc->binder_blocking_score;
    scores[JANK_CAUSE_FUTEX_BLOCKING]    = fc->futex_blocking_score;
    scores[JANK_CAUSE_IO_WAIT]           = fc->io_wait_score;
    scores[JANK_CAUSE_MEMORY_RECLAIM]    = fc->memory_reclaim_score;
    scores[JANK_CAUSE_GPU_STALL]         = fc->gpu_stall_score;
    scores[JANK_CAUSE_RUNNABLE_DELAY]    = fc->runnable_delay_score;
    scores[JANK_CAUSE_UNKNOWN]           = 0.0;
    scores[JANK_CAUSE_VIDEO_LATE_RENDER] = fc->video_late_render_score;
    scores[JANK_CAUSE_AUDIO_SYNC_DRIFT]  = fc->audio_sync_drift_score;
    scores[JANK_CAUSE_THERMAL_THROTTLE]  = fc->thermal_throttle_score;

    if (g && g->freq_throttle_ratio > 0.2)
        scores[JANK_CAUSE_THERMAL_THROTTLE] +=
            g->freq_throttle_ratio * 0.5;

    if (thermal && thermal->throttle_score > 0.2)
        scores[JANK_CAUSE_THERMAL_THROTTLE] +=
            thermal->throttle_score * 0.6;

    for (uint32_t i = 0; g && i < g->node_count; i++) {
        graph_node_t *n = &g->nodes[i];
        if (n->frame_window_overlap < 0.05)
            continue;
        if (n->runnable_delay_p95_ns > 5000000)
            scores[JANK_CAUSE_RUNNABLE_DELAY] +=
                log1p((double)n->runnable_delay_p95_ns / 1e6) * n->frame_window_overlap;
        if (n->binder_dependency_centrality > 0.1)
            scores[JANK_CAUSE_BINDER_BLOCKING] +=
                n->binder_dependency_centrality * n->frame_window_overlap;
        if (n->render_path_proximity > 0.5 && n->futex_wait_contribution > 0.05)
            scores[JANK_CAUSE_FUTEX_BLOCKING] +=
                n->futex_wait_contribution * 0.5;
    }

    for (int k = 0; k < JANK_CAUSE_COUNT; k++) {
        if (scores[k] > best_s) {
            second_s = best_s;
            second = best;
            best_s = scores[k];
            best = k;
        } else if (scores[k] > second_s) {
            second_s = scores[k];
            second = k;
        }
    }

    fi->confidence = best_s / (best_s + second_s + 0.001);
    snprintf(fi->hypothesis, sizeof(fi->hypothesis), "%s", cause_name((jank_cause_t)best));
    snprintf(fi->secondary, sizeof(fi->secondary), "%s", cause_name((jank_cause_t)second));

    if (scores[JANK_CAUSE_BINDER_BLOCKING] > 0.15)
        add_evidence(fi, "binder_centrality", scores[JANK_CAUSE_BINDER_BLOCKING],
                     "Binder subgraph active in jank window");
    if (scores[JANK_CAUSE_FUTEX_BLOCKING] > 0.1)
        add_evidence(fi, "futex_wait", scores[JANK_CAUSE_FUTEX_BLOCKING],
                     "Render/UI thread blocked on futex");
    if (scores[JANK_CAUSE_RUNNABLE_DELAY] > 0.1)
        add_evidence(fi, "runnable_delay", scores[JANK_CAUSE_RUNNABLE_DELAY],
                     "High wakeup-to-run delay on critical threads");
    if (scores[JANK_CAUSE_THERMAL_THROTTLE] > 0.15) {
        char buf[128];
        snprintf(buf, sizeof(buf), "freq_throttle=%.2f thermal_delta=%d mc",
                 g ? g->freq_throttle_ratio : 0.0,
                 thermal ? thermal->jank_window_delta_mc : 0);
        add_evidence(fi, "thermal_throttle", scores[JANK_CAUSE_THERMAL_THROTTLE], buf);
    }
    if (scores[JANK_CAUSE_IO_WAIT] > 0.2)
        add_evidence(fi, "system_irq", scores[JANK_CAUSE_IO_WAIT],
                     "IRQ/softirq overhead in frame window");
    if (scores[JANK_CAUSE_VIDEO_LATE_RENDER] > 0.1)
        add_evidence(fi, "decode_late", scores[JANK_CAUSE_VIDEO_LATE_RENDER],
                     "Decoder path lag vs presentation deadline");

    snprintf(fi->summary, sizeof(fi->summary),
             "%s (conf=%.2f) over %s in %s scenario",
             fi->hypothesis, fi->confidence, fi->secondary,
             scenario ? scenario : "unknown");
    (void)fc;
}

inference_report_t *inference_build(critical_path_graph_t *g,
    frame_classification_t *classifications, int class_count,
    const thermal_profile_t *thermal, const char *scenario)
{
    inference_report_t *rep;
    int jank_count = 0;
    int cause_hist[JANK_CAUSE_COUNT];
    char top_cause[32] = "UNKNOWN";

    if (!classifications || class_count <= 0)
        return NULL;

    rep = calloc(1, sizeof(*rep));
    if (!rep)
        return NULL;

    rep->frames = calloc(class_count, sizeof(*rep->frames));
    if (!rep->frames) {
        free(rep);
        return NULL;
    }
    rep->frame_count = class_count;

    memset(cause_hist, 0, sizeof(cause_hist));
    for (int i = 0; i < class_count; i++) {
        infer_one_frame(&rep->frames[i], &classifications[i], g, thermal, scenario);
        if (classifications[i].is_jank) {
            jank_count++;
            for (int k = 0; k < JANK_CAUSE_COUNT; k++) {
                if (strcmp(rep->frames[i].hypothesis, cause_name((jank_cause_t)k)) == 0)
                    cause_hist[k]++;
            }
        }
    }

    {
        int best = 0;
        for (int k = 1; k < JANK_CAUSE_COUNT; k++) {
            if (cause_hist[k] > cause_hist[best])
                best = k;
        }
        snprintf(top_cause, sizeof(top_cause), "%s", cause_name((jank_cause_t)best));
    }

    if (strcmp(top_cause, "THERMAL_THROTTLE") == 0 ||
        (thermal && thermal->throttle_score > 0.3))
        snprintf(rep->recommended_hint, sizeof(rep->recommended_hint),
                 "UCLAMP_MIN_TEMPORARY");
    else if (strcmp(top_cause, "RUNNABLE_DELAY") == 0)
        snprintf(rep->recommended_hint, sizeof(rep->recommended_hint), "BOOST_THREAD");
    else if (strcmp(top_cause, "BINDER_BLOCKING") == 0)
        snprintf(rep->recommended_hint, sizeof(rep->recommended_hint), "PROTECT_UI_CHAIN");
    else
        snprintf(rep->recommended_hint, sizeof(rep->recommended_hint), "BOOST_THREAD");

    snprintf(rep->session_summary, sizeof(rep->session_summary),
             "Inference over %d jank frames: dominant=%s, scenario=%s, thermal_score=%.2f",
             jank_count, top_cause, scenario ? scenario : "unknown",
             thermal ? thermal->throttle_score : (g ? g->freq_throttle_ratio : 0.0));

    return rep;
}

void inference_print_json_section(FILE *out, const inference_report_t *rep)
{
    if (!out || !rep)
        return;

    fprintf(out, "  \"inference\": {\n");
    fprintf(out, "    \"session_summary\": \"%s\",\n", rep->session_summary);
    fprintf(out, "    \"recommended_hint\": \"%s\",\n", rep->recommended_hint);
    fprintf(out, "    \"frame_inferences\": [\n");

    int printed = 0;
    for (int i = 0; i < rep->frame_count; i++) {
        const frame_inference_t *fi = &rep->frames[i];
        if (!fi->is_jank)
            continue;
        if (printed > 0)
            fprintf(out, ",\n");
        fprintf(out, "      {\n");
        fprintf(out, "        \"frame_id\": %llu,\n", (unsigned long long)fi->frame_id);
        fprintf(out, "        \"hypothesis\": \"%s\",\n", fi->hypothesis);
        fprintf(out, "        \"secondary\": \"%s\",\n", fi->secondary);
        fprintf(out, "        \"confidence\": %.4f,\n", fi->confidence);
        fprintf(out, "        \"summary\": \"%s\",\n", fi->summary);
        fprintf(out, "        \"evidence\": [\n");
        for (int e = 0; e < fi->evidence_count; e++) {
            fprintf(out, "          {\n");
            fprintf(out, "            \"signal\": \"%s\",\n", fi->evidence[e].signal);
            fprintf(out, "            \"weight\": %.4f,\n", fi->evidence[e].weight);
            fprintf(out, "            \"detail\": \"%s\"\n", fi->evidence[e].detail);
            fprintf(out, "          }%s\n", (e + 1 < fi->evidence_count) ? "," : "");
        }
        fprintf(out, "        ]\n");
        fprintf(out, "      }");
        printed++;
        if (printed >= 32)
            break;
    }

    fprintf(out, "\n    ]\n");
    fprintf(out, "  },\n");
}

void inference_free(inference_report_t *rep)
{
    if (!rep)
        return;
    free(rep->frames);
    free(rep);
}
