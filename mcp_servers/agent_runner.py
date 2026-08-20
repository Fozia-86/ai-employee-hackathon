import os
import sys
import time
import re
import asyncio
from contextlib import AsyncExitStack
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

VAULT_PATH = os.environ.get("VAULT_PATH", os.getcwd())
PYTHON_EXEC = os.environ.get("PYTHON_EXEC", sys.executable)

# Setup independent parameters for multiple servers
vault_params = StdioServerParameters(command=PYTHON_EXEC, args=[f"{VAULT_PATH}/mcp_servers/vault_server.py"])
odoo_params = StdioServerParameters(command=PYTHON_EXEC, args=[f"{VAULT_PATH}/mcp_servers/odoo_server.py"])
social_params = StdioServerParameters(command=PYTHON_EXEC, args=[f"{VAULT_PATH}/mcp_servers/social_server.py"])

def parse_discount_percentage(text: str) -> float:
    match = re.search(r'(Discount|discount|Requested Discount|Requested discount):\s*(\d+)%', text)
    if match:
        return float(match.group(2))
    match_fallback = re.search(r'(\d+)%', text)
    if match_fallback:
        return float(match_fallback.group(1))
    return 0.0

def parse_customer_name(text: str) -> str:
    match = re.search(r'(Customer Name|Customer|Client):\s*(.*)', text, re.IGNORECASE)
    if match:
        return match.group(2).strip()
    return "Unknown Customer"

def parse_customer_email(text: str) -> str:
    # Match standard email addresses
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if match:
        return match.group(0).strip()
    return "unknown@example.com"  # Neutral fallback -- was a stray personal-looking
    # default ("sultan@gmail.com") left over from early demo/testing; a bare
    # placeholder is used instead so a malformed trigger never gets
    # attributed to a real-looking (but wrong) person.

def looks_like_deal_request(text: str) -> bool:
    """Guards the legacy deal/discount pipeline (2026-08 production-readiness
    fix). Before this, ANY trigger that wasn't type:email or type:whatsapp
    fell straight into the deal pipeline below with silent defaults
    (discount_rate=0.0, customer="Unknown Customer") -- meaning a random,
    unrelated file dropped into Inbox/ (a stray note, a misfiled document)
    would silently be treated as a *completed* $3500 deal: a mock Odoo
    invoice created, a fake "new partner onboarding" tweet/Facebook post
    drafted, and a DEAL_COMPLETED audit entry logged, all for content that
    was never actually a sales deal. Only proceed into that pipeline when the
    trigger text plausibly is one -- an explicit "Customer/Client:" field
    (parse_customer_name's own pattern) or an explicit "Discount: NN%" field
    (parse_discount_percentage's primary, non-fallback pattern). Anything
    else is treated as unrecognized, per Company_Handbook.md's own rule that
    ambiguous tasks get isolated for human review, not silently actioned."""
    has_customer_field = re.search(r'(Customer Name|Customer|Client):\s*\S', text, re.IGNORECASE) is not None
    has_explicit_discount = re.search(r'(Discount|discount|Requested Discount|Requested discount):\s*(\d+)%', text) is not None
    return has_customer_field or has_explicit_discount

# === Domain Classification logic (Personal vs Business separation) ===
def classify_domain(email: str) -> str:
    personal_domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]
    domain = email.split("@")[-1].lower()
    if domain in personal_domains:
        return "Personal"
    return "Business"

# === Email trigger dispatch (Requirement 7 / Platinum gate, Cloud side) ===
def extract_original_file(trigger_text: str):
    """Pulls the `original_file:` frontmatter value watcher.py wrote into the
    TRIGGER_*.md wrapper, so we can go read the actual inbox file it points at."""
    match = re.search(r'^original_file:\s*(.+)$', trigger_text, re.MULTILINE)
    return match.group(1).strip() if match else None

