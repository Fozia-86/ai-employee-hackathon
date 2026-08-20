"""File-level operations backing the Web GUI's Approval Center.

Mirrors review_approvals.py's frontmatter parse/render/approve/reject logic
(kept behaviorally identical so the CLI and the GUI never disagree about what
"approved" means on disk) but, per Requirement 3b (Dashboard single-writer),
writes a signal file to Updates/ instead of appending to Dashboard.md
directly -- only the Local-only merge_dashboard.py is allowed to touch
Dashboard.md itself.
"""
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

VAULT_PATH = Path(__file__).resolve().parent.parent
PENDING_PATH = VAULT_PATH / "Pending_Approval"
APPROVED_PATH = VAULT_PATH / "Approved"
REJECTED_PATH = VAULT_PATH / "Rejected"
UPDATES_PATH = VAULT_PATH / "Updates"
DONE_PATH = VAULT_PATH / "Done"
FINANCIALS_PATH = DONE_PATH / "Financials"
AUDIT_LOG_PATH = VAULT_PATH / "Audit_Logs" / "audit_log.json"
NEEDS_ACTION_PATH = VAULT_PATH / "Needs_Action"
WHATSAPP_CLOUD_STATE_PATH = NEEDS_ACTION_PATH / ".whatsapp_cloud_processed_ids.json"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
DRAFT_REPLY_RE = re.compile(r"## Draft Reply\n\n(.*?)\s*$", re.DOTALL)
SOCIAL_BODY_RE = re.compile(r"# Social Draft.*?\n\n(.*)$", re.DOTALL)

# Word the operator must type in the GUI confirmation step before a live
# action fires -- same purpose as the "type SEND"/"type CONFIRM" prompts in
# send_approved_emails.py / process_approved_payments.py (added after the
# 2026-07-19 accidental-send incident documented in CLAUDE.md). whatsapp_draft
# is deliberately absent: WhatsApp stays draft/approve-only, no live send
# exists (Phase 3 scope decision).
LIVE_ACTION_CONFIRM_WORDS = {
    "email_draft": "SEND",
    "social_draft": "POST",
    "payment_request": "RECORD",
}
LIVE_ACTION_LABELS = {
    "email_draft": "Send Email",
    "social_draft": "Publish Post",
    "payment_request": "Record Payment",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_file(path: Path):
    """Returns (frontmatter_dict_or_None, frontmatter_key_order, body_text)."""
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None, [], text

    fm_block, body = match.group(1), match.group(2)
    fm, order = {}, []
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
    lines, seen = [], set()
    for key in order:
        if key in fm:
            lines.append(f"{key}: {fm[key]}")
            seen.add(key)
    for key, value in fm.items():
        if key not in seen:
            lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def summarize(path: Path) -> dict:
    """Card data for the Approval Center UI."""
    fm, _, body = parse_file(path)
    file_type = (fm or {}).get("type", "unknown")
    summary = ""
    for line in body.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            summary = stripped
            break
    if len(summary) > 220:
        summary = summary[:217] + "..."
    return {
        "name": path.name,
        "domain": path.parent.name if path.parent != PENDING_PATH else "General",
        "type": file_type,
        "summary": summary or "(empty)",
        "fields": {k: v for k, v in (fm or {}).items() if k not in ("type",)},
    }


def list_pending() -> list[dict]:
    """Root files (legacy flat, e.g. error_recovery_*.md) plus one level into
    the Sales/Support/General domain subfolders -- same scope review_approvals.py
    uses (Requirement 3a)."""
    if not PENDING_PATH.exists():
        return []
    root_files = (p for p in PENDING_PATH.iterdir() if p.is_file())
    subfolder_files = PENDING_PATH.glob("*/*")
    paths = sorted({
        p for p in list(root_files) + list(subfolder_files)
        if p.is_file() and not p.name.startswith(".")
    })
    return [summarize(p) for p in paths]


def resolve_pending(name: str) -> Path | None:
    candidates = [PENDING_PATH / name, *PENDING_PATH.glob(f"*/{name}")]
    return next((p for p in candidates if p.exists()), None)


def write_update_signal(note: str) -> None:
    """Requirement 3b: drop a small timestamped signal file into Updates/
    instead of writing Dashboard.md directly -- same contract as
    vault_server.py's write_dashboard_update() MCP tool, reimplemented here
    since the GUI process doesn't spawn the MCP stdio server."""
    UPDATES_PATH.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:6]
    target = UPDATES_PATH / f"update_{timestamp}_{suffix}.md"
    target.write_text(
        f"---\ntype: dashboard_update\ncreated: {timestamp}\nsource: web_gui\n---\n\n{note}\n",
        encoding="utf-8",
    )


