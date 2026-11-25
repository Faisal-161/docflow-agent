"""Generate a labeled set of synthetic invoices for evaluation.

50 invoices across 9 scenarios (clean, large, duplicate, unknown vendor, blocked vendor, math error,
over PO budget, missing PO, closed PO), 3 layouts, and 3 formats: plain text, digital PDF, and a
scanned-style PNG that must go through OCR. Each invoice has ground-truth fields and the action a
careful AP clerk would take.

Run:  python eval/generate.py   (writes eval/dataset/ and copies a few into data/samples/)
"""

from __future__ import annotations

import json
import random
import shutil
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "dataset"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

VENDORS = {  # vendor id -> how the name appears on their invoices, and their PO
    "V-1001": ("Acme Office Supplies Inc.", "PO-2201", "INV-"),
    "V-1002": ("Northwind Cloud Services LLC", "PO-2202", "NW-"),
    "V-1003": ("Bluepeak Logistics Ltd", "PO-2203", "BPL-"),
    "V-1004": ("Greenleaf Facilities Co.", "PO-2204", "GF-"),
    "V-1005": ("Orion Data Labs Inc.", "PO-2205", "ODL-"),
}
ITEMS = [
    ("Printer paper A4 box", 24.99),
    ("Toner cartridge black", 89.50),
    ("Ergonomic office chair", 249.00),
    ("Cloud compute hours", 0.85),
    ("Managed database plan", 420.00),
    ("Freight shipping pallet", 135.00),
    ("Same-day courier", 48.75),
    ("Janitorial service visit", 180.00),
    ("HVAC maintenance", 310.00),
    ("Data labeling batch", 650.00),
    ("Annotation QA review", 95.00),
    ("Standing desk frame", 389.00),
]
SCENARIOS = [  # (name, count, expected action, approver)
    ("clean", 14, "approve_payment", "ap_clerk"),
    ("large", 6, "approve_payment", "finance_manager"),
    ("duplicate", 5, "reject", "ap_clerk"),
    ("unknown_vendor", 5, "escalate", "finance_manager"),
    ("blocked_vendor", 3, "escalate", "finance_manager"),
    ("math_error", 5, "request_info", "ap_clerk"),
    ("over_budget", 4, "escalate", "finance_manager"),
    ("no_po", 4, "request_info", "ap_clerk"),
    ("closed_po", 4, "request_info", "ap_clerk"),
]
POSTED = {"V-1001": "INV-1040", "V-1002": "NW-88190", "V-1003": "BPL-5512", "V-1004": "GF-0311", "V-1005": "ODL-7001"}


def fmt_date(d: date, style: int) -> str:
    return [d.isoformat(), d.strftime("%m/%d/%Y"), d.strftime("%d %B %Y")][style]


def fmt_due(d: date, style: int) -> str:
    return [d.strftime("%b %d, %Y"), d.strftime("%b %d, %Y"), d.isoformat()][style]


