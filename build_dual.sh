#!/usr/bin/env bash
# Build ONE installer that adds two homescreen shortcuts (Poops and Relapse),
# plus its PC setup host. Usage: ./build_dual.sh   (HOST_ONLY=1 to rebuild
# only the host from an already built ELF)
# Outputs: webkit-autoloader-dual-installer_v<version>.elf
#          webkit-autoloader-dual-host_v<version>.py
set -euo pipefail
cd "$(dirname "$0")"
IMAGE_NAME="ps5-webkit-autoloader-sdk"

sdk() {
    docker run --rm -u "$(id -u):$(id -g)" \
        -e BUILD_VERSION -e "BUILD_TYPE=${BUILD_TYPE:-dev}" -e "CUSTOM_VERSION=${CUSTOM_VERSION:-}" \
        -v "$(pwd)":/src -w /src "$IMAGE_NAME" "$@"
}

if [ -z "$(docker images -q "$IMAGE_NAME")" ]; then
    docker build -t "$IMAGE_NAME" -f third_party/ps5-webkit-remote-loader/Dockerfile.sdk third_party/ps5-webkit-remote-loader
fi

if [ "${HOST_ONLY:-0}" = 1 ]; then
    elf="$(ls -t webkit-autoloader-dual-installer_v*.elf 2>/dev/null | head -1)"
    [ -n "$elf" ] || { echo "No dual installer ELF, run without HOST_ONLY first" >&2; exit 1; }
    BUILD_VERSION="${elf#webkit-autoloader-dual-installer_v}"
    export BUILD_VERSION="${BUILD_VERSION%.elf}"
else
    BUILD_VERSION="$(python3 tools/gen_version.py --print)"
    export BUILD_VERSION
    elf="webkit-autoloader-dual-installer_v${BUILD_VERSION}.elf"
    rm -f webkit-autoloader-dual-installer_v*.elf
    sdk make clean all VARIANT=dual
    mv installer.elf "$elf"
    echo "Built $elf"
fi

host="webkit-autoloader-dual-host_v${BUILD_VERSION}.py"
rm -f webkit-autoloader-dual-host_v*.py
rm -rf build/host   # not cleaned by make; avoid stale payload ELFs in the host
sdk make host VARIANT=dual HOST_PAYLOAD="$elf"
mv webkit-autoloader-host.py "$host"
echo "Built $host"
