"""
Local-zone standalone processor for WhatsApp triage.

Lets you test/demo the whatsapp_watcher.py -> triage_whatsapp() flow on this
machine without needing the full agent_runner.py/MCP stack -- that stack is
hardcoded to the Ubuntu VM's paths (see CLAUDE.md's Current Architecture
State) and isn't meant to run locally. This script does the same job
agent_runner.py's WhatsApp dispatch branch does, standalone:

  1. Scans Needs_Action/ for TRIGGER_whatsapp_*.md files (written by
     whatsapp_watcher.py).
  2. Reads each one's original_file, calls vault_server.triage_whatsapp()
     on it (rule-based + Claude-enhanced classification, draft-reply if
     business-relevant).
  3. Archives both files to Done/Processed_Triggers/ with a SUCCESS prefix
     (same convention agent_runner.py uses) so re-running this script
     doesn't reprocess the same message twice.

Usage:
    python process_whatsapp_triggers.py
"""
import datetime
import glob
import logging
import os
import re
import shutil
import sys
import types

logging.getLogger("httpx").setLevel(logging.WARNING)

VAULT_PATH = os.path.dirname(os.path.abspath(__file__))

# vault_server.py imports mcp.server.fastmcp only to register @mcp.tool()
# decorators -- irrelevant for calling the underlying functions directly, so
# stub it out rather than requiring the full MCP package (same approach as
# demo_claude_labeling.py).
_mcp = types.ModuleType("mcp")
_mcp_server = types.ModuleType("mcp.server")
_mcp_fastmcp = types.ModuleType("mcp.server.fastmcp")


class _FastMCP:
    def __init__(self, *a, **k):
        pass

    def tool(self):
        def deco(f):
            return f
        return deco


_mcp_fastmcp.FastMCP = _FastMCP
sys.modules["mcp"] = _mcp
sys.modules["mcp.server"] = _mcp_server
sys.modules["mcp.server.fastmcp"] = _mcp_fastmcp

sys.path.insert(0, os.path.join(VAULT_PATH, "mcp_servers"))
import vault_server as vs  # noqa: E402
vs.VAULT_PATH = VAULT_PATH

NEEDS_ACTION = os.path.join(VAULT_PATH, "Needs_Action")
ARCHIVE_DIR = os.path.join(VAULT_PATH, "Done", "Processed_Triggers")


def parse_original_file(trigger_text):
    m = re.search(r'^original_file:\s*(.+)$', trigger_text, re.MULTILINE)
    return m.group(1).strip() if m else None


def parse_whatsapp_fields(raw_text):
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', raw_text, re.DOTALL)
    if not fm_match:
        return None
    frontmatter, body = fm_match.group(1), fm_match.group(2)
    fm = dict(re.findall(r'^(\w+):\s*(.*)$', frontmatter, re.MULTILINE))
    if fm.get("type") != "whatsapp":
        return None
    return {
        "sender": fm.get("from", "(unknown chat)"),
        "body": body.strip(),
        "whatsapp_message_id": fm.get("whatsapp_message_id", ""),
    }


def main():
    if not vs._claude_client:
        print(
            "WARNING: ANTHROPIC_API_KEY not found (checked mcp_servers/.env) -- "
            "will fall back to rule-based-only classification.\n"
        )

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    trigger_files = sorted(glob.glob(os.path.join(NEEDS_ACTION, "TRIGGER_whatsapp_*.md")))

    if not trigger_files:
        print(
            "No WhatsApp triggers found in Needs_Action/. Run whatsapp_watcher.py "
            "first and send yourself a test message containing a business keyword."
        )
        return

    for trigger_path in trigger_files:
        trigger_name = os.path.basename(trigger_path)
        with open(trigger_path, "r", encoding="utf-8") as f:
            trigger_text = f.read()

        original_file = parse_original_file(trigger_text)
        if not original_file:
            print(f"Skipping {trigger_name}: no original_file reference found.")
            continue

        original_path = os.path.join(NEEDS_ACTION, original_file)
        if not os.path.exists(original_path):
            print(f"Skipping {trigger_name}: original file {original_file} not found.")
            continue

        with open(original_path, "r", encoding="utf-8") as f:
            fields = parse_whatsapp_fields(f.read())
        if not fields:
            print(f"Skipping {trigger_name}: {original_file} isn't type: whatsapp.")
            continue

        print(f"Processing {original_file} from {fields['sender']!r}...")
        result = vs.triage_whatsapp(fields["sender"], fields["body"], fields["whatsapp_message_id"])
        print(f"  -> {result}\n")

        timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        shutil.move(trigger_path, os.path.join(ARCHIVE_DIR, f"{timestamp}_SUCCESS_{trigger_name}"))
        shutil.move(original_path, os.path.join(ARCHIVE_DIR, f"{timestamp}_SUCCESS_{original_file}"))


if __name__ == "__main__":
    main()
