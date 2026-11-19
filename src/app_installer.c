/*
 * Multi-app homescreen installer (replaces remote-loader's single-app
 * installer/common/app_installer.c in this build).
 *
 * Installs/updates every app listed in WKAL_APPS (generated into
 * include/wkali_version.h by tools/gen_version.py), e.g. the "dual" variant
 * installs a Poops shortcut and a Relapse shortcut from one installer run.
 * Logic per app is unchanged from the shared installer, which is based on the
 * original implementation in ftpsrv by John Törnblom. GPLv3.
 */

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "app_installer.h"
#include "installer_support.h"
#include "installer_config.h"
#include <ps5/kernel.h>

#define INCASSET(name, file)                                                   \
  __asm__(".section .rodata\n"                                                 \
          ".global " #name "\n"                                                \
          ".global " #name "_end\n"                                            \
          ".global " #name "_size\n"                                           \
          ".align 16\n" #name ":\n"                                            \
          ".incbin \"" file "\"\n" #name "_end:\n" #name "_size:\n"            \
          ".quad " #name "_end - " #name "\n"                                  \
          ".previous\n");                                                      \
  extern const uint8_t name[];                                                 \
  extern const size_t name##_size;

#define WKAL_APP_ASSETS(i, title_id, name, param, icon)                        \
  INCASSET(wkal_app##i##_param, param)                                         \
  INCASSET(wkal_app##i##_icon, icon)
WKAL_APPS(WKAL_APP_ASSETS)

struct wkal_app {
  const char *title_id;
  const char *name;
  const uint8_t *param;
  const size_t *param_size;
  const uint8_t *icon;
  const size_t *icon_size;
};

#define WKAL_APP_ROW(i, title_id, name, param, icon)                           \
  {title_id, name, wkal_app##i##_param, &wkal_app##i##_param_size,             \
   wkal_app##i##_icon, &wkal_app##i##_icon_size},
static const struct wkal_app wkal_apps[] = {WKAL_APPS(WKAL_APP_ROW)};

#define WKAL_APP_ID_CHECK(i, title_id, name, param, icon)                      \
  _Static_assert(sizeof(title_id) <= 16, "title ID too long for path buffers");
WKAL_APPS(WKAL_APP_ID_CHECK)

int sceAppInstUtilInitialize(void);
int sceAppInstUtilTerminate(void);
int sceAppInstUtilAppInstallAll(void *);

static int mkdir_p(const char *path, mode_t mode) {
  char tmp[256];
  snprintf(tmp, sizeof(tmp), "%s", path);
  size_t len = strlen(tmp);
  if (len == 0)
    return 0;
  if (tmp[len - 1] == '/')
    tmp[len - 1] = '\0';
  for (char *p = tmp + 1; *p; p++) {
    if (*p == '/') {
      *p = '\0';
      if (mkdir(tmp, mode) != 0 && errno != EEXIST) {
        return -1;
      }
      *p = '/';
    }
  }
  if (mkdir(tmp, mode) != 0 && errno != EEXIST) {
    return -1;
  }
  return 0;
}

static int install_file(const char *path, const uint8_t *data, size_t size) {
  FILE *f;
  if (!(f = fopen(path, "wb"))) {
    return -1;
  }
  if (fwrite(data, size, 1, f) != 1) {
    fclose(f);
    return -1;
  }
  fclose(f);
  return 0;
}

static int install_app(const char *title_id, const char *dir) {
  int (*sceAppInstUtilAppInstallTitleDir)(const char *, const char *, void *) =
      0;
  const char *nid = "Wudg3Xe3heE";
  uint32_t handle;

  if (!kernel_dynlib_handle(-1, "libSceAppInstUtil.sprx", &handle)) {
    sceAppInstUtilAppInstallTitleDir =
        (void *)kernel_dynlib_resolve(-1, handle, nid);
  }

  if (sceAppInstUtilAppInstallTitleDir) {
    return sceAppInstUtilAppInstallTitleDir(title_id, dir, 0);
  }

  return sceAppInstUtilAppInstallAll(0);
}

static int needs_update(const char *path, const uint8_t *expected_data,
                        size_t expected_size) {
  struct stat st;
  if (stat(path, &st) != 0)
    return 1;
  if ((size_t)st.st_size != expected_size)
    return 1;

  FILE *f = fopen(path, "rb");
  if (!f)
    return 1;

  uint8_t *buf = malloc(expected_size);
  if (!buf) {
    fclose(f);
    return 1;
  }

  if (fread(buf, 1, expected_size, f) != expected_size) {
    free(buf);
    fclose(f);
    return 1;
  }
  fclose(f);

  int mismatch = memcmp(buf, expected_data, expected_size);
  free(buf);

  return mismatch != 0;
}

/* Returns 1 if the app was (re)installed, 0 if already up to date, -1 on error.
 * Expects sceAppInstUtilInitialize() to have been called. */
static int install_one(const struct wkal_app *app) {
  const char *title_id = app->title_id;
  char base_dir[256];
  char sce_sys_dir[256];
  char param_path[256];
  char icon_path[256];

  snprintf(base_dir, sizeof(base_dir), "/user/app/%s", title_id);
  snprintf(sce_sys_dir, sizeof(sce_sys_dir), "/user/app/%s/sce_sys", title_id);
  snprintf(param_path, sizeof(param_path), "%s/param.json", sce_sys_dir);
  snprintf(icon_path, sizeof(icon_path), "%s/icon0.png", sce_sys_dir);

  struct stat st;
  int exists = stat(base_dir, &st) == 0;
  if (exists && !needs_update(param_path, app->param, *app->param_size) &&
      !needs_update(icon_path, app->icon, *app->icon_size)) {
    installer_log(INSTALLER_LOG_PREFIX " %s (%s) is up to date\n", app->name, title_id);
    return 0;
  }

  installer_log(INSTALLER_LOG_PREFIX " %s browser launcher app %s (%s)...\n",
                exists ? "Updating" : "Installing", app->name, title_id);
  installer_notify("%s %s App...", exists ? "Updating" : "Installing", app->name);

  if (mkdir_p(sce_sys_dir, 0755) != 0) {
    installer_log(INSTALLER_LOG_PREFIX " Failed to create app dir: %s (errno: %d)\n",
                  sce_sys_dir, errno);
    return -1;
  }
  if (install_file(param_path, app->param, *app->param_size)) {
    installer_log(INSTALLER_LOG_PREFIX " Failed to install %s\n", param_path);
    return -1;
  }
  if (install_file(icon_path, app->icon, *app->icon_size)) {
    installer_log(INSTALLER_LOG_PREFIX " Failed to install %s\n", icon_path);
    return -1;
  }

  int err;
  if ((err = install_app(title_id, "/user/app/"))) {
    installer_log(INSTALLER_LOG_PREFIX " install_app(%s): error 0x%08X\n", title_id, err);
    return -1;
  }

  installer_log(INSTALLER_LOG_PREFIX " %s (%s) installed successfully.\n", app->name, title_id);
  return 1;
}

int installer_install_app_if_needed(void) {
  int err;
  if ((err = sceAppInstUtilInitialize())) {
    installer_log(INSTALLER_LOG_PREFIX " sceAppInstUtilInitialize: error 0x%08X\n", err);
    return -1;
  }

  int failed = 0;
  int changed = 0;
  for (size_t i = 0; i < sizeof(wkal_apps) / sizeof(wkal_apps[0]); i++) {
    int r = install_one(&wkal_apps[i]);
    if (r < 0)
      failed++;
    else
      changed += r;
  }

  sceAppInstUtilTerminate();

  if (failed) {
    installer_notify(INSTALLER_APP_NAME ": %d of %d apps failed to install",
                     failed, WKAL_APP_COUNT);
    return -1;
  }
  if (changed)
    installer_notify(INSTALLER_APP_NAME " App%s Ready!", WKAL_APP_COUNT > 1 ? "s" : "");
  return 0;
}
