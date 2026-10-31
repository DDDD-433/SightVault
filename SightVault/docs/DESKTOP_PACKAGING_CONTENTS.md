# SightVault desktop artifact contents

What a built SightVault desktop artifact (macOS `.app`, Windows onedir) contains, what must
never be packaged into one, and how `scripts/verify_desktop_artifact.py` records and re-checks
that afterwards.

## Contents

Both targets wrap the same FastAPI server, UI and SQLite schema in a PyInstaller onedir
bundle with an embedded Python runtime.

| macOS `.app` | Windows onedir | |
| --- | --- | --- |
| `Contents/MacOS/SightVault` | `SightVault.exe` | Entry executable plus the bundled runtime |
| `Contents/Resources/` | `_internal/` | Libraries, app code, data files |
| | | Collection output of `packaging/desktop/SightVault.spec` |
| `Contents/Resources/build-info.json` | `_internal/build-info.json` | Build provenance: source revision, lock hashes, interpreter, build host |
| `Contents/Info.plist` | — | Bundle metadata; `CFBundleShortVersionString` is the app version |

Included in the collection:

- The application server and UI, manifests (`handoff/MODEL_MANIFEST.json`,
  `SAMPLE_MANIFEST.json`, `DEMO_EVIDENCE_MANIFEST.json`), the pinned downloader and verifier,
  the MLX checkpoint preparation script, and optionally the reviewed 62-image demo bundle.
- The embedded runtime: the embedded Python runtime,
  the numeric/NN stack binaries, uvicorn, pywebview, and **RapidOCR's bundled ONNX model
  files** shipped inside `rapidocr_onnxruntime` — OCR detectors and recognisers that are legitimate runtime assets and must be recorded as included.
- Targets other than RapidOCR (EmbeddingGemma image/text embeddings, MiniCPM OCR and
  analysis, MLX checkpoints) load weights downloaded **at first setup** into the per-user
  model folder, Those downloaded checkpoints remain outside the artifact after setup.

Not included by the spec, and never acceptable inside an artifact:

- Externally downloaded Hugging Face checkpoints: a `.models` cache directory,
  `*.safetensors`, or `pytorch_model*.bin` files. This list is deliberately narrow — other
  `.bin` files (runtime or shader binaries) are legitimate.
- Virtual environments (`.venv`), credential files (`.env`, `.netrc`, `.git-credentials`),
  and user data (`data/`).

## Contents manifest

`scripts/verify_desktop_artifact.py` records and re-checks the file-level integrity and
provenance of an existing artifact:

```text
# Record (manifest is written outside the artifact; writing inside a signed bundle
# invalidates the signature)
python scripts/verify_desktop_artifact.py create dist/SightVault.app --out SightVault-manifest.json

# Re-check later, e.g. after packaging or transfer
python scripts/verify_desktop_artifact.py verify dist/SightVault.app SightVault-manifest.json --report verify-result.json
```

`create` walks every regular file and symlink below the artifact root and writes, per entry,
either its relative path, size and SHA-256, or its exact symlink target. A separate
`assets` section records, each with path, size and SHA-256: the `runtime_executables`
(the bundle's entry executables, i.e. the embedded runtime entry points) and the
`rapidocr_onnx_models` bundled with the RapidOCR runtime — both are included assets and are
reported explicitly apart from the full entry list. The manifest records what exists; it
makes no claim about model weights being present or absent. Provenance is read only from
the artifact's own `build-info.json` (platform, source revision) and, on macOS, `Info.plist`
(app version); both files are lstat-checked first, so symlinked or outside-resolving
provenance files are refused before their content is opened, and nothing is invented —
a Windows manifest records `app_version: null` unless build-info carries it.

`verify` re-walks the artifact and compares: provenance (platform, source revision, app
version, build-info hash) and every path, size, hash and symlink target. Failing reasons are
printed as JSON with exit code 1.

Rejections and characteristics:

- Symlinks must target inside the artifact by an exact relative path. Absolute links, links
  escaping the root (lexically or through symlinked ancestors), cyclic and dangling links
  are rejected using only metadata lookups; a target outside the artifact is never read.
  Drive-like (`C:\…`) and UNC targets are rejected. Relative Windows link targets may
  use backslash separators. Windows junctions (where `os.path.isjunction` is available)
  are rejected before traversal rather than followed.
  Symlinked directories are recorded but not followed, so internal files covered by a link
  alias are never double-counted.
- Manifest paths are validated (no absolute paths, `..`, empty components, duplicates,
  drive-colon or stream-style colon names) before any content is hashed, and excessive
  inputs are refused by size limit.
- Excluded content (external HF checkpoints, `.venv`, credential file patterns) stops
  manifest creation rather than being recorded.
- Manifests and verification reports stored inside the artifact are refused before any
  write, since writing inside a signed bundle invalidates the signature.

Exit codes: 0 verified, 1 verification failure, 2 refusal or invalid manifest.

## What this does not verify

The manifest verifies **bytes and build provenance only** — which exact files a given source
revision produced. It does not verify code signing or notarization (see
`docs/DESKTOP_RELEASE.md`), does not make any statement about malware safety, and does not
establish that the frozen app can load models or run inference; weight completeness and real
model execution are validated per target platform in the QA steps of
`docs/DESKTOP_RELEASE.md`. The source-only portable bundles from the earlier migration are a
different artifact shape and are not covered by this manifest format.
