"""Streamlit demo: upload an invoice, review the agent's proposal, approve or reject it.

Run: streamlit run ui/app.py
"""

import json
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docflow.agent import DocFlowAgent  # noqa: E402
from docflow.config import Settings  # noqa: E402
from docflow.db import DB  # noqa: E402

st.set_page_config(page_title="DocFlow Agent", layout="wide")


@st.cache_resource
def agent() -> DocFlowAgent:
    s = Settings.from_env()
    s.erp_seed_dir = str(ROOT / "data" / "erp")
    db = DB(":memory:")  # demo: fresh mock ERP per server start
    db.seed(s.erp_seed_dir)
    return DocFlowAgent(db, s)


ACTION_LABEL = {
    "approve_payment": "Approve payment",
    "request_info": "Request information",
    "escalate": "Escalate",
    "reject": "Reject as duplicate",
}

st.title("DocFlow Agent")
st.caption(
    "Upload an invoice. The agent extracts fields, checks them against the ERP and business rules, "
    "and proposes one action. Nothing happens until a person approves it."
)

samples = sorted((ROOT / "data" / "samples").glob("*"))
left, right = st.columns([1, 2])
with left:
    upload = st.file_uploader("Invoice (PDF, image or text)", type=["pdf", "png", "jpg", "jpeg", "txt"])
    sample = st.selectbox("...or try a sample", ["-"] + [p.name for p in samples])
    role = st.radio("Reviewing as", ["ap_clerk", "finance_manager"], horizontal=True)

path = None
if upload is not None:
    tmp = Path(tempfile.gettempdir()) / upload.name
    tmp.write_bytes(upload.getvalue())
    path = tmp
elif sample != "-":
    path = ROOT / "data" / "samples" / sample

if path is not None:
    key = f"{path.name}:{path.stat().st_size}"
    if st.session_state.get("key") != key:
        with st.spinner("Reading document and checking the ERP..."):
            st.session_state.result = agent().process_file(path)
        st.session_state.key = key
    r = st.session_state.result
    p = r.proposal

    with right:
        st.subheader(f"Proposed: {ACTION_LABEL[p.action.value]}")
        st.write(p.reason)
        st.caption(f"Needs approval from: {p.approver_role.replace('_', ' ')}")
        if p.draft_message:
            st.text_area("Draft message to vendor", p.draft_message, height=110)

        status = agent().db.document(r.document_id)["status"]
        if status == "pending_approval":
            a, b = st.columns(2)
            if a.button("Approve", type="primary", use_container_width=True):
                try:
                    effect = agent().approve(r.document_id, "demo-user", role)
                    st.success(f"Approved. Effect: {json.dumps(effect, default=str)}")
                except PermissionError as exc:
                    st.error(str(exc))
            if b.button("Reject proposal", use_container_width=True):
                agent().reject(r.document_id, "demo-user", "rejected in demo")
                st.info("Proposal rejected. Nothing was executed.")
        else:
            st.info(f"Status: {status}")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Extracted fields")
        f = r.extraction.fields.model_dump(mode="json")
        items = f.pop("line_items")
        st.table([{"field": k, "value": v, "confidence": r.extraction.confidence.get(k, "")} for k, v in f.items()])
        if items:
            st.table(items)
    with c2:
        st.subheader("Checks")
        for c in r.checks:
            mark = "PASS" if c.passed else ("WARN" if c.severity.value == "warning" else "FAIL")
            st.write(f"**{mark}** `{c.rule}` - {c.message}")

    with st.expander("Audit trail (every step and tool call)"):
        for e in agent().db.audit_trail(r.document_id):
            st.write(f"`{e['step']}`")
            st.json(e["detail"], expanded=False)
