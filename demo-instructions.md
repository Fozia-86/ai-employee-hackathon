Ab poore steps, exact commands ke saath:

Step 1 — Dependencies install karein

Apni real machine (yeh WSL terminal, jahan aapke pass sudo access hai) pe:

cd /mnt/d/AI_Employee_Vault
venv/bin/python3 -m pip install playwright anthropic python-dotenv cryptography
venv/bin/python3 -m playwright install chromium --with-deps
--with-deps sudo password mangega — yahan wahi sandbox nahi hai, aap password de sakte hain, isliye yeh chal jayega (mere sandbox mein isi wajah se fail hua tha).

Step 2 — API key confirm karein

mcp_servers/.env mein ANTHROPIC_API_KEY already save hai (humne pichle step mein kiya tha) — dobara check karne ki zarurat nahi.

Step 3 — WhatsApp Watcher chalayein

venv/bin/python3 whatsapp_watcher.py
- Ek browser window khulegi.
- Agar purana session (.whatsapp_session/) abhi bhi valid hai to direct login ho jayega (QR nahi lagega).
- Agar session expire ho chuka hai to QR code dikhega — apne phone se scan karein: WhatsApp app → Settings/⋮ → Linked Devices → Link a Device.
- Login hone ke baad terminal mein "WhatsApp Watcher started. Polling every 60s." dikhega — isay chalta rehne dein.

Step 4 — Test message bhejwayein

Apne hi number pe (ya kisi dost se) ek WhatsApp message bhejwayein jisme business keyword ho — jaise:

▎ "Hi, we need a quote for bulk order, can you offer a discount?"

(keywords list: invoice, pricing, quote, quotation, proposal, order, payment, urgent, interested in, support, issue, help, discount)

~60 second ke andar terminal mein yeh line dikhegi:
New WhatsApp business message from '...' -> whatsapp_....md

Step 5 — Watcher ko rok dein (Ctrl+C)

Ek hi terminal use kar rahe hain to watcher ko rok dein taake agla step chal sake. (Agar do terminals use karein to rokne ki zarurat nahi.)

Step 6 — Claude se draft banayein

venv/bin/python3 process_whatsapp_triggers.py
Yeh naya script maine abhi banaya hai — yeh detect huay message ko Claude-enhanced classify kare ga aur agar business-relevant hai to draft bana dega. Output kuch aisa dikhega:
Processing whatsapp_xxxx.md from 'Your Contact Name'...
  -> Draft Created [business_inquiry, decision=AUTONOMOUS_APPROVED]: saved to .../Pending_Approval/Sales/whatsapp_draft_....md for Local zone approval.

Step 7 — Client ko dikhayein

Generated file Pending_Approval/Sales/whatsapp_draft_*.md (ya Support//General/) ko Obsidian mein khol kar dikhayen — usme labeling_method, ai_reasoning, aur ready-made draft reply dikhega.

Step 8 (optional) — Approval flow bhi dikhayein

venv/bin/python3 review_approvals.py
Isse full HITL flow bhi demo ho jayega — approve karte hi file Approved/ mein chali jayegi.

---Yaad rahe: yeh sirf draft banata hai, WhatsApp pe khud reply send nahi karta — approved reply aapko khud copy kar ke WhatsApp pe bhejni hogi (jaisa humne pehle discuss kiya, auto-send abhi scope mein nahi hai).

✻ Sautéed for 1m 32s

※ recap: Goal: add Claude-enhanced labeling and full WhatsApp triage to the AI Employee vault for a client demo. Just gave you exact steps to run whatsapp_watcher.py and process_whatsapp_triggers.py locally; next step is you sending a test WhatsApp message to try it. (disable recaps in /config)
