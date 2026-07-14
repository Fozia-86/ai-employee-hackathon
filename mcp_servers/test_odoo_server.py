"""Live integration test for the Odoo JSON-2 invoice-creation tool.

Exercises `create_odoo_invoice` against the real Odoo Cloud database configured
in `mcp_servers/.env` (ODOO_URL / ODOO_DB / ODOO_API_KEY). It creates a real
*draft* invoice for a clearly labelled test customer.

Notes:
- JSON-2 external API access is only available on Custom Odoo pricing plans. If
  the plan disallows JSON-2 (or the key/DB is wrong), the tool degrades to an
  "Offline Fallback" string and this test will fail with that message shown.
- The test skips (rather than fails) when credentials are absent, so it stays
  green in offline/sandbox environments.

Run:
    python -m pytest test_odoo_server.py -v
"""
import os

import pytest
from dotenv import load_dotenv

# Load the same .env the server uses before importing it.
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from odoo_server import create_odoo_invoice

TEST_CUSTOMER = "JSON2 Integration Test Co"
TEST_DISCOUNT = 10.0   # within the 20% autonomous ceiling
TEST_DEAL_VALUE = 500.0


@pytest.mark.skipif(
    not (os.environ.get("ODOO_URL") and os.environ.get("ODOO_API_KEY")),
    reason="Odoo credentials (ODOO_URL / ODOO_API_KEY) not configured; skipping live JSON-2 test.",
)
def test_create_invoice_live_json2():
    result = create_odoo_invoice(
        customer_name=TEST_CUSTOMER,
        discount_rate=TEST_DISCOUNT,
        deal_value=TEST_DEAL_VALUE,
    )
    print(f"\ncreate_odoo_invoice -> {result}")

    # Must have hit the live JSON-2 path, not the sandbox or offline fallback.
    assert "Sandbox" not in result, f"Ran in sandbox mode (missing creds?): {result}"
    assert "Offline Fallback" not in result, f"Live JSON-2 call failed: {result}"
    assert "Invoice ID" in result and "JSON-2 API Sync" in result, f"Unexpected result: {result}"


def test_signature_unchanged():
    """Guard the MCP-facing contract agent_runner.py depends on."""
    import inspect

    # FastMCP may leave the function unwrapped or expose the original via `.fn`.
    target = getattr(create_odoo_invoice, "fn", create_odoo_invoice)
    params = list(inspect.signature(target).parameters)
    assert params == ["customer_name", "discount_rate", "deal_value"], params
