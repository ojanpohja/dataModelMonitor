#!/usr/bin/env python3
"""
Ryhti OpenAPI monitor (GitHub Actions)

- Fetch latest commit for sykefi/Ryhti-rajapintakuvaukset path OpenApi
- If first run -> send STARTUP email
- If commit changed -> send CHANGE email
- If no change and >7d since last healthcheck -> send HEALTHCHECK email
- Save state to .github/monitor/ryhti_state.json (workflow commits/pushes it)
- Uses Mailjet for email delivery via env secrets MAILJET_API_KEY & MAILJET_SECRET_KEY
"""

import os
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
import requests
from requests.auth import HTTPBasicAuth

# ---------- Configuration ----------
REPO_OWNER = "sykefi"
REPO_NAME = "Ryhti-rajapintakuvaukset"
TARGET_PATH = "OpenApi"
STATE_PATH = Path(".github/monitor/ryhti_state.json")
GITHUB_API_COMMITS = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/commits"
    f"?path={TARGET_PATH}&per_page=1"
)
HEALTHCHECK_DAYS = int(os.getenv("HEALTHCHECK_DAYS", "7"))

# Mailjet credentials from env
MAILJET_API_KEY = os.getenv("MAILJET_API_KEY")
MAILJET_SECRET_KEY = os.getenv("MAILJET_SECRET_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO", "")  # comma-separated

# Optional Slack fallback
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK")

# ---------- Helpers ----------
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
        try:
            return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
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

