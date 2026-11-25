"""FastAPI service. Run: uvicorn docflow.api:app --reload"""

from __future__ import annotations

import json
import tempfile
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from pydantic import BaseModel

from . import __version__
from .agent import DocFlowAgent
from .models import DocumentResult

app = FastAPI(
    title="DocFlow Agent",
    version=__version__,
    description="Turns invoices into validated, human-approved accounts-payable actions.",
)


@lru_cache
def get_agent() -> DocFlowAgent:
    return DocFlowAgent.from_settings()


class Decision(BaseModel):
    user: str
    role: str = "ap_clerk"
    note: str = ""


@app.get("/health")
def health() -> dict:
    agent = get_agent()
    return {"status": "ok", "version": __version__, "mode": agent.settings.mode}


@app.post("/documents", response_model=DocumentResult)
async def upload(file: UploadFile) -> DocumentResult:
    suffix = Path(file.filename or "upload.txt").suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
    try:
        result = get_agent().process_file(tmp.name)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    return result.model_copy(update={"filename": file.filename or result.filename})


@app.get("/documents")
def list_documents(status: str | None = None) -> list[dict]:
    rows = get_agent().db.documents(status)
    return [{**r, "proposal": json.loads(r["proposal"]) if r["proposal"] else None} for r in rows]


@app.get("/documents/{doc_id}/audit")
def audit(doc_id: int) -> list[dict]:
    return get_agent().db.audit_trail(doc_id)


@app.post("/documents/{doc_id}/approve")
def approve(doc_id: int, d: Decision) -> dict:
    try:
        return get_agent().approve(doc_id, d.user, d.role)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/documents/{doc_id}/reject")
def reject(doc_id: int, d: Decision) -> dict:
    try:
        get_agent().reject(doc_id, d.user, d.note)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "rejected"}
