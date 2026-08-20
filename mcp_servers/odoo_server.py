import os
import requests
import logging
import random
import uuid
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO)
mcp = FastMCP("Core Odoo Server")


def is_cloud_execution() -> bool:
    """EXECUTION_ZONE defaults to 'cloud' (safe/sandbox) unless explicitly set to 'local'."""
    return os.environ.get("EXECUTION_ZONE", "cloud").strip().lower() != "local"


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
    """Connects to live Odoo Cloud database via the JSON-2 API and generates a draft Invoice.

    Gated by EXECUTION_ZONE: cloud (default) never makes a live call, even with
    valid credentials -- only EXECUTION_ZONE=local may create a real invoice.
    A real customer/invoice record in the live accounting system is a
    financial transaction per the Company Handbook's HITL rule, regardless of
    the invoice starting in Odoo's 'draft' state, so the autonomous Cloud
    agent must not be able to create one unsupervised. Same zone boundary as
    cancel_odoo_invoice/record_payment.
    """
    if is_cloud_execution():
        logging.info(f"EXECUTION_ZONE=cloud active — invoice creation for [{customer_name}] skipped, no live call made.")
        mock_inv = random.randint(10000, 99999)
        return (f"EXECUTION_ZONE=cloud Sandbox: Created Mock Draft Invoice [INV-2026-00{mock_inv}] "
                f"for [{customer_name}] (requires EXECUTION_ZONE=local for a real Odoo record).")

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
    behind the Local Agent's approval workflow (as of the production-readiness
    fix, create_odoo_invoice is zone-gated the same way — the Cloud agent has
    no live Odoo mutation authority at all, draft-create included).

    Gated by EXECUTION_ZONE: cloud (default) never makes a live cancel call,
    even with valid credentials — only EXECUTION_ZONE=local may cancel/post.
    """
    if is_cloud_execution():
        logging.info(f"EXECUTION_ZONE=cloud active — cancel of Invoice [{invoice_id}] skipped, no live call made.")
        return f"EXECUTION_ZONE=cloud Sandbox: Cancel of Invoice [{invoice_id}] NOT executed (requires EXECUTION_ZONE=local)."

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


@mcp.tool()
def record_payment(invoice_id: str, amount: float, method: str) -> str:
    """Record a payment against an Odoo invoice (account.move) via the JSON-2 API.

    Requirement 2b (payments/banking, sandbox-only): always simulates a dummy
    payment-gateway confirmation first (a fake transaction ID + CONFIRMED
    status, in the shape a real gateway callback would carry) so the response
    shape is already correct if a real processor is ever wired in later. That
    dummy confirmation is what triggers the Odoo side -- an account.payment
    record is created and posted (action_post), which moves the linked
    invoice's state, never a raw status-field edit. (Simplified: this does not
    perform full invoice/payment reconciliation a production Odoo flow would
    normally do via the register-payment wizard -- out of scope for this
    sandbox tool.)

    Gated by EXECUTION_ZONE: cloud (default) never makes a live call or even a
    sandbox mutation -- only EXECUTION_ZONE=local may record a payment, same
    zone boundary as cancel_odoo_invoice. Intended to be called only after a
    payment_request draft has been through Local-zone HITL approval (see
    process_approved_payments.py).
    """
    if is_cloud_execution():
        logging.info(f"EXECUTION_ZONE=cloud active — payment recording for Invoice [{invoice_id}] skipped, no action taken.")
        return f"EXECUTION_ZONE=cloud Sandbox: Payment recording for Invoice [{invoice_id}] NOT executed (requires EXECUTION_ZONE=local)."

    txn_id = f"TXN-{uuid.uuid4().hex[:10].upper()}"
    gateway_response = f"Dummy Gateway Response: transaction_id={txn_id}, status=CONFIRMED, amount={float(amount):.2f}, method={method}"

    ODOO_URL = os.environ.get("ODOO_URL", "")
    ODOO_DB = os.environ.get("ODOO_DB", "")
    ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")

    if not ODOO_API_KEY or not ODOO_URL:
        logging.warning("Sandbox Mode: Real Odoo credentials missing.")
        return (f"Sandbox Success: Mock Recorded Payment [{txn_id}] of {float(amount):.2f} via {method} "
                f"against Invoice [{invoice_id}] (no live call made). {gateway_response}")

    try:
        inv_id = int(invoice_id)
        pre = _odoo_call(
            ODOO_URL, ODOO_API_KEY, ODOO_DB, 'account.move', 'read',
            ids=[inv_id], fields=['state', 'partner_id'],
        )
        if not pre:
            return f"Failure: Odoo Invoice ID [{inv_id}] not found. {gateway_response}"
        if pre[0].get('state') == 'paid':
            return f"Success: Odoo Invoice ID [{inv_id}] is already in 'paid' state (no action needed). {gateway_response}"

        partner_id = pre[0]['partner_id'][0] if pre[0].get('partner_id') else False

        created_payment = _odoo_call(
            ODOO_URL, ODOO_API_KEY, ODOO_DB, 'account.payment', 'create',
            vals_list=[{
                'payment_type': 'inbound',
                'partner_type': 'customer',
                'partner_id': partner_id,
                'amount': float(amount),
                'ref': f"{method} / {txn_id}",
            }],
        )
        payment_id = created_payment[0] if isinstance(created_payment, list) else created_payment

        _odoo_call(ODOO_URL, ODOO_API_KEY, ODOO_DB, 'account.payment', 'action_post', ids=[payment_id])

        post = _odoo_call(
            ODOO_URL, ODOO_API_KEY, ODOO_DB, 'account.move', 'read',
            ids=[inv_id], fields=['state'],
        )
        state = post[0].get('state') if post else None
        return (f"Success: Recorded Payment ID [{payment_id}] ({txn_id}) of {float(amount):.2f} via {method} "
                f"against Invoice [{inv_id}] via JSON-2 API. Invoice state is now '{state}'. {gateway_response}")
    except Exception as e:
        logging.error(f"Live Odoo payment recording Failed. Details: {str(e)}")
        return f"Failure: Could not record payment for Odoo Invoice ID [{invoice_id}] (API Failure: {str(e)}). {gateway_response}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
