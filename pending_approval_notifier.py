"""
WhatsApp-based approval notifier (client-requested feature, 2026-08-19).

Polls Pending_Approval/ the same way web_gui/vault_ops.list_pending() does
(root files + one level into Sales/Support/General domain subfolders). For
every item it hasn't already notified about, sends the configured approver
a WhatsApp message with a short 6-character reply code and reply
instructions ("APPROVE <code>" / "REJECT <code> <reason>").

Deliberately runs on the CLOUD VM, alongside agent-runner.service -- that is
where drafts are actually created (agent_runner.py), so notifications go out
the instant a draft lands, with no dependency on Syncthing syncing the file
to the Local machine first (Syncthing has been unreliable in this project).

The matching reply-handling logic lives in web_gui/app.py's WhatsApp webhook
route (_handle_whatsapp_approval_reply) -- it reads the same state file this
script writes to resolve a reply code back to a filename, then calls the
exact same vault_ops.approve()/reject() functions the browser Approve/Reject
buttons use. See .env.example's WHATSAPP_APPROVER_NUMBER comment and the
"Dashboard Ka Poora Tour" guide for the full picture, including the
important security note about what this bypasses and why.

Run continuously (systemd service, same pattern as agent-runner.service):
    python3 pending_approval_notifier.py
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

VAULT_PATH = Path(__file__).resolve().parent
load_dotenv(VAULT_PATH / ".env")
sys.path.insert(0, str(VAULT_PATH))

from whatsapp_notify import send_whatsapp_message  # noqa: E402

PENDING_PATH = VAULT_PATH / "Pending_Approval"
STATE_PATH = VAULT_PATH / "Needs_Action" / ".whatsapp_approval_codes.json"
POLL_INTERVAL_SECONDS = int(os.environ.get("APPROVAL_NOTIFY_POLL_SECONDS", "30"))


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def make_code(filename: str) -> str:
    return hashlib.sha256(filename.encode("utf-8")).hexdigest()[:6].upper()


def list_pending_files() -> list[Path]:
    """Same scope as web_gui/vault_ops.list_pending(): root files + one
    level into domain subfolders, skipping dotfiles."""
    if not PENDING_PATH.exists():
        return []
    root_files = [p for p in PENDING_PATH.iterdir() if p.is_file() and not p.name.startswith(".")]
    subfolder_files = [p for p in PENDING_PATH.glob("*/*") if p.is_file() and not p.name.startswith(".")]
    return sorted(set(root_files + subfolder_files), key=lambda p: p.name)


def summarize_for_whatsapp(path: Path) -> str:
    """Crude frontmatter-stripped first-line summary -- mirrors
    web_gui/vault_ops.summarize()'s logic without importing it (this script
    intentionally has no dependency on the web_gui package)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "(could not read file)"
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) > 2:
            body = parts[2]
    for line in body.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:200]
    return "(empty draft)"


def poll_once(state: dict) -> int:
    approver = os.environ.get("WHATSAPP_APPROVER_NUMBER", "")
    if not approver:
        return 0

    notified = state.setdefault("notified", {})
    codes = state.setdefault("codes", {})

    current = {p.name: p for p in list_pending_files()}
    new_count = 0

    for name, path in current.items():
        if name in notified:
            continue
        code = make_code(name)
        summary = summarize_for_whatsapp(path)
        message = (
            f"New item needs your approval:\n\n{summary}\n\n"
            f"Reply APPROVE {code} to approve, or REJECT {code} <reason> to reject."
        )
        ok, info = send_whatsapp_message(approver, message)
        if ok:
            notified[name] = code
            codes[code] = name
            new_count += 1
        else:
            print(f"Failed to notify for {name}: {info}", flush=True)

    # Drop codes for items that are no longer pending (already handled via
    # the dashboard, WhatsApp, or the CLI) so codes can't be replayed.
    stale = [name for name in notified if name not in current]
    for name in stale:
        code = notified.pop(name, None)
        if code:
            codes.pop(code, None)

    save_state(state)
    return new_count


if __name__ == "__main__":
    state = load_state()
    print(f"Pending-approval WhatsApp notifier started. Polling every {POLL_INTERVAL_SECONDS}s.", flush=True)
    while True:
        try:
            n = poll_once(state)
            if n:
                print(f"Notified approver about {n} new pending item(s).", flush=True)
        except Exception as e:
            print(f"Error during poll cycle: {e}", flush=True)
        time.sleep(POLL_INTERVAL_SECONDS)
