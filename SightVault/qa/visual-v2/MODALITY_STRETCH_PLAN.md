# Phase 2 thinking and Omni stretch plan

**Status:** Completed on the local Apple M1 Pro for the bounded probes described below. Raw answers and media are committed under the referenced run directories. These optional modality results remain separate from the 32-case image comparison and from product feature acceptance.

## Completed stages

| Stage | Result | Report / raw output |
| --- | --- | --- |
| LFM3-VL thinking, all 32 cases | 32/32 complete final answers; 128-token thinking cap, separate 384-token final-answer cap; no final-cap truncations | [`MODALITY_RESULTS.md`](MODALITY_RESULTS.md), [`thinking/runs/lfm3-thinking-all-20261030T210520891236Z-731645/`](thinking/runs/lfm3-thinking-all-20261030T210520891236Z-731645/) |
| Omni ASR, synthetic clips | 3/3 requests; literal mean WER 0.494; fixture-specific numeric/time slot score 4/4 | [`results/omni-asr-number-normalization.json`](results/omni-asr-number-normalization.json), [`modalities/runs/omni-asr-20261030T210817967496Z-3a530e/`](modalities/runs/omni-asr-20261030T210817967496Z-3a530e/) |
| Spoken image and text controls | 6/6 requests across three paired questions; basic transport and core answers pass, with over-detailed spoken answers | [`MODALITY_RESULTS.md`](MODALITY_RESULTS.md), [`modalities/runs/omni-spoken-image-20261030T211019615960Z-d28893/`](modalities/runs/omni-spoken-image-20261030T211019615960Z-d28893/) |
| Video-only | Correct motion answer, 1/1 request | [`modalities/runs/omni-video-20261030T211449807662Z-387ee2/`](modalities/runs/omni-video-20261030T211449807662Z-387ee2/) |
| Audiovisual video | On the same local clip, correctly names motion/direction and the exact soundtrack phrase; 1/1 request | [`modalities/runs/omni-video-audio-20261030T211829037444Z-0fcb71/`](modalities/runs/omni-video-audio-20261030T211829037444Z-0fcb71/) |
| TTS | 3/3 WAV outputs decoded at PCM16 mono 24 kHz | [`modalities/runs/omni-tts-20261030T211542606296Z-0fa4ab/`](modalities/runs/omni-tts-20261030T211542606296Z-0fa4ab/) |
| TTS to ASR roundtrip | Three generated WAVs converted locally to PCM16 mono 16 kHz; 3/3 ASR responses; literal mean WER 0.0333 | [`modalities/runs/omni-tts-asr-roundtrip-20261030T212212250616Z-eae029/`](modalities/runs/omni-tts-asr-roundtrip-20261030T212212250616Z-eae029/) |

The exact separate modality outcomes, input/output hashes, observed limits, and preserved setup failures are documented in [`MODALITY_RESULTS.md`](MODALITY_RESULTS.md). The thinking-mode final-answer review is in [`results/lfm3-thinking-ai-review-results.json`](results/lfm3-thinking-ai-review-results.json). The 50-call stability measurements remain in [`STABILITY_AND_THINKING_FOLLOWUP.md`](STABILITY_AND_THINKING_FOLLOWUP.md).

## Runtime pins and protocol

The image and audio/video probes used `LiquidAI/LFM3-Omni-2B-GGUF` revision `3d29af2ad166f8299c2d820937e47dc0add20ffc`, Q4_K_M weights and matching projector, plus upstream llama.cpp commit `e0f142d5ddc1461ce721644d92af33db23545105`. The local runtime binary SHA-256 is `2d973f9be4949e8df3f66ac3a45808ef01e5d8325c5f236a6151cd7924302a13`. The separate LFM3-VL thinking run used MLX-VLM 0.7.11 at `918763c6875c80e82f9f48344dfad4763346fea8` and the already-pinned `LiquidAI/LFM3-VL-2B-MLX-4bit` revision `4146d7fa689833cfa7a06c474e748350b458f41e`.

The Omni runner uses the macOS outbound-deny sandbox profile `(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))`, with localhost allowed and native outbound attempts uninstrumented. The model card documents local audio, video, image, and TTS interfaces; this phase made bounded actual calls, not just a `/props` capability check. No new weights or forks were introduced. All inputs were local and synthetic or the existing benchmark images; no microphone or private recordings were used.

The [pinned video-context review](VIDEO_RUNTIME_CONTEXT_REVIEW.md) records the source-derived two-fps frame selection and the 8192-token context used for the six-second clip. The video fixture has 24 H.264 frames at 4 fps and an AAC soundtrack. The runtime source predicts 12 evenly sampled frames; that count is source-derived rather than directly instrumented in the generated response.

## Interpretation and non-goals

- The LFM3 thinking run changes latency and final-answer quality; its ratings are a separate non-blind exploratory AI review and do not include hidden reasoning.
- Omni audio recognition is weak on this three-clip sample despite reading all four numeric/time slots. The ASR TTS roundtrip is a same-model automated proxy, not independent or human intelligibility review.
- Spoken-image, video-only, and audiovisual results cover only three paired image questions and one six-second clip. Spoken-image responses include unsupported detail. Runtime logs also call audio input experimental and warn of reduced quality.
- These results do not validate app-level semantic-search responsiveness, multi-request model concurrency, broad modality accuracy, Windows execution, or shipped audio/video/TTS user flows. Keep those features out of the product scope for this experiment.
- The MiniCPM app default remains. Omni can be evaluated as an opt-in vision provider; these modality probes alone do not promote it. The results do not meet the quality/performance gate for Bonsai 9B.
