/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef __IDENTITY_H__
#define __IDENTITY_H__

#include <stdint.h>
#include <sys/types.h>

#include "frame_aggregator.h"

struct frame_window;

#define IDENTITY_BOOT_ID_LEN   48
#define IDENTITY_PKG_LEN       256
#define IDENTITY_NAME_LEN      256
#define IDENTITY_KEY_LEN       512
#define IDENTITY_SESSION_LEN   64

typedef struct {
    char boot_id[IDENTITY_BOOT_ID_LEN];
    pid_t pid;
    uint64_t start_time_ns;
    uid_t uid;
    char process_name[IDENTITY_NAME_LEN];
    char package[IDENTITY_PKG_LEN];
    char instance_id[IDENTITY_KEY_LEN];
} process_instance_t;

typedef struct {
    char boot_id[IDENTITY_BOOT_ID_LEN];
    pid_t tid;
    pid_t pid;
    uint64_t start_time_ns;
    char comm[16];
    char thread_key[IDENTITY_KEY_LEN];
} thread_identity_t;

typedef struct {
    char package[IDENTITY_PKG_LEN];
    uid_t uid;
    char process_instance_id[IDENTITY_KEY_LEN];
    int64_t frame_token;
    char frame_key[IDENTITY_KEY_LEN];
} frame_key_t;

typedef struct {
    char session_id[IDENTITY_SESSION_LEN];
    char package[IDENTITY_PKG_LEN];
    uid_t uid;
    uint64_t collection_start_ns;
} app_session_t;

struct sched_event;

void identity_init_session(const char *target_package, uint64_t collection_start_ns);
const app_session_t *identity_get_session(void);

int  identity_read_boot_id(char *out, size_t out_len);
uint64_t identity_read_process_start_time(pid_t pid);

process_instance_t *identity_get_or_create_process(pid_t pid);
thread_identity_t  *identity_get_or_create_thread(pid_t tid, pid_t pid, const char *comm);

frame_key_t identity_build_frame_key(const struct frame_window *fw,
                                     const process_instance_t *proc);

int identity_scan_sched_events(struct sched_event *events, size_t count);
int identity_apply_packages_to_graph(critical_path_graph_t *g);

int identity_save_json(const char *path);
int identity_load_json(const char *path);
int identity_auto_load_beside(const char *events_path);

const char *identity_package_for_pid(pid_t pid);
const char *identity_package_for_tid(pid_t tid);

#endif /* __IDENTITY_H__ */
