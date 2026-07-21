"""
Local-zone WhatsApp watcher (Requirement 2b -- Local owns the WhatsApp session).

Uses a Playwright persistent browser context so the WhatsApp Web login
(QR-code scan) only has to happen once -- session data (cookies/localStorage)
is cached on disk under SESSION_DIR and reused on every subsequent run,
exactly like gmail_watcher.py caches its OAuth token in token.json.

First run: launches a *visible* Chromium window and waits for you to scan
the QR code with your phone (WhatsApp > Linked Devices > Link a Device).
Every run after that reuses the saved session and skips the QR step,
unless the session is invalidated (e.g. manually logged out from the phone,
or WhatsApp's own multi-week inactivity expiry).

Deliberately reads only the chat-list preview text for each unread chat --
never opens a chat -- so this watcher never sends read receipts (blue
ticks) to the sender. Only a lightweight scan of what's already unread.

WhatsApp Web's DOM/selectors are not a documented, versioned API (unlike
the Gmail API gmail_watcher.py uses) and can shift with WhatsApp UI
updates. If the watcher stops finding unread chats after a WhatsApp Web
update, the selectors in find_unread_chats() are the first place to check.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

VAULT_PATH = Path(__file__).resolve().parent
NEEDS_ACTION_PATH = VAULT_PATH / "Needs_Action"
SESSION_DIR = VAULT_PATH / ".whatsapp_session"
STATE_FILE = NEEDS_ACTION_PATH / ".whatsapp_processed_ids.json"

BUSINESS_KEYWORDS = [
    "invoice", "pricing", "quote", "quotation", "proposal", "order",
    "payment", "urgent", "interested in", "support", "issue", "help",
    "discount",
]

POLL_INTERVAL_SECONDS = 60
HEADLESS = os.environ.get("WHATSAPP_HEADLESS", "false").lower() == "true"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")


def load_processed_ids():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_processed_ids(ids):
    STATE_FILE.write_text(json.dumps(sorted(ids)))


def make_message_id(chat_name, preview_text):
    digest = hashlib.sha256(f"{chat_name}|{preview_text}".encode("utf-8")).hexdigest()
    return digest[:16]


def contains_business_keyword(text):
    lowered = text.lower()
    return any(keyword in lowered for keyword in BUSINESS_KEYWORDS)


def wait_for_login(page):
    logging.info("Opening WhatsApp Web...")
    page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

    chat_list = page.locator('div[aria-label="Chat list"]')
    qr_canvas = page.locator("canvas[aria-label], div[data-ref] canvas")

    logging.info("Waiting for either a QR code or an already-logged-in chat list...")
    while True:
        if chat_list.count() > 0:
            logging.info("Already logged in -- reusing saved session.")
            return
        if qr_canvas.count() > 0:
            logging.info(
                "QR code displayed. Scan it now with your phone: "
                "WhatsApp app -> Settings -> Linked Devices -> Link a Device."
            )
            chat_list.wait_for(state="visible", timeout=0)
            logging.info("Login detected -- session saved for future runs.")
            return
        time.sleep(1)


def find_unread_chats(page):
    """Return [(chat_name, preview_text)] for chats with unread messages.

    Reads only the chat-list row (name + last-message preview) -- never
    opens the chat -- so the sender never sees a read receipt.
    """
    rows = page.locator('div[aria-label="Chat list"] div[role="row"]')
    unread = []

    for i in range(rows.count()):
        row = rows.nth(i)
        unread_badge = row.locator('span[aria-label*="unread message"]')
        if unread_badge.count() == 0:
            continue

        titled_spans = row.locator("span[title]")
        span_count = titled_spans.count()
        if span_count == 0:
            continue

        chat_name = titled_spans.nth(0).get_attribute("title") or "(unknown chat)"
        preview_text = titled_spans.nth(span_count - 1).get_attribute("title") or ""
        if not preview_text:
            preview_text = row.inner_text().strip()

        unread.append((chat_name, preview_text))

    return unread


def write_trigger_files(chat_name, preview_text, msg_id):
    inbox_filename = f"whatsapp_{msg_id}.md"
    (NEEDS_ACTION_PATH / inbox_filename).write_text(
        f"---\ntype: whatsapp\nfrom: {chat_name}\n"
        f"whatsapp_message_id: {msg_id}\n"
        f"received_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n---\n\n"
        f"{preview_text.strip()}\n"
    )

    trigger_filename = f"TRIGGER_whatsapp_{msg_id}.md"
    (NEEDS_ACTION_PATH / trigger_filename).write_text(
        f"---\ntype: triage_trigger\noriginal_file: {inbox_filename}\n"
        f"detected_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\nstatus: pending\n---\n\n"
        f"Claude, a new WhatsApp message `{inbox_filename}` has arrived from "
        f"{chat_name}. Please analyze this file, update the Dashboard, and create a plan."
    )

    logging.info(f"New WhatsApp business message from {chat_name!r} -> {inbox_filename}")


def poll_once(page, processed_ids):
    new_count = 0
    for chat_name, preview_text in find_unread_chats(page):
        msg_id = make_message_id(chat_name, preview_text)
        if msg_id in processed_ids:
            continue

        processed_ids.add(msg_id)
        save_processed_ids(processed_ids)

        if contains_business_keyword(preview_text):
            write_trigger_files(chat_name, preview_text, msg_id)
            new_count += 1
        else:
            logging.info(f"Skipping non-business unread chat: {chat_name!r}")

    return new_count


if __name__ == "__main__":
    NEEDS_ACTION_PATH.mkdir(exist_ok=True)
    SESSION_DIR.mkdir(exist_ok=True)
    processed_ids = load_processed_ids()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(SESSION_DIR),
            headless=HEADLESS,
        )
        page = context.pages[0] if context.pages else context.new_page()

        wait_for_login(page)
        logging.info(f"WhatsApp Watcher started. Polling every {POLL_INTERVAL_SECONDS}s.")

        try:
            while True:
                try:
                    n = poll_once(page, processed_ids)
                    if n:
                        logging.info(f"Processed {n} new business message(s).")
                except Exception as e:
                    logging.error(f"Error during poll cycle: {e}")
                time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logging.info("Stopping")
        finally:
            context.close()
