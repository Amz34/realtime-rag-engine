"""
publish_with_image.py
---------------------
Uploads the high-resolution architecture infographic to LinkedIn (/rest/images flow)
and publishes the complete technical commentary.
"""

import json
import os
import sys
import urllib.request
import urllib.error

TOKEN_FILE = "/home/ubuntu/linkedin_token.json"
POST_FILE = "/home/ubuntu/agy-argon-home/.gemini/antigravity-cli/scratch/realtime_rag_engine/LINKEDIN_POST.md"
IMAGE_PATH = "/home/ubuntu/agy-argon-home/.gemini/antigravity-cli/scratch/realtime_rag_engine/architecture_infographic.png"
API_VERSION = "202610"

def run():
    with open(TOKEN_FILE, "r") as f:
        tok = json.load(f)["access_token"]

    with open(POST_FILE, "r", encoding="utf-8") as f:
        raw = f.read()

    marker = "```text"
    if marker in raw:
        post_text = raw.split(marker)[1].split("```")[0].strip()
    else:
        post_text = raw.strip()

    print(f"Loaded commentary: {len(post_text)} characters")

    # 1. Resolve author URN
    req_me = urllib.request.Request(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {tok}"}
    )
    me = json.loads(urllib.request.urlopen(req_me).read().decode())
    author = f"urn:li:person:{me['sub']}"
    print(f"Author: {me.get('name')} ({author})")

    # 2. Step 1: initializeUpload on /rest/images
    init_body = {
        "initializeUploadRequest": {
            "owner": author
        }
    }
    req_init = urllib.request.Request(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        data=json.dumps(init_body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
            "LinkedIn-Version": API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0"
        }
    )
    init_resp = urllib.request.urlopen(req_init)
    init_data = json.loads(init_resp.read().decode())
    image_urn = init_data["value"]["image"]
    upload_url = init_data["value"]["uploadUrl"]
    print(f"Image URN initialized: {image_urn}")

    # 3. Step 2: PUT PNG bytes
    with open(IMAGE_PATH, "rb") as f:
        image_bytes = f.read()

    print(f"Uploading PNG bytes ({len(image_bytes)} bytes)...")
    req_upload = urllib.request.Request(
        upload_url,
        data=image_bytes,
        method="PUT",
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "image/png"
        }
    )
    upload_resp = urllib.request.urlopen(req_upload)
    print(f"Image uploaded successfully! Status: {upload_resp.status}")

    # 4. Step 3: Publish post with attached image
    post_body = {
        "author": author,
        "commentary": post_text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": []
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
        "content": {
            "media": {
                "id": image_urn,
                "altText": "Real-Time Streaming Incremental RAG Engine Architecture & Benchmark Infographic"
            }
        }
    }

    req_publish = urllib.request.Request(
        "https://api.linkedin.com/rest/posts",
        data=json.dumps(post_body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
            "LinkedIn-Version": API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0"
        }
    )

    try:
        resp = urllib.request.urlopen(req_publish, timeout=30)
        post_urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id")
        print("\n=======================================================")
        print("SUCCESS! Post with Architecture Infographic Published!")
        print(f"HTTP Status: {resp.status}")
        print(f"Post URN: {post_urn}")
        if post_urn:
            clean_id = post_urn.split(":")[-1]
            print(f"Direct LinkedIn URL: https://www.linkedin.com/feed/update/urn:li:share:{clean_id}/")
        print("=======================================================")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode()}")
        sys.exit(1)

if __name__ == "__main__":
    run()
