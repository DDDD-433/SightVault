# Omni, thinking mode, and modality follow-up results

This note reports the completed thinking-mode and Omni modality probes as separate evidence from the 32-case, four-candidate image arena. It is a local Mac experiment, not a production-readiness or Windows compatibility claim.

## Shared runtime and offline controls

The Omni probes used the local `LiquidAI/LFM3-Omni-2B-GGUF` snapshot at revision `3d29af2ad166f8299c2d820937e47dc0add20ffc`, Q4_K_M model and matching Q4_K_M projector. The runtime is upstream llama.cpp commit `e0f142d5ddc1461ce721644d92af33db23545105` (build 12005 / `0.8.0-dev`); the llama-server binary SHA-256 is `2d973f9be4949e8df3f66ac3a45808ef01e5d8325c5f236a6151cd7924302a13`. No model or audio weights were downloaded for these stages. Runs used an Apple M1 Pro with 16 GiB unified memory and the macOS outbound-deny sandbox `(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))`; localhost was allowed. Native socket attempts were not instrumented; the OS sandbox enforced the denial. Each heavy stage ran as a separate server process, which exited at stage completion.

The pinned [Omni GGUF model card](https://huggingface.co/LiquidAI/LFM3-Omni-2B-GGUF) describes image, audio, video, ASR, and speech paths. The upstream [llama.cpp LFM3 integration](https://github.com/ggml-org/llama.cpp/pull/31257) and its [server request documentation](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/tools/server/README.md) informed the local request path. The model card and runtime docs establish the intended interfaces; the results below establish only the bounded local examples that actually ran.

## LFM3-VL thinking mode

All 32 frozen image cases ran with the exact normal-mode image bytes, questions, OCR context, temperature, and answer limit. The local MLX-VLM runtime was version `0.7.11`, commit `918763c6875c80e82f9f48344dfad4763346fea8`; the checkpoint was `LiquidAI/LFM3-VL-2B-MLX-4bit` revision `4146d7fa689833cfa7a06c474e748350b458f41e`.

| Measure | Normal mode | Thinking mode |
| --- | ---: | ---: |
| Usable final answers | 32/32 | 32/32 |
| Mean request latency | 3.070 s | 4.617 s |
| Paired mean latency increase | — | 1.547 s (+50.4%) |
| Mean final-answer tokens | — | 148.38 |
| Mean hidden-thought token count | disabled | 123.25 / 128 cap |
| Thought terminator | — | forced at 128 tokens in 28/32; natural in 4/32 |
| Incomplete/truncated final answers | 0 | 0; the independent 384-token final cap was not reached |

Hidden reasoning text and token IDs were never persisted, displayed, or reviewed. The full run and token accounting are in [`thinking/runs/lfm3-thinking-all-20261030T210520891236Z-731645/`](thinking/runs/lfm3-thinking-all-20261030T210520891236Z-731645/), with deterministic checks in that directory's `final-answer-coarse-score.json`.

The separately labeled, non-blind owner AI review scored final answers only. The 0–5 dimensions are not accuracy percentages or human judgments.

| Dimension | Normal LFM3-VL | Thinking mode | Difference |
| --- | ---: | ---: | ---: |
| Factual correctness | 4.469 | 4.969 | +0.500 |
| Grounding | 4.375 | 4.812 | +0.437 |
| Hallucination control | 3.938 | 4.625 | +0.687 |
| Instruction following | 4.469 | 4.875 | +0.406 |

The full case ratings and normal-mode answer hashes are in [`results/lfm3-thinking-ai-review-results.json`](results/lfm3-thinking-ai-review-results.json). The review is exploratory, identity-visible, and not independent ground truth. Thinking-mode final answers correct several normal-mode misses on the photosynthesis inputs, both in-progress task numbers, ERD cardinality, small people, and GUI urgent-card columns. The foreign-key diagram answer gives the right path and correctly names `order_id` and `product_id` as both the `OrderItem` composite key and foreign keys. A remaining weak detail is the dense terminal's “MPI (MPS)” wording; the intended backend is MPS. Two object-study answers exceeded the requested 160-word limit (162 and 169 words); their instruction-following ratings reflect that.

## Audio recognition (ASR)

Three authored synthetic 16 kHz mono PCM16 clips were sent to the local `/v1/audio/transcriptions` path. The run completed 3/3 requests without errors, with mean latency 0.412 s and sampled server-tree peak RSS 1.747 GB. Literal lower-case alphanumeric-token WER is retained unchanged: the per-case values were 0.500, 0.625, and 0.357, for the recorded mean 0.494 (micro WER 0.500; 18 errors / 36 reference words).

| Clip | Literal reference → hypothesis result | Number/time slot result |
| --- | --- | --- |
| `asr-01` | “The green status light is steady” → “The green stat is light as steady” (3 errors / 6 words) | No numeric slots; genuine word recognition errors remain. |
| `asr-02` | Correctly reads ticket `2048` and queue `Q731`; “Mira Chen” is rendered “Merichen.” | Both numeric IDs correct. Proper-name error is preserved; no name normalization. |
| `asr-03` | Writes `$42` for “forty two dollars” and `6.15` for “six fifteen.” | Amount and time values both correct. |

Fixture-specific number/time normalization gives aggregate WER 0.214 (6 errors / 28 words). It canonicalizes only the authored number/time forms, leaves Mira Chen/Merichen uncorrected, and leaves the Q/queue spelling difference visible. The separate deterministic number/time slot score is 4/4. These three short synthetic examples do not estimate general ASR quality. Details and exact input hashes are in [`results/omni-asr-number-normalization.json`](results/omni-asr-number-normalization.json) and [`modalities/runs/omni-asr-20261030T210817967496Z-3a530e/`](modalities/runs/omni-asr-20261030T210817967496Z-3a530e/).

## Spoken image questions

The model accepted 3 spoken questions paired with the same questions as text controls: 6/6 requests completed with HTTP 200 and no errors. Mean latency was 2.672 s; sampled server-tree peak RSS was 1.947 GB. Both input paths correctly identified the red cup, blue cup, and tiny people near the left-side building. This checks transport and basic task response only, not a broad speech-understanding score.

The spoken-input answers were more elaborate than the text controls and included weak visual details: the red-cup answer calls the platform a saucer and says its rim is white; the blue-cup answer describes a lighter blue/white rim area; the bridge answer speculates on the building identity and clothing. Treat these as grounding concerns, not evidence of reliable spoken-image QA. The paired raw answers are in [`modalities/runs/omni-spoken-image-20261030T211019615960Z-d28893/`](modalities/runs/omni-spoken-image-20261030T211019615960Z-d28893/).

## Video and audiovisual question

The local six-second synthetic MP4 is H.264/AAC, 24 video frames at 4 fps, with a separately authored soundtrack phrase. Its SHA-256 is `a06c720a08eeeb8afccc7814e19d84df4b02808c67a4f41997366881d51bb856`. The video-stage server used `--video-fps 2` and context 8192. Review of the pinned runtime predicts 12 sampled frames, evenly spaced and grouped for Omni; this count is source-derived, not directly instrumented in the response. See [`VIDEO_RUNTIME_CONTEXT_REVIEW.md`](VIDEO_RUNTIME_CONTEXT_REVIEW.md) and the pinned [frame-sampling source](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/tools/mtmd/mtmd.cpp#L2557-L2583).

The visual-only call returned the correct motion: the red circle moves left to right along the top line (1/1, 2.601 s, sampled peak RSS 1.854 GB). A second call on the same MP4 asked both which shape moved and its direction, plus the exact soundtrack phrase. The model answered “The red circle moves to the right along the top line” and returned the exact phrase, “This is a short synthetic benchmark clip.” That reference phrase was fixture metadata only and was not included in the request; this is an actual audiovisual pass for one local clip (1/1, 2.483 s, sampled peak RSS 1.855 GB). The runtime log warns that audio-input quality is experimental/reduced; a single fixture does not establish robust video/audio understanding. Raw outputs: [`video`](modalities/runs/omni-video-20261030T211449807662Z-387ee2/) and [`video-audio`](modalities/runs/omni-video-audio-20261030T211829037444Z-0fcb71/).

## Speech generation and ASR roundtrip

The `/tts` stage completed 3/3 prompts and decoded all WAVs: PCM16 mono at 24 kHz, durations 2.96 s, 3.84 s, and 3.84 s. Mean generation latency was 1.126 s; sampled server-tree peak RSS was 1.753 GB. The exact files and hashes are preserved in [`modalities/runs/omni-tts-20261030T211542606296Z-0fa4ab/`](modalities/runs/omni-tts-20261030T211542606296Z-0fa4ab/).

Each generated WAV was locally converted by ffmpeg to mono PCM16 16 kHz and sent to the ASR path without the source text in the request. The three-call roundtrip completed 3/3 with mean latency 0.325 s, sampled server-tree peak RSS 1.746 GB, and literal mean WER 0.0333. Two hypotheses exactly matched their prompt text. The third says “SiteVault” for “SightVault,” a proper-name spelling error. This is an automated self-ASR proxy using the same model/runtime, not human listening, independent intelligibility testing, or a speech quality rating. WAVs, converted inputs, hashes, prompts, and responses are in [`modalities/runs/omni-tts-asr-roundtrip-20261030T212212250616Z-eae029/`](modalities/runs/omni-tts-asr-roundtrip-20261030T212212250616Z-eae029/).

## Preserved harness failures and limits

Before the successful paired spoken-image run, one attempt failed before launch because the runner looked up the wrong stage key; it is retained at [`attempts/omni-spoken-image-20261030T210855396527Z-257797/`](modalities/attempts/omni-spoken-image-20261030T210855396527Z-257797/). An initial video attempt similarly failed before launch on an undefined context-size variable and is retained at [`attempts/omni-video-20261030T211357215335Z-6f3ba6/`](modalities/attempts/omni-video-20261030T211357215335Z-6f3ba6/). The first audiovisual attempt loaded the model but hit a runner `KeyError: 'kind'` before making a request; its empty request list and server exit are preserved in [`run.json`](modalities/runs/omni-video-audio-20261030T211736088931Z-d5b732/run.json). These were harness defects repaired before the successful runs, not model capability failures.

The current evidence supports local Omni image, audio-input, video, and TTS request paths for these bounded cases. It does not validate app-level search concurrency, broad modality quality, human intelligibility, Windows packaging, or production voice/video features. Keep Omni an opt-in image candidate pending parent integration review; audio/video/TTS remain experiment-only. MiniCPM remains the app default for this phase, and the results do not justify downloading or benchmarking Bonsai 9B.
