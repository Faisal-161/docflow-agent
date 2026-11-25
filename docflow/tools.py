"""Tools the agent can call. One registry serves the offline planner, the Claude tool-use loop,
and the MCP server, so all three behave the same way."""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .db import DB

_SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|co|corp|corporation|company|gmbh|plc)\b\.?", re.I)


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", _SUFFIX.sub("", name.lower())).strip()


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., dict]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


class Toolbox:
    def __init__(self, db: DB, settings: Settings):
        self.db, self.settings = db, settings
        self.tools: dict[str, Tool] = {t.name: t for t in self._build()}

    def call(self, name: str, args: dict[str, Any]) -> dict:
        if name not in self.tools:
            return {"error": f"Unknown tool {name}"}
        try:
            return self.tools[name].fn(**args)
        except TypeError as exc:
            return {"error": f"Bad arguments for {name}: {exc}"}

    def specs(self) -> list[dict]:
        return [t.spec() for t in self.tools.values()]

    # Tool implementations ---------------------------------------------------------------------
    def find_vendor(self, name: str) -> dict:
        target = normalize(name)
        best, best_score = None, 0.0
        for v in self.db.vendors():
            for candidate in [v["name"], *v["aliases"]]:
                score = difflib.SequenceMatcher(None, target, normalize(candidate)).ratio()
                if score > best_score:
                    best, best_score = v, score
        if best and best_score >= 0.85:
            return {"found": True, "vendor": best, "match_score": round(best_score, 2)}
        return {"found": False, "closest": best["name"] if best else None, "match_score": round(best_score, 2)}

    def get_purchase_order(self, po_number: str) -> dict:
        po = self.db.purchase_order(po_number.upper().replace(" ", "-"))
        if not po:
            return {"found": False}
        return {"found": True, "po": po, "remaining": round(po["amount"] - po["invoiced"], 2)}

    def check_duplicate(self, vendor_id: str, invoice_number: str | None, total: float | None) -> dict:
        if invoice_number and (hit := self.db.posted_invoice(vendor_id, invoice_number)):
            return {
                "duplicate": True,
                "message": f"Invoice {invoice_number} from {vendor_id} was already posted on {hit['posted_at'][:10]}.",
            }
        if total is not None and (similar := self.db.similar_posted(vendor_id, total)):
            nums = ", ".join(s["invoice_number"] for s in similar)
            return {
                "duplicate": False,
                "message": f"No exact duplicate. Same vendor and amount as {nums}; worth a quick look.",
                "similar": similar,
            }
        return {"duplicate": False, "message": "No duplicate found."}

    def _build(self) -> list[Tool]:
        return [
            Tool(
                "find_vendor",
                "Look up a vendor in the vendor master by name (fuzzy match). Returns the vendor "
                "record with id and status, or found=false.",
                {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                self.find_vendor,
            ),
            Tool(
                "get_purchase_order",
                "Fetch a purchase order by number, with the amount still available to invoice.",
                {"type": "object", "properties": {"po_number": {"type": "string"}}, "required": ["po_number"]},
                self.get_purchase_order,
            ),
            Tool(
                "check_duplicate",
                "Check whether this vendor's invoice was already posted.",
                {
                    "type": "object",
                    "properties": {
                        "vendor_id": {"type": "string"},
                        "invoice_number": {"type": ["string", "null"]},
                        "total": {"type": ["number", "null"]},
                    },
                    "required": ["vendor_id", "invoice_number", "total"],
                },
                self.check_duplicate,
            ),
        ]
