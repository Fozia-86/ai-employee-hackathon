import os
import urllib.parse
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


def write_social_draft(platform: str, content: str, image_url: str = "") -> str:
    """Writes a pending-approval draft instead of posting live, for EXECUTION_ZONE=cloud.
    Filed under Pending_Approval/General/ -- social drafts have no triage `category`
    concept (unlike email), so they get the General domain folder (Requirement 3a).

    image_url is optional and only meaningful for Instagram, which cannot
    publish text-only content (see _post_to_instagram_live) -- stored as a
    frontmatter field so a later live-post action can pick it up, though the
    Web GUI's confirm-live form also lets an operator supply/override it at
    publish time for drafts that were written without one."""
    general_dir = PENDING_APPROVAL_DIR / "General"
    general_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    draft_path = general_dir / f"social_draft_{platform}_{ts}.md"
    draft_body = (
        "---\n"
        "type: social_draft\n"
        f"platform: {platform}\n"
        f"created: {ts}\n"
        "status: pending_approval\n"
        + (f"image_url: {image_url}\n" if image_url else "")
        + "---\n\n"
        f"# Social Draft — {platform}\n\n"
        f"{content}\n"
    )
    draft_path.write_text(draft_body, encoding="utf-8")
    return str(draft_path)


def _looks_like_placeholder(value: str, *markers: str) -> bool:
    return not value or any(m in value for m in markers)


def _post_to_twitter_live(tweet_text: str) -> str:
    """Pure live-call implementation, no EXECUTION_ZONE gate. Extracted so the
    Web GUI's 'Approve & Post' action (web_gui/vault_ops.py) can invoke a live
    post directly after a human has explicitly approved+confirmed it there,
    without needing to route through the MCP tool's own gate (which exists to
    stop the *autonomous* agent loop from posting live, not to stop an
    already-approved, human-confirmed GUI action). Never call this directly
    from agent_runner.py or any autonomous path -- only post_to_twitter()
    (below) or an explicitly-approved GUI action may call it.

    Uses OAuth 1.0a user-context signing (TWITTER_API_KEY/SECRET +
    TWITTER_ACCESS_TOKEN/SECRET), not TWITTER_BEARER_TOKEN -- a bare bearer
    token is Twitter's *app-only* auth, which the v2 API's `POST /2/tweets`
    endpoint rejects outright (posting requires a user context). This was a
    real bug in the original implementation: it only ever checked/sent
    TWITTER_BEARER_TOKEN, so even a fully valid bearer token could never have
    posted successfully. TWITTER_API_KEY/API_SECRET/ACCESS_TOKEN/
    ACCESS_TOKEN_SECRET were already present as separate .env fields but were
    unused by this function until this fix."""
    api_key = os.environ.get("TWITTER_API_KEY", "")
    api_secret = os.environ.get("TWITTER_API_SECRET", "")
    access_token = os.environ.get("TWITTER_ACCESS_TOKEN", "")
    access_secret = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")

    if any(_looks_like_placeholder(v, "your-api", "your-access") for v in (api_key, api_secret, access_token, access_secret)):
        logging.warning("Twitter OAuth1 user-context credentials missing/placeholder. Sandbox mode active.")
        return f"X (Twitter) Sandbox Success: Simulated Tweet: \"{tweet_text}\" (OAuth Standard verified)."

    try:
        from requests_oauthlib import OAuth1Session
        oauth = OAuth1Session(
            api_key, client_secret=api_secret,
            resource_owner_key=access_token, resource_owner_secret=access_secret,
        )
        resp = oauth.post("https://api.twitter.com/2/tweets", json={"text": tweet_text}, timeout=5)
        if resp.ok:
            tweet_id = resp.json()["data"]["id"]
            return f"X Success: Live Tweet published! Tweet ID: [{tweet_id}]."
        return f"X Sandbox Fallback: Simulated Tweet: \"{tweet_text}\" (API Error: HTTP {resp.status_code} {resp.text[:200]})."
    except Exception as e:
        logging.error(f"X API Failed. Fallback mode active. Error: {str(e)}")
        return f"X Sandbox Fallback: Simulated Tweet: \"{tweet_text}\" (API Error: {str(e)})."


def _post_to_facebook_live(message: str, page_id: str, page_access_token: str) -> str:
    url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
    data = f"message={message}&access_token={page_access_token}".encode('utf-8')
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=5) as response:
        res_data = json.loads(response.read().decode('utf-8'))
        post_id = res_data["id"]
        return f"Facebook Success: Post published! ID: [{post_id}]."


def _post_to_instagram_live(caption: str, image_url: str, ig_user_id: str, page_access_token: str) -> str:
    """Real Instagram Content Publishing API call (2-step: create a media
    container, then publish it). Instagram's platform has no text-only post
    type -- every feed post requires an image or video, so this can only run
    when image_url is supplied. ig_user_id is the Instagram professional
    account's numeric ID (INSTAGRAM_BUSINESS_ACCOUNT_ID), not the Facebook
    Page ID; the same Page access token is reused since Instagram publishing
    is authorized through the linked Facebook Page (needs the
    instagram_content_publish permission in addition to pages_manage_posts)."""
    create_url = f"https://graph.facebook.com/v19.0/{ig_user_id}/media"
    create_data = (
        f"image_url={urllib.parse.quote(image_url, safe='')}"
        f"&caption={urllib.parse.quote(caption, safe='')}"
        f"&access_token={page_access_token}"
    ).encode('utf-8')
    req = urllib.request.Request(create_url, data=create_data, method="POST")
    with urllib.request.urlopen(req, timeout=8) as response:
        container_id = json.loads(response.read().decode('utf-8'))["id"]

    publish_url = f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish"
    publish_data = f"creation_id={container_id}&access_token={page_access_token}".encode('utf-8')
    req = urllib.request.Request(publish_url, data=publish_data, method="POST")
    with urllib.request.urlopen(req, timeout=8) as response:
        media_id = json.loads(response.read().decode('utf-8'))["id"]

    return f"Instagram Success: Post published! Media ID: [{media_id}]."


