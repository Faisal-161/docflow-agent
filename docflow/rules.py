"""Business rules and the approval policy.

Rules turn findings into pass/fail checks. The policy maps checks to exactly one proposed action
and decides who must approve it. The policy is code, not a prompt: an LLM planner can suggest an
action, but `enforce` overrides any suggestion the checks don't allow.
"""

from __future__ import annotations

from datetime import date

from .config import Settings
from .models import REQUIRED_FIELDS, Action, InvoiceFields, Proposal, RuleResult, Severity


def _r(rule: str, passed: bool, message: str, severity: Severity = Severity.error) -> RuleResult:
    return RuleResult(rule=rule, passed=passed, severity=severity, message=message)


def validate(
    fields: InvoiceFields,
    confidence: dict[str, float],
    vendor: dict | None,
    po: dict | None,
    duplicate: dict | None,
    settings: Settings,
    today: date | None = None,
) -> list[RuleResult]:
    tol = settings.amount_tolerance
    today = today or date.today()
    out: list[RuleResult] = []

    missing = [f for f in REQUIRED_FIELDS if getattr(fields, f) in (None, "")]
    out.append(
        _r(
            "required_fields",
            not missing,
            "All required fields present." if not missing else f"Missing: {', '.join(missing)}.",
        )
    )

    low = sorted(k for k, v in confidence.items() if v < settings.min_confidence)
    out.append(
        _r(
            "extraction_confidence",
            not low,
            "Extraction confidence is high." if not low else f"Low confidence: {', '.join(low)}.",
            Severity.warning,
        )
    )

    if fields.line_items and fields.subtotal is not None:
        s = round(sum(i.amount for i in fields.line_items), 2)
        out.append(
            _r(
                "line_items_sum",
                abs(s - fields.subtotal) <= tol,
                f"Line items add up to {s:.2f}; subtotal is {fields.subtotal:.2f}.",
            )
        )
        bad = [i.description for i in fields.line_items if abs(i.quantity * i.unit_price - i.amount) > tol]
        out.append(
            _r(
                "line_item_math",
                not bad,
                "Every line is quantity x unit price." if not bad else f"Wrong line math: {', '.join(bad)}.",
            )
        )

    if None not in (fields.subtotal, fields.tax, fields.total):
        expected = round(fields.subtotal + fields.tax, 2)
        out.append(
            _r(
                "totals_add_up",
                abs(expected - fields.total) <= tol,
                f"Subtotal + tax = {expected:.2f}; total is {fields.total:.2f}.",
            )
        )

    if fields.invoice_date and fields.due_date:
        out.append(
            _r(
                "due_after_issue",
                fields.due_date >= fields.invoice_date,
                "Due date is on or after the invoice date."
                if fields.due_date >= fields.invoice_date
                else "Due date is before the invoice date.",
            )
        )
    if fields.invoice_date:
        out.append(
            _r(
                "not_future_dated",
                fields.invoice_date <= today,
                "Invoice date is not in the future."
                if fields.invoice_date <= today
                else f"Invoice is dated in the future ({fields.invoice_date}).",
                Severity.warning,
            )
        )

    if vendor is None:
        out.append(_r("known_vendor", False, f"Vendor '{fields.vendor_name}' is not in the vendor master."))
    else:
        active = vendor["status"] == "active"
        out.append(
            _r(
                "known_vendor",
                active,
                f"Matched vendor {vendor['id']} ({vendor['name']})"
                + ("." if active else f", but its status is '{vendor['status']}'."),
            )
        )
        if fields.currency and fields.currency != vendor.get("currency", fields.currency):
            out.append(
                _r(
                    "currency_matches_vendor",
                    False,
                    f"Invoice is in {fields.currency}; vendor is billed in {vendor['currency']}.",
                    Severity.warning,
                )
            )

    if fields.po_number:
        if po is None:
            out.append(_r("po_exists", False, f"PO {fields.po_number} was not found."))
        else:
            out.append(_r("po_exists", po["status"] == "open", f"PO {po['po_number']} is {po['status']}."))
            if vendor and po["vendor_id"] != vendor["id"]:
                out.append(_r("po_vendor_matches", False, f"PO {po['po_number']} belongs to another vendor."))
            if fields.total is not None:
                remaining = round(po["amount"] - po["invoiced"], 2)
                out.append(
                    _r(
                        "within_po_budget",
                        fields.total <= remaining + tol,
                        f"Invoice total {fields.total:.2f}; PO has {remaining:.2f} remaining.",
                    )
                )
    else:
        out.append(_r("po_exists", False, "No PO number on the invoice.", Severity.warning))

    if duplicate is not None:
        out.append(_r("not_duplicate", not duplicate["duplicate"], duplicate.get("message", "No duplicate found.")))
    return out


def _failed(checks: list[RuleResult], *rules: str) -> list[RuleResult]:
    return [c for c in checks if not c.passed and c.rule in rules]


def decide(fields: InvoiceFields, checks: list[RuleResult], vendor: dict | None, settings: Settings) -> Proposal:
    """Map checks to one action, in priority order."""
    vendor_name = fields.vendor_name or "the vendor"
    ref = fields.invoice_number or "your invoice"

    if f := _failed(checks, "not_duplicate"):
        return Proposal(
            action=Action.reject,
            reason=f[0].message,
            approver_role="ap_clerk",
            draft_message=f"Hello {vendor_name}, invoice {ref} appears to duplicate one we have already "
            "received, so we will not process it again. Please contact us if this is an error.",
        )
    if f := _failed(checks, "known_vendor", "po_vendor_matches"):
        return Proposal(action=Action.escalate, reason=" ".join(c.message for c in f), approver_role="finance_manager")
    if f := _failed(
        checks,
        "required_fields",
        "line_items_sum",
        "line_item_math",
        "totals_add_up",
        "due_after_issue",
        "po_exists",
        "extraction_confidence",
    ):
        issues = "; ".join(c.message for c in f)
        return Proposal(
            action=Action.request_info,
            reason=issues,
            approver_role="ap_clerk",
            draft_message=f"Hello {vendor_name}, we could not process invoice {ref}: {issues} "
            "Please send a corrected invoice.",
        )
    if f := _failed(checks, "within_po_budget"):
        return Proposal(action=Action.escalate, reason=f[0].message, approver_role="finance_manager")

    big = (fields.total or 0) > settings.manager_threshold
    other = [c.message for c in checks if not c.passed]
    reason = "All checks passed." + (f" Notes: {' '.join(other)}" if other else "")
    if big:
        reason += f" Total exceeds {settings.manager_threshold:,.0f}, so a finance manager must approve."
    return Proposal(
        action=Action.approve_payment, reason=reason, approver_role="finance_manager" if big else "ap_clerk"
    )


def enforce(suggested: Proposal | None, policy: Proposal) -> tuple[Proposal, bool]:
    """Keep an LLM's suggestion only if it matches the policy's action. Returns (proposal, overridden)."""
    if suggested is None or suggested.action != policy.action:
        return policy, suggested is not None
    # Same action: keep the LLM's wording, but the approver role always comes from policy.
    return suggested.model_copy(update={"approver_role": policy.approver_role}), False
