#!/usr/bin/env python3
"""Run src/app_installer.c (multi-app homescreen installer) on Linux with
stubbed PS5 services. All /user paths are redirected into a temp directory.
Needs a native C compiler with GNU ld --wrap (e.g. inside the SDK image).
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
COMMON = REPO / 'third_party/ps5-webkit-remote-loader/installer/common'

HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include "app_installer.h"
#include "installer_support.h"

static const char *root;
static int initialized, terminated, installed, init_error, install_error, notified;
static char notification[3075];

static const char *mapped(const char *path, char *out) {
    if (strcmp(path, "/user") && strncmp(path, "/user/", 6)) return path;
    assert(snprintf(out, 2048, "%s%s", root, path) < 2048);
    return out;
}
FILE *__real_fopen(const char *, const char *);
int __real_stat(const char *, struct stat *);
int __real_mkdir(const char *, mode_t);
FILE *__wrap_fopen(const char *p, const char *m) { char b[2048]; return __real_fopen(mapped(p,b),m); }
int __wrap_stat(const char *p, struct stat *s) { char b[2048]; return __real_stat(mapped(p,b),s); }
int __wrap_mkdir(const char *p, mode_t m) { char b[2048]; return __real_mkdir(mapped(p,b),m); }

int kernel_dynlib_handle(int pid, const char *name, uint32_t *handle) { return -1; }
uintptr_t kernel_dynlib_resolve(int pid, uint32_t handle, const char *nid) { return 0; }
int sceAppInstUtilInitialize(void) { initialized++; return init_error; }
int sceAppInstUtilTerminate(void) { terminated++; return 0; }
int sceAppInstUtilAppInstallAll(void *arg) { installed++; return install_error; }
int sceKernelSendNotificationRequest(int device, notify_request_t *req, size_t size, int unused) {
    snprintf(notification, sizeof(notification), "%s", req->message); notified++; return 0;
}

static void matches(const char *path, const char *expected) {
    char b[1024]; FILE *f = fopen(path, "rb"); assert(f);
    size_t n = fread(b, 1, sizeof(b), f); fclose(f);
    assert(n == strlen(expected)); assert(!memcmp(b, expected, n));
}
static void write_file(const char *path, const char *content) {
    FILE *f = fopen(path, "wb"); assert(f); fputs(content, f); fclose(f);
}

int main(int argc, char **argv) {
    root = argv[1];
    /* Fresh install: both apps, one init/terminate, one "Apps Ready!" */
    assert(installer_install_app_if_needed() == 0);
    assert(initialized == 1 && terminated == 1 && installed == 2);
    matches("/user/app/TEST00002/sce_sys/param.json", "param-a");
    matches("/user/app/TEST00002/sce_sys/icon0.png", "icon-a");
    matches("/user/app/TEST00003/sce_sys/param.json", "param-b");
    matches("/user/app/TEST00003/sce_sys/icon0.png", "icon-b");
    assert(!strcmp(notification, "Dual Test Apps Ready!"));

    /* Up to date: nothing reinstalled, no new notification */
    int before = notified;
    assert(installer_install_app_if_needed() == 0);
    assert(installed == 2 && notified == before);

    /* Only the changed app is updated */
    write_file("/user/app/TEST00003/sce_sys/param.json", "stale");
    assert(installer_install_app_if_needed() == 0);
    assert(installed == 3);
    matches("/user/app/TEST00003/sce_sys/param.json", "param-b");

    /* Install failure is reported and returns an error */
    write_file("/user/app/TEST00002/sce_sys/icon0.png", "stale");
    install_error = 1;
    assert(installer_install_app_if_needed() == -1);
    assert(strstr(notification, "1 of 2 apps failed"));
    install_error = 0;

    /* Init failure: nothing touched */
    init_error = 1;
    assert(installer_install_app_if_needed() == -1);
    init_error = 0;
    assert(installer_install_app_if_needed() == 0);
    matches("/user/app/TEST00002/sce_sys/icon0.png", "icon-a");
    puts("multi-app installer passed");
    return 0;
}
'''

CONFIG = r'''#pragma once
#define INSTALLER_APP_NAME "Dual Test"
#define INSTALLER_LOG_PREFIX "[TEST]"
#define WKAL_APP_COUNT 2
#define WKAL_APPS(X) \
    X(0, "TEST00002", "Test Poops", "param-a", "icon-a") \
    X(1, "TEST00003", "Test Relapse", "param-b", "icon-b")
'''


def main():
    compiler = shutil.which(os.environ.get('HOST_CC', 'clang')) or shutil.which('cc')
    if not compiler or not sys.platform.startswith('linux'):
        raise SystemExit('Needs Linux and a native C compiler (try inside the SDK image).')
    with tempfile.TemporaryDirectory(prefix='wkal-apps-') as directory:
        work = Path(directory)
        (work / 'ps5').mkdir()
        (work / 'ps5/kernel.h').write_text(
            '#include <stdint.h>\nint kernel_dynlib_handle(int, const char *, uint32_t *);\n'
            'uintptr_t kernel_dynlib_resolve(int, uint32_t, const char *);\n')
        (work / 'installer_config.h').write_text(CONFIG)
        for name in ('param-a', 'param-b', 'icon-a', 'icon-b'):
            (work / name).write_text(name)
        (work / 'test.c').write_text(HARNESS)
        (work / 'tree').mkdir()
        command = [compiler, '-D_GNU_SOURCE', '-Wall', '-Werror', '-pthread',
                   '-I' + str(work), '-I' + str(COMMON), str(work / 'test.c'),
                   str(REPO / 'src/app_installer.c'),
                   str(COMMON / 'log.c'), str(COMMON / 'notification.c')]
        command += ['-Wl,--wrap=' + n for n in ('fopen', 'stat', 'mkdir')]
        command += ['-o', str(work / 'test')]
        subprocess.run(command, cwd=work, check=True)
        result = subprocess.run([str(work / 'test'), str(work / 'tree')], cwd=work,
                                capture_output=True, text=True)
        if result.returncode:
            raise SystemExit(result.stdout + result.stderr)
        print(result.stdout.strip().splitlines()[-1])


if __name__ == '__main__':
    main()