def make_case(rng: random.Random, scenario: str, n: int) -> dict:
    vid = rng.choice(list(VENDORS))
    vendor, po, prefix = VENDORS[vid]
    if scenario == "unknown_vendor":
        vendor, po, prefix = (
            rng.choice(["Zephyr Consulting LLC", "Quartz Media Group Inc.", "Harbor Lane Catering"]),
            rng.choice(["PO-2201", "PO-2203", None]),
            "ZC-",
        )
    elif scenario == "blocked_vendor":
        vendor, po, prefix = "Summit Print Co", "PO-2206", "SP-"
    elif scenario == "over_budget":
        vid = rng.choice(["V-1001", "V-1003"])
        vendor, _, prefix = VENDORS[vid]
        po = {"V-1001": "PO-2207", "V-1003": "PO-2208"}[vid]
    elif scenario == "no_po":
        po = None
    elif scenario == "closed_po":
        vid = "V-1002"
        vendor, _, prefix = VENDORS[vid]
        po = "PO-2190"

    k = rng.randint(1, 4)
    lines = []
    for desc, price in rng.sample(ITEMS, k):
        qty = rng.randint(1, 12) if price > 5 else rng.randint(200, 2000)
        lines.append(
            {"description": desc, "quantity": float(qty), "unit_price": price, "amount": round(qty * price, 2)}
        )
    if scenario != "large" and sum(item["amount"] for item in lines) > 4200:
        lines = lines[:1]
        lines[0]["quantity"] = 1.0
        lines[0]["amount"] = lines[0]["unit_price"]
    if scenario == "large":
        lines.append(
            {"description": "Annual service contract", "quantity": 1.0, "unit_price": 6400.00, "amount": 6400.00}
        )
    if scenario == "over_budget":
        lines.append({"description": "Expedited handling fee", "quantity": 1.0, "unit_price": 400.00, "amount": 400.00})

    subtotal = round(sum(item["amount"] for item in lines), 2)
    tax = round(subtotal * rng.choice([0.0, 0.05, 0.08, 0.0875]), 2)
    total = round(subtotal + tax, 2)
    shown_total = round(total + rng.choice([10.0, 100.0, -25.0]), 2) if scenario == "math_error" else total

    issued = date(2026, 9, 1) + timedelta(days=rng.randint(0, 75))
    due = issued + timedelta(days=rng.choice([15, 30, 45]))
    number = POSTED[vid] if scenario == "duplicate" else f"{prefix}{rng.randint(1000, 99999)}"
    expected = next((a, r) for s, _, a, r in SCENARIOS if s == scenario)
    return {
        "id": f"{scenario}-{n:02d}",
        "scenario": scenario,
        "fields": {
            "invoice_number": number,
            "vendor_name": vendor,
            "invoice_date": issued.isoformat(),
            "due_date": due.isoformat(),
            "po_number": po,
            "currency": "USD",
            "subtotal": subtotal,
            "tax": tax,
            "total": shown_total,
            "line_items": lines,
        },
        "expected_action": expected[0],
        "expected_approver": expected[1],
    }


