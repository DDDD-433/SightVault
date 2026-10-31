# OCR V2 bounded capability probes

These probes are separate from the shared-prompt plain OCR comparison. Each supported candidate uses the task prompt or native API documented for that backend. Case lists and generation caps are frozen here before the calls. Run one command at a time inside a separately granted serialized inference slot. The runner writes predictions and metrics to ignored `results/raw_outputs/`; it does not render or execute model output.

## Frozen probe matrix

| Task | Cases | Cap | OvisOCR 3 | MiniCPM-OCR MLX adapter | RapidOCR 1.4.4 |
| --- | --- | ---: | --- | --- | --- |
| Word spotting / word boxes | `T01,I01,F01,TB02,MC01,ROT01` (6) | 768 tokens | Supported, official word-spotting JSON prompt; normalized `[0,1000)` boxes | Unsupported by measured direct-image MLX API; no inference | Supported native `return_word_box=True`; pixel polygons converted with the pinned contract |
| KIE | `R01,I01,F01,A01` (4) | 256 tokens | Supported, official `kie_fields.txt` prompt | Supported, stored adaptation of the official field-extraction task | Unsupported; records result without loading model |
| Table recognition | `TB01,TB02,TB03` (3) | 512 tokens | Supported, official document-structured JSON prompt; inert HTML table content | Supported, official model-card `Table Recognition:` task prompt | Unsupported; records result without loading model |
| Chart parsing | `C01,C02,C03,C04` (4) | 512 tokens | Supported, official chart-parsing JSON prompt | Supported, official model-card `Chart to Table:` task prompt | Unsupported; records result without loading model |
| Full-page structured parsing | `I01,F01,TB02,MC01` (4) | 1024 tokens | Supported, official `document_structured.txt` JSON element prompt | Not supported by the measured direct-image MLX adapter. The official full-document SDK uses a separate PP-DocLayoutV3 layout stage; that complete route needs a separate setup/fit review before measurement. | Unsupported; records result without loading model |

The selected cases cover terminals, invoices, forms, tables, multi-column content, and rotated content. The table probe uses three representative fixtures from the six frozen tables; the chart probe covers all four frozen charts. The complete candidate-specific prompt text and the pinned bbox contract remain in `prompts/`, `provenance/ovisocr3/prompts/`, and `provenance/bbox-coordinate-contracts.json`.

## Scoring boundaries

- Spotting checks strict JSON/native shape, text-only recall/precision, coordinate bounds, and text-plus-box matching. Rapid boxes use the original input-raster pixel frame; Ovis boxes use normalized integers `[0,1000)` relative to the supplied image. No IoU is emitted for an unknown frame.
- KIE requires exactly the authored field names and string values; schema validity and exact field-value precision/recall are separate.
- Table results report serialization/schema validity separately from cell and numeric-value correctness. Chart results report schema, headers, value pairs, numeric values, and exact chart matches separately.
- Ovis full-page structured parsing validates the official array schema, allowed categories, normalized box geometry, and extracted content CER/WER/identifier/numeric values. The authored box labels are line-level, while this output contains semantic page elements; the probe therefore reports geometry validity and does not claim line-to-element IoU.
- The MiniCPM complete-document SDK path is documented, but the current benchmark adapter loads only MLX-VLM direct-image recognition. Its SDK/layout pipeline is not silently substituted or called unsupported for the whole model family.
- The cap is an upper bound, not an expected generated length. For future MiniCPM and Ovis probes, predictions record the generated-token count, configured limit, whether the output count reached the limit, and any finish reason exposed by the runtime. Ovis Transformers generation does not expose a finish reason in this adapter; `at_token_budget` is only a possible cap-hit indicator, not a confirmed truncation label.

## Expected budgets

The supported probes request up to 21 Ovis calls, 11 MiniCPM calls, and 6 RapidOCR calls. The aggregate generation ceilings are 13,312 Ovis tokens and 4,608 MiniCPM tokens; actual outputs may stop sooner. Ovis loads once for each supported task command, MiniCPM loads once for each of its three supported tasks, and RapidOCR loads for its single spotting command. This is a small capability matrix, not a throughput benchmark.

### Expected slot time

These are planning allowances, not measured capability results. Ovis's completed plain stream measured 0.0790 continuous images/s at the 50-call point (about 12.66 s/call), with a 6.13 s model load. At that observed pace, its 21 calls take about 4.4 minutes plus five loads; allow **8 minutes** for the larger 512/768/1024-token caps and per-run scoring. MiniCPM's plain run measured 5.39 s warm p50, 9.33 s p95, and 4.50 s load; 11 calls plus three loads project to about 1.5 minutes; allow **2 minutes**. RapidOCR measured 0.39 s warm p50 and 0.31 s load; six box calls project under 10 seconds; allow **1 minute** including command setup. The unsupported classifications issue no model calls. Ovis did not save generated-token counts in the plain run, so its capability duration cannot be reliably scaled by tokens/second; the allowance includes that uncertainty.

## Exact commands

Run the commands sequentially after a separate slot grant. Unsupported paths return a recorded `unsupported` result and do not load weights or issue inference calls.

```sh
# OvisOCR 3: official word-spotting JSON, KIE, table, chart, and full-page structure.
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task spotting --case-ids T01,I01,F01,TB02,MC01,ROT01 --max-new-tokens 768
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task kie --case-ids R01,I01,F01,A01 --max-new-tokens 256
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task table --case-ids TB01,TB02,TB03 --max-new-tokens 512
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task chart --case-ids C01,C02,C03,C04 --max-new-tokens 512
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model ovis --task document_parse --case-ids I01,F01,TB02,MC01 --max-new-tokens 1024

# MiniCPM-OCR 1.0: MLX direct-image tasks. Full-document SDK route is not measured here.
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task spotting --case-ids T01,I01,F01,TB02,MC01,ROT01 --max-new-tokens 768
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task kie --case-ids R01,I01,F01,A01 --max-new-tokens 256
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task table --case-ids TB01,TB02,TB03 --max-new-tokens 512
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task chart --case-ids C01,C02,C03,C04 --max-new-tokens 512
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model minicpm --task document_parse --case-ids I01,F01,TB02,MC01 --max-new-tokens 1024

# RapidOCR control: native boxes, plus explicit unsupported classifications.
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model rapid --task spotting --case-ids T01,I01,F01,TB02,MC01,ROT01
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model rapid --task kie --case-ids R01,I01,F01,A01
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model rapid --task table --case-ids TB01,TB02,TB03
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model rapid --task chart --case-ids C01,C02,C03,C04
sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh --model rapid --task document_parse --case-ids I01,F01,TB02,MC01
```

The unsupported classifications for MiniCPM spotting/document parsing and Rapid KIE/table/chart/document parsing are scope evidence, not failed model calls. Windows has not been tested on hardware.
