#!/usr/bin/env python3
"""Create a tiny, separate diagnostic set for chart/table positives and people crops.

The two people crops come only from the already-shipped public bridge photo; the
synthetic chart/table images are deterministic Pillow drawings. None is added to
the demo samples, database, retrieval candidate pool, or 62-image baseline.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "diagnostic_supplement"
MANIFEST = HERE / "diagnostic_supplement_manifest.json"
IMAGE_MANIFEST = HERE / "diagnostic_supplement_image_manifest.json"


def font(size: int = 30):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def text(draw: ImageDraw.ImageDraw, xy, value: str, size: int = 30, fill=(25, 35, 48)) -> None:
    draw.text(xy, value, font=font(size), fill=fill)


def make_bar_chart(path: Path) -> None:
    im = Image.new("RGB", (1000, 700), "white")
    d = ImageDraw.Draw(im)
    text(d, (60, 30), "Quarterly demand", 46)
    left, right, top, bottom = 190, 920, 150, 570
    for n in range(5):
        y = bottom - n * 100
        d.line((left, y, right, y), fill=(220, 226, 232), width=2)
        text(d, (125, y - 14), str(n * 20), 22, (80, 90, 100))
    d.line((left, top, left, bottom), fill=(45, 55, 65), width=4)
    d.line((left, bottom, right, bottom), fill=(45, 55, 65), width=4)
    values = [45, 75, 55, 90]
    labels = ["Q1", "Q2", "Q3", "Q4"]
    colors = [(46, 125, 176), (243, 156, 18), (39, 174, 96), (142, 68, 173)]
    width = 105
    for i, (value, label, color) in enumerate(zip(values, labels, colors)):
        x = 260 + i * 165
        y = bottom - value * 4
        d.rectangle((x, y, x + width, bottom), fill=color)
        text(d, (x + 24, y - 34), str(value), 22)
        text(d, (x + 28, bottom + 22), label, 22)
    text(d, (600, 640), "Orders per quarter", 24, (80, 90, 100))
    im.save(path)


def make_line_chart(path: Path) -> None:
    im = Image.new("RGB", (1000, 700), "white")
    d = ImageDraw.Draw(im)
    text(d, (60, 30), "Monthly visitors", 46)
    left, right, top, bottom = 180, 920, 150, 570
    for n in range(5):
        y = bottom - n * 100
        d.line((left, y, right, y), fill=(220, 226, 232), width=2)
        text(d, (110, y - 14), str(n * 25), 22, (80, 90, 100))
    d.line((left, top, left, bottom), fill=(45, 55, 65), width=4)
    d.line((left, bottom, right, bottom), fill=(45, 55, 65), width=4)
    values = [18, 42, 33, 73, 61, 88]
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    points = []
    for i, value in enumerate(values):
        x = left + 55 + i * 125
        y = bottom - value * 4
        points.append((x, y))
        text(d, (x - 15, bottom + 20), labels[i], 20, (80, 90, 100))
    d.line(points, fill=(26, 115, 232), width=8, joint="curve")
    for x, y in points:
        d.ellipse((x - 10, y - 10, x + 10, y + 10), fill=(26, 115, 232), outline="white", width=2)
    text(d, (600, 640), "Month", 24, (80, 90, 100))
    im.save(path)


def make_table(path: Path, title: str, headers: list[str], rows: list[list[str]]) -> None:
    im = Image.new("RGB", (1000, 700), "white")
    d = ImageDraw.Draw(im)
    text(d, (55, 35), title, 44)
    x0, y0 = 60, 155
    widths = [255, 210, 210, 205]
    row_h = 92
    x_edges = [x0]
    for width in widths:
        x_edges.append(x_edges[-1] + width)
    values = [headers, *rows]
    for r, row in enumerate(values):
        top = y0 + r * row_h
        bottom = top + row_h
        fill = (224, 235, 245) if r == 0 else ((249, 251, 253) if r % 2 else (238, 243, 248))
        d.rectangle((x0, top, x_edges[-1], bottom), fill=fill)
        for edge in x_edges:
            d.line((edge, top, edge, bottom), fill=(54, 73, 91), width=3)
        d.line((x0, top, x_edges[-1], top), fill=(54, 73, 91), width=3)
        d.line((x0, bottom, x_edges[-1], bottom), fill=(54, 73, 91), width=3)
        for c, value in enumerate(row):
            text(d, (x_edges[c] + 14, top + 30), value, 24, (22, 37, 51))
    im.save(path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_bar_chart(OUT / "chart-bars.png")
    make_line_chart(OUT / "chart-line.png")
    make_table(
        OUT / "table-inventory.png",
        "Inventory summary",
        ["Item", "Count", "Location", "Status"],
        [["Camera", "4", "Shelf A", "Ready"], ["Tripod", "2", "Shelf B", "Ready"], ["Cable", "7", "Drawer 3", "Check"]],
    )
    make_table(
        OUT / "table-monthly.png",
        "Monthly totals",
        ["Month", "Orders", "Returns", "Net"],
        [["January", "42", "3", "39"], ["February", "55", "5", "50"], ["March", "61", "4", "57"]],
    )

    bridge = Image.open(ROOT / "samples" / "bridge_photo.png").convert("RGB")
    # Separate visible pedestrian clusters from the same existing public source photo.
    # These are useful image-level positives but remain one source scene, so never
    # count them as independent photographs in claims about generalization.
    crop_specs = [
        ("person-crop-left-group.png", (0, 465, 50, 535)),
        ("person-crop-center-group.png", (35, 465, 80, 535)),
    ]
    for name, box in crop_specs:
        crop = bridge.crop(box)
        crop.resize((crop.width * 6, crop.height * 6), Image.Resampling.LANCZOS).save(OUT / name)

    positive_definitions = {
        "chart": "A standalone plotted data visualization with axes and bars or a connected line.",
        "table": "A standalone data table with visible row/column cell borders and headers.",
        "person_crop": "A crop from the existing bridge photograph that visibly contains pedestrians; both crops share one source image.",
    }
    tags = {
        "contains_error": False,
        "contains_terminal": False,
        "contains_code": False,
        "contains_receipt": False,
        "contains_document": False,
        "contains_chart": False,
        "contains_table": False,
        "contains_ui": False,
        "contains_person": False,
    }
    records = []
    declarations = [
        ("D01", "chart-bars.png", "chart", {**tags, "contains_chart": True}),
        ("D02", "chart-line.png", "chart", {**tags, "contains_chart": True}),
        ("D03", "table-inventory.png", "table", {**tags, "contains_table": True}),
        ("D04", "table-monthly.png", "table", {**tags, "contains_table": True}),
        ("D05", "person-crop-left-group.png", "photo", {**tags, "contains_person": True}),
        ("D06", "person-crop-center-group.png", "photo", {**tags, "contains_person": True}),
    ]
    for image_id, file_name, content_type, labels in declarations:
        path = OUT / file_name
        with Image.open(path) as image:
            width, height = image.size
        records.append(
            {
                "id": image_id,
                "file": file_name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "width": width,
                "height": height,
                "content_type": content_type,
                "labels": labels,
                "source": (
                    {
                        "kind": "deterministic synthetic Pillow fixture",
                        "generator": "make_diagnostic_supplement.py",
                    }
                    if image_id.startswith("D0") and image_id not in {"D05", "D06"}
                    else {
                        "kind": "crop from baseline I001",
                        "url": "https://raw.githubusercontent.com/google-gemma/cookbook/refs/heads/main/apps/sample-data/GoldenGate.png",
                        "crop_box_xyxy": list(next(box for name, box in crop_specs if name == file_name)),
                        "pixels_upscaled_by": 6,
                        "independence_note": "Both person crops come from the same original photo and are correlated diagnostics, not independent source images.",
                    }
                ),
            }
        )
    manifest = {
        "dataset": "SightVault d1 diagnostic supplement; separate from the 62-image baseline and retrieval candidate pool",
        "labels": positive_definitions,
        "images": records,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    inference_manifest = {
        "dataset": manifest["dataset"],
        "images": [
            {key: record[key] for key in ("id", "file", "sha256", "width", "height")}
            for record in records
        ],
    }
    IMAGE_MANIFEST.write_text(json.dumps(inference_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(records)} diagnostic images to {OUT}, {MANIFEST}, and label-free {IMAGE_MANIFEST}")


if __name__ == "__main__":
    main()
