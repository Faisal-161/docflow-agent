"""Field extraction.

- `RuleExtractor`: offline baseline using label patterns. Handles common invoice layouts, date and
  currency formats, and line-item tables. Gives each field a confidence score.
- `ClaudeExtractor`: asks Claude to fill the `InvoiceFields` schema through a forced tool call,
  so the output is always schema-valid JSON.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Protocol

from .classify import classify
from .models import DocType, Extraction, InvoiceFields, LineItem

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%b %d %Y")
DATE_IN_TEXT = (
    r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}"
    r"|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}|\d{1,2} [A-Z][a-z]{2,8} \d{4}"
)
MONEY = r"(?:[A-Z]{3}\s*)?[$€£]?\s*(-?[\d,]+\.\d{2})"
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
COMPANY = re.compile(r"\b(inc|llc|ltd|limited|co|corp|corporation|gmbh|plc|company)\.?$", re.I)

LABELS = {
    "invoice_number": [r"invoice\s*(?:no\.?|number|num|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})"],
    "po_number": [
        r"(?:p\.?o\.?\s*(?:no\.?|number|#)?|purchase\s+order(?:\s+no\.?)?|reference\s+po)"
        r"\s*[:#]?\s*(P[OQ0][-\s]?\d{3,})"  # OCR often reads O as Q or 0
    ],
    "vendor_name": [r"(?:^|\n)\s*(?:from|vendor|supplier|bill\s+from|seller)\s*:\s*([^\n]+)"],
    "invoice_date": [r"(?:invoice\s+date|date\s+issued|issue\s+date|(?<!due )(?<!payment )\bdate)\s*:\s*([^\n|]+)"],
    "due_date": [r"(?:due\s+date|payment\s+due|due\s+by)\s*:\s*([^\n|]+)"],
    "subtotal": [rf"sub\s*-?\s*total\s*:?\s*{MONEY}"],
    "tax": [rf"(?:sales\s+tax|vat|gst|tax)(?:\s*\(\s*[\d.]+\s*%\s*\))?\s*:?\s*{MONEY}"],
    "total": [
        rf"(?:amount\s+due|total\s+due|balance\s+due|grand\s+total)\s*:?\s*{MONEY}",
        rf"(?<!sub)(?<!sub )(?<!sub-)\btotal\s*:?\s*{MONEY}",
    ],
}
LINE_ITEM = re.compile(
    r"^\s*(?P<desc>[A-Za-z][^\n]*?)\s+(?P<qty>\d+(?:\.\d+)?)\s+[$€£]?(?P<unit>[\d,]+\.\d{2})"
    r"\s+[$€£]?(?P<amount>[\d,]+\.\d{2})\s*$",
    re.M,
)


def parse_date(raw: str) -> date | None:
    cleaned = re.sub(r"\bSept\b", "Sep", re.sub(r"\s+", " ", raw.strip().rstrip(".,")))
    cleaned = re.split(r"\s{2,}|\s\|\s", cleaned)[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    m = re.search(DATE_IN_TEXT, cleaned)
    if m and m.group(0) != cleaned:
        return parse_date(m.group(0).replace(".", ""))
    return None


def money(raw: str) -> float:
    return round(float(raw.replace(",", "")), 2)


class Extractor(Protocol):
    name: str

    def extract(self, text: str) -> Extraction: ...


class RuleExtractor:
    name = "rules"

    def _find(self, field: str, text: str) -> str | None:
        for pattern in LABELS[field]:
            m = re.search(pattern, text, re.I)
            if m:
                return m.group(1).strip()
        return None

    def extract(self, text: str) -> Extraction:
        doc_type, _ = classify(text)
        f: dict = {}
        conf: dict[str, float] = {}

        for key in ("invoice_number", "po_number"):
            if value := self._find(key, text):
                if key == "po_number":
                    value = re.sub(r"^P[OQ0][-\s]?", "PO-", value.upper())
                f[key] = value
                conf[key] = 0.9

        if vendor := self._find("vendor_name", text):
            f["vendor_name"], conf["vendor_name"] = vendor.strip(), 0.9
        else:  # fallback: first line that looks like a company name
            for line in text.splitlines()[:8]:
                if COMPANY.search(line.strip()):
                    f["vendor_name"], conf["vendor_name"] = line.strip(), 0.6
                    break

        for key in ("invoice_date", "due_date"):
            if (raw := self._find(key, text)) and (d := parse_date(raw)):
                f[key], conf[key] = d, 0.9

        for key in ("subtotal", "tax", "total"):
            if raw := self._find(key, text):
                f[key], conf[key] = money(raw), 0.85

        code = re.search(r"\b(USD|EUR|GBP|CAD|AUD|BDT|INR)\b", text)
        symbol = next((s for s in CURRENCY_SYMBOLS if s in text), None)
        if code or symbol:
            f["currency"] = code.group(1) if code else CURRENCY_SYMBOLS[symbol]
            conf["currency"] = 0.9 if code else 0.7

        items = []
        for m in LINE_ITEM.finditer(text):
            desc = m.group("desc").strip()
            if re.search(r"\b(sub\s*total|total|tax|vat|amount due|balance)\b", desc, re.I):
                continue
            items.append(
                LineItem(
                    description=desc,
                    quantity=float(m.group("qty")),
                    unit_price=money(m.group("unit")),
                    amount=money(m.group("amount")),
                )
            )
        f["line_items"] = items
        if items:
            conf["line_items"] = 0.8

        # Cross-check raises confidence: totals that add up were probably read correctly.
        if items and f.get("subtotal") is not None:
            if abs(sum(i.amount for i in items) - f["subtotal"]) <= 0.02:
                conf["subtotal"] = conf["line_items"] = 0.97
        if None not in (f.get("subtotal"), f.get("tax"), f.get("total")):
            if abs(f["subtotal"] + f["tax"] - f["total"]) <= 0.02:
                conf["total"] = conf["tax"] = 0.97

        return Extraction(doc_type=doc_type, fields=InvoiceFields(**f), confidence=conf, extractor=self.name)


class ClaudeExtractor:
    name = "claude"

    def __init__(self, model: str):
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("Set ANTHROPIC_API_KEY to use the Claude extractor.")
        self.client = anthropic.Anthropic()
        self.model = model

    def extract(self, text: str) -> Extraction:
        doc_type, _ = classify(text)
        schema = InvoiceFields.model_json_schema()
        schema["properties"]["confidence"] = {
            "type": "object",
            "description": "Confidence 0..1 for each field you filled",
            "additionalProperties": {"type": "number"},
        }
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=0,
            system="Extract invoice fields exactly as written. Leave a field null if it is not in the document. "
            "Never guess numbers. Dates as YYYY-MM-DD.",
            tools=[
                {
                    "name": "record_invoice",
                    "description": "Record the extracted invoice fields.",
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": "record_invoice"},
            messages=[{"role": "user", "content": f"<document>\n{text}\n</document>"}],
        )
        data = next(b.input for b in msg.content if b.type == "tool_use")
        conf = {k: float(v) for k, v in (data.pop("confidence", None) or {}).items()}
        fields = InvoiceFields.model_validate(data)
        for k, v in fields.model_dump().items():
            if v not in (None, []) and k not in conf:
                conf[k] = 0.9
        return Extraction(
            doc_type=doc_type if doc_type != DocType.unknown else DocType.invoice,
            fields=fields,
            confidence=conf,
            extractor=self.name,
        )


def get_extractor(mode: str, model: str) -> Extractor:
    return ClaudeExtractor(model) if mode == "claude" else RuleExtractor()
