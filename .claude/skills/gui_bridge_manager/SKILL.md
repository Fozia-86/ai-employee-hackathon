# SKILL: GUI & API Bridge Manager

## Purpose
This skill governs the creation and maintenance of the Web Dashboard, REST API endpoints, and Visual Setup Wizard that replace manual CLI tools (`review_approvals.py`, manual `.env` edits) for non-technical users.

## Operating Guidelines
1. **Web-Based HITL Approval Center**:
   - Provide API endpoints / UI triggers to list, review, approve, and reject items in `Pending_Approval/` (including subfolders `Sales/`, `Support/`, `General/`).
   - Standardize visual cards for `email_draft`, `whatsapp_draft`, `social_draft`, and `payment_request`.
   - Ensure approval actions update frontmatter with `decision: approved` and `reviewed_at` timestamps without corrupting markdown structures.
2. **Visual Onboarding & Setup Wizard**:
   - API endpoints to configure Gmail OAuth, Odoo Credentials (`ODOO_URL`, `ODOO_DB`, `ODOO_API_KEY`), Meta/Twitter API tokens, and WhatsApp session QR streams.
3. **Dashboard Single-Writer Rule**:
   - Respect Requirement 3b: Web endpoints must write status signals to `Updates/` and let the dashboard consolidation engine merge signals cleanly into `Dashboard.md`.