def approve(path: Path) -> Path:
    APPROVED_PATH.mkdir(exist_ok=True)
    fm, order, body = parse_file(path)
    if fm is None:
        fm, order = {}, []
    for key in ("decision", "reviewed_at"):
        if key not in order:
            order.append(key)
    fm["decision"] = "approved"
    fm["reviewed_at"] = now_iso()

    dest = APPROVED_PATH / path.name
    dest.write_text(render_file(fm, order, body), encoding="utf-8")
    path.unlink()

    write_update_signal(f"{now_iso()} — APPROVED via Web GUI: `{path.name}` moved to Approved/")
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
    fm["reason"] = reason.strip() if reason and reason.strip() else "(no reason given)"

    dest = REJECTED_PATH / path.name
    dest.write_text(render_file(fm, order, body), encoding="utf-8")
    path.unlink()

    write_update_signal(f"{now_iso()} — REJECTED via Web GUI: `{path.name}` moved to Rejected/ (reason: {fm['reason']})")
    return dest


def append_audit_log(event_type: str, domain: str, details: str) -> None:
    """Mirrors vault_server.py's write_audit_log() tool exactly (same JSON
    shape/fields) -- reimplemented here since the GUI process is a plain
    FastAPI app, not an MCP client, and doesn't spawn vault_server.py."""
    AUDIT_LOG_PATH.parent.mkdir(exist_ok=True)
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event_type": event_type,
        "domain": domain,
        "details": details,
        "agent_state": "Gold-Tier-Autonomous",
    }
    logs = []
    if AUDIT_LOG_PATH.exists():
        try:
            logs = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            logs = []
    logs.append(entry)
    AUDIT_LOG_PATH.write_text(json.dumps(logs, indent=4), encoding="utf-8")


