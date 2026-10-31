# Omni video sampling and context preparation

## Pin and fixture

The video stage uses the same local `LiquidAI/LFM3-Omni-2B-GGUF` revision `3d29af2ad166f8299c2d820937e47dc0add20ffc`, Q4_K_M model and projector, and upstream llama.cpp commit `e0f142d5ddc1461ce721644d92af33db23545105` as the image, ASR, and spoken-image stages. No model or fixture bytes changed for this preparation.

The authored video fixture `qa/visual-v2/modality-fixtures/moving-shape-video.mp4` has SHA-256 `a06c720a08eeeb8afccc7814e19d84df4b02808c67a4f41997366881d51bb856`: H.264, 512x320, 24 frames at 4 fps, 6.0 seconds; AAC mono audio at 16 kHz.

## Pinned runtime behavior

Inspection of the exact local/upstream `e0f142d5ddc1461ce721644d92af33db23545105` source found:

- The server accepts `input_video.data` and decodes video formats supported by ffmpeg. For LFM3-Omni, the soundtrack is included with groups of frames. The server CLI documents `--video-fps` with generic default 4 fps.
- The LFM3-VL projector's model-specific video config selects 2 fps, computes a frame count from source duration and sampling rate, clamps Omni video to 4–384 frames, and evenly samples source-frame indices. This model setting takes precedence over the generic fps fallback.
- The Omni video template groups two frames at a time and interleaves the matching soundtrack segment. The local 6-second fixture therefore yields 12 video frames in six frame-pairs, not 24, plus its soundtrack.

These details are visible in the pinned upstream [`mtmd.cpp`](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/tools/mtmd/mtmd.cpp#L2557-L2583), [`mtmd-helper.cpp` frame selection](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/tools/mtmd/mtmd-helper.cpp#L666-L680) and [soundtrack slicing](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/tools/mtmd/mtmd-helper.cpp#L703-L763), and the pinned [server video API and CLI docs](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/tools/server/README.md#L475-L485) / [typed video input docs](https://github.com/ggml-org/llama.cpp/blob/e0f142d5ddc1461ce721644d92af33db23545105/tools/server/README.md#L1686-L1698).

## Bounded run settings

The modality runner now passes `--video-fps 2` for this stage and records the expected 12-frame sampling in its settings. The server context is 8192 tokens for video only, with generation still capped at 384 tokens; the other Omni stages remain at context 4096. This gives the bounded fixture explicit prompt/context headroom for frame embeddings and audio without changing the image, audio, or question content. It is a prepared setting, not a claim that 12 frames require 8192 tokens. Actual server timing/prompt-token usage, request outcome, peak RSS, answer, and finish state are preserved in the run record. If the runtime rejects this request, preserve that failure as the video outcome rather than changing the fixture or silently dropping audio/frames.

The first real video run used the manifest's visual-only question and verified motion/direction response. A separate bounded `video-audio` query on the same MP4 asks for both motion/direction and the exact soundtrack phrase; the reference phrase remains in run metadata and is not included in the model payload. This is necessary to test actual audiovisual use rather than infer it from accepted video transport. Source and `/props` metadata alone do not establish successful decoding or answer quality.
