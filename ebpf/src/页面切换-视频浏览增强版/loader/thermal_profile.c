/* SPDX-License-Identifier: BSD-2-Clause */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "thermal_profile.h"

int thermal_profile_load(const char *path, thermal_profile_t *prof)
{
    FILE *fp;
    char line[256];
    size_t cap = 256, cnt = 0;

    if (!prof)
        return -1;
    memset(prof, 0, sizeof(*prof));

    if (!path || !path[0])
        return -1;

    fp = fopen(path, "r");
    if (!fp)
        return -1;

    prof->samples = malloc(cap * sizeof(*prof->samples));
    if (!prof->samples) {
        fclose(fp);
        return -1;
    }

    while (fgets(line, sizeof(line), fp)) {
        unsigned long long ts = 0;
        int temp = 0;

        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r')
            continue;
        if (strstr(line, "timestamp_ns") != NULL)
            continue;
        if (sscanf(line, "%llu %d", &ts, &temp) != 2 &&
            sscanf(line, "\"%llu\",%d", &ts, &temp) != 2 &&
            sscanf(line, "%llu,%d", &ts, &temp) != 2)
            continue;
        if (temp < 20000 || temp > 120000)
            continue;

        if (cnt >= cap) {
            cap *= 2;
            thermal_sample_t *tmp = realloc(prof->samples, cap * sizeof(*tmp));
            if (!tmp) break;
            prof->samples = tmp;
        }

        prof->samples[cnt].timestamp_ns = ts;
        prof->samples[cnt].temp_mc = temp;
        cnt++;
    }
    fclose(fp);

    prof->count = cnt;
    if (cnt == 0) {
        free(prof->samples);
        prof->samples = NULL;
        return -1;
    }

    prof->baseline_temp_mc = prof->samples[0].temp_mc;
    prof->peak_temp_mc = prof->samples[0].temp_mc;
    for (size_t i = 1; i < cnt; i++) {
        if (prof->samples[i].temp_mc < prof->baseline_temp_mc)
            prof->baseline_temp_mc = prof->samples[i].temp_mc;
        if (prof->samples[i].temp_mc > prof->peak_temp_mc)
            prof->peak_temp_mc = prof->samples[i].temp_mc;
    }
    return 0;
}

void thermal_profile_free(thermal_profile_t *prof)
{
    if (!prof)
        return;
    free(prof->samples);
    prof->samples = NULL;
    prof->count = 0;
}

int thermal_profile_stats_in_window(const thermal_profile_t *prof,
    uint64_t win_start, uint64_t win_end,
    int32_t *max_temp, int32_t *min_temp, int *sample_count)
{
    int32_t tmax = 0, tmin = 0;
    int n = 0;

    if (!prof || prof->count == 0)
        return -1;

    for (size_t i = 0; i < prof->count; i++) {
        uint64_t ts = prof->samples[i].timestamp_ns;
        if (ts < win_start || ts > win_end)
            continue;
        if (n == 0) {
            tmax = tmin = prof->samples[i].temp_mc;
        } else {
            if (prof->samples[i].temp_mc > tmax) tmax = prof->samples[i].temp_mc;
            if (prof->samples[i].temp_mc < tmin) tmin = prof->samples[i].temp_mc;
        }
        n++;
    }

    if (n == 0)
        return -1;

    if (max_temp) *max_temp = tmax;
    if (min_temp) *min_temp = tmin;
    if (sample_count) *sample_count = n;
    return 0;
}

void thermal_profile_apply_to_graph(thermal_profile_t *prof,
    critical_path_graph_t *g,
    struct frame_window *frames, int frame_count,
    int64_t clock_offset_ns)
{
    int32_t jank_peak = 0;
    int32_t jank_base = 0;
    int jank_samples = 0;
    int jank_windows = 0;

    if (!prof || !g || !frames || frame_count <= 0)
        return;

    for (int i = 0; i < frame_count; i++) {
        if (!frames[i].is_jank)
            continue;

        int64_t ws = (int64_t)frames[i].expected_start_ns - 20000000LL;
        int64_t we = (int64_t)frames[i].actual_end_ns + 10000000LL;
        if (ws < 0) ws = 0;

        int32_t tmax = 0, tmin = 0;
        int n = 0;
        if (thermal_profile_stats_in_window(prof, (uint64_t)ws, (uint64_t)we,
                &tmax, &tmin, &n) != 0)
            continue;

        jank_windows++;
        jank_samples += n;
        if (jank_peak == 0 || tmax > jank_peak)
            jank_peak = tmax;
        if (jank_base == 0)
            jank_base = tmin;
    }

    if (jank_peak <= 0)
        return;

    prof->jank_window_peak_mc = jank_peak;
    prof->jank_window_delta_mc = jank_peak - prof->baseline_temp_mc;
    if (prof->jank_window_delta_mc < 0)
        prof->jank_window_delta_mc = 0;

    prof->throttle_score = (double)prof->jank_window_delta_mc / 10000.0;
    if (g->freq_throttle_ratio > prof->throttle_score)
        prof->throttle_score = g->freq_throttle_ratio;
    if (prof->throttle_score > 1.0)
        prof->throttle_score = 1.0;

    g->freq_throttle_ratio = prof->throttle_score;

    for (uint32_t ni = 0; ni < g->node_count; ni++) {
        graph_node_t *n = &g->nodes[ni];
        if (n->type == GRAPH_NODE_VIDEO_DECODER || n->decode_path_proximity > 0.1)
            n->thermal_proximity = prof->throttle_score * (0.5 + n->decode_path_proximity);
        else if (n->frame_window_overlap > 0.1)
            n->thermal_proximity = prof->throttle_score * n->frame_window_overlap;
    }

    (void)clock_offset_ns;
    (void)jank_samples;
    (void)jank_windows;
}

int thermal_profile_auto_path(const char *frame_data_path,
    char *out, size_t out_len)
{
    const char *slash;
    size_t dlen;

    if (!frame_data_path || !out || out_len == 0)
        return -1;

    slash = strrchr(frame_data_path, '/');
    if (!slash)
        slash = strrchr(frame_data_path, '\\');

    if (slash) {
        dlen = (size_t)(slash - frame_data_path) + 1;
        if (dlen >= out_len)
            dlen = out_len - 32;
        memcpy(out, frame_data_path, dlen);
        out[dlen] = '\0';
        strncat(out, "thermal_profile.txt", out_len - dlen - 1);
    } else {
        snprintf(out, out_len, "thermal_profile.txt");
    }
    return 0;
}
