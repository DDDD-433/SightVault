# LFM3-VL experimental evaluation

## Recommendation

Keep MiniCPM-V 4.7 1B as SightVault's default vision backend. Offer LFM3-VL 2B as an opt-in experimental backend on Apple Silicon. In this 16-image comparison, LFM3 produced richer answers and received a higher reviewer-assessed score, but it was slower after the first load and still made a substantive mistake about the inputs to photosynthesis. The sample is small and the scores are an AI review, not human or ground-truth accuracy. It is not enough evidence to change the default.

MiniCPM remains better for concise answers and correctly named carbon dioxide and water as the photosynthesis inputs. LFM3 transcribed more of the visible CUDA allocation details, but inferred a loss module from `loss.backward()`, and its bridge description overlooked small people by the building. MiniCPM also invented a normalization example and an API-document word count in two different cases; LFM3 did not. These examples show useful strengths and remaining hallucination risk in both models.

## Model and runtime pins

- Model repository: [`LiquidAI/LFM3-VL-2B-MLX-4bit`](https://huggingface.co/LiquidAI/LFM3-VL-2B-MLX-4bit), revision `4146d7fa689833cfa7a06c474e748350b458f41e`.
- Runtime: [`mlx-vlm`](https://github.com/Blaizzy/mlx-vlm) commit `918763c6875c80e82f9f48344dfad4763346fea8`; the pinned commit was verified with real image inference through LFM3-VL, MiniCPM-V 4.7 and MiniCPM-OCR 1.0.
- The LFM3 checkpoint is vendor mixed precision: QAD affine 4-bit with group size 32, 8-bit token embeddings, and BF16 tensors retained by the checkpoint. It is not a uniformly 4-bit model.
- The model card still describes released MLX-VLM support as in progress. SightVault pins the upstream commit above as an experimental adapter; this does not change the stable `requirements-mac.lock.txt` or `.venv`.
- The local snapshot's `model.safetensors` is 1,590,959,088 bytes with SHA-256 `58268dfdf441ae4020d2a52fc97a814645e8a9624946e2d490403d4dcd71a255`. The full required snapshot is checked file-by-file by the downloader and has a `source.json` with repository, revision, runtime commit and verified file hashes.

The experimental runtime uses MLX on the Metal GPU. This is distinct from PyTorch MPS, which continues to handle EmbeddingGemma in FP32. MiniCPM-OCR, MiniCPM-V and LFM3-VL share one generative slot; the implementation unloads the previous generator before loading another. EmbeddingGemma keeps its independent lock and direct normalized 768-dimensional image and query embeddings.

## Separate setup and run

First prepare the existing MiniCPM/EmbeddingGemma models with the normal Mac setup. Do not install the experimental lock into `.venv`.

```bash
python3.12 -m venv .venv-lfm3
.venv-lfm3/bin/python -m pip install -r requirements-lfm3.lock.txt
.venv-lfm3/bin/python scripts/download_models.py --vision lfm3
```

The model downloader is the only setup command that fetches the LFM3 snapshot. It verifies every Hugging Face file object against its pinned size and hash and records `source.json`. Runtime inference uses only the local snapshot, with Hugging Face and Transformers offline flags enabled.

Start LFM3 on an available loopback port:

```bash
SIGHTVAULT_VISION=lfm3 \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
.venv-lfm3/bin/python app.py --port 7864
```

To use MiniCPM in the same experimental environment, restart with `SIGHTVAULT_VISION=minicpm`. If the vision variable is omitted, MiniCPM remains the default. Status, API results and the UI identify the selected model and runtime. There is no automatic switch to the other vision model when one fails.

The `requirements-lfm3.lock.txt` file is a copy of the pinned Mac environment with only MLX-VLM overridden to the experimental commit. Recreate `.venv-lfm3` from that lock after updates; leave `.venv` and `requirements-mac.lock.txt` untouched.

## Paired image benchmark

The benchmark used 16 representative images from the copied synthetic demo library: five terminal screenshots, three lecture slides, two receipts, two public photos, one task board, one API design note, and two object illustrations. Both backends received the exact same image bytes, question, stored OCR text, maximum of 384 new tokens, and `enable_thinking=False`. Requests were serialized in separate worker processes so the two VLMs were never loaded together. Only final answer text was recorded; benchmark output does not copy raw OCR transcripts.

Run the benchmark with:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
.venv-lfm3/bin/python scripts/benchmark_vision_backends.py \
  --data-dir "$PWD/data" --models-dir "$PWD/.models" \
  --output qa/vision-benchmark.json
```

| Measure | MiniCPM-V 4.7 1B | LFM3-VL 2B |
| --- | ---: | ---: |
| Model load | 2.285 s | 0.937 s |
| Median warm answer latency (15 cases) | 1.564 s | 2.792 s |
| Process peak RSS | 2.483 GB | 2.365 GB |
| MLX peak allocation | 3.503 GB | 2.643 GB |
| Whole-machine free memory after run | 37% | 46% |
| Reviewer-assessed mean score (16 cases, 1–5) | 4.06 | 4.56 |

Process RSS and MLX allocation are separate measurements and should not be added together. Whole-machine free memory is the `memory_pressure -Q` snapshot after each backend run. The benchmark is a single serialized comparison on an M1 Pro with 16 GiB unified memory, so timing and memory figures are observations from this machine rather than general performance guarantees. Warm LFM3 answers were about 1.8× slower by median in this run, while its recorded MLX peak was lower.

### Reviewer assessment

Codex/Luna assigned a 1–5 score to each answer after inspecting its source image. This is reviewer-assessed AI evaluation, not human review or ground truth. A 5 means accurate and sufficiently complete with no material unsupported claim; 4 allows a minor omission or inference; 3 means mostly correct with a meaningful omission or questionable detail; 2 marks a material hallucination or reliability issue; 1 is mostly incorrect or nonresponsive.

| Case group | MiniCPM score / max | LFM3 score / max |
| --- | ---: | ---: |
| Terminals | 19 / 25 | 24 / 25 |
| Slides | 12 / 15 | 13 / 15 |
| Receipts | 10 / 10 | 10 / 10 |
| Public photos | 8 / 10 | 8 / 10 |
| Task board | 5 / 5 | 5 / 5 |
| API design note | 2 / 5 | 5 / 5 |
| Object illustrations | 9 / 10 | 8 / 10 |
| **Total** | **65 / 80** | **73 / 80** |

Parent Codex independently reviewed the CUDA screenshot, photosynthesis slide and bridge photo. That review confirmed LFM3's visible CUDA allocation figures with a mild inference about the loss module; its substantive photosynthesis input error; and its richer bridge details paired with an omission of small people near the building. MiniCPM named the photosynthesis inputs correctly and noted vehicles visible beside the bridge.

## Verification and evidence

The browser/API runs checked the exact `instance_root` against this worktree and the selected model ID before recording results. Tracked QA summaries use `.` for the verified worktree root and repo-relative model/database paths, so they do not expose a local username or checkout path.

- `qa/lfm3-runtime-proof.json`: offline-guarded, repeated real-image LFM3 inference on `terminal_eresolve_01.png`, with image/model hashes, exact runtime revision, final answers, RSS and MLX memory readings. Reproduce with:

  ```bash
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
  .venv-lfm3/bin/python scripts/qa_lfm3_runtime.py \
    --image samples/terminal_eresolve_01.png --repeat 2 --max-tokens 384 \
    --output qa/lfm3-runtime-proof.json
  ```

- `qa/vision-benchmark.json`: paired questions, input image hashes, final outputs, timings, memory and reviewer-assessed score notes.
- `qa/lfm3-isolated-retrieval.json`: 17/17 retrieval cases on the copied 62-image database; three fresh image embeddings matched stored vectors at cosine 0.9999–1.0000; four negative semantic queries ranked below the positives.
- `qa/offline-real-lfm3.json` and `qa/offline-real-minicpm.json`: real end-to-end semantic search, MiniCPM-OCR and selected vision analysis with offline flags and an in-process socket guard. The guard is not an OS firewall test.
- `qa/model-recovery-experimental.json`: invalid MiniCPM-OCR weights in a disposable directory triggered the labeled RapidOCR fallback; restoring the local checkpoint restored MiniCPM-OCR, MiniCPM-V analysis and embedding/indexing. This is a model-load failure test, not a real OOM injection.
- `tests/test_vision_backends.py`: mocked Metal OOM and downstream-exception cleanup assertions, empty-final-answer rejection, route/status identity, and embedding/search recovery. No real GPU OOM was induced.
- `qa/browser-qa-experimental-minicpm.json` and `qa/browser-qa-experimental-lfm3.json`: 37/37 browser checks per backend. Corresponding `qa/normal-csp-lfm3-experiment-*.json` reports have 10/10 checks; `qa/concurrency-experimental-*.json` reports have 40/40 API checks per backend, with 20 semantic searches finishing before concurrent analysis. Screenshots remain available locally in `qa/screenshots/lfm3-experiment-minicpm/` and `qa/screenshots/lfm3-experiment-lfm3/`; those directories are ignored because the detail view includes a local source path.
- Full test command: `.venv-lfm3/bin/python -m pytest tests -q` — **140 passed, 1 expected xfail in 5.24s**.

RECOMMENDATION: Keep MiniCPM-V as default; retain LFM3 as optional backend
