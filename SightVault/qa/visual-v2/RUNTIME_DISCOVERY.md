# Visual v2 model and runtime discovery

Research and local smoke evidence, updated 2026-10-30. Source/model preparation is separate from inference. The full paired arena is still pending the parent-assigned heavy-run slot.

## Bonsai 2 4B Q2_0

- Exact repository: [`prism-ml/Ternary-Bonsai-2-4B-gguf`](https://huggingface.co/prism-ml/Ternary-Bonsai-2-4B-gguf), pinned revision `8d6557168168f91ea6697a4a49ed2ef622b53adb`.
- Language model: `Ternary-Bonsai-2-4B-Q2_0.gguf`, 1,212,103,520 bytes, SHA-256 `64a5183b44921bdb5ac4a6f5f92007821d6ebe5c66997d3c147fd214ab9433d4`.
- Vision projector: `Ternary-Bonsai-2-4B-mmproj-Q8_0.gguf`, 366,894,624 bytes, SHA-256 `db939d5f9b7014137810205e91d05e5f54afd72b0b3af9d33bd7d82f7906a55d`.
- The current model card says upstream llama.cpp supports Q2_0 and rejects the fork-specific PQ2_0/PTQ1_0 packings; Q8_0 is the recommended vision projector. The upstream Hadamard runtime landed in [llama.cpp PR #29600](https://github.com/ggml-org/llama.cpp/pull/29600), merge commit [`a9d27ac693ff0a1d3cba449c912c2b891904a4c7`](https://github.com/ggml-org/llama.cpp/commit/a9d27ac693ff0a1d3cba449c912c2b891904a4c7). The repo card contains both the older Prism fork instructions and the later upstream-Q2_0 qualification; this experiment uses only the latter packing.
- No mandatory fork is used. The Q2_0 model and Q8_0 projector are passed as local paths to upstream `llama-server`.

## LFM3-Omni-2B

- Exact image GGUF repository: [`LiquidAI/LFM3-Omni-2B-GGUF`](https://huggingface.co/LiquidAI/LFM3-Omni-2B-GGUF), pinned revision `3d29af2ad166f8299c2d820937e47dc0add20ffc`.
- Language model: `LFM3-Omni-2B-Q4_K_M.gguf`, 1,099,630,080 bytes, SHA-256 `9f8d30e9d4fd7a9349d586e077988229b3ca0a0e4ad730897563d1eccc9893e2`.
- Vision projector: `mmproj-LFM3-Omni-2B-Q4_K_M.gguf`, 291,586,720 bytes, SHA-256 `0a746c0794782a16d4456b230c2a0b5888a2d94a6677211edbed7d7a4177a39a`.
- Upstream support for LFM3-Audio and LFM3-Omni merged in [llama.cpp PR #31257](https://github.com/ggml-org/llama.cpp/pull/31257) as [`19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4`](https://github.com/ggml-org/llama.cpp/commit/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4) on 2026-10-27. The upstream [b11941 prerelease](https://github.com/ggml-org/llama.cpp/releases/tag/b11941) includes this change. The Hugging Face README currently exposes an older pending-merge warning; the pinned local runtime is newer than that warning and contains the merge commit, as verified by Git ancestry.
- LFM3-Omni is non-reasoning; the model card specifies `--reasoning off` and recommends greedy text generation at temperature 0. The image arena sets reasoning off and holds the final-token cap at 384. Audio/speech bonuses are not included in the visual score. Video is not supported by llama.cpp in the documented/current path and is not part of this run.

## Shared upstream runtime

- Repository: [`ggml-org/llama.cpp`](https://github.com/ggml-org/llama.cpp), source commit `e0f142d5ddc1461ce721644d92af33db23545105` (2026-10-29). This commit contains both support merges above.
- Local build: `0.8.0-dev (build 12005, commit e0f142d5d)`, AppleClang `21.0.0.21000101`, Darwin `arm64`; Release, `GGML_METAL=ON`, server/tools enabled, examples/tests/UI disabled.
- Binary SHA-256: `llama-server` `2d973f9be4949e8df3f66ac3a45808ef01e5d8325c5f236a6151cd7924302a13`; `llama-cli` `e782d9f498dd05515e9f65a01c6a1a8d4e05906913ea2cd77de5a4e309944cd8`; `llama-mtmd-cli` `2c5ff8f5891d4c3ef8ffb974e60f5a446add0cf0f5adffe151964cb9957f7c4f`.
- The current [upstream Windows build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md) documents x64 builds with Visual Studio 2022/MSVC or LLVM/Clang and CMake. This is a source-backed build route only; Windows inference has not been run. The Omni card also lists llama.cpp-based Windows use. No Mac results are used to claim Windows validation.

## Actual first-pixel image transport smoke

Two separate `llama-server` processes were run and exited under the approved slot, one per candidate. Both accepted a local image data URL and loaded the requested projector. This proves transport/runtime feasibility on this Mac, not image-grounded quality:

| Candidate | HTTP | Ready | Request | Peak RSS | Output |
| --- | ---: | ---: | ---: | ---: | --- |
| Bonsai 2 4B Q2_0 + Q8_0 projector | 200 | 7.262 s | 6.386 s | 2,322,694,144 bytes | Identified the React 19 / legacy-widget 2.3.1 conflict |
| LFM3-Omni 2B Q4_K_M + Q4_K_M projector | 200 | 1.279 s | 3.235 s | 1,813,331,968 bytes | Identified the ERESOLVE/peer dependency conflict |

- Both outputs ended with `finish_reason=length` at 96 new tokens. This was a bounded transport smoke, not the arena's 384-token configuration and not a successful quality pass.
- The test included the stored OCR transcription, so its answer could be text-derived. The full arena begins with two identical-question red/blue cup images and empty OCR; that pair is the first image-dependence diagnostic. The full harness also verifies `/props` model identity and the exact projector path in the server log.
- The HTTP server had a loopback listener before/after the request. Native outbound attempts were not instrumented. The enforced isolation was `/usr/bin/sandbox-exec` with `deny network-outbound` and `allow network-outbound` to localhost; it is described as a macOS sandbox, not a firewall. No `-hf` download shortcut was used.
- Raw smoke JSON and HTTP outputs are in `raw_outputs/`; local server logs and model/environment/cache files are excluded from the commit.

## Prepared artifacts and candidate set

Selected Omni and Bonsai model/projector files total 2,970,214,944 bytes (about 2.77 GiB), before runtime and environment files. MiniCPM-V and LFM3-VL control model directories are read-only symlinks to the existing local stores; no target model directories were modified. The paired harness uses these four candidates on the same 32 cases, image bytes, user questions, OCR contexts, 384-token cap, 160-word instruction, temperature 0, and no-thinking setting. Omni receives the model-card-required `Respond in text.` system directive; this is recorded as a small prompt-format difference.

The exact revisions, file hashes, runtime build and commands are preserved by `scripts/prepare_visual_v2_models.py`, the pinned local source tree, the per-candidate result JSON, and this note. The actual full arena remains pending. No 9B, repeated-call stability, or Omni audio/video run is included in the initial arena slot.
