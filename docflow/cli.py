"""Command line: process, list, approve, reject, audit."""

from __future__ import annotations

import argparse
import json
import sys

from .agent import DocFlowAgent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="docflow")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("process", help="Process one or more documents")
    pr.add_argument("paths", nargs="+")
    ls = sub.add_parser("list", help="List documents")
    ls.add_argument("--status")
    for name in ("approve", "reject"):
        c = sub.add_parser(name)
        c.add_argument("doc_id", type=int)
        c.add_argument("--user", required=True)
        c.add_argument("--role", default="ap_clerk", choices=["ap_clerk", "finance_manager"])
        c.add_argument("--note", default="")
    au = sub.add_parser("audit", help="Show a document's audit trail")
    au.add_argument("doc_id", type=int)
    args = p.parse_args(argv)

    agent = DocFlowAgent.from_settings()
    if args.cmd == "process":
        for path in args.paths:
            r = agent.process_file(path)
            failed = [c.rule for c in r.checks if not c.passed]
            print(
                f"#{r.document_id} {r.filename}: {r.proposal.action.value} "
                f"(needs {r.proposal.approver_role}) - {r.proposal.reason}"
            )
            if failed:
                print(f"    failed checks: {', '.join(failed)}")
    elif args.cmd == "list":
        for d in agent.db.documents(args.status):
            action = json.loads(d["proposal"])["action"] if d["proposal"] else "-"
            print(f"#{d['id']:<4} {d['status']:<17} {action:<16} {d['filename']}")
    elif args.cmd == "approve":
        try:
            print(json.dumps(agent.approve(args.doc_id, args.user, args.role), indent=2, default=str))
        except (PermissionError, ValueError, KeyError) as exc:
            print(f"Not approved: {exc}", file=sys.stderr)
            return 1
    elif args.cmd == "reject":
        agent.reject(args.doc_id, args.user, args.note)
        print("Rejected.")
    elif args.cmd == "audit":
        for e in agent.db.audit_trail(args.doc_id):
            print(f"{e['at']}  {e['step']:<26} {json.dumps(e['detail'], default=str)[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
