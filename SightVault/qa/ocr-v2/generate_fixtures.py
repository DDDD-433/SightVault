"""Build a deterministic, synthetic OCR ground-truth corpus.

Every expected string originates in the literal fixture definitions below;
no OCR engine is used to create or repair labels. The image bytes are isolated
under qa/ocr-v2 and do not touch the packaged demo or its database.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import PIL
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from metrics import extract_special_tokens

ROOT = Path(__file__).resolve().parent
IMAGES = ROOT / "images"
WIDTH, HEIGHT = 1600, 1100
FONT_SANS = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
FONT_MONO = Path("/System/Library/Fonts/Supplemental/Courier New.ttf")
FONT_MONO_BOLD = Path("/System/Library/Fonts/Supplemental/Courier New Bold.ttf")

CASES: list[dict] = []


def add(case_id, category, title, blocks, *, layout="lines", fields=None,
        headers=None, rows=None, chart=None, transform=None, dark=False,
        tiny=False, columns=None):
    """Add one authored case. `blocks` is source text, never model output."""
    CASES.append({
        "id": case_id, "category": category, "title": title,
        "blocks": blocks, "layout": layout, "fields": fields or {},
        "headers": headers or [], "rows": rows or [], "chart": chart or {},
        "transform": transform, "dark": dark, "tiny": tiny,
        "columns": columns or [],
    })


def _font(size, mono=False, bold=False):
    path = FONT_MONO_BOLD if mono and bold else FONT_MONO if mono else FONT_BOLD if bold else FONT_SANS
    return ImageFont.truetype(str(path), size)


def _safe_text(s: str) -> str:
    return str(s)


def _render(case):
    rgba = case["transform"] == "transparent"
    # White RGB under the transparent pixels is intentional: adapters below
    # composite/convert alpha consistently onto white, keeping black text legible.
    bg = (255, 255, 255, 0) if rgba else (17, 23, 34) if case["dark"] else (247, 246, 242)
    im = Image.new("RGBA" if rgba else "RGB", (WIDTH, HEIGHT), bg)
    d = ImageDraw.Draw(im)
    fg = (230, 237, 246, 255) if case["dark"] else (32, 40, 51, 255)
    muted = (157, 171, 191, 255) if case["dark"] else (87, 100, 119, 255)
    boxes = []
    line_gap = 1.28

    def line(text, x, y, size=27, mono=False, bold=False, color=None, key=None):
        text = _safe_text(text)
        f = _font(size, mono=mono, bold=bold)
        color = color or fg
        bbox = d.textbbox((x, y), text, font=f)
        d.text((x, y), text, font=f, fill=color)
        boxes.append({"text": text, "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
                      "role": key or "text"})
        # The word boxes are ground truth derived from the same rendered glyphs.
        cursor_index = 0
        for token in text.split():
            # Measure from the exact source prefix to preserve proportional spacing.
            start = text.find(token, cursor_index)
            if start < 0:
                continue
            px = x + d.textlength(text[:start], font=f)
            wb = d.textbbox((px, y), token, font=f)
            boxes.append({"text": token, "bbox": [wb[0], wb[1], wb[2], wb[3]],
                          "role": "word"})
            cursor_index = start + len(token)

    title = case["title"]
    if case["layout"] == "terminal":
        d.rectangle((0, 0, WIDTH, 73), fill=(33, 42, 57, 255))
        line(title, 40, 21, 25, mono=True, bold=True, color=(218, 228, 242, 255), key="title")
        y = 105
        size = 18 if case["tiny"] else 23
        for text in case["blocks"]:
            color = (255, 172, 164, 255) if any(t in text for t in ("Error", "ERR!", "E404", "CUDA_ERROR")) else (194, 222, 181, 255) if text.startswith("$") else fg
            line(text, 45, y, size, mono=True, color=color)
            y += int(size * (1.9 if case["tiny"] else 1.8))
    elif case["layout"] in ("receipt", "invoice"):
        paper_x, paper_y, paper_w = 250, 70, 1100
        d.rounded_rectangle((paper_x, paper_y, paper_x + paper_w, 1030), radius=12,
                            fill=(255, 254, 249, 255), outline=(201, 200, 191, 255), width=3)
        line(title, paper_x + 70, paper_y + 54, 38, bold=True, key="title")
        d.line((paper_x + 60, 174, paper_x + paper_w - 60, 174), fill=(100, 106, 115, 255), width=2)
        y = 202
        for idx, text in enumerate(case["blocks"]):
            line(text, paper_x + 72, y, 24 if case["layout"] == "receipt" else 25,
                 mono=case["layout"] == "receipt", bold=text.startswith("TOTAL"))
            y += 46 if text else 27
    elif case["layout"] == "form":
        d.rectangle((90, 80, WIDTH - 90, HEIGHT - 65), outline=(176, 184, 193, 255), width=2)
        line(title, 135, 120, 40, bold=True, key="title")
        d.line((130, 190, WIDTH - 130, 190), fill=(115, 128, 145, 255), width=2)
        y = 232
        for text in case["blocks"]:
            label, sep, value = text.partition(":")
            line(label + sep, 145, y, 28, bold=True)
            line(value.lstrip(), 565, y, 28)
            y += 62
    elif case["layout"] == "table":
        line(title, 100, 90, 40, bold=True, key="title")
        x0, y0, table_w = 100, 205, 1400
        col_w = table_w // len(case["headers"])
        all_rows = [case["headers"], *case["rows"]]
        row_h = min(105, 700 // len(all_rows))
        for ri, vals in enumerate(all_rows):
            y = y0 + ri * row_h
            fill = (221, 230, 243, 255) if ri == 0 else (252, 252, 250, 255)
            d.rectangle((x0, y, x0 + table_w, y + row_h), fill=fill,
                        outline=(157, 168, 181, 255), width=2)
            for ci, value in enumerate(vals):
                x = x0 + ci * col_w
                if ci:
                    d.line((x, y, x, y + row_h), fill=(157, 168, 181, 255), width=2)
                line(str(value), x + 16, y + 27, 22 if ci else 21,
                     bold=(ri == 0), key="header" if ri == 0 else "cell")
    elif case["layout"] == "chart":
        line(title, 95, 75, 40, bold=True, key="title")
        chart = case["chart"]
        line(chart["source"], 100, 132, 18, color=muted)
        left, top, right, bottom = 210, 260, 1190, 860
        d.line((left, top, left, bottom), fill=(45, 54, 67, 255), width=3)
        d.line((left, bottom, right, bottom), fill=(45, 54, 67, 255), width=3)
        line(chart["y_title"], 85, 282, 21, color=muted)
        line(chart["x_title"], 650, 900, 21, color=muted)
        values = chart["values"]
        max_value = max(values.values())
        slot = (right - left) // len(values)
        for idx, (label, val) in enumerate(values.items()):
            bx = left + idx * slot + slot // 5
            bar_h = int((bottom - top) * val / max_value * .82)
            d.rectangle((bx, bottom - bar_h, bx + slot * 3 // 5, bottom), fill=(82, 120, 189, 255))
            line(str(val), bx, bottom - bar_h - 40, 20, bold=True, key="value")
            line(label, bx - 8, bottom + 15, 20, key="label")
        legend = chart.get("legend", "Series: Indexed documents")
        line(legend, 1260, 330, 20, color=muted, key="legend")
    elif case["layout"] == "columns":
        line(title, 85, 74, 42, bold=True, key="title")
        line("REPORT · INTERNAL REFERENCE", 88, 135, 16, color=muted)
        top = 220
        col_w = 690
        for ci, col in enumerate(case["columns"]):
            x = 100 + ci * 740
            for li, text in enumerate(col):
                line(text, x, top + li * 88, 24 if li else 28, bold=li == 0,
                     key="column-heading" if li == 0 else "text")
    elif case["layout"] == "slide":
        d.rectangle((0, 0, 22, HEIGHT), fill=(77, 106, 173, 255))
        line("LECTURE NOTES · WEEK " + case["id"][-2:], 80, 57, 18, color=muted)
        line(title, 80, 145, 50, bold=True, key="title")
        line("CONCEPTS AND WORKED EXAMPLES", 84, 226, 17, color=muted)
        y = 310
        for text in case["blocks"]:
            d.ellipse((95, y + 12, 108, y + 25), fill=(77, 106, 173, 255))
            line(text, 138, y, 28)
            y += 105
    else:
        line(title, 100, 95, 38, bold=True, key="title")
        y = 180
        for text in case["blocks"]:
            line(text, 110, y, 25)
            y += 56

    transform = case["transform"]
    if transform == "rotated90":
        im = im.rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
        old_w = WIDTH
        for box in boxes:
            x0, y0, x1, y1 = box["bbox"]
            box["bbox"] = [y0, old_w - x1, y1, old_w - x0]
    elif transform == "rotated270":
        im = im.rotate(270, expand=True, resample=Image.Resampling.BICUBIC)
        old_h = HEIGHT
        for box in boxes:
            x0, y0, x1, y1 = box["bbox"]
            box["bbox"] = [old_h - y1, x0, old_h - y0, x1]
    elif transform == "blur":
        im = im.filter(ImageFilter.GaussianBlur(radius=0.85))
    elif transform == "jpeg-compression":
        # The final writer below performs the one documented lossy JPEG encode.
        im = im.convert("RGB")

    return im, boxes


def main():
    if not FONT_SANS.is_file() or not FONT_MONO.is_file():
        raise SystemExit("Expected Arial and Courier New fonts on this Mac; see manifest for paths.")
    IMAGES.mkdir(parents=True, exist_ok=True)

    for n, (title, lines) in enumerate([
        ("npm install · Resolver", ["$ npm install", "npm ERR! code ERESOLVE", "npm ERR! ERESOLVE unable to resolve dependency tree", "While resolving: api-shell@2.4.1", "Found: react@19.0.0", "Could not resolve dependency:", 'peer react@"^18.0.0" from legacy-widget@2.3.1', "Log: https://registry.npmjs.org/-/npm/v1", "Date: 2026-10-30 09:14:06 -0400"]),
        ("Python · Missing module", ["$ python app.py", "Traceback (most recent call last):", '  File "app.py", line 2, in <module>', "    import pandas", "ModuleNotFoundError: No module named 'pandas'", "Python 3.12.9 | .venv/bin/python", "Request ID: SV-24018-1I0l"]),
        ("CUDA worker · allocation failure", ["$ python train.py --batch-size 16", "torch.OutOfMemoryError: CUDA_ERROR_OUT_OF_MEMORY", "Tried to allocate 512.00 MiB", "GPU 0 total: 12.00 GiB | free: 96.00 MiB", "Process holds 11.62 GiB", "Sequence length: 4096 | batch_size=16"]),
        ("Client · response codes", ["GET /v1/items?id=OI10l", "Primary response: E404", "Fallback response: E4040", "HTTP status 404 != application code E4040", "Build: v1.10.0 | previous: v1.1.0", "Date: 2026-10-30"]),
        ("Identifier stress · 0/O/1/I/l", ["Order ID: OI-1010 | batch: 0O1Il", "ModuleNotFoundError vs ModuleNotFoundError0", "E404 and E4040 are distinct identifiers", "URL: https://sv.local/v1/files/0O1Il", "Version: 3.12.9-rc1 | price: $10.01"]),
        ("Shell · URL and release", ["$ curl -I https://downloads.sightvault.dev/releases/v2.7.14/arm64", "HTTP/2 200", "Last-Modified: Fri, 30 Oct 2026 13:12:45 GMT", "Content-Length: 10485760", "SHA256: 8f4a90c7d01b2e66", "Package: SightVault-2.7.14-arm64.dmg"]),
        ("ES module · dependency conflict", ["ERESOLVE unable to resolve dependency tree", "Found: vite@6.1.2", "peer vite@\"^5.0.0\" from vite-plugin-legacy@2.8.0", "npm ERR! code ERESOLVE", "Use npm install --legacy-peer-deps only after review.", "Ticket: DEP-8814 | opened 2026-10-29"]),
        ("Database migration · retry", ["Migration 0042 failed", "sqlite3.OperationalError: no such column: asset_id", "Backup: data/sightvault.sqlite3.bak", "Retry after schema v4.2.0 is installed.", "Connection: sqlite:///data/local.db", "Error ref: DB-MIG-0042"]),
        ("Tiny dark terminal · memory warning", ["WARN 03:14:01.027 OOM guard at 91.5%", "ERR 03:14:01.113 cache.limit=4096 MiB", "PID 18402 · retry=0/3 · device=mps:0", "E4040 from endpoint /api/v1/index", "Build v2.14.0+mac.arm64"]),
        ("PowerShell · package check", ["PS> Get-FileHash .\\SightVault-2.7.14.zip -Algorithm SHA256", "Algorithm: SHA256", "Hash: 01a4b5c6d7e8f90123456789abcdef00", "Path: C:\\Users\\Dev\\Downloads\\SightVault-2.7.14.zip", "Verified: 10/30/2026 9:14 AM", "Exit code: 0"]),
    ]):
        add(f"T{n+1:02d}", "terminal", title, lines, layout="terminal", dark=True, tiny=n in (8,))

    receipts = [
        ("Mechanical keyboard", "79.99", "4.80", "84.79", "RC-4201"),
        ("USB-C charger", "24.95", "1.50", "26.45", "RC-4202"),
        ("Notebook and pens", "9.20", "0.55", "9.75", "RC-4203"),
        ("Coffee and muffin", "7.50", "0.45", "7.95", "RC-4204"),
        ("HDMI adapter", "18.49", "1.11", "19.60", "RC-4205"),
        ("Storage drive 1 TB", "68.00", "4.08", "72.08", "RC-4206"),
        ("Sandwich and juice", "12.30", "0.74", "13.04", "RC-4207"),
        ("USB-C cable 2 m", "11.99", "0.72", "12.71", "RC-4208"),
    ]
    for n, (item, subtotal, tax, total, rid) in enumerate(receipts):
        lines = ["SYNTHETIC RECEIPT · no personal data", "Campus Corner | 123 Example Lane", f"Receipt: {rid}", f"Date: 2026-10-{20+n:02d} 18:42", f"Item: {item}", "Qty: 1", f"Subtotal: ${subtotal}", f"Tax (6%): ${tax}", f"TOTAL: ${total}", "PAID · Visa ending 1024"]
        add(f"R{n+1:02d}", "receipt", "CAMPUS CORNER", lines, layout="receipt",
            fields={"receipt_id": rid, "date": f"2026-10-{20+n:02d}", "item": item,
                    "subtotal": f"${subtotal}", "tax": f"${tax}", "total": f"${total}", "payment": "Visa ending 1024"})

    invoices = [
        ("INV-1081", "Northwind Demo LLC", "2026-10-03", "2026-10-24", "1,240.00", "99.20", "1,339.20"),
        ("INV-1082", "Harbor Light Supplies", "2026-10-05", "2026-10-26", "875.50", "70.04", "945.54"),
        ("INV-1083", "Redwood Systems Inc.", "2026-10-07", "2026-10-28", "2,019.99", "161.60", "2,181.59"),
        ("INV-1084", "Quiet River Studio", "2026-10-09", "2026-10-30", "450.00", "36.00", "486.00"),
        ("INV-1085", "Maple Field Office", "2026-10-11", "2026-11-01", "98.75", "7.90", "106.65"),
        ("INV-1086", "Blue Harbor Tools", "2026-10-13", "2026-11-03", "6,440.00", "515.20", "6,955.20"),
    ]
    for n, (iid, vendor, date, due, sub, tax, total) in enumerate(invoices):
        lines = ["INVOICE · fictional test data", f"Supplier: {vendor}", f"Invoice number: {iid}", f"Invoice date: {date}", f"Due date: {due}", f"Subtotal: USD {sub}", f"Sales tax: USD {tax}", f"Amount due: USD {total}", f"Purchase order: PO-{7330+n}", "Terms: Net 21"]
        add(f"I{n+1:02d}", "invoice", "INVOICE", lines, layout="invoice",
            fields={"supplier": vendor, "invoice_number": iid, "invoice_date": date,
                    "due_date": due, "subtotal": f"USD {sub}", "tax": f"USD {tax}",
                    "amount_due": f"USD {total}", "purchase_order": f"PO-{7330+n}"})

    forms = [
        ("Service request", {"Request ID": "SR-10418", "Opened": "2026-10-30", "Status": "Pending review", "Priority": "P2", "Owner": "A. Rivera"}),
        ("Device return form", {"RMA number": "RMA-008413", "Serial number": "0O1I-l774", "Received": "2026-10-28", "Refund": "$129.95", "Condition": "Unopened"}),
        ("Expense claim", {"Claim ID": "EX-22091", "Employee": "Jordan Lee", "Submitted": "10/29/2026", "Meals": "$38.40", "Transport": "$16.25"}),
        ("Access request", {"Ticket": "IAM-7182", "Account": "mcho@example.test", "Role": "Report Viewer", "Expires": "2026-11-30", "Approver": "N. Patel"}),
        ("Lab intake sheet", {"Specimen": "LAB-22A-091", "Collected": "2026-10-27 08:14", "Temperature": "4.0 C", "Batch": "B-00O1", "Result": "Awaiting analysis"}),
        ("Shipping declaration", {"Tracking ID": "SVX-8810-04", "Ship date": "2026-10-26", "Declared value": "$240.00", "Pieces": "2", "Destination": "Suite 410"}),
    ]
    for n, (title, fields) in enumerate(forms):
        lines = [f"Form version: 3.{n+1}.0", "All values are fictional benchmark data.", *[f"{k}: {v}" for k, v in fields.items()]]
        add(f"F{n+1:02d}", "form", title, lines, layout="form", fields=fields)

    tables = [
        ("Quarterly ticket summary", ["Month", "Opened", "Closed", "Backlog"], [["Jul", "128", "121", "14"], ["Aug", "141", "138", "17"], ["Sep", "152", "148", "21"]]),
        ("Model package manifest", ["Model", "Version", "Bytes", "SHA suffix"], [["MiniCPM OCR", "1.0.0", "2600969816", "e8a19c"], ["OvisOCR3", "0.8.0", "1706030528", "d4637ffd"], ["RapidOCR", "1.4.4", "14915192", "82cbf"]]),
        ("Order reconciliation", ["Item", "Qty", "Unit price", "Line total"], [["Cable 1 m", "3", "$7.25", "$21.75"], ["Adapter 2-port", "2", "$14.00", "$28.00"], ["Hub USB-C", "1", "$39.95", "$39.95"]]),
        ("Error frequency", ["Code", "Count", "Last seen", "Severity"], [["E404", "12", "2026-10-29", "Low"], ["E4040", "3", "2026-10-30", "Medium"], ["ERESOLVE", "7", "2026-10-28", "High"]]),
        ("Release checks", ["Build", "Tests", "Duration", "Result"], [["v2.7.12", "140", "38.2 s", "PASS"], ["v2.7.13", "141", "40.1 s", "PASS"], ["v2.7.14", "141", "42.8 s", "REVIEW"]]),
        ("Invoice lines", ["SKU", "Description", "Units", "Amount"], [["A-010", "USB-C cable", "4", "$31.96"], ["B-1O1", "Docking unit", "1", "$149.00"], ["C-009", "Power adapter", "2", "$48.50"]]),
    ]
    for n, (title, headers, rows) in enumerate(tables):
        add(f"TB{n+1:02d}", "table", title, [], layout="table", headers=headers, rows=rows,
            fields={"columns": headers, "rows": rows})

    charts = [
        ("Weekly indexing throughput", "Source: local fixture · 2026-10-30", "Images / minute", {"Mon": 41, "Tue": 52, "Wed": 48, "Thu": 67, "Fri": 72}),
        ("OCR character error rate", "Source: synthetic measurement · v1.4.4", "CER (%)", {"Rapid": 8, "MiniCPM": 3, "Ovis": 2}),
        ("Monthly document count", "Source: demo archive · updated 2026-10-29", "Files", {"Jun": 18, "Jul": 24, "Aug": 31, "Sep": 29}),
        ("Latency by image size", "Source: local CPU sample · 3.12.9", "Milliseconds", {"Small": 14, "Medium": 43, "Large": 119}),
    ]
    for n, (title, source, ytitle, values) in enumerate(charts):
        chart = {"source": source, "y_title": ytitle, "x_title": "Period / category", "values": values, "legend": "Series: Measurement"}
        blocks = [title, source, ytitle, "Period / category", "Series: Measurement"] + [f"{k}: {v}" for k, v in values.items()]
        add(f"C{n+1:02d}", "chart", title, blocks, layout="chart", chart=chart,
            fields={"title": title, "source": source, "y_title": ytitle, "x_title": "Period / category", "values": values})

    columns = [
        ("Incident summary", ["Root cause: missing wheel metadata.", "Observed on Python 3.12.9.", "Package: rapidocr_onnxruntime 1.4.4."], ["Mitigation", "Install from the pinned lock file.", "Retry after dependency verification.", "Ticket OCR-1042 · 2026-10-30"]),
        ("Purchase review", ["Vendor: Harbor Light Supplies.", "Invoice INV-1082 · USD 945.54.", "Due date: 2026-10-26."], ["Approval", "Owner: M. Chen.", "Status: Approved at 14:08 EDT.", "Reference AP-2026-1026"]),
        ("Migration notes", ["Source schema: v4.1.0.", "Target schema: v4.2.0.", "Backup: vault-041.db."], ["Verification", "Rows checked: 1,024.", "Errors: 0.", "Digest: 4d2b8ae0f718"]),
        ("Service bulletin", ["Endpoint: https://api.sv.local/v2/status.", "Region: us-east-1.", "Build: 2026.10.30.7."], ["Availability", "Window: 02:00–02:15 UTC.", "Error budget: 0.25%.", "Reference: OPS-7418"]),
    ]
    for n, (title, left, right) in enumerate(columns):
        blocks = left + right
        add(f"MC{n+1:02d}", "multicolumn", title, blocks, layout="columns", columns=[left, right])

    slides = [
        ("Database normalization", ["First normal form keeps values atomic.", "Second normal form removes partial dependencies.", "Third normal form removes transitive dependencies.", "Goal: reduce update anomalies."]),
        ("Photosynthesis", ["Chloroplasts transform light energy.", "Carbon dioxide + water produce glucose + oxygen.", "Chlorophyll absorbs red and blue wavelengths.", "Light-dependent reactions occur in thylakoids."]),
        ("Conservation of momentum", ["Momentum = mass × velocity.", "Total momentum is conserved in a closed system.", "Elastic collisions conserve kinetic energy.", "Measure initial and final velocity."]),
        ("Relational joins", ["INNER JOIN keeps matching rows.", "LEFT JOIN keeps every row on the left.", "A foreign key connects related tables.", "SELECT customer_id, order_total FROM orders."]),
        ("Supply and demand", ["Demand slopes downward.", "Supply slopes upward.", "Equilibrium occurs where the curves meet.", "A shortage can put upward pressure on price."]),
        ("Secure API design", ["Validate each request at the boundary.", "Return stable error code E401, not internal traces.", "Escape untrusted text before HTML output.", "Version endpoint: /api/v2/items."]),
    ]
    for n, (title, lines) in enumerate(slides):
        add(f"S{n+1:02d}", "slide", title, lines, layout="slide")

    add("ROT01", "rotated", "Rotated invoice", ["Invoice: INV-2094", "Date: 2026-10-30", "Amount due: USD 708.40", "PO: PO-4418"], layout="invoice", fields={"invoice_number": "INV-2094", "date": "2026-10-30", "amount_due": "USD 708.40", "purchase_order": "PO-4418"}, transform="rotated90")
    add("ROT02", "rotated", "Rotated terminal", ["$ pip install local-wheel.whl", "ERROR: No matching distribution found", "Python 3.12.9 | macOS arm64", "Ref: PY-0O1I-772"], layout="terminal", dark=True, transform="rotated270")
    add("A01", "transparency", "Transparent maintenance note", ["Window: 2026-11-02 01:00 UTC", "Service: ocr-indexer", "Action: restart after model update", "Change ID: CHG-7801"], layout="form", transform="transparent", fields={"Window": "2026-11-02 01:00 UTC", "Service": "ocr-indexer", "Action": "restart after model update", "Change ID": "CHG-7801"})
    add("A02", "transparency", "Transparent shipment record", ["Shipment: PKG-44109", "Date: 2026-10-30", "Weight: 1.25 kg", "Declared value: $80.00"], layout="form", transform="transparent", fields={"Shipment": "PKG-44109", "Date": "2026-10-30", "Weight": "1.25 kg", "Declared value": "$80.00"})
    add("D01", "blur", "Blurred dark incident trace", ["ERESOLVE · dependency tree conflict", "ModuleNotFoundError: fastapi", "Ticket: OCR-4102 | date 2026-10-30", "Build v1.8.0 | E4040"], layout="terminal", dark=True, tiny=True, transform="blur")
    add("D02", "jpeg_compression", "Compressed receipt copy", ["RECEIPT RC-77510", "Date: 2026-10-29", "Item: Storage drive 2 TB", "Subtotal: $119.99", "Tax: $7.20", "TOTAL: $127.19"], layout="receipt", transform="jpeg-compression", fields={"receipt_id": "RC-77510", "date": "2026-10-29", "item": "Storage drive 2 TB", "subtotal": "$119.99", "tax": "$7.20", "total": "$127.19"})

    manifest = {
        "experiment": "SightVault OCR_V2",
        "baseline_commit": "766027c79d4c6f17ba7b88b1df3bc079dfbbd141",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "generate_fixtures.py",
        "label_provenance": "Authored literal fixture strings and structured fields in generate_fixtures.py; no OCR engine or generated OCR output contributes to labels.",
        "source_kinds": ["isolated synthetic fixture", "manual literal text/field fixtures"],
        "image_count": len(CASES),
        "category_counts": {},
        "images": [],
        "font_files": {},
        "python": sys.version,
        "platform": platform.platform(),
        "pillow": PIL.__version__,
    }
    for font_path in (FONT_SANS, FONT_BOLD, FONT_MONO, FONT_MONO_BOLD):
        if font_path.is_file():
            manifest["font_files"][font_path.name] = {
                "relative_system_path": str(font_path.relative_to(Path("/System/Library/Fonts"))),
                "sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
            }

    gt_path = ROOT / "ground_truth.jsonl"
    rows = []
    for case in CASES:
        image, boxes = _render(case)
        ext = ".jpg" if case["transform"] == "jpeg-compression" else ".png"
        image_path = IMAGES / (case["id"] + ext)
        if ext == ".jpg":
            image.save(image_path, format="JPEG", quality=38, optimize=False, subsampling=2)
        else:
            image.save(image_path, format="PNG", optimize=False)
        text_parts = [case["title"]]
        if case["layout"] == "chart":
            chart = case["chart"]
            text_parts += [chart["source"], chart["y_title"], chart["x_title"], chart.get("legend", "Series: Measurement")]
            text_parts += [f"{k}: {v}" for k, v in chart["values"].items()]
        elif case["layout"] == "table":
            text_parts += [" ".join(case["headers"])]
            text_parts += [" ".join(row) for row in case["rows"]]
        elif case["layout"] == "columns":
            text_parts += ["REPORT · INTERNAL REFERENCE"]
            text_parts += case["blocks"]
        elif case["layout"] == "slide":
            text_parts += ["LECTURE NOTES · WEEK " + case["id"][-2:], "CONCEPTS AND WORKED EXAMPLES"]
            text_parts += case["blocks"]
        else:
            text_parts += case["blocks"]
        expected = "\n".join(x for x in text_parts if x != "")
        special = extract_special_tokens(expected)
        entry = {
            "id": case["id"], "category": case["category"], "path": str(image_path.relative_to(ROOT)),
            "layout": case["layout"], "transform": case["transform"],
            "width": image.width, "height": image.height, "mode": image.mode,
            "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "bytes": image_path.stat().st_size,
        }
        manifest["images"].append(entry)
        manifest["category_counts"][case["category"]] = manifest["category_counts"].get(case["category"], 0) + 1
        rows.append({
            "id": case["id"], "category": case["category"], "image": entry,
            "expected_text": expected, **special,
            "fields": case["fields"], "table_headers": case["headers"], "table_rows": case["rows"],
            "chart": case["chart"], "line_boxes_xyxy_px": [b for b in boxes if b["role"] != "word"],
            "word_boxes_xyxy_px": [b for b in boxes if b["role"] == "word"],
            "fixture_label_source": "authored literal strings/fields; rendered with PIL; independent of OCR output",
        })
    gt_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    manifest["ground_truth_file"] = {"path": gt_path.name, "sha256": hashlib.sha256(gt_path.read_bytes()).hexdigest()}
    generator = Path(__file__)
    manifest["generator_sha256"] = hashlib.sha256(generator.read_bytes()).hexdigest()
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(CASES), "categories": manifest["category_counts"], "manifest": str(ROOT / 'manifest.json')}, indent=2))


if __name__ == "__main__":
    main()
