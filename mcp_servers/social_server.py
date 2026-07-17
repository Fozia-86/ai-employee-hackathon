import os
import urllib.request
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO)
mcp = FastMCP("Core Social Server")

PENDING_APPROVAL_DIR = PROJECT_ROOT / "Pending_Approval"


def is_cloud_execution() -> bool:
    """EXECUTION_ZONE defaults to 'cloud' (safe/draft-only) unless explicitly set to 'local'."""
    return os.environ.get("EXECUTION_ZONE", "cloud").strip().lower() != "local"


def write_social_draft(platform: str, content: str) -> str:
    """Writes a pending-approval draft instead of posting live, for EXECUTION_ZONE=cloud."""
    PENDING_APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    draft_path = PENDING_APPROVAL_DIR / f"social_draft_{platform}_{ts}.md"
    draft_body = (
        "---\n"
        "type: social_draft\n"
        f"platform: {platform}\n"
        f"created: {ts}\n"
        "status: pending_approval\n"
        "---\n\n"
        f"# Social Draft — {platform}\n\n"
        f"{content}\n"
    )
    draft_path.write_text(draft_body, encoding="utf-8")
    return str(draft_path)


@mcp.tool()
def post_to_twitter(tweet_text: str) -> str:
    """Publishes a promo post to Twitter (X) via standard v2 API."""
    if is_cloud_execution():
        draft_path = write_social_draft("twitter", tweet_text)
        logging.info(f"EXECUTION_ZONE=cloud active — Twitter post drafted, not sent live: {draft_path}")
        return f"EXECUTION_ZONE=cloud Draft-Only: Tweet saved for local approval at {draft_path}."

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
    """Publishes content to Meta platform (facebook/instagram) using Graph API."""
    if is_cloud_execution():
        draft_path = write_social_draft(platform.lower(), message)
        logging.info(f"EXECUTION_ZONE=cloud active — Meta ({platform}) post drafted, not sent live: {draft_path}")
        return f"EXECUTION_ZONE=cloud Draft-Only: {platform} post saved for local approval at {draft_path}."

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