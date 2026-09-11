#!/usr/bin/env bash
# Build the Run8 Map self-host bundle (dist/Run8MapHost) with PyInstaller and
# stage the operator-editable files beside the .exe. Bash equivalent of
# build_bundle.ps1 - run from the repo root on Windows (Git Bash):
#
#     ./build_bundle.sh              # build + stage
#     ./build_bundle.sh --stage-only # only re-copy operator files into an existing build
#
set -euo pipefail
cd "$(dirname "$0")"

# Prefer the project venv's Python; fall back to python on PATH.
PY="./venv/Scripts/python.exe"
[ -x "$PY" ] || PY="python"
echo "Using Python: $PY"

DIST="dist/Run8MapHost"
STAGE_ONLY="${1:-}"

if [ "$STAGE_ONLY" != "--stage-only" ]; then
    # Ensure PyInstaller is installed (probe quietly; non-zero just means "install").
    if ! "$PY" -m pip show pyinstaller >/dev/null 2>&1; then
        echo "PyInstaller not installed; installing into the venv..."
        "$PY" -m pip install pyinstaller
    fi
    echo
    echo "Building bundle with PyInstaller..."
    "$PY" -m PyInstaller run8maphost.spec --noconfirm --clean
fi

[ -d "$DIST" ] || { echo "ERROR: expected build output not found at $DIST"; exit 1; }

echo
echo "Staging operator files into $DIST ..."
# NOTE: config-socal.ini / areas_socal.ini are NOT staged as loose files - they ride
# inside the exe as templates (run8maphost.spec datas) and host_map seeds them beside
# the exe on first run, so an update can't overwrite an operator's edits. Likewise the
# operator's settings live in settings.bat (seeded by Start Map.bat from the template);
# only the template ships.
cp -f "packaging/README-operator.txt"  "$DIST/README.txt"
cp -f "packaging/Start Map.bat"         "$DIST/Start Map.bat"
cp -f "packaging/settings.default.bat"  "$DIST/settings.default.bat"
echo "  staged: README.txt, Start Map.bat, settings.default.bat"
echo "  (config-socal.ini / areas_socal.ini / settings.bat are seeded on first run, not shipped loose)"

# --- zip the folder into the release artifact + a sha256 --------------------
# Use Python (always present) so this works the same in Git Bash and elsewhere.
VER="$("$PY" -c "import version; print(version.__version__)")"
ZIP="dist/Run8MapHost-${VER}.zip"
echo
echo "Zipping release artifact..."
"$PY" -c "import shutil; shutil.make_archive('dist/Run8MapHost-${VER}', 'zip', root_dir='dist', base_dir='Run8MapHost')"
"$PY" -c "import hashlib; d=hashlib.sha256(open('${ZIP}','rb').read()).hexdigest(); open('${ZIP}.sha256','w').write(d+'  Run8MapHost-${VER}.zip\n'); print('  sha256:', d)"

echo
echo "Done."
echo "  Bundle folder : $DIST"
echo "  Release zip   : $ZIP  (+ .sha256)  <- attach to the GitHub Release (see packaging/RELEASING.md)"
echo "Smoke-test the exe (it seeds config-socal.ini + areas_socal.ini beside itself on first run):"
echo "  cd \"$DIST\" && ./Run8MapHost.exe --host 127.0.0.1 --port 8001 --world \"<autosave path>\""
