"""SQLite storage: a mock ERP (vendors, purchase orders, posted invoices, payments), the document
queue with proposals awaiting approval, and an append-only audit log."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active', email TEXT, currency TEXT NOT NULL DEFAULT 'USD');
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_number TEXT PRIMARY KEY, vendor_id TEXT NOT NULL, amount REAL NOT NULL,
    invoiced REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS posted_invoices (
    vendor_id TEXT NOT NULL, invoice_number TEXT NOT NULL, total REAL NOT NULL, posted_at TEXT NOT NULL,
    PRIMARY KEY (vendor_id, invoice_number));
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL, vendor_id TEXT NOT NULL,
    amount REAL NOT NULL, scheduled_for TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, text TEXT NOT NULL,
    extraction TEXT, checks TEXT, proposal TEXT, status TEXT NOT NULL,
    decided_by TEXT, decided_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL, step TEXT NOT NULL,
    detail TEXT NOT NULL, at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # Seeding ----------------------------------------------------------------------------------
    def seed(self, seed_dir: str | Path) -> None:
        d = Path(seed_dir)
        if self.conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]:
            return
        for v in json.loads((d / "vendors.json").read_text()):
            self.conn.execute(
                "INSERT INTO vendors VALUES (?,?,?,?,?,?)",
                (
                    v["id"],
                    v["name"],
                    json.dumps(v.get("aliases", [])),
                    v.get("status", "active"),
                    v.get("email"),
                    v.get("currency", "USD"),
                ),
            )
        for po in json.loads((d / "purchase_orders.json").read_text()):
            self.conn.execute(
                "INSERT INTO purchase_orders VALUES (?,?,?,?,?)",
                (po["po_number"], po["vendor_id"], po["amount"], po.get("invoiced", 0), po.get("status", "open")),
            )
        for inv in json.loads((d / "posted_invoices.json").read_text()):
            self.conn.execute(
                "INSERT INTO posted_invoices VALUES (?,?,?,?)",
                (inv["vendor_id"], inv["invoice_number"], inv["total"], now()),
            )
        self.conn.commit()

    # ERP lookups ------------------------------------------------------------------------------
    def vendors(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM vendors").fetchall()
        return [{**dict(r), "aliases": json.loads(r["aliases"])} for r in rows]

    def purchase_order(self, po_number: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,)).fetchone()
        return dict(row) if row else None

    def posted_invoice(self, vendor_id: str, invoice_number: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM posted_invoices WHERE vendor_id = ? AND invoice_number = ?", (vendor_id, invoice_number)
        ).fetchone()
        return dict(row) if row else None

    def similar_posted(self, vendor_id: str, total: float) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM posted_invoices WHERE vendor_id = ? AND ABS(total - ?) < 0.01", (vendor_id, total)
        ).fetchall()
        return [dict(r) for r in rows]

    # Documents and approvals ------------------------------------------------------------------
    def add_document(self, filename: str, text: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO documents (filename, text, status, created_at) VALUES (?,?,?,?)",
            (filename, text, "processing", now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def save_result(self, doc_id: int, extraction: str, checks: str, proposal: str, status: str) -> None:
        self.conn.execute(
            "UPDATE documents SET extraction=?, checks=?, proposal=?, status=? WHERE id=?",
            (extraction, checks, proposal, status, doc_id),
        )
        self.conn.commit()

    def document(self, doc_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def documents(self, status: str | None = None) -> list[dict]:
        q, args = "SELECT id, filename, status, created_at, proposal FROM documents", ()
        if status:
            q, args = q + " WHERE status = ?", (status,)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY id DESC", args).fetchall()]

    def decide(self, doc_id: int, status: str, user: str) -> None:
        self.conn.execute(
            "UPDATE documents SET status=?, decided_by=?, decided_at=? WHERE id=?", (status, user, now(), doc_id)
        )
        self.conn.commit()

    def post_invoice(
        self, doc_id: int, vendor_id: str, invoice_number: str, total: float, po_number: str | None, due: str | None
    ) -> None:
        """Side effects of an approved payment, done in one transaction."""
        with self.conn:
            self.conn.execute("INSERT INTO posted_invoices VALUES (?,?,?,?)", (vendor_id, invoice_number, total, now()))
            if po_number:
                self.conn.execute(
                    "UPDATE purchase_orders SET invoiced = invoiced + ? WHERE po_number = ?", (total, po_number)
                )
            self.conn.execute(
                "INSERT INTO payments (document_id, vendor_id, amount, scheduled_for, created_at) VALUES (?,?,?,?,?)",
                (doc_id, vendor_id, total, due, now()),
            )

    def payments(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM payments").fetchall()]

    # Audit ------------------------------------------------------------------------------------
    def audit(self, doc_id: int, step: str, detail: dict) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (document_id, step, detail, at) VALUES (?,?,?,?)",
            (doc_id, step, json.dumps(detail, default=str), now()),
        )
        self.conn.commit()

    def audit_trail(self, doc_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM audit_log WHERE document_id = ? ORDER BY id", (doc_id,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
