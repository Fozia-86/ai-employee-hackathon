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

VAULT_PATH = "/mnt/d/AI_Employee_Vault"
PYTHON_EXEC = "/mnt/d/ai_agent_env/bin/python3"

# Setup independent parameters for multiple servers
vault_params = StdioServerParameters(command=PYTHON_EXEC, args=["/mnt/d/AI_Employee_Vault/mcp_servers/vault_server.py"])
odoo_params = StdioServerParameters(command=PYTHON_EXEC, args=["/mnt/d/AI_Employee_Vault/mcp_servers/odoo_server.py"])
social_params = StdioServerParameters(command=PYTHON_EXEC, args=["/mnt/d/AI_Employee_Vault/mcp_servers/social_server.py"])

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
    return "sultan@gmail.com"  # Default fallback email

# === Domain Classification logic (Personal vs Business separation) ===
def classify_domain(email: str) -> str:
    personal_domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]
    domain = email.split("@")[-1].lower()
    if domain in personal_domains:
        return "Personal"
    return "Business"

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
                triggers_output = await execute_tool_with_retry(session_v, "monitor_triggers", {})
                
                if "No trigger files found" in triggers_output or not triggers_output.strip():
                    await execute_tool_with_retry(session_v, "generate_weekly_audit", {})
                    await asyncio.sleep(5)
                    continue
                    
                print("\n📥 New Trigger Identified!")
                print(triggers_output)
                
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
                
                if discount_rate > 15.0:
                    print("🚨 Discount exceeds limit (15%). Escalating to CEO...")
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
                        {"filename": "escalation_deal.md", "content": approval_content}
                    )
                    
                    status_table = (
                        "| Deal Identifier | Status | Action/Details | Error Attempts |\n"
                        "| :--- | :--- | :--- | :--- |\n"
                        f"| TRIGGER_test_deal | Halted | Over-discount limit ({discount_rate}%). Escalated. | 0 |\n"
                    )
                    await execute_tool_with_retry(session_v, "update_dashboard", {"status_table": status_table})
                    
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
                        f"| TRIGGER_test_deal | Completed | Approved. {odoo_response} | AES-256 GCM Saved | Twitter + Meta Synced | {domain_route} | 0 |\n"
                    )
                    await execute_tool_with_retry(session_v, "update_dashboard", {"status_table": status_table})
                    
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