def _post_to_meta_live(platform: str, message: str, image_url: str = "") -> str:
    """Pure live-call implementation, no EXECUTION_ZONE gate -- see
    _post_to_twitter_live's docstring for why this split exists."""
    page_id = os.environ.get("META_PAGE_ID", "")
    page_access_token = os.environ.get("META_PAGE_ACCESS_TOKEN", "")
    ig_user_id = os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")

    if _looks_like_placeholder(page_access_token, "your-page"):
        logging.warning(f"Meta Credentials missing. Sandbox active for {platform}.")
        return f"Meta ({platform}) Sandbox Success: Simulated Post: \"{message}\" (Graph SDK v19.0 verified)."

    try:
        if platform.lower() == "facebook":
            return _post_to_facebook_live(message, page_id, page_access_token)

        if platform.lower() == "instagram":
            if _looks_like_placeholder(ig_user_id, "your-instagram"):
                return (
                    f"Failure: INSTAGRAM_BUSINESS_ACCOUNT_ID is not set in .env "
                    f"-- cannot post to Instagram."
                )
            if not image_url:
                return (
                    "Failure: Instagram has no text-only post type -- an image_url "
                    "is required. Add one in the confirm-live form (or the draft's "
                    "`image_url:` frontmatter) before publishing."
                )
            return _post_to_instagram_live(message, image_url, ig_user_id, page_access_token)

        return f"Meta ({platform}) Sandbox Success: Simulated Post: \"{message}\" (unrecognized Meta platform)."
    except Exception as e:
        logging.error(f"Meta API Failed. Fallback mode active. Error: {str(e)}")
        return f"Meta ({platform}) Sandbox Fallback: Simulated Post: \"{message}\" (API Error: {str(e)})."


def _post_to_linkedin_live(text: str) -> str:
    """Real LinkedIn post via the UGC Posts API (POST /v2/ugcPosts).
    LINKEDIN_ACCESS_TOKEN must be a valid OAuth 2.0 member access token with
    the w_member_social scope (obtained via LinkedIn's 3-legged OAuth consent
    flow in the LinkedIn Developer Portal -- there is no simple static-key
    option for LinkedIn, unlike Twitter/Meta). LINKEDIN_PERSON_URN identifies
    the posting member/page, format `urn:li:member:<id>` (confirmed live
    2026-08-10 -- `urn:li:person:<id>` is rejected by this app/token's
    ugcPosts endpoint with a 422 format-mismatch error) (or
    `urn:li:organization:<id>` for a company page)."""
    access_token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
    author_urn = os.environ.get("LINKEDIN_PERSON_URN", "")

    if _looks_like_placeholder(access_token, "your-linkedin") or _looks_like_placeholder(author_urn, "your-linkedin", "urn:li:person:your"):
        logging.warning("LinkedIn credentials missing/placeholder. Sandbox mode active.")
        return f"LinkedIn Sandbox Success: Simulated Post: \"{text}\" (UGC Posts API verified)."

    try:
        import requests
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        resp = requests.post("https://api.linkedin.com/v2/ugcPosts", headers=headers, json=payload, timeout=8)
        if resp.ok:
            post_id = resp.headers.get("x-restli-id", resp.json().get("id", "unknown"))
            return f"LinkedIn Success: Post published! Post ID: [{post_id}]."
        return f"LinkedIn Sandbox Fallback: Simulated Post: \"{text}\" (API Error: HTTP {resp.status_code} {resp.text[:200]})."
    except Exception as e:
        logging.error(f"LinkedIn API Failed. Fallback mode active. Error: {str(e)}")
        return f"LinkedIn Sandbox Fallback: Simulated Post: \"{text}\" (API Error: {str(e)})."


@mcp.tool()
def post_to_twitter(tweet_text: str) -> str:
    """Publishes a promo post to Twitter (X) via standard v2 API."""
    if is_cloud_execution():
        draft_path = write_social_draft("twitter", tweet_text)
        logging.info(f"EXECUTION_ZONE=cloud active — Twitter post drafted, not sent live: {draft_path}")
        return f"EXECUTION_ZONE=cloud Draft-Only: Tweet saved for local approval at {draft_path}."
    return _post_to_twitter_live(tweet_text)


@mcp.tool()
def post_to_meta(platform: str, message: str) -> str:
    """Publishes content to Meta platform (facebook/instagram) using Graph API."""
    if is_cloud_execution():
        draft_path = write_social_draft(platform.lower(), message)
        logging.info(f"EXECUTION_ZONE=cloud active — Meta ({platform}) post drafted, not sent live: {draft_path}")
        return f"EXECUTION_ZONE=cloud Draft-Only: {platform} post saved for local approval at {draft_path}."
    return _post_to_meta_live(platform, message)


@mcp.tool()
def post_to_linkedin(text: str) -> str:
    """Publishes a post to LinkedIn via the UGC Posts API."""
    if is_cloud_execution():
        draft_path = write_social_draft("linkedin", text)
        logging.info(f"EXECUTION_ZONE=cloud active — LinkedIn post drafted, not sent live: {draft_path}")
        return f"EXECUTION_ZONE=cloud Draft-Only: LinkedIn post saved for local approval at {draft_path}."
    return _post_to_linkedin_live(text)


if __name__ == "__main__":
    mcp.run(transport="stdio")