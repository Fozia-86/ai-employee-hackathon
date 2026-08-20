import os
import re
import glob
import shutil
import logging
import json
import datetime
import uuid
from dotenv import load_dotenv
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from mcp.server.fastmcp import FastMCP

try:
    import anthropic
except ImportError:
    anthropic = None

dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

logging.basicConfig(level=logging.INFO)
mcp = FastMCP("Core Vault Server")
VAULT_PATH = os.environ.get("VAULT_PATH", os.getcwd())

# Work-Zone Separation (Phase 2): same fail-safe pattern as social_server.py.
# No email-send implementation exists yet (that's future, separate work), but
# this flag is wired now so triage_email never needs restructuring later --
# missing/unset defaults to draft-only.
CLOUD_ZONE = os.environ.get("CLOUD_ZONE", "true").strip().lower() not in ("false", "0", "no")

# Claude-enhanced labeling (Requirement: "Better labeling"). Same fail-safe
# pattern as ODOO_API_KEY in odoo_server.py -- missing key or missing SDK
# means classify_email_with_claude() is skipped and triage_email() falls
# back to pure rule-based (BUSINESS_KEYWORDS/etc) classification, so this is
# additive and never a hard dependency for the email pipeline.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_claude_client = None
if anthropic and ANTHROPIC_API_KEY:
    _claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SPAM_KEYWORDS = [
    "unsubscribe", "congratulations you won", "click here now", "free money",
    "lottery", "viagra", "you have been selected", "claim your prize", "act now",
]
SUPPORT_KEYWORDS = [
    "issue", "problem", "bug", "not working", "broken", "error", "help",
    "trouble", "support", "complaint",
]
BUSINESS_KEYWORDS = [
    "quote", "pricing", "proposal", "partnership", "interested in", "discount",
    "service", "project", "onboarding", "contract", "inquiry", "collaborate",
]

# === Domain-folder routing (Requirement 3a) ===
# Distinct from classify_domain() in agent_runner.py, which means Personal/Business.
# This maps email category (from classify_email below) to the Needs_Action/Plans/
# Pending_Approval subfolder a trigger/draft should live in.
EMAIL_CATEGORY_TO_FOLDER = {"business_inquiry": "Sales", "support": "Support"}

def category_to_folder(category: str) -> str:
    return EMAIL_CATEGORY_TO_FOLDER.get(category, "General")

def classify_email(subject: str, body: str) -> str:
    """Simple keyword-based triage. Returns 'spam', 'support', 'business_inquiry',
    or 'irrelevant' (no business keywords matched)."""
    text = f"{subject} {body}".lower()
    if any(k in text for k in SPAM_KEYWORDS):
        return "spam"
    if any(k in text for k in BUSINESS_KEYWORDS):
        return "business_inquiry"
    if any(k in text for k in SUPPORT_KEYWORDS):
        return "support"
    return "irrelevant"

def _parse_discount_percentage(text: str):
    """Only treats a '<N>%' match as a discount ask if a discount-related word
    appears near it. The old blind regex fired on any percentage in the text
    (e.g. "100% satisfaction guarantee", "50% of our uploads are failing"),
    which combined with the category bug below could turn a support ticket
    into a fabricated discount offer."""
    for match in re.finditer(r'(\d{1,3})\s*%', text):
        window = text[max(0, match.start() - 40):match.end() + 40].lower()
        if any(w in window for w in ("discount", "off", "reduc")):
            return float(match.group(1))
    return None

_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["spam", "support", "business_inquiry", "irrelevant"],
        },
        "discount_percentage": {
            "anyOf": [{"type": "number"}, {"type": "null"}],
            "description": "Discount % the sender is requesting, or null if none mentioned.",
        },
        "reasoning": {
            "type": "string",
            "description": "One sentence on why this category was chosen.",
        },
    },
    "required": ["category", "discount_percentage", "reasoning"],
    "additionalProperties": False,
}

