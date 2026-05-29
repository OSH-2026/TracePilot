/* SPDX-License-Identifier: BSD-2-Clause */
/*
 * PID → UID → package_name resolver.
 *
 * Resolution chain:
 *   1. /proc/<pid>/cmdline  →  may contain package name directly
 *   2. /proc/<pid>/status    →  uid
 *   3. pm list packages -U   →  uid → package_name
 *
 * Uses an in-memory array cache for fast uid→package lookups.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "resolver.h"

#define MAX_UID_MAP   8192
#define MAX_PKG_LEN   256

struct uid_pkg {
	uid_t  uid;
	char   pkg[MAX_PKG_LEN];
};

static struct uid_pkg g_uid_map[MAX_UID_MAP];
static int            g_uid_cnt = 0;
static int            g_uid_map_loaded = 0;

/*
 * Check if a string looks like an Android package name
 * (contains dots, no slashes).
 */
static int looks_like_pkg(const char *s)
{
	if (!s || s[0] == '\0')
		return 0;
	if (strchr(s, '/') != NULL)
		return 0;
	return strchr(s, '.') != NULL;
}

/*
 * Load uid→package mapping via "pm list packages -U".
 * Format: "package:com.example.app uid:10123"
 */
static void load_uid_pkg_map(void)
{
	FILE *fp;
	char line[512];

	if (g_uid_map_loaded)
		return;
	g_uid_map_loaded = 1;

	fp = popen("pm list packages -U 2>/dev/null", "r");
	if (!fp)
		return;

	while (fgets(line, sizeof(line), fp)) {
		char pkg[MAX_PKG_LEN] = {0};
		unsigned uid = 0;
		char *p_pkg, *p_uid;

		p_pkg = strstr(line, "package:");
		p_uid = strstr(line, "uid:");
		if (!p_pkg || !p_uid)
			continue;

		p_pkg += 8; /* strlen("package:") */
		if (sscanf(p_pkg, "%255s", pkg) != 1)
			continue;
		if (sscanf(p_uid, "uid:%u", &uid) != 1)
			continue;

		if (g_uid_cnt < MAX_UID_MAP) {
			g_uid_map[g_uid_cnt].uid = (uid_t)uid;
			strncpy(g_uid_map[g_uid_cnt].pkg, pkg, MAX_PKG_LEN - 1);
			g_uid_map[g_uid_cnt].pkg[MAX_PKG_LEN - 1] = '\0';
			g_uid_cnt++;
		}
	}
	pclose(fp);
}

/*
 * Lookup package name by uid.
 */
static const char *uid_to_pkg(uid_t uid)
{
	for (int i = 0; i < g_uid_cnt; i++) {
		if (g_uid_map[i].uid == uid)
			return g_uid_map[i].pkg;
	}
	return NULL;
}

/*
 * Read /proc/<pid>/cmdline to get the process command line.
 * For Android apps this is typically the package name.
 */
static int pid_to_cmdline(pid_t pid, char *buf, size_t n)
{
	char path[64];
	FILE *fp;

	snprintf(path, sizeof(path), "/proc/%d/cmdline", pid);
	fp = fopen(path, "rb");
	if (!fp)
		return -1;

	size_t r = fread(buf, 1, n - 1, fp);
	fclose(fp);
	if (r == 0)
		return -1;

	buf[r] = '\0';
	return 0;
}

/*
 * Read UID from /proc/<pid>/status.
 * Format: "Uid:\t<real_uid>\t<effective_uid>\t..."
 */
int get_uid_from_pid(pid_t pid, uid_t *uid)
{
	char path[64];
	char line[256];
	FILE *fp;

	snprintf(path, sizeof(path), "/proc/%d/status", pid);
	fp = fopen(path, "r");
	if (!fp)
		return -1;

	while (fgets(line, sizeof(line), fp)) {
		int ruid, euid, suid, fsuid;
		if (sscanf(line, "Uid:\t%d\t%d\t%d\t%d", &ruid, &euid, &suid, &fsuid) == 4) {
			*uid = (uid_t)ruid;
			fclose(fp);
			return 0;
		}
	}
	fclose(fp);
	return -1;
}

/*
 * Resolve PID → package_name.
 *
 * Strategy (matching user's page_turning.c pattern):
 *   1. Read /proc/<pid>/cmdline — for Android apps this is the package name
 *   2. If cmdline doesn't look like a package, fall back to uid→pkg mapping
 *
 * Returns 0 on success, -1 if not found.
 */
int resolve_pid(pid_t pid, char *package_name, size_t pkg_len)
{
	char cmdline[MAX_PKG_LEN] = {0};

	load_uid_pkg_map();

	/* Try cmdline first */
	if (pid_to_cmdline(pid, cmdline, sizeof(cmdline)) == 0 && cmdline[0] != '\0') {
		if (looks_like_pkg(cmdline)) {
			strncpy(package_name, cmdline, pkg_len - 1);
			package_name[pkg_len - 1] = '\0';
			return 0;
		}
	}

	/* Fall back to uid→pkg */
	uid_t uid;
	if (get_uid_from_pid(pid, &uid) == 0) {
		const char *pkg = uid_to_pkg(uid);
		if (pkg) {
			strncpy(package_name, pkg, pkg_len - 1);
			package_name[pkg_len - 1] = '\0';
			return 0;
		}
		/* If not in packages.list, return uid as string */
		snprintf(package_name, pkg_len, "uid_%u", uid);
		return -1;
	}

	return -1;
}