def write_error_recovery(details: str) -> Path:
    """Ralph Wiggum protocol isolation step (knowledge_base.md §4), same shape
    as vault_server.py's write_error_recovery_file() tool: after automatic
    retries are exhausted, the failed payload is isolated to
    Pending_Approval/error_recovery_[timestamp].md for HITL review instead of
    crashing the request or silently dropping the failure."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = PENDING_PATH / f"error_recovery_{timestamp}.md"
    target.write_text(
        f"---\ntype: error_recovery\ntimestamp: {timestamp}\nsource: web_gui\n---\n\n"
        f"# Ralph Wiggum Error Recovery\n\n"
        f"Automatic retries exhausted (Web GUI live-action). Payload isolated for human review.\n\n"
        f"{details}\n",
        encoding="utf-8",
    )
    return target


def _load_whatsapp_cloud_ids() -> set:
    if WHATSAPP_CLOUD_STATE_PATH.exists():
        try:
            return set(json.loads(WHATSAPP_CLOUD_STATE_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_whatsapp_cloud_ids(ids: set) -> None:
    NEEDS_ACTION_PATH.mkdir(exist_ok=True)
    WHATSAPP_CLOUD_STATE_PATH.write_text(json.dumps(sorted(ids)), encoding="utf-8")


def write_whatsapp_trigger(sender: str, body: str, msg_id: str) -> Path | None:
    """Meta WhatsApp Business Cloud API counterpart to whatsapp_watcher.py's
    write_trigger_files() -- writes the identical Needs_Action/whatsapp_<id>.md
    + TRIGGER_whatsapp_<id>.md pair (frontmatter `type: whatsapp`) so
    agent_runner.py's existing parse_whatsapp_fields()/triage_whatsapp()
    dispatch (mcp_servers/agent_runner.py, mcp_servers/vault_server.py) picks
    it up with zero changes, exactly as it already does for the Playwright
    watcher's output. Dedup is tracked separately from the Playwright
    watcher's own state file (different message-ID namespace: Cloud API IDs
    are Meta's own `wamid.*` values, not the watcher's content hash), so the
    two ingestion paths can run side by side without colliding.

    Returns None (and writes nothing) if msg_id was already processed."""
    ids = _load_whatsapp_cloud_ids()
    if msg_id in ids:
        return None
    ids.add(msg_id)
    _save_whatsapp_cloud_ids(ids)

    NEEDS_ACTION_PATH.mkdir(exist_ok=True)
    timestamp = now_iso()
    inbox_filename = f"whatsapp_{msg_id}.md"
    (NEEDS_ACTION_PATH / inbox_filename).write_text(
        f"---\ntype: whatsapp\nfrom: {sender}\n"
        f"whatsapp_message_id: {msg_id}\n"
        f"received_at: {timestamp}\nsource: cloud_api\n---\n\n"
        f"{body.strip()}\n",
        encoding="utf-8",
    )

    trigger_filename = f"TRIGGER_whatsapp_{msg_id}.md"
    trigger_path = NEEDS_ACTION_PATH / trigger_filename
    trigger_path.write_text(
        f"---\ntype: triage_trigger\noriginal_file: {inbox_filename}\n"
        f"detected_at: {timestamp}\nstatus: pending\n---\n\n"
        f"Claude, a new WhatsApp message `{inbox_filename}` has arrived from "
        f"{sender} via the WhatsApp Cloud API. Please analyze this file, "
        f"update the Dashboard, and create a plan.\n",
        encoding="utf-8",
    )
    return trigger_path


def _parse_fields(path: Path) -> dict:
    fm, _, _ = parse_file(path)
    return fm or {}


def _send_email_live(path: Path) -> tuple[bool, str]:
    sys.path.insert(0, str(VAULT_PATH))
    from gmail_sender import send_email_reply  # deferred: only import once we're actually sending

    fm = _parse_fields(path)
    text = path.read_text(encoding="utf-8")
    match = DRAFT_REPLY_RE.search(text)
    reply_body = match.group(1).strip() if match else ""
    to_addr = fm.get("original_sender", "")
    subject = fm.get("original_subject", "(no subject)")

    last_exc = None
    for attempt in range(1, 4):  # Ralph Wiggum: 3-step automatic retry on transient failure
        try:
            sent_id = send_email_reply(
                to_addr=to_addr,
                original_subject=subject,
                reply_body=reply_body,
                gmail_thread_id=fm.get("gmail_thread_id", ""),
                gmail_rfc_message_id=fm.get("gmail_rfc_message_id", ""),
            )
            break
        except Exception as e:
            last_exc = e
            if attempt < 3:
                time.sleep(1.0 * attempt)
    else:
        write_error_recovery(
            f"Live email send failed after 3 attempts.\n\n"
            f"- **File**: `{path.name}`\n- **To**: {to_addr}\n- **Subject**: {subject}\n"
            f"- **Error**: {last_exc}\n"
        )
        append_audit_log("EMAIL_SEND_FAILED", "Business", f"Failed to send `{path.name}` to {to_addr}: {last_exc}")
        return False, f"Send failed after 3 attempts ({last_exc}). Draft left in Pending_Approval/ for retry; failure isolated for review."

    DONE_PATH.mkdir(exist_ok=True)
    fm_all, order, body = parse_file(path)
    for key in ("decision", "reviewed_at", "sent", "sent_at", "gmail_sent_message_id"):
        if key not in order:
            order.append(key)
    fm_all["decision"] = "approved"
    fm_all["reviewed_at"] = now_iso()
    fm_all["sent"] = "true"
    fm_all["sent_at"] = now_iso()
    fm_all["gmail_sent_message_id"] = sent_id
    dest = DONE_PATH / path.name
    dest.write_text(render_file(fm_all, order, body), encoding="utf-8")
    path.unlink()

    append_audit_log("EMAIL_SENT", "Business", f"Sent `{path.name}` to {to_addr} (gmail id: {sent_id}), moved to Done/")
    write_update_signal(f"{now_iso()} — SENT via Web GUI: `{path.name}` emailed to {to_addr} (gmail id: {sent_id}), moved to Done/")
    return True, f"Email sent to {to_addr} (gmail id: {sent_id})."


def _post_social_live(path: Path, image_url: str = "") -> tuple[bool, str]:
    sys.path.insert(0, str(VAULT_PATH / "mcp_servers"))
    from social_server import _post_to_twitter_live, _post_to_meta_live, _post_to_linkedin_live

    fm = _parse_fields(path)
    platform = fm.get("platform", "twitter").lower()
    text = path.read_text(encoding="utf-8")
    match = SOCIAL_BODY_RE.search(text)
    content = match.group(1).strip() if match else ""
    # Instagram has no text-only post type -- an image is required. A draft
    # written autonomously may not have one baked into its frontmatter, so
    # the GUI's confirm-live form lets the operator supply/override it at
    # publish time (see web_gui/templates/confirm_live.html).
    effective_image_url = image_url or fm.get("image_url", "")

    if platform == "twitter":
        result = _post_to_twitter_live(content)
    elif platform == "linkedin":
        result = _post_to_linkedin_live(content)
    else:
        result = _post_to_meta_live(platform, content, image_url=effective_image_url)

    if result.startswith("Failure"):
        # A precondition problem (e.g. Instagram missing an image_url or
        # INSTAGRAM_BUSINESS_ACCOUNT_ID), not an API/network failure -- leave
        # the draft untouched in Pending_Approval/ for retry once fixed,
        # same convention as _record_payment_live's failure path.
        append_audit_log("SOCIAL_POST_FAILED", "Business", f"{platform} post from `{path.name}` not published: {result}")
        write_update_signal(f"{now_iso()} — POST FAILED via Web GUI: `{path.name}` ({platform}) -> {result}")
        return False, result

    # _post_to_twitter_live/_post_to_meta_live/_post_to_linkedin_live never
    # raise -- a live API failure degrades to a "Sandbox Fallback" string,
    # same graceful-fallback contract as odoo_server.py. No retry loop needed
    # here; just record it.
    ok = "Fallback" not in result
    DONE_PATH.mkdir(exist_ok=True)
    fm_all, order, body = parse_file(path)
    for key in ("decision", "reviewed_at", "posted", "posted_at", "post_result"):
        if key not in order:
            order.append(key)
    fm_all["decision"] = "approved"
    fm_all["reviewed_at"] = now_iso()
    fm_all["posted"] = "true" if ok else "false"
    fm_all["posted_at"] = now_iso()
    fm_all["post_result"] = f"\"{result}\""
    dest = DONE_PATH / path.name
    dest.write_text(render_file(fm_all, order, body), encoding="utf-8")
    path.unlink()

    event = "SOCIAL_POSTED" if ok else "SOCIAL_POST_FALLBACK"
    append_audit_log(event, "Business", f"{platform} post from `{path.name}`: {result}")
    write_update_signal(f"{now_iso()} — {'POSTED' if ok else 'POST FALLBACK'} via Web GUI: `{path.name}` ({platform}) -> {result}")
    return True, result


def _record_payment_live(path: Path) -> tuple[bool, str]:
    sys.path.insert(0, str(VAULT_PATH / "mcp_servers"))
    from odoo_server import record_payment
    from secure_vault import encrypt_file

    fm = _parse_fields(path)
    invoice_id = fm.get("invoice_id", "")
    amount = fm.get("amount", "0")
    method = fm.get("method", "")

    result = record_payment(invoice_id=invoice_id, amount=float(amount), method=method)
    short_result = result if len(result) <= 140 else result[:137] + "..."

    if result.startswith("Failure") or result.startswith("EXECUTION_ZONE=cloud"):
        append_audit_log("PAYMENT_FAILED", "Business", f"`{path.name}` against invoice {invoice_id} not recorded: {short_result}")
        write_update_signal(f"{now_iso()} — PAYMENT FAILED via Web GUI: `{path.name}` against invoice {invoice_id} not recorded ({short_result})")
        return False, f"Payment not recorded: {result}. Draft left in Pending_Approval/ for retry."

    FINANCIALS_PATH.mkdir(parents=True, exist_ok=True)
    fm_all, order, body = parse_file(path)
    for key in ("decision", "reviewed_at", "recorded", "recorded_at", "payment_result"):
        if key not in order:
            order.append(key)
    fm_all["decision"] = "approved"
    fm_all["reviewed_at"] = now_iso()
    fm_all["recorded"] = "true"
    fm_all["recorded_at"] = now_iso()
    fm_all["payment_result"] = f"\"{short_result}\""
    dest = FINANCIALS_PATH / path.name
    dest.write_text(render_file(fm_all, order, body), encoding="utf-8")
    path.unlink()
    encrypt_file(dest)
    enc_dest = dest.with_suffix(".enc")

    append_audit_log("PAYMENT_RECORDED", "Business", f"`{path.name}` against invoice {invoice_id} ({amount} via {method}), encrypted to Done/Financials/{enc_dest.name}")
    write_update_signal(f"{now_iso()} — PAYMENT RECORDED via Web GUI: `{path.name}` against invoice {invoice_id} ({amount} via {method}), encrypted -> Done/Financials/{enc_dest.name}")
    return True, f"Payment recorded and encrypted -> Done/Financials/{enc_dest.name}"


LIVE_ACTION_HANDLERS = {
    "email_draft": _send_email_live,
    "social_draft": _post_social_live,
    "payment_request": _record_payment_live,
}


def _placeholder(value: str, *markers: str) -> bool:
    return not value or any(m in value for m in markers)


def get_channel_status() -> list[dict]:
    """Live/Sandbox/Not-configured status per outbound channel, for the Home
    dashboard's channel grid (Phase 7). Deliberately just reads .env values
    and file existence -- never imports mcp_servers/* (same reasoning as
    get_system_health()'s Odoo probe: a status widget must never be able to
    break because an MCP-related package is missing/mismatched)."""
    env = os.environ
    channels = []

    fb_token = env.get("META_PAGE_ACCESS_TOKEN", "")
    fb_live = not _placeholder(fb_token, "your-page")
    channels.append({"name": "Facebook", "icon": "📘", "status": "Live" if fb_live else "Sandbox"})

    ig_id = env.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
    ig_live = fb_live and not _placeholder(ig_id, "your-instagram")
    channels.append({"name": "Instagram", "icon": "📸", "status": "Live" if ig_live else "Sandbox"})

    tw_creds = [env.get(k, "") for k in ("TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET")]
    tw_live = not any(_placeholder(v, "your-api", "your-access") for v in tw_creds)
    channels.append({"name": "Twitter / X", "icon": "🐦", "status": "Live" if tw_live else "Sandbox"})

    li_token = env.get("LINKEDIN_ACCESS_TOKEN", "")
    li_urn = env.get("LINKEDIN_PERSON_URN", "")
    li_live = not _placeholder(li_token, "your-linkedin") and not _placeholder(li_urn, "your-linkedin", "urn:li:person:your")
    channels.append({"name": "LinkedIn", "icon": "💼", "status": "Live" if li_live else "Sandbox"})

    gmail_ready = (VAULT_PATH / "token.json").exists() and (VAULT_PATH / "credentials.json").exists()
    channels.append({"name": "Gmail", "icon": "📧", "status": "Live" if gmail_ready else "Not configured"})

    wa_token = env.get("WHATSAPP_CLOUD_API_TOKEN", "")
    wa_phone_id = env.get("WHATSAPP_PHONE_NUMBER_ID", "")
    wa_cloud_ready = not _placeholder(wa_token, "your-whatsapp") and not _placeholder(wa_phone_id, "your-phone")
    channels.append({
        "name": "WhatsApp",
        "icon": "💬",
        "status": "Cloud API" if wa_cloud_ready else "Draft-only",
    })

    return channels


DRAFT_TYPES = {"email_draft", "whatsapp_draft", "social_draft", "payment_request"}
MINUTES_SAVED_PER_ITEM = 15


def get_roi_metrics() -> dict:
    """Business ROI/impact estimates for the Home dashboard (Task 3).
    Scans every folder a triage draft can currently sit in (still pending,
    approved-but-not-yet-executed, rejected, or terminal Done/) so a draft is
    counted exactly once across its whole lifecycle, regardless of which
    state it's in right now."""
    folders = [PENDING_PATH, APPROVED_PATH, REJECTED_PATH, DONE_PATH, FINANCIALS_PATH]
    inquiries_processed = 0
    total_payment_value = 0.0
    for folder in folders:
        if not folder.exists():
            continue
        for path in folder.rglob("*.md"):
            fm, _, _ = parse_file(path)
            if not fm:
                continue
            file_type = fm.get("type", "")
            if file_type not in DRAFT_TYPES:
                continue
            inquiries_processed += 1
            if file_type == "payment_request":
                try:
                    total_payment_value += float(fm.get("amount", "0"))
                except ValueError:
                    pass

    logs = []
    if AUDIT_LOG_PATH.exists():
        try:
            logs = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            logs = []
    total_events = len(logs)
    failed_events = sum(1 for e in logs if "FAILED" in e.get("event_type", ""))
    success_rate_pct = round((total_events - failed_events) / total_events * 100, 1) if total_events else 0.0

    return {
        "hours_saved": round(inquiries_processed * MINUTES_SAVED_PER_ITEM / 60, 1),
        "inquiries_processed": inquiries_processed,
        "total_payment_value": round(total_payment_value, 2),
        "success_rate_pct": success_rate_pct,
    }


def get_dashboard_overview() -> dict:
    """Aggregate view for the Home page (Phase 7): pending/approved/rejected
    counts, a breakdown of all-time audit log activity by event type, the
    last few activity entries, live system health, and per-channel status."""
    logs = []
    if AUDIT_LOG_PATH.exists():
        try:
            logs = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            logs = []

    event_counts: dict[str, int] = {}
    for entry in logs:
        et = entry.get("event_type", "UNKNOWN")
        event_counts[et] = event_counts.get(et, 0) + 1

    def _count(folder: Path) -> int:
        return len(list(folder.rglob("*.md"))) if folder.exists() else 0

    return {
        "pending_count": len(list_pending()),
        "approved_count": _count(APPROVED_PATH),
        "rejected_count": _count(REJECTED_PATH),
        "done_count": _count(DONE_PATH),
        "total_actions_logged": len(logs),
        "event_counts": event_counts,
        "recent_activity": list(reversed(logs[-8:])),
        "health": get_system_health(),
        "channels": get_channel_status(),
        "roi": get_roi_metrics(),
    }


def get_recent_audit_entries(limit: int = 20) -> list[dict]:
    """Last `limit` entries from Audit_Logs/audit_log.json, newest first, for
    the System Health & Audit Logs tab (Phase 4)."""
    if not AUDIT_LOG_PATH.exists():
        return []
    try:
        logs = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(reversed(logs[-limit:]))


def get_system_health() -> dict:
    """Live system metrics for the Health tab: EXECUTION_ZONE, a lightweight
    Odoo reachability probe, and a count of triggers currently mid-pipeline.
    Deliberately calls the Odoo JSON-2 API directly with `requests` here
    instead of importing mcp_servers/odoo_server.py -- that module imports
    `mcp.server.fastmcp` at module load time, which this page should not be
    able to break just because an MCP-related package is missing/mismatched
    in a given environment (see the Phase 3 CLAUDE.md note on the venv's
    mcp==2.0.0/fastmcp gap). A read-only health probe should degrade to
    'Unreachable', never take the whole page down."""
    import os as _os

    zone = _os.environ.get("EXECUTION_ZONE", "").strip().lower() or "(unset)"

    odoo_url = _os.environ.get("ODOO_URL", "")
    odoo_key = _os.environ.get("ODOO_API_KEY", "")
    odoo_db = _os.environ.get("ODOO_DB", "")
    if not odoo_url or not odoo_key:
        odoo_status = "Not configured (sandbox/mock mode)"
    else:
        try:
            import requests
            headers = {"Authorization": f"bearer {odoo_key}", "Content-Type": "application/json"}
            if odoo_db:
                headers["X-Odoo-Database"] = odoo_db
            url = f"{odoo_url.rstrip('/')}/json/2/res.partner/search_count"
            resp = requests.post(url, headers=headers, json={"domain": []}, timeout=5)
            odoo_status = "Connected" if resp.ok else f"Unreachable (HTTP {resp.status_code})"
        except Exception as e:
            odoo_status = f"Unreachable ({type(e).__name__})"

    needs_action = VAULT_PATH / "Needs_Action"
    active = list(needs_action.glob("TRIGGER_*.md")) if needs_action.exists() else []
    in_progress = VAULT_PATH / "In_Progress"
    if in_progress.exists():
        active += list(in_progress.glob("*/TRIGGER_*.md"))

    return {
        "execution_zone": zone,
        "odoo_status": odoo_status,
        "active_triggers": len(active),
        "pending_approvals": len(list_pending()),
    }