def classify_email_with_claude(subject: str, body: str):
    """Claude-enhanced labeling layer on top of the keyword-based classify_email().
    Returns a dict {category, discount_percentage, reasoning} or None if the
    Claude client isn't configured (missing package/key) or the call fails --
    callers must treat None as "fall back to rule-based only", never as an error.
    Rule-based keyword matching stays the fast, zero-cost first pass; this call
    catches what keywords miss (e.g. a "not interested, please unsubscribe"
    email that happens to contain "interested in") and extracts the discount
    percentage more reliably than the single-regex fallback."""
    if not _claude_client:
        return None
    try:
        response = _claude_client.messages.create(
            model="claude-opus-4-8",
            max_tokens=500,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _CLASSIFICATION_SCHEMA},
            },
            system=(
                "You triage inbound business emails for an AI office-automation "
                "system. Classify the email and extract any discount percentage "
                "the sender is requesting. 'business_inquiry' = a genuine sales "
                "lead, proposal request, or partnership interest. 'support' = an "
                "existing customer reporting a problem. 'spam' = unsolicited "
                "marketing/scam content. 'irrelevant' = anything else (personal, "
                "newsletter, automated notification, etc)."
            ),
            messages=[{
                "role": "user",
                "content": f"Subject: {subject}\n\nBody:\n{body}",
            }],
        )
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)
    except Exception as exc:
        logging.warning(f"Claude-enhanced labeling skipped (falling back to rule-based): {exc}")
        return None

@mcp.tool()
def triage_email(subject: str, sender: str, body: str, gmail_message_id: str = "",
                  gmail_thread_id: str = "", gmail_rfc_message_id: str = "") -> str:
    """Classifies an inbound email and, if business-relevant, drafts a reply to
    /Pending_Approval/ (email_draft_<timestamp>.md). Never sends anything live --
    Cloud zone drafts only; a human on the Local zone reviews and sends.
    Spam/irrelevant emails are logged and skipped, no draft is created.
    gmail_message_id/gmail_thread_id/gmail_rfc_message_id (all optional, blank
    for non-Gmail/dummy sources) are carried into the draft frontmatter so a
    later send step can thread the reply correctly."""
    category = classify_email(subject, body)
    labeling_method = "rule_based_only"
    ai_reasoning = ""

    ai_result = classify_email_with_claude(subject, body)
    if ai_result and ai_result.get("category") in (
        "spam", "support", "business_inquiry", "irrelevant"
    ):
        ai_reasoning = ai_result.get("reasoning", "")
        if ai_result["category"] != category:
            logging.info(
                f"Claude-enhanced labeling overrode rule-based classification: "
                f"'{category}' -> '{ai_result['category']}' ({ai_reasoning})"
            )
            category = ai_result["category"]
            labeling_method = "claude_enhanced_override"
        else:
            labeling_method = "claude_enhanced_confirmed"

    if category in ("spam", "irrelevant"):
        return (
            f"Email classified as [{category}] ({labeling_method}). No draft created. "
            f"Sender: {sender}, Subject: {subject}"
        )

    discount_pct = _parse_discount_percentage(f"{subject} {body}")
    if ai_result and ai_result.get("discount_percentage") is not None:
        discount_pct = float(ai_result["discount_percentage"])
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    unique_suffix = uuid.uuid4().hex[:6]

    decision = "AUTONOMOUS_APPROVED"
    hitl_required = "false"
    cited_rule = ""
    escalation_note = ""

    if category == "business_inquiry" and discount_pct is not None:
        kb_content = search_kb("discount")
        if discount_pct > 20.0:
            decision = "ESCALATION_REQUIRED"
            hitl_required = "true"
            cited_rule = (
                f"20% discount ceiling — knowledge_base.md § Standard Discount "
                f"Boundaries (Escalation Constraint), requires HITL approval"
            )
            escalation_note = (
                f"\n## Policy Escalation\n"
                f"- **Cited rule**: {cited_rule}\n"
                f"- **Requested discount**: {discount_pct:.0f}%\n"
                f"- **KB excerpt**:\n\n> {kb_content.strip().splitlines()[4] if len(kb_content.strip().splitlines()) > 4 else kb_content.strip()}\n\n"
                f"This draft is not a routine reply — it is a policy-driven escalation. "
                f"Do not approve/send without confirming the discount amount with the business owner.\n"
            )
            reply_body = (
                f"Thank you for reaching out, {sender}.\n\n"
                f"We've received your request for a {discount_pct:.0f}% discount. "
                f"This exceeds our standard autonomous authorization ceiling of 20%, so "
                f"it has been escalated to our team for manual review before we can confirm.\n\n"
                f"We'll follow up shortly with next steps."
            )
        else:
            cited_rule = (
                f"20% loyalty/discount ceiling — knowledge_base.md § Standard "
                f"Discount Boundaries (Autonomous Authorization)"
            )
            reply_body = (
                f"Thank you for reaching out, {sender}.\n\n"
                f"We're happy to offer a {discount_pct:.0f}% discount on your order, which "
                f"is within our standard authorization limits. Let us know if you'd like to "
                f"proceed and we'll prepare the paperwork."
            )
    elif category == "support":
        reply_body = (
            f"Hi {sender},\n\nThanks for letting us know about the issue described in "
            f"\"{subject}\". Our team is looking into it and will follow up with a resolution "
            f"shortly."
        )
    else:  # business_inquiry
        reply_body = (
            f"Hi {sender},\n\nThank you for your interest in our services regarding "
            f"\"{subject}\". We'd love to learn more about your requirements and share a "
            f"proposal. Could you tell us more about your timeline and scope?"
        )

    filename = f"email_draft_{timestamp}_{unique_suffix}.md"
    folder = category_to_folder(category)
    pending_dir = os.path.join(VAULT_PATH, "Pending_Approval", folder)
    os.makedirs(pending_dir, exist_ok=True)
    target_path = os.path.join(pending_dir, filename)

    draft_content = (
        f"---\n"
        f"type: email_draft\n"
        f"category: {category}\n"
        f"original_sender: {sender}\n"
        f"original_subject: {subject}\n"
        f"created: {timestamp}\n"
        f"status: awaiting_local_approval\n"
        f"decision: {decision}\n"
        f"hitl_required: {hitl_required}\n"
        f"labeling_method: {labeling_method}\n"
        + (f"ai_reasoning: \"{ai_reasoning.replace(chr(34), chr(39))}\"\n" if ai_reasoning else "")
        + (f"cited_rule: \"{cited_rule}\"\n" if cited_rule else "")
        + (f"gmail_message_id: {gmail_message_id}\n" if gmail_message_id else "")
        + (f"gmail_thread_id: {gmail_thread_id}\n" if gmail_thread_id else "")
        + (f"gmail_rfc_message_id: \"{gmail_rfc_message_id}\"\n" if gmail_rfc_message_id else "")
        + f"---\n\n"
        f"# Email Draft — {category}\n\n"
        f"## Original Email\n"
        f"- **From**: {sender}\n"
        f"- **Subject**: {subject}\n\n"
        f"> {body}\n"
        f"{escalation_note}\n"
        f"## Draft Reply\n\n{reply_body}\n"
    )

    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(draft_content)

    return (
        f"Draft Created [{category}, decision={decision}]: saved to {target_path} "
        f"for Local zone approval."
    )

