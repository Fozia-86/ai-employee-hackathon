# SKILL: Live Channel Dispatcher

## Purpose
This skill governs direct outbound execution across communication channels (Gmail, WhatsApp, Social Media, Odoo payments) following human approval via the Web GUI.

## Safety & Execution Boundaries
1. **Draft-Only Cloud Boundary Override**:
   - When a draft in `Pending_Approval/` receives explicit human approval via the Web API, trigger the corresponding channel sender.
2. **WhatsApp Direct Sender**:
   - Manage Playwright / Web session state securely inside `.whatsapp_session/`.
   - Provide automated chat locator and message dispatch while handling anti-detection delays and retry logic.
3. **Social Media Live Publishing**:
   - Execute live posts to X (Twitter) and Meta (Facebook/Instagram) via `mcp_servers/social_server.py` when explicitly authorized.
4. **Audit Logging & Encryption**:
   - Every live send action must write an audit record to `Audit_Logs/audit_log.json`.
   - Financial payment actions must encrypt completed files into `Done/Financials/` via `secure_vault.py`.
