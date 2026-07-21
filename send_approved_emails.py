#!/usr/bin/env python3
"""Sends approved email drafts via live Gmail — Local zone only.

Scans Approved/ for type: email_draft files, shows each one, and requires
typing the literal word SEND to actually send it (anything else skips).
This is the only place in the codebase that calls a live Gmail-send API --
review_approvals.py never does. Hard-refuses to run at all unless
EXECUTION_ZONE=local, matching the CLOUD_ZONE fail-safe-default pattern
used elsewhere in this vault (see CLAUDE.md, Work-Zone Separation).
"""
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

VAULT_PATH = Path(__file__).resolve().parent
APPROVED_PATH = VAULT_PATH / "Approved"
DONE_PATH = VAULT_PATH / "Done"
DASHBOARD_PATH = VAULT_PATH / "Dashboard.md"

load_dotenv(VAULT_PATH / ".env")

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
DRAFT_REPLY_RE = re.compile(r"## Draft Reply\n\n(.*?)\s*$", re.DOTALL)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_execution_zone() -> None:
    zone = os.environ.get("EXECUTION_ZONE", "").strip().lower()
    if zone != "local":
        print(
            f"Refusing to run: EXECUTION_ZONE is {zone or '(unset)'!r}, not 'local'. "
            f"Live email sending is only permitted on the Local zone machine.",
            file=sys.stderr,
        )
        sys.exit(1)


def parse_draft(path: Path):
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    fm_block, body = match.group(1), match.group(2)
    fm = {}
    for line in fm_block.split("\n"):
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip().strip('"')

    if fm.get("type") != "email_draft":
        return None

    reply_match = DRAFT_REPLY_RE.search(body)
    reply_body = reply_match.group(1).strip() if reply_match else ""

    return {
        "to": fm.get("original_sender", ""),
        "subject": fm.get("original_subject", "(no subject)"),
        "gmail_thread_id": fm.get("gmail_thread_id", ""),
        "gmail_rfc_message_id": fm.get("gmail_rfc_message_id", ""),
        "reply_body": reply_body,
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


def move_to_done(path: Path, gmail_sent_id: str) -> Path:
    DONE_PATH.mkdir(exist_ok=True)
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    fm_block, body = match.group(1), match.group(2)
    fm_block += f"\nsent: true\nsent_at: {now_iso()}\ngmail_sent_message_id: {gmail_sent_id}"
    dest = DONE_PATH / path.name
    dest.write_text(f"---\n{fm_block}\n---\n{body}", encoding="utf-8")
    path.unlink()
    return dest


def main() -> None:
    check_execution_zone()

    # Imported here, not at module top-level: importing gmail_sender pulls in
    # the Google API client libraries and reads token.json, which should only
    # happen once we've already confirmed this is the Local zone.
    from gmail_sender import send_email_reply

    if not APPROVED_PATH.exists():
        print("Approved/ does not exist — nothing to send.")
        return

    files = sorted(p for p in APPROVED_PATH.iterdir() if p.is_file())
    drafts = [(p, parse_draft(p)) for p in files]
    drafts = [(p, d) for p, d in drafts if d is not None]

    if not drafts:
        print("No type: email_draft files in Approved/ — nothing to send.")
        return

    if not sys.stdin.isatty():
        print(
            "Refusing to run: stdin is not an interactive terminal. This script "
            "sends real, live email and must be run directly in a terminal so each "
            "file can be confirmed one at a time -- piped/batch input (e.g. "
            "`echo SEND | ...` or a pre-typed answer sequence) is not permitted, "
            "since it can silently answer SEND against the wrong file if the "
            "processing order isn't what the operator assumed.",
            file=sys.stderr,
        )
        sys.exit(1)

    total = len(drafts)
    print(f"{total} email draft(s) in Approved/, processed oldest-first:\n")
    for i, (path, draft) in enumerate(drafts, 1):
        print(f"  [{i}/{total}] {path.name}")
        print(f"          TO:      {draft['to']}")
        print(f"          SUBJECT: Re: {draft['subject']}")

    for i, (path, draft) in enumerate(drafts, 1):
        print(f"\n{'=' * 60}")
        print(f"FILE {i}/{total}: {path.name}")
        print(f"{'=' * 60}")
        print(f"  TO:       {draft['to']}")
        print(f"  SUBJECT:  Re: {draft['subject']}")
        print(f"  THREADED: {'yes' if draft['gmail_thread_id'] else 'no (no thread info on this draft)'}")
        print(f"  BODY:\n{draft['reply_body']}\n")

        choice = input(
            f"Send email {i}/{total} to {draft['to']!r}? "
            f"Type SEND to confirm (anything else skips): "
        ).strip()
        if choice != "SEND":
            print("Skipped.")
            continue

        try:
            sent_id = send_email_reply(
                to_addr=draft["to"],
                original_subject=draft["subject"],
                reply_body=draft["reply_body"],
                gmail_thread_id=draft["gmail_thread_id"],
                gmail_rfc_message_id=draft["gmail_rfc_message_id"],
            )
        except Exception as e:
            print(f"Send failed: {e}", file=sys.stderr)
            continue

        dest = move_to_done(path, sent_id)
        append_dashboard_log(
            f"{now_iso()} — SENT: `{path.name}` emailed to {draft['to']} "
            f"(gmail id: {sent_id}), moved to Done/"
        )
        print(f"Sent -> {dest}")


if __name__ == "__main__":
    main()
