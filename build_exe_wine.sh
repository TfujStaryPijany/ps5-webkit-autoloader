#!/usr/bin/env bash
# Pack the newest webkit-autoloader-dual-host_v*.py into a Windows .exe with
# PyInstaller running under Wine (no Windows machine needed). Uses its own
# Wine prefix (default ~/.cache/wkal-wine), never ~/.wine. First run downloads
# Python 3.11.9 for Windows (python.org, ~25 MB) and PyInstaller (PyPI).
set -euo pipefail
cd "$(dirname "$0")"
export WINEPREFIX="${WKAL_WINEPREFIX:-$HOME/.cache/wkal-wine}" WINEARCH=win64 WINEDEBUG=-all
PY_VER=3.11.9
WINPY='C:\py311\python.exe'

py="$(ls -t webkit-autoloader-dual-host_v*.py 2>/dev/null | head -1)"
[ -n "$py" ] || { echo "No dual host .py, run ./build_dual.sh first" >&2; exit 1; }
name="${py%.py}"

if [ ! -f "$WINEPREFIX/drive_c/py311/python.exe" ]; then
    mkdir -p "$WINEPREFIX"
    wineboot -i >/dev/null 2>&1
    winecfg -v win10 >/dev/null 2>&1   # Python 3.11 refuses Windows 7
    inst="$WINEPREFIX/python-$PY_VER-amd64.exe"
    curl -fsSL -o "$inst" "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-amd64.exe"
    wine "$inst" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_doc=0 \
        Include_tcltk=0 Include_launcher=0 'TargetDir=C:\py311' >/dev/null 2>&1
    rm -f "$inst"
    wine "$WINPY" -m pip install --disable-pip-version-check --no-warn-script-location pyinstaller
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp "$py" assets/icon.ico "$work/"
# X utf8: the host prints box-drawing characters; without UTF-8 mode it
# crashes when stdout is a pipe/file on non-UTF-8 Windows code pages.
(cd "$work" && wine "$WINPY" -m PyInstaller --onefile --noconfirm --icon icon.ico \
    --python-option "X utf8" --name "$name" --workpath work --distpath dist --specpath . "$py")
cp -f "$work/dist/$name.exe" .
echo "Built $name.exe"
