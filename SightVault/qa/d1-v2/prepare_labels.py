#!/usr/bin/env python3
"""Build the frozen, reviewer-annotated d1 fixture labels from the 62 source images.

IDs are assigned by case-folded source filename order. The tag sets below were
written after visual review of anonymized contact sheets; the model and retrieval
outputs are never inputs to this script.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
OUT = Path(__file__).resolve().parent / "labels.json"
MANIFEST_OUT = Path(__file__).resolve().parent / "image_manifest.json"

CONTENT_TYPE = {
    **{f"I{i:03d}": "photo" for i in (1, 10)},
    **{f"I{i:03d}": "document_page" for i in range(2, 10)},
    **{f"I{i:03d}": "lecture_slide" for i in range(11, 23)},
    **{f"I{i:03d}": "illustration" for i in range(23, 33)},
    **{f"I{i:03d}": "project_board" for i in range(33, 41)},
    **{f"I{i:03d}": "receipt" for i in range(41, 53)},
    **{f"I{i:03d}": "terminal_screenshot" for i in range(53, 63)},
}

ALL_IDS = {f"I{i:03d}" for i in range(1, 63)}
TERMINALS = {f"I{i:03d}" for i in range(53, 63)}
DOCS = {f"I{i:03d}" for i in range(2, 10)}
SLIDES = {f"I{i:03d}" for i in range(11, 23)}
BOARDS = {f"I{i:03d}" for i in range(33, 41)}
RECEIPTS = {f"I{i:03d}" for i in range(41, 53)}
VISUALS = {f"I{i:03d}" for i in range(1, 2)} | {f"I{i:03d}" for i in range(10, 11)} | {
    f"I{i:03d}" for i in range(23, 33)
}
ERRORS = {f"I{i:03d}" for i in range(53, 61)}
CODE = TERMINALS | {"I017", "I018"}
UI = DOCS | SLIDES | BOARDS | TERMINALS
DOCUMENT = DOCS | SLIDES | RECEIPTS
PERSON = {"I001"}

LABEL_DEFINITIONS = {
    "contains_error": "An explicit visible failure, error, traceback, or out-of-memory event; generic technical subject matter is not enough.",
    "contains_terminal": "A terminal/console window is the primary visible content.",
    "contains_code": "Visible executable command/query or source code. Shell commands at a prompt count; prose endpoint examples do not.",
    "contains_receipt": "A point-of-sale receipt or receipt-like itemized purchase record.",
    "contains_document": "A document-like page, lecture slide, or receipt; excludes terminal screens, project boards, photos, and illustrations.",
    "contains_chart": "A plotted data chart with axes, bars, lines, or a pie plot; diagrams and prose about a chart do not count.",
    "contains_table": "An explicit grid of cells with aligned rows and columns; a receipt list or kanban cards do not count.",
    "contains_ui": "Visible application/window chrome or a software interface, including terminal windows.",
    "contains_person": "At least one human figure is visibly present, even if small.",
}


def main() -> None:
    paths = sorted(
        (p for p in SAMPLES.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}),
        key=lambda p: p.name.casefold(),
    )
    if len(paths) != 62:
        raise SystemExit(f"Expected the frozen 62-image demo fixture, found {len(paths)}")

    images = []
    manifest = []
    for index, path in enumerate(paths, start=1):
        image_id = f"I{index:03d}"
        if image_id not in CONTENT_TYPE:
            raise SystemExit(f"Missing explicit label for {image_id}")
        with Image.open(path) as image:
            width, height = image.size
        annotations = {
            "contains_error": image_id in ERRORS,
            "contains_terminal": image_id in TERMINALS,
            "contains_code": image_id in CODE,
            "contains_receipt": image_id in RECEIPTS,
            "contains_document": image_id in DOCUMENT,
            "contains_chart": False,
            "contains_table": False,
            "contains_ui": image_id in UI,
            "contains_person": image_id in PERSON,
            "content_type": CONTENT_TYPE[image_id],
        }
        images.append(
            {
                "id": image_id,
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "width": width,
                "height": height,
                "labels": annotations,
                "annotation_note": (
                    "Only the bridge photo visibly contains people. No image has a plotted chart or a gridded table under the frozen definitions."
                    if image_id == "I001"
                    else None
                ),
            }
        )
        manifest.append(
            {
                "id": image_id,
                "file": path.name,
                "sha256": images[-1]["sha256"],
                "width": width,
                "height": height,
            }
        )

    all_sets = [TERMINALS, DOCS, SLIDES, BOARDS, RECEIPTS, VISUALS, ERRORS, CODE, UI, DOCUMENT, PERSON]
    if set(CONTENT_TYPE) != ALL_IDS or any(not group <= ALL_IDS for group in all_sets):
        raise SystemExit("Annotation IDs do not cover the frozen fixture")

    payload = {
        "dataset": "SightVault synthetic demo 62-image fixture",
        "baseline_commit": "766027c79d4c6f17ba7b88b1df3bc079dfbbd141",
        "annotation": {
            "method": "Single-annotator visual review of anonymized contact sheets and source pixels; labels were frozen before d1 inference.",
            "annotator": "AI agent experiment reviewer; not human ground truth and not independently double-annotated.",
            "retrieval_isolation": "Classification labels are never used to create query text, select EmbeddingGemma candidates, or construct d1 reranking inputs.",
            "limitations": "Synthetic fixture, repeated templates, correlated examples, and low/zero prevalence for person/chart/table. Treat estimates as exploratory.",
            "id_rule": "I001-I062 are assigned by case-folded source filename order; the file-to-ID mapping and SHA-256 are included for reproducibility.",
            "definitions": LABEL_DEFINITIONS,
            "thresholds": [0.5, 0.7, 0.8, 0.9],
            "threshold_policy": "Predeclared fixed thresholds; no threshold fitting on these evaluation images.",
        },
        "images": images,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MANIFEST_OUT.write_text(
        json.dumps(
            {
                "dataset": payload["dataset"],
                "id_rule": payload["annotation"]["id_rule"],
                "images": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(images)} labels to {OUT} and an unlabelled image manifest to {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
