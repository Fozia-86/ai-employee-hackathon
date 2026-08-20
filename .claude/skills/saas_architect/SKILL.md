# SKILL: SaaS Commercial Architect

## Purpose
This skill governs the conversion of the single-tenant Obsidian AI Employee vault into a multi-tenant, containerized commercial SaaS backend.

## Architecture & Code Requirements
1. **No Hardcoded Paths**: Replace all VM-specific hardcoded paths (such as `/home/ubuntu/ai-employee-hackathon` or hardcoded `venv` interpreters) with dynamic environment lookups:
   - `VAULT_PATH = os.environ.get("VAULT_PATH", os.getcwd())`
   - `PYTHON_EXEC = sys.executable`
2. **State Machine Preservation**: Maintain the core folder pipeline structure without breaking existing triggers or logs:
   `Inbox/` -> `Needs_Action/` -> `In_Progress/` -> `Pending_Approval/` -> `Approved/` -> `Done/`
3. **Containerization**:
   - Create a production `Dockerfile` installing Python 3.10+, Playwright dependencies, and system utilities.
   - Create `docker-compose.yml` configured to isolate tenant vaults, environment files, and session states.
4. **Environment Isolation**: Ensure `.env` and `.vault_key` are loaded per tenant container and never exposed or shared across tenant boundaries.
