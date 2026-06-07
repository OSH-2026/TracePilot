/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef __INFERENCE_ENGINE_H__
#define __INFERENCE_ENGINE_H__

#include <stdio.h>

#include "frame_aggregator.h"
#include "thermal_profile.h"

#define INFERENCE_MAX_EVIDENCE 8
#define INFERENCE_SIGNAL_LEN   64
#define INFERENCE_DETAIL_LEN   128

typedef struct {
    char   signal[INFERENCE_SIGNAL_LEN];
    double weight;
    char   detail[INFERENCE_DETAIL_LEN];
} inference_evidence_t;

typedef struct {
    uint64_t frame_id;
    int      is_jank;
    char     hypothesis[32];
    char     secondary[32];
    double   confidence;
    inference_evidence_t evidence[INFERENCE_MAX_EVIDENCE];
    int      evidence_count;
    char     summary[256];
} frame_inference_t;

typedef struct {
    frame_inference_t *frames;
    int                frame_count;
    char               session_summary[512];
    char               recommended_hint[32];
} inference_report_t;

inference_report_t *inference_build(critical_path_graph_t *g,
    frame_classification_t *classifications, int class_count,
    const thermal_profile_t *thermal, const char *scenario);

void inference_print_json_section(FILE *out, const inference_report_t *rep);
void inference_free(inference_report_t *rep);

#endif /* __INFERENCE_ENGINE_H__ */
