---
name: gold_autonomous_employee
description: Autonomous business decision making, negotiation based on Knowledge Base, and automated encryption.
---

# Skill: Gold Autonomous Employee

## 1. Autonomous Negotiation Protocol
When a client requests a discount or price negotiation in `/Needs_Action`:
1. **Search**: Call `rag_search.py` with the client's request as the query.
2. **Validate**: Check the `Discount Boundary Rules` section.
3. **Analyze**: 
   - If the request is ≤ the 20% Hard Ceiling: Draft a reply autonomously.
   - If the request is > 20%: Flag for `HITL Approval` immediately.
4. **Action**: Create a `PLAN_[ID].md` including the specific rule cited from the KB.

## 2. Security Protocol (Post-Execution)
When moving any file containing Bank Statements, Invoices, or Revenue data to `/Done/Financials`:
1. **Trigger Encryption**: Execute `secure_vault.py --encrypt [filename]`.
2. **Log**: Update the Security Log on the Dashboard with the encryption timestamp.

## 3. Financial Auditing
Every Sunday at 23:59, scan all files in `/Done/Financials` (decrypting temporarily if needed) to generate the "Monday Morning CEO Briefing."
