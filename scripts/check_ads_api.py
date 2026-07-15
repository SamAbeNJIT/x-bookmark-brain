"""Probe whether X has granted the app Ads API access (the xconv prerequisite).

One-shot:   .venv/bin/python scripts/check_ads_api.py
Watch mode: .venv/bin/python scripts/check_ads_api.py --watch   (hourly; exits + speaks up
            the moment access is granted — leave it running in a spare shell)

Reads the X_ADS_* credentials from .env. A 403 UNAUTHORIZED_CLIENT_APPLICATION means the
app-level grant hasn't landed yet; 200 means GO (then: regenerate the access token pair,
update .env + App Runner, start the registrations campaign).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import httpx  # noqa: E402

from xbb.xconv import _oauth1_header  # noqa: E402


def probe() -> tuple[int, str]:
    url = "https://ads-api.x.com/12/accounts"
    header = _oauth1_header(
        "GET", url,
        os.environ["X_ADS_CONSUMER_KEY"], os.environ["X_ADS_CONSUMER_SECRET"],
        os.environ["X_ADS_ACCESS_TOKEN"], os.environ["X_ADS_ACCESS_SECRET"])
    try:
        r = httpx.get(url, headers={"Authorization": header}, timeout=15)
        return r.status_code, r.text[:200]
    except Exception as e:
        return -1, f"network error: {type(e).__name__}"


def check_once() -> bool:
    status, body = probe()
    now = datetime.now().strftime("%H:%M:%S")
    if status == 200:
        print(f"[{now}] ✅ ADS API ACCESS GRANTED — status 200")
        print("    Next: regenerate the Access Token + Secret in the developer portal,")
        print("    update .env + App Runner, then start the registrations campaign.")
        return True
    if status == 403 and "UNAUTHORIZED_CLIENT_APPLICATION" in body:
        print(f"[{now}] ⏳ not yet — app still lacks Ads API access (403)")
    elif status == 401:
        print(f"[{now}] ⚠️  401 — credentials problem (regenerated tokens not in .env?)")
    else:
        print(f"[{now}] ❓ status {status}: {body}")
    return False


if __name__ == "__main__":
    if "--watch" in sys.argv:
        print("Watching for Ads API access (checks hourly, Ctrl-C to stop)…")
        while not check_once():
            time.sleep(3600)
        try:  # audible victory lap (macOS); harmless elsewhere
            subprocess.run(["say", "X ads A P I access granted"], timeout=10)
        except Exception:
            pass
    else:
        sys.exit(0 if check_once() else 1)
