from datetime import date

import pytest

from docflow.classify import classify
from docflow.extract import RuleExtractor, parse_date
from docflow.models import DocType
from docflow.ocr import extract_text
from tests.conftest import CLEAN, SAMPLES


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-10-01", date(2026, 10, 1)),
        ("10/01/2026", date(2026, 10, 1)),
        ("Oct 1, 2026", date(2026, 10, 1)),
        ("29 September 2026", date(2026, 9, 29)),
        ("Sept 3, 2026", date(2026, 9, 3)),
        ("not a date", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_extracts_all_fields_from_clean_invoice():
    ex = RuleExtractor().extract(CLEAN)
    f = ex.fields
    assert ex.doc_type == DocType.invoice
    assert (f.invoice_number, f.po_number, f.currency) == ("INV-5001", "PO-2201", "USD")
    assert f.vendor_name == "Acme Office Supplies Inc."
    assert (f.subtotal, f.tax, f.total) == (428.90, 34.31, 463.21)
    assert [i.amount for i in f.line_items] == [249.90, 179.00]
    assert ex.confidence["total"] > 0.95  # totals cross-check raised confidence


def test_total_is_not_confused_with_subtotal():
    f = RuleExtractor().extract("INVOICE\nInvoice No: A-123\nSubtotal: $100.00\nTax: $5.00\n").fields
    assert f.subtotal == 100.0 and f.total is None


def test_ocr_misread_po_is_normalized():
    f = RuleExtractor().extract("INVOICE\nPO: PQ-2205\n").fields
    assert f.po_number == "PO-2205"


def test_classify_purchase_order_and_receipt():
    assert classify("PURCHASE ORDER\nShip to: warehouse\nDelivery date: soon")[0] == DocType.purchase_order
    assert classify("RECEIPT\nPaid by card\nCashier: 4")[0] == DocType.receipt
    assert classify("hello world")[0] == DocType.unknown


@pytest.mark.parametrize("suffix", ["pdf", "png"])
def test_reads_pdf_and_scanned_image(suffix):
    path = next(SAMPLES.glob(f"*.{suffix}"))
    text, method = extract_text(path)
    assert method in ("pdf_text", "image_ocr")
    assert RuleExtractor().extract(text).fields.total is not None