def parse_email_fields(raw_text: str):
    """Parses a dummy inbox email file (frontmatter `type: email`, `from:`,
    `subject:`, plain-text body). Returns None if the file isn't email-typed,
    so non-email triggers (deals) fall through to the existing pipeline untouched."""
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', raw_text, re.DOTALL)
    if not fm_match:
        return None
    frontmatter, body = fm_match.group(1), fm_match.group(2)
    fm = dict(re.findall(r'^(\w+):\s*(.*)$', frontmatter, re.MULTILINE))
    if fm.get("type") != "email":
        return None
    return {
        "subject": fm.get("subject", "(no subject)"),
        "sender": fm.get("from", "unknown@example.com"),
        "body": body.strip(),
        "gmail_message_id": fm.get("gmail_message_id", ""),
        "gmail_thread_id": fm.get("gmail_thread_id", ""),
        "gmail_rfc_message_id": fm.get("gmail_rfc_message_id", "").strip('"'),
    }

def parse_whatsapp_fields(raw_text: str):
    """Parses a whatsapp_watcher.py inbox file (frontmatter `type: whatsapp`,
    `from:`, plain-text body). Returns None if the file isn't whatsapp-typed,
    same fall-through contract as parse_email_fields() -- this must be
    checked BEFORE the generic deal/discount pipeline, or a WhatsApp trigger
    silently misroutes into an Odoo-invoice/social-draft flow it has nothing
    to do with (found as a real bug: pre-existing WhatsApp triggers had no
    dedicated dispatch at all)."""
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

async def execute_tool_with_retry(session, tool_name, arguments, max_retries=3):
    attempt = 0
    while attempt < max_retries:
        try:
            response = await session.call_tool(tool_name, arguments)
            return response.content[0].text
        except Exception as e:
            attempt += 1
            print(f"⚠️ [Ralph Wiggum Loop] Exception caught on '{tool_name}'. Attempt {attempt}/{max_retries}. Error: {str(e)}")
            await asyncio.sleep(2)
            if attempt >= max_retries:
                print(f"❌ [Ralph Wiggum Loop] Critical Failure on '{tool_name}' after maximum retries.")
                raise e

