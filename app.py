#!/usr/bin/env python3
"""Web GUI for the AI Employee Vault -- HITL Approval Center + Setup Wizard.

Replaces the manual CLI (review_approvals.py, hand-editing .env) with a
browser UI for non-technical users, per .claude/skills/gui_bridge_manager/SKILL.md.

Run:
    python3 web_gui/app.py
    # or: uvicorn web_gui.app:app --host 127.0.0.1 --port 8899

Security notes (read before exposing this beyond localhost):
  - Binds to 127.0.0.1 by default (WEB_GUI_HOST env var to override).
  - If WEB_GUI_USERNAME/WEB_GUI_PASSWORD are set in .env, every route requires
    HTTP Basic Auth. If unset, auth is skipped -- acceptable only because the
    default bind is localhost-only; do NOT set WEB_GUI_HOST=0.0.0.0 without
    also setting credentials.
  - Approve/Reject actions are gated the same way every other live-action
    script in this repo is gated (send_approved_emails.py,
    process_approved_payments.py): they refuse to execute unless
    EXECUTION_ZONE=local, per the Known Constraints rule in CLAUDE.md that
    real approvals must happen on the Local machine, not the Cloud VM. The
    GUI still renders on the Cloud zone (read-only) so a human can see what's
    pending without exposing the write path there.
  - The Setup Wizard never sends real secret values to the browser -- only a
    masked last-4-chars preview (see env_ops.py). Submitted fields are only
    applied if non-empty, so leaving a field blank never wipes a key.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

VAULT_PATH = Path(__file__).resolve().parent.parent
load_dotenv(VAULT_PATH / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(VAULT_PATH))
import vault_ops
import env_ops
import kb_ops
from whatsapp_notify import send_whatsapp_message  # noqa: E402 -- lives at VAULT_PATH root, not web_gui/

app = FastAPI(title="AI Employee Vault -- Control Center")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

security = HTTPBasic(auto_error=False)

# --- Phase 4: session-based admin login -------------------------------------
# Separate from (and in addition to) the pre-existing WEB_GUI_USERNAME/
# WEB_GUI_PASSWORD HTTP Basic layer below, which stays as-is for programmatic/
# API-style access. This is the primary gate for the browser UI: every human
# must log in with WEB_ADMIN_PASSWORD before seeing Pending_Approval/ contents,
# the Setup Wizard, or firing a live send/post/payment.
#
# WEB_SESSION_SECRET signs the session cookie (via itsdangerous, same as any
# Starlette app) -- generated once and persisted to .env on first run, same
# auto-generate-on-first-use pattern secure_vault.py already uses for
# .vault_key, so sessions survive a server restart instead of invalidating
# every logged-in browser each time the process restarts.
WEB_SESSION_SECRET = os.environ.get("WEB_SESSION_SECRET", "")
if not WEB_SESSION_SECRET:
    WEB_SESSION_SECRET = secrets.token_hex(32)
    env_ops.update_env({"WEB_SESSION_SECRET": WEB_SESSION_SECRET})
    os.environ["WEB_SESSION_SECRET"] = WEB_SESSION_SECRET

# Demo/first-run-only default -- see the startup warning below and the
# Setup Wizard, which should be used to set a real WEB_ADMIN_PASSWORD before
# this instance is ever exposed beyond localhost.
DEFAULT_ADMIN_PASSWORD = "changeme-admin"
LOGIN_THROTTLE_SECONDS = 0.6  # crude brute-force slow-down; no rate-limit dependency added for this

# --- Login lockout (2026-08 production-readiness fix) -----------------------
# LOGIN_THROTTLE_SECONDS alone was self-documented as "crude" -- at ~1.6
# attempts/sec an attacker can still run tens of thousands of guesses within a
# few hours against a weak WEB_ADMIN_PASSWORD, with no lockout at all. This
# adds a simple in-memory per-IP lockout on top of the existing throttle: after
# LOGIN_MAX_ATTEMPTS consecutive failures from the same IP, that IP is locked
# out for LOGIN_LOCKOUT_SECONDS. In-memory (not persisted) is a deliberate,
# acceptable tradeoff here -- this process is single-worker (see uvicorn.run()
# at the bottom of this file, no `workers=` argument), so there's exactly one
# process holding this state, and a restart clearing it is a minor inconvenience,
# not a security hole (the throttle+lockout resets, it doesn't disable).
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300  # 5 minutes
_login_failure_state: dict[str, dict] = {}  # ip -> {"count": int, "locked_until": float}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _login_lockout_remaining(ip: str) -> float:
    """Seconds remaining in an active lockout for this IP, or 0.0 if none."""
    state = _login_failure_state.get(ip)
    if not state:
        return 0.0
    return max(0.0, state.get("locked_until", 0.0) - time.time())


def _record_login_failure(ip: str) -> None:
    state = _login_failure_state.setdefault(ip, {"count": 0, "locked_until": 0.0})
    state["count"] += 1
    if state["count"] >= LOGIN_MAX_ATTEMPTS:
        state["locked_until"] = time.time() + LOGIN_LOCKOUT_SECONDS
        state["count"] = 0


def _record_login_success(ip: str) -> None:
    _login_failure_state.pop(ip, None)


app.add_middleware(
    SessionMiddleware,
    secret_key=WEB_SESSION_SECRET,
    session_cookie="vault_admin_session",
    same_site="lax",
    max_age=8 * 60 * 60,  # 8 hours
)


def get_admin_password() -> str:
    return os.environ.get("WEB_ADMIN_PASSWORD", "") or DEFAULT_ADMIN_PASSWORD


def using_default_admin_password() -> bool:
    return not os.environ.get("WEB_ADMIN_PASSWORD", "").strip()


class NotAuthenticated(Exception):
    pass


@app.exception_handler(NotAuthenticated)
def _not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url=f"/login?next={quote(request.url.path)}", status_code=303)


def require_login(request: Request):
    if not request.session.get("authenticated"):
        raise NotAuthenticated()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    expected_user = os.environ.get("WEB_GUI_USERNAME", "")
    expected_pass = os.environ.get("WEB_GUI_PASSWORD", "")
    if not expected_user or not expected_pass:
        return  # No credentials configured -- localhost-only bind is the guard.
    if not credentials or not (
        secrets.compare_digest(credentials.username, expected_user)
        and secrets.compare_digest(credentials.password, expected_pass)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def is_local_zone() -> bool:
    return os.environ.get("EXECUTION_ZONE", "").strip().lower() == "local"


# Phase 7: white-label branding. BRAND_NAME is a plain .env value (editable
# via the Setup Wizard, same generic mechanism every other .env key already
# uses -- no env_ops.py change needed) so a client's own name/product can be
# shown instead of a hardcoded "AI Employee Vault" -- this GUI is meant to be
# resold per the saas_commercial_suite skill, and different clients want
# different names on the page they and their team actually look at.
DEFAULT_BRAND_NAME = "AI Employee"


def get_brand_name() -> str:
    return os.environ.get("BRAND_NAME", "").strip() or DEFAULT_BRAND_NAME


def render(request: Request, template_name: str, context: dict | None = None):
    """Thin TemplateResponse wrapper that injects brand_name/csrf_token into
    every page automatically, so individual routes don't each have to
    remember to."""
    ctx = dict(context or {})
    ctx.setdefault("brand_name", get_brand_name())
    ctx.setdefault("csrf_token", get_csrf_token(request))
    return templates.TemplateResponse(request, template_name, ctx)


# --- Task 4: CSRF hardening --------------------------------------------------
# Synchronizer-token pattern: a random per-session token, stored server-side
# in request.session (already tamper-proof -- SessionMiddleware signs the
# cookie via itsdangerous) and echoed back as a hidden form field. Every
# state-changing POST route below validates it before doing anything else.
def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_hex(16)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, submitted_token: str) -> bool:
    expected = request.session.get("csrf_token", "")
    return bool(expected) and secrets.compare_digest(submitted_token or "", expected)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: str = "/approvals", error: str = ""):
    if request.session.get("authenticated"):
        return RedirectResponse(url=next if next.startswith("/") and not next.startswith("//") else "/approvals")
    locked_seconds = round(_login_lockout_remaining(_client_ip(request)))
    return render(
        request,
        "login.html",
        {
            "next": next,
            "error": "locked" if (error != "locked" and locked_seconds > 0) else error,
            "locked_seconds": locked_seconds,
            "using_default": using_default_admin_password(),
        },
    )


@app.post("/login", include_in_schema=False)
def login_submit(request: Request, password: str = Form(...), next: str = Form("/approvals")):
    time.sleep(LOGIN_THROTTLE_SECONDS)
    ip = _client_ip(request)
    safe_next = next if next.startswith("/") and not next.startswith("//") else "/approvals"

    remaining = _login_lockout_remaining(ip)
    if remaining > 0:
        return RedirectResponse(url=f"/login?error=locked&next={quote(safe_next)}", status_code=303)

    if secrets.compare_digest(password, get_admin_password()):
        _record_login_success(ip)
        request.session["authenticated"] = True
        return RedirectResponse(url=safe_next, status_code=303)

    _record_login_failure(ip)
    return RedirectResponse(url=f"/login?error=1&next={quote(safe_next)}", status_code=303)


@app.post("/logout", include_in_schema=False)
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/setup", include_in_schema=False)
def setup_alias():
    """Alias -- the Setup Wizard route is /settings; kept as its own name
    below since it predates Phase 4, but /setup is what the Phase 4 spec and
    nav copy call it."""
    return RedirectResponse(url="/settings")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root(request: Request, _login=Depends(require_login), _auth=Depends(require_auth)):
    return render(request, "dashboard.html", vault_ops.get_dashboard_overview() | {"local_zone": is_local_zone()})


@app.get("/approvals", response_class=HTMLResponse)
def approvals_page(request: Request, flash: str = "", flash_kind: str = "info", _login=Depends(require_login), _auth=Depends(require_auth)):
    return render(
        request,
        "approvals.html",
        {
            "items": vault_ops.list_pending(),
            "local_zone": is_local_zone(),
            "zone": os.environ.get("EXECUTION_ZONE", "(unset)"),
            "confirm_words": vault_ops.LIVE_ACTION_CONFIRM_WORDS,
            "action_labels": vault_ops.LIVE_ACTION_LABELS,
            "flash": flash,
            "flash_kind": flash_kind,
        },
    )


@app.post("/approvals/{filename}/approve")
def approve_item(filename: str, request: Request, csrf_token: str = Form(""), _login=Depends(require_login), _auth=Depends(require_auth)):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse(
            url=f"/approvals?flash={quote('Security token expired or invalid — please retry.')}&flash_kind=error",
            status_code=303,
        )
    if not is_local_zone():
        raise HTTPException(
            status_code=403,
            detail="Approvals must be performed from the Local zone (EXECUTION_ZONE=local). "
                   "This Cloud-zone instance is read-only, per CLAUDE.md Known Constraints.",
        )
    path = vault_ops.resolve_pending(filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"{filename} not found in Pending_Approval/")
    vault_ops.approve(path)
    return RedirectResponse(url="/approvals", status_code=303)


@app.post("/approvals/{filename}/reject")
def reject_item(filename: str, request: Request, reason: str = Form(""), csrf_token: str = Form(""), _login=Depends(require_login), _auth=Depends(require_auth)):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse(
            url=f"/approvals?flash={quote('Security token expired or invalid — please retry.')}&flash_kind=error",
            status_code=303,
        )
    if not is_local_zone():
        raise HTTPException(
            status_code=403,
            detail="Rejections must be performed from the Local zone (EXECUTION_ZONE=local). "
                   "This Cloud-zone instance is read-only, per CLAUDE.md Known Constraints.",
        )
    path = vault_ops.resolve_pending(filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"{filename} not found in Pending_Approval/")
    vault_ops.reject(path, reason)
    return RedirectResponse(url="/approvals", status_code=303)


@app.get("/approvals/{filename}/confirm-live", response_class=HTMLResponse)
def confirm_live_page(filename: str, request: Request, _login=Depends(require_login), _auth=Depends(require_auth)):
    """Step 1 of the live-send flow: shows the draft again and requires the
    operator to type a channel-specific confirmation word before anything
    fires. Mirrors the "type SEND"/"type CONFIRM" interactive prompt in
    send_approved_emails.py/process_approved_payments.py -- a single click is
    not enough for a live send/post/payment, per the Phase 3 scope decision
    (this vault has a real prior incident from a live-action safeguard being
    too weak, see CLAUDE.md Incidents)."""
    if not is_local_zone():
        raise HTTPException(status_code=403, detail="Live actions require EXECUTION_ZONE=local.")
    path = vault_ops.resolve_pending(filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"{filename} not found in Pending_Approval/")
    item = vault_ops.summarize(path)
    confirm_word = vault_ops.LIVE_ACTION_CONFIRM_WORDS.get(item["type"])
    if confirm_word is None:
        raise HTTPException(status_code=400, detail=f"No live-send action exists for type '{item['type']}'.")
    return render(
        request,
        "confirm_live.html",
        {
            "item": item,
            "confirm_word": confirm_word,
            "action_label": vault_ops.LIVE_ACTION_LABELS.get(item["type"], "Execute"),
            "needs_image_url": item["type"] == "social_draft",
        },
    )


@app.post("/approvals/{filename}/confirm-live")
def confirm_live_execute(filename: str, request: Request, confirm_text: str = Form(...), image_url: str = Form(""), csrf_token: str = Form(""), _login=Depends(require_login), _auth=Depends(require_auth)):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse(
            url=f"/approvals?flash={quote('Security token expired or invalid — please retry.')}&flash_kind=error",
            status_code=303,
        )
    if not is_local_zone():
        raise HTTPException(status_code=403, detail="Live actions require EXECUTION_ZONE=local.")
    path = vault_ops.resolve_pending(filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"{filename} not found in Pending_Approval/")
    item = vault_ops.summarize(path)
    expected = vault_ops.LIVE_ACTION_CONFIRM_WORDS.get(item["type"])
    if expected is None:
        raise HTTPException(status_code=400, detail=f"No live-send action exists for type '{item['type']}'.")
    if confirm_text.strip() != expected:
        return RedirectResponse(
            url=f"/approvals?flash={quote('Confirmation word did not match — nothing was sent.')}&flash_kind=error",
            status_code=303,
        )

    ok, message = vault_ops.execute_live_action(path, image_url=image_url.strip())
    kind = "success" if ok else "error"
    return RedirectResponse(url=f"/approvals?flash={quote(message)}&flash_kind={kind}", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", error: str = "", _login=Depends(require_login), _auth=Depends(require_auth)):
    return render(
        request,
        "settings.html",
        {"rows": env_ops.read_env_masked(), "saved": saved, "error": error},
    )


@app.post("/settings")
async def settings_save(request: Request, _login=Depends(require_login), _auth=Depends(require_auth)):
    form = await request.form()
    form_dict = dict(form)
    if not verify_csrf(request, form_dict.pop("csrf_token", "")):
        return RedirectResponse(
            url=f"/settings?error={quote('Security token expired or invalid — please retry.')}",
            status_code=303,
        )
    changed = env_ops.update_env(form_dict)
    return RedirectResponse(url=f"/settings?saved={','.join(changed)}", status_code=303)


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request, saved: str = "", error: str = "", _login=Depends(require_login), _auth=Depends(require_auth)):
    return render(request, "rules.html", {**kb_ops.read_kb_rules(), "saved": saved, "error": error})


@app.post("/rules/save")
def rules_save(
    request: Request,
    discount_ceiling: str = Form(...),
    escalation_domains: str = Form(""),
    operating_tone: str = Form(""),
    operating_hours: str = Form(""),
    confirm_text: str = Form(""),
    csrf_token: str = Form(""),
    _login=Depends(require_login),
    _auth=Depends(require_auth),
):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse(
            url=f"/rules?error={quote('Security token expired or invalid — please retry.')}",
            status_code=303,
        )
    # This controls every future deal's auto-approve/escalate outcome, so it
    # gets the same typed-confirmation + audit-log treatment as a live
    # send/post/pay action, not just a plain form submit.
    if confirm_text.strip() != "CONFIRM":
        return RedirectResponse(
            url=f"/rules?error={quote('Type CONFIRM exactly to change business rules.')}",
            status_code=303,
        )
    try:
        old_ceiling = kb_ops.get_discount_ceiling()
        ceiling = int(discount_ceiling)
        kb_ops.update_kb_rules(ceiling, escalation_domains, operating_tone, operating_hours)
    except ValueError as e:
        return RedirectResponse(url=f"/rules?error={quote(str(e))}", status_code=303)
    vault_ops.append_audit_log(
        "BUSINESS_RULE_CHANGE",
        "Business",
        f"Discount ceiling changed {old_ceiling}% -> {ceiling}% via Web GUI Business Rules page "
        f"(escalation_domains={escalation_domains!r}, operating_tone={operating_tone!r}, operating_hours={operating_hours!r})",
    )
    return RedirectResponse(url="/rules?saved=1", status_code=303)


@app.get("/health", response_class=HTMLResponse)
def health_page(request: Request, _login=Depends(require_login), _auth=Depends(require_auth)):
    return render(
        request,
        "health.html",
        {
            "metrics": vault_ops.get_system_health(),
            "audit_entries": vault_ops.get_recent_audit_entries(20),
            "local_zone": is_local_zone(),
        },
    )


# --- Task 1: WhatsApp Business Cloud API webhook -----------------------------
# Deliberately NOT behind require_login/require_auth -- Meta's own servers call
# these directly (no browser session exists), same reasoning /login is exempt.
# The GET verification handshake is protected by WHATSAPP_VERIFY_TOKEN (Meta's
# own webhook-setup convention). The POST endpoint is protected by an
# X-Hub-Signature-256 HMAC check (2026-08 production-readiness fix -- this was
# previously a known, documented gap: anyone who knew/guessed this URL could
# inject arbitrary draft-triage trigger files into Needs_Action/ with no
# signature check at all). This only ever *writes draft-triage trigger files*
# (identical to whatsapp_watcher.py's own output) -- it never sends/executes
# anything live, so the blast radius was always limited to spam/noise, not a
# live-action bypass, but it's worth closing now that this is meant to run
# unattended for a real client.
@app.get("/webhooks/whatsapp", include_in_schema=False)
def whatsapp_webhook_verify(request: Request):
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and secrets.compare_digest(token, expected):
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Verification token mismatch.")


def _whatsapp_signature_valid(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    """Verifies Meta's X-Hub-Signature-256 header: 'sha256=<hex hmac>' computed
    over the exact raw request body bytes using WHATSAPP_APP_SECRET as the HMAC
    key (Meta's documented webhook payload-signing scheme)."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    provided = signature_header.split("=", 1)[1].strip()
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


WHATSAPP_APPROVAL_CODES_PATH = VAULT_PATH / "Needs_Action" / ".whatsapp_approval_codes.json"


def _normalize_phone(num: str) -> str:
    """Digits-only comparison so '+92 300 1234567', '923001234567', and
    Meta's own 'from' format (already digits-only, no +) all match."""
    return "".join(ch for ch in (num or "") if ch.isdigit())


def _load_approval_codes() -> dict:
    """Reads the same state file pending_approval_notifier.py writes
    (code -> pending filename). Read fresh on every webhook call since the
    notifier is a separate process updating this file independently."""
    if WHATSAPP_APPROVAL_CODES_PATH.exists():
        try:
            return json.loads(WHATSAPP_APPROVAL_CODES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _consume_approval_code(code: str) -> None:
    """Removes a used code so the same WhatsApp reply can't be replayed to
    approve/reject twice."""
    state = _load_approval_codes()
    filename = state.get("codes", {}).pop(code, None)
    if filename:
        state.get("notified", {}).pop(filename, None)
        WHATSAPP_APPROVAL_CODES_PATH.parent.mkdir(exist_ok=True)
        WHATSAPP_APPROVAL_CODES_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _handle_whatsapp_approval_reply(sender: str, body: str) -> bool:
    """WhatsApp-based approval (client-requested feature, 2026-08-19).

    Returns True if this inbound message was handled as an approver
    command (and should NOT also be filed as a new customer inquiry).

    Authorization: the sender's number must exactly match
    WHATSAPP_APPROVER_NUMBER (digits-only compare). Combined with the
    X-Hub-Signature-256 check above (when WHATSAPP_APP_SECRET is set), a
    message that reaches this point is a cryptographically verified Meta
    webhook delivery from that specific WhatsApp number -- this is treated
    as an equivalent trust level to a Local-zone browser click, and is a
    DELIBERATE, NARROW exception to the EXECUTION_ZONE=local gate the
    /approvals browser routes enforce (see CLAUDE.md Known Constraints and
    the 2026-07-19 accidental-send incident before loosening this further).

    Scope is intentionally limited to the "soft" approve/reject (moves the
    file to Approved/Rejected, same as the dashboard's plain Approve/Reject
    buttons) -- it does NOT trigger the two-step "confirm-live" flow
    (typed-word-gated live email send / social post / payment), which still
    requires the Local-zone dashboard. So a WhatsApp APPROVE stages an item
    for sending; it does not, by itself, ever send/post/pay anything live.
    """
    approver = _normalize_phone(os.environ.get("WHATSAPP_APPROVER_NUMBER", ""))
    if not approver or _normalize_phone(sender) != approver:
        return False

    text = (body or "").strip()
    upper = text.upper()
    if upper.startswith("APPROVE"):
        action = "approve"
        rest = text[len("APPROVE"):].strip()
        code = rest.split()[0].upper() if rest else ""
        reason = ""
    elif upper.startswith("REJECT"):
        action = "reject"
        rest = text[len("REJECT"):].strip()
        parts = rest.split(None, 1)
        code = parts[0].upper() if parts else ""
        reason = parts[1] if len(parts) > 1 else ""
    else:
        # Not a recognized command from the approver -- ignore silently
        # (e.g. the approver chatting normally on their own WhatsApp number).
        return True

    if not code:
        send_whatsapp_message(sender, "Couldn't find a reference code. Format: APPROVE ABC123 or REJECT ABC123 <reason>")
        return True

    state = _load_approval_codes()
    filename = state.get("codes", {}).get(code)
    if not filename:
        send_whatsapp_message(sender, f"Code {code} not found or already handled.")
        return True

    path = vault_ops.resolve_pending(filename)
    if path is None:
        send_whatsapp_message(sender, f"'{filename}' is no longer pending (already handled elsewhere).")
        _consume_approval_code(code)
        return True

    if action == "approve":
        vault_ops.approve(path)
        vault_ops.append_audit_log("APPROVAL_VIA_WHATSAPP", "approvals", f"{filename} approved via WhatsApp reply from {sender}")
        send_whatsapp_message(sender, f"Approved: {filename}\n(Staged -- live send/post/payment still needs the Local-zone dashboard confirmation step.)")
    else:
        vault_ops.reject(path, reason)
        vault_ops.append_audit_log("REJECTION_VIA_WHATSAPP", "approvals", f"{filename} rejected via WhatsApp reply from {sender} (reason: {reason or '(none)'})")
        send_whatsapp_message(sender, f"Rejected: {filename}")

    _consume_approval_code(code)
    return True


@app.post("/webhooks/whatsapp", include_in_schema=False)
async def whatsapp_webhook_receive(request: Request):
    raw_body = await request.body()

    app_secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    if app_secret:
        signature_header = request.headers.get("x-hub-signature-256", "")
        if not _whatsapp_signature_valid(raw_body, signature_header, app_secret):
            logging.warning("WhatsApp webhook: X-Hub-Signature-256 verification failed -- payload rejected.")
            raise HTTPException(status_code=403, detail="Invalid signature.")
    else:
        # Fail-open only because WHATSAPP_APP_SECRET hasn't been configured yet
        # (backward-compatible with an existing webhook setup that predates this
        # fix) -- logged loudly so it's visible in the Health tab / journal, and
        # the Setup Wizard should be used to set WHATSAPP_APP_SECRET as soon as
        # possible. Once set, verification above is enforced unconditionally.
        logging.warning(
            "WHATSAPP_APP_SECRET is not set -- webhook payload signature is NOT being "
            "verified. Set WHATSAPP_APP_SECRET in .env (Meta App Dashboard -> App "
            "Settings -> Basic -> App Secret) to close this gap."
        )

    try:
        payload = json.loads(raw_body)
    except Exception:
        return JSONResponse({"status": "ignored", "reason": "invalid JSON"}, status_code=200)

    created = 0
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    msg_id = msg.get("id", "")
                    sender = msg.get("from", "")
                    body = (msg.get("text", {}) or {}).get("body", "")
                    if not msg_id or not sender:
                        continue
                    if _handle_whatsapp_approval_reply(sender, body):
                        continue
                    if vault_ops.write_whatsapp_trigger(sender, body, msg_id):
                        created += 1
    except Exception as e:
        # Meta retries on non-2xx and can retry-storm -- always ack 200 even on
        # a malformed/unexpected payload shape, just log it for investigation.
        logging.error(f"WhatsApp webhook payload processing error: {e}")

    return JSONResponse({"status": "ok", "triggers_created": created})


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("WEB_GUI_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_GUI_PORT", "8899"))

    if using_default_admin_password():
        print(
            f"WARNING: WEB_ADMIN_PASSWORD is not set in .env -- using the built-in "
            f"demo default ({DEFAULT_ADMIN_PASSWORD!r}). Set WEB_ADMIN_PASSWORD in "
            f".env (Setup Wizard or hand-edit) before exposing this GUI to anyone "
            f"but yourself on localhost.",
            file=sys.stderr,
        )

    if host not in ("127.0.0.1", "localhost"):
        if using_default_admin_password():
            print(
                "Refusing to bind to a non-localhost host while WEB_ADMIN_PASSWORD "
                "is still the built-in demo default -- set a real WEB_ADMIN_PASSWORD "
                "in .env first.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not (os.environ.get("WEB_GUI_USERNAME") and os.environ.get("WEB_GUI_PASSWORD")):
            print(
                "Refusing to bind to a non-localhost host without WEB_GUI_USERNAME/"
                "WEB_GUI_PASSWORD set in .env -- the Approval Center and Setup "
                "Wizard would otherwise be reachable by anyone on the network.",
                file=sys.stderr,
            )
            sys.exit(1)
    uvicorn.run(app, host=host, port=port)
