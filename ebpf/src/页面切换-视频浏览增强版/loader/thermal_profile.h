/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef __THERMAL_PROFILE_H__
#define __THERMAL_PROFILE_H__

#include <stdint.h>

#include "frame_aggregator.h"

typedef struct {
    uint64_t timestamp_ns;
    int32_t  temp_mc;
} thermal_sample_t;

typedef struct thermal_profile {
    thermal_sample_t *samples;
    size_t            count;
    int32_t           baseline_temp_mc;
    int32_t           peak_temp_mc;
    int32_t           jank_window_peak_mc;
    int32_t           jank_window_delta_mc;
    double            throttle_score;
} thermal_profile_t;

int  thermal_profile_load(const char *path, thermal_profile_t *prof);
void thermal_profile_free(thermal_profile_t *prof);

int thermal_profile_stats_in_window(const thermal_profile_t *prof,
    uint64_t win_start, uint64_t win_end,
    int32_t *max_temp, int32_t *min_temp, int *sample_count);

void thermal_profile_apply_to_graph(thermal_profile_t *prof,
    critical_path_graph_t *g,
    struct frame_window *frames, int frame_count,
    int64_t clock_offset_ns);

int thermal_profile_auto_path(const char *frame_data_path,
    char *out, size_t out_len);

#endif /* __THERMAL_PROFILE_H__ */
