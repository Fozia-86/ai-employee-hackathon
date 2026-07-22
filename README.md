# AI Employee — Autonomous Vault-Based Business Agent

An Obsidian vault that *is* an autonomous "AI Employee." Markdown files are the
state/database, Python helper scripts are the tools, an MCP server orchestrates
the loop, and Claude (via the `obsidian_vault_manager` skill) is the runtime
that reasons over it. Work items move through the vault as files moving
between folders — the folder a file lives in **is** its status.

## 1. Overview

This project implements a semi-autonomous "employee" that:

- Watches an inbox (local files today, real Gmail as of this build) for
  incoming work (sales inquiries, support requests, deal/discount asks).
- Triages and classifies each item against a Knowledge Base of governance
  rules (discount ceilings, Odoo mutation guardrails, escalation policy).
- Drafts a response/action autonomously when it's within its authority
  (e.g. ≤20% discount), and routes to a Human-in-the-Loop (HITL) approval
  queue when it isn't (e.g. >20% discount, any financial transaction, any
  external send/post).
- Executes the approved action for real: **live Gmail send**, sandboxed
  Odoo draft invoices/payments, and drafted (never live) social posts.
- Logs every action to an audit trail and a live status Dashboard, and
  encrypts financial records at rest.

**Tier reached: Platinum.** Requirement 7, the platinum minimum passing
gate (email arrives → triage → draft → HITL approval → live send → `Done/`),
has been run **end-to-end against real data** (a real Gmail account, a real
approval on the Local machine, a real Gmail API send with confirmed
receipt) — not just fixtures. Most other requirements (domain routing,
claim-by-move, single-writer Dashboard, WhatsApp detection, sandboxed
payments) are also complete. A small number of items are explicitly
stubbed or deferred — see §7.

## 2. Architecture

### Zones

The deployment is split into two zones **by machine**, not by business
domain:

- **Cloud zone** (a VM, always-on) — may only ever **draft** outbound
  content. It classifies, drafts, and files things into `Pending_Approval/`.
  It must never call a live send/publish API. Enforced by a `CLOUD_ZONE`
  flag that **defaults to safe/draft-only when unset**, so a missing
  config fails safe, not open.
- **Local zone** (a separate machine, human-operated) — reviews drafts and
  performs the actual send/publish/payment after human approval. Anything
  sensitive (WhatsApp session, Gmail send, banking/payments) only ever runs
  here, gated by `EXECUTION_ZONE=local` (also fails safe if unset/wrong).

### File-flow state machine

The folder a file lives in *is* its status:

```
Inbox/  →  Needs_Action/  →  In_Progress/cloud-agent/ (claimed)  →  [Pending_Approval/ if HITL]  →  Approved/  →  Done/
                                                                              │
                                                                              └→ Rejected/ (terminal)
```

### High-level data flow

```
                         ┌───────────────────────── CLOUD ZONE (VM, 24/7) ─────────────────────────┐
                         │                                                                          │
  Real Gmail ──poll──▶  gmail_watcher.py (LOCAL, see below) ──Syncthing sync──▶  Needs_Action/       │
                         │                                                          │                │
  Local file drop ──▶ watcher.py ───────────────────────────────────────────▶  Needs_Action/          │
                         │                                                          │                │
                         │                                            claim_trigger() (durable claim)│
                         │                                                          ▼                │
                         │                                          In_Progress/cloud-agent/          │
                         │                                                          │                │
                         │                                agent_runner.py: triage / classify /        │
                         │                                KB lookup / draft (Odoo sandbox, social      │
                         │                                drafts, email drafts)                        │
                         │                                                          │                │
                         │                              within authority?  ──yes──▶ auto-complete      │
                         │                                      │no                     │             │
                         │                                      ▼                       ▼             │
                         │                              Pending_Approval/     write_dashboard_update()  │
                         │                             (Sales/Support/General)   → Updates/*.md         │
                         └──────────────────────────────────────┬───────────────────────┬──────────────┘
                                                                 │                       │
                         ┌───────────────────── LOCAL ZONE (human machine) ──────────────┴──────────────┐
                         │                                                                               │
                         │  review_approvals.py  ──approve/reject──▶  Approved/ | Rejected/               │
                         │        │                                                                       │
                         │        ▼                                                                       │
                         │  send_approved_emails.py  ──live Gmail API send──▶  Done/ (sent:true)            │
                         │  process_approved_payments.py ──Odoo sandbox payment──▶ Done/Financials/ (.enc)  │
                         │        │                                                                       │
                         │  merge_dashboard.py (EXECUTION_ZONE=local) merges Updates/*.md into Dashboard.md │
                         └───────────────────────────────────────────────────────────────────────────────┘
```

Odoo integration (`mcp_servers/odoo_server.py`) uses the JSON-2 API
(bearer API-key auth), with a sandbox/mock fallback when credentials are
absent or a live call fails — this is what lets the payments/invoice flow
be demoed safely without a production Odoo instance.

