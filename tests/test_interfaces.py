import asyncio
import json

import pytest

from tests.conftest import CLEAN


def test_mcp_server_exposes_tools_but_not_approval(agent):
    pytest.importorskip("mcp.server.mcpserver")
    from docflow.mcp_server import build_server

    server = build_server(agent)

    async def go():
        names = {t.name for t in await server.list_tools()}
        res = await server.call_tool("process_invoice_text", {"text": CLEAN})
        return names, json.loads(res.content[0].text)

    names, out = asyncio.run(go())
    assert {"find_vendor", "get_purchase_order", "check_duplicate", "process_invoice_text"} <= names
    assert not any("approve" in n for n in names)
    assert out["proposal"]["action"] == "approve_payment"
    assert agent.db.payments() == []


def test_api_upload_and_approve(agent, monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from docflow import api

    monkeypatch.setattr(api, "get_agent", lambda: agent)
    client = TestClient(api.app)
    res = client.post("/documents", files={"file": ("inv.txt", CLEAN.encode(), "text/plain")}).json()
    assert res["proposal"]["action"] == "approve_payment"
    doc = res["document_id"]
    assert client.post(f"/documents/{doc}/approve", json={"user": "maria", "role": "ap_clerk"}).status_code == 200
    assert client.post(f"/documents/{doc}/approve", json={"user": "maria"}).status_code == 409
    assert len(client.get(f"/documents/{doc}/audit").json()) == 8