async def run_agent_loop():
    print("🚀 Parallel Multi-MCP Agent Host with Cross-Domain Audit integration active.")
    print("🔌 Spawning Vault, Odoo, and Social Media MCP Server subprocesses...\n")
    
    async with AsyncExitStack() as stack:
        # Connect to Core Vault Server
        read_v, write_v = await stack.enter_async_context(stdio_client(vault_params))
        session_v = await stack.enter_async_context(ClientSession(read_v, write_v))
        await session_v.initialize()
        print("✅ Core Vault Server connected successfully.")

        # Connect to Core Odoo Server
        read_o, write_o = await stack.enter_async_context(stdio_client(odoo_params))
        session_o = await stack.enter_async_context(ClientSession(read_o, write_o))
        await session_o.initialize()
        print("✅ Core Odoo Server connected successfully.")

        # Connect to Core Social Server
        read_s, write_s = await stack.enter_async_context(stdio_client(social_params))
        session_s = await stack.enter_async_context(ClientSession(read_s, write_s))
        await session_s.initialize()
        print("✅ Core Social Communication Server connected successfully.\n")

        print("🔄 Autonomous Polling Loop Active...")
        
        while True:
            try:
                triggers_output = await execute_tool_with_retry(session_v, "claim_trigger", {})
                
                if "No trigger files found" in triggers_output or not triggers_output.strip():
                    await execute_tool_with_retry(session_v, "generate_weekly_audit", {})
                    await asyncio.sleep(5)
                    continue
                    
                print("\n📥 New Trigger Identified!")
                print(triggers_output)

                try:
                    # Dispatch: is this trigger's original file an email (type: email)?
                    # If so, route to triage_email() instead of the deal/discount
                    # pipeline below. Checked first and kept in its own branch so the
                    # existing discount pipeline is untouched when it's not an email.
                    original_file = extract_original_file(triggers_output)
                    email_fields = None
                    whatsapp_fields = None
                    if original_file:
                        # claim_trigger() already moved this file (and its
                        # TRIGGER_ wrapper) into In_Progress/cloud-agent/ before
                        # returning triggers_output -- read it from there, not
                        # from Needs_Action/ (Requirement 2, claim-by-move).
                        original_path = os.path.join(VAULT_PATH, "In_Progress", "cloud-agent", original_file)
                        if os.path.exists(original_path):
                            with open(original_path, "r", encoding="utf-8") as f:
                                raw_original = f.read()
                            email_fields = parse_email_fields(raw_original)
                            if not email_fields:
                                whatsapp_fields = parse_whatsapp_fields(raw_original)

                    if whatsapp_fields:
                        print(f"💬 WhatsApp trigger detected -> Sender: {whatsapp_fields['sender']}")
                        triage_result = await execute_tool_with_retry(
                            session_v,
                            "triage_whatsapp",
                            {
                                "sender": whatsapp_fields["sender"],
                                "body": whatsapp_fields["body"],
                                "whatsapp_message_id": whatsapp_fields["whatsapp_message_id"],
                            }
                        )
                        print(f"💬 WhatsApp Triage Result: {triage_result}")

                        status_table = (
                            "| Deal Identifier | Status | Action/Details | Error Attempts |\n"
                            "| :--- | :--- | :--- | :--- |\n"
                            f"| {original_file} | WhatsApp Triaged | {triage_result[:150]} | 0 |\n"
                        )
                        await execute_tool_with_retry(
                            session_v, "write_dashboard_update",
                            {"status_table": status_table, "note": f"WhatsApp triaged: {original_file}"}
                        )

                        audit_msg = (
                            f"WhatsApp message from {whatsapp_fields['sender']} triaged. "
                            f"Result: {triage_result}"
                        )
                        await execute_tool_with_retry(
                            session_v,
                            "write_audit_log",
                            {"event_type": "WHATSAPP_TRIAGE", "domain_type": "Business", "details": audit_msg}
                        )

                    elif email_fields:
                        print(f"📧 Email trigger detected -> Subject: {email_fields['subject']}, Sender: {email_fields['sender']}")
                        triage_result = await execute_tool_with_retry(
                            session_v,
                            "triage_email",
                            {
                                "subject": email_fields["subject"],
                                "sender": email_fields["sender"],
                                "body": email_fields["body"],
                                "gmail_message_id": email_fields["gmail_message_id"],
                                "gmail_thread_id": email_fields["gmail_thread_id"],
                                "gmail_rfc_message_id": email_fields["gmail_rfc_message_id"],
                            }
                        )
                        print(f"✉️ Email Triage Result: {triage_result}")

                        email_domain_route = classify_domain(email_fields["sender"])
                        status_table = (
                            "| Deal Identifier | Status | Action/Details | Domain Route | Error Attempts |\n"
                            "| :--- | :--- | :--- | :--- | :--- |\n"
                            f"| {original_file} | Email Triaged | {triage_result[:150]} | {email_domain_route} | 0 |\n"
                        )
                        await execute_tool_with_retry(
                            session_v, "write_dashboard_update",
                            {"status_table": status_table, "note": f"Email triaged: {original_file}"}
                        )

                        audit_msg = (
                            f"Email from {email_fields['sender']} (subject: \"{email_fields['subject']}\") "
                            f"triaged. Result: {triage_result}"
                        )
                        await execute_tool_with_retry(
                            session_v,
                            "write_audit_log",
                            {"event_type": "EMAIL_TRIAGE", "domain_type": email_domain_route, "details": audit_msg}
                        )

                    else:
                        if not looks_like_deal_request(triggers_output):
                            # 2026-08 production-readiness fix: previously this branch ran
                            # unconditionally for anything that wasn't type:email/type:whatsapp,
                            # so an unrelated file dropped into Inbox/ would silently be treated
                            # as a *completed* deal (mock invoice + draft social posts + a fake
                            # DEAL_COMPLETED audit entry). Raising here routes it through the
                            # existing Ralph Wiggum isolation path below (write_error_recovery_file
                            # + archive as failed) instead of fabricating business activity.
                            raise ValueError(
                                "Trigger is not type:email, type:whatsapp, or a recognizable "
                                "Customer/Discount deal request (no 'Customer:'/'Client:' or "
                                "explicit 'Discount: NN%' field found) -- refusing to silently "
                                "treat an unrecognized file as a completed sales deal."
                            )

                        # RAG Check on Vault Server
                        guidelines = await execute_tool_with_retry(session_v, "search_kb", {"query": "discount"})
                        print("📖 Guidelines loaded successfully.")

                        discount_rate = parse_discount_percentage(triggers_output)
                        customer = parse_customer_name(triggers_output)
                        email = parse_customer_email(triggers_output)
    
                        # Perform Domain Classification (Strict Separation)
                        domain_route = classify_domain(email)
                        print(f"📊 Extracted Details -> Customer: {customer}, Email: {email}, Discount Requested: {discount_rate}%")
                        print(f"💼 Cross-Domain Routing: Classified as [{domain_route}] segment.")
    
                        # 20% is the KB's autonomous-discount ceiling (knowledge_base.md §3,
                        # loyalty band) -- must match triage_email/triage_whatsapp's threshold
                        # so the same discount is decided the same way regardless of channel.
                        DISCOUNT_CEILING = 20.0
                        if discount_rate > DISCOUNT_CEILING:
                            print(f"🚨 Discount exceeds limit ({DISCOUNT_CEILING}%). Escalating to CEO...")
                            approval_content = (
                                f"# Escalation Alert\n\n"
                                f"- **Customer**: {customer}\n"
                                f"- **Email**: {email}\n"
                                f"- **Requested Discount**: {discount_rate}%\n"
                                f"- **Domain Route**: {domain_route}\n"
                            )
                            await execute_tool_with_retry(
                                session_v,
                                "write_approval_file",
                                {"filename": "escalation_deal.md", "content": approval_content, "folder": "Sales"}
                            )

                            status_table = (
                                "| Deal Identifier | Status | Action/Details | Error Attempts |\n"
                                "| :--- | :--- | :--- | :--- |\n"
                                f"| TRIGGER_test_deal | Halted | Over-discount limit ({discount_rate}%). Escalated. | 0 |\n"
                            )
                            await execute_tool_with_retry(
                                session_v, "write_dashboard_update",
                                {"status_table": status_table, "note": f"Escalated deal: {customer}"}
                            )
    
                            # Log audit event for escalation
                            audit_msg = f"Escalated deal of customer {customer} ({email}) with {discount_rate}% requested discount."
                            await execute_tool_with_retry(
                                session_v,
                                "write_audit_log",
                                {"event_type": "DEAL_ESCALATION", "domain_type": domain_route, "details": audit_msg}
                            )
    
                        else:
                            print("✅ Discount within boundaries. Routing tasks to specialized servers...")
    
                            # 1. Trigger Invoice on Session 2 (Odoo)
                            print("🛠️ Routing task: Create Odoo invoice...")
                            odoo_response = await execute_tool_with_retry(
                                session_o,
                                "create_odoo_invoice",
                                {
                                    "customer_name": customer,
                                    "discount_rate": discount_rate,
                                    "deal_value": 3500.0
                                }
                            )
                            print(f"🎯 Odoo Response: {odoo_response}")
    
                            # 2. Trigger Encryption on Session 1 (Vault)
                            print("🔐 Routing task: Cryptographic encryption...")
                            raw_payload = f"Customer: {customer}, Approved Discount: {discount_rate}%, Odoo Status: {odoo_response}"
                            encryption_response = await execute_tool_with_retry(
                                session_v,
                                "encrypt_sensitive_data",
                                {"payload": raw_payload}
                            )
                            print(f"🔒 Encryption Response: {encryption_response}")
    
                            # 3. Trigger Tweets & Facebook on Session 3 (Social)
                            print("🐦 Routing task: Twitter promo posting...")
                            promo_tweet = f"Excited to welcome our new partner {customer} on board! Exclusive enterprise onboarding initiated! 🚀"
                            twitter_response = await execute_tool_with_retry(
                                session_s,
                                "post_to_twitter",
                                {"tweet_text": promo_tweet}
                            )
                            print(f"🐦 Twitter Response: {twitter_response}")
    
                            print("👥 Routing task: Meta (FB & IG) posting...")
                            promo_meta = f"We are thrilled to announce our latest partnership with {customer}. Onboarding active! 🌟"
                            facebook_response = await execute_tool_with_retry(
                                session_s,
                                "post_to_meta",
                                {"platform": "facebook", "message": promo_meta}
                            )
                            instagram_response = await execute_tool_with_retry(
                                session_s,
                                "post_to_meta",
                                {"platform": "instagram", "message": promo_meta}
                            )
                            print(f"👥 Facebook Response: {facebook_response}")
                            print(f"📸 Instagram Response: {instagram_response}")
    
                            # Work-Zone Separation (Phase 2): social_server.py runs in the Cloud
                            # zone and drafts instead of publishing live (see CLOUD_ZONE flag in
                            # social_server.py). Reflect that honestly in the audit log/Dashboard
                            # rather than always claiming "Synced".
                            social_responses = (twitter_response, facebook_response, instagram_response)
                            social_drafted = any("Draft Created" in r for r in social_responses)
                            social_status_label = "Drafts Pending Local Approval" if social_drafted else "Twitter + Meta Synced"
    
                            # 4. Write Detailed JSON Audit Log on Session 1 (Vault)
                            print("📝 Routing task: Writing comprehensive JSON audit log...")
                            audit_msg = f"Approved discount of {discount_rate}%. Odoo invoice generated successfully. Encryption written to secure vault."
                            audit_response = await execute_tool_with_retry(
                                session_v,
                                "write_audit_log",
                                {
                                    "event_type": "DEAL_COMPLETED",
                                    "domain_type": domain_route,
                                    "details": audit_msg
                                }
                            )
                            print(f"📜 Audit System Response: {audit_response}")
    
                            # 5. Update Dashboard on Session 1 (Vault)
                            status_table = (
                                "| Deal Identifier | Status | Action/Details | Security State | Social Media Sync | Domain Route | Error Attempts |\n"
                                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                                f"| TRIGGER_test_deal | Completed | Approved. {odoo_response} | AES-256 GCM Saved | {social_status_label} | {domain_route} | 0 |\n"
                            )
                            await execute_tool_with_retry(
                                session_v, "write_dashboard_update",
                                {"status_table": status_table, "note": f"Deal completed: {customer}"}
                            )

                    # Trigger successfully handled (escalated or completed) - archive it so
                    # it is not re-read and reprocessed by claim_trigger next cycle.
                    archive_result = await execute_tool_with_retry(
                        session_v, "archive_processed_triggers", {"outcome": "success"}
                    )
                    print(f"🗄️ Trigger Archive: {archive_result}")

                except Exception as processing_error:
                    # Ralph Wiggum protocol: execute_tool_with_retry already exhausted its
                    # 3 automatic retries on the failing tool call. Isolate the payload and
                    # remove the trigger from Needs_Action so it doesn't loop-retry forever.
                    print(f"❌ [Ralph Wiggum Loop] Trigger processing failed after retries. Isolating payload. Error: {str(processing_error)}")
                    try:
                        error_details = (
                            f"Trigger content that failed processing:\n\n{triggers_output}\n\n"
                            f"Error: {str(processing_error)}"
                        )
                        recovery_result = await execute_tool_with_retry(
                            session_v, "write_error_recovery_file", {"details": error_details}
                        )
                        print(f"🧯 {recovery_result}")

                        archive_result = await execute_tool_with_retry(
                            session_v, "archive_processed_triggers", {"outcome": "failed"}
                        )
                        print(f"🗄️ Trigger Archive (failed): {archive_result}")
                    except Exception as cleanup_error:
                        print(f"🔥 Failed to isolate/archive trigger after processing error: {str(cleanup_error)}")

                # Compile Audit Report on Session 1 (Vault)
                print("📊 Routing task: Compiling Weekly Audit & CEO Briefing...")
                audit_res = await execute_tool_with_retry(session_v, "generate_weekly_audit", {})
                print(f"📈 Audit System Response: {audit_res}")

                print("🔄 Cycle complete. Waiting for next event...\n")
                await asyncio.sleep(10)

            except Exception as loop_error:
                print(f"🔥 Critical loop failure: {str(loop_error)}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(run_agent_loop())
    except KeyboardInterrupt:
        print("\nStopping Agent Loop gracefully.")
        sys.exit(0)