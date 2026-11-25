"""Typed data models. The Pydantic schemas double as the JSON schemas given to the LLM."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DocType(StrEnum):
    invoice = "invoice"
    purchase_order = "purchase_order"
    receipt = "receipt"
    unknown = "unknown"


class LineItem(BaseModel):
    description: str
    quantity: float = Field(ge=0)
    unit_price: float = Field(ge=0)
    amount: float = Field(ge=0)


class InvoiceFields(BaseModel):
    """Fields extracted from an invoice. Every field is optional: missing data is a finding, not a crash."""

    invoice_number: str | None = None
    vendor_name: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    po_number: str | None = None
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. USD")
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    line_items: list[LineItem] = Field(default_factory=list)


REQUIRED_FIELDS = ("invoice_number", "vendor_name", "invoice_date", "total")


class Extraction(BaseModel):
    doc_type: DocType
    fields: InvoiceFields
    confidence: dict[str, float] = Field(default_factory=dict)  # per field, 0..1
    extractor: str


class Severity(StrEnum):
    info = "info"
    warning = "warning"
    error = "error"


class RuleResult(BaseModel):
    rule: str
    passed: bool
    severity: Severity = Severity.error
    message: str


class Action(StrEnum):
    approve_payment = "approve_payment"
    request_info = "request_info"
    escalate = "escalate"
    reject = "reject"


class Proposal(BaseModel):
    action: Action
    reason: str
    approver_role: str  # who must sign off: ap_clerk or finance_manager
    draft_message: str | None = None  # e.g. the email to send the vendor


class Status(StrEnum):
    pending = "pending_approval"
    approved = "approved"
    rejected = "rejected"


class AuditEvent(BaseModel):
    document_id: int
    step: str
    detail: dict
    at: datetime


class DocumentResult(BaseModel):
    document_id: int
    filename: str
    extraction: Extraction
    checks: list[RuleResult]
    proposal: Proposal
    status: Status
    steps: list[str] = Field(default_factory=list)
