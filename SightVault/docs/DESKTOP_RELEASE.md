# SightVault desktop app

This document describes the native desktop packaging layer for macOS on Apple Silicon and Windows x64: how it works, how to build it, and what has and has not been validated.

## Status

| Item | State |
| --- | --- |
| Desktop shell source (`sightvault/desktop/`) | Implemented |
| Unit tests for lifecycle, storage, path safety, desktop routes, setup tasks, demo import (`tests/test_desktop.py`) | Validated on this Mac; see the desktop QA record |
| Desktop dependency locks (`packaging/desktop/requirements-desktop-*.lock.txt`) | Hash-pinned for both targets against the existing runtime constraints |
| macOS `.app` / `.dmg` build | Development build; ad-hoc signed, not notarized |
| Windows onedir / installer build | Scripted, **not yet built or run on Windows** |
| Real embedding, OCR and vision inference inside a frozen build | Release validation is recorded separately in the desktop QA record |
| Code signing / notarization | **None.** macOS builds are ad-hoc signed only; Windows builds are unsigned |

These are development builds. The [validation checklist](#pending-validation) must pass on each target before a public release; native Windows execution and distribution signing remain outstanding. The existing source-based launchers (`Start-SightVault.command`, `Start-SightVault.cmd`, `scripts/launch.py`, `app.py`) and the portable source-and-setup archives are unchanged and remain the validated way to run SightVault.

## Architecture

The desktop app is a thin native wrapper around the existing application. The FastAPI server, the vanilla HTML/CSS/JS UI, the models, the SQLite schema and retrieval are the same code the source launchers run.

- **Window:** [pywebview](https://pywebview.flowrl.com/) shows the UI in the system web view (WKWebView on macOS, Edge WebView2 on Windows). On Windows the shell requires WebView2 and stops with an explanation instead of falling back to a legacy engine.
- **Server:** uvicorn runs inside the same process on a background thread. The shell binds a socket to `127.0.0.1` on a free port *before* starting uvicorn, so the port cannot be taken between selection and listening, and it never listens on another interface. The window opens a "starting" page and loads the app only after `/api/status` answers for this instance.
- **Packaging:** PyInstaller in onedir mode: a `.app` bundle on macOS (wrapped in a `.dmg`), a folder with `SightVault.exe` on Windows (wrapped in an Inno Setup per-user installer when `iscc` is available).
- **Lifecycle:** closing the window stops the setup child process (if one is running), asks uvicorn to finish in-flight requests and run the app's normal shutdown, which cancels indexing jobs and releases the generative model slot, then removes the instance record and releases the lock. Quitting while an image is mid-inference waits for that image to finish. The shell never terminates any process other than the setup child it started itself.
- **Single instance:** an OS advisory lock (`flock` / `msvcrt.locking`) in the per-user folder. A second launch asks the running one, over loopback and with the running instance's token, to bring its window forward, then exits. If the first instance is still starting or does not answer, the second launch exits with a message and stops nothing.

### Storage

All writable state lives outside the application bundle:

| Platform | Library, index, stored copies | Models | Logs |
| --- | --- | --- | --- |
| macOS | `~/Library/Application Support/SightVault/data` | `~/Library/Application Support/SightVault/models` | `~/Library/Logs/SightVault` |
| Windows | `%LOCALAPPDATA%\SightVault\data` | `%LOCALAPPDATA%\SightVault\models` | `%LOCALAPPDATA%\SightVault\logs` |

`SIGHTVAULT_HOME` replaces the base folder (`data`, `models` and `logs` are created inside it). `SIGHTVAULT_DATA` and `SIGHTVAULT_MODELS` override single folders, for example to reuse weights already downloaded for a source checkout. Paths must be absolute; the filesystem root, the home folder itself, existing files and any location inside the installed application (after resolving symlinks) are refused. A read-only model folder is accepted for inference and reported in diagnostics; downloads into it are refused. Uninstalling the app does not delete these folders.

The installed app keeps the existing environment-variable behaviour: `SIGHTVAULT_DEVICE` (default `auto`), `SIGHTVAULT_OCR`, `SIGHTVAULT_VISION` and `SIGHTVAULT_LFM3_THINKING_TOKENS` work as described in the README.

### Security model

- The existing protections are unchanged and apply to every route, including the desktop ones: trusted-host check, refusal of cross-origin state-changing requests, and the `Content-Security-Policy` (`script-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`).
- **No JavaScript-to-Python bridge.** No `js_api` object is exposed. Desktop actions (folder picker, setup tasks, demo import, opening a documentation link, focus) are `/desktop/api/*` routes that require a random per-launch token in a request header. The token is never served over HTTP. The shell injects it, with a small script, only into pages whose URL has the app's own loopback origin; other local processes and pages from other origins cannot obtain it. The only other copy is an owner-only instance record in the per-user folder, used for the single-instance hand-off.
- **Navigation guard.** If the window ever ends up on a page that is not the app's own origin, the shell sends it back to the library and does not inject the token. Links with `target="_blank"` (for example *Open full size*) open in the system browser.
- **External links** opened through the desktop API must be `https://huggingface.co/...` or `https://github.com/...` without credentials or a custom port; everything else is refused.
- **The folder picker** only returns the path the user chose; the existing indexing rules (supported formats, size and count limits, only the chosen folder is read) still apply.
- The web view runs in private mode (no persistent cookies or storage). Downloads from the page are disabled.

### Models and first run

Model weights are never bundled. On first launch, if the required pinned files are missing, the window opens **Setup** (`/desktop/setup`) instead of the library. Setup shows each pinned repository and revision, the size to download, the folders in use and runtime diagnostics, and offers explicit actions:

- **Download required models** — runs the existing pinned downloader (`handoff/download_pinned_models.py`) with the per-user model folder. It downloads the manifest revisions and checks every file's size and SHA-256; a mismatch fails the task. Optional groups (LFM3-VL, Omni) are not downloaded.
- **Verify checksums** — full SHA-256 check of the required files, plus a fingerprint check of any MLX checkpoints present.
- **Prepare Apple Silicon checkpoints** (Apple Silicon only) — runs the existing MiniCPM → MLX conversion into a staging folder and moves a checkpoint into place only if it matches the reviewed fingerprints in `handoff/MAC_CONVERTED_MODEL_MANIFEST.json`. A mismatching result is kept aside for inspection and is not used.
- **Import the demo library** — only shown when the build bundles the reviewed demo and the library is empty (see below).

Each task runs in a **separate child process** of the same executable (`--setup-worker`). Only that child has `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` switched off; the app process keeps them on, so search, OCR and analysis stay offline. The task log is shown in Setup and kept in the setup log folder. Nothing is downloaded or loaded automatically: models still load lazily on first use, exactly as in the source app, and a missing model produces the existing "model files missing" status instead of a fake result. After a download finishes, no restart is needed.

Device reporting is unchanged: the model status shows the device each model actually loaded on, and Setup's PyTorch backend line is labelled as availability only.

### Demo library

A build may bundle the reviewed 62-image public demo (`--demo-db`/`--demo-data-root` at build time). The build stage reuses the portable packager's validation: the database must have the 62 canonical rows with direct 768-dimensional embeddings, and every stored image and thumbnail must match `handoff/DEMO_EVIDENCE_MANIFEST.json`. Only those referenced evidence files are copied, and the bundled database is a SQLite backup switched to rollback-journal mode so it is never written next to.

*Import the demo library* re-checks every sample and evidence hash, refuses unless the library is empty, checks every destination before copying anything, never overwrites a differing file, copies the samples to `data/demo-samples` with their recorded synthetic dates restored, and inserts the rows with their original OCR, vectors and indexed timestamps in one transaction. It does not re-index. The bundled samples are also staged with their recorded dates, so *Load the demo collection* in the library indexes them with the same dates (this path runs the models).

### Headless modes

The same executable supports QA without a window:

```text
SightVault --smoke --report qa.json  start, wait for /api/status, stop; save QA JSON
SightVault --serve [--port N]      run the loopback server until Ctrl-C or SIGTERM
SightVault --print-paths           print the per-user folders as JSON
```

On macOS the executable is `SightVault.app/Contents/MacOS/SightVault`. Use `--report <absolute-file>` for QA JSON: windowed executables may have no stdout. Setup workers also write to a dedicated task log. Use `SIGHTVAULT_HOME` pointing at a fresh disposable folder for QA so a real library is not touched; the single-instance lock is per base folder.

## Build inputs

Desktop builds use a separate environment (`.venv-desktop`), so the runtime `.venv` is not modified. Inputs:

- `requirements-mac.lock.txt` / `requirements-windows.lock.txt` — the existing runtime locks.
- `packaging/desktop/requirements-desktop.in` — pywebview, PyInstaller and its hooks. Resolve a hash-pinned lock per platform against the runtime lock **before the first build**; the build scripts refuse to run without it:

  ```bash
  uv pip compile packaging/desktop/requirements-desktop.in \
    --constraint requirements-mac.lock.txt --python-version 3.12 --python-platform aarch64-apple-darwin --generate-hashes \
    -o packaging/desktop/requirements-desktop-macos.lock.txt
  ```

  The Windows lock uses `--python-platform x86_64-pc-windows-msvc` with `requirements-windows.lock.txt` and `-o packaging/desktop/requirements-desktop-windows.lock.txt`. Do not loosen existing runtime pins.
- `packaging/desktop/stage_resources.py` — stages the manifest-verified samples (with recorded dates), the optional demo, and `build-info.json` (source revision, lock hashes, interpreter, build host).
- `packaging/desktop/SightVault.spec` — PyInstaller spec; collects the UI, the setup page, the model/sample/evidence manifests, the pinned downloader, verifier and MLX converter, and the packages that load code by name at runtime (transformers model modules, sentence-transformers, RapidOCR data, pywebview, MLX/MLX-VLM on macOS). It never collects `.models`, environments or `data/`.

Builds are not bit-for-bit reproducible (PyInstaller and native libraries embed build paths and timestamps); the locks, `build-info.json`, `build-environment.txt` and the artifact `.sha256` record exactly what went in.

## Build commands

The Windows desktop build script requires PowerShell 7. The Windows installer itself does not require Python or PowerShell. A pinned Windows GitHub Actions build is provided in `.github/workflows/desktop.yml`; its native run status must be checked before claiming a Windows pass. The reviewed public build fixture is in `packaging/desktop/public-demo/`.

### macOS (Apple Silicon, native Python 3.12)

```bash
bash packaging/desktop/build_macos.sh
# Optional: bundle the verified public demo
SIGHTVAULT_DEMO_DB=/path/to/verified-demo/sightvault.sqlite3 \
SIGHTVAULT_DEMO_DATA_ROOT=/path/to/verified-demo \
bash packaging/desktop/build_macos.sh
```

Output in `build/desktop/macos/`: `dist/SightVault.app`, `SightVault-<version>-macOS-arm64.dmg` with its `.sha256`, `smoke.json` (headless start/stop of the frozen app in a disposable folder), `codesign.txt`, `build-environment.txt`. The script replaces only `build/desktop/macos/`.

### Windows x64 (native, Python 3.12 via the `py` launcher)

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\packaging\desktop\build_windows.ps1
```

Output in `build\desktop\windows\`: `dist\SightVault\SightVault.exe`, and either `SightVault-<version>-Windows-x64-Setup.exe` (Inno Setup 6 installed) or `SightVault-<version>-Windows-x64-folder.zip`, each with a `.sha256`, plus `smoke.json`. The installer is per-user (`%LOCALAPPDATA%\Programs\SightVault`), needs no administrator rights and leaves the user's library in place on uninstall. Windows 10 hosts may need the Microsoft Edge WebView2 Runtime; Windows 11 includes it.

A macOS build is not evidence for Windows, and the reverse. Each platform must be built and validated on its own hardware.

## Signing and notarization

No signing identity exists in this project. Current builds:

- **macOS:** ad-hoc signed by PyInstaller (required for arm64 code to run). Not notarized. A copy downloaded from the internet carries the quarantine attribute and Gatekeeper will refuse to open it until the user explicitly approves *this app* in **System Settings → Privacy & Security → Open Anyway** (or Control-click → Open). Do not disable Gatekeeper, remove quarantine globally, or turn off SIP to work around this. A copy built locally is not quarantined.
- **Windows:** unsigned. SmartScreen will warn on first run.

To sign later, without storing any secret in the repository:

- **macOS:** install a *Developer ID Application* certificate in the login keychain and create a notarytool profile once (`xcrun notarytool store-credentials <profile> ...`). Then build with `SIGHTVAULT_CODESIGN_IDENTITY="Developer ID Application: …"` and `SIGHTVAULT_NOTARY_PROFILE=<profile>`. PyInstaller signs the collected binaries and the bundle with that identity and `packaging/desktop/entitlements.plist`; confirm the hardened-runtime flag (`flags=0x10000(runtime)`) in `codesign.txt` before notarizing. The script notarizes and staples the app, then signs the DMG. The entitlement set is a starting point and is unverified: confirm every model backend still runs on a signed build and remove anything not needed.
- **Windows:** set `SIGHTVAULT_SIGNTOOL_CERT_SHA1` (certificate thumbprint in the certificate store) and `SIGHTVAULT_TIMESTAMP_URL`; the script signs the executable and the installer with `signtool`.

## Tests

```bash
.venv/bin/python -m pytest tests/test_desktop.py -q
.venv/bin/python -m pytest tests -q
```

`tests/test_desktop.py` covers: per-user locations on each platform, overrides, refusal of relative paths, the root, the home folder, files in place of folders and paths inside the app bundle (including through a symlink), unwritable folders and read-only model folders; exclusive single-instance locking, owner-only instance records, loopback-only hand-off; the loopback server starting on a free `127.0.0.1` port, answering for the right instance and stopping cleanly; token enforcement on every desktop route, the cross-origin guard and CSP on desktop pages, the external-link allow list and setup action validation; setup child processes (offline flags only off in the child, one task at a time, cancellation that leaves unrelated processes running, refusal to download into a read-only folder); model presence checks; MLX fingerprint checks; and demo import (evidence copied and verified, paths rebased, dates and indexed timestamps preserved, full-text index rebuilt, refusal for a non-empty library, no overwrite of a differing file, no partial copy on conflict, tampered samples and path traversal rejected). They use no GUI, network or model weights; they do not establish that a frozen build or a model runs.

## Pending validation

Run on each target platform with the produced artifact, using a disposable `SIGHTVAULT_HOME`, and record the results in a platform QA report:

1. Generate the desktop lock, build, and confirm `smoke.json` reports `ready: true`.
2. Launch the `.app`/`.exe` normally: starting page, first-run Setup, *Open the library*, window close, relaunch, second launch while running (window comes forward, no second process stays), quit during an indexing job.
3. Confirm the desktop script injection works under the app's CSP on WKWebView and WebView2: *Choose folder…* appears and returns the chosen path; *Setup and diagnostics* link appears in Settings. If injection is blocked, the typed folder path must still work.
4. Run *Download required models* and *Verify checksums* in the frozen app; on Apple Silicon also *Prepare Apple Silicon checkpoints* and check the fingerprint result.
5. With `--serve --port <N>` on the frozen build, run real inference end to end: index a folder (EmbeddingGemma image embeddings, MiniCPM-OCR), semantic and exact search, MiniCPM-V analysis; record the device each model reports. Then run `scripts/benchmark.py`-equivalent retrieval against an imported demo and `scripts/qa_browser.py` against the served URL. This step is what establishes that PyInstaller collected everything transformers, sentence-transformers, MLX/MLX-VLM, RapidOCR and ONNX Runtime load at runtime.
6. Confirm offline behaviour of the frozen app after setup (loopback only; same caveat as the source app: an in-process socket guard is not a firewall audit).
7. Demo import in the frozen app, then restart and confirm the library, dates and evidence links.
8. *Open full size* opens the loopback image in the system browser; no other navigation is possible inside the window.
9. Optional runtimes: LFM3-VL and Omni report themselves unavailable with their existing reasons unless their files/runtimes are provided (Omni's llama.cpp runtime is not bundled; set `SIGHTVAULT_LLAMA_SERVER` to an absolute path).

## Known limitations

- Recheck dynamically imported model modules, MLX adapters, metadata and TorchScript source collection whenever runtime dependencies change. A Mac pass does not establish Windows compatibility.
- Desktop integration uses native `run_js` rather than an eval wrapper, preserving the existing CSP. The Mac WebKit window was exercised; Windows WebView2 remains a native validation gate.
- Quitting while an image is being embedded or transcribed waits for that image to finish.
- A second launch during the first one's startup exits with a message instead of focusing the window.
- macOS minimum version is set to 14.0 in the bundle; older systems are not supported by this build.
- The Omni llama.cpp runtime is not bundled; LFM3-VL requires the pinned MLX-VLM build that the macOS lock already provides plus its optional weights.
- Moving the app does not affect the library; stored evidence copies stay in the data folder. Demo samples are copied into the data folder, so their source links survive app updates.
