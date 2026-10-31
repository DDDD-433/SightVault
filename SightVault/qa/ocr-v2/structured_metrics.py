"""Ground-truth metrics for structured OCR outputs and coordinate-safe boxes.

Returned markup is parsed as inert text only. Box IoU is reported only when the
backend's source documents/code establish the coordinate unit and image frame.
"""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
import json
import math

from metrics import extract_special_tokens, normalize_for_distance


class TableTextParser(HTMLParser):
    """Read table cells without rendering or executing returned markup."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.current_row = None
        self.current_cell = None
        self.opened_table = False
        self.closed_table = False
        self.malformed_nesting = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self.opened_table = True
        elif tag == "tr":
            if self.current_row is not None:
                self.malformed_nesting = True
            self.current_row = []
        elif tag in ("td", "th"):
            if self.current_row is None or self.current_cell is not None:
                self.malformed_nesting = True
            else:
                self.current_cell = []
        elif tag == "br" and self.current_cell is not None:
            self.current_cell.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th"):
            if self.current_cell is None or self.current_row is None:
                self.malformed_nesting = True
            else:
                self.current_row.append(normalize_for_distance("".join(self.current_cell)))
                self.current_cell = None
        elif tag == "tr":
            if self.current_row is None or self.current_cell is not None:
                self.malformed_nesting = True
            else:
                self.rows.append(self.current_row)
                self.current_row = None
        elif tag == "table":
            self.closed_table = True

    def handle_data(self, data):
        if self.current_cell is not None:
            self.current_cell.append(data)

    @property
    def structurally_valid(self):
        return (self.opened_table and self.closed_table and not self.malformed_nesting
            and self.current_cell is None and self.current_row is None and bool(self.rows)
            and all(self.rows) and all(len(row) == len(self.rows[0]) for row in self.rows))


class DocumentContentParser(HTMLParser):
    """Extract text from structured content as inert markup, without rendering it."""
    _SEPARATORS = {"br", "div", "p", "td", "th", "tr", "li"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SEPARATORS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag.lower() in self._SEPARATORS:
            self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)


def safe_json(raw: str):
    """Parse exact JSON output; do not strip fences, repair syntax, or execute it."""
    try:
        return json.loads(raw), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _prf(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall
          else (0.0 if tp + fp + fn else None))
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision,
            "recall": recall, "f1": f1, "ground_truth_support": tp + fn,
            "predicted_support": tp + fp}


def _counter_prf(expected_items, predicted_items):
    expected = Counter(expected_items)
    predicted = Counter(predicted_items)
    tp = sum((expected & predicted).values())
    fp = sum((predicted - expected).values())
    fn = sum((expected - predicted).values())
    return _prf(tp, fp, fn)


def _numeric_tokens(values):
    tokens = []
    for value in values:
        parsed = extract_special_tokens(str(value))
        for category in ("dates", "times", "versions", "prices", "numbers"):
            tokens.extend(parsed[category])
    return tokens


def evaluate_kie(expected: dict, raw: str):
    parsed, error = safe_json(raw)
    parsed_object = parsed if isinstance(parsed, dict) else {}
    expected = {str(k): str(v) for k, v in expected.items()}
    predicted = {str(k): str(v) for k, v in parsed_object.items()}
    expected_pairs = set(expected.items())
    predicted_pairs = set(predicted.items())
    tp = len(expected_pairs & predicted_pairs)
    fp = len(predicted_pairs - expected_pairs)
    fn = len(expected_pairs - predicted_pairs)
    exact_keys = isinstance(parsed, dict) and set(parsed) == set(expected)
    all_string_values = isinstance(parsed, dict) and all(isinstance(value, str) for value in parsed.values())
    return {
        "json_parse_success": error is None,
        "serialization_valid": error is None,
        "schema_valid": error is None and exact_keys and all_string_values,
        "parse_error": error,
        "json_object": parsed_object,
        "expected_keys": len(expected), "predicted_keys": len(predicted),
        "missing_keys": sorted(set(expected) - set(predicted)),
        "extra_keys": sorted(set(predicted) - set(expected)),
        "exact_schema": error is None and exact_keys and all_string_values,
        "exact_match": error is None and parsed == expected,
        "fields": _prf(tp, fp, fn),
    }


_ALLOWED_DOC_CATEGORIES = {
    "abstract", "algorithm", "aside_text", "chart", "content", "display_formula",
    "doc_title", "figure_title", "footer", "footer_image", "footnote", "formula_number",
    "header", "header_image", "image", "inline_formula", "number", "paragraph_title",
    "reference_content", "seal", "table", "text", "vertical_text", "vision_footnote",
}


def _valid_ovis_bbox(bbox):
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    if any(isinstance(value, bool) or not isinstance(value, int) for value in bbox):
        return False
    x1, y1, x2, y2 = bbox
    return 0 <= x1 < x2 < 1000 and 0 <= y1 < y2 < 1000


def _validate_ovis_document(parsed):
    if not isinstance(parsed, list) or not parsed:
        return False, False, []
    elements_valid = True
    tables = []
    for item in parsed:
        if not isinstance(item, dict) or set(item) != {"bbox", "category", "content"}:
            elements_valid = False
            continue
        if (not _valid_ovis_bbox(item.get("bbox")) or item.get("category") not in _ALLOWED_DOC_CATEGORIES
                or not isinstance(item.get("content"), str)):
            elements_valid = False
        if item.get("category") == "table" and isinstance(item.get("content"), str):
            tables.append(item["content"])
    return True, elements_valid, tables


def evaluate_ovis_document_parse(ground_truth: dict, raw: str, bbox_contract=None):
    """Score Ovis's official document-structure JSON without rendering markup.

    The benchmark's authored labels are line-level boxes, while this task returns
    semantic page elements. We validate the pinned normalized coordinate frame
    and box bounds, but do not claim line-to-element IoU as an apples-to-apples
    metric.
    """
    from metrics import evaluate_plain

    parsed, error = safe_json(raw)
    json_parse_success = error is None
    is_array, schema_valid, _ = _validate_ovis_document(parsed)
    elements = parsed if isinstance(parsed, list) else []
    boxes = [item.get("bbox") for item in elements if isinstance(item, dict) and "bbox" in item]
    valid_boxes = sum(_valid_ovis_bbox(box) for box in boxes)
    content_parts = []
    visual_categories = {"chart", "footer_image", "header_image", "image"}
    for item in elements:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            continue
        if item.get("category") in visual_categories:
            continue
        parser = DocumentContentParser()
        try:
            parser.feed(item["content"])
            parser.close()
            content_parts.append("".join(parser.parts))
        except Exception:
            # Keep malformed content as literal text. It is never rendered or
            # executed, and malformed structure is already counted above.
            content_parts.append(item["content"])
    text = "\n".join(content_parts)
    return {
        "json_parse_success": json_parse_success,
        "serialization_valid": json_parse_success,
        "schema_valid": json_parse_success and is_array and schema_valid,
        "parse_error": error,
        "element_count": len(elements),
        "bbox_geometry": {"coordinate_contract": bbox_contract,
            "bbox_count": len(boxes), "valid_bbox_count": valid_boxes,
            "all_bboxes_valid": bool(elements) and len(boxes) == len(elements)
                and valid_boxes == len(elements)},
        "text_metrics": evaluate_plain(ground_truth, text),
        "text_characters_after_inert_markup_parse": len(text),
    }


def _parse_inert_html_table(raw):
    parser = TableTextParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return [], [], False
    rows = parser.rows
    if not rows:
        return [], [], False
    return rows[0], rows[1:], parser.structurally_valid


def _extract_table(parsed, raw):
    """Extract cells and separately validate each supported serialization shape."""
    if isinstance(parsed, dict) and "columns" in parsed and "rows" in parsed:
        columns_raw, rows_raw = parsed.get("columns"), parsed.get("rows")
        schema_valid = (set(parsed) == {"columns", "rows"} and isinstance(columns_raw, list)
            and isinstance(rows_raw, list) and bool(columns_raw) and all(isinstance(x, str) for x in columns_raw)
            and all(isinstance(row, list) and len(row) == len(columns_raw)
                and all(isinstance(cell, str) for cell in row) for row in rows_raw))
        columns = [str(x) for x in columns_raw] if isinstance(columns_raw, list) else []
        rows = [[str(x) for x in row] for row in rows_raw if isinstance(row, list)] if isinstance(rows_raw, list) else []
        return columns, rows, "json_object", True, schema_valid, True
    if isinstance(parsed, list):
        json_valid, doc_schema_valid, table_contents = _validate_ovis_document(parsed)
        for content in table_contents:
            columns, rows, html_valid = _parse_inert_html_table(content)
            if columns or rows:
                return (columns, rows, "ovis_document_json_with_inert_html_table", True,
                    doc_schema_valid and html_valid, json_valid)
        return [], [], "json_document_without_table", True, doc_schema_valid and bool(table_contents), json_valid
    columns, rows, html_valid = _parse_inert_html_table(raw)
    if columns or rows:
        return columns, rows, "inert_html_table", False, None, html_valid
    return [], [], None, False, False, False


def evaluate_table(expected_columns: list, expected_rows: list, raw: str):
    parsed, json_error = safe_json(raw)
    columns, rows, mode, json_parse_success, schema_valid, serialization_valid = _extract_table(parsed, raw)
    json_parse_success = json_error is None
    serialization_valid = serialization_valid or json_parse_success
    if json_parse_success and mode is None:
        mode = "json_wrong_schema"
    expected_columns = [str(x) for x in expected_columns]
    expected_rows = [[str(x) for x in row] for row in expected_rows]
    expected_cells = expected_columns + [cell for row in expected_rows for cell in row]
    predicted_cells = columns + [cell for row in rows for cell in row]
    all_cells = _counter_prf(expected_cells, predicted_cells)
    error = None if mode and serialization_valid else json_error
    return {
        "serialization_valid": serialization_valid,
        "schema_valid": schema_valid,
        "json_parse_success": json_parse_success,
        "parse_error": error,
        "parse_mode": mode,
        "predicted_columns": columns,
        "predicted_rows": rows,
        "exact_table": columns == expected_columns and rows == expected_rows,
        "exact_header": columns == expected_columns,
        "exact_row_count": len(rows) == len(expected_rows),
        "cells": all_cells,
        "numeric_values": _counter_prf(_numeric_tokens(expected_cells), _numeric_tokens(predicted_cells)),
    }


def _extract_chart_pairs(parsed, raw):
    if isinstance(parsed, dict) and isinstance(parsed.get("values"), dict):
        return [(str(k), str(v)) for k, v in parsed["values"].items()], parsed, "json"
    columns, rows, html_valid = _parse_inert_html_table(raw)
    pairs = [(row[0], row[-1]) for row in rows if len(row) >= 2]
    return pairs, {}, "inert_html_table" if html_valid and pairs else None


def evaluate_chart(expected: dict, raw: str):
    parsed, error = safe_json(raw)
    pairs, parsed_chart, mode = _extract_chart_pairs(parsed, raw)
    expected_values = {str(k): str(v) for k, v in expected.get("values", {}).items()}
    expected_pairs = list(expected_values.items())
    value_metrics = _counter_prf(expected_pairs, pairs)
    expected_keys = {"title", "source", "x_title", "y_title", "values"}
    json_schema_valid = False
    invalid_fields = []
    if mode == "json":
        json_schema_valid = isinstance(parsed_chart, dict) and set(parsed_chart) == expected_keys
        for key in ("title", "source", "x_title"):
            if not isinstance(parsed_chart.get(key), str):
                invalid_fields.append(key)
        y_title = parsed_chart.get("y_title")
        if not (isinstance(y_title, str) or isinstance(y_title, list) and all(isinstance(x, str) for x in y_title)):
            invalid_fields.append("y_title")
        values = parsed_chart.get("values")
        if not isinstance(values, dict) or not values or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            invalid_fields.append("values")
        json_schema_valid = json_schema_valid and not invalid_fields
    exact_headers = None
    if mode == "json":
        expected_header = {key: str(expected.get(key) or "None") for key in ("title", "source", "x_title", "y_title")}
        exact_headers = all(parsed_chart.get(key) == expected_header[key] for key in expected_header)
    exact_chart = (mode == "json" and json_schema_valid and exact_headers is True
        and {str(k): str(v) for k, v in parsed_chart.get("values", {}).items()} == expected_values)
    numeric_expected = _numeric_tokens(expected_values.values())
    numeric_predicted = _numeric_tokens(value for _, value in pairs)
    serialization_valid = error is None or mode == "inert_html_table"
    if error is None and mode is None:
        mode = "json_wrong_schema"
    return {
        "serialization_valid": serialization_valid,
        "schema_valid": json_schema_valid if mode == "json" else (False if error is None else None),
        "json_parse_success": error is None,
        "parse_error": None if serialization_valid else error,
        "schema_errors": invalid_fields,
        "parse_mode": mode,
        "predicted_chart": parsed_chart,
        "value_pairs": value_metrics,
        "numeric_values": _counter_prf(numeric_expected, numeric_predicted),
        "exact_headers": exact_headers,
        "exact_chart": exact_chart,
    }


def _box_points(box):
    if not isinstance(box, (tuple, list)):
        return None, "malformed_box"
    if len(box) == 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in box):
        points = [(float(box[0]), float(box[1])), (float(box[2]), float(box[3]))]
    elif len(box) >= 4 and all(isinstance(point, (tuple, list)) and len(point) >= 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point[:2]) for point in box):
        points = [(float(point[0]), float(point[1])) for point in box]
    else:
        return None, "malformed_box"
    if not all(math.isfinite(value) for point in points for value in point):
        return None, "non_finite_coordinate"
    return points, None


def _validate_box(box, width, height, coordinate_frame):
    points, error = _box_points(box)
    if error:
        return None, error
    values = [value for point in points for value in point]
    if coordinate_frame == "normalized_0_1000_input_raster_top_left":
        if any(not float(value).is_integer() for value in values):
            return None, "non_integer_normalized_coordinate"
        if any(value < 0 or value >= 1000 for value in values):
            return None, "out_of_bounds"
        points = [(x * width / 1000.0, y * height / 1000.0) for x, y in points]
    elif coordinate_frame == "pixel_input_raster_top_left":
        if any(x < 0 or x > width or y < 0 or y > height for x, y in points):
            return None, "out_of_bounds"
    else:
        return None, "unknown_coordinate_frame"
    x_values, y_values = [x for x, _ in points], [y for _, y in points]
    rect = [min(x_values), min(y_values), max(x_values), max(y_values)]
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        return None, "degenerate_geometry"
    return rect, None


def _iou(a, b):
    if not a or not b:
        return 0.0
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def evaluate_boxes(expected: list, predicted: list, width: int, height: int, *, coordinate_frame: str):
    supported_frames = {"pixel_input_raster_top_left", "normalized_0_1000_input_raster_top_left"}
    if coordinate_frame not in supported_frames:
        return {"status": "unsupported_frame", "coordinate_frame": coordinate_frame,
            "reason": "No pinned source established this output coordinate unit and image frame; IoU was not calculated.",
            "matched_mean_iou": None, "exact_text_and_bbox": None, "text_only": None,
            "expected_boxes": len(expected), "predicted_boxes": len(predicted)}
    refs = []
    for item in expected:
        rect, error = _validate_box(item.get("bbox"), width, height, "pixel_input_raster_top_left")
        if error:
            raise ValueError(f"Invalid authored ground-truth box: {item.get('text')!r}: {error}")
        refs.append((normalize_for_distance(item.get("text", "")), rect))
    hyps, invalid = [], Counter()
    predicted_text = []
    for item in predicted:
        text = normalize_for_distance(item.get("text", "")) if isinstance(item, dict) else ""
        predicted_text.append(text)
        bbox = item.get("bbox") if isinstance(item, dict) else None
        rect, error = _validate_box(bbox, width, height, coordinate_frame)
        if error:
            invalid[error] += 1
        else:
            hyps.append((text, rect))
    pairs = []
    for i, (ref_text, ref_box) in enumerate(refs):
        for j, (hyp_text, hyp_box) in enumerate(hyps):
            if ref_text == hyp_text:
                overlap = _iou(ref_box, hyp_box)
                pairs.append((overlap, i, j))
    matched_ref, matched_hyp, overlaps = set(), set(), []
    for overlap, i, j in sorted(pairs, reverse=True):
        if i not in matched_ref and j not in matched_hyp and overlap >= 0.5:
            matched_ref.add(i); matched_hyp.add(j); overlaps.append(overlap)
    invalid_count = sum(invalid.values())
    tp = len(matched_ref)
    fp = (len(hyps) - len(matched_hyp)) + invalid_count
    fn = len(refs) - len(matched_ref)
    return {
        "status": "scored", "coordinate_frame": coordinate_frame,
        "bbox_iou_threshold": 0.5, "matched_mean_iou": sum(overlaps) / len(overlaps) if overlaps else None,
        "exact_text_and_bbox": _prf(tp, fp, fn),
        "text_only": _counter_prf([text for text, _ in refs], predicted_text),
        "expected_boxes": len(refs), "predicted_boxes": len(predicted),
        "valid_predicted_boxes": len(hyps), "invalid_predicted_boxes": invalid_count,
        "invalid_geometry_reasons": dict(invalid),
    }
