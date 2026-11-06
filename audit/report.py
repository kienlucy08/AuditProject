import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def watchlist(store):
    """People who left/deactivated on one platform but are still active on another."""
    members = store.members()
    resolved = store.resolutions()
    # A person has "left" a platform only when NO active membership remains there
    # (leaving one org of several on the same platform is not leaving the platform).
    by_email = defaultdict(lambda: defaultdict(list))
    for m in members:
        if m["email"]:
            by_email[m["email"]][m["platform"]].append(m)

    latest_exit = {}   # (email, platform) -> most recent removed/deactivated event
    for e in store.events():          # newest first
        if e["event_type"] in ("removed", "deactivated") and (e["email"], e["platform"]) not in latest_exit:
            latest_exit[(e["email"], e["platform"])] = e

    rows = []
    exited = defaultdict(set)         # includes people with NO rows left (e.g. accounts deleted in the backend)
    for (em, p) in latest_exit:
        exited[em].add(p)
    for email in set(by_email) | set(exited):
        plats = by_email.get(email, {})
        left = [p for p in sorted(exited.get(email, ()))
                if not any(m["status"] == "active" for m in plats.get(p, []))]
        active = [p for p, ms in plats.items() if any(m["status"] == "active" for m in ms)]
        todo = [p for p in active if (email, p) not in resolved]
        if not left or not todo:
            continue
        rows.append({
            "email": email,
            "name": next((m["name"] for ms in plats.values() for m in ms if m["name"]), ""),
            "left": [{"platform": p, "when": latest_exit[(email, p)]["detected_at"],
                      "type": latest_exit[(email, p)]["event_type"],
                      "reason": latest_exit[(email, p)]["reason"]} for p in left],
            "remove_from": todo})
    rows.sort(key=lambda r: min(l["when"] for l in r["left"]), reverse=True)
    return rows


def pending_reasons(store):
    return [e for e in store.events()
            if e["event_type"] in ("removed", "deactivated") and not e["reason"]]


def is_internal(email, domains):
    return email.rsplit("@", 1)[-1].lower() in {d.lower() for d in domains}


def stale(store, cfg, today=None):
    """Active users with no login inside the threshold. Internal users get the short
    threshold; external (demo/manager) users the long one. Users with an 'ok to stay'
    exception are split out, not dropped, so the decision and its reason stay visible."""
    today = today or datetime.now(timezone.utc)
    domains = cfg.get("internal_domains", [])
    days = {True: cfg.get("stale_days_internal", 60), False: cfg.get("stale_days_external", 180)}
    kept = {(x["email"], x["platform"]): x for x in store.exceptions()}
    stale_rows, ok_rows = [], []
    # One row per person per platform: a person in several orgs is judged on their latest login
    # across all of them, and is stale only if every active membership is.
    people = {}
    for m in store.members():
        if m["status"] != "active" or not m["email"]:
            continue
        p = people.setdefault((m["platform"], m["email"]), {"name": m["name"], "last": "", "orgs": []})
        p["last"] = max(p["last"], m["last_login"] or "")
        org = json.loads(m.get("attrs") or "{}").get("organization")
        if org:
            p["orgs"].append(org)
    for (platform, email), p in people.items():
        internal = is_internal(email, domains)
        if p["last"]:
            age = (today - datetime.strptime(p["last"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)).days
        else:
            age = None                                   # no login recorded
        if age is not None and age < days[internal]:
            continue
        row = {"platform": platform, "email": email, "name": p["name"], "internal": internal,
               "last_login": p["last"], "days_since": age, "threshold": days[internal],
               "orgs": sorted(set(p["orgs"]))}
        x = kept.get((email, platform)) or kept.get((email, "*"))
        if x:
            row.update(keep_reason=x["reason"], review_after=x["review_after"])
            ok_rows.append(row)
        else:
            stale_rows.append(row)
    key = lambda r: (not r["internal"], -(r["days_since"] if r["days_since"] is not None else 10**6))
    return sorted(stale_rows, key=key), sorted(ok_rows, key=key)


def recent_exits(store, cfg, today=None):
    """Exits (removed/deactivated) in the last N days, with where the person is STILL active,
    so 'left org A' vs 'left the platform entirely' is obvious."""
    today = today or datetime.now(timezone.utc)
    days = cfg.get("recent_exit_days", 60)
    cutoff = (today - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    domains = cfg.get("internal_domains", [])
    by_key, still = {}, defaultdict(lambda: defaultdict(list))
    for m in store.members():
        org = json.loads(m.get("attrs") or "{}").get("organization", "")
        by_key[(m["platform"], m["key"])] = org
        if m["status"] == "active" and m["email"]:
            still[m["email"]][m["platform"]].append(org)
    rows = []
    for e in store.events():
        if e["event_type"] not in ("removed", "deactivated") or e["detected_at"] < cutoff:
            continue
        when = datetime.strptime(e["detected_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        org = by_key.get((e["platform"], e["key"]), "") or (e["old_value"] if e["field"] == "organization" else "")
        rows.append({**e, "org": org, "internal": is_internal(e["email"], domains),
                     "days_ago": (today - when).days,
                     "still_active": {p: sorted(o for o in orgs if o) for p, orgs in still.get(e["email"], {}).items()},
                     "left_platform": e["platform"] not in still.get(e["email"], {})})
    return rows


def build_data(store, cfg=None) -> dict:
    cfg = cfg or {}
    domains = cfg.get("internal_domains", [])
    events, wl = store.events(), watchlist(store)
    for e in events:
        e["internal"] = is_internal(e["email"], domains)
    for r in wl:
        r["internal"] = is_internal(r["email"], domains)
    stale_rows, kept_rows = stale(store, cfg)
    members = store.members()
    snaps = store.snapshots()
    return {"events": events, "recent_exits": recent_exits(store, cfg), "recent_exit_days": cfg.get("recent_exit_days", 60), "watchlist": wl, "stale": stale_rows, "kept": kept_rows,
            "last_snapshot": snaps[0]["taken_at"] if snaps else None,
            "thresholds": {"internal": cfg.get("stale_days_internal", 60),
                           "external": cfg.get("stale_days_external", 180)},
            "totals": {p: len({m["email"] for m in members if m["platform"] == p and m["status"] == "active"})
                       for p in {m["platform"] for m in members}}}


UI_PATH = Path(__file__).with_name("ui.html")


def render(store, cfg=None, live=False) -> str:
    """Self-contained HTML. live=True leaves data null so the page fetches /api/data and enables actions."""
    blob = "null" if live else json.dumps(build_data(store, cfg)).replace("</", "<\\/")
    return UI_PATH.read_text().replace("__DATA__", blob)
