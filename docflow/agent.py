"""The DocFlow agent: document -> text -> fields -> tool calls -> rule checks -> proposed action,
then waits for a human to approve before anything is executed.

Two planners decide which tools to call:
- `RulePlanner`: fixed, deterministic order (offline, used in tests and evals).
- `ClaudePlanner`: Claude chooses tools in a tool-use loop and suggests an action.
Either way, `rules.validate` + `rules.decide` + `rules.enforce` have the final word, and any lookup
the planner skipped is run by the agent before the checks.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings, get_settings
from .db import DB
from .extract import Extractor, get_extractor
from .models import Action, DocumentResult, Extraction, Proposal, RuleResult, Status
from .ocr import extract_text
from .rules import decide, enforce, validate
from .tools import Toolbox

ROLE_RANK = {"ap_clerk": 1, "finance_manager": 2}


@dataclass
class Findings:
    vendor: dict | None = None
    po: dict | None = None
    duplicate: dict | None = None
    suggestion: Proposal | None = None
    calls: list[str] = field(default_factory=list)


class RulePlanner:
    name = "rules"

    def investigate(self, ex: Extraction, tools: Toolbox, log) -> Findings:
        f, fields = Findings(), ex.fields
        if fields.vendor_name:
            res = log("find_vendor", {"name": fields.vendor_name})
            f.vendor = res.get("vendor") if res.get("found") else None
        if fields.po_number:
            res = log("get_purchase_order", {"po_number": fields.po_number})
            f.po = res.get("po") if res.get("found") else None
        if f.vendor:
            f.duplicate = log(
                "check_duplicate",
                {"vendor_id": f.vendor["id"], "invoice_number": fields.invoice_number, "total": fields.total},
            )
        return f


PROPOSE_TOOL = {
    "name": "propose_action",
    "description": "Finish by proposing exactly one action for a human to approve.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": [a.value for a in Action]},
            "reason": {"type": "string", "description": "One or two sentences citing the evidence."},
            "draft_message": {"type": ["string", "null"], "description": "Message to the vendor, if one is needed."},
        },
        "required": ["action", "reason"],
    },
}

PLANNER_SYSTEM = """You are an accounts-payable agent. You receive fields extracted from a vendor invoice.
Verify them with the tools: find the vendor, fetch the PO if there is one, and check for duplicates.
Then call propose_action once. Use approve_payment only if the vendor is known and active, the PO is open
with enough budget, the math is right, and it is not a duplicate. Use request_info for missing or
inconsistent data, escalate for unknown vendors or budget overruns, reject for duplicates.
A human approves every action; you never move money yourself."""


class ClaudePlanner:
    name = "claude"

    def __init__(self, model: str, max_steps: int):
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("Set ANTHROPIC_API_KEY to use the Claude planner.")
        self.client, self.model, self.max_steps = anthropic.Anthropic(), model, max_steps

    def investigate(self, ex: Extraction, tools: Toolbox, log) -> Findings:
        f = Findings()
        messages = [{"role": "user", "content": "Extracted invoice:\n" + ex.fields.model_dump_json(indent=2)}]
        for _ in range(self.max_steps):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                temperature=0,
                system=PLANNER_SYSTEM,
                tools=[*tools.specs(), PROPOSE_TOOL],
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                break
            results = []
            for use in uses:
                if use.name == "propose_action":
                    f.suggestion = Proposal(approver_role="ap_clerk", **use.input)
                    return f
                out = log(use.name, use.input)
                if use.name == "find_vendor" and out.get("found"):
                    f.vendor = out["vendor"]
                elif use.name == "get_purchase_order" and out.get("found"):
                    f.po = out["po"]
                elif use.name == "check_duplicate":
                    f.duplicate = out
                results.append({"type": "tool_result", "tool_use_id": use.id, "content": json.dumps(out, default=str)})
            messages.append({"role": "user", "content": results})
        return f


class DocFlowAgent:
    def __init__(self, db: DB, settings: Settings | None = None, extractor: Extractor | None = None, planner=None):
        self.db = db
        self.settings = settings or get_settings()
        self.tools = Toolbox(db, self.settings)
        mode = self.settings.mode
        self.extractor = extractor or get_extractor(mode, self.settings.claude_model)
        self.planner = planner or (
            ClaudePlanner(self.settings.claude_model, self.settings.max_agent_steps)
            if mode == "claude"
            else RulePlanner()
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> DocFlowAgent:
        s = settings or get_settings()
        db = DB(s.db_path)
        db.seed(s.erp_seed_dir)
        return cls(db, s)

    # Processing -------------------------------------------------------------------------------
    def process_file(self, path: str | Path) -> DocumentResult:
        p = Path(path)
        text, method = extract_text(p)
        return self.process_text(p.name, text, method)

    def process_text(self, filename: str, text: str, method: str = "text") -> DocumentResult:
        t0 = time.perf_counter()
        doc_id = self.db.add_document(filename, text)
        steps: list[str] = []

        def audit(step: str, detail: dict) -> None:
            self.db.audit(doc_id, step, detail)
            steps.append(step)

        def log_tool(name: str, args: dict) -> dict:
            result = self.tools.call(name, args)
            audit(f"tool:{name}", {"args": args, "result": result})
            return result

        audit("text_extracted", {"method": method, "chars": len(text)})
        ex = self.extractor.extract(text)
        audit(
            "fields_extracted",
            {
                "extractor": ex.extractor,
                "doc_type": ex.doc_type.value,
                "fields": ex.fields.model_dump(mode="json"),
                "confidence": ex.confidence,
            },
        )

        found = self.planner.investigate(ex, self.tools, log_tool)
        # Guardrail: run any lookup the planner skipped, so checks never rest on missing evidence.
        fields = ex.fields
        if fields.vendor_name and found.vendor is None and not any(s == "tool:find_vendor" for s in steps):
            res = log_tool("find_vendor", {"name": fields.vendor_name})
            found.vendor = res.get("vendor") if res.get("found") else None
        if fields.po_number and found.po is None and not any(s == "tool:get_purchase_order" for s in steps):
            res = log_tool("get_purchase_order", {"po_number": fields.po_number})
            found.po = res.get("po") if res.get("found") else None
        if found.vendor and found.duplicate is None:
            found.duplicate = log_tool(
                "check_duplicate",
                {"vendor_id": found.vendor["id"], "invoice_number": fields.invoice_number, "total": fields.total},
            )

        checks = validate(fields, ex.confidence, found.vendor, found.po, found.duplicate, self.settings)
        if ex.doc_type.value not in ("invoice", "unknown"):
            checks.append(
                RuleResult(
                    rule="is_invoice",
                    passed=False,
                    message=f"Document looks like a {ex.doc_type.value}, not an invoice.",
                )
            )
        audit("rules_checked", {"failed": [c.rule for c in checks if not c.passed], "total": len(checks)})

        policy = decide(fields, checks, found.vendor, self.settings)
        proposal, overridden = enforce(found.suggestion, policy)
        audit(
            "action_proposed",
            {
                "planner": self.planner.name,
                "action": proposal.action.value,
                "approver_role": proposal.approver_role,
                "reason": proposal.reason,
                "llm_suggestion_overridden": overridden,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            },
        )

        self.db.save_result(
            doc_id,
            ex.model_dump_json(),
            json.dumps([c.model_dump() for c in checks]),
            proposal.model_dump_json(),
            Status.pending.value,
        )
        return DocumentResult(
            document_id=doc_id,
            filename=filename,
            extraction=ex,
            checks=checks,
            proposal=proposal,
            status=Status.pending,
            steps=steps,
        )

    # Human in the loop ------------------------------------------------------------------------
    def load(self, doc_id: int) -> tuple[dict, Extraction, Proposal]:
        row = self.db.document(doc_id)
        if not row or not row["proposal"]:
            raise KeyError(f"Document {doc_id} not found")
        return row, Extraction.model_validate_json(row["extraction"]), Proposal.model_validate_json(row["proposal"])

    def approve(self, doc_id: int, user: str, role: str) -> dict:
        row, ex, proposal = self.load(doc_id)
        if row["status"] != Status.pending.value:
            raise ValueError(f"Document {doc_id} is already {row['status']}.")
        if ROLE_RANK.get(role, 0) < ROLE_RANK[proposal.approver_role]:
            raise PermissionError(f"This action needs a {proposal.approver_role}; {user} is a {role}.")

        effect: dict = {"action": proposal.action.value}
        if proposal.action == Action.approve_payment:
            vendor = self.tools.find_vendor(ex.fields.vendor_name or "")["vendor"]
            self.db.post_invoice(
                doc_id,
                vendor["id"],
                ex.fields.invoice_number,
                ex.fields.total,
                ex.fields.po_number,
                str(ex.fields.due_date) if ex.fields.due_date else None,
            )
            effect.update(payment_scheduled=ex.fields.total, vendor_id=vendor["id"], due=str(ex.fields.due_date))
        elif proposal.draft_message:
            effect["message_sent"] = proposal.draft_message  # stand-in for an email/Slack integration
        elif proposal.action == Action.escalate:
            effect["escalated_to"] = "finance_manager"

        self.db.decide(doc_id, Status.approved.value, user)
        self.db.audit(doc_id, "approved", {"user": user, "role": role, **effect})
        return effect

    def reject(self, doc_id: int, user: str, note: str = "") -> None:
        row, _, _ = self.load(doc_id)
        if row["status"] != Status.pending.value:
            raise ValueError(f"Document {doc_id} is already {row['status']}.")
        self.db.decide(doc_id, Status.rejected.value, user)
        self.db.audit(doc_id, "proposal_rejected", {"user": user, "note": note})
