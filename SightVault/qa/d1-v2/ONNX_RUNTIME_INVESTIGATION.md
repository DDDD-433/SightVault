# d1-2B ONNX runtime investigation

Checked 2026-10-30 against `LiquidAI/d1-2B-ONNX` revision `7f5c6801cba6164dda9daae925178f89717b960f` and the matching official model card.

## Official package

The model card lists the Q4 CPU pair as `decoder_q4` plus `vision_encoder_q8`, about 1.94 GB. The card clarifies that Q4 is mixed precision: selected MLP gate and up projections are int4 and the rest are int8. Other variants include Q8 CPU, Q4F16/Q8F16 GPU, FP16 GPU, and FP32 reference. The exact graph and external-weight byte counts and SHA-256 values are in `model_provenance.json`.

The documented decoder consumes packed `input_ids`, `attention_mask`, `position_ids`, projected image/video features, `question_ids`, and `slot_positions`; it returns answer-slot logits and final-normalized hidden states. A row can hold up to 16 questions. The card says its Python code builds the inputs and maps outputs to typed answers using `processor_config.json`.

## Runtime support check

I fetched and statically inspected all 26 Python source files at the pinned revision. The official `runner.py` imports PyTorch, `modeling_d1_lfm3_vl.py` composes `Lfm3SystemOne` with the native LFM3-VL model, and none of the Python files imports `onnxruntime` or constructs an `InferenceSession`. The repo does contain the ONNX graphs and external data, but it does not expose a turnkey Python `system_one` implementation backed by those graphs. Running the documented ONNX interface for this experiment would require a custom implementation of graph input assembly, image preprocessing, question packing, calibrated typed readouts, and validation against the reference answers.

ONNX Runtime documents a CoreML Execution Provider in its macOS Python wheels. That establishes provider availability in the package, not that these d1 graphs can use Core ML or the Apple GPU/Neural Engine. The d1 card describes its Q4 package as CPU-targeted; no d1 ONNX graph/provider compatibility run was performed. Windows CPU execution is advertised by the package's cross-platform positioning but was not measured here.

## Experiment decision

The d1-2B BF16 Transformers/PyTorch reference path provides the supported `system_one` API and passed the bounded real-image classification and query-conditioned relevance smoke. I did not build a custom ONNX adapter or present an unverified CoreML/Windows path as tested. Revisit ONNX only if the reference quality and full-run memory/latency results justify the engineering cost.

## Primary sources

- [Official d1-2B-ONNX card](https://huggingface.co/LiquidAI/d1-2B-ONNX)
- [Official d1-2B card](https://huggingface.co/LiquidAI/d1-2B)
- [Liquid ONNX deployment guide](https://docs.liquid.ai/deployment/on-device/onnx)
- [ONNX Runtime CoreML Execution Provider documentation](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html)
