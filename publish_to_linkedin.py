"""
publish_to_linkedin.py
----------------------
Standalone script to publish the complete Real-Time RAG LinkedIn post
using the authenticated OAuth2 token.
"""

import json
import os
import sys
import urllib.request
import urllib.error

TOKEN_FILE = "/home/ubuntu/linkedin_token.json"
POST_FILE = "/home/ubuntu/agy-argon-home/.gemini/antigravity-cli/scratch/realtime_rag_engine/LINKEDIN_POST.md"

def publish():
    if not os.path.exists(TOKEN_FILE):
        print("ERROR: Token file not found at", TOKEN_FILE)
        sys.exit(1)

    with open(POST_FILE, "r", encoding="utf-8") as f:
        raw = f.read()

    # Extract exact clean text
    marker = "```text"
    if marker in raw:
        post_text = raw.split(marker)[1].split("```")[0].strip()
    else:
        post_text = raw.strip()

    print(f"Loaded post text ({len(post_text)} characters).")
    print("--------------------------------------------------")
    print(post_text[:150] + "...\n" + post_text[-120:])
    print("--------------------------------------------------")

    with open(TOKEN_FILE, "r") as f:
        tok = json.load(f)["access_token"]

    # 1. Resolve author URN
    user_req = urllib.request.Request(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {tok}"}
    )
    user_resp = urllib.request.urlopen(user_req, timeout=15)
    me = json.loads(user_resp.read().decode("utf-8"))
    author = f"urn:li:person:{me['sub']}"
    author_name = me.get("name", "Unknown")
    print(f"Authenticated as: {author_name} ({author})")

    # 2. Build payload
    body = {
        "author": author,
        "commentary": post_text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": []
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False
    }

    # 3. Post to LinkedIn REST API
    req = urllib.request.Request(
        "https://api.linkedin.com/rest/posts",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
            "LinkedIn-Version": "202610",
            "X-Restli-Protocol-Version": "2.0.0"
        }
    )

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        post_urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id") or "PUBLISHED"
        print("SUCCESS! Post Published.")
        print(f"HTTP Status: {resp.status}")
        print(f"Post URN: {post_urn}")
        
        # If URN is urn:li:share:123 or urn:li:ugcPost:123, generate direct URL
        if post_urn.startswith("urn:li:share:"):
            share_id = post_urn.split(":")[-1]
            print(f"Direct LinkedIn URL: https://www.linkedin.com/feed/update/urn:li:share:{share_id}/")
        elif ":" in post_urn:
            clean_urn = post_urn.split(":")[-1]
            print(f"Post ID: {clean_urn}")

    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        print(f"HTTP Error {e.code}: {err_msg}")
        sys.exit(1)
    except Exception as e:
        print(f"Publishing Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    publish()
