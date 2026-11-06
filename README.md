# Platform Audit

Tracks who joins, changes, and leaves **your app (Postgres)** and **ArcGIS Online**, keeps the history
in a local SQLite file, flags people who left one platform but are still active on another (with the reason),
and lists users who haven't logged in lately so you can decide who is OK to stay.

## How it works

Neither source keeps a change history, so each run takes a **snapshot** and diffs it against the last known
state. Every difference becomes an event: `added`, `readded`, `deactivated`, `reactivated`, `removed`, `changed`
(role / user type / email / any extra column). The first run only records a baseline (no events).
People are matched across platforms by lowercased **email**.

**Internal vs external.** Emails on `internal_domains` (FieldSync, Murphy Tower) are the primary focus; the
report defaults to internal only (checkbox to include everyone). External demo/manager users use a longer
stale threshold (`stale_days_external`, default 180) because months without a login is normal for them.

**The "why"** can't be inferred from data. For exits it comes from a `reason` column in your Postgres query
if you have one, otherwise you add it:
`python -m audit reason --email jo@x.com "left company"`. Exits missing a reason are highlighted.

**Stale users / "ok to stay".** Active users with no login inside the threshold (or never) appear under
"needs a decision". Record a decision with the reason and it moves to "Approved to stay", where it remains
visible with an optional review date:
`python -m audit keep jo@x.com "demo access for sales, logs in quarterly" --review-after 2027-06-01`

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp config.example.yaml config.yaml     # edit the SQL query to match your users table
cp .env.example .env                   # fill in credentials, then: source .env
python -m audit snapshot               # first run = baseline
```

Use a **read-only** DB user (the connection is also opened read-only).
Schedule `python -m audit snapshot && python -m audit report` daily (cron / launchd).

## The UI

```bash
python -m audit serve        # opens http://127.0.0.1:8765
```

Tabs: **Needs decision** (stale users; tick several and "Keep selected…", or "Keep…" one with a reason and
optional revisit date), **Approved to stay** (with Undo), **Removal watchlist** ("Removed from X" button),
**Exits needing reason**, and **Activity** (every join/change/exit, filterable). "Refresh from platforms"
pulls a new snapshot (your DB tunnel must be up). It only listens on localhost.
`python -m audit report` still writes a read-only HTML export.

## Monthly audit workflow (CLI equivalents)

```bash
python -m audit snapshot && python -m audit report        # open reports/audit.html
python -m audit stale                                     # who hasn't logged in and has no decision
python -m audit keep jo@x.com "why" [--platform arcgis] [--review-after YYYY-MM-DD]
python -m audit pending                                   # exits needing a reason
python -m audit reason --email jo@x.com "left company"    # or --event ID
python -m audit watchlist                                 # who to remove, from where, and why
python -m audit resolve jo@x.com arcgis --note "license reclaimed"
```

## Safety

If a snapshot returns 0 users or drops more than `max_drop_fraction` (default 25%) vs. the last run, it aborts
and records nothing, so a failed query isn't logged as everyone leaving.

## Limits / next steps

- Granularity is per run: a change is dated when it was *detected*, not when it happened.
- Last login is the platform's current value, not a login history.
- ArcGIS tracking covers members, role, user type/license type, level, last login, and disabled state.
  Add-on licenses/entitlements and credit usage are not captured yet.

Tests: `pytest`
