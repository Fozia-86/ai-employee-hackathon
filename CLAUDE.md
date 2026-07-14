# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This is **not a conventional software project** — it is an Obsidian vault that *is* an autonomous "AI Employee." Claude Code is the runtime: markdown files are the state/database, Python helper scripts are the tools, and the `obsidian_vault_manager` skill defines the business behavior. When you operate here you are the employee, not just editing code.

Two audiences read files: **you (Claude)** and the **Obsidian app** the human uses to view the vault. Preserve YAML frontmatter and `[[wikilink]]` references — Obsidian relies on them.

## Architecture: the file-flow state machine

Work moves through folders as a pipeline. The folder a file lives in *is* its status.

```
Inbox/  →  Needs_Action/  →  (Pending_Approval/ if HITL)  →  Approved/  →  Done/
```

- **`Inbox/`** — raw incoming tasks/files. `watcher.py` monitors this folder.
- **`Needs_Action/`** — triaged items awaiting Claude's analysis. `watcher.py` moves files here and drops a `TRIGGER_*.md` file (frontmatter `type: triage_trigger`) instructing Claude to analyze, plan, and update the Dashboard. Ambiguous tasks get a `CLARIFICATION_REQUIRED.md` note here.
- **`Pending_Approval/`** — actions requiring **Human-in-the-Loop (HITL)**: any financial transaction, external communication, or discount > 20%. Also the isolation target for failed error-recovery payloads (`error_recovery_[timestamp].md`).
- **`Approved/`** — human-cleared actions ready to execute.
- **`Done/`** (with `Financials/`, `Reference/` subfolders) — completed work, moved with a timestamped log entry. Financial files here must be encrypted.
- **`Logs/Security/`** — security audit trail (currently empty). Encryption events are also logged to the Dashboard's execution log.

Analysis output is written as a `PLAN_[ID].md` file (frontmatter `type: autonomous_plan`) citing the exact KB rule used and the HITL decision. See `Needs_Action/PLAN_2026_07_10_B.md` for the canonical example.

**Odoo integration:** `mcp_servers/odoo_server.py` uses Odoo's JSON-2 API (Odoo 19+) via HTTP + API-key auth (endpoints: `/json/2/<model>/<method>`, e.g. `/json/2/account.move/create`), migrated from XML-RPC on 2026-07-12. Auth is a bearer API key (`Authorization: bearer <ODOO_API_KEY>`); the database is selected via the `X-Odoo-Database` header when `ODOO_DB` is set. All calls use named arguments (JSON-2 does not support positional args) and errors are handled via HTTP status codes (4xx/5xx), not exceptions. See the [[Key Decisions]] note below for why XML-RPC was dropped.

## Governing documents (read these before acting)

- **`.claude/skills/obsidian_vault_manager/SKILL.md`** — the operational protocol: negotiation flow, post-execution encryption, weekly audit. This is the primary behavior spec. (Its internal skill `name:` is `gold_autonomous_employee`; the folder name is `obsidian_vault_manager`.)
- **`Company_Handbook.md`** — rules of engagement (Privacy First, HITL, communication tone, 5-minute triage SLA).
- **`Knowledge_Base/knowledge_base.md`** — "Enterprise Governance, Guardrails & Tier Specifications": system tiers, cross-domain routing boundaries, financial/operational rules (incl. the 20% discount ceiling and Odoo mutation guardrails), and the Ralph Wiggum error-recovery protocol. The single source of business truth; query it via `rag_search.py`, never hardcode rules. Note: it holds *governance rules*, not a pricing matrix or FAQ list — unit prices (e.g. the $5,000 Agentic Workflow Setup) appear only in artifacts like PLAN files, not the KB.
- **`Dashboard.md`** — live status board (`👑 ... [GOLD TIER]`): a status table (Last Sync / Tier / Total Runs / Success Rate / Status), Live System Health Metrics (Odoo MCP, Social Media MCP, Crypto Vault, Ralph Wiggum loop state), and Recent Execution Logs. **Update it after every significant action** (financials, security log, active autonomy plan). It has **no YAML frontmatter** — append log lines and edit the metric/status rows in place.

## Core business rule: the discount ceiling

