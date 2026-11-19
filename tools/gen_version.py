#!/usr/bin/env python3
"""Compute the build version and generate include/wkali_version.h + assets/param.json.

Version scheme (mirrors the ps5-bdjb-autoloader project):

    dev:          <base>-<build_type>-<suffix>
    stable:       <base>
    pre-release:  <base>-pre-<suffix>

    e.g. 0.0.1-dev-abc1234           clean tree, dev build
         0.0.1-dev-20260806123500    dirty tree — timestamp instead of hash
         0.0.1-pre-abc1234           clean tree, pre-release build
         0.0.1                       stable build — no hash/timestamp suffix

The base version comes from WKAL_VERSION in include/wkali.h. BUILD_TYPE is
taken from the BUILD_TYPE environment variable (default: dev).

When CUSTOM_VERSION is set, it is appended to the base version and takes
precedence over the build type (e.g. CUSTOM_VERSION=cache-test -> 0.0.1-cache-test).
The custom suffix is also shown in the PS5 homescreen app title.

Usage:
    gen_version.py                 # (re)generate version header and app metadata
    gen_version.py --print         # print the full version string
    gen_version.py --variant       # print the selected build variant
    gen_version.py --page-config PATH  # emit standalone UI build information
"""

import datetime
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = os.path.join(REPO, "include", "wkali_version.h")
WKALI_H = os.path.join(REPO, "include", "wkali.h")
PARAM_JSON = os.path.join(REPO, "assets", "param.json")
PARAM_TEMPLATE = os.path.join(REPO, "assets", "param.json.template")
VERSION_PLACEHOLDER = b"[[VERSION_PLACEHOLDER]]"

# Build variants (WKAL_VARIANT env / make VARIANT=...).
#   auto: upstream behaviour, one homescreen app, exploit picked at runtime.
#   dual: ONE installer that caches the page once and installs TWO homescreen
#         apps, one forcing Poops and one forcing Relapse. Its own port (= own
#         WebKit origin and AppCache), so it never touches the auto install.
# Each app gets assets/param-<id>.json, assets/icon0-<id>.png and an entry page
# /app/<entry> -> /app/<version>/<entry> ("index.html" for auto, "<id>.html").
# Thread names must stay <= 16 chars (FreeBSD TDNAMLEN) for stale-kill to work.
VARIANTS = {
    "auto": {
        "port": 18181,
        "app_name": "WebKit Autoloader",
        "thread_name": "wkali.elf",
        "apps": [
            {"id": "auto", "title_id": "WKAL00001", "name": "WebKit Autoloader",
             "exploit": "", "color": None, "label": None},
        ],
    },
    "dual": {
        "port": 18182,
        "app_name": "WebKit Autoloader Poops+Relapse",
        "thread_name": "wkali-dual.elf",
        "apps": [
            {"id": "poops", "title_id": "WKAL00002", "name": "WebKit Autoloader Poops",
             "exploit": "poops", "color": "#f28c28", "label": "POOPS"},
            {"id": "relapse", "title_id": "WKAL00003", "name": "WebKit Autoloader Relapse",
             "exploit": "relapse", "color": "#9b59d6", "label": "RELAPSE"},
        ],
    },
}


def app_entry(app):
    """Entry page file name of an app inside /app/ and /app/<version>/."""
    return "index.html" if app["id"] == "auto" else app["id"] + ".html"


def app_param_path(app):
    return os.path.join("assets", f"param-{app['id']}.json")


def app_icon_path(app):
    return os.path.join("assets", f"icon0-{app['id']}.png")


def get_variant():
    """Return (name, settings) for the WKAL_VARIANT environment variable."""
    name = os.environ.get("WKAL_VARIANT", "").strip() or "auto"
    if name not in VARIANTS:
        sys.exit(f"Unknown WKAL_VARIANT '{name}' (expected: {', '.join(VARIANTS)})")
    return name, VARIANTS[name]


def read_base_version():
    """Read the base WKAL_VERSION from include/wkali.h."""
    try:
        with open(WKALI_H) as f:
            content = f.read()
        m = re.search(r'#define\s+WKAL_VERSION\s+"([^"]+)"', content)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "0.0.0"


def git(*args):
    """Run a git command in the repo; returns stdout stripped or ''."""
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, errors="replace"
        ).stdout.strip()
    except OSError:
        return ""


