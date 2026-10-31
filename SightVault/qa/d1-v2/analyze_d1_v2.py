#!/usr/bin/env python3
"""Score isolated d1 outputs against separately frozen visual judgments."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
BINARY_LABELS = [
    "contains_error",
    "contains_terminal",
    "contains_code",
    "contains_receipt",
    "contains_document",
    "contains_chart",
    "contains_table",
    "contains_ui",
    "contains_person",
]
THRESHOLDS = [0.5, 0.7, 0.8, 0.9]


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def safe_div(num: float, den: float):
    return num / den if den else None


def confusion_and_scores(y_true: list[bool], y_pred: list[bool]) -> dict:
    tp = sum(a and b for a, b in zip(y_true, y_pred))
    fp = sum((not a) and b for a, b in zip(y_true, y_pred))
    fn = sum(a and (not b) for a, b in zip(y_true, y_pred))
    tn = sum((not a) and (not b) for a, b in zip(y_true, y_pred))
    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    if precision is None and tp + fn > 0:
        precision = 0.0
    if precision is not None and recall is not None:
        f1 = safe_div(2 * precision * recall, precision + recall)
        if f1 is None and tp + fn > 0:
            f1 = 0.0
    else:
        f1 = None
    accuracy = safe_div(tp + tn, tp + fp + fn + tn)
    return {
        "support_positive": tp + fn,
        "support_negative": tn + fp,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "status": "defined" if tp + fn > 0 else "undefined_no_positive_gold",
    }


def calibration_metrics(y_true: list[bool], probabilities: list[float], bins: int = 10) -> dict:
    brier = sum((p - float(y)) ** 2 for y, p in zip(y_true, probabilities)) / len(y_true) if y_true else None
    calibration_bins = []
    ece = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        members = [
            (y, p) for y, p in zip(y_true, probabilities)
            if (low <= p < high) or (index == bins - 1 and p == 1.0)
        ]
        if not members:
            calibration_bins.append({"lower": low, "upper": high, "count": 0, "mean_probability": None, "observed_rate": None})
            continue
        mean_probability = sum(p for _, p in members) / len(members)
        observed_rate = sum(y for y, _ in members) / len(members)
        ece += len(members) / len(y_true) * abs(mean_probability - observed_rate)
        calibration_bins.append({
            "lower": low,
            "upper": high,
            "count": len(members),
            "mean_probability": mean_probability,
            "observed_rate": observed_rate,
        })
    return {"brier_score": brier, "expected_calibration_error_10_equal_width_bins": ece if y_true else None, "bins": calibration_bins}


def classification_dataset(prediction_rows: list[dict], gold_rows: list[dict], dataset: str) -> dict:
    prediction_by_id = {row["image_id"]: row for row in prediction_rows}
    gold_by_id = {row["id"]: row for row in gold_rows}
    if set(prediction_by_id) != set(gold_by_id):
        raise SystemExit(f"{dataset} label/prediction IDs differ: predictions={len(prediction_by_id)} labels={len(gold_by_id)}")
    threshold_results = {}
    per_label_calibration = {}
    all_gold = []
    all_probs = []
    for label in BINARY_LABELS:
        truths = [bool(gold_by_id[image_id]["labels"][label]) for image_id in sorted(gold_by_id)]
        probabilities = [float(prediction_by_id[image_id]["raw_output"][label]["noul"]) for image_id in sorted(gold_by_id)]
        all_gold.extend(truths)
        all_probs.extend(probabilities)
        per_label_calibration[label] = calibration_metrics(truths, probabilities)
    for threshold in THRESHOLDS:
        per_label = {}
        macro_support = []
        for label in BINARY_LABELS:
            truths = [bool(gold_by_id[image_id]["labels"][label]) for image_id in sorted(gold_by_id)]
            probabilities = [float(prediction_by_id[image_id]["raw_output"][label]["noul"]) for image_id in sorted(gold_by_id)]
            scores = confusion_and_scores(truths, [probability >= threshold for probability in probabilities])
            per_label[label] = scores
            if scores["support_positive"] > 0:
                macro_support.append((scores["precision"], scores["recall"], scores["f1"], scores["accuracy"]))
        threshold_results[str(threshold)] = {
            "per_label": per_label,
            "macro_over_labels_with_positive_gold_support_only": {
                "label_count": len(macro_support),
                "precision": statistics.mean(row[0] for row in macro_support) if macro_support else None,
                "recall": statistics.mean(row[1] for row in macro_support) if macro_support else None,
                "f1": statistics.mean(row[2] for row in macro_support) if macro_support else None,
                "accuracy": statistics.mean(row[3] for row in macro_support) if macro_support else None,
                "undefined_labels_excluded": [label for label in BINARY_LABELS if per_label[label]["support_positive"] == 0],
            },
        }

    content_true = [
        gold_by_id[image_id].get("labels", {}).get("content_type", gold_by_id[image_id].get("content_type"))
        for image_id in sorted(gold_by_id)
    ]
    if any(value is None for value in content_true):
        raise SystemExit(f"{dataset} labels are missing content_type annotations")
    content_pred = [prediction_by_id[image_id]["raw_output"]["content_type"]["choice"] for image_id in sorted(gold_by_id)]
    content_probs = [prediction_by_id[image_id]["raw_output"]["content_type"]["probabilities"] for image_id in sorted(gold_by_id)]
    class_types = sorted(set(content_true) | set(content_pred))
    class_scores = {}
    for label in class_types:
        binary = confusion_and_scores([x == label for x in content_true], [x == label for x in content_pred])
        class_scores[label] = binary
    brier_per_image = []
    top_confidence_pairs = []
    for true, pred, probs in zip(content_true, content_pred, content_probs):
        brier_per_image.append(sum((float(probs.get(label, 0.0)) - float(true == label)) ** 2 for label in sorted(probs)))
        best_prob = max(float(value) for value in probs.values())
        top_confidence_pairs.append((pred == true, best_prob))
    top_ece = calibration_metrics([correct for correct, _ in top_confidence_pairs], [prob for _, prob in top_confidence_pairs])
    content_correct = sum(actual == predicted for actual, predicted in zip(content_true, content_pred))
    return {
        "dataset": dataset,
        "image_count": len(gold_rows),
        "annotation_method": "Single-annotator AI-agent visual fixture annotation; exploratory, not human ground truth.",
        "threshold_metrics": threshold_results,
        "binary_calibration": {
            "overall_brier_score_across_all_image_label_pairs": sum((p - float(y)) ** 2 for y, p in zip(all_gold, all_probs)) / len(all_gold),
            "overall_equal_width_ece_10_bins": calibration_metrics(all_gold, all_probs)["expected_calibration_error_10_equal_width_bins"],
            "per_label": per_label_calibration,
        },
        "content_type": {
            "accuracy": content_correct / len(content_true) if content_true else None,
            "correct": content_correct,
            "count": len(content_true),
            "per_class": class_scores,
            "macro_f1_over_supported_classes": statistics.mean([v["f1"] for v in class_scores.values() if v["f1"] is not None]) if class_scores else None,
            "multiclass_brier_sum_per_image_mean": statistics.mean(brier_per_image) if brier_per_image else None,
            "top_choice_probability_calibration": top_ece,
        },
    }


def route_metrics(classification: dict, prompts: dict, targets: dict) -> dict:
    by_id = {row["image_id"]: row for row in classification["baseline_predictions"]}
    prompt_by_id = {case["id"]: case for case in prompts["cases"]}
    target_map = targets["targets"]
    y_true = []
    y_pred = []
    latency = []
    per_case = []
    for route_id, gold in sorted(target_map.items()):
        case = prompt_by_id[route_id]
        row = by_id[case["image_id"]]
        raw = row["raw_output"].get(f"route_{route_id}")
        if not raw or raw.get("type") != "choice":
            raise SystemExit(f"Missing typed route output for {route_id}")
        predicted = raw["choice"]
        y_true.append(gold)
        y_pred.append(predicted)
        call_elapsed = float(row.get("elapsed_with_memory_policy_s", row["elapsed_s"]))
        latency.append(call_elapsed)
        per_case.append({"route_id": route_id, "image_id": case["image_id"], "gold": gold, "predicted": predicted, "correct": gold == predicted, "image_call_latency_including_memory_policy_s": call_elapsed, "probabilities": raw["probabilities"]})
    labels = sorted(set(y_true) | set(y_pred))
    per_class = {}
    for label in labels:
        per_class[label] = confusion_and_scores([v == label for v in y_true], [v == label for v in y_pred])
    confusion = {gold: dict(Counter(pred for actual, pred in zip(y_true, y_pred) if actual == gold)) for gold in sorted(set(y_true))}
    return {
        "annotation_method": targets["annotation_method"],
        "count": len(y_true),
        "accuracy": sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true) if y_true else None,
        "correct": sum(a == b for a, b in zip(y_true, y_pred)),
        "confusion": confusion,
        "per_class": per_class,
        "macro_f1": statistics.mean([value["f1"] for value in per_class.values() if value["f1"] is not None]) if per_class else None,
        "latency_of_shared_classification_and_route_call": {
            "mean_s": statistics.mean(latency) if latency else None,
            "median_s": statistics.median(latency) if latency else None,
            "p95_s": sorted(latency)[max(0, math.ceil(0.95 * len(latency)) - 1)] if latency else None,
            "note": "The route choice shared one SystemOne image pass with the 10 classification questions; this is the full call latency.",
        },
        "per_case": per_case,
    }


def retrieval_metrics(embedding: dict, rerank: dict, relevance: dict) -> dict:
    rerank_by_pair = {
        (row["query_id"], row["image_id"]): row for row in rerank["candidate_results"]
    }
    if len(rerank_by_pair) != len(rerank["candidate_results"]):
        raise SystemExit("Rerank output has duplicate query/candidate pair scores")
    if len(rerank_by_pair) != 16 * 20:
        raise SystemExit(f"Expected 320 independently scored query/candidate pairs, found {len(rerank_by_pair)}")
    timing_by_query = {row["query_id"]: row for row in rerank.get("query_timings", [])}
    relevant_by_query = relevance["queries"]
    per_query = []
    for base_query in embedding["queries"]:
        query_id = base_query["query_id"]
        gold = set(relevant_by_query[query_id])
        baseline = base_query["top20"]
        if len(baseline) != 20:
            raise SystemExit(f"Frozen EmbeddingGemma list is not top-20 for {query_id}")
        scored = []
        for rank, candidate in enumerate(baseline, start=1):
            result = rerank_by_pair.get((query_id, candidate["image_id"]))
            if not result:
                raise SystemExit(f"Missing d1 candidate score for {query_id}/{candidate['image_id']}")
            if result.get("question_count") != 1:
                raise SystemExit(f"Expected one query-conditioned question for {query_id}/{candidate['image_id']}")
            qscore = result["p_yes"]
            scored.append({
                **candidate,
                "baseline_rank": rank,
                "d1_p_yes": qscore,
                "candidate_call_elapsed_s": result.get("elapsed_with_memory_policy_s", result.get("elapsed_s")),
                "candidate_inference_only_s": result["elapsed_s"],
            })
            if scored[-1]["candidate_call_elapsed_s"] is None:
                raise SystemExit(f"Missing d1 call latency for {query_id}/{candidate['image_id']}")
        reranked = sorted(scored, key=lambda row: (-row["d1_p_yes"], row["baseline_rank"]))

        def metric_set(rows: list[dict], k: int) -> dict:
            top = rows[:k]
            hits = [row for row in top if row["image_id"] in gold]
            first_relevant_rank = next((i for i, row in enumerate(top, start=1) if row["image_id"] in gold), None)
            return {
                "recall_at_5": len([row for row in rows[:5] if row["image_id"] in gold]) / len(gold) if gold else None,
                "hit_at_5": any(row["image_id"] in gold for row in rows[:5]),
                f"mrr_at_{k}": 1.0 / first_relevant_rank if first_relevant_rank else 0.0,
                "top1_hit": bool(rows and rows[0]["image_id"] in gold),
                "relevant_in_top5": [row["image_id"] for row in rows[:5] if row["image_id"] in gold],
                "total_relevant_in_corpus": len(gold),
                "hits_in_first_k": len(hits),
            }

        base_mrr20 = metric_set(baseline, 20)
        d1_mrr20 = metric_set(reranked, 20)
        baseline_top5 = metric_set(baseline, 5)
        d1_top5 = metric_set(reranked, 5)
        query_latency = sum(row["candidate_call_elapsed_s"] for row in scored)
        query_timing = timing_by_query.get(query_id)
        if not query_timing or query_timing.get("successful_calls") != 20:
            raise SystemExit(f"Missing complete 20-call timing for {query_id}")
        measured_loop_wall = query_timing.get("query_loop_wall_seconds_including_checkpoints")
        cold_values = (
            embedding["performance"].get("model_load_s"),
            base_query["embedding_latency_s"],
            rerank["performance"].get("cold_model_load_s"),
            rerank["performance"].get("processor_and_tokenizer_init_s"),
            query_latency,
        )
        cold_total = sum(cold_values) if all(isinstance(value, (int, float)) for value in cold_values) else None
        per_query.append({
            "query_id": query_id,
            "query_text": base_query["query_text"],
            "gold_relevant_image_ids": sorted(gold),
            "embeddinggemma_top20": [row["image_id"] for row in baseline],
            "d1_reranked_top20": [row["image_id"] for row in reranked],
            "d1_reranked_top5": [row["image_id"] for row in reranked[:5]],
            "embeddinggemma_top5_metrics": baseline_top5,
            "d1_reranked_top5_metrics": d1_top5,
            "embeddinggemma_mrr_at_20": base_mrr20["mrr_at_20"],
            "d1_reranked_mrr_at_20": d1_mrr20["mrr_at_20"],
            "d1_reranked_mrr_at_5": d1_top5["mrr_at_5"],
            "embeddinggemma_mrr_at_5": baseline_top5["mrr_at_5"],
            "d1_reranked_mrr_at_5": d1_top5["mrr_at_5"],
            "embeddinggemma_candidate_relevant_in_top20": sum(row["image_id"] in gold for row in baseline),
            "total_relevant_in_corpus": len(gold),
            "embeddinggemma_candidate_recall_at_20": sum(row["image_id"] in gold for row in baseline) / len(gold) if gold else None,
            "embedding_query_latency_s": base_query["embedding_latency_s"],
            "d1_serial_call_time_across_all_20_candidates_s": query_latency,
            "d1_measured_query_loop_wall_20_candidates_s": measured_loop_wall,
            "d1_query_loop_wall_measurement": query_timing.get("query_loop_wall_measurement", "measured by the original runner"),
            "d1_per_candidate_call_mean_s": query_latency / 20,
            "end_to_end_loaded_query_path_s": base_query["embedding_latency_s"] + query_latency,
            "cold_process_total_with_both_models_loaded_s": cold_total,
            "latency_note": "This query's 20 candidates were each evaluated in a separate one-question/one-image call. The serial candidate-call sum includes recorded answer-release and every-five cache policy overhead. When available, loop wall also includes monitoring checkpoints and result persistence; recovered runs may leave that separate loop-wall value unknown. No calls are divided or amortized across queries.",
        })

    aggregate = {}
    for key in ("embeddinggemma_top5_metrics", "d1_reranked_top5_metrics"):
        aggregate[key] = {
            "recall_at_5_macro": statistics.mean(row[key]["recall_at_5"] for row in per_query if row[key]["recall_at_5"] is not None),
            "mrr_at_5_macro": statistics.mean(row[key]["mrr_at_5"] for row in per_query),
            "hit_at_5_rate": statistics.mean(float(row[key]["hit_at_5"]) for row in per_query),
            "top1_hit_rate": statistics.mean(float(row[key]["top1_hit"]) for row in per_query),
        }
    aggregate["embeddinggemma_mrr_at_20_macro"] = statistics.mean(row["embeddinggemma_mrr_at_20"] for row in per_query)
    aggregate["d1_reranked_mrr_at_20_macro"] = statistics.mean(row["d1_reranked_mrr_at_20"] for row in per_query)
    aggregate["embeddinggemma_candidate_recall_at_20_macro"] = statistics.mean(
        row["embeddinggemma_candidate_recall_at_20"]
        for row in per_query
        if row["embeddinggemma_candidate_recall_at_20"] is not None
    )
    loop_wall_values = [row["d1_measured_query_loop_wall_20_candidates_s"] for row in per_query
                        if row["d1_measured_query_loop_wall_20_candidates_s"] is not None]
    loop_wall_unknown = [row["query_id"] for row in per_query
                         if row["d1_measured_query_loop_wall_20_candidates_s"] is None]
    return {
        "annotation_method": relevance["annotation_method"],
        "relevance_rule": relevance["relevance_rule"],
        "query_count": len(per_query),
        "candidate_set_method": "For every query, d1 reorders exactly the same original EmbeddingGemma top-20 candidate set; it does not expand recall beyond top-20.",
        "rerank_recovery_status": rerank.get("recovery_status", "finalized"),
        "finalization_failure": rerank.get("finalization_failure"),
        "metrics": aggregate,
        "latency_summary": {
            "embeddinggemma_model_load_s": embedding["performance"]["model_load_s"],
            "embeddinggemma_all_16_query_embed_and_rank_s": embedding["performance"]["total_query_embedding_and_ranking_s"],
            "d1_model_load_s": rerank["performance"]["cold_model_load_s"],
            "d1_processor_init_s": rerank["performance"]["processor_and_tokenizer_init_s"],
            "d1_all_320_query_candidate_pair_wall_s": rerank["performance"]["query_candidate_pair_evaluation_wall_s"],
            "per_query_serial_candidate_call_seconds_mean": statistics.mean(row["d1_serial_call_time_across_all_20_candidates_s"] for row in per_query),
            "per_query_serial_candidate_call_seconds_median": statistics.median(row["d1_serial_call_time_across_all_20_candidates_s"] for row in per_query),
            "per_query_loop_wall_seconds_mean": statistics.mean(loop_wall_values) if loop_wall_values else None,
            "per_query_loop_wall_seconds_median": statistics.median(loop_wall_values) if loop_wall_values else None,
            "per_query_loop_wall_measured_query_count": len(loop_wall_values),
            "per_query_loop_wall_unknown_query_ids": loop_wall_unknown,
            "d1_model_load_s_available": rerank["performance"].get("cold_model_load_s") is not None,
            "d1_processor_init_s_available": rerank["performance"].get("processor_and_tokenizer_init_s") is not None,
            "d1_total_320_pair_loop_wall_available": rerank["performance"].get("query_candidate_pair_evaluation_wall_s") is not None,
            "cold_process_total_available_query_count": sum(row["cold_process_total_with_both_models_loaded_s"] is not None for row in per_query),
            "serial_scoring_note": "Every query/candidate pair was evaluated independently (320 calls total); no call was shared across queries. Per-query sums are measured serial call costs, not shares of a batched run.",
        },
        "per_query": per_query,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--classification", default=HERE / "classification_routing_results.json", type=Path)
    parser.add_argument("--labels", default=HERE / "labels.json", type=Path)
    parser.add_argument("--supplement-labels", default=HERE / "diagnostic_supplement_manifest.json", type=Path)
    parser.add_argument("--routing-prompts", default=HERE / "routing_prompts.json", type=Path)
    parser.add_argument("--routing-targets", default=HERE / "routing_targets.json", type=Path)
    parser.add_argument("--embedding", default=HERE / "embedding_top20.json", type=Path)
    parser.add_argument("--relevance", default=HERE / "retrieval_relevance.json", type=Path)
    parser.add_argument("--rerank", default=HERE / "d1_rerank_scores.json", type=Path)
    parser.add_argument("--output", default=HERE / "analysis_results.json", type=Path)
    args = parser.parse_args()

    classification = read(args.classification)
    labels = read(args.labels)
    supplement_labels = read(args.supplement_labels)
    routing_prompts = read(args.routing_prompts)
    routing_targets = read(args.routing_targets)
    baseline = classification_dataset(classification["baseline_predictions"], labels["images"], "frozen 62-image baseline")
    supplement = classification_dataset(classification["supplement_predictions"], supplement_labels["images"], "separate 6-image diagnostic supplement")
    routes = route_metrics(classification, routing_prompts, routing_targets)

    output = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment": "SightVault Phase 2 D1 v2",
        "dataset_caveat": "Single-annotator, correlated synthetic demo fixture. These metrics are exploratory, not human ground truth or broad generalization claims.",
        "classification": {"baseline": baseline, "supplement": supplement},
        "routing": routes,
        "retrieval": None,
        "performance": classification["performance"],
    }
    if args.embedding.is_file() and args.rerank.is_file():
        output["retrieval"] = retrieval_metrics(read(args.embedding), read(args.rerank), read(args.relevance))
    out = args.output.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(out),
        "baseline_images": baseline["image_count"],
        "supplement_images": supplement["image_count"],
        "routing_accuracy": routes["accuracy"],
        "retrieval_analyzed": output["retrieval"] is not None,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
