"""
Outbound WhatsApp sender (Meta WhatsApp Business Cloud API) -- shared helper
used by:
  - pending_approval_notifier.py (pings the approver when a new item lands
    in Pending_Approval/)
  - web_gui/app.py's WhatsApp webhook handler (sends back a confirmation
    like "Approved: <file>" after processing an APPROVE/REJECT reply)

This module ONLY sends messages -- it never reads vault files itself.
Reuses the same WHATSAPP_CLOUD_API_TOKEN / WHATSAPP_PHONE_NUMBER_ID that
web_gui/vault_ops.py's get_channel_status() already checks for the Home
dashboard's WhatsApp channel row, so no new inbound credentials are needed --
just outbound *send* permission on the same Meta app/number.
"""
import os

import requests

GRAPH_API_VERSION = "v19.0"


def send_whatsapp_message(to_number: str, text: str) -> tuple[bool, str]:
    """Sends a plain text WhatsApp message via the Cloud API.

    Returns (ok, info) -- info is either "sent" or a human-readable error,
    never raises (callers are watcher/webhook code that must keep running
    even if a single send fails).
    """
    token = os.environ.get("WHATSAPP_CLOUD_API_TOKEN", "")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    if not token or not phone_id:
        return False, "WHATSAPP_CLOUD_API_TOKEN / WHATSAPP_PHONE_NUMBER_ID not configured in .env."
    if not to_number:
        return False, "No destination phone number given."

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text[:4096]},  # WhatsApp text message hard limit
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except Exception as e:
        return False, f"WhatsApp send failed (network error): {e}"

    if resp.status_code == 200:
        return True, "sent"
    return False, f"WhatsApp API error {resp.status_code}: {resp.text[:300]}"
