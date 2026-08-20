---
type: task_list
created: 2026-08-10
updated: 2026-08-11
status: active
---

# Next Tasks — Single-Client Production Readiness

Context: on 2026-08-11 the business owner decided to pivot away from the multi-tenant SaaS roadmap for now and focus on making this vault fully production-ready as a **single-business automation engine** (for the owner's own business, or one first client) — the ~75-80% mature core, not the ~25-30% mature multi-tenant layer. The old multi-tenant plan (multi-tenancy, billing, self-serve onboarding, hosted infra) is kept below as **Parked**, not deleted — revisit once single-client operation is solid and there's a reason to resell.

## Step 1 — Reliability & stability pass — DONE (2026-08-11)
- [x] `agent-runner.service` / `agent-watcher.service` (Cloud VM) both confirmed `active`, `NRestarts=0`, zero errors in the last 24h journal.
- [x] Health-check watchdog re-run manually, clean (exit 0, no new warning).
- [x] Syncthing was found **dead on both machines** (Local + Cloud VM) — started and enabled on both; pairing reconfirmed `connected: true` via the Syncthing REST API.
- [x] **Real bug found and fixed**: Cloud VM's own `.env` still had the *old, invalid* `ODOO_API_KEY` (`.env` is never synced between zones by design — Known Constraints) — the 2026-08-10 Odoo fix only updated the Local machine's `.env`. Updated the Cloud VM's `.env` to the correct key; direct probe now returns `200` live.
- [x] `Needs_Action/`, `In_Progress/cloud-agent/`, `Pending_Approval/` all checked — clean, no stuck triggers or stale drafts.
- [ ] Ralph Wiggum error-recovery path (`write_error_recovery_file`) — not re-tested this pass, still pending if desired.

## Step 2 — Security basics — DONE (2026-08-11)
- [x] `.env` / `.vault_key` confirmed excluded from git (`.gitignore`) and Syncthing (`.stignore`), and confirmed **not** tracked by git (`git ls-files` — clean).
- [x] Full `.env` read-through on both machines: Local `.env` had a harmless stray `.` line (leftover from earlier editing) — removed. Cloud VM `.env` scanned line-by-line, clean (no malformed lines).
- [x] `WEB_ADMIN_PASSWORD`/`WEB_GUI_PASSWORD` checked — both already strong/random generated values, not the `changeme-admin` demo default. No rotation needed.
- [ ] **`.vault_key` has no backup anywhere** — still needs the business owner to copy it to a password manager, USB, or another safe location outside this vault. Losing it makes everything in `Done/Financials/` permanently unrecoverable. (Cannot be done by Claude — needs a human to pick a storage location.)

## Step 3 — Approval workflow polish — DONE (2026-08-11)
- [x] Decided: **Web GUI** is the daily-use approval flow (business owner's choice over CLI `review_approvals.py`).
- [x] Full login flow re-verified live: session login (`WEB_ADMIN_PASSWORD`) + HTTP Basic (`WEB_GUI_USERNAME`/`WEB_GUI_PASSWORD`, both stacked, both required since real values are set) → `/`, `/approvals`, `/health`, `/rules` all `200`.
- [x] CSRF-protected reject flow tested end-to-end with a synthetic draft (never a real item) — draft rendered correctly with its platform badge (📘 Facebook), reject with a scraped real CSRF token succeeded (`303`), file moved to `Rejected/` correctly. Test artifact deleted afterward, no leftover `Updates/`/audit-log residue.
- [x] `Pending_Approval/` confirmed empty (real queue) — nothing stale to clear.
- [x] **New**: Web GUI was only ever started manually before — set up as a proper systemd `--user` service (`ai-employee-webgui.service`, `Restart=always`, enabled for auto-start on boot, same pattern as the Syncthing service) so it's always available at `http://127.0.0.1:8899` without needing a terminal open.

## Step 4 — Channel confirmation
- [x] Twitter — live and verified (2026-08-10).
- [x] Facebook — live and verified.
- [x] Gmail — live and verified (Requirement 7).
- [x] Odoo — live and verified (2026-08-10).
- [x] WhatsApp — confirmed end-to-end (2026-08-11): webhook handshake (`GET /webhooks/whatsapp`, correct/wrong token), synthetic incoming-message POST correctly created `Needs_Action/whatsapp_*.md`+`TRIGGER_*.md`, `triage_whatsapp()` correctly drafted a business-inquiry reply (10% discount, `AUTONOMOUS_APPROVED`) and correctly escalated a 35% discount request (`ESCALATION_REQUIRED`) even with the Claude API temporarily overloaded (rule-based fallback caught it). Draft-only, no auto-send, as designed. All test artifacts cleaned up on both machines.
- [~] Instagram — explicitly skipped for now (2026-08-11 decision). No IG Business account linked in Meta Business Suite. Revisit later if needed.
- [~] LinkedIn — parked (2026-08-10 decision), blocked on LinkedIn Developer Portal "Share on LinkedIn" product approval.

## Parked — Multi-tenant SaaS roadmap (revisit later, not now)
- [ ] Multi-tenant data isolation (separate credentials/data per customer).
- [ ] Billing/subscription collection (Stripe/Paddle) for SaaS customers.
- [ ] Self-serve signup & onboarding flow.
- [ ] Hosted, scalable infrastructure (move off manual Cloud-VM + Local-WSL pairing).
- [ ] Formal third-party security audit for multi-customer scale.

## Already resolved (2026-08-10) — no action needed
- Twitter — OAuth2 credentials had been mistakenly placed in OAuth1 fields; regenerated correctly, `GET /2/users/me` now returns 200 for @naadvion.
- Odoo — `ODOO_URL` was missing entirely from `.env`; added, plus a freshly-rotated `ODOO_API_KEY`; `search_count` now returns 200.
- WhatsApp webhook handshake, Web GUI login/CSRF/approval flow, `triage_email`/`triage_whatsapp` discount-ceiling enforcement — all re-verified live and working correctly.
