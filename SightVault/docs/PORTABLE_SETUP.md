# SightVault portable source-and-setup packages

These platform archives contain the same Python application and SQLite schema plus setup scripts. They are **portable source-and-setup bundles**, not native executables. Model weights and Python environments are excluded. Windows has not been validated on a Windows host by this scaffold.

The app runs locally after setup. Dependency installation and an explicitly requested model download need internet access. At runtime the launchers set the Hugging Face offline flags; inference does not silently download models or use a cloud API. The model manifest pins each source revision and required file hash. Optional model groups remain optional unless explicitly requested.

## macOS arm64

1. Extract `SightVault-macOS-arm64.zip` and open Terminal in the extracted `SightVault` folder.
2. Verify the package before editing it, then install the pinned Mac dependencies:

   ```bash
   python3.12 handoff/verify_transfer.py
   bash Setup-Mac.sh
   ```

3. Download the pinned weights explicitly while online and verify their SHA-256 values:

   ```bash
   .venv/bin/python handoff/download_pinned_models.py
   .venv/bin/python handoff/verify_transfer.py --models
   ```

4. Only when the selected manifest requires the MiniCPM MLX variants, create the local checkpoints while online:

   ```bash
   .venv/bin/python scripts/prepare_mac_models.py --only minicpm-ocr
   .venv/bin/python scripts/prepare_mac_models.py --only minicpm-v
   ```

   These are explicit setup conversions. Do not run them for models not selected by the package manifest.
   After both selected MiniCPM conversions, check them against the reviewed local conversion fingerprints:

   ```bash
   .venv/bin/python scripts/verify_mac_conversions.py
   ```
5. Optional image specialist: install the named LFM3-VL snapshot only if you intend to select it:

   ```bash
   .venv/bin/python handoff/download_pinned_models.py --only lfm3-vl-2b-mlx-4bit
   .venv/bin/python handoff/verify_transfer.py --models --only lfm3-vl-2b-mlx-4bit
   ```

   Run it with `SIGHTVAULT_VISION=lfm3`. It is an explicit opt-in image provider; thinking remains off by default.
6. Optional LFM3-Omni image analysis requires only its two pinned model files and a local llama.cpp build. Download and verify the named group explicitly while online:

   ```bash
   .venv/bin/python handoff/download_pinned_models.py --only lfm3-omni-2b-gguf
   .venv/bin/python handoff/verify_transfer.py --models --only lfm3-omni-2b-gguf
   ```

   Build the pinned upstream runtime locally:

   ```bash
   mkdir -p .runtime/llama.cpp
   git clone --no-checkout https://github.com/ggml-org/llama.cpp .runtime/llama.cpp/source
   git -C .runtime/llama.cpp/source checkout e0f142d5ddc1461ce721644d92af33db23545105
   cmake -S .runtime/llama.cpp/source -B .runtime/llama.cpp/build \
     -DGGML_METAL=ON -DLLAMA_BUILD_SERVER=ON
   cmake --build .runtime/llama.cpp/build --config Release --target llama-server --parallel 6
   .venv/bin/python scripts/prepare_omni_runtime.py
   ```

   Start its image-analysis provider with `SIGHTVAULT_VISION=omni`. Omni is opt-in and image-only in SightVault; audio, video, speech recognition and text-to-speech experiments do not add those product features. Windows setup instructions are build guidance only; neither this provider nor the portable scaffold has been validated on Windows.
7. If this is the prepared public 62-image demo, stop any running instance and relocate the copied demo paths:

   ```bash
   .venv/bin/python handoff/prepare_demo.py
   .venv/bin/python handoff/prepare_demo.py --apply
   ```

   The first command is a dry run. The apply command backs up the SQLite database and preserves its indexed vectors and timestamps.
8. Start the app offline:

   ```bash
   HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 bash Start-SightVault.command
   ```

   Open the URL printed by the launcher (default `http://127.0.0.1:7861/`). Stop the app with Control-C in its foreground terminal, or use the process manager that launched it.

The Mac installer requires native Python 3.12. On Apple Silicon, verify that `python3.12` reports `arm64`, not a Rosetta interpreter. A clean package install does not prove that a particular model backend runs on the host; use the platform QA report for model-specific evidence.

