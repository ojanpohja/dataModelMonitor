# Ryhti & Suomi.fi Datamodel Monitors

This repository contains **GitHub Actions–based monitors** for detecting changes in:

1. **Ryhti OpenAPI specifications** (GitHub repository)
2. **Suomi.fi datamodel versions** (web UI)

The monitors run automatically on a schedule and send **proactive email notifications** when changes are detected.  
Email is used because it is **application-independent, reliable, and auditable**, making the solution suitable for long-term public-sector integrations.

---

## What Is Monitored

### 1. Ryhti OpenAPI
- **Source repository:** `sykefi/Ryhti-rajapintakuvaukset`
- **Scope:** All files under the `OpenApi/` directory
- **Change trigger:** New Git commit affecting `OpenApi/`

### 2. Suomi.fi Datamodels
The following datamodels are monitored for **version changes**:

- https://tietomallit.suomi.fi/model/rytj-kaava/
- https://tietomallit.suomi.fi/model/ryhti-tont/
- https://tietomallit.suomi.fi/model/ryhti-akr
- https://tietomallit.suomi.fi/model/raklu/info

The monitor detects the version from:
- the resolved URL parameter (`?ver=x.y.z`), or
- the page header text (e.g. `Versio 1.0.5`)

---

## How It Works

- **Platform:** GitHub Actions
- **Execution:** Python scripts
- **Triggering:** Time-based scheduling (cron)
- **State storage:** Versioned JSON files committed to the repository
- **Notifications:** Email via **Mailjet HTTP API**

This design avoids inbound webhooks, external services, or custom infrastructure.

---

## Workflows

### Ryhti OpenAPI Monitor
- **Workflow:** `.github/workflows/ryhti-monitor.yml`
- **Schedule:** Every 6 hours
- **Script:** `tools/monitor_ryhti_action.py`

### Suomi.fi Datamodel Monitor
- **Workflow:** `.github/workflows/suomifi-model-monitor.yml`
- **Schedule:** Daily
- **Script:** `tools/monitor_suomifi_models.py`

Both workflows also support **manual execution** via the GitHub Actions UI.

---

## Notifications

Each monitor can send the following emails:

- **STARTUP** – First successful run (confirms the monitor is alive)
- **CHANGE** – A monitored value has changed
- **HEALTHCHECK** – Periodic confirmation that no changes have occurred

Healthcheck emails act as a **liveness signal**.  
If they stop arriving, the monitor may not be running.

---

## Required Secrets

Configure the following repository secrets under  
**Settings → Secrets and variables → Actions**:

| Secret | Description |
|------|-------------|
| `MAILJET_API_KEY` | Mailjet API key |
| `MAILJET_SECRET_KEY` | Mailjet secret key |
| `EMAIL_FROM` | Verified sender (e.g. `Ryhti Monitor <monitor@domain.fi>`) |
| `EMAIL_TO` | Comma-separated recipient list |

Optional:
- `SLACK_WEBHOOK` – Fallback notification channel (optional)
- `HEALTHCHECK_DAYS` – Override healthcheck interval (per workflow)

---

## State Files

The monitors persist state in the repository:

- `.github/monitor/ryhti_state.json`
- `.github/monitor/suomifi_state.json`

These files record:
- last observed values
- last execution time
- last healthcheck timestamp

They are automatically updated and committed by the workflows.

---

## How to Verify the Monitors Are Running

1. **GitHub Actions**
   - New runs appear according to the schedule
2. **State files**
   - `last_checked` timestamps update regularly
3. **Email**
   - Startup email on first run
   - Periodic healthcheck emails when no changes occur

All three together confirm correct operation.

---

## How to Pause or Stop Monitoring

- **Pause temporarily:** Comment out the `schedule` block in the workflow
- **Disable completely:** Disable the workflow in the GitHub Actions UI
- **Emergency stop (emails):** Remove Mailjet secrets

---

## Design Principles

- Application-independent notifications (email)
- No inbound endpoints or firewall changes
- Deterministic, auditable behavior
- Minimal operational overhead
- Easy to extend (diffing, SMS, Jira, etc.)

---

## License / Usage

Internal monitoring utility.  
Reuse and modification permitted within project and organizational needs.
