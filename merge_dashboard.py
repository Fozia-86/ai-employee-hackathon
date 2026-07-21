#!/usr/bin/env python3
"""Merges /Updates/ signal files into Dashboard.md — Local zone only.

Requirement 3b (Dashboard single-writer): agent_runner.py (Cloud) no longer
writes Dashboard.md directly — its update_dashboard() calls were replaced with
write_dashboard_update(), which drops small timestamped files into Updates/
instead. This script is the *only* thing that writes Dashboard.md's status
table, and it never destroys the `## Recent Execution Log` section the way
the old direct-overwrite update_dashboard() tool did (that section is
appended to, never replaced) -- review_approvals.py/send_approved_emails.py
append to that same section directly, so this script must be additive too.

Hard-refuses to run unless EXECUTION_ZONE=local, matching the fail-safe-default
pattern used elsewhere in this vault (see CLAUDE.md, Work-Zone Separation, and
send_approved_emails.py).

Usage: EXECUTION_ZONE=local venv/bin/python3 merge_dashboard.py
"""
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from review_approvals import append_dashboard_log, now_iso

VAULT_PATH = Path(__file__).resolve().parent
UPDATES_PATH = VAULT_PATH / "Updates"
MERGED_PATH = UPDATES_PATH / "Merged"
DASHBOARD_PATH = VAULT_PATH / "Dashboard.md"

load_dotenv(VAULT_PATH / ".env")

LOG_MARKER = "## Recent Execution Log"
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n\n(.*)$", re.DOTALL)


def check_execution_zone() -> None:
    zone = os.environ.get("EXECUTION_ZONE", "").strip().lower()
    if zone != "local":
        print(
            f"Refusing to run: EXECUTION_ZONE is {zone or '(unset)'!r}, not 'local'. "
            f"Dashboard.md must only be merged from the Local zone machine.",
            file=sys.stderr,
        )
        sys.exit(1)


def list_updates() -> list[Path]:
    if not UPDATES_PATH.exists():
        return []
    return sorted(p for p in UPDATES_PATH.iterdir() if p.is_file() and p.suffix == ".md")


def parse_update(path: Path) -> tuple[str, str]:
    """Returns (status_table, note) from an Updates/*.md file."""
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    body = match.group(2) if match else text
    note = ""
    note_match = re.search(r"^note:\s*(.*)$", body, re.MULTILINE)
    if note_match:
        note = note_match.group(1).strip()
        body = body[: note_match.start()]
    return body.strip(), note


def split_dashboard() -> tuple[str, str]:
    """Returns (top, log_section) split at the Recent Execution Log marker.
    top is replaced wholesale with the latest status table; log_section is
    only ever appended to, never replaced -- this is the fix for the
    overwrite bug described in the module docstring."""
    if not DASHBOARD_PATH.exists():
        return "# Agent Activity Dashboard\n", f"\n{LOG_MARKER}\n"
    text = DASHBOARD_PATH.read_text(encoding="utf-8")
    if LOG_MARKER not in text:
        return text, f"\n{LOG_MARKER}\n"
    top, _, rest = text.partition(LOG_MARKER)
    return top, LOG_MARKER + rest


def main() -> None:
    check_execution_zone()

    updates = list_updates()
    if not updates:
        print("Updates/ is empty — nothing to merge.")
        return

    MERGED_PATH.mkdir(parents=True, exist_ok=True)

    latest_status_table = None
    for path in updates:
        status_table, note = parse_update(path)
        if status_table:
            latest_status_table = status_table
        summary = note or path.stem
        append_dashboard_log(f"{now_iso()} — DASHBOARD UPDATE: {summary}")

    if latest_status_table is not None:
        top, log_section = split_dashboard()
        # append_dashboard_log() above already re-read/re-wrote DASHBOARD_PATH
        # per update, so re-read the current log_section before rewriting top.
        _, log_section = split_dashboard()
        new_top = "# Agent Activity Dashboard\n\n" + latest_status_table + "\n\n"
        DASHBOARD_PATH.write_text(new_top + log_section, encoding="utf-8")

    for path in updates:
        path.rename(MERGED_PATH / path.name)

    print(f"Merged {len(updates)} update(s) into Dashboard.md.")


if __name__ == "__main__":
    main()