## Windows x64

1. Install 64-bit CPython 3.12 and the Python Launcher from the official Windows installer. Open PowerShell in the extracted `SightVault` folder.
2. Install the hash-pinned Windows dependencies. Add `-DownloadModels` to fetch the manifest-pinned weight files explicitly while online:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\Setup-Windows.ps1
   # Or, while online, explicitly download and verify the pinned model files:
   .\Setup-Windows.ps1 -DownloadModels
   ```

   The second form downloads required model groups at the exact revisions listed in `handoff/MODEL_MANIFEST.json` and checks their file hashes. The first form installs dependencies without downloading weights. Optional model groups are skipped by default. Name a specific optional group to add it, for example `-DownloadModels -AdditionalModels <manifest-name>`; do not request every optional group as a batch.
3. If the prepared public 62-image demo is present, relocate it before launch. Stop any running instance first:

   ```powershell
   .\.venv\Scripts\python.exe handoff\prepare_demo.py
   .\.venv\Scripts\python.exe handoff\prepare_demo.py --apply
   ```

4. Start offline:

   ```powershell
   .\Start-SightVault.cmd
   ```

   Open `http://127.0.0.1:7860/`. Close the launcher window to stop its foreground server.
5. For native Windows validation, run the separate harness after model installation:

   ```powershell
   .\Validate-SightVault-Windows.ps1
   ```

The harness checks native OS/interpreter architecture, dependency versions, SQLite FTS5, model checksums, the test suite, and real local startup, MiniCPM OCR, semantic search, and MiniCPM-V analysis with backend/model/runtime/device identity. It stops that smoke server before a separate offline 17-case retrieval benchmark. When the public demo is included, the validator checks the 62-row package data, SQLite-backs it up to a disposable data root, copies only referenced stored images/thumbs after package-manifest SHA verification, and requires 17/17 retrieval, negative-query separation and fresh-image/stored-vector cosine proof. The original database and file paths are not modified. If no public demo is bundled, retrieval is explicitly marked `skipped-not-validated` and the JSON includes manual instructions. It writes a JSON report and text log, with the full retrieval report retained separately. Any failed check returns a nonzero exit code. A source archive, lockfile resolution, or successful static inspection does not count as a Windows runtime pass.

## Integrity and contents

Each archive has an internal `handoff/PORTABLE_PACKAGE_MANIFEST.json` with the included file sizes and hashes, plus an adjacent `.sha256` file covering the complete ZIP. Verify both before extraction:

```bash
python3 scripts/verify_package_integrity.py SightVault-macOS-arm64.zip
```

The package builder accepts an explicit source root, model manifest, QA allowlist, and verified 62-image demo database/data directory. It uses SQLite's backup API and includes only the indexed demo images and thumbnails referenced by those 62 rows. The package manifest records each pinned model group's revision, required/optional status, file count and expected download bytes; no model weight bytes are included. It never includes `.models`, virtual environments, arbitrary `data/` content, raw logs, or local/private database rows.

Before packaging, the builder checks the explicit database against the canonical 62-image sample manifest and `handoff/DEMO_EVIDENCE_MANIFEST.json`. That reviewed anchor pins the exact bytes of the 60 unique normalized PNGs and WebP thumbnails after their decoded content was checked against the source samples and the ingestion thumbnail policy. Missing, extra, symlinked, or changed evidence paths stop the build. The manifest is included in the bundle and its SHA-256 is recorded in the package manifest.

To build after the integration branch has been reviewed, run one command per target and point `--demo-db` and `--demo-data-root` at the verified public demo copy:

```bash
python3 scripts/build_portable_package.py \
  --source-root /path/to/reviewed/SightVault \
  --platform macos-arm64 \
  --model-manifest /path/to/reviewed/SightVault/handoff/MODEL_MANIFEST.json \
  --qa-allowlist /path/to/reviewed/qa-allowlist.json \
  --demo-db /path/to/verified-demo/sightvault.sqlite3 \
  --demo-data-root /path/to/verified-demo
```

Use `--platform windows-x64` for the Windows archive. This scaffold intentionally does not build either release archive from its staging checkout.
