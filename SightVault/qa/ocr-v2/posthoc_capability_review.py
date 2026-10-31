"""Rescore saved OCR capability outputs without loading models or editing raw runs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from metrics import evaluate_plain, extract_special_tokens, summarize_plain  # noqa: E402
from structured_metrics import evaluate_boxes  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_ground_truth(path: Path):
    return {row["id"]: row for row in
            (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)}


def finish_counts(counts):
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall
          else (0.0 if tp + fp + fn else None))
    return {**counts, "precision": precision, "recall": recall, "f1": f1}


def prf(expected_items, predicted_items):
    expected, predicted = Counter(expected_items), Counter(predicted_items)
    tp = sum((expected & predicted).values())
    fp, fn = sum((predicted - expected).values()), sum((expected - predicted).values())
    return finish_counts({"tp": tp, "fp": fp, "fn": fn,
        "ground_truth_support": tp + fn, "predicted_support": tp + fp})


def add_counts(rows):
    return {key: sum(row.get(key, 0) for row in rows)
            for key in ("tp", "fp", "fn", "ground_truth_support", "predicted_support")}


def source_record(path: Path, run):
    try:
        relative_path = str(path.resolve().relative_to(HERE.parents[1].resolve()))
    except ValueError:
        relative_path = str(path)
    return {"path": relative_path, "sha256": sha256_file(path), "run_id": run.get("run_id"),
        "experiment_commit": run.get("experiment_commit"),
        "experiment_code_sha256": run.get("experiment_code_sha256"),
        "model": run.get("model"), "runtime": run.get("runtime"), "command": run.get("command"),
        "started_at_utc": run.get("started_at_utc"), "finished_at_utc": run.get("finished_at_utc"),
        "offline": run.get("offline")}


def simple_otsl(raw: str):
    """Parse the observed rectangular OTSL subset using only fcel/nl tokens."""
    text = str(raw).strip()
    if not (text.startswith("<otsl>") and text.endswith("</otsl>")):
        return {"serialization_valid": False, "schema_valid": False, "rows": [],
                "error": "missing exact OTSL root"}
    token_re = re.compile(r"(<[^<>]*>)")
    parts = token_re.split(text[len("<otsl>"):-len("</otsl>")])
    rows, row, cell_parts, in_cell = [], [], [], False
    for part in parts:
        if part == "<fcel>":
            # OTSL uses fcel as the start of each next cell, so it also closes
            # the preceding cell in the same row.
            if in_cell:
                row.append("".join(cell_parts).strip())
            in_cell, cell_parts = True, []
        elif part == "<nl>":
            if not in_cell:
                return {"serialization_valid": False, "schema_valid": False,
                        "rows": rows, "error": "row delimiter outside cell"}
            row.append("".join(cell_parts).strip())
            rows.append(row)
            row, cell_parts, in_cell = [], [], False
        elif part.startswith("<") and part.endswith(">"):
            return {"serialization_valid": False, "schema_valid": False,
                    "rows": rows, "error": "unsupported OTSL token: " + part}
        elif in_cell:
            cell_parts.append(part)
        elif part.strip():
            return {"serialization_valid": False, "schema_valid": False,
                    "rows": rows, "error": "text outside cell"}
    if in_cell:
        row.append("".join(cell_parts).strip())
    if row:
        rows.append(row)
    valid = bool(rows) and all(rows) and all(len(item) == len(rows[0]) for item in rows)
    return {"serialization_valid": True, "schema_valid": bool(valid), "rows": rows,
            "error": None if valid else "empty or non-rectangular table"}


def numeric_tokens(values):
    tokens = []
    for value in values:
        found = extract_special_tokens(str(value))
        for key in ("dates", "times", "versions", "prices", "numbers"):
            tokens.extend(found[key])
    return tokens


def mini_table_review(path: Path, gt):
    run = read_json(path)
    cases, cell_counts, numeric_counts, generation = [], [], [], []
    for case in run["cases"]:
        image_id, prediction = case["image_id"], case["prediction"]
        label = gt[image_id]
        parsed = simple_otsl(prediction.get("text", ""))
        rows = parsed["rows"]
        headers, data = (rows[0], rows[1:]) if rows else ([], [])
        exp_headers, exp_rows = label.get("table_headers", []), label.get("table_rows", [])
        expected_cells = [str(v) for v in exp_headers]
        expected_cells.extend(str(v) for row in exp_rows for v in row)
        predicted_cells = [str(v) for row in rows for v in row]
        cells = prf(expected_cells, predicted_cells)
        exp_numbers = numeric_tokens([v for row in exp_rows for v in row])
        pred_numbers = numeric_tokens([v for row in data for v in row])
        numbers = prf(exp_numbers, pred_numbers)
        cell_counts.append(cells)
        numeric_counts.append(numbers)
        generation.append({"image_id": image_id, "generation_tokens": prediction.get("generation_tokens"),
            "max_new_tokens": prediction.get("max_new_tokens"),
            "at_token_budget": prediction.get("at_token_budget"), "finish_reason": prediction.get("finish_reason")})
        cases.append({"image_id": image_id, "otsl_serialization_valid": parsed["serialization_valid"],
            "rectangular_schema_valid": parsed["schema_valid"], "parse_error": parsed["error"],
            "parse_mode": "simple_rectangular_fcel_nl_otsl", "predicted_columns": headers,
            "predicted_rows": data, "exact_table": headers == exp_headers and data == exp_rows,
            "cells": cells, "numeric_values": numbers})
    return {"source": source_record(path, run), "original_harness_summary": run.get("quality"),
        "posthoc_adapter": "MiniCPM native OTSL; observed rectangular fcel/nl subset consistent with its documented SDK OTSL conversion path; inert parser",
        "cases": cases, "exact_tables": sum(c["exact_table"] for c in cases), "total_tables": len(cases),
        "cell_micro": finish_counts(add_counts(cell_counts)),
        "numeric_value_micro": finish_counts(add_counts(numeric_counts)), "generation": generation}


def parse_decimal(value: str):
    match = re.fullmatch(r"\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(%)?\s*", str(value))
    if not match:
        return None, False
    try:
        return Decimal(match.group(1).replace(",", "")), bool(match.group(2))
    except InvalidOperation:
        return None, False


def chart_pair_scores(expected, predicted, y_title):
    percent_axis = "%" in str(y_title) or "percent" in str(y_title).lower()
    expected_literal = [(str(k), str(v)) for k, v in expected]
    predicted_literal = [(str(k), str(v)) for k, v in predicted]
    expected_semantic, predicted_semantic, unit_errors = [], [], []
    for key, value in expected_literal:
        number, _suffix = parse_decimal(value)
        expected_semantic.append((key, number, percent_axis))
    for key, value in predicted_literal:
        number, percent_suffix = parse_decimal(value)
        if number is None:
            unit_errors.append({"key": key, "value": value, "reason": "not a scalar number"})
            continue
        if percent_suffix and not percent_axis:
            unit_errors.append({"key": key, "value": value, "reason": "percent suffix conflicts with axis"})
            continue
        # A suffixless scalar inherits the visible axis unit. Thus 8, 8.0,
        # and 8.0% compare as one CER value only on a percent axis.
        predicted_semantic.append((key, number, percent_axis))
    return {"literal_pairs": prf(expected_literal, predicted_literal),
        "numeric_semantic_pairs": prf(expected_semantic, predicted_semantic),
        "percent_axis": percent_axis, "unit_errors": unit_errors}


def numeric_cell_column(rows):
    if not rows or not rows[0]:
        return None
    widths = len(rows[0])
    candidates = []
    for column in range(widths):
        numeric = sum(parse_decimal(row[column])[0] is not None for row in rows if column < len(row))
        if numeric >= len(rows) - 1:
            candidates.append(column)
    return candidates[0] if len(candidates) == 1 else None


def mini_chart_review(path: Path, gt):
    run = read_json(path)
    cases, literal, semantic, axis_exact = [], [], [], []
    for case in run["cases"]:
        image_id, prediction = case["image_id"], case["prediction"]
        label = gt[image_id]
        parsed = simple_otsl(prediction.get("text", ""))
        rows = parsed["rows"]
        headers, data = (rows[0], rows[1:]) if rows else ([], [])
        value_col = numeric_cell_column(data)
        label_col = 1 - value_col if value_col in (0, 1) else None
        pairs = [(row[label_col], row[value_col]) for row in data
                 if label_col is not None and len(row) > max(label_col, value_col)]
        chart = label.get("chart", {})
        expected = [(str(k), str(v)) for k, v in chart.get("values", {}).items()]
        scores = chart_pair_scores(expected, pairs, chart.get("y_title", ""))
        literal.append(scores["literal_pairs"])
        semantic.append(scores["numeric_semantic_pairs"])
        axis = {"x_title": headers[label_col] if label_col is not None else None,
                "y_title": headers[value_col] if value_col is not None else None}
        expected_axis = {"x_title": chart.get("x_title"), "y_title": chart.get("y_title")}
        axis_score = {key: {"expected": expected_axis[key], "actual": axis[key],
                            "exact": expected_axis[key] == axis[key]} for key in expected_axis}
        axis_exact.append(sum(item["exact"] for item in axis_score.values()))
        cases.append({"image_id": image_id, "otsl_serialization_valid": parsed["serialization_valid"],
            "rectangular_schema_valid": parsed["schema_valid"], "parse_error": parsed["error"],
            "headers_in_model_order": headers, "rows_in_model_order": data,
            "inferred_label_column": label_col, "inferred_value_column": value_col,
            "axis_header_check_after_orientation": axis_score, "predicted_value_pairs": pairs,
            "pair_scores": scores, "generation": {"generation_tokens": prediction.get("generation_tokens"),
                "max_new_tokens": prediction.get("max_new_tokens"),
                "at_token_budget": prediction.get("at_token_budget"), "finish_reason": prediction.get("finish_reason")}})
    return {"source": source_record(path, run), "original_harness_summary": run.get("quality"),
        "posthoc_adapter": "MiniCPM Chart-to-Table OTSL; category/value columns inferred by numeric cells",
        "native_chart_title_and_source_available": False, "cases": cases,
        "literal_value_pairs_micro": finish_counts(add_counts(literal)),
        "numeric_semantic_value_pairs_micro": finish_counts(add_counts(semantic)),
        "exact_axis_header_fields": {"exact": sum(axis_exact), "total": 2 * len(axis_exact),
            "accuracy": sum(axis_exact) / max(1, 2 * len(axis_exact))}}


def valid_chart_schema(parsed):
    if not isinstance(parsed, dict) or set(parsed) != {"title", "source", "x_title", "y_title", "values"}:
        return False
    if not all(isinstance(parsed.get(k), str) for k in ("title", "source", "x_title")):
        return False
    y_title = parsed.get("y_title")
    if not (isinstance(y_title, str) or isinstance(y_title, list) and all(isinstance(v, str) for v in y_title)):
        return False
    def string_or_object(value):
        if isinstance(value, str):
            return True
        return isinstance(value, dict) and bool(value) and all(
            isinstance(k, str) and string_or_object(v) for k, v in value.items())
    values = parsed.get("values")
    return isinstance(values, dict) and bool(values) and all(
        isinstance(k, str) and string_or_object(v) for k, v in values.items())


def flatten_values(values):
    pairs, series = [], []
    for key, value in values.items():
        if isinstance(value, dict):
            series.append(str(key))
            pairs.extend((str(label), item) for label, item in value.items() if isinstance(item, str))
        elif isinstance(value, str):
            pairs.append((str(key), value))
    return pairs, series


def ovis_chart_review(path: Path, gt):
    run = read_json(path)
    cases, literal, semantic, header_exact = [], [], [], 0
    for case in run["cases"]:
        image_id = case["image_id"]
        label = gt[image_id]
        raw = case["prediction"].get("text", "")
        try:
            parsed, parse_error = json.loads(raw), None
        except Exception as exc:
            parsed, parse_error = None, f"{type(exc).__name__}: {exc}"
        schema = valid_chart_schema(parsed)
        chart = label.get("chart", {})
        predicted, series = flatten_values(parsed.get("values", {})) if isinstance(parsed, dict) and isinstance(parsed.get("values"), dict) else ([], [])
        expected = [(str(k), str(v)) for k, v in chart.get("values", {}).items()]
        scores = chart_pair_scores(expected, predicted, chart.get("y_title", ""))
        literal.append(scores["literal_pairs"])
        semantic.append(scores["numeric_semantic_pairs"])
        expected_lines = label.get("expected_text", "").splitlines()
        expected_headers = {"title": expected_lines[0] if expected_lines else "",
            "source": chart.get("source", ""), "x_title": chart.get("x_title", ""),
            "y_title": chart.get("y_title", "")}
        headers = {key: {"expected": value, "actual": parsed.get(key) if isinstance(parsed, dict) else None,
                         "exact": (parsed.get(key) if isinstance(parsed, dict) else None) == value}
                   for key, value in expected_headers.items()}
        header_exact += sum(row["exact"] for row in headers.values())
        token = case["prediction"]
        cases.append({"image_id": image_id, "json_parse_success": parse_error is None,
            "json_parse_error": parse_error, "official_schema_valid_including_nested_string_values": schema,
            "original_harness_metrics_preserved_in_source": case.get("metrics"),
            "headers": headers, "native_series_names": series, "predicted_value_pairs": predicted,
            "pair_scores": scores, "generation": {"generation_tokens": token.get("generation_tokens"),
                "max_new_tokens": token.get("max_new_tokens"), "at_token_budget": token.get("at_token_budget"),
                "finish_reason": token.get("finish_reason")}})
    return {"source": source_record(path, run), "original_harness_summary": run.get("quality"),
        "posthoc_adapter": "Official chart JSON schema with recursively nested string-valued series; values flattened only for separate scoring",
        "official_schema_valid_cases": sum(c["official_schema_valid_including_nested_string_values"] for c in cases),
        "total_cases": len(cases), "cases": cases,
        "literal_value_pairs_micro": finish_counts(add_counts(literal)),
        "numeric_semantic_value_pairs_micro": finish_counts(add_counts(semantic)),
        "header_field_exact": {"exact": header_exact, "total": 4 * len(cases),
            "accuracy": header_exact / max(1, 4 * len(cases))}}


def points_from_box(box):
    if not isinstance(box, (list, tuple)):
        return None
    if len(box) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box):
        return [(float(box[0]), float(box[1])), (float(box[2]), float(box[3]))]
    if len(box) >= 4:
        try:
            return [(float(point[0]), float(point[1])) for point in box]
        except (TypeError, ValueError, IndexError):
            return None
    return None


def box_center(box):
    points = points_from_box(box)
    return ((sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))
            if points else None)


def point_in_polygon(point, polygon):
    if not polygon or len(polygon) < 3:
        return False
    x, y = point
    inside, j = False, len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi)
        if abs(cross) <= 1e-6 and min(xi, xj) - 1e-6 <= x <= max(xi, xj) + 1e-6 \
                and min(yi, yj) - 1e-6 <= y <= max(yi, yj) + 1e-6:
            return True
        if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-30) + xi:
            inside = not inside
        j = i
    return inside


def union_quads(chars):
    points = [p for char in chars for p in (points_from_box(char.get("bbox")) or [])]
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    box = [min(xs), min(ys), max(xs), max(ys)]
    return box if box[0] < box[2] and box[1] < box[3] else None


def collapse_ws(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def derive_rapid_words(lines, chars):
    by_line = [[] for _ in lines]
    unassigned = ambiguous = 0
    for char_index, char in enumerate(chars):
        center = box_center(char.get("bbox"))
        if center is None:
            unassigned += 1
            continue
        hits = [i for i, line in enumerate(lines)
                if point_in_polygon(center, points_from_box(line.get("bbox")) or [])]
        if len(hits) == 1:
            by_line[hits[0]].append((char_index, char))
        elif len(hits) > 1:
            ambiguous += 1
        else:
            unassigned += 1

    derived, statuses = [], []
    for i, line in enumerate(lines):
        text = str(line.get("text", ""))
        polygon = points_from_box(line.get("bbox")) or []
        if len(polygon) < 2:
            statuses.append({"line_index": i, "text": text, "aligned": False,
                             "reason": "malformed_line_quad", "derived_word_count": 0})
            continue
        dx, dy = polygon[1][0] - polygon[0][0], polygon[1][1] - polygon[0][1]
        length = math.hypot(dx, dy)
        if not length:
            statuses.append({"line_index": i, "text": text, "aligned": False,
                             "reason": "degenerate_line_direction", "derived_word_count": 0})
            continue
        entries = by_line[i]
        def projection(entry):
            center = box_center(entry[1].get("bbox"))
            return (center[0] * dx + center[1] * dy) / length
        entries.sort(key=lambda entry: (projection(entry), entry[0]))
        char_text = "".join(str(char.get("text", "")) for _, char in entries)
        if collapse_ws(char_text) != collapse_ws(text):
            statuses.append({"line_index": i, "text": text, "native_char_text": char_text,
                "aligned": False, "reason": "native_chars_do_not_exactly_match_line",
                "native_char_count": len(entries), "derived_word_count": 0})
            continue
        words, word_chars = [], []
        for _, char in entries:
            value = str(char.get("text", ""))
            if value and value.isspace():
                if word_chars:
                    word_text = "".join(str(item.get("text", "")) for item in word_chars)
                    box = union_quads(word_chars)
                    if box:
                        words.append({"text": word_text, "bbox": box, "source_line_index": i})
                    word_chars = []
            else:
                word_chars.append(char)
        if word_chars:
            word_text = "".join(str(item.get("text", "")) for item in word_chars)
            box = union_quads(word_chars)
            if box:
                words.append({"text": word_text, "bbox": box, "source_line_index": i})
        if collapse_ws(" ".join(word["text"] for word in words)) != collapse_ws(text):
            statuses.append({"line_index": i, "text": text, "native_char_text": char_text,
                "aligned": False, "reason": "word boundaries do not recompose line",
                "native_char_count": len(entries), "derived_word_count": 0})
            continue
        derived.extend(words)
        statuses.append({"line_index": i, "text": text, "aligned": True, "reason": None,
            "native_char_count": len(entries), "derived_word_count": len(words)})
    return {"words": derived, "line_status": statuses,
            "unassigned_characters": unassigned, "ambiguous_characters": ambiguous}


def sum_existing_metrics(rows, field):
    counts = {part: 0 for part in ("tp", "fp", "fn", "ground_truth_support", "predicted_support")}
    for row in rows:
        metrics = row.get(field) or {}
        for part in counts:
            counts[part] += metrics.get(part, 0)
    return finish_counts(counts)


def spotting_review(rapid_path: Path, ovis_path: Path, gt):
    rapid, ovis = read_json(rapid_path), read_json(ovis_path)
    rapid_cases, derived_scores, line_scores, ovis_cases, ovis_text_rows = [], [], [], [], []
    ovis_valid_text_rows = []
    for case in rapid["cases"]:
        image_id, prediction = case["image_id"], case["prediction"]
        label = gt[image_id]
        derived = derive_rapid_words(prediction.get("lines", []), prediction.get("words", []))
        word_score = evaluate_boxes(label.get("word_boxes_xyxy_px", []), derived["words"],
            label["image"]["width"], label["image"]["height"], coordinate_frame="pixel_input_raster_top_left")
        line_ground_truth = [box for box in label.get("line_boxes_xyxy_px", [])
            if str(box.get("text", "")).strip() and isinstance(box.get("bbox"), list)
            and len(box["bbox"]) == 4 and box["bbox"][0] < box["bbox"][2]
            and box["bbox"][1] < box["bbox"][3]]
        line_score = evaluate_boxes(line_ground_truth, prediction.get("lines", []),
            label["image"]["width"], label["image"]["height"], coordinate_frame="pixel_input_raster_top_left")
        derived_scores.append(word_score)
        line_scores.append(line_score)
        rapid_cases.append({"image_id": image_id, "native_metric_unchanged": case.get("metrics"),
            "method": "center-in-line-quad assignment; projection sort on line top edge; exact character stream must match line after whitespace collapse; union native character quads per whitespace-delimited word",
            "unassigned_character_count": derived["unassigned_characters"],
            "ambiguous_character_count": derived["ambiguous_characters"],
            "aligned_lines": sum(s["aligned"] for s in derived["line_status"]),
            "line_count": len(derived["line_status"]), "unalignable_lines": [
                {k: v for k, v in status.items() if k in ("line_index", "text", "reason", "native_char_count")}
                for status in derived["line_status"] if not status["aligned"]],
            "derived_word_count": len(derived["words"]), "derived_word_boxes_xyxy_px": derived["words"],
            "derived_word_metrics": word_score,
            "native_line_box_metrics": line_score})

    for case in ovis["cases"]:
        image_id, prediction = case["image_id"], case["prediction"]
        label = gt[image_id]
        try:
            regions = json.loads(prediction.get("text", ""))
            parse_error = None
            if not isinstance(regions, list):
                regions, parse_error = [], "decoded JSON is not an array"
        except Exception as exc:
            regions, parse_error = [], f"{type(exc).__name__}: {exc}"
        region_text = "\n".join(item.get("text", "") for item in regions if isinstance(item, dict))
        text_score = evaluate_plain(label, region_text)
        ovis_text_rows.append({"image_id": image_id, "metrics": text_score})
        if parse_error is None:
            ovis_valid_text_rows.append({"image_id": image_id, "metrics": text_score})
        metrics = case.get("metrics", {})
        prediction_meta = case.get("prediction", {})
        ovis_cases.append({"image_id": image_id, "native_granularity": "official JSON text regions; outputs are not split",
            "strict_json_parse_success": parse_error is None, "strict_json_parse_error": parse_error,
            "region_count": len(regions), "fixture_word_count": len(label.get("word_boxes_xyxy_px", [])),
            "native_word_box_metrics_unchanged": {k: metrics.get(k) for k in
                ("exact_text_and_bbox", "text_only", "valid_predicted_boxes", "predicted_boxes")},
            "region_text_fullpage_metrics": text_score,
            "generation": {"generation_tokens": prediction_meta.get("generation_tokens"),
                "max_new_tokens": prediction_meta.get("max_new_tokens"),
                "at_token_budget": prediction_meta.get("at_token_budget"),
                "finish_reason": prediction_meta.get("finish_reason")}})
    return {"rapid_source": source_record(rapid_path, rapid), "ovis_source": source_record(ovis_path, ovis),
        "rapid_native_character_box_metrics_micro": {
            "text_and_bbox": sum_existing_metrics([c["native_metric_unchanged"] for c in rapid_cases], "exact_text_and_bbox"),
            "text_only": sum_existing_metrics([c["native_metric_unchanged"] for c in rapid_cases], "text_only")},
        "rapid_derived_char_to_word_variant": {
            "method_scope": "Saved outputs only; derived word geometry uses native character quads. Unalignable lines are excluded and counted; no line is repaired or split by guessed positions.",
            "scope_limit": "Observed only on this English fixture subset and RapidOCR 1.4.4 return_word_box route.",
            "text_and_bbox_micro": finish_counts(add_counts([x["exact_text_and_bbox"] for x in derived_scores])),
            "text_only_micro": finish_counts(add_counts([x["text_only"] for x in derived_scores])),
            "cases": rapid_cases},
        "rapid_native_line_box_usefulness": {
            "text_and_bbox_micro": finish_counts(add_counts([x["exact_text_and_bbox"] for x in line_scores])),
            "text_only_micro": finish_counts(add_counts([x["text_only"] for x in line_scores])),
            "cases": [{"image_id": c["image_id"], "metrics": c["native_line_box_metrics"]} for c in rapid_cases]},
        "ovis_region_level_review": {
            "granularity_note": "Ovis boxes remain native multiword regions; no word boxes are synthesized.",
            "word_metric_limitation": "Original word-level spotting score is retained, but it penalizes native regions containing multiple words.",
            "native_word_metric_micro": {
                "text_and_bbox": sum_existing_metrics([c["native_word_box_metrics_unchanged"] for c in ovis_cases], "exact_text_and_bbox"),
                "text_only": sum_existing_metrics([c["native_word_box_metrics_unchanged"] for c in ovis_cases], "text_only")},
            "region_text_fullpage_corpus_metrics": summarize_plain(ovis_text_rows),
            "region_text_fullpage_metrics_strict_json_cases_only": summarize_plain(ovis_valid_text_rows),
            "strict_json_case_count": len(ovis_valid_text_rows),
            "cases": ovis_cases}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=HERE / "results/raw_outputs")
    parser.add_argument("--ground-truth", type=Path, default=HERE / "ground_truth.jsonl")
    parser.add_argument("--output", type=Path, default=HERE / "results/posthoc-capability-review.json")
    args = parser.parse_args()
    gt = load_ground_truth(args.ground_truth)
    files = {"minicpm_table": "minicpm-table-20261030T205102Z.json",
        "minicpm_chart": "minicpm-chart-20261030T205408Z.json",
        "rapid_spotting": "rapid-spotting-20261030T204803Z.json",
        "ovis_spotting": "ovis-spotting-20261030T205452Z.json",
        "ovis_chart": "ovis-chart-20261030T210021Z.json"}
    paths = {name: args.results_dir / filename for name, filename in files.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise SystemExit("Missing saved run files: " + ", ".join(missing))
    result = {"experiment": "SightVault OCR V2 capability post-hoc review",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "no_model_inference": True, "raw_input_files_unchanged": True,
        "ground_truth_sha256": sha256_file(args.ground_truth),
        "posthoc_script_sha256": sha256_file(Path(__file__)),
        "minicpm_native_otsl_tables": mini_table_review(paths["minicpm_table"], gt),
        "minicpm_native_otsl_charts": mini_chart_review(paths["minicpm_chart"], gt),
        "ovis_native_chart_json": ovis_chart_review(paths["ovis_chart"], gt),
        "spotting_granularity_review": spotting_review(paths["rapid_spotting"], paths["ovis_spotting"], gt)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "completed", "output": str(args.output),
        "posthoc_script_sha256": result["posthoc_script_sha256"],
        "input_sha256": {name: sha256_file(path) for name, path in paths.items()}}, indent=2))


if __name__ == "__main__":
    main()