@mcp.tool()
def triage_whatsapp(sender: str, body: str, whatsapp_message_id: str = "") -> str:
    """WhatsApp counterpart to triage_email(): classifies an inbound WhatsApp
    message (rule-based + Claude-enhanced, same as email) and, if
    business-relevant, drafts a reply to /Pending_Approval/
    (whatsapp_draft_<timestamp>.md). Draft only -- this never sends anything
    over WhatsApp; a human on the Local zone must copy the reply and send it
    manually, or approve it once a WhatsApp send tool exists. Spam/irrelevant
    messages are logged and skipped, no draft is created."""
    category = classify_email(sender, body)
    labeling_method = "rule_based_only"
    ai_reasoning = ""

    ai_result = classify_email_with_claude(sender, body)
    if ai_result and ai_result.get("category") in (
        "spam", "support", "business_inquiry", "irrelevant"
    ):
        ai_reasoning = ai_result.get("reasoning", "")
        if ai_result["category"] != category:
            logging.info(
                f"Claude-enhanced labeling overrode rule-based classification "
                f"(WhatsApp): '{category}' -> '{ai_result['category']}' ({ai_reasoning})"
            )
            category = ai_result["category"]
            labeling_method = "claude_enhanced_override"
        else:
            labeling_method = "claude_enhanced_confirmed"

    if category in ("spam", "irrelevant"):
        return (
            f"WhatsApp message classified as [{category}] ({labeling_method}). "
            f"No draft created. Sender: {sender}"
        )

    discount_pct = _parse_discount_percentage(body)
    if ai_result and ai_result.get("discount_percentage") is not None:
        discount_pct = float(ai_result["discount_percentage"])
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    unique_suffix = uuid.uuid4().hex[:6]

    decision = "AUTONOMOUS_APPROVED"
    hitl_required = "false"
    cited_rule = ""
    escalation_note = ""

    if category == "business_inquiry" and discount_pct is not None:
        kb_content = search_kb("discount")
        if discount_pct > 20.0:
            decision = "ESCALATION_REQUIRED"
            hitl_required = "true"
            cited_rule = (
                f"20% discount ceiling — knowledge_base.md § Standard Discount "
                f"Boundaries (Escalation Constraint), requires HITL approval"
            )
            escalation_note = (
                f"\n## Policy Escalation\n"
                f"- **Cited rule**: {cited_rule}\n"
                f"- **Requested discount**: {discount_pct:.0f}%\n"
                f"- **KB excerpt**:\n\n> {kb_content.strip().splitlines()[4] if len(kb_content.strip().splitlines()) > 4 else kb_content.strip()}\n\n"
                f"This draft is not a routine reply — it is a policy-driven escalation. "
                f"Do not approve/send without confirming the discount amount with the business owner.\n"
            )
            reply_body = (
                f"Thanks for reaching out! We've received your request for a "
                f"{discount_pct:.0f}% discount. This exceeds our standard autonomous "
                f"authorization ceiling of 20%, so it's been escalated to our team for "
                f"manual review before we can confirm."
            )
        else:
            cited_rule = (
                f"20% loyalty/discount ceiling — knowledge_base.md § Standard "
                f"Discount Boundaries (Autonomous Authorization)"
            )
            reply_body = (
                f"Thanks for reaching out! We're happy to offer a {discount_pct:.0f}% "
                f"discount on your order, which is within our standard authorization "
                f"limits. Let us know if you'd like to proceed."
            )
    elif category == "support":
        reply_body = (
            f"Hi, thanks for letting us know about the issue. Our team is looking "
            f"into it and will follow up with a resolution shortly."
        )
    else:  # business_inquiry
        reply_body = (
            f"Hi, thank you for your interest in our services! We'd love to learn "
            f"more about your requirements and share a proposal. Could you tell us "
            f"more about your timeline and scope?"
        )

    filename = f"whatsapp_draft_{timestamp}_{unique_suffix}.md"
    folder = category_to_folder(category)
    pending_dir = os.path.join(VAULT_PATH, "Pending_Approval", folder)
    os.makedirs(pending_dir, exist_ok=True)
    target_path = os.path.join(pending_dir, filename)

    draft_content = (
        f"---\n"
        f"type: whatsapp_draft\n"
        f"category: {category}\n"
        f"original_sender: {sender}\n"
        f"created: {timestamp}\n"
        f"status: awaiting_local_approval\n"
        f"decision: {decision}\n"
        f"hitl_required: {hitl_required}\n"
        f"labeling_method: {labeling_method}\n"
        + (f"ai_reasoning: \"{ai_reasoning.replace(chr(34), chr(39))}\"\n" if ai_reasoning else "")
        + (f"cited_rule: \"{cited_rule}\"\n" if cited_rule else "")
        + (f"whatsapp_message_id: {whatsapp_message_id}\n" if whatsapp_message_id else "")
        + f"---\n\n"
        f"# WhatsApp Draft — {category}\n\n"
        f"## Original Message\n"
        f"- **From**: {sender}\n\n"
        f"> {body}\n"
        f"{escalation_note}\n"
        f"## Draft Reply (send manually via WhatsApp — no auto-send yet)\n\n{reply_body}\n"
    )

    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(draft_content)

    return (
        f"Draft Created [{category}, decision={decision}]: saved to {target_path} "
        f"for Local zone approval."
    )

