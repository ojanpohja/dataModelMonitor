#!/usr/bin/env python3
import os
import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from requests.auth import HTTPBasicAuth

# --- Models to monitor (root URLs) ---
MODELS = [
    "https://tietomallit.suomi.fi/model/rytj-kaava/",
    "https://tietomallit.suomi.fi/model/ryhti-tont/",
    "https://tietomallit.suomi.fi/model/ryhti-akr",
    "https://tietomallit.suomi.fi/model/raklu/info",
]

STATE_PATH = Path(".github/monitor/suomifi_state.json")

# --- Mailjet settings from env ---
MAILJET_API_KEY = os.getenv("MAILJET_API_KEY")
MAILJET_SECRET_KEY = os.getenv("MAILJET_SECRET_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO", "")  # comma-separated

MAILJET_API_URL = "https://api.mailjet.com/v3.1/send"

# --- Optional: healthcheck (off by default) ---
HEALTHCHECK_DAYS = int(os.getenv("HEALTHCHECK_DAYS", "0"))  # set 7 to enable

# --- Fetch headers to avoid basic bot blocks ---
DEFAULT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
}

VERSION_RE = re.compile(r"\bVersio\s+(\d+\.\d+\.\d+)\b", re.IGNORECASE)
VER_PARAM_RE = re.compile(r"[?&]ver=([0-9]+\.[0-9]+\.[0-9]+)\b", re.IGNORECASE)

def now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def parse_iso(dt_str):
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def send_email(subject: str, body: str):
    to_emails = [e.strip() for e in EMAIL_TO.split(",") if e.strip()]
    if not to_emails:
        print("EMAIL_TO not set; skipping email")
        return
    if not (MAILJET_API_KEY and MAILJET_SECRET_KEY and EMAIL_FROM):
        raise RuntimeError("Mailjet credentials or EMAIL_FROM missing")

    # Parse EMAIL_FROM like "Name <email@domain>"
    if "<" in EMAIL_FROM and ">" in EMAIL_FROM:
        name_part = EMAIL_FROM.split("<")[0].strip()
        email_part = EMAIL_FROM.split("<")[1].split(">")[0].strip()
    else:
        name_part = None
        email_part = EMAIL_FROM.strip()

    payload = {
        "Messages": [{
            "From": {"Email": email_part, **({"Name": name_part} if name_part else {})},
            "To": [{"Email": t} for t in to_emails],
            "Subject": subject,
            "TextPart": body
        }]
    }

    auth = HTTPBasicAuth(MAILJET_API_KEY, MAILJET_SECRET_KEY)

    # retries for transient errors
    for attempt in range(1, 4):
        try:
            r = requests.post(MAILJET_API_URL, auth=auth, json=payload, timeout=15)
            if r.status_code in (200, 201, 202):
                print(f"Email sent via Mailjet (status {r.status_code})")
                return
            if 400 <= r.status_code < 500:
                raise RuntimeError(f"Mailjet permanent error {r.status_code}: {r.text}")
            raise RuntimeError(f"Mailjet server error {r.status_code}: {r.text}")
        except Exception as e:
            print(f"Mailjet send attempt {attempt} failed: {e}")
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)

def extract_version(final_url: str, html: str):
    # 1) URL param (works if server-side redirects or URL contains ver)
    m = VER_PARAM_RE.search(final_url or "")
    if m:
        return m.group(1)

    # 2) HTML contains "Versio x.y.z"
    m = VERSION_RE.search(html or "")
    if m:
        return m.group(1)

    # 3) fallback: any ver= in HTML (sometimes SPA embeds links)
    m = VER_PARAM_RE.search(html or "")
    if m:
        return m.group(1)

    return None

def fetch_model_version(url: str):
    # allow redirects; capture final URL
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=30, allow_redirects=True)
    # If blocked, raise for visibility (we’ll include in email on startup/change)
    r.raise_for_status()
    final_url = str(r.url)
    html = r.text
    version = extract_version(final_url, html)
    return version, final_url

def main():
    state = load_state()
    initialized = state.get("initialized", False)
    last_healthcheck_at = parse_iso(state.get("last_healthcheck_sent"))
    now = datetime.now(timezone.utc)
    now_iso = now_utc_iso()

    current = {}
    errors = []

    for model_url in MODELS:
        try:
            version, resolved_url = fetch_model_version(model_url)
            current[model_url] = {
                "version": version,
                "resolved_url": resolved_url
            }
            if not version:
                errors.append(f"{model_url}: version not found (resolved: {resolved_url})")
        except Exception as e:
            current[model_url] = {
                "version": None,
                "resolved_url": None
            }
            errors.append(f"{model_url}: fetch failed: {e}")

    prev_models = state.get("models", {})

    # Determine changes (version changes only)
    changes = []
    for model_url, info in current.items():
        new_v = info.get("version")
        old_v = (prev_models.get(model_url) or {}).get("version")
        if initialized and new_v and old_v and new_v != old_v:
            changes.append(f"- {model_url}\n  {old_v} -> {new_v}\n  resolved: {info.get('resolved_url')}")

    # Startup notification (first successful run)
    if not initialized:
        body_lines = [
            "[Suomi.fi model monitor][STARTUP] Initialized and fetched initial versions.",
            "",
            "Models:",
        ]
        for model_url, info in current.items():
            body_lines.append(f"- {model_url}")
            body_lines.append(f"  version: {info.get('version')}")
            body_lines.append(f"  resolved: {info.get('resolved_url')}")
        if errors:
            body_lines += ["", "Warnings/errors:", *[f"- {e}" for e in errors]]
        body_lines += ["", f"Checked at: {now_iso}"]
        send_email("Suomi.fi model monitor: STARTUP", "\n".join(body_lines))

        state.update({
            "initialized": True,
            "models": current,
            "last_checked": now_iso,
            "last_healthcheck_sent": now_iso if HEALTHCHECK_DAYS > 0 else None
        })
        save_state(state)
        return

    # Change notification
    if changes:
        body_lines = [
            "[Suomi.fi model monitor][CHANGE] Model version changed.",
            "",
            "Changes:",
            *changes
        ]
        if errors:
            body_lines += ["", "Warnings/errors:", *[f"- {e}" for e in errors]]
        body_lines += ["", f"Checked at: {now_iso}"]
        send_email("Suomi.fi model monitor: CHANGE detected", "\n".join(body_lines))

        state["models"] = current
        state["last_checked"] = now_iso
        save_state(state)
        return

    # No changes -> optional healthcheck
    if HEALTHCHECK_DAYS > 0:
        send_health = False
        if last_healthcheck_at is None:
            send_health = True
        else:
            if now - last_healthcheck_at >= timedelta(days=HEALTHCHECK_DAYS):
                send_health = True

        if send_health:
            body_lines = [
                "[Suomi.fi model monitor][HEALTHCHECK] No version changes detected.",
                "",
                "Current versions:",
            ]
            for model_url, info in current.items():
                body_lines.append(f"- {model_url}")
                body_lines.append(f"  version: {info.get('version')}")
                body_lines.append(f"  resolved: {info.get('resolved_url')}")
            if errors:
                body_lines += ["", "Warnings/errors:", *[f"- {e}" for e in errors]]
            body_lines += ["", f"Checked at: {now_iso}"]
            send_email("Suomi.fi model monitor: HEALTHCHECK — no changes", "\n".join(body_lines))
            state["last_healthcheck_sent"] = now_iso

    state["models"] = current
    state["last_checked"] = now_iso
    save_state(state)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fatal error:", e)
        sys.exit(1)
