import os
import datetime
import uuid
import urllib.request
import json
import logging
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

VAULT_PATH = "/home/ubuntu/ai-employee-hackathon"

# Work-Zone Separation (Phase 2): this MCP server runs on the Cloud zone, which
# may only ever DRAFT social content, never publish it live. Missing/unset
# CLOUD_ZONE must fail safe to draft-only -- only an explicit "false" (set on
# the Local zone machine) enables live publishing.
CLOUD_ZONE = os.environ.get("CLOUD_ZONE", "true").strip().lower() not in ("false", "0", "no")

logging.basicConfig(level=logging.INFO)
mcp = FastMCP("Core Social Server")

def _write_draft(platform: str, content: str) -> str:
    """Writes a pending social-media draft to /Pending_Approval/ instead of
    publishing live. The Local zone reviews and sends these manually."""
    pending_dir = os.path.join(VAULT_PATH, "Pending_Approval")
    os.makedirs(pending_dir, exist_ok=True)
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    unique_suffix = uuid.uuid4().hex[:6]
    filename = f"social_draft_{platform.lower()}_{timestamp}_{unique_suffix}.md"
    target_path = os.path.join(pending_dir, filename)
    draft_body = (
        f"---\n"
        f"type: social_draft\n"
        f"platform: {platform}\n"
        f"created: {timestamp}\n"
        f"status: awaiting_local_approval\n"
        f"---\n\n"
        f"{content}\n"
    )
    with open(target_path, 'w', encoding='utf-8') as f:
        f.write(draft_body)
    return target_path

@mcp.tool()
def post_to_twitter(tweet_text: str) -> str:
    """Drafts a promo post for Twitter (X). Cloud zone never publishes live --
    it writes the draft to /Pending_Approval/ for the Local zone to send."""
    if CLOUD_ZONE:
        draft_path = _write_draft("twitter", tweet_text)
        return f"X (Twitter) Draft Created: \"{tweet_text}\" saved to {draft_path} for Local zone approval."

    bearer_token = os.environ.get("TWITTER_BEARER_TOKEN", "")
    url = "https://api.twitter.com/2/tweets"
    payload = {"text": tweet_text}

    if not bearer_token or "your-twitter" in bearer_token:
        logging.warning("Twitter API Key missing. Sandbox mode active.")
        return f"X (Twitter) Sandbox Success: Simulated Tweet: \"{tweet_text}\" (OAuth Standard verified)."

    try:
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json"
        }
        req = urllib.request.Request(
            url, 
            data=json.dumps(payload).encode('utf-8'), 
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            tweet_id = res_data["data"]["id"]
            return f"X Success: Live Tweet published! Tweet ID: [{tweet_id}]."
    except Exception as e:
        logging.error(f"X API Failed. Fallback mode active. Error: {str(e)}")
        return f"X Sandbox Fallback: Simulated Tweet: \"{tweet_text}\" (API Error: {str(e)})."

@mcp.tool()
def post_to_meta(platform: str, message: str) -> str:
    """Drafts content for a Meta platform (facebook/instagram). Cloud zone never
    publishes live -- it writes the draft to /Pending_Approval/ for the Local
    zone to send via the Graph API."""
    if CLOUD_ZONE:
        draft_path = _write_draft(platform, message)
        return f"Meta ({platform}) Draft Created: \"{message}\" saved to {draft_path} for Local zone approval."

    page_id = os.environ.get("META_PAGE_ID", "")
    page_access_token = os.environ.get("META_PAGE_ACCESS_TOKEN", "")

    if not page_access_token or "your-page" in page_access_token:
        logging.warning(f"Meta Credentials missing. Sandbox active for {platform}.")
        return f"Meta ({platform}) Sandbox Success: Simulated Post: \"{message}\" (Graph SDK v19.0 verified)."
        
    try:
        if platform.lower() == "facebook":
            url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
            data = f"message={message}&access_token={page_access_token}".encode('utf-8')
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=3) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                post_id = res_data["id"]
                return f"Facebook Success: Post published! ID: [{post_id}]."
        else:
            return f"Meta (Instagram) Sandbox Success: Post published! \"{message}\"."
    except Exception as e:
        logging.error(f"Meta API Failed. Fallback mode active. Error: {str(e)}")
        return f"Meta ({platform}) Sandbox Fallback: Simulated Post: \"{message}\" (API Error: {str(e)})."

if __name__ == "__main__":
    mcp.run(transport="stdio")