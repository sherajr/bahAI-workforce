"""
Quick diagnostic — test xAI image generation directly.
Run: python scripts/test_xai_image.py
"""
import os
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

KEY   = os.getenv("XAI_API_KEY", "")
BASE  = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
MODEL = os.getenv("XAI_IMAGE_MODEL", "grok-imagine-image-quality")

print(f"Base URL : {BASE}")
print(f"Model    : {MODEL}")
print(f"Key set  : {bool(KEY)} ({KEY[:8]}...)\n")

headers = {
    "Authorization": f"Bearer {KEY}",
    "Content-Type": "application/json",
}

# Try the images/generations endpoint
for endpoint in ["/images/generations", "/image/generate", "/imagine"]:
    url = BASE + endpoint
    print(f"Trying: POST {url}")
    try:
        r = requests.post(
            url,
            headers=headers,
            json={"model": MODEL, "prompt": "a simple red circle on white background"},
            timeout=30,
        )
        print(f"  Status : {r.status_code}")
        print(f"  Body   : {r.text[:300]}\n")
        if r.status_code == 200:
            print("SUCCESS — image generation works!")
            data = r.json()
            print(f"  URL    : {data.get('data', [{}])[0].get('url', '(no url field)')}")
            break
    except Exception as e:
        print(f"  Error  : {e}\n")
