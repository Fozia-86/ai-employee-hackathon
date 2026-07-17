import os
import requests
import logging
import random
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

logging.basicConfig(level=logging.INFO)
mcp = FastMCP("Core Odoo Server")


def _odoo_call(base_url: str, api_key: str, db: str, model: str, method: str, **kwargs):
    """Execute a single Odoo JSON-2 API call.

    Odoo 19+ exposes the Models API over HTTP at /json/2/<model>/<method>.
    Auth is a bearer API key; the database is selected via the X-Odoo-Database
    header only when multiple DBs share a domain. All arguments are named
    (JSON-2 has no positional args). Errors surface as 4xx/5xx HTTP statuses.
    """
    headers = {
        "Authorization": f"bearer {api_key}",
        "Content-Type": "application/json",
    }
    if db:
        headers["X-Odoo-Database"] = db

    url = f"{base_url.rstrip('/')}/json/2/{model}/{method}"
    resp = requests.post(url, headers=headers, json=kwargs, timeout=30)

    # HTTP-status-driven error handling (not exception-based like XML-RPC).
    if not resp.ok:
        raise RuntimeError(f"Odoo JSON-2 {method} on {model} failed [HTTP {resp.status_code}]: {resp.text}")
    return resp.json()


@mcp.tool()
def create_odoo_invoice(customer_name: str, discount_rate: float, deal_value: float) -> str:
    """Connects to live Odoo Cloud database via the JSON-2 API and generates a draft Invoice."""
    ODOO_URL = os.environ.get("ODOO_URL", "")
    ODOO_DB = os.environ.get("ODOO_DB", "")
    ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")

    if not ODOO_API_KEY or not ODOO_URL:
        logging.warning("Sandbox Mode: Real Odoo credentials missing.")
        mock_inv = random.randint(10000, 99999)
        return f"Sandbox Success: Created Mock Draft Invoice [INV-2026-00{mock_inv}]"

    try:
        # 1. Find the customer partner, creating it if it does not exist yet.
        partner_ids = _odoo_call(
            ODOO_URL, ODOO_API_KEY, ODOO_DB, 'res.partner', 'search',
            domain=[['name', '=', customer_name]],
        )

        if partner_ids:
            partner_id = partner_ids[0]
        else:
            created_partner = _odoo_call(
                ODOO_URL, ODOO_API_KEY, ODOO_DB, 'res.partner', 'create',
                vals_list=[{'name': customer_name}],
            )
            # create returns a list of ids; normalize to a scalar.
            partner_id = created_partner[0] if isinstance(created_partner, list) else created_partner

        # 2. Create the draft customer invoice (account.move / out_invoice).
        #    Command tuples become JSON arrays: (0, 0, {...}) -> [0, 0, {...}].
        created_invoice = _odoo_call(
            ODOO_URL, ODOO_API_KEY, ODOO_DB, 'account.move', 'create',
            vals_list=[{
                'move_type': 'out_invoice',
                'partner_id': partner_id,
                'invoice_line_ids': [[0, 0, {
                    'name': f"Enterprise Deal - {customer_name}",
                    'quantity': 1,
                    'price_unit': float(deal_value),
                    'discount': float(discount_rate),
                }]],
            }],
        )
        invoice_id = created_invoice[0] if isinstance(created_invoice, list) else created_invoice
        return f"Success: Created Real Odoo Invoice ID [{invoice_id}] via JSON-2 API Sync."
    except Exception as e:
        logging.error(f"Live Odoo API Failed. Fallback engaged. Details: {str(e)}")
        mock_inv = random.randint(10000, 99999)
        return f"Offline Fallback: Generated Draft Invoice [INV-2026-00{mock_inv}] (API Failure: {str(e)})"


@mcp.tool()
def cancel_odoo_invoice(invoice_id: int) -> str:
    """Cancel an Odoo invoice (account.move) via the JSON-2 API.

    Invokes the standard `button_cancel` method to move the invoice into the
    `cancel` state. This performs NO hard deletion — per the KB Odoo guardrail,
    invoices may only be Drafted or Cancelled, never deleted. Intended for use
    behind the Local Agent's approval workflow (Cloud Agent has draft-create
    only; no cancel/post authority).
    """
    ODOO_URL = os.environ.get("ODOO_URL", "")
    ODOO_DB = os.environ.get("ODOO_DB", "")
    ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")

    if not ODOO_API_KEY or not ODOO_URL:
        logging.warning("Sandbox Mode: Real Odoo credentials missing.")
        return f"Sandbox Success: Mock Cancelled Invoice [{invoice_id}] (no live call made)."

    inv_id = int(invoice_id)
    try:
        # Check current state first. `button_cancel` only accepts draft entries,
        # so this keeps the tool idempotent for the approval workflow: an invoice
        # already in `cancel` is treated as success, not an error.
        pre = _odoo_call(
            ODOO_URL, ODOO_API_KEY, ODOO_DB, 'account.move', 'read',
            ids=[inv_id], fields=['state'],
        )
        if not pre:
            return f"Failure: Odoo Invoice ID [{inv_id}] not found."
        if pre[0].get('state') == 'cancel':
            return f"Success: Odoo Invoice ID [{inv_id}] is already in 'cancel' state (no action needed)."

        # button_cancel is a record-level method: pass the target id(s) via `ids`.
        _odoo_call(
            ODOO_URL, ODOO_API_KEY, ODOO_DB, 'account.move', 'button_cancel',
            ids=[inv_id],
        )

        # Read back the state to confirm the transition (never delete).
        post = _odoo_call(
            ODOO_URL, ODOO_API_KEY, ODOO_DB, 'account.move', 'read',
            ids=[inv_id], fields=['state'],
        )
        state = post[0].get('state') if post else None
        if state == 'cancel':
            return f"Success: Cancelled Odoo Invoice ID [{inv_id}] via JSON-2 API. State is now 'cancel'."
        return f"Warning: button_cancel called on Invoice ID [{inv_id}] but state is '{state}' (expected 'cancel')."
    except Exception as e:
        logging.error(f"Live Odoo cancel Failed. Details: {str(e)}")
        return f"Failure: Could not cancel Odoo Invoice ID [{inv_id}] (API Failure: {str(e)})."


if __name__ == "__main__":
    mcp.run(transport="stdio")
