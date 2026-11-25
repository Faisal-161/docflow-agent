"""Score DocFlow on the labeled dataset.

Metrics
- field_accuracy: per field, share of documents where the extracted value equals ground truth
- document_accuracy: share of documents with every header field correct
- action_accuracy: share of documents where the proposed action matches the expected one
- approver_accuracy: share where the required approver role also matches
- unsafe_approvals: documents proposed for payment that should NOT be paid (the costly error)
- by_format: action accuracy split by txt / pdf / png (OCR)
- latency_ms_p50 / p95
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docflow.agent import DocFlowAgent  # noqa: E402
from docflow.config import Settings  # noqa: E402
from docflow.db import DB  # noqa: E402

HEADER = ["invoice_number", "vendor_name", "invoice_date", "due_date", "po_number", "subtotal", "tax", "total"]


def same(field: str, got, want) -> bool:
    if want is None or got is None:
        return got == want
    if field in ("subtotal", "tax", "total"):
        return abs(float(got) - float(want)) <= 0.01
    if field in ("invoice_date", "due_date"):
        return (got if isinstance(got, str) else got.isoformat()) == want
    return str(got).strip().lower() == str(want).strip().lower()


def run(dataset_dir: str | Path = ROOT / "eval" / "dataset", settings: Settings | None = None) -> dict:
    d = Path(dataset_dir)
    labels = [json.loads(x) for x in (d / "labels.jsonl").read_text().splitlines() if x.strip()]
    rows, latencies = [], []
    field_hits: dict[str, list[bool]] = defaultdict(list)
    by_format: dict[str, list[bool]] = defaultdict(list)

    for item in labels:
        # Fresh ERP per document, so one approval can't change another case's expected outcome.
        s = settings or Settings(db_path=":memory:", erp_seed_dir=str(ROOT / "data" / "erp"))
        db = DB(":memory:")
        db.seed(s.erp_seed_dir)
        agent = DocFlowAgent(db, s)
        t0 = time.perf_counter()
        res = agent.process_file(d / item["file"])
        latencies.append((time.perf_counter() - t0) * 1000)

        got = res.extraction.fields.model_dump()
        hits = {f: same(f, got[f], item["fields"][f]) for f in HEADER}
        for f, ok in hits.items():
            field_hits[f].append(ok)
        items_ok = len(got["line_items"]) == len(item["fields"]["line_items"]) and all(
            abs(a["amount"] - b["amount"]) <= 0.01
            for a, b in zip(got["line_items"], item["fields"]["line_items"], strict=False)
        )
        field_hits["line_items"].append(items_ok)
        action_ok = res.proposal.action.value == item["expected_action"]
        by_format[item["format"]].append(action_ok)
        rows.append(
            {
                "id": item["id"],
                "file": item["file"],
                "format": item["format"],
                "layout": item["layout"],
                "expected": item["expected_action"],
                "got": res.proposal.action.value,
                "action_ok": action_ok,
                "approver_ok": action_ok and res.proposal.approver_role == item["expected_approver"],
                "wrong_fields": [f for f, ok in hits.items() if not ok] + ([] if items_ok else ["line_items"]),
                "reason": res.proposal.reason,
            }
        )

    lat = sorted(latencies)
    pct = lambda xs: round(sum(xs) / len(xs), 3)  # noqa: E731
    summary = {
        "documents": len(rows),
        "action_accuracy": pct([r["action_ok"] for r in rows]),
        "approver_accuracy": pct([r["approver_ok"] for r in rows]),
        "unsafe_approvals": sum(r["got"] == "approve_payment" and r["expected"] != "approve_payment" for r in rows),
        "document_accuracy": pct([not [f for f in r["wrong_fields"] if f != "line_items"] for r in rows]),
        "field_accuracy": {f: pct(v) for f, v in field_hits.items()},
        "by_format": {k: pct(v) for k, v in sorted(by_format.items())},
        "latency_ms_p50": round(statistics.median(lat), 1),
        "latency_ms_p95": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 1),
        "run_date": date.today().isoformat(),
    }
    return {"summary": summary, "rows": rows}


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "dataset"  # "dataset" (dev) or "holdout"
    report = run(ROOT / "eval" / which)
    out = ROOT / "eval" / f"results_{which}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["summary"], indent=2))
    for r in report["rows"]:
        if not r["action_ok"] or r["wrong_fields"]:
            print(
                f"  {r['id']:<20} {r['format']} L{r['layout']} expected={r['expected']:<16} got={r['got']:<16} "
                f"wrong={r['wrong_fields']}"
            )
