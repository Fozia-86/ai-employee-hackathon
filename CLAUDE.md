# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

This is **not a conventional software project** — it is an Obsidian vault that *is* an autonomous "AI Employee." Claude Code is the runtime: markdown files are the state/database, Python helper scripts are the tools, and the `obsidian_vault_manager` skill defines the business behavior. When you operate here you are the employee, not just editing code.

Two audiences read files: **you (Claude)** and the **Obsidian app** the human uses to view the vault. Preserve YAML frontmatter and `[[wikilink]]` references — Obsidian relies on them.

## Architecture: the file-flow state machine

Work moves through folders as a pipeline. The folder a file lives in *is* its status.

```
Inbox/  →  Needs_Action/  →  In_Progress/cloud-agent/ (claimed)  →  (Pending_Approval/ if HITL)  →  Approved/  →  Done/
```

- **`Inbox/`** — raw incoming tasks/files. `watcher.py` monitors this folder.
- **`Needs_Action/`** — triaged items awaiting Claude's analysis. `watcher.py` moves files here and drops a `TRIGGER_*.md` file (frontmatter `type: triage_trigger`) instructing Claude to analyze, plan, and update the Dashboard. Ambiguous tasks get a `CLARIFICATION_REQUIRED.md` note here. Has `Sales/`/`Support/`/`General/` domain subfolders (Requirement 3a) for structure/manual filing — `watcher.py`/`gmail_watcher.py` still drop new triggers flat in the root by design (domain isn't knowable until the content is parsed downstream), `claim_trigger()` scans root + one subfolder level so a manually-filed item still gets picked up.
- **`In_Progress/cloud-agent/`** — claim-by-move staging (Requirement 2, 2026-07-19). `vault_server.py`'s `claim_trigger()` tool (replacing the old `monitor_triggers()`, which is still defined but no longer polled) moves a `TRIGGER_*.md` file (and its `original_file:` sibling) here *before* `agent_runner.py` processes it, so a trigger is claimed exactly once. If `agent_runner.py` crashes mid-cycle, the next `claim_trigger()` call re-serves whatever's stranded here instead of re-scanning `Needs_Action/` (durable claim, not lost work) — but see Known Constraints for the corollary risk. This also fixes the pre-existing "multiple unrelated triggers in `Needs_Action/` get processed as one blob" limitation, since only one trigger is ever claimed per cycle now.
- **`Pending_Approval/`** — actions requiring **Human-in-the-Loop (HITL)**: any financial transaction, external communication, or discount > 20%. Has `Sales/`/`Support/`/`General/` domain subfolders (Requirement 3a) — `triage_email` routes by its `category` (`business_inquiry`→`Sales/`, `support`→`Support/`), `write_approval_file` (deal escalations) defaults to `Sales/`, `write_social_draft` (no category concept) always uses `General/`. `write_error_recovery_file` (failed-trigger isolation) is the one exception and stays flat in the `Pending_Approval/` root — guessing its domain risks hiding a failure from whoever triages it. Also the isolation target for failed error-recovery payloads (`error_recovery_[timestamp].md`) and, since Phase 2, social-media drafts (`social_draft_<platform>_<timestamp>.md`, frontmatter `type: social_draft`) written by the Cloud zone for the Local zone to send. Reviewed with `python review_approvals.py` (see Helper scripts below), which recurses into the domain subfolders (as of Requirement 3a) and only moves files to `Approved/`/`Rejected/` (flat, no domain split) and edits frontmatter — it never fires a live send/post/Odoo call itself.
- **`Approved/`** — human-cleared actions ready to execute.
- **`Rejected/`** — human-declined actions, moved here by `review_approvals.py` with `decision: rejected` + `reason:` added to frontmatter. Terminal state; nothing currently reads back out of this folder.
- **`Done/`** (with `Financials/`, `Reference/` subfolders) — completed work, moved with a timestamped log entry. Financial files here must be encrypted.
- **`Logs/Security/`** — security audit trail (currently empty). Encryption events are also logged to the Dashboard's execution log.

Analysis output is written as a `PLAN_[ID].md` file (frontmatter `type: autonomous_plan`) citing the exact KB rule used and the HITL decision. See `Needs_Action/PLAN_2026_07_10_B.md` for the canonical example.

