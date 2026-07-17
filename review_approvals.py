#!/usr/bin/env python3
"""Interactive HITL reviewer for Pending_Approval/.

Lists every file in Pending_Approval/, lets a human approve/reject/skip
each one, and moves it to Approved/ or Rejected/ accordingly. This script
never calls any live send/post/Odoo API — it only reads, edits frontmatter
on, and moves local files.
"""
import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

VAULT_PATH = Path(__file__).resolve().parent
PENDING_PATH = VAULT_PATH / "Pending_Approval"
APPROVED_PATH = VAULT_PATH / "Approved"
REJECTED_PATH = VAULT_PATH / "Rejected"
DASHBOARD_PATH = VAULT_PATH / "Dashboard.md"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_file(path: Path):
    """Returns (frontmatter_dict_or_None, frontmatter_order, body_text)."""
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None, [], text

    fm_block, body = match.group(1), match.group(2)
    fm = {}
    order = []
    for line in fm_block.split("\n"):
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        fm[key] = value.strip()
        order.append(key)
    return fm, order, body


def render_file(fm: dict, order: list, body: str) -> str:
    if fm is None:
        return body
    lines = []
    seen = set()
    for key in order:
        if key in fm:
            lines.append(f"{key}: {fm[key]}")
            seen.add(key)
    for key, value in fm.items():
        if key not in seen:
            lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def summarize(path: Path) -> tuple[str, str]:
    """Returns (type, short_summary) for display."""
    fm, _, body = parse_file(path)
    file_type = fm.get("type", "(no frontmatter)") if fm else "(no frontmatter)"
    summary = ""
    for line in body.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            summary = stripped
            break
    if len(summary) > 90:
        summary = summary[:87] + "..."
    return file_type, summary


def append_dashboard_log(line: str) -> None:
    text = DASHBOARD_PATH.read_text(encoding="utf-8") if DASHBOARD_PATH.exists() else "# Agent Activity Dashboard\n"
    marker = "## Recent Execution Log"
    if marker not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += f"\n{marker}\n"
    text = text.rstrip("\n") + f"\n- {line}\n"
    DASHBOARD_PATH.write_text(text, encoding="utf-8")


def approve(path: Path, reason: str = None) -> Path:
    APPROVED_PATH.mkdir(exist_ok=True)
    fm, order, body = parse_file(path)
    if fm is None:
        fm, order = {}, []
    if "decision" not in order:
        order.append("decision")
    if "reviewed_at" not in order:
        order.append("reviewed_at")
    fm["decision"] = "approved"
    fm["reviewed_at"] = now_iso()

    new_text = render_file(fm, order, body)
    dest = APPROVED_PATH / path.name
    dest.write_text(new_text, encoding="utf-8")
    path.unlink()

    append_dashboard_log(f"{now_iso()} — APPROVED: `{path.name}` moved to Approved/")
    return dest


def reject(path: Path, reason: str) -> Path:
    REJECTED_PATH.mkdir(exist_ok=True)
    fm, order, body = parse_file(path)
    if fm is None:
        fm, order = {}, []
    for key in ("decision", "reviewed_at", "reason"):
        if key not in order:
            order.append(key)
    fm["decision"] = "rejected"
    fm["reviewed_at"] = now_iso()
    fm["reason"] = reason or "(no reason given)"

    new_text = render_file(fm, order, body)
    dest = REJECTED_PATH / path.name
    dest.write_text(new_text, encoding="utf-8")
    path.unlink()

    append_dashboard_log(f"{now_iso()} — REJECTED: `{path.name}` moved to Rejected/ (reason: {fm['reason']})")
    return dest


def list_pending() -> list[Path]:
    if not PENDING_PATH.exists():
        return []
    return sorted(p for p in PENDING_PATH.iterdir() if p.is_file())


def run_interactive() -> None:
    files = list_pending()
    if not files:
        print("Pending_Approval/ is empty — nothing to review.")
        return

    print(f"{len(files)} file(s) in Pending_Approval/:\n")
    for path in files:
        file_type, summary = summarize(path)
        print(f"  {path.name}")
        print(f"    type: {file_type}")
        print(f"    summary: {summary or '(empty)'}\n")

    for path in files:
        if not path.exists():
            continue
        file_type, summary = summarize(path)
        print(f"\n--- {path.name} ---")
        print(f"type: {file_type}")
        print(f"summary: {summary or '(empty)'}")
        choice = input("[a]pprove / [r]eject / [s]kip / [q]uit? ").strip().lower()
        if choice == "a":
            dest = approve(path)
            print(f"Approved -> {dest}")
        elif choice == "r":
            reason = input("Rejection reason: ").strip()
            dest = reject(path, reason)
            print(f"Rejected -> {dest}")
        elif choice == "q":
            print("Stopping review.")
            break
        else:
            print("Skipped.")


def run_non_interactive(approve_name: str = None, reject_name: str = None, reason: str = None) -> None:
    if approve_name:
        path = PENDING_PATH / approve_name
        if not path.exists():
            print(f"Error: {path} not found.", file=sys.stderr)
            sys.exit(1)
        dest = approve(path)
        print(f"Approved -> {dest}")
    if reject_name:
        path = PENDING_PATH / reject_name
        if not path.exists():
            print(f"Error: {path} not found.", file=sys.stderr)
            sys.exit(1)
        if reason is None:
            reason = input("Rejection reason: ").strip()
        dest = reject(path, reason)
        print(f"Rejected -> {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Review files in Pending_Approval/.")
    parser.add_argument("--approve", metavar="FILENAME", help="Non-interactively approve a single file.")
    parser.add_argument("--reject", metavar="FILENAME", help="Non-interactively reject a single file.")
    parser.add_argument("--reason", metavar="TEXT", help="Rejection reason (used with --reject).")
    args = parser.parse_args()

    if args.approve or args.reject:
        run_non_interactive(approve_name=args.approve, reject_name=args.reject, reason=args.reason)
    else:
        run_interactive()


if __name__ == "__main__":
    main()
