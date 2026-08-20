"""Live Gmail send — Local zone only.

Provides send_email_reply(), which sends a real, threaded Gmail reply.
Shares credentials/token handling with gmail_watcher.py (same token.json,
now requires the gmail.send scope in addition to gmail.readonly -- see
SCOPES in gmail_watcher.py). Never imported/called from any Cloud-zone
code path (agent_runner.py, vault_server.py, social_server.py) --
send_approved_emails.py is the only caller, and it hard-gates on
EXECUTION_ZONE=local before touching this module.
"""
import base64
import json
import logging
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from gmail_watcher import SCOPES, CREDENTIALS_FILE, TOKEN_FILE, OAUTH_PORT

VAULT_PATH = Path(__file__).resolve().parent


def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        granted_scopes = set(json.loads(TOKEN_FILE.read_text()).get("scopes", []))
        if set(SCOPES).issubset(granted_scopes):
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except Exception as e:
                # Same fallback fix as gmail_watcher.py's get_gmail_service() --
                # a revoked/expired-beyond-refresh token must not crash the
                # caller, it should fall through to a fresh consent flow.
                logging.info(f"Token refresh failed ({e}) -- falling back to full re-consent.")
        if not refreshed:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=OAUTH_PORT, open_browser=True)
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def send_email_reply(to_addr: str, original_subject: str, reply_body: str,
                      gmail_thread_id: str = "", gmail_rfc_message_id: str = "") -> str:
    """Sends a real Gmail reply. Threads it into the original conversation
    when gmail_thread_id/gmail_rfc_message_id are available (Gmail-sourced
    drafts); otherwise sends as a standalone 'Re:' message (drafts from
    non-Gmail/dummy sources, which carry no thread info)."""
    service = get_gmail_service()

    subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"

    msg = EmailMessage()
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(reply_body)

    if gmail_rfc_message_id:
        msg["In-Reply-To"] = gmail_rfc_message_id
        msg["References"] = gmail_rfc_message_id

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body = {"raw": raw}
    if gmail_thread_id:
        body["threadId"] = gmail_thread_id

    sent = service.users().messages().send(userId="me", body=body).execute()
    return sent["id"]