## 3. Setup Instructions

### 3.1 Prerequisites (both machines)

- Python 3.10+ (each machine must build its **own** `venv/` — never sync
  `venv/` between machines via Syncthing; interpreter/ABI mismatches will
  silently break imports)
- `pip install watchdog rank-bm25 cryptography requests google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client`
  (no `requirements.txt` exists yet — install manually)

```bash
python3 -m venv venv
source venv/bin/activate
pip install watchdog rank-bm25 cryptography requests \
    google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

### 3.2 Cloud VM setup (always-on orchestrator)

1. Clone this repo to the VM (this repo root **is** the vault root — no
   separate `vault/` subfolder).
2. Verify/update the hardcoded paths in `mcp_servers/agent_runner.py`
   (`VAULT_PATH`, `PYTHON_EXEC`) and `mcp_servers/vault_server.py`
   (`VAULT_PATH`) to match this machine — these are **not** read from
   `.env` and must be edited by hand if the host changes.
3. Create `.env` in the repo root (see §3.4 for variables). Set
   `EXECUTION_ZONE=cloud` and leave `CLOUD_ZONE` unset or `true`.
4. Run `watcher.py` and `mcp_servers/agent_runner.py` as long-running
   services (this deployment uses systemd units — `agent-watcher.service`
   and `agent-runner.service` — `Restart=always`, unbuffered `-u` output).
5. Install Syncthing (used to sync `Needs_Action/`, `Pending_Approval/`,
   `Approved/`, etc. between this VM and the Local machine) and pair it
   with the Local machine's device ID via the Syncthing GUI (tunnel the
   GUI over SSH — it's bound to `127.0.0.1` only, by design).

### 3.3 Local machine setup (human-operated, sends/approves)

1. Clone/sync the same vault (via Syncthing, paired with the VM above).
2. Build a local `venv/` the same way as §3.1 — do not reuse a synced one.
3. Place a Google Cloud OAuth **Web application** client's `credentials.json`
   in the repo root (register `http://localhost:8080/` as an authorized
   redirect URI). Running `gmail_watcher.py` once will open a browser
   consent flow and cache a token to `token.json`.
4. Set `EXECUTION_ZONE=local` in this machine's own `.env` (not synced —
   each machine's `.env` is excluded from sync via `.stignore` and must be
   set by hand).
5. Run `gmail_watcher.py` (Gmail polling → `Needs_Action/`) and, as needed,
   `python review_approvals.py` (interactive HITL review),
   `python send_approved_emails.py` (live Gmail send), and
   `python process_approved_payments.py` (sandbox payment execution).

### 3.4 Environment variables (`.env`, no real secrets committed)

```
EXECUTION_ZONE=cloud|local        # which machine this .env belongs to
CLOUD_ZONE=true|false             # true (or unset) = draft-only; false = live-post allowed
ODOO_URL=                         # Odoo JSON-2 API base URL (optional — sandbox fallback if unset)
ODOO_API_KEY=                     # Odoo bearer API key (optional)
ODOO_DB=                          # Odoo database name, if multi-db instance
TWITTER_BEARER_TOKEN=             # optional, Local zone only, gated by CLOUD_ZONE
META_PAGE_ACCESS_TOKEN=           # optional, Local zone only, gated by CLOUD_ZONE
GMAIL_API_TOKEN=                  # rotated via secure_vault.py, not required for OAuth flow
```

`credentials.json` and `token.json` (Gmail OAuth) and `.vault_key`
(Fernet encryption key) are **never** committed — see §5.

## 4. Features Implemented

| Tier | Feature | Status |
|---|---|---|
| Bronze | Inbox → Needs_Action triage pipeline, TRIGGER files | ✅ Complete, live (systemd) |
| Bronze | Knowledge Base RAG lookup (`rag_search.py`, BM25) | ✅ Complete |
| Silver | Discount-ceiling enforcement (20% autonomous cap → HITL above) | ✅ Complete |
| Silver | Odoo draft-invoice creation (JSON-2 API + sandbox fallback) | ✅ Complete |
| Silver | Encryption of financial records (`secure_vault.py`, Fernet) | ✅ Complete |
| Silver | Ralph Wiggum error-recovery (retry → isolate to `Pending_Approval/`) | ✅ Complete |
| Gold | Social media drafting (Twitter/Meta), Cloud-zone draft-only gating | ✅ Complete (drafts only — no live post, by design) |
| Gold | Email triage + drafted replies | ✅ Complete |
| Gold | Domain subfolders (Sales/Support/General), claim-by-move, single-writer Dashboard | ✅ Complete |
| Gold | Interactive HITL reviewer (`review_approvals.py`) | ✅ Complete |
| Platinum | **Real Gmail ingestion** (OAuth, live inbox polling, narrowed query) | ✅ Complete, verified against real mail |
| Platinum | **Live Gmail send** (`gmail_sender.py` + `send_approved_emails.py`) | ✅ Complete — 4+ real emails sent, receipt confirmed |
| Platinum | **Requirement 7 full chain**, real data at every step | ✅ Verified end-to-end (Gmail → Local detect → Sync → Cloud triage/draft → Local approve → live send → `Done/`) |
| Platinum | WhatsApp unread-chat detection (`whatsapp_watcher.py`) | ✅ Detection working (selector fixed 2026-07-21); known limitation: only the latest message per chat is visible via WhatsApp Web's DOM |
| Platinum | Payments/banking, sandbox-only | ✅ Complete — simulated gateway confirmation → Odoo `account.payment`, Local-zone gated, HITL-approved via `Pending_Approval/Sales/` |