def get_version_info(build_type=None):
    """Compute the version components. Returns a dict with 'full' being the
    version string to display everywhere."""
    if build_type is None:
        build_type = os.environ.get("BUILD_TYPE", "dev")
    if build_type not in ("dev", "stable", "pre-release"):
        build_type = "dev"

    variant, settings = get_variant()
    base = read_base_version()
    custom = os.environ.get("CUSTOM_VERSION", "").strip()

    git_hash = git("rev-parse", "--short", "HEAD")
    dirty = git("status", "--porcelain")
    suffix = git_hash or "unknown"
    if dirty:
        suffix = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    if custom:
        full = f"{base}-{custom}"
    elif build_type == "stable":
        full = base
    else:
        segment = "pre" if build_type == "pre-release" else "dev"
        full = f"{base}-{segment}-{suffix}"

    return {
        "base": base,
        "build_type": build_type,
        "suffix": suffix,
        "full": os.environ.get("BUILD_VERSION") or full,
        "git_hash": git_hash or "unknown",
        "dirty": bool(dirty),
        "build_time": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        "title": full if custom else base,
        "variant": variant,
        **settings,
    }


def header_text(info):
    return (
        "#pragma once\n"
        "/* Auto-generated by tools/gen_version.py - do not edit. */\n"
        "\n"
        f'#define WKAL_BASE_VERSION "{info["base"]}"\n'
        f'#define WKAL_BUILD_TYPE "{info["build_type"]}"\n'
        f'#define WKAL_BUILD_SUFFIX "{info["suffix"]}"\n'
        f'#define WKAL_FULL_VERSION "{info["full"]}"\n'
        f'#define WKAL_BUILD_TIME "{info["build_time"]}"\n'
        "\n"
        f'#define WKAL_VARIANT "{info["variant"]}"\n'
        f'#define WKAL_APP_NAME "{info["app_name"]}"\n'
        f'#define WKAL_TITLE_ID "{info["apps"][0]["title_id"]}"\n'
        f'#define WKALI_PORT {info["port"]}\n'
        f'#define WKALI_THREAD_NAME "{info["thread_name"]}"\n'
        "\n"
        "/* Homescreen apps installed by /install: X(index, title_id, name, param, icon) */\n"
        f'#define WKAL_APP_COUNT {len(info["apps"])}\n'
        "#define WKAL_APPS(X) \\\n"
        + "".join(
            f'    X({i}, "{a["title_id"]}", "{a["name"]}", '
            f'"{app_param_path(a)}", "{app_icon_path(a)}") \\\n'
            for i, a in enumerate(info["apps"])
        )
        + "\n"
    )


def write_if_changed(path, data):
    """Write bytes only when the content differs, to avoid pointless rebuilds."""
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "--print":
        print(get_version_info()["full"])
        return 0
    if argv and argv[0] == "--build-time":
        print(get_version_info()["build_time"])
        return 0
    if argv and argv[0] == "--title":
        info = get_version_info()
        print(f"{info['app_name']} v{info['full']} by PLK (built {info['build_time']})")
        return 0
    if argv and argv[0] == "--variant":
        print(get_variant()[0])
        return 0
    if argv and argv[0] == "--entries":
        print(" ".join(app_entry(a) for a in get_variant()[1]["apps"]))
        return 0

    if len(argv) == 2 and argv[0] == "--page-config":
        info = get_version_info()
        config = {"version": info["full"], "buildTime": info["build_time"]}
        data = "window.WKAL_PAGE = " + json.dumps(config).replace("<", "\\u003c") + ";\n"
        write_if_changed(os.path.abspath(argv[1]), data.encode("utf-8"))
        return 0

    info = get_version_info()

    write_if_changed(HEADER, header_text(info).encode("utf-8"))

    # PS5 homescreen app metadata — title gets the base version (or the full
    # custom version) so the app label reflects what was built.
    with open(PARAM_TEMPLATE, "rb") as f:
        param = f.read()
    for i, app in enumerate(info["apps"]):
        data = (
            param.replace(VERSION_PLACEHOLDER, info["title"].encode("utf-8"))
            .replace(b"[[TITLE_ID_PLACEHOLDER]]", app["title_id"].encode("utf-8"))
            .replace(b"[[PORT_PLACEHOLDER]]", str(info["port"]).encode("utf-8"))
            .replace(b"[[ENTRY_PLACEHOLDER]]", app_entry(app).encode("utf-8"))
            .replace(b"[[APP_NAME_PLACEHOLDER]]", app["name"].encode("utf-8"))
        )
        write_if_changed(os.path.join(REPO, app_param_path(app)), data)
        if i == 0:
            # Upstream's shared installer config still names assets/param.json.
            write_if_changed(PARAM_JSON, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
