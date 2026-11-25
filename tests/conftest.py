from pathlib import Path

import pytest

from docflow.agent import DocFlowAgent
from docflow.config import Settings
from docflow.db import DB

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
DATASET = ROOT / "eval" / "dataset"


@pytest.fixture()
def agent() -> DocFlowAgent:
    s = Settings(db_path=":memory:", erp_seed_dir=str(ROOT / "data" / "erp"))
    db = DB(":memory:")
    db.seed(s.erp_seed_dir)
    return DocFlowAgent(db, s)


CLEAN = """Acme Office Supplies Inc.
INVOICE
Invoice No: INV-5001
Invoice Date: 2026-10-01
Due Date: Oct 31, 2026
PO Number: PO-2201

Description  Qty  Unit Price  Amount
Printer paper A4 box  10  24.99  249.90
Toner cartridge black  2  89.50  179.00

Subtotal: $428.90
Tax (8%): $34.31
Total: $463.21
"""
