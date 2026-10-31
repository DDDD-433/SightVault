# SightVault Phase 2 visual arena v2

**Result:** four local vision backends completed the same 32-case run. The run establishes local image transport, offline operation, and single-process stability; quality review is exploratory and does not justify moving the app default or starting Bonsai 9B.

## Protocol

The arena started from baseline `766027c79d4c6f17ba7b88b1df3bc079dfbbd141` and used experiment code commit `16522549e9388c4eb5cb5b37af05db85faa28504`. The frozen manifest SHA-256 is `a32398bd1d2c2ef981628708672b6476a202a0a7f17963c638d96d1a9ea79523`.

The 32 paired cases preserve the original 16 image hashes, questions, and OCR inputs, then add 16 authored fixtures covering harder terminals, GUI state, dense diagrams and documents, ambiguous product identification, a prompt-injection chart, a small-person photo, and a red/blue image pair. New synthetic fixtures have empty OCR context. Expected facts were written before inference and were not sent as OCR or prompt content. The two source photos in the preserved v1 set use the same saved evidence PNG bytes as v1.

All four candidates ran `--scope all` sequentially on the same Apple M1 Pro with 16 GiB unified memory. Common settings were temperature 0, no thinking, max 384 generated tokens, and a 160-word answer instruction. The image pair ran first with the same question and empty OCR for both colors. The prompt is shared byte-for-byte, with the Omni-required `Respond in text.` card directive supplied as a separate system message. MLX-VLM receives the original image bytes decoded as RGB; llama.cpp receives the original bytes as a data URL.

Each run was wrapped with macOS `sandbox-exec`, denying outbound networking while allowing localhost. Model and projector paths were local, and the llama.cpp requests did not use `-hf`. Native outbound attempts were not instrumented; the operating-system sandbox enforced the denial. Model/projector identity and the MLX image processor were checked. Omni and Bonsai each loaded the expected vision projector.

## Runtime and performance

| Candidate | Pinned artifact and runtime | Artifact bytes | Fresh-process load | First request | Warm median | Warm p95 | Median output rate | Peak RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| MiniCPM-V 4.7 1B | `openbmb/MiniCPM-V-4.7-1B` @ `bb99eab4`; MLX-VLM 0.7.11, runtime commit `918763c6875c80e82f9f48344dfad4763346fea8`; mixed affine 4-bit/BF16, group 64 (checkpoint preserves vision, merger, and sensitive layers in higher precision) | 2,665,920,710 | 2.101 s | 6.073 s | 1.670 s | 2.780 s | 108.934 tok/s | 1.94 GiB |
| LFM3-VL 2B | `LiquidAI/LFM3-VL-2B-MLX-4bit` @ `4146d7fa`; MLX-VLM 0.7.11, runtime commit `918763c6875c80e82f9f48344dfad4763346fea8`; vendor QAD 4-bit, group 32, mixed BF16/8-bit tensors | 1,590,959,088 | 0.758 s | 3.935 s | 2.839 s | 4.415 s | 99.620 tok/s | 2.28 GiB |
| LFM3-Omni 2B | `LiquidAI/LFM3-Omni-2B-GGUF` @ `3d29af2a`; upstream llama.cpp `e0f142d5` (0.8.0-dev, build 12005); Q4_K_M plus Q4_K_M projector | 1,391,216,800 | 0.814 s | 2.676 s | 3.430 s | 4.822 s | 92.921 tok/s | 2.68 GiB |
| Ternary Bonsai 2 4B | `prism-ml/Ternary-Bonsai-2-4B-gguf` @ `8d655716`; same llama.cpp commit; Q2_0 model plus Q8_0 projector | 1,578,998,144 | 1.044 s | 9.018 s | 8.716 s | 11.628 s | 41.951 tok/s | 3.25 GiB |

All candidates completed 32/32 requests with zero request failures, zero OOMs, `finish_reason=stop` on every case, and no token-cap truncations. These 32 requests are not the brief's 50-call stability check. The table records process peak RSS; reliable accelerator-memory accounting was unavailable for the llama.cpp runs. Metrics and full model file SHA-256 values are in each candidate result JSON.

“Fresh-process load” means the model was unloaded before the run and a new runtime process was started. OS filesystem and shader caches were not purged, so these timings are not cold-storage or first-ever Metal compilation measurements.

## Quality evidence

### Coarse deterministic checks

| Candidate | Lexical gates passed | Cases that missed at least one lexical check |
| --- | ---: | --- |
| MiniCPM-V | 26/32 | v1-01, v2-17, v2-20, v2-22, v2-25, v2-28 |
| LFM3-VL | 30/32 | v2-25, v2-27 |
| LFM3-Omni | 30/32 | v1-02, v2-19 |
| Bonsai 2 4B | 31/32 | v2-29 |

These are coarse phrase-presence checks, not semantic accuracy. They can miss correct paraphrases or accept an incorrect answer containing expected words. The small-person token check is especially coarse and does not evaluate negation. The case-level check details are in each `*-coarse-lexical.json`.

### Exploratory AI review

The experiment owner reviewed all 128 final answers with candidate names visible. This was **non-blind AI review**, not human review, independent ground truth, or an accuracy percentage. The four 0–5 dimensions are reported separately as arithmetic means over 32 cases:

| Candidate | Factual correctness | Grounding | Hallucination control | Instruction following |
| --- | ---: | ---: | ---: | ---: |
| MiniCPM-V | 4.281 | 4.438 | 4.188 | 4.344 |
| LFM3-VL | 4.469 | 4.375 | 3.938 | 4.469 |
| LFM3-Omni | 4.562 | 4.625 | 4.406 | 4.594 |
| Bonsai 2 4B | 4.594 | 4.562 | 3.812 | 4.250 |