**Odoo integration:** `mcp_servers/odoo_server.py` uses Odoo's JSON-2 API (Odoo 19+) via HTTP + API-key auth (endpoints: `/json/2/<model>/<method>`, e.g. `/json/2/account.move/create`), migrated from XML-RPC on 2026-07-12. Auth is a bearer API key (`Authorization: bearer <ODOO_API_KEY>`); the database is selected via the `X-Odoo-Database` header when `ODOO_DB` is set. All calls use named arguments (JSON-2 does not support positional args) and errors are handled via HTTP status codes (4xx/5xx), not exceptions. See the [[Key Decisions]] note below for why XML-RPC was dropped.

## Work-Zone Separation (Phase 2, 2026-07-16)

The deployment is split into two zones by *machine*, not by Personal/Business classification (that's a separate, older concept — see `classify_domain` in `agent_runner.py`):
- **Cloud zone** — this VM. May only ever **draft** outbound content (social posts, and in future email replies). Must never call a live send/publish API.
- **Local zone** — a separate machine (not yet set up) that will review drafts in `Pending_Approval/` and perform the actual send/publish after human approval. Anything sensitive (WhatsApp session, payments/banking) is Local-only and never touches the Cloud zone.

Enforcement so far (Cloud-side only; Local zone setup is future work): `mcp_servers/social_server.py` gates `post_to_twitter`/`post_to_meta` behind a `CLOUD_ZONE` flag (env var, read once at import). When `CLOUD_ZONE` is true — **which is also the default when the variable is missing/unset, by design, so a deleted `.env` line fails safe rather than fails open** — both tools skip their live API call entirely and instead write a `social_draft_<platform>_<timestamp>.md` file to `Pending_Approval/` (frontmatter `type: social_draft`) and return a "Draft Created" confirmation string. Only an explicit `CLOUD_ZONE=false` (intended for the Local zone machine) re-enables the original live-call code path. `mcp_servers/agent_runner.py`'s Dashboard/audit-log write for the social-posting step now inspects the tool responses for `"Draft Created"` and labels the status `Drafts Pending Local Approval` instead of unconditionally claiming `Twitter + Meta Synced`.

No email-send function exists anywhere in the codebase yet (`gmail_watcher.py` is still the inert stub described below) — when one is built, it must be gated the same draft-only way before it can be considered Cloud-zone-safe. `triage_email` (see Completed Phases) already follows this pattern against dummy data, ahead of that real integration landing. As of 2026-07-17 `triage_email` is also wired into the live `Inbox/ → watcher.py → monitor_triggers → agent_runner.py` pipeline (see Completed Phases, "Email trigger dispatch wired into agent_runner.py") — it fires automatically for any inbox file with `type: email` frontmatter, not just via the standalone `test_email_triage.py` script. Still dummy/local-file data only — no real Gmail connection.

**Known limitation:** `CLOUD_ZONE` (like `TWITTER_BEARER_TOKEN`, `META_PAGE_ACCESS_TOKEN`, etc.) is set in the root `.env`, but `social_server.py` only calls `load_dotenv()` against `mcp_servers/.env` (which doesn't exist), and `agent_runner.py` spawns it via `StdioServerParameters` without an explicit `env=` dict — MCP's stdio transport therefore only passes a minimal safe-inherited environment, not the parent's full `os.environ`. In practice this means none of these vars currently reach the subprocess by any path, and the code's `CLOUD_ZONE` default (safe/draft-only) is what actually governs behavior today, not the `.env` value. This is pre-existing behavior (the same gap already made Twitter/Meta live-posting unreachable before Phase 2, and separately makes Odoo credentials unreachable the same way — Odoo already tolerates this via its documented sandbox/mock-invoice fallback). Wiring real env passthrough is deferred until the Local zone machine is actually set up and needs to flip `CLOUD_ZONE=false` for real.

## Governing documents (read these before acting)

- **`.claude/skills/obsidian_vault_manager/SKILL.md`** — the operational protocol: negotiation flow, post-execution encryption, weekly audit. This is the primary behavior spec. (Its internal skill `name:` is `gold_autonomous_employee`; the folder name is `obsidian_vault_manager`.)
- **`.claude/skills/saas_architect/SKILL.md`**, **`.claude/skills/gui_bridge_manager/SKILL.md`**, **`.claude/skills/live_channel_dispatcher/SKILL.md`** — the 3-phase SaaS-commercialization plan (dynamic multi-tenant paths, the Web GUI, then live outbound sending), plus **`.claude/skills/saas_commercial_suite/SKILL.md`**, the same 3 phases merged into one file. All 3 phases are now implemented — see the "SaaS commercialization skills..." and "Phase 3 — Live Outbound Execution Engines..." entries under Completed Phases below.
- **`Company_Handbook.md`** — rules of engagement (Privacy First, HITL, communication tone, 5-minute triage SLA).
- **`Knowledge_Base/knowledge_base.md`** — "Enterprise Governance, Guardrails & Tier Specifications": system tiers, cross-domain routing boundaries, financial/operational rules (incl. the 20% discount ceiling and Odoo mutation guardrails), and the Ralph Wiggum error-recovery protocol. The single source of business truth; query it via `rag_search.py`, never hardcode rules. Note: it holds *governance rules*, not a pricing matrix or FAQ list — unit prices (e.g. the $5,000 Agentic Workflow Setup) appear only in artifacts like PLAN files, not the KB.
- **`Dashboard.md`** — live status board (`👑 ... [GOLD TIER]`): a status table (Last Sync / Tier / Total Runs / Success Rate / Status), Live System Health Metrics (Odoo MCP, Social Media MCP, Crypto Vault, Ralph Wiggum loop state), and Recent Execution Logs. **Update it after every significant action** (financials, security log, active autonomy plan). It has **no YAML frontmatter** — append log lines and edit the metric/status rows in place. **Single-writer as of Requirement 3b (2026-07-19): `agent_runner.py` (Cloud) never writes this file directly anymore** — its old `update_dashboard()` calls were a full-file overwrite (`open(..., 'w')`) that silently destroyed the `## Recent Execution Log` section `review_approvals.py`/`send_approved_emails.py` append to, every time a trigger was processed. It now calls `write_dashboard_update()` instead, which drops small signal files into `Updates/`; only the Local-only `merge_dashboard.py` (`EXECUTION_ZONE=local`-gated) actually writes `Dashboard.md`, merging those signals in additively and archiving them to `Updates/Merged/`. `review_approvals.py`/`send_approved_emails.py` are unaffected — they still append directly, since they're Local-zone scripts, not the thing this fix targets.

## Core business rule: the discount ceiling

The most important autonomous-authority boundary (from `knowledge_base.md` §3, Financial & Operational Rules):
- **Maximum autonomous discount = 20%** (loyalty band, for clients with >3 prior projects).
- **>20% = hard ceiling → always route to `Pending_Approval/` for HITL.** Never draft a >20% discount autonomously. At or below 20%, you may draft the reply autonomously and record the cited rule in the PLAN file.
- Related Odoo guardrail: no direct invoice deletion — only `Draft` or `Cancelled` states.

## Helper scripts (the "tools")

Run from the vault root. No `requirements.txt` exists; dependencies must be installed manually: `pip install watchdog rank-bm25 cryptography requests anthropic python-dotenv "mcp>=1.2,<2" google-auth google-auth-oauthlib google-api-python-client fastapi uvicorn jinja2 python-multipart itsdangerous requests-oauthlib`. The 5 Web GUI ones (`fastapi`/`uvicorn`/`jinja2`/`python-multipart`/`itsdangerous`) were added in Phase 4 (`itsdangerous` backs the session-cookie login); `requests-oauthlib` was added in Phase 5 (backs the real Twitter OAuth1 posting fix). **`mcp` must be pinned `<2`** — `mcp==2.0.0` lacks the `mcp.server.fastmcp` submodule every MCP server in this repo imports; a dev checkout that picks up bare `pip install mcp` will silently get an unusable version until this constraint is applied (this happened for real during Phase 5 testing, see Completed Phases).

```bash
# Interactive HITL reviewer for Pending_Approval/. Lists each file's frontmatter
# type + a short body summary, then prompts [a]pprove/[r]eject/[s]kip/[q]uit per
# file. Approve adds decision: approved + reviewed_at: <ts> to frontmatter and
# moves the file to Approved/; reject prompts for a reason, adds decision:
# rejected + reviewed_at + reason, and moves it to Rejected/. Never calls any
# live send/post/Odoo API -- file moves and frontmatter edits only.
python review_approvals.py
python review_approvals.py --approve <filename>
python review_approvals.py --reject <filename> --reason "..."

# Semantic-ish KB lookup (BM25). Use this for every pricing/policy question.
python rag_search.py "15% loyalty discount 4th project"

# Encrypt a financial file (AES/Fernet). Renames to .enc in place.
python secure_vault.py --encrypt Done/Financials/INVOICE_C01.md

# Rotate an API key in .env (creates .env if absent)
python secure_vault.py --rotate GMAIL_API_TOKEN <new_key>

# Inbox watcher — moves new files to Needs_Action and creates a TRIGGER file.
# Uses PollingObserver for WSL/Windows filesystem compatibility.
python watcher.py

# Local-only. Merges Updates/*.md signal files (written by agent_runner.py's
# write_dashboard_update tool) into Dashboard.md -- the only thing that writes
# Dashboard.md's status table. Appends to the Recent Execution Log rather than
# overwriting it, then archives consumed files to Updates/Merged/. Refuses to
# run unless EXECUTION_ZONE=local (same fail-safe pattern as
# send_approved_emails.py). See Work-Zone Separation / Requirement 3b.
EXECUTION_ZONE=local python merge_dashboard.py
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

## Extended documentation & history

Detailed session-by-session history — every "Completed Phase" (feature build-outs, bug fixes, verification steps) and the incident log — has been moved to **[docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)** to keep this file focused on current, load-bearing rules. Consult it when you need:
- The full build history/rationale behind a specific feature (e.g. why Odoo uses JSON-2, how the Web GUI's CSRF protection works, the WhatsApp Cloud API ingestion details).
- Past incidents and the process fixes that came out of them (e.g. the accidental-send incident and the resulting `send_approved_emails.py` safeguards).
- Verification/testing notes for features already marked complete above.

This file (`CLAUDE.md`) stays the source of truth for **current** architecture, active rules, and helper-script usage — if something here conflicts with `docs/PROJECT_CONTEXT.md`, this file wins (it's the one kept up to date going forward).

## Current Architecture State

- **Runtime host:** Ubuntu VM at `/home/ubuntu/ai-employee-hackathon` (this repo root doubles as the vault root — there is no separate `vault/` subfolder; `Inbox/`, `Needs_Action/`, `Pending_Approval/`, `Approved/`, `Done/` all live directly here). **Public IP: `139.185.47.189`** (Oracle Cloud instance; needed for SSH tunneling to the Syncthing GUI and similar remote-access tasks — confirmed 2026-07-16).
- **`mcp_servers/agent_runner.py`** (2026-07-15): hardcodes `VAULT_PATH = "/home/ubuntu/ai-employee-hackathon"` and `PYTHON_EXEC = "/home/ubuntu/ai-employee-hackathon/venv/bin/python3"`, used as the interpreter and working paths for spawning the Vault, Odoo, and Social Media MCP server subprocesses. Previously these pointed at stale WSL paths (`/mnt/d/AI_Employee_Vault`, `/mnt/d/ai_agent_env/bin/python3`) left over from a prior Windows/WSL environment; migrated when the project moved to this Ubuntu VM. Verified working: all three subprocesses spawn and connect without crashing.
- **`mcp_servers/vault_server.py`** (fixed 2026-07-15): also had a hardcoded stale `VAULT_PATH = "/mnt/d/AI_Employee_Vault"` (line 16) missed during the earlier `agent_runner.py` migration — this silently broke `monitor_triggers` (always globbed a nonexistent directory, always returned "No trigger files found") and every other vault-server tool (`update_dashboard`, `write_audit_log`, `encrypt_sensitive_data`, `generate_weekly_audit`, `write_approval_file`). Found while verifying the Inbox→Needs_Action pipeline end-to-end; corrected to `/home/ubuntu/ai-employee-hackathon`. `mcp_servers/local_vault_mcp.py` still has the same stale path but is dead code (never spawned by `agent_runner.py`) — left as-is.
- **`.env`** does not define `PYTHON_EXEC` or `VAULT_PATH` — those are Python constants in `agent_runner.py`/`vault_server.py`, not environment-driven.
- **Inbox ingestion is fully wired end-to-end:** `watcher.py` runs as its own systemd service (`agent-watcher.service`, see Completed Phases) and moves new `Inbox/` files into `Needs_Action/` + writes a `TRIGGER_*.md` file; `agent_runner.py`'s `monitor_triggers` tool (via `vault_server.py`) picks those up on its polling loop, processes them, then archives them via `archive_processed_triggers` (see Completed Phases) so each trigger is handled exactly once. `agent-runner.service` runs Python with `-u` (unbuffered stdout) so `/var/log/agent-runner.log` reflects `print()` output in real time instead of being stuck in a block-I/O buffer.

## Known Constraints

- `.env`/`.vault_key` kabhi sync nahi hone chahiye — `.stignore` isay enforce karta hai, delete na karna. Yeh gap pehle mila tha (2026-07-17), ab fix ho chuka hai dono zones mein.
- **`EXECUTION_ZONE` confirmed final (2026-07-17):** VM (Cloud) = `"cloud"`, Local machine = `"local"`. Set manually in each machine's own `.env` — not synced via Syncthing (per `.stignore`, see above), so both zones must be updated by hand if this value ever changes.
- **Approval sirf Local machine se honi chahiye Requirement 7 ke mutabiq** — VM par `review_approvals.py` chala kar mechanics test karna theek hai (jaisa 2026-07-17 ko hua aur turant revert kiya gaya), lekin asal demo/approval Local zone se hi complete karna hai, taake HITL boundary (Cloud = draft-only, Local = approve+send) waqai enforce ho, na ke sirf documented rahe.
- `PYTHON_EXEC` and `VAULT_PATH` in `mcp_servers/agent_runner.py`, and `VAULT_PATH` in `mcp_servers/vault_server.py`, must point at Ubuntu VM paths (`/home/ubuntu/...`) — if this project ever migrates off this VM (e.g. back to a WSL/Windows environment or a different host), these constants must be re-verified and updated manually; they are not read from `.env` or auto-detected.
- `monitor_triggers` still reads all `Needs_Action/TRIGGER_*.md` files as one concatenated blob per cycle, and `agent_runner.py` parses discount/customer/email from that blob with a single regex pass (first match wins) — if multiple *unrelated* triggers are ever in `Needs_Action/` at the same moment (race between watcher pickup and agent_runner's poll), they get processed together as one deal rather than individually, though each is still archived (see below) so no infinite reprocessing occurs. True per-file isolation would require a further refactor to loop over trigger files individually, which is out of scope for the duplicate-prevention fix below.
- **`venv/` must not be shared between Local and Cloud/VM via Syncthing (found 2026-07-18):** the Local (WSL) machine's `venv/` was a Syncthing-synced copy of the Cloud VM's venv — built there against Python 3.10, but on the Local machine `venv/bin/python3` is a symlink to the system's `/usr/bin/python3` (3.12), and `pip`'s own scripts carry a stale `/home/ubuntu/...` shebang. The result: the venv looked populated (packages like `mcp`, `dotenv`, `cryptography`, `watchdog` were visible under `venv/lib/python3.10/site-packages/`) but were invisible to the interpreter actually being invoked, which reads `venv/lib/python3.12/site-packages/` instead — a near-empty directory. Symptoms were confusing: some imports worked (whatever had been pip-installed directly against the 3.12 interpreter, e.g. the Gmail API libs) while others failed with `ModuleNotFoundError` for packages that visibly existed elsewhere in the same `venv/` tree. Worked around per-package by running `venv/bin/python3 -m pip install <pkg>` (bootstrapping `pip` itself first via `python3 -m ensurepip`, since the venv's `pip` script itself was unusable) to install directly against the interpreter that's actually running. **Each machine (Local and Cloud/VM) must maintain its own separately-built `venv/`** — never let Syncthing carry the `venv/` folder across machines; install locally from `requirements.txt` (or the manual package list in Helper scripts above, until one exists) on each machine instead. `venv/` is already `.gitignore`d; if it isn't already in `.stignore` too, it should be added so Syncthing stops carrying it.

## Key Decisions

- **Odoo — JSON-2 over XML-RPC (2026-07-12):** did not keep XML-RPC because in Odoo 19+ both XML-RPC (`/xmlrpc`, `/xmlrpc/2`) and legacy JSON-RPC (`/jsonrpc`) are deprecated with removal scheduled (targeted for Odoo 20, fall 2026). JSON-2 (`POST /json/2/<model>/<method>`, bearer API-key auth, named-only arguments) is the current recommended external API, so `mcp_servers/odoo_server.py` was migrated to it. The `create_odoo_invoice` MCP tool signature was kept identical so `agent_runner.py` needed no change. See the Architecture section for the wiring details.


## Next Steps / Pending

- ~~ACTIVE — deploy the 2026-08-11 pre-deployment code-review fixes to production before onboarding a real client.~~ — **resolved 2026-08-11, same day:** Syncthing had already carried all 3 fixed files (`mcp_servers/odoo_server.py`, `mcp_servers/agent_runner.py`, `web_gui/app.py`+`web_gui/templates/rules.html`) to the Cloud VM by the time of the deploy check — a byte-for-byte `diff` against the VM's copies confirmed identical content before restarting anything. `agent-runner.service` was restarted on the Cloud VM (all 3 MCP subprocesses reconnected cleanly, polling loop resumed, no errors in the journal) and `ai-employee-webgui.service` was restarted on the Local machine (clean startup, `/login` returns `200`). Both services are now running the fixed code. See `docs/PROJECT_CONTEXT.md`'s "Pre-deployment code review" entry.
- **Requirement 5 (Odoo full 24/7 HTTPS+backup deployment) — NOT implemented, deliberate scope decision (2026-07-21):** self-hosting Odoo Community (server + PostgreSQL) on this VM was investigated and rejected as unsafe. Resource check: `free -h` showed only ~353Mi available RAM, with the system already dipping ~300Mi into swap under normal load from the existing 24/7 processes (`agent-runner.service`, `agent-watcher.service`, Syncthing, 3 MCP subprocesses). Odoo Community's own minimum guidance (~2GB for Odoo alone) plus a separate PostgreSQL instance would very likely push this VM into OOM/swap-thrashing, risking the already-working, verified live pipeline (Requirement 7: real Gmail triage → HITL approval → live send). Deploying a standalone Odoo server with HTTPS (reverse proxy/Certbot), backups, and health monitoring requires a separate/larger VM — out of scope here. **This does not affect the existing Odoo MCP integration**: `mcp_servers/odoo_server.py` (draft invoice creation/cancellation/payment recording via the JSON-2 API, see Architecture section) is already functional against a hosted Odoo Online (`*.odoo.com`) instance with real credentials in `.env` — only a self-hosted Odoo Community *server deployment* is what's being deferred.
- ~~Unlabeled orphaned key 'aero_live_...' in `.env`~~ — **resolved 2026-07-17:** user confirmed removal, key permanently deleted from `.env`. No code path referenced it (previously verified via grep).
- ~~Requirement 7 — only remaining gap: actual send/publish via MCP.~~ — **resolved 2026-07-19:** `gmail_sender.py` + `send_approved_emails.py` built (Local zone only, gated on `EXECUTION_ZONE=local`) and verified with 4 real live sends via the Gmail API, receipt confirmed by the user in the actual inbox. See Completed Phases, "REQUIREMENT 7 — FULLY COMPLETE INCLUDING LIVE SEND". Requirement 7 is now fully closed end-to-end. Remaining lower-priority items: (1) the `CLOUD_ZONE`/credential env-passthrough gap noted above, still unfixed but now moot for email since the Local-zone send path doesn't depend on it, (2) decide whether Odoo invoice creation and WhatsApp/payments also need explicit zone gating or are already sufficiently isolated (WhatsApp/payments are specified Local-only by design and not yet implemented anywhere), (3) equivalent live-post tooling for social media (Twitter/Meta) is still undone — only email send was in scope for Requirement 7.
- ~~`gmail_watcher.py` query scope must be narrowed before production/unattended use~~ — **resolved and live-verified 2026-07-18:** original `is:unread is:important` scope (63 false positives, see Completed Phases) replaced with `is:unread subject:(proposal OR inquiry OR pricing OR quote OR "interested in") newer_than:1d` + a `maxResults=5` cap per poll. An intermediate keyword-OR-without-`subject:` attempt also produced 64 false positives (see Completed Phases, "First rework attempt") before landing on the current `subject:`-scoped query; a state-save kill-safety bug found during that testing was also fixed (see Completed Phases). Final clean test confirmed exactly 1 message matched for 1 controlled dummy email, and a full Cloud-side pipeline test (also 2026-07-18, see Completed Phases) confirmed that message flows correctly all the way to a `Pending_Approval/` draft.
- **Architecture confirmed (2026-07-18): `agent_runner.py` does not need to run on the Local machine at all.** `gmail_watcher.py` (Local) has exactly one job — detect matching Gmail messages and write them into `Needs_Action/` in the same `type: email` shape `agent_runner.py` already expects. Triage/draft-generation (`triage_email`, dashboard/audit-log writes, archiving) is always performed by the Cloud VM's `agent_runner.py`, which already runs 24/7 as `agent-runner.service` (see Completed Phases, systemd deployment) — Syncthing carries the newly-written `Needs_Action/` files to the VM automatically, where the VM's own `monitor_triggers` poll loop picks them up. No local/manual triage-processing step is needed, and the 2026-07-18 scratchpad-script pipeline test (see Completed Phases) was purely a one-off verification that the dispatch *logic* behaves correctly against a real Gmail message — not a sign that `agent_runner.py` needs to become runnable locally. The `VAULT_PATH`/`PYTHON_EXEC` hardcoded-to-VM constants noted in Known Constraints are therefore correct as they are and do not need to become dynamic for this flow to work.