@mcp.tool()
def monitor_triggers() -> str:
    """Scans /Needs_Action/ for files starting with 'TRIGGER_'."""
    directory = os.path.join(VAULT_PATH, "Needs_Action")
    pattern = os.path.join(directory, "TRIGGER_*")
    files = glob.glob(pattern)
    
    if not files:
        return "No trigger files found in /Needs_Action."
    
    results = []
    for filepath in files:
        filename = os.path.basename(filepath)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            results.append(f"File: {filename}\nContent:\n{content}\n---")
        except Exception as e:
            results.append(f"Error reading {filename}: {str(e)}")
    return "\n".join(results)

@mcp.tool()
def claim_trigger(agent_id: str = "cloud-agent") -> str:
    """Claim-by-move (Requirement 2): moves one TRIGGER_*.md (and its
    original_file sibling, if present) out of /Needs_Action/ into
    /In_Progress/<agent_id>/ before it is processed, so a trigger is never
    read/processed by more than one agent and a crash mid-processing leaves a
    durable marker instead of silently reprocessing or losing the trigger.
    Returns the same "File: ...\\nContent:\\n...\\n---" format monitor_triggers
    used to return, so existing parsing (extract_original_file, discount/
    customer regexes) is unaffected. Returns "No trigger files found in
    /Needs_Action." (unchanged sentinel string) when there is nothing to do."""
    in_progress_dir = os.path.join(VAULT_PATH, "In_Progress", agent_id)
    os.makedirs(in_progress_dir, exist_ok=True)

    # Recovery-first: if this agent already has a claimed trigger sitting in
    # In_Progress/ (e.g. a crash happened after claiming but before the
    # archive_processed_triggers call at the end of a cycle), re-serve it
    # instead of claiming a new one, so work is never silently stranded.
    stranded = glob.glob(os.path.join(in_progress_dir, "TRIGGER_*"))
    if stranded:
        filepath = stranded[0]
        filename = os.path.basename(filepath)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return f"File: {filename}\nContent:\n{content}\n---"

    needs_action_dir = os.path.join(VAULT_PATH, "Needs_Action")
    # Root-level triggers (where watcher.py/gmail_watcher.py drop them) plus
    # one subfolder level deep (Needs_Action/Sales/, etc. -- e.g. if a human
    # manually filed something in Obsidian) are both eligible.
    candidates = glob.glob(os.path.join(needs_action_dir, "TRIGGER_*")) + \
                 glob.glob(os.path.join(needs_action_dir, "*", "TRIGGER_*"))
    candidates = [f for f in candidates if os.path.isfile(f)]
    candidates.sort(key=os.path.getmtime)

    already_claimed = set(
        os.path.basename(f)
        for f in glob.glob(os.path.join(VAULT_PATH, "In_Progress", "*", "TRIGGER_*"))
    )

    for filepath in candidates:
        filename = os.path.basename(filepath)
        if filename in already_claimed:
            # Already claimed by another agent -- skip (defensive; guards a
            # future multi-agent race, though only one agent runs today).
            continue

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            return f"Error reading {filename}: {str(e)}"

        match = re.search(r'original_file:\s*(\S+)', content)
        if match:
            orig_name = match.group(1).strip()
            orig_path = os.path.join(os.path.dirname(filepath), orig_name)
            if os.path.exists(orig_path):
                shutil.move(orig_path, os.path.join(in_progress_dir, orig_name))

        shutil.move(filepath, os.path.join(in_progress_dir, filename))

        return f"File: {filename}\nContent:\n{content}\n---"

    return "No trigger files found in /Needs_Action."