**Explicitly out of scope / not attempted:** a real banking/payment
gateway integration (sandbox only, by design — see §7); live social-media
posting (drafts only, by design, per the Cloud/Local zone split).

## 5. Security Disclosure

- **No secrets are committed.** `.gitignore` excludes `.env`, `.vault_key`,
  `credentials.json`, `token.json`, and `venv/`. `.stignore` additionally
  prevents these from ever being carried between machines by Syncthing —
  `.env`/`.vault_key` are deliberately machine-local and must be set up
  independently on each zone.
- **Fail-safe defaults.** Both `CLOUD_ZONE` (Cloud draft-only gate) and
  `EXECUTION_ZONE` (Local live-action gate) default to the *safe* state
  when unset or misconfigured — a deleted env line fails closed, not open.
  Live-action scripts (`send_approved_emails.py`,
  `process_approved_payments.py`) additionally refuse to run at all
  outside `EXECUTION_ZONE=local` and refuse non-interactive/piped stdin,
  after an incident (see below) where piped input caused a live send to
  land on the wrong (fortunately fictional) recipient.
- **HITL approval is mandatory** for any financial transaction, external
  communication, or discount above the 20% autonomous ceiling — these are
  never auto-executed; they are written as drafts to `Pending_Approval/`
  and require an explicit human `approve` via `review_approvals.py` before
  a send/payment script will touch them.
- **Financial records are encrypted at rest.** Anything moved to
  `Done/Financials/` is encrypted via `secure_vault.py` (Fernet/AES) before
  it's considered "done."
- **Credentials are never passed to subprocess MCP servers today** — a
  known, documented gap (`social_server.py`/`odoo_server.py` env
  passthrough) that currently means those live-API paths are unreachable
  regardless of `.env` content; this is called out explicitly rather than
  silently relied upon.

## 6. Tier Declaration

**Platinum Tier — most requirements complete, Requirement 7 (minimum
passing gate) fully verified with real data.**

The full chain — real Gmail message arrives → Local `gmail_watcher.py`
detects it → Syncthing syncs the trigger to the Cloud VM → the
already-running `agent_runner.py` triages and drafts a reply →
`review_approvals.py` on the Local machine performs a genuine human
approval → `send_approved_emails.py` sends a real, live reply via the
Gmail API → the task lands in `Done/` with a confirmed `gmail_sent_message_id`
— has been run start to finish with no manual triggering and no fixture
data standing in for a real step.

## 7. Known Limitations / Future Work

- **Odoo is sandbox/mock-fallback only, not a full 24/7 production
  deployment.** The VM used for this project is resource-constrained;
  standing up a persistent Odoo instance behind HTTPS with backups was
  judged out of scope for the hackathon timeline. `odoo_server.py`
  degrades gracefully to a mock invoice/payment ID when Odoo credentials
  are absent or a live call fails, which is what all current
  testing/demo runs against.
- **Domain-folder migration is forward-only.** The `Sales/`/`Support/`/
  `General/` subfolder convention (Requirement 3a) applies to files
  created after that change; pre-existing flat files in `Needs_Action/`
  and `Pending_Approval/` were deliberately left in place rather than
  retroactively migrated.
- **`CLOUD_ZONE`/Odoo credential env-passthrough gap.** MCP subprocess
  servers are spawned without the parent's full environment, so
  `CLOUD_ZONE`, Twitter/Meta tokens, and Odoo credentials don't currently
  reach `social_server.py`/`odoo_server.py` by any path — the *code's*
  safe-default behavior is what actually governs today, not `.env`
  content. Deferred until real credential wiring is needed.
- **Live social-media posting is not implemented** — only drafting, by
  design (Cloud zone may never post live); an equivalent Local-zone
  "approve and post" tool (mirroring `send_approved_emails.py`) does not
  yet exist.
- **WhatsApp watcher only sees the latest unread message per chat** — an
  inherent limitation of WhatsApp Web's DOM (only one message preview per
  chat row), not a bug in the selector logic.
- **Multi-agent trigger isolation is partial.** `claim_trigger()` claims
  one trigger file per cycle (fixing the earlier "multiple triggers
  processed as one blob" issue), but a crash between claim and archive
  during extended downtime leaves a trigger invisible to a human browsing
  `Needs_Action/` in Obsidian until the service restarts — acceptable for
  a single-agent deployment, would need hardening for multi-agent use.
