import pytest

from docflow.agent import Findings
from docflow.models import Action, Proposal
from tests.conftest import CLEAN, DATASET


def test_clean_invoice_is_proposed_for_payment_not_paid(agent):
    r = agent.process_text("clean.txt", CLEAN)
    assert r.proposal.action == Action.approve_payment
    assert r.proposal.approver_role == "ap_clerk"
    assert agent.db.payments() == []  # nothing moves before a human approves


def test_approval_posts_invoice_and_schedules_payment(agent):
    r = agent.process_text("clean.txt", CLEAN)
    effect = agent.approve(r.document_id, "maria", "ap_clerk")
    assert effect["payment_scheduled"] == 463.21
    assert agent.db.payments()[0]["vendor_id"] == "V-1001"
    assert agent.db.purchase_order("PO-2201")["invoiced"] == pytest.approx(1200 + 463.21)
    # Same invoice again is now a duplicate
    again = agent.process_text("clean-again.txt", CLEAN)
    assert again.proposal.action == Action.reject


def test_cannot_approve_twice(agent):
    r = agent.process_text("clean.txt", CLEAN)
    agent.approve(r.document_id, "maria", "ap_clerk")
    with pytest.raises(ValueError):
        agent.approve(r.document_id, "maria", "ap_clerk")


def test_large_invoice_needs_finance_manager(agent):
    big = CLEAN.replace("Toner cartridge black  2  89.50  179.00", "Annual service contract  1  6,000.00  6,000.00")
    big = big.replace("$428.90", "$6,249.90").replace("$34.31", "$499.99").replace("$463.21", "$6,749.89")
    r = agent.process_text("big.txt", big)
    assert r.proposal.action == Action.approve_payment
    assert r.proposal.approver_role == "finance_manager"
    with pytest.raises(PermissionError):
        agent.approve(r.document_id, "maria", "ap_clerk")
    agent.approve(r.document_id, "sam", "finance_manager")


@pytest.mark.parametrize(
    "edit, action, failed_rule",
    [
        (("$463.21", "$563.21"), Action.request_info, "totals_add_up"),
        (("Acme Office Supplies Inc.", "Zephyr Consulting LLC"), Action.escalate, "known_vendor"),
        (("PO-2201", "PO-9999"), Action.request_info, "po_exists"),
        (("PO-2201", "PO-2207"), Action.escalate, "within_po_budget"),
        (("PO-2201", "PO-2202"), Action.escalate, "po_vendor_matches"),
        (("INV-5001", "INV-1040"), Action.reject, "not_duplicate"),
    ],
)
def test_rule_failures_map_to_actions(agent, edit, action, failed_rule):
    r = agent.process_text("case.txt", CLEAN.replace(*edit))
    assert r.proposal.action == action
    assert failed_rule in [c.rule for c in r.checks if not c.passed]


def test_rejecting_proposal_executes_nothing(agent):
    r = agent.process_text("clean.txt", CLEAN)
    agent.reject(r.document_id, "maria", "wrong cost center")
    assert agent.db.document(r.document_id)["status"] == "rejected"
    assert agent.db.payments() == []


def test_audit_trail_records_every_step(agent):
    r = agent.process_text("clean.txt", CLEAN)
    steps = [e["step"] for e in agent.db.audit_trail(r.document_id)]
    assert steps == [
        "text_extracted",
        "fields_extracted",
        "tool:find_vendor",
        "tool:get_purchase_order",
        "tool:check_duplicate",
        "rules_checked",
        "action_proposed",
    ]


class OverEagerPlanner:
    """Simulates an LLM planner that skips checks and wants to pay everything."""

    name = "over_eager"

    def investigate(self, ex, tools, log):
        return Findings(
            suggestion=Proposal(action=Action.approve_payment, reason="Looks fine to me.", approver_role="ap_clerk")
        )


def test_policy_overrides_llm_suggestion_and_fills_skipped_checks(agent):
    agent.planner = OverEagerPlanner()
    r = agent.process_text("dup.txt", CLEAN.replace("INV-5001", "INV-1040"))
    assert r.proposal.action == Action.reject  # policy wins
    trail = agent.db.audit_trail(r.document_id)
    assert {"tool:find_vendor", "tool:check_duplicate"} <= {e["step"] for e in trail}  # agent ran skipped lookups
    assert trail[-1]["detail"]["llm_suggestion_overridden"] is True


def test_policy_keeps_llm_wording_but_sets_approver(agent):
    class Planner(OverEagerPlanner):
        def investigate(self, ex, tools, log):
            return Findings(
                suggestion=Proposal(action=Action.approve_payment, reason="All verified.", approver_role="nobody")
            )

    agent.planner = Planner()
    r = agent.process_text("clean.txt", CLEAN)
    assert r.proposal.reason == "All verified." and r.proposal.approver_role == "ap_clerk"


def test_dev_set_quality_gate():
    from eval.run_eval import run

    summary = run(DATASET)["summary"]
    assert summary["action_accuracy"] >= 0.9
    assert summary["unsafe_approvals"] == 0