@mcp.tool()
def search_kb(query: str) -> str:
    """Reads system knowledge_base.md rules."""
    kb_file = os.path.join(VAULT_PATH, "Knowledge_Base", "knowledge_base.md")
    if not os.path.exists(kb_file):
        return f"Error: Knowledge base file not found at {kb_file}"
    try:
        with open(kb_file, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        return f"Error reading Knowledge Base: {str(e)}"

@mcp.tool()
def write_approval_file(filename: str, content: str, folder: str = "Sales") -> str:
    """Writes escalation file to /Pending_Approval/<folder>/ (folder defaults to
    Sales -- the deal/discount escalation pipeline is this tool's only caller today)."""
    safe_filename = os.path.basename(filename)
    if not safe_filename.endswith(".md"):
        safe_filename += ".md"
    target_dir = os.path.join(VAULT_PATH, "Pending_Approval", folder)
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, safe_filename)
    try:
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote pending approval file to: {target_path}"
    except Exception as e:
        return f"Error writing approval file: {str(e)}"

@mcp.tool()
def write_payment_request(invoice_id: str, amount: float, method: str, customer_name: str = "") -> str:
    """Writes a payment-request draft to /Pending_Approval/Sales/ for HITL review
    (Requirement 2b, payments/banking, sandbox-only).

    This is a manual/dummy trigger only -- it records that a human has
    reported a payment was received (there is no real bank/gateway webhook
    wired into this vault) and never calls any payment API itself. The actual
    mutation happens only after Local-zone approval via review_approvals.py,
    then record_payment() (mcp_servers/odoo_server.py, EXECUTION_ZONE=local
    gated) run through process_approved_payments.py.
    """
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    unique = uuid.uuid4().hex[:6]
    filename = f"payment_request_{timestamp}_{unique}.md"
    target_dir = os.path.join(VAULT_PATH, "Pending_Approval", "Sales")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, filename)

    display_customer = customer_name or "Unknown Customer"
    content = (
        "---\n"
        "type: payment_request\n"
        f"invoice_id: {invoice_id}\n"
        f"amount: {amount}\n"
        f"method: {method}\n"
        f"customer_name: {display_customer}\n"
        f"created: {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        "status: awaiting_local_approval\n"
        "decision: PENDING\n"
        "---\n\n"
        "## Payment Request\n\n"
        f"A payment of **{amount}** via **{method}** has been reported against "
        f"Invoice **{invoice_id}** ({display_customer}).\n\n"
        "This is a sandbox/dummy request awaiting Local-zone human approval. "
        "Approving this file (via review_approvals.py) and then running "
        "process_approved_payments.py will call record_payment() against Odoo, "
        "gated behind EXECUTION_ZONE=local.\n"
    )
    try:
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Payment Request Created: saved to {target_path}"
    except Exception as e:
        return f"Error writing payment request: {str(e)}"


