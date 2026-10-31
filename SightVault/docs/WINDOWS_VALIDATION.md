# Windows x64 validation record

The portable Windows source-and-setup scaffold has not been run on a Windows machine. The dependency lock was resolved for CPython 3.12 x64 Windows and includes PyPI hashes; that resolution is not a Windows installation or runtime validation. Optional model groups are downloaded only when selected by name, and the setup script rejects groups whose manifest platform list excludes Windows x64. The current optional Windows download is the LFM3-Omni GGUF group; using it requires a local llama.cpp build and its Windows runtime remains unvalidated. The MLX LFM3-VL group is macOS arm64 only.

On a native Windows x64 PC:

1. Extract `SightVault-Windows-x64.zip` and open PowerShell in `SightVault`.
2. Install dependencies and explicitly download/check weights:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\Setup-Windows.ps1 -DownloadModels
   ```

3. If the public demo is included, dry-run and apply the path relocation before starting the app:

   ```powershell
   .\.venv\Scripts\python.exe handoff\prepare_demo.py
   .\.venv\Scripts\python.exe handoff\prepare_demo.py --apply
   ```

4. Run the native checks:

   ```powershell
   .\Validate-SightVault-Windows.ps1
   ```

The report records OS, architecture, Python and package versions, SQLite FTS5, model checksum results, tests, and loopback API checks for indexing with the preferred MiniCPM OCR backend, semantic search, and the selected MiniCPM-V model/runtime/device. It also records SHA-256 values for the Windows lockfile, retrieval scripts, and portable package manifest when present, along with package or Git source revision when available. The validator then stops its smoke server, verifies the packaged public demo, SQLite-backs it up to a disposable data directory, copies only the 62 assets' hash-verified stored images and thumbnails, and runs `scripts/benchmark.py --offline-check --output <report>`. The benchmark check requires 17/17 retrieval cases, three fresh-pixel/stored-vector cosine checks of at least 0.99, 62 database records, and all four negative queries below the positives. Source file paths in the copied database are left untouched; the benchmark reads its indexed OCR, vectors, and stored evidence from the disposable data directory.

If the package declares no public demo, `public_demo_retrieval` is recorded as `skipped-not-validated` with manual instructions. A skip does not establish retrieval validation. The validator also records the process-level outbound socket/DNS guard; that guard is not a Windows Firewall test. Review the JSON, log and retained `public-demo-retrieval.json` report in `qa/windows-validation/`. Absent a successful native Windows run, Windows support remains unverified.
