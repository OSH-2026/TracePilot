/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef __RESOLVER_H__
#define __RESOLVER_H__

#include <sys/types.h>

/* Read UID from /proc/<pid>/status. Returns 0 on success. */
int get_uid_from_pid(pid_t pid, uid_t *uid);

/* Resolve PID to package_name via packages.list. Returns 0 on success. */
int resolve_pid(pid_t pid, char *package_name, size_t pkg_len);

#endif /* __RESOLVER_H__ */