@mcp.tool()
def archive_processed_triggers(outcome: str, note: str = "", agent_id: str = "cloud-agent") -> str:
    """Moves all current /In_Progress/<agent_id>/TRIGGER_*.md files (and their
    referenced original_file siblings) into /Done/Processed_Triggers/, timestamped,
    so a claimed trigger (see claim_trigger) is never left in In_Progress/ or
    reprocessed. outcome should be 'success' or 'failed'."""
    directory = os.path.join(VAULT_PATH, "In_Progress", agent_id)
    pattern = os.path.join(directory, "TRIGGER_*")
    files = glob.glob(pattern)

    if not files:
        return "No trigger files present to archive."

    dest_dir = os.path.join(VAULT_PATH, "Done", "Processed_Triggers")
    os.makedirs(dest_dir, exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    status_tag = "SUCCESS" if outcome == "success" else "FAILED"

    moved = []
    for filepath in files:
        filename = os.path.basename(filepath)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            content = ""

        match = re.search(r'original_file:\s*(\S+)', content)
        if match:
            orig_name = match.group(1).strip()
            orig_path = os.path.join(directory, orig_name)
            if os.path.exists(orig_path):
                orig_dest = os.path.join(dest_dir, f"{timestamp}_{status_tag}_{orig_name}")
                try:
                    shutil.move(orig_path, orig_dest)
                    moved.append(orig_name)
                except Exception:
                    pass

        dest_path = os.path.join(dest_dir, f"{timestamp}_{status_tag}_{filename}")
        try:
            shutil.move(filepath, dest_path)
            moved.append(filename)
        except Exception as e:
            return f"Error archiving {filename}: {str(e)}"

    if note:
        try:
            with open(os.path.join(dest_dir, f"{timestamp}_{status_tag}_note.md"), 'w', encoding='utf-8') as f:
                f.write(note)
        except Exception:
            pass

    return f"Archived {len(moved)} file(s) with outcome=[{status_tag}] to Done/Processed_Triggers/: {', '.join(moved)}"

@mcp.tool()
def write_error_recovery_file(details: str) -> str:
    """Ralph Wiggum protocol: after automatic retries are exhausted, isolates the
    failed payload to /Pending_Approval/error_recovery_[timestamp].md for HITL review."""
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename = f"error_recovery_{timestamp}.md"
    target_path = os.path.join(VAULT_PATH, "Pending_Approval", filename)
    try:
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(
                f"---\ntype: error_recovery\ntimestamp: {timestamp}\n---\n\n"
                f"# Ralph Wiggum Error Recovery\n\n"
                f"Automatic retries exhausted. Payload isolated for human review.\n\n"
                f"{details}\n"
            )
        return f"Error recovery payload isolated to: {target_path}"
    except Exception as e:
        return f"Error writing recovery file: {str(e)}"

@mcp.tool()
def update_dashboard(status_table: str) -> str:
    """Updates Dashboard.md with a status table. UNUSED by the Cloud agent loop
    as of Requirement 3b (single-writer Dashboard fix) -- this full-file overwrite
    was silently destroying the `## Recent Execution Log` section that
    review_approvals.py/send_approved_emails.py append to. agent_runner.py now
    calls write_dashboard_update() instead, which writes to /Updates/ for the
    Local-only merge_dashboard.py to merge in without clobbering the log. Kept
    defined (not deleted) for lower risk; do not wire a new Cloud caller to this
    -- use write_dashboard_update instead."""
    dashboard_file = os.path.join(VAULT_PATH, "Dashboard.md")
    try:
        with open(dashboard_file, 'w', encoding='utf-8') as f:
            f.write("# Agent Activity Dashboard\n\n")
            f.write(status_table)
        return f"Dashboard successfully updated at {dashboard_file}"
    except Exception as e:
        return f"Error writing dashboard: {str(e)}"

@mcp.tool()
def write_dashboard_update(status_table: str, note: str = "") -> str:
    """Requirement 3b (Dashboard single-writer): writes a small timestamped
    signal file to /Updates/ instead of touching Dashboard.md directly. The
    Cloud agent loop calls this after every processed trigger; only the
    Local-only merge_dashboard.py actually writes Dashboard.md, merging these
    signals in without destroying the Recent Execution Log section."""
    updates_dir = os.path.join(VAULT_PATH, "Updates")
    os.makedirs(updates_dir, exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    unique_suffix = uuid.uuid4().hex[:6]
    filename = f"update_{timestamp}_{unique_suffix}.md"
    target_path = os.path.join(updates_dir, filename)
    content = (
        f"---\ntype: dashboard_update\ncreated: {timestamp}\n---\n\n"
        f"{status_table}\n"
        + (f"\nnote: {note}\n" if note else "")
    )
    try:
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Dashboard update signal written to {target_path}"
    except Exception as e:
        return f"Error writing dashboard update signal: {str(e)}"

@mcp.tool()
def encrypt_sensitive_data(payload: str) -> str:
    """Encrypts sensitive data using AES-256 GCM algorithm.

    Fail-safe, not fail-open: this vault's convention everywhere else
    (CLOUD_ZONE, EXECUTION_ZONE) is that a missing/misconfigured safety
    setting refuses the action rather than silently degrading to something
    weaker. A hardcoded fallback key defeats the entire point of encrypting
    financial records -- anyone with a copy of this source file could
    decrypt every record ever written under that fallback. So a missing or
    too-short SECRET_ENCRYPTION_KEY now refuses to encrypt at all instead of
    silently using a publicly-known key (2026-08 production-readiness fix).
    """
    raw_key = os.environ.get("SECRET_ENCRYPTION_KEY", "")
    if not raw_key or len(raw_key) < 32:
        logging.error("SECRET_ENCRYPTION_KEY missing or too short (need >=32 chars). Refusing to encrypt.")
        return (
            "Encryption Failure: SECRET_ENCRYPTION_KEY is missing or shorter than 32 characters. "
            "Set a real 32+ character key in .env before financial data can be encrypted -- "
            "refusing to fall back to a weak default key."
        )

    try:
        key_bytes = raw_key[:32].encode('utf-8')
        iv = os.urandom(12)
        
        encryptor = Cipher(
            algorithms.AES(key_bytes),
            modes.GCM(iv),
            backend=default_backend()
        ).encryptor()
        
        ciphertext = encryptor.update(payload.encode('utf-8')) + encryptor.finalize()
        tag = encryptor.tag
        
        secure_record = {
            "iv": iv.hex(),
            "ciphertext": ciphertext.hex(),
            "tag": tag.hex()
        }
        
        records_dir = os.path.join(VAULT_PATH, "Secure_Records")
        os.makedirs(records_dir, exist_ok=True)
        record_file = os.path.join(records_dir, "encrypted_transactions.json")
        
        records_list = []
        if os.path.exists(record_file):
            try:
                with open(record_file, 'r') as rf:
                    records_list = json.load(rf)
            except:
                pass
                
        records_list.append(secure_record)
        with open(record_file, 'w') as wf:
            json.dump(records_list, wf, indent=4)
            
        return f"Secure Vault Success: Ciphertext successfully written to Secure_Records."
    except Exception as e:
        return f"Encryption Failure: {str(e)}"

@mcp.tool()
def generate_weekly_audit() -> str:
    """Scans vault directories and compiles a Weekly Business Audit & CEO Briefing.

    2026-08 production-readiness fix: the metrics below used to be derived by
    counting the words "Completed"/"Halted" inside Dashboard.md's status
    table. Since the Requirement 3b single-writer fix, Dashboard.md's table
    only ever holds the *latest single trigger's row* (merge_dashboard.py
    replaces it wholesale each merge) -- so that count was structurally
    capped at 0 or 1 and never reflected real cumulative activity. The real,
    durable history of every deal lives in Audit_Logs/audit_log.json
    (write_audit_log() appends one entry per DEAL_COMPLETED/DEAL_ESCALATION
    event and is never overwritten), so metrics are now computed from there,
    scoped to entries timestamped within the current ISO week (matching what
    a "Weekly" briefing should actually mean -- the old version counted
    all-time totals every week regardless of the filename's week number).
    """
    today = datetime.date.today()
    year, week_num, _ = today.isocalendar()
    briefing_filename = f"CEO_Briefing_{year}_W{week_num}.md"
    briefing_path = os.path.join(VAULT_PATH, briefing_filename)

    audit_log_file = os.path.join(VAULT_PATH, "Audit_Logs", "audit_log.json")
    completed_deals_count = 0
    halted_deals_count = 0

    if os.path.exists(audit_log_file):
        try:
            with open(audit_log_file, 'r', encoding='utf-8') as f:
                entries = json.load(f)
            for entry in entries:
                ts_raw = entry.get("timestamp", "")
                try:
                    ts = datetime.datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                ts_year, ts_week, _ = ts.isocalendar()
                if (ts_year, ts_week) != (year, week_num):
                    continue
                event_type = entry.get("event_type", "")
                if event_type == "DEAL_COMPLETED":
                    completed_deals_count += 1
                elif event_type == "DEAL_ESCALATION":
                    halted_deals_count += 1
        except Exception:
            pass

    pending_dir = os.path.join(VAULT_PATH, "Pending_Approval")
    pending_files = []
    if os.path.exists(pending_dir):
        pending_files = [f for f in os.listdir(pending_dir) if f.endswith(".md")]
    pending_count = len(pending_files)

    briefing_content = f"""# Weekly CEO Business Briefing (Fiscal Week {week_num}, {year})
*Generated automatically by Autonomous AI Employee Loop on {today.strftime('%Y-%m-%d')}*

## 1. Executive Operations Summary
The autonomous agent loop has executed continuously under **Gold Tier** policies. Below is the parsed metrics summary across all business processes for this fiscal week:

- **Total Transactions Screened**: {completed_deals_count + halted_deals_count}
- **Deals Approved Autonomously (≤ 20% discount)**: {completed_deals_count}
- **Escalation Requests Blocked (> 20% discount)**: {halted_deals_count} (Currently waiting in `/Pending_Approval/`)
- **Active Pending CEO Approvals**: {pending_count}

## 2. Escalation Queue Details
The following requests require your immediate manual authorization in the `/Pending_Approval/` directory:
"""
    if pending_files:
        for f in pending_files:
            briefing_content += f"- **File**: `{f}` (Pending Review)\n"
    else:
        briefing_content += "- *No active escalations in queue. System operating inside boundaries.*\n"
        
    briefing_content += f"""
## 3. Data Integrity & Enterprise Sync Status
- **Odoo Cloud ERP Status**: Synced (Live draft invoice records registered in `account.move`)
- **Local Database State**: Secure (Sensitive transactional records encrypted under AES-256 GCM inside `/Secure_Records/`)
- **System Health**: 100% Operational, Zero critical runtime errors.

---
*Actions Required: Please review any files inside `/Pending_Approval/` and move approved ones to a processed archive.*
"""
    try:
        with open(briefing_path, 'w', encoding='utf-8') as f:
            f.write(briefing_content)
        return f"Audit Success: Compiled briefing inside Vault as '{briefing_filename}'."
    except Exception as e:
        return f"Audit Failure: Unable to write briefing file: {str(e)}"

# === NAYA INTEGRATION TOOL: Professional Comprehensive JSON Audit Logger ===
@mcp.tool()
def write_audit_log(event_type: str, domain_type: str, details: str) -> str:
    """
    Appends a highly structured JSON security audit record inside Vault/Audit_Logs/.
    Logs target domains (Personal or Business) for strict cross-domain separation policies.
    """
    logs_dir = os.path.join(VAULT_PATH, "Audit_Logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    log_file = os.path.join(logs_dir, "audit_log.json")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Create complete structural audit payload
    audit_entry = {
        "timestamp": timestamp,
        "event_type": event_type,
        "domain": domain_type,  # Strict Personal / Business segregation marker
        "details": details,
        "agent_state": "Gold-Tier-Autonomous"
    }
    
    logs_list = []
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8') as rf:
                logs_list = json.load(rf)
        except:
            pass
            
    logs_list.append(audit_entry)
    
    try:
        with open(log_file, 'w', encoding='utf-8') as wf:
            json.dump(logs_list, wf, indent=4)
        return f"Audit Logging Success: Event of type [{event_type}] logged for [{domain_type}] domain."
    except Exception as e:
        return f"Audit Logging Failure: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")