#!/usr/bin/env python3
"""Manual/dummy trigger for a payment_request draft (Requirement 2b, sandbox-only).

Real-world payment confirmation is not automated anywhere in this vault (no
bank/gateway webhook) -- a human runs this after confirming out-of-band that
money was actually received, which then goes through the normal HITL flow:
this script only writes the draft to Pending_Approval/Sales/; it never calls
any payment API. Approve it via review_approvals.py, then run
process_approved_payments.py (Local-only) to actually record it in Odoo.

Usage: venv/bin/python3 create_payment_request.py <invoice_id> <amount> <method> [customer_name]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "mcp_servers"))
import vault_server as vs

# vault_server.py hardcodes VAULT_PATH for the Cloud VM deployment (see
# CLAUDE.md, Current Architecture State). When this script is run on a
# different machine (e.g. for local testing), point it at this checkout
# instead -- same monkeypatch approach used for the 2026-07-18 pipeline test.
vs.VAULT_PATH = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    if len(sys.argv) < 4:
        print(
            "Usage: create_payment_request.py <invoice_id> <amount> <method> [customer_name]",
            file=sys.stderr,
        )
        sys.exit(1)

    invoice_id = sys.argv[1]
    amount = float(sys.argv[2])
    method = sys.argv[3]
    customer_name = sys.argv[4] if len(sys.argv) > 4 else ""

    result = vs.write_payment_request(invoice_id, amount, method, customer_name)
    print(result)


if __name__ == "__main__":
    main()