Full per-case scores and notes are in [`ai-review-results.json`](results/ai-review-results.json); raw answers are preserved in `raw_outputs/`. The separate parent-agent visual review was limited to selected images and is not human ground truth:

- On the GUI triage fixture (`v2-27`), MiniCPM invents a third urgent card and assigns Keyboard focus to the wrong status; LFM3 puts Badge contrast audit in In progress. Omni and Bonsai map both urgent cards correctly.
- Bonsai correctly traces all three foreign-key links in the schema diagram (`v2-26`). Omni omits the `customer_id` link needed for the requested trace; LFM3 confuses `OrderItem`'s primary and foreign keys.
- Omni's small-person answer (`v2-24`) contradicts itself: it says there are no people, then describes figures near the cars.
- All four distinguish the red and blue cups in the identical-question, empty-OCR pair. LFM3 falsely attributes the colors to OCR text that was absent. Bonsai gets the red hue but invents a red rim and rectangular body; the fixture has a gray rim and trapezoidal body.
- All four return the chart total 41 and reject the image-borne override. Mentioning or quoting the note is not a failure. Bonsai's answer contains a typo in its quotation but still answers 41.
- Bonsai declines to identify a brand on the ambiguous laptop but overgeneralizes that no company can be identified “because it is a graphic.”

Other salient output issues include LFM3's original biology answer calling chloroplasts an input, LFM3's false OCR attribution on the empty-OCR laptop and cup cases, Omni's wrong failed command on `v2-19`, and MiniCPM's altered rollback threshold and missed sampled-ID condition on `v2-28`. The raw records are the source for each observation.

## Recommendation

Keep MiniCPM as the app default pending final integration review. Omni is the strongest **provisional optional image candidate** in this round: it leads the non-blind review on grounding and instruction following, performs the GUI spot-check well, completes every request, and has a 3.43-second warm median. MiniCPM remains the latency leader at 1.67 seconds. Omni's separate 50-call serial stability run passed, and bounded local ASR, spoken-image, video, audiovisual, and TTS requests also completed; the three-clip ASR sample still contains ordinary word and proper-name errors, and spoken-image answers add weakly grounded detail. App-level semantic-search concurrency is still untested. Resolve the contradictory tiny-person response and get a blind review of difficult GUI, diagram, document, and photo cases before considering default use. LFM3-VL remains a reasonable specialist comparison, but its normal-mode false OCR attribution lowers grounding confidence.

LFM3-VL thinking mode completed a separate 32-case run with its final answers scored independently. Its non-blind exploratory dimension means were higher than normal mode in this review, with a mean paired latency increase of 1.55 seconds (50.4%). These are separate reviewer scores, not ground-truth or statistically significant quality estimates. See [`STABILITY_AND_THINKING_FOLLOWUP.md`](STABILITY_AND_THINKING_FOLLOWUP.md) and [`MODALITY_RESULTS.md`](MODALITY_RESULTS.md) for the token accounting, rating deltas, and per-modality limitations.

Bonsai 4B does **not** pass the 9B gate. Its slightly higher exploratory factual mean is not a consistent quality win; it has the lowest hallucination-control and instruction-following means, the red-cup shape hallucination, and an 8.72-second warm median with 3.25 GiB peak RSS in the 32-case arena. A separate 50-call serial stability run completed 50/50 with no request errors, OOMs, or token-cap truncations, and all 18 repeated inputs produced identical answer hashes. Its sampled process-tree RSS peaked at 6.99 GB in that run; machine free-memory percentage fell from 83% to 39%, then RSS decreased during repeated calls. This is a substantial observed memory peak, not proof of a leak. The run-level peak is higher than the arena run's peak, so keep both measurements attached to their own runs rather than treating them as interchangeable. Do not download or benchmark 9B on this evidence.

## Limits and reproduction

This was one Mac M1 Pro, single-process, serial inference. Omni and Bonsai completed separate 50-call serial repeatability checks; LFM3-VL thinking mode completed the all-32 comparison, and Omni's bounded audio/video/speech stages completed on local synthetic inputs. Concurrent semantic-search responsiveness, general modality quality, human speech-intelligibility review, blind quality review, and Windows packaging remain unvalidated. The 50-call runs used fresh server processes with OS filesystem and shader caches left intact. These results are a local Mac runtime and image-quality comparison, not a Windows validation. See [`STABILITY_AND_THINKING_FOLLOWUP.md`](STABILITY_AND_THINKING_FOLLOWUP.md), [`MODALITY_RESULTS.md`](MODALITY_RESULTS.md), and [`MODALITY_STRETCH_PLAN.md`](MODALITY_STRETCH_PLAN.md).

Run the same frozen image arena from `SightVault/` with the pinned environment and local model files:

```sh
.venv-visual-v2/bin/python scripts/run_visual_v2_arena.py --candidate minicpm --scope all
.venv-visual-v2/bin/python scripts/run_visual_v2_arena.py --candidate lfm3 --scope all
.venv-visual-v2/bin/python scripts/run_visual_v2_arena.py --candidate omni --scope all
.venv-visual-v2/bin/python scripts/run_visual_v2_arena.py --candidate bonsai --scope all
```

The outputs are summarized in `results/{minicpm,lfm3,omni,bonsai}.json`, with per-case answer records and llama.cpp HTTP response bodies in `raw_outputs/`.