# ---------- GitHub API ----------
def fetch_latest_commit():
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(GITHUB_API_COMMITS, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise RuntimeError("No commit data returned from GitHub API")
    entry = data[0]
    sha = entry.get("sha")
    date = entry.get("commit", {}).get("committer", {}).get("date")
    html_url = entry.get("html_url")
    return sha, date, html_url

# ---------- Mailjet send_email ----------
MAILJET_API_URL = "https://api.mailjet.com/v3.1/send"

def _send_mailjet(subject: str, body: str):
    """Low-level Mailjet send with retries."""
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

    messages = [{
        "From": {"Email": email_part, **({"Name": name_part} if name_part else {})},
        "To": [{"Email": to} for to in to_emails],
        "Subject": subject,
        "TextPart": body
    }]

    payload = {"Messages": messages}
    auth = HTTPBasicAuth(MAILJET_API_KEY, MAILJET_SECRET_KEY)

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(MAILJET_API_URL, auth=auth, json=payload, timeout=15)
            if resp.status_code in (200, 201, 202):
                print(f"Email sent via Mailjet (status {resp.status_code})")
                return
            else:
                print(f"Mailjet returned status {resp.status_code}: {resp.text}")
                # treat 4xx as permanent failure (no retry)
                if 400 <= resp.status_code < 500:
                    raise RuntimeError(f"Mailjet permanent error {resp.status_code}: {resp.text}")
                else:
                    raise RuntimeError(f"Mailjet server error {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"Attempt {attempt} failed sending Mailjet email: {e}")
            if attempt == max_attempts:
                raise
            sleep_for = 2 ** attempt
            print(f"Retrying in {sleep_for}s...")
            time.sleep(sleep_for)

def send_email(subject: str, body: str):
    try:
        _send_mailjet(subject, body)
    except Exception as e:
        print("Mailjet send failed:", e)
        # fallback to Slack if configured (non-blocking)
        if SLACK_WEBHOOK:
            try:
                payload = {"text": f"{subject}\n\n{body}"}
                r = requests.post(SLACK_WEBHOOK, json=payload, timeout=10)
                r.raise_for_status()
                print("Fallback Slack notification sent.")
            except Exception as se:
                print("Fallback Slack also failed:", se)
        else:
            raise

# ---------- Message builders ----------
def make_startup_message(sha, date, url):
    return (
        "[Ryhti-monitor][STARTUP] Monitor initialized and fetched initial data.\n\n"
        f"Repo: https://github.com/{REPO_OWNER}/{REPO_NAME}\n"
        f"Path: {TARGET_PATH}\n"
        f"Latest commit SHA: {sha}\n"
        f"Commit date: {date}\n"
        f"Commit: {url}\n\n"
        "This is the initial startup notification."
    )

def make_change_message(sha, date, url, prev_sha, prev_date):
    return (
        "[Ryhti-monitor][CHANGE] OpenAPI repository updated.\n\n"
        f"Repo: https://github.com/{REPO_OWNER}/{REPO_NAME}\n"
        f"Path: {TARGET_PATH}\n"
        f"New commit SHA: {sha}\n"
        f"New commit date: {date}\n"
        f"Commit: {url}\n\n"
        f"Previous commit SHA: {prev_sha}\n"
        f"Previous commit date: {prev_date}\n\n"
        "Note: This notification reports that the repo commit changed. No schema diff was performed."
    )

def make_healthcheck_message(last_sha, last_date, checked_at):
    return (
        "[Ryhti-monitor][HEALTHCHECK] Weekly status — no changes detected.\n\n"
        f"Last commit SHA: {last_sha}\n"
        f"Last commit date: {last_date}\n"
        f"Healthcheck time: {checked_at}\n\n"
        "If you'd like content diffing added, we can extend this workflow."
    )

# ---------- Main logic ----------
def main():
    try:
        sha, date, url = fetch_latest_commit()
        print("Fetched latest commit:", sha, date, url)
    except Exception as e:
        print("Failed to fetch latest commit:", e)
        sys.exit(1)

    state = load_state()
    last_sha = state.get("last_commit_sha")
    last_date = state.get("last_commit_date")
    last_healthcheck_at = parse_iso(state.get("last_healthcheck_sent"))
    had_run_before = state.get("initialized", False)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")

    # First-time startup
    if not had_run_before:
        print("No prior state found -> sending startup notification.")
        msg = make_startup_message(sha, date, url)
        try:
            send_email("Ryhti-monitor: STARTUP", msg)
        except Exception as e:
            print("Startup notification failed:", e)
            # Do not abort; still save initial state to avoid infinite loop
        state.update({
            "initialized": True,
            "last_commit_sha": sha,
            "last_commit_date": date,
            "last_checked": now_iso,
            "last_healthcheck_sent": now_iso
        })
        save_state(state)
        print("Initial state saved; startup run complete.")
        return

    # Change detection
    if last_sha != sha:
        print("Change detected -> sending change notification.")
        prev_sha = last_sha
        prev_date = last_date
        msg = make_change_message(sha, date, url, prev_sha, prev_date)
        try:
            send_email("Ryhti-monitor: CHANGE detected", msg)
        except Exception as e:
            print("Change email failed:", e)
        state.update({
            "last_commit_sha": sha,
            "last_commit_date": date,
            "last_checked": now_iso
        })
        save_state(state)
        print("State updated after change.")
        return

    # No change -> weekly healthcheck
    print("No change detected.")
    send_health = False
    if last_healthcheck_at is None:
        send_health = True
    else:
        if now - last_healthcheck_at >= timedelta(days=HEALTHCHECK_DAYS):
            send_health = True

    if send_health:
        print("Sending weekly healthcheck (no changes).")
        msg = make_healthcheck_message(state.get("last_commit_sha"), state.get("last_commit_date"), now_iso)
        try:
            send_email("Ryhti-monitor: HEALTHCHECK — no changes", msg)
        except Exception as e:
            print("Healthcheck email failed:", e)
        state["last_healthcheck_sent"] = now_iso
        state["last_checked"] = now_iso
        save_state(state)
        print("Healthcheck state updated.")
        return

    # Nothing to notify; update last_checked
    state["last_checked"] = now_iso
    save_state(state)
    print("No notifications needed; state updated last_checked.")

if __name__ == "__main__":
    main()