def execute_live_action(path: Path, image_url: str = "") -> tuple[bool, str]:
    """Dispatches an already-approved (typed-confirmation) draft to its live
    channel sender. Every branch either moves the file to a terminal Done/
    location on success, or leaves it untouched in Pending_Approval/ on
    failure (never partially mutates), and always writes an audit log entry
    -- per the Live Channel Dispatcher skill's Safety & Execution Boundaries.

    image_url is only meaningful for social_draft/Instagram (see
    _post_social_live) -- ignored for every other type.

    Defensive EXECUTION_ZONE check (found missing in code review, 2026-08-04):
    web_gui/app.py's confirm-live route already checks is_local_zone() before
    ever calling this, but the handlers this dispatches to
    (gmail_sender.send_email_reply, social_server's _post_to_*_live) have no
    gate of their own -- unlike odoo_server.record_payment, which
    self-checks independent of its caller. Without this, a future caller of
    execute_live_action() (a new route, a script, a test harness) could
    silently bypass the Cloud/Local boundary this vault otherwise treats as
    needing redundant, fail-safe defaults everywhere else."""
    if os.environ.get("EXECUTION_ZONE", "").strip().lower() != "local":
        return False, "EXECUTION_ZONE is not 'local' -- live actions are only permitted on the Local zone machine."
    fm = _parse_fields(path)
    file_type = fm.get("type", "")
    handler = LIVE_ACTION_HANDLERS.get(file_type)
    if handler is None:
        return False, f"No live-send action exists for type '{file_type}'."
    if file_type == "social_draft":
        return handler(path, image_url=image_url)
    return handler(path)
