#!/usr/bin/env python3
"""Processes approved payment_request drafts against Odoo -- Local zone only
(Requirement 2b, payments/banking, sandbox-only).

Scans Approved/ for type: payment_request files, shows each one, and requires
typing the literal word CONFIRM to actually record it (anything else skips).
This is the only place in the codebase that calls record_payment() --
review_approvals.py never does. Hard-refuses to run at all unless
EXECUTION_ZONE=local, matching the fail-safe-default pattern used by
send_approved_emails.py. On success, the file is encrypted (secure_vault.py)
and moved to Done/Financials/, per the CLAUDE.md financial-file rule.
"""
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

VAULT_PATH = Path(__file__).resolve().parent
APPROVED_PATH = VAULT_PATH / "Approved"
FINANCIALS_PATH = VAULT_PATH / "Done" / "Financials"
DASHBOARD_PATH = VAULT_PATH / "Dashboard.md"

load_dotenv(VAULT_PATH / ".env")

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_execution_zone() -> None:
    zone = os.environ.get("EXECUTION_ZONE", "").strip().lower()
    if zone != "local":
        print(
            f"Refusing to run: EXECUTION_ZONE is {zone or '(unset)'!r}, not 'local'. "
            f"Payment recording is only permitted on the Local zone machine.",
            file=sys.stderr,
        )
        sys.exit(1)


def parse_request(path: Path):
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    fm_block, _ = match.group(1), match.group(2)
    fm = {}
    for line in fm_block.split("\n"):
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip().strip('"')

    if fm.get("type") != "payment_request":
        return None

    return {
        "invoice_id": fm.get("invoice_id", ""),
        "amount": fm.get("amount", "0"),
        "method": fm.get("method", ""),
        "customer_name": fm.get("customer_name", "Unknown Customer"),
    }


def append_dashboard_log(line: str) -> None:
    text = DASHBOARD_PATH.read_text(encoding="utf-8") if DASHBOARD_PATH.exists() else "# Agent Activity Dashboard\n"
    marker = "## Recent Execution Log"
    if marker not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += f"\n{marker}\n"
    text = text.rstrip("\n") + f"\n- {line}\n"
    DASHBOARD_PATH.write_text(text, encoding="utf-8")


def move_to_financials(path: Path, result: str) -> Path:
    FINANCIALS_PATH.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    fm_block, body = match.group(1), match.group(2)
    fm_block += f"\nrecorded: true\nrecorded_at: {now_iso()}\npayment_result: \"{result}\""
    dest = FINANCIALS_PATH / path.name
    dest.write_text(f"---\n{fm_block}\n---\n{body}", encoding="utf-8")
    path.unlink()
    return dest


def main() -> None:
    check_execution_zone()

    # Imported here, not at module top-level: mirrors send_approved_emails.py's
    # deferred import of gmail_sender -- keep the live-action dependency out of
    # the way until we've already confirmed this is the Local zone.
    sys.path.insert(0, str(VAULT_PATH / "mcp_servers"))
    from odoo_server import record_payment
    from secure_vault import encrypt_file

    if not APPROVED_PATH.exists():
        print("Approved/ does not exist — nothing to process.")
        return

    files = sorted(p for p in APPROVED_PATH.iterdir() if p.is_file())
    requests_ = [(p, parse_request(p)) for p in files]
    requests_ = [(p, r) for p, r in requests_ if r is not None]

    if not requests_:
        print("No type: payment_request files in Approved/ — nothing to process.")
        return

    if not sys.stdin.isatty():
        print(
            "Refusing to run: stdin is not an interactive terminal. This script "
            "records real (sandbox) payments and must be run directly in a "
            "terminal so each file can be confirmed one at a time -- piped/batch "
            "input is not permitted, since it can silently answer CONFIRM against "
            "the wrong file if the processing order isn't what the operator "
            "assumed (see the 2026-07-19 send_approved_emails.py incident in "
            "CLAUDE.md).",
            file=sys.stderr,
        )
        sys.exit(1)

    total = len(requests_)
    print(f"{total} payment request(s) in Approved/, processed oldest-first:\n")
    for i, (path, req) in enumerate(requests_, 1):
        print(f"  [{i}/{total}] {path.name}")
        print(f"          INVOICE:  {req['invoice_id']}")
        print(f"          AMOUNT:   {req['amount']}")
        print(f"          METHOD:   {req['method']}")
        print(f"          CUSTOMER: {req['customer_name']}")

    for i, (path, req) in enumerate(requests_, 1):
        print(f"\n{'=' * 60}")
        print(f"FILE {i}/{total}: {path.name}")
        print(f"{'=' * 60}")
        print(f"  INVOICE:  {req['invoice_id']}")
        print(f"  AMOUNT:   {req['amount']}")
        print(f"  METHOD:   {req['method']}")
        print(f"  CUSTOMER: {req['customer_name']}")

        choice = input(
            f"Record payment {i}/{total} against invoice {req['invoice_id']!r}? "
            f"Type CONFIRM to proceed (anything else skips): "
        ).strip()
        if choice != "CONFIRM":
            print("Skipped.")
            continue

        try:
            result = record_payment(
                invoice_id=req["invoice_id"],
                amount=float(req["amount"]),
                method=req["method"],
            )
        except Exception as e:
            print(f"Recording failed: {e}", file=sys.stderr)
            continue

        print(f"  Result: {result}")
        short_result = result if len(result) <= 140 else result[:137] + "..."

        if result.startswith("Failure") or result.startswith("EXECUTION_ZONE=cloud"):
            print("Payment was NOT recorded -- leaving file in Approved/ for retry.", file=sys.stderr)
            append_dashboard_log(
                f"{now_iso()} — PAYMENT FAILED: `{path.name}` against invoice "
                f"{req['invoice_id']} not recorded ({short_result})"
            )
            continue

        dest = move_to_financials(path, result)
        encrypt_file(dest)
        enc_dest = dest.with_suffix(".enc")

        append_dashboard_log(
            f"{now_iso()} — PAYMENT RECORDED: `{path.name}` against invoice "
            f"{req['invoice_id']} ({req['amount']} via {req['method']}), "
            f"encrypted and moved to Done/Financials/{enc_dest.name}"
        )
        print(f"Recorded and encrypted -> {enc_dest}")


if __name__ == "__main__":
    main()
