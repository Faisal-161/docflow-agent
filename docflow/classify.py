"""Document-type classification by keyword evidence (offline baseline)."""

from __future__ import annotations

import re

from .models import DocType

SIGNALS: dict[DocType, list[str]] = {
    DocType.invoice: [r"\binvoice\b", r"\bbill to\b", r"\bamount due\b", r"\bdue date\b", r"\bremit\b"],
    DocType.purchase_order: [r"\bpurchase order\b", r"\bship to\b", r"\bordered by\b", r"\bdelivery date\b"],
    DocType.receipt: [r"\breceipt\b", r"\bpaid\b", r"\bchange\b", r"\bcashier\b", r"\bthank you for shopping\b"],
}


def classify(text: str) -> tuple[DocType, float]:
    low = text.lower()
    scores = {t: sum(bool(re.search(p, low)) for p in pats) for t, pats in SIGNALS.items()}
    best = max(scores, key=lambda t: scores[t])
    if scores[best] == 0:
        return DocType.unknown, 0.0
    total = sum(scores.values())
    return best, round(scores[best] / total, 2)