def render(case: dict, layout: int) -> str:
    f = case["fields"]
    issued, due = date.fromisoformat(f["invoice_date"]), date.fromisoformat(f["due_date"])
    rate = round(f["tax"] / f["subtotal"] * 100, 2) if f["subtotal"] else 0
    rows = [
        f"{i['description']}  {i['quantity']:g}  {i['unit_price']:,.2f}  {i['amount']:,.2f}" for i in f["line_items"]
    ]
    po_line = {0: f"PO Number: {f['po_number']}", 1: f"Reference PO: {f['po_number']}", 2: f"PO: {f['po_number']}"}
    if layout == 0:
        out = [
            f["vendor_name"],
            "123 Market Street, Springfield, IL 62701",
            "",
            "INVOICE",
            f"Invoice No: {f['invoice_number']}",
            f"Invoice Date: {fmt_date(issued, 0)}",
            f"Due Date: {fmt_due(due, 0)}",
        ]
        out += [po_line[0]] if f["po_number"] else []
        out += [
            "Bill To: Contoso Health Inc., 500 Lake Ave, Chicago, IL",
            "",
            "Description  Qty  Unit Price  Amount",
            *rows,
            "",
            f"Subtotal: ${f['subtotal']:,.2f}",
            f"Tax ({rate:g}%): ${f['tax']:,.2f}",
            f"Total: ${f['total']:,.2f}",
            "",
            "Please remit payment within the terms above. Thank you for your business.",
        ]
    elif layout == 1:
        out = [
            "TAX INVOICE",
            f"Vendor: {f['vendor_name']}",
            f"Invoice # {f['invoice_number']} | Date: {fmt_date(issued, 1)}",
            f"Payment Due: {fmt_due(due, 1)}",
        ]
        out += [po_line[1]] if f["po_number"] else []
        out += [
            "Bill To: Contoso Health Inc.",
            "",
            "Item  Qty  Rate  Line Total",
            *rows,
            "",
            f"Sub-total USD {f['subtotal']:,.2f}",
            f"Sales Tax USD {f['tax']:,.2f}",
            f"Amount Due USD {f['total']:,.2f}",
        ]
    elif layout == 3:  # held-out layout: never used while writing the extraction rules
        rows = [
            f"{i['quantity']:g} x {i['description']} @ {i['unit_price']:,.2f} = {i['amount']:,.2f}"
            for i in f["line_items"]
        ]
        out = [
            "BILLING STATEMENT",
            f"Remit to {f['vendor_name']}",
            f"Ref {f['invoice_number']}",
            f"Dated {issued.strftime('%B %-d, %Y')}",
            f"Terms Net 30, due {due.strftime('%m/%d/%Y')}",
        ]
        out += [f"Our order ref {f['po_number']}"] if f["po_number"] else []
        out += [
            "",
            *rows,
            "",
            f"Net amount {f['subtotal']:,.2f}",
            f"Tax {f['tax']:,.2f}",
            f"Total payable USD {f['total']:,.2f}",
        ]
    else:
        out = [
            f"Supplier: {f['vendor_name']}",
            "Customer: Contoso Health Inc.",
            "",
            f"Invoice Number: {f['invoice_number']}",
            f"Issue Date: {fmt_date(issued, 2)}",
            f"Due By: {fmt_due(due, 2)}",
        ]
        out += [po_line[2]] if f["po_number"] else []
        out += [
            "",
            "Service  Qty  Price  Amount",
            *rows,
            "",
            f"Subtotal {f['subtotal']:,.2f}",
            f"VAT {f['tax']:,.2f}",
            f"Balance Due USD {f['total']:,.2f}",
        ]
    return "\n".join(out) + "\n"


def write_pdf(text: str, path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for line in text.splitlines():
        c.setFont("Helvetica-Bold" if line.isupper() else "Helvetica", 11)
        c.drawString(60, y, line)
        y -= 18
    c.save()


def write_png(text: str, path: Path, rng: random.Random) -> None:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    font = ImageFont.truetype(FONT, 26)
    lines = text.splitlines()
    img = Image.new("L", (1400, 80 + 40 * len(lines)), 255)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((60, 40 + 40 * i), line, fill=rng.randint(0, 50), font=font)
    img = img.rotate(rng.uniform(-0.8, 0.8), expand=False, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    img.save(path)


def main(holdout: bool = False) -> None:
    rng = random.Random(7 if holdout else 42)
    out_dir = OUT.parent / "holdout" if holdout else OUT
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    labels, i = [], 0
    for scenario, count, _, _ in SCENARIOS:
        for n in range(count):
            case = make_case(rng, scenario, n)
            text = render(case, layout=3 if holdout else i % 3)
            fmt = ["txt", "pdf", "png"][(i // 3) % 3]
            name = f"{case['id']}.{fmt}"
            if fmt == "txt":
                (out_dir / name).write_text(text, encoding="utf-8")
            elif fmt == "pdf":
                write_pdf(text, out_dir / name)
            else:
                write_png(text, out_dir / name, rng)
            labels.append({**case, "file": name, "layout": 3 if holdout else i % 3, "format": fmt})
            i += 1
    (out_dir / "labels.jsonl").write_text("\n".join(json.dumps(x) for x in labels) + "\n", encoding="utf-8")
    print(f"Wrote {len(labels)} invoices to {out_dir}")
    if holdout:
        return

    samples = ROOT / "data" / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    for pick in ("clean-00", "large-00", "duplicate-00", "math_error-00", "unknown_vendor-00", "over_budget-00"):
        src = next(OUT.glob(f"{pick}.*"))
        shutil.copy2(src, samples / src.name)


if __name__ == "__main__":
    main()
    main(holdout=True)
