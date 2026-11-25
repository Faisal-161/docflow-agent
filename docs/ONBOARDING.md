# Onboarding a new customer

How to take DocFlow from the demo to one customer's accounts-payable process. Most deployments touch four places, each behind a small interface.

## Day 1 checklist

1. **Collect 30 to 50 real invoices** from the customer, covering their top vendors and at least a few bad ones (duplicates, wrong totals, missing POs). Label the fields and the action an AP clerk took. This becomes their eval set: copy the format of `eval/dataset/labels.jsonl`.
2. **Run the baseline**: `python eval/run_eval.py <their-folder>`. The per-field accuracy and the list of wrong fields tell you what to fix first.
3. **Agree the policy** with their AP lead: approval thresholds, who approves what, and which failures block payment.
4. **Connect the ERP** (read-only first), then run in shadow mode for a week: the agent proposes, people work as usual, and you compare.

## 1. Connect their ERP

`docflow/db.py` is a SQLite stand-in. Replace these methods with calls to the customer's system; nothing else changes:

| Method | Typical source |
| --- | --- |
| `vendors()` | Vendor master |
| `purchase_order(po_number)` | PO module |
| `posted_invoice(...)`, `similar_posted(...)` | AP ledger |
| `post_invoice(...)` | AP bill creation + payment run (keep this behind approval) |

Start read-only. Turn on `post_invoice` only after shadow mode agrees with the team.

## 2. Handle their layouts

- **Rules mode:** add label variants to `LABELS` in `docflow/extract.py` (for example `"remit to"` for vendor, `"ref"` for invoice number), then rerun the eval.
- **Claude mode:** set `DOCFLOW_MODE=claude`. No per-layout work; use the eval set to confirm accuracy and watch the `extraction_confidence` check.
- OCR quality: scans below about 200 DPI hurt accuracy. Common misreads (O read as Q or 0 in PO numbers) are normalized in `extract.py`; add the customer's own patterns there.

## 3. Tune rules and approvals

- Thresholds: `DOCFLOW_MANAGER_THRESHOLD`, `DOCFLOW_AMOUNT_TOLERANCE`, `DOCFLOW_MIN_CONFIDENCE`.
- New rule: add a check in `rules.validate` and map it to an action in `rules.decide`. Add a test in `tests/test_agent.py` with a one-line invoice edit, like the existing parametrized cases.
- New role: add it to `ROLE_RANK` in `agent.py`.

## 4. Add a document type

1. Add keyword signals in `classify.py`.
2. Add a Pydantic model for its fields in `models.py` (the Claude extractor uses it as the schema).
3. Add its rules and policy branch in `rules.py`.
4. Add labeled examples and run the eval.

## Go-live gates

- Zero unsafe approvals on the customer's eval set.
- Action accuracy agreed with the AP lead (a common target is 90%+, with the rest routed to people).
- Audit log reviewed by whoever owns financial controls.
