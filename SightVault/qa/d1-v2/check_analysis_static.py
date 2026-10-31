#!/usr/bin/env python3
"""No-model analyzer schema and unsupported-class regression checks."""
from __future__ import annotations

from analyze_d1_v2 import BINARY_LABELS, classification_dataset, confusion_and_scores, retrieval_metrics


def main() -> int:
    missed_positive = confusion_and_scores([True, False], [False, False])
    assert missed_positive["precision"] == 0.0
    assert missed_positive["recall"] == 0.0
    assert missed_positive["f1"] == 0.0
    assert missed_positive["support_positive"] == 1
    print("OK: a supported class with no predicted positives contributes F1=0 to macro metrics")

    content_types = {"photo": 0.9, "table": 0.1}
    prediction = {
        "image_id": "I000",
        "raw_output": {
            **{label: {"noul": 0.1} for label in BINARY_LABELS},
            "content_type": {"choice": "photo", "probabilities": content_types},
        },
    }
    nested = {"id": "I000", "labels": {**{label: False for label in BINARY_LABELS}, "content_type": "photo"}}
    top_level = {"id": "I000", "labels": {label: False for label in BINARY_LABELS}, "content_type": "photo"}
    baseline = classification_dataset([prediction], [nested], "baseline test")
    supplement = classification_dataset([prediction], [top_level], "supplement test")
    assert baseline["content_type"]["correct"] == supplement["content_type"]["correct"] == 1
    for result in (baseline, supplement):
        threshold = result["threshold_metrics"]["0.5"]
        macro = threshold["macro_over_labels_with_positive_gold_support_only"]
        assert macro["label_count"] == 0
        assert len(macro["undefined_labels_excluded"]) == len(BINARY_LABELS)
    print("OK: nested baseline and top-level supplement labels; unsupported labels excluded")

    embedding = {"performance": {"model_load_s": 2.0, "total_query_embedding_and_ranking_s": 3.0}, "queries": []}
    relevance = {"annotation_method": "synthetic unit fixture", "relevance_rule": "one relevant item", "queries": {}}
    rerank = {"performance": {
        "cold_model_load_s": 5.0,
        "processor_and_tokenizer_init_s": 1.0,
        "query_candidate_pair_evaluation_wall_s": 320.0,
    }, "candidate_results": [], "query_timings": []}
    for q in range(16):
        query_id = f"Q{q:02d}"
        candidates = [
            {"image_id": f"I{q:02d}-{i:02d}", "filename": f"{q:02d}-{i:02d}.png", "timestamp": i, "semantic_score": 1.0 - i / 100}
            for i in range(20)
        ]
        embedding["queries"].append({"query_id": query_id, "query_text": query_id, "top20": candidates, "embedding_latency_s": 0.1})
        relevance["queries"][query_id] = [candidates[4]["image_id"]]
        for i, candidate in enumerate(candidates):
            rerank["candidate_results"].append({
                "query_id": query_id,
                "image_id": candidate["image_id"],
                "question_count": 1,
                "p_yes": 0.99 if i == 4 else 0.01,
                "elapsed_s": 1.5,
                "elapsed_with_memory_policy_s": 2.0,
            })
        rerank["query_timings"].append({
            "query_id": query_id,
            "successful_calls": 20,
            "query_loop_wall_seconds_including_checkpoints": 42.0,
        })
    retrieval = retrieval_metrics(embedding, rerank, relevance)
    q0 = retrieval["per_query"][0]
    assert q0["embeddinggemma_top5_metrics"]["top1_hit"] is False
    assert q0["d1_reranked_top5_metrics"]["top1_hit"] is True
    assert q0["embeddinggemma_top5_metrics"]["hit_at_5"] is True
    assert q0["d1_serial_call_time_across_all_20_candidates_s"] == 40.0
    assert q0["d1_measured_query_loop_wall_20_candidates_s"] == 42.0
    print("OK: per-query 20 independent calls, metric definitions, and end-to-end latency scoring")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
