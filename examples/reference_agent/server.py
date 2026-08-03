"""Deterministic MCP server used by Aura's trace-contract CI example."""

from mcp.server import MCPServer

mcp = MCPServer("Aura Reference Support Agent", version="1.0.0")


@mcp.tool()
def search_customer(customer_id: str) -> dict[str, object]:
    """Return a deterministic customer record."""
    return {
        "customer_id": customer_id,
        "name": "Ada Lovelace",
        "status": "active",
    }


@mcp.tool()
def refund_order(order_id: str, amount: int) -> dict[str, object]:
    """Stage a refund for an order."""
    return {"order_id": order_id, "amount": amount, "status": "staged"}


@mcp.tool()
def delete_customer(customer_id: str) -> dict[str, object]:
    """Delete a customer record after approval."""
    return {"customer_id": customer_id, "status": "deleted"}


if __name__ == "__main__":
    mcp.run()
