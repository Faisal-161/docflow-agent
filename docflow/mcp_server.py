"""MCP server: exposes DocFlow's ERP tools and document processing to any MCP client
(Claude Desktop, Claude Code, other agents).

Run over stdio:  python -m docflow.mcp_server
Claude Code:     claude mcp add docflow -- python -m docflow.mcp_server

Approving payments is deliberately NOT exposed: an MCP client can process documents and read
proposals, but money only moves through the human approval endpoints.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .agent import DocFlowAgent


def build_server(agent: DocFlowAgent | None = None) -> MCPServer:
    agent = agent or DocFlowAgent.from_settings()
    server = MCPServer(
        "docflow",
        instructions="Accounts-payable tools: vendor lookup, purchase orders, "
        "duplicate checks, and invoice processing into proposals that a human approves.",
    )

    @server.tool(description="Look up a vendor by name (fuzzy match) in the vendor master.")
    def find_vendor(name: str) -> dict:
        return agent.tools.find_vendor(name)

    @server.tool(description="Fetch a purchase order and how much of it is still available to invoice.")
    def get_purchase_order(po_number: str) -> dict:
        return agent.tools.get_purchase_order(po_number)

    @server.tool(description="Check whether a vendor's invoice number or amount was already posted.")
    def check_duplicate(vendor_id: str, invoice_number: str | None = None, total: float | None = None) -> dict:
        return agent.tools.check_duplicate(vendor_id, invoice_number, total)

    @server.tool(
        description="Process invoice text into extracted fields, rule checks and a proposed action. "
        "The proposal waits for human approval; nothing is paid."
    )
    def process_invoice_text(text: str, filename: str = "mcp-input.txt") -> dict:
        res = agent.process_text(filename, text)
        return {
            "document_id": res.document_id,
            "proposal": res.proposal.model_dump(mode="json"),
            "failed_checks": [c.model_dump(mode="json") for c in res.checks if not c.passed],
            "fields": res.extraction.fields.model_dump(mode="json"),
        }

    @server.tool(description="List documents, optionally filtered by status (e.g. pending_approval).")
    def list_documents(status: str | None = None) -> list[dict]:
        return agent.db.documents(status)

    return server


if __name__ == "__main__":
    build_server().run()