The most important autonomous-authority boundary (from `knowledge_base.md` §3, Financial & Operational Rules):
- **Maximum autonomous discount = 20%** (loyalty band, for clients with >3 prior projects).
- **>20% = hard ceiling → always route to `Pending_Approval/` for HITL.** Never draft a >20% discount autonomously. At or below 20%, you may draft the reply autonomously and record the cited rule in the PLAN file.
- Related Odoo guardrail: no direct invoice deletion — only `Draft` or `Cancelled` states.

## Helper scripts (the "tools")

Run from the vault root. No `requirements.txt` exists; dependencies must be installed manually: `pip install watchdog rank-bm25 cryptography requests`.

```bash
# Semantic-ish KB lookup (BM25). Use this for every pricing/policy question.
python rag_search.py "15% loyalty discount 4th project"

# Encrypt a financial file (AES/Fernet). Renames to .enc in place.
python secure_vault.py --encrypt Done/Financials/INVOICE_C01.md

# Rotate an API key in .env (creates .env if absent)
python secure_vault.py --rotate GMAIL_API_TOKEN <new_key>

# Inbox watcher — moves new files to Needs_Action and creates a TRIGGER file.
# Uses PollingObserver for WSL/Windows filesystem compatibility.
python watcher.py
```

`gmail_watcher.py` and `linkedin_watcher.py` are currently **persistent stubs** — each just prints a banner and runs `while True: time.sleep(10)`. Placeholders for planned integrations (Gmail, LinkedIn/social), not functional. Odoo, by contrast, is wired: `mcp_servers/odoo_server.py` is a functional MCP server that creates draft invoices over the JSON-2 API (see the Architecture section), degrading to a sandbox/offline mock invoice ID when credentials are missing or the live call fails.

## Operating tier & recovery protocol

The vault runs in **Gold Tier** mode (per `knowledge_base.md` §1 and the Dashboard). Gold Tier claims full autonomous cross-domain execution: Odoo 19 accounting mutations, live social posting (X & Meta), automated recovery loops, and weekly CEO audits. In practice these integrations are still stubs (see above) — treat the tier as the *intended* authority envelope, not currently-wired capability.

The **Ralph Wiggum error-recovery protocol** (`knowledge_base.md` §4) governs fault handling: network drops / 5xx / 429 get a 3-step automatic retry; if all retries fail, run the fallback tool, isolate the payload to `Pending_Approval/error_recovery_[timestamp].md`, and downgrade gracefully. The Dashboard tracks a "Ralph Wiggum Loop State".

## Encryption & keys

- `secure_vault.py` auto-generates `.vault_key` (Fernet) on first run if missing. This key is required to decrypt anything in `Done/Financials/`. Do not delete or overwrite it.
- Per SKILL.md, any file with bank statements, invoices, or revenue data moved to `Done/Financials/` must be encrypted via `secure_vault.py --encrypt`, then the encryption timestamp logged on the Dashboard.

## Conventions

- Most state-changing files carry YAML frontmatter with at least `type:` — match the existing patterns (`triage_trigger`, `autonomous_plan`, `documentation`, `email`). `status:` is common but not universal (PLAN files instead use `decision:` / `hitl_required:`). The `Dashboard.md` is the deliberate exception: no frontmatter.
- Timestamps use ISO-ish format (`2026-07-10T18:19:00Z` or `2026-07-10`); PLAN IDs follow `PLAN_YYYY_MM_DD_[suffix]`.
- Cross-reference related files with Obsidian `[[wikilinks]]` (e.g. Dashboard links to the active `[[PLAN_...]]`).

## Note on rag_search.py

The file contains a stray heredoc-duplication artifact (an `EO` marker mid-file and a second copy of the script). The functional definition is the second copy; the module still imports and runs. If editing, collapse it to a single clean definition.

## Key Decisions

- **Odoo — JSON-2 over XML-RPC (2026-07-12):** did not keep XML-RPC because in Odoo 19+ both XML-RPC (`/xmlrpc`, `/xmlrpc/2`) and legacy JSON-RPC (`/jsonrpc`) are deprecated with removal scheduled (targeted for Odoo 20, fall 2026). JSON-2 (`POST /json/2/<model>/<method>`, bearer API-key auth, named-only arguments) is the current recommended external API, so `mcp_servers/odoo_server.py` was migrated to it. The `create_odoo_invoice` MCP tool signature was kept identical so `agent_runner.py` needed no change. See the Architecture section for the wiring details.
