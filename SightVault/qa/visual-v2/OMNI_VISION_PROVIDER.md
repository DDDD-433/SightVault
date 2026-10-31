# Opt-in LFM3-Omni vision provider

This document covers the optional `SIGHTVAULT_VISION=omni` image-analysis provider. It does not change the default `minicpm` backend or add audio, video, or text-to-speech features to the application.

## Pinned artifacts

| Artifact | Source and pin | Local format |
| --- | --- | --- |
| Model | [`LiquidAI/LFM3-Omni-2B-GGUF`](https://huggingface.co/LiquidAI/LFM3-Omni-2B-GGUF/tree/3d29af2ad166f8299c2d820937e47dc0add20ffc), revision `3d29af2ad166f8299c2d820937e47dc0add20ffc` | `LFM3-Omni-2B-Q4_K_M.gguf`, 1,099,630,080 bytes, SHA-256 `9f8d30e9d4fd7a9349d586e077988229b3ca0a0e4ad730897563d1eccc9893e2` |
| Vision projector | Same model revision | `mmproj-LFM3-Omni-2B-Q4_K_M.gguf`, 291,586,720 bytes, SHA-256 `0a746c0794782a16d4456b230c2a0b5888a2d94a6677211edbed7d7a4177a39a` |
| Runtime source | [`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp/tree/e0f142d5ddc1461ce721644d92af33db23545105), commit `e0f142d5ddc1461ce721644d92af33db23545105` | Locally built `llama-server`; exact binary SHA-256 and `--version` output recorded beside the executable |

The selected Omni model and projector were exercised as a real image pair in the Phase 2 visual arena using this runtime. These pins are the tested Q4_K_M files; this provider does not download or run Bonsai. See [`MODALITY_RESULTS.md`](MODALITY_RESULTS.md) for the experiment's quality, latency, stability, and modality results and their limits.

## One-time setup on macOS

Run setup while online. The normal application launch does not fetch models, compile code, or use remote model endpoints.

```bash
# From the SightVault directory; this fetches only the two pinned model files.
.venv/bin/python scripts/prepare_omni_vision.py --download

# Install isolated build tools (the app environment stays pinned).
# Apple Command Line Tools are required; use xcode-select --install if absent.
.venv/bin/python -m venv .runtime/build-tools
.runtime/build-tools/bin/python -m pip install cmake==4.4.4 ninja==1.13.2

# Fetch and build the exact upstream runtime revision.
mkdir -p .runtime/llama.cpp
git clone --no-checkout https://github.com/ggml-org/llama.cpp .runtime/llama.cpp/source
git -C .runtime/llama.cpp/source checkout e0f142d5ddc1461ce721644d92af33db23545105
.runtime/build-tools/bin/cmake -S .runtime/llama.cpp/source -B .runtime/llama.cpp/build \
  -G Ninja -DCMAKE_MAKE_PROGRAM="$PWD/.runtime/build-tools/bin/ninja" \
  -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_OPENSSL=OFF \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
.runtime/build-tools/bin/cmake --build .runtime/llama.cpp/build --config Release --target llama-server --parallel 6
.venv/bin/python scripts/prepare_omni_runtime.py
```

The model-preparation script checks exact byte counts and SHA-256 values and writes local provenance. Runtime preparation checks the upstream Git origin and commit, executable version string, and executable SHA-256, then writes `sightvault-llama-runtime.json` beside `llama-server`. These files live in ignored model/runtime directories.

The local server build disables the unused embedded web UI and HTTPS support; the app uses loopback HTTP and in-memory image data URLs. This avoids an unnecessary external OpenSSL runtime dependency. Explicit Python setup downloads still use HTTPS.

The first run may use the local Metal build. Start the app with:

```bash
SIGHTVAULT_VISION=omni bash Start-SightVault.command
```

Use `SIGHTVAULT_DEVICE=cpu` to explicitly disable GPU layers. With `auto`, the server requests GPU offload, but SightVault reports a GPU device only if the owned process log confirms layers were offloaded. Otherwise status reports `unverified (no GPU offload evidence)`; it does not assume CPU or GPU execution. The validated behavior in this project is macOS-only; Windows instructions below are build guidance and have not been validated as a SightVault installation.

## One-time setup on Windows

Use a Visual Studio 2022 Developer PowerShell with the C++ desktop workload and CMake tools, as described in the upstream [llama.cpp build guide](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/docs/build.md). From the SightVault directory:

```powershell
& .venv\Scripts\python.exe scripts\prepare_omni_vision.py --download
git clone --no-checkout https://github.com/ggml-org/llama.cpp .runtime\llama.cpp\source
git -C .runtime\llama.cpp\source checkout e0f142d5ddc1461ce721644d92af33db23545105
cmake -S .runtime\llama.cpp\source -B .runtime\llama.cpp\build -DLLAMA_BUILD_SERVER=ON -DLLAMA_OPENSSL=OFF -DLLAMA_USE_PREBUILT_UI=OFF
cmake --build .runtime\llama.cpp\build --config Release --target llama-server --parallel 4
& .venv\Scripts\python.exe scripts\prepare_omni_runtime.py `
  --source .runtime\llama.cpp\source `
  --binary .runtime\llama.cpp\build\bin\Release\llama-server.exe
```

The model files are stored under `.models/lfm3-omni-2b-gguf`. The exact runtime output location can vary with the generator; pass the resulting `llama-server.exe` path to `--binary`. To run with a nondefault location, set `SIGHTVAULT_LLAMA_SERVER` to that executable for the app process. The prepared artifact must report the pinned commit in `--version`. SightVault has not been validated on Windows with this provider, so these commands are source-backed build guidance only.

## Runtime behavior

- The provider starts `llama-server` lazily, owns that child process, binds it only to `127.0.0.1`, and uses an HTTP client with environment proxy settings disabled.
- SightVault sends the image as an in-memory PNG data URL. It accepts no user-supplied image URLs and never uses the server's remote-model option.
- Before sending an image, the provider checks `/props` for the exact requested model path, unique process alias, and vision capability. It also requires the startup log to confirm the requested projector path. A mismatch stops the owned child and returns a clear analysis error.
- One generative model occupies the shared slot. Switching to Omni releases loaded native OCR/vision model references; an OCR request stops the owned Omni process before loading OCR. EmbeddingGemma stays independent and keeps direct normalized 768-dimensional embeddings.
- Model/runtime failures are returned as an analysis-unavailable error. There is no silent MiniCPM fallback; semantic search remains available.
- The common evidence prompt is retained, reasoning is disabled, and the final answer cap is 384 tokens. Status and analysis responses report model ID, revision, quantization, projector, runtime, device evidence, and reasoning configuration.
- App shutdown closes the owned server. The app does not terminate processes on a shared port or any process it did not start.

## Verification

The focused tests use fake local processes and streams; they do not load model weights. They cover configuration, missing artifacts, no-fallback behavior, shared-slot transitions, loopback and data-URL transport, proxy isolation, process cleanup, `/props` mismatch, GPU-offload evidence, and the LFM3 separate thinking/final token boundaries. The visual benchmark and audio/video modality results remain experiment CLI artifacts and do not imply product feature support.

Upstream references: [llama.cpp build guide at the pinned commit](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/docs/build.md), [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/tools/server/README.md), and the [LFM Open License](https://huggingface.co/LiquidAI/LFM3-Omni-2B-GGUF/tree/3d29af2ad166f8299c2d820937e47dc0add20ffc).
