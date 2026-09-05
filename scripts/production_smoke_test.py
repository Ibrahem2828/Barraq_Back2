#!/usr/bin/env python3
"""Non-destructive HTTP smoke checks for the deployed public API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api.barraq.xn--mgbaab0cxheq.tech"
ENDPOINTS = {
    "/api/v1/health/live/": "ok",
    "/api/v1/health/ready/": "ready",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("PUBLIC_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--allow-http", action="store_true", help="Only for a local development target.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in ({"http", "https"} if args.allow_http else {"https"}) or not parsed.hostname:
        print("Invalid smoke-test base URL.")
        return 2

    failures = 0
    for path, expected_status in ENDPOINTS.items():
        request = Request(base_url + path, headers={"Accept": "application/json", "User-Agent": "Baraq-Production-Smoke-Test/1.0"})
        try:
            with urlopen(request, timeout=args.timeout) as response:
                status_code = response.status
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            print(f"FAIL: {path} — HTTP {exc.code}")
            failures += 1
            continue
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            print(f"FAIL: {path} — {type(exc).__name__}")
            failures += 1
            continue

        is_valid = (
            status_code == 200
            and payload.get("success") is True
            and payload.get("data", {}).get("status") == expected_status
        )
        print(f"{'PASS' if is_valid else 'FAIL'}: {path} — HTTP {status_code}")
        failures += not is_valid

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
