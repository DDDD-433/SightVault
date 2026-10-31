#!/bin/bash
# Build the SightVault macOS arm64 desktop app (.app) and disk image (.dmg).
#
# Uses a separate build environment (.venv-desktop) so the runtime .venv is untouched.
# Without SIGHTVAULT_CODESIGN_IDENTITY the result is ad-hoc signed only: Gatekeeper will
# not open a downloaded copy until the user approves that one app. Nothing here disables
# or weakens Gatekeeper, SIP or quarantine.
#
# Optional environment:
#   PYTHON_BIN                     native arm64 Python 3.12 (default python3.12)
#   SIGHTVAULT_BUILD_VENV          build environment folder (default .venv-desktop)
#   SIGHTVAULT_DEMO_DB / SIGHTVAULT_DEMO_DATA_ROOT  verified public demo to bundle
#   SIGHTVAULT_CODESIGN_IDENTITY   "Developer ID Application: ..." identity in the login keychain
#   SIGHTVAULT_NOTARY_PROFILE      notarytool keychain profile name (requires an identity)
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "Build the macOS app on an Apple Silicon Mac." >&2
  exit 1
fi
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
"$PYTHON_BIN" -c 'import platform,sys; assert sys.version_info[:2]==(3,12), "Python 3.12 required"; assert platform.machine()=="arm64", "native arm64 Python required"'
DESKTOP_LOCK="packaging/desktop/requirements-desktop-macos.lock.txt"
if [[ ! -f "$DESKTOP_LOCK" ]]; then
  echo "Missing $DESKTOP_LOCK. Resolve it from packaging/desktop/requirements-desktop.in (see that file)." >&2
  exit 1
fi
VENV="${SIGHTVAULT_BUILD_VENV:-.venv-desktop}"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
PY="$VENV/bin/python"
"$PY" -m pip install -r requirements-mac.lock.txt
"$PY" -m pip install --require-hashes -r "$DESKTOP_LOCK"

OUT="$ROOT/build/desktop/macos"
# Only this script's own derived output folder is replaced.
rm -rf "$OUT"
mkdir -p "$OUT"
STAGE_ARGS=(--stage "$OUT/stage" --platform macos-arm64)
if [[ -n "${SIGHTVAULT_DEMO_DB:-}" ]]; then
  STAGE_ARGS+=(--demo-db "$SIGHTVAULT_DEMO_DB" --demo-data-root "${SIGHTVAULT_DEMO_DATA_ROOT:?set SIGHTVAULT_DEMO_DATA_ROOT}")
fi
"$PY" packaging/desktop/stage_resources.py "${STAGE_ARGS[@]}"

export SIGHTVAULT_STAGE="$OUT/stage" SIGHTVAULT_TARGET=macos-arm64 PYTHONHASHSEED=0
"$PY" -m PyInstaller --noconfirm --clean --distpath "$OUT/dist" --workpath "$OUT/work" packaging/desktop/SightVault.spec
APP="$OUT/dist/SightVault.app"
VERSION="$("$PY" -c 'from sightvault.desktop import APP_VERSION; print(APP_VERSION)')"

codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dv --verbose=4 "$APP" > "$OUT/codesign.txt" 2>&1 || true

# Headless start/stop check of the frozen app in a disposable per-user folder (no window, no models).
SMOKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/sightvault-smoke.XXXXXX")"
SIGHTVAULT_HOME="$SMOKE_HOME/home" "$APP/Contents/MacOS/SightVault" --smoke --report "$OUT/smoke.json"
rm -rf "$SMOKE_HOME"
"$PY" -c 'import json,sys; report=json.load(open(sys.argv[1])); assert report["ready"], report' "$OUT/smoke.json"

if [[ -n "${SIGHTVAULT_CODESIGN_IDENTITY:-}" && -n "${SIGHTVAULT_NOTARY_PROFILE:-}" ]]; then
  ditto -c -k --keepParent "$APP" "$OUT/notarize.zip"
  xcrun notarytool submit "$OUT/notarize.zip" --keychain-profile "$SIGHTVAULT_NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP"
fi

DMG_ROOT="$OUT/dmg-root"
mkdir -p "$DMG_ROOT"
ditto "$APP" "$DMG_ROOT/SightVault.app"
ln -s /Applications "$DMG_ROOT/Applications"
DMG="$OUT/SightVault-$VERSION-macOS-arm64.dmg"
hdiutil create -volname "SightVault" -srcfolder "$DMG_ROOT" -fs HFS+ -format UDZO -ov "$DMG"
if [[ -n "${SIGHTVAULT_CODESIGN_IDENTITY:-}" ]]; then
  codesign --sign "$SIGHTVAULT_CODESIGN_IDENTITY" --timestamp "$DMG"
fi
(cd "$OUT" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
"$PY" -m pip freeze --all > "$OUT/build-environment.txt"
SIGNED=$([[ -n "${SIGHTVAULT_CODESIGN_IDENTITY:-}" ]] && echo "Developer ID" || echo "ad-hoc (unsigned for distribution)")
echo "Built $DMG"
echo "Signature: $SIGNED. Smoke report: $OUT/smoke.json"
