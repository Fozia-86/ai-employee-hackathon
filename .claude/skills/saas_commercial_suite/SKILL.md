# SKILL: SaaS Commercial Suite

## Purpose
This skill governs the full transformation of the single-tenant Obsidian AI Employee vault into a multi-tenant, containerized commercial SaaS product — architecture/refactoring, the web GUI/API bridge that replaces manual CLI tools, and live outbound channel execution after human approval. It merges three sub-domains (architecture, GUI bridge, live dispatch) into one complete skill.

---

## Part 1 — Commercial Architecture (multi-tenant, containerized backend)

### Architecture & Code Requirements
1. **No Hardcoded Paths**: Replace all VM-specific hardcoded paths (such as `/home/ubuntu/ai-employee-hackathon` or hardcoded `venv` interpreters) with dynamic environment lookups:
   - `VAULT_PATH = os.environ.get("VAULT_PATH", os.getcwd())`
   - `PYTHON_EXEC = sys.executable`
2. **State Machine Preservation**: Maintain the core folder pipeline structure without breaking existing triggers or logs:
   `Inbox/` -> `Needs_Action/` -> `In_Progress/` -> `Pending_Approval/` -> `Approved/` -> `Done/`
3. **Containerization**:
   - Create a production `Dockerfile` installing Python 3.10+, Playwright dependencies, and system utilities.
   - Create `docker-compose.yml` configured to isolate tenant vaults, environment files, and session states.
4. **Environment Isolation**: Ensure `.env` and `.vault_key` are loaded per tenant container and never exposed or shared across tenant boundaries.

---

## Part 2 — GUI & API Bridge Manager (web dashboard, replaces manual CLI)

### Purpose
Governs the creation and maintenance of the Web Dashboard, REST API endpoints, and Visual Setup Wizard that replace manual CLI tools (`review_approvals.py`, manual `.env` edits) for non-technical users.

### Operating Guidelines
1. **Web-Based HITL Approval Center**:
   - Provide API endpoints / UI triggers to list, review, approve, and reject items in `Pending_Approval/` (including subfolders `Sales/`, `Support/`, `General/`).
   - Standardize visual cards for `email_draft`, `whatsapp_draft`, `social_draft`, and `payment_request`.
   - Ensure approval actions update frontmatter with `decision: approved` and `reviewed_at` timestamps without corrupting markdown structures.
2. **Visual Onboarding & Setup Wizard**:
   - API endpoints to configure Gmail OAuth, Odoo Credentials (`ODOO_URL`, `ODOO_DB`, `ODOO_API_KEY`), Meta/Twitter API tokens, and WhatsApp session QR streams.
3. **Dashboard Single-Writer Rule**:
   - Respect Requirement 3b: Web endpoints must write status signals to `Updates/` and let the dashboard consolidation engine merge signals cleanly into `Dashboard.md`.

---

## Part 3 — Live Channel Dispatcher (outbound execution after approval)

### Purpose
Governs direct outbound execution across communication channels (Gmail, WhatsApp, Social Media, Odoo payments) following human approval via the Web GUI.

### Safety & Execution Boundaries
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
