import base64
import json
import logging
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

VAULT_PATH = Path(__file__).resolve().parent
NEEDS_ACTION_PATH = VAULT_PATH / "Needs_Action"
CREDENTIALS_FILE = VAULT_PATH / "credentials.json"
TOKEN_FILE = VAULT_PATH / "token.json"
STATE_FILE = NEEDS_ACTION_PATH / ".gmail_processed_ids.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
QUERY = 'is:unread subject:(proposal OR inquiry OR pricing OR quote OR "interested in") newer_than:1d'
POLL_INTERVAL_SECONDS = 60
OAUTH_PORT = 8080
MAX_RESULTS_PER_POLL = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")


def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        # Credentials.from_authorized_user_file(path, scopes=SCOPES) sets
        # .scopes to the *requested* SCOPES, not what's actually stored in
        # the file -- so a stale token missing a newly added scope (e.g.
        # gmail.send) would otherwise look "valid" and never trigger
        # re-consent. Compare against the file's raw granted scopes instead.
        granted_scopes = set(json.loads(TOKEN_FILE.read_text()).get("scopes", []))
        if set(SCOPES).issubset(granted_scopes):
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        else:
            logging.info("Cached token.json is missing a newly required scope -- forcing re-consent.")

    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except Exception as e:
                # A revoked/expired-beyond-refresh token (e.g. "invalid_grant:
                # Token has been expired or revoked") used to propagate straight
                # out of this function and crash the caller instead of falling
                # back to a fresh consent flow -- found while re-authenticating
                # after a real revoked-token incident. Fall through to the full
                # OAuth flow below instead.
                logging.info(f"Token refresh failed ({e}) -- falling back to full re-consent.")
        if not refreshed:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            # Web-type OAuth client: this exact redirect URI must be registered
            # in Google Cloud Console under the client's Authorized redirect URIs.
            creds = flow.run_local_server(port=OAUTH_PORT, open_browser=True)
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def load_processed_ids():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_processed_ids(ids):
    STATE_FILE.write_text(json.dumps(sorted(ids)))


def extract_email_body(payload):
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        body = extract_email_body(part)
        if body:
            return body

    return None


def fetch_and_process_new_emails(service, processed_ids):
    results = service.users().messages().list(userId="me", q=QUERY, maxResults=MAX_RESULTS_PER_POLL).execute()
    messages = results.get("messages", [])

    new_count = 0
    for m in messages:
        msg_id = m["id"]
        if msg_id in processed_ids:
            continue

        full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in full["payload"].get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        sender = headers.get("From", "unknown@example.com")
        message_id_header = headers.get("Message-ID", headers.get("Message-Id", ""))
        thread_id = full.get("threadId", msg_id)
        body = extract_email_body(full["payload"]) or full.get("snippet", "")

        inbox_filename = f"gmail_{msg_id}.md"
        (NEEDS_ACTION_PATH / inbox_filename).write_text(
            f"---\ntype: email\nfrom: {sender}\nsubject: {subject}\n"
            f"gmail_message_id: {msg_id}\ngmail_thread_id: {thread_id}\n"
            f"gmail_rfc_message_id: \"{message_id_header}\"\n"
            f"received_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n---\n\n"
            f"{body.strip()}\n"
        )

        trigger_filename = f"TRIGGER_gmail_{msg_id}.md"
        (NEEDS_ACTION_PATH / trigger_filename).write_text(
            f"---\ntype: triage_trigger\noriginal_file: {inbox_filename}\n"
            f"detected_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\nstatus: pending\n---\n\n"
            f"Claude, a new email `{inbox_filename}` has arrived from Gmail. "
            f"Please analyze this file, update the Dashboard, and create a plan."
        )

        logging.info(f"New email pulled from Gmail: {subject!r} from {sender} -> {inbox_filename}")
        processed_ids.add(msg_id)
        save_processed_ids(processed_ids)
        new_count += 1

    return new_count


if __name__ == "__main__":
    NEEDS_ACTION_PATH.mkdir(exist_ok=True)

    logging.info("Authenticating with Gmail...")
    service = get_gmail_service()
    processed_ids = load_processed_ids()
    logging.info(f"Gmail Watcher started. Polling every {POLL_INTERVAL_SECONDS}s for query: {QUERY!r}")

    try:
        while True:
            try:
                n = fetch_and_process_new_emails(service, processed_ids)
                if n:
                    logging.info(f"Processed {n} new email(s).")
            except HttpError as e:
                logging.error(f"Gmail API error: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logging.info("Stopping")
