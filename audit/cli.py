import argparse
import os
import sys
from pathlib import Path

import yaml

from .collectors import COLLECTORS
from .diff import diff
from .envfile import load_env
from .report import pending_reasons, render, stale, watchlist
from .store import Store


def load_config(path):
    load_env()
    raw = yaml.safe_load(Path(path).read_text())

    def sub(x):   # expand ${VAR} per value after parsing, so special chars in secrets can't break YAML
        if isinstance(x, str):
            return os.path.expandvars(x)
        if isinstance(x, dict):
            return {k: sub(v) for k, v in x.items()}
        if isinstance(x, list):
            return [sub(v) for v in x]
        return x
    return sub(raw)


def snapshot(cfg, store, only=None):
    ok = True
    for name, pcfg in cfg["platforms"].items():
        if only and name != only:
            continue
        try:
            cur = COLLECTORS[pcfg["type"]](pcfg)
        except Exception as exc:                       # one platform failing shouldn't block the other
            print(f"[{name}] FAILED to collect: {exc}", file=sys.stderr)
            ok = False
            continue
        baseline = not store.has_snapshot(name)
        prev = store.previous(name)
        prev_active = sum(1 for v in prev.values() if v["status"] != "removed")
        limit = cfg.get("max_drop_fraction", 0.25)
        if prev_active and (len(cur) == 0 or (prev_active - len(cur)) / prev_active > limit):
            print(f"[{name}] ABORTED: {len(cur)} users fetched vs {prev_active} known "
                  f"(> {limit:.0%} drop). Check the query/credentials; nothing was recorded.", file=sys.stderr)
            ok = False
            continue
        events = [] if baseline else diff(name, prev, cur)
        store.apply(name, cur, events, baseline)
        if baseline:
            print(f"[{name}] baseline recorded: {len(cur)} users (no events on first run)")
        else:
            counts = {}
            for e in events:
                counts[e.event_type] = counts.get(e.event_type, 0) + 1
            print(f"[{name}] {len(cur)} users, events: {counts or 'none'}")
    return ok


def main():
    ap = argparse.ArgumentParser(prog="audit", description="Track platform users joining/changing/leaving.")
    ap.add_argument("-c", "--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="fetch current users and log changes")
    s.add_argument("--platform")
    r = sub.add_parser("report", help="write the HTML dashboard")
    r.add_argument("-o", "--out")
    sub.add_parser("pending", help="list exits that still need a reason")
    sub.add_parser("watchlist", help="print the cross-platform removal watchlist")
    w = sub.add_parser("reason", help="record why someone left")
    w.add_argument("text")
    g = w.add_mutually_exclusive_group(required=True)
    g.add_argument("--event", type=int, help="event id")
    g.add_argument("--email", help="apply to all of this person's exits that lack a reason")
    sv = sub.add_parser("serve", help="open the interactive audit UI in your browser")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-open", action="store_true")
    le = sub.add_parser("log-exit", help="record an exit snapshots can't see (backend delete, etc.)")
    le.add_argument("email")
    le.add_argument("reason")
    le.add_argument("--platform", default="fieldsync")
    le.add_argument("--org", default="", help="organization they were removed from")
    le.add_argument("--date", default="", help="YYYY-MM-DD (default: today)")
    ie = sub.add_parser("import-exits", help="bulk-record exits from a CSV: platform,email,organization,reason,date,approx")
    ie.add_argument("csv")
    sub.add_parser("stale", help="list users with no recent login that still need a decision")
    k = sub.add_parser("keep", help="record that a person is OK to stay, and why")
    k.add_argument("email")
    k.add_argument("text")
    k.add_argument("--platform", help="limit to one platform (default: all)")
    k.add_argument("--review-after", default="", help="YYYY-MM-DD to revisit")
    u = sub.add_parser("unkeep", help="remove an 'ok to stay' exception")
    u.add_argument("email")
    u.add_argument("--platform")
    d = sub.add_parser("resolve", help="mark a watchlist removal as done")
    d.add_argument("email")
    d.add_argument("platform")
    d.add_argument("--note", default="removed")
    a = ap.parse_args()

    cfg = load_config(a.config)
    store = Store(cfg.get("database", "audit.db"))

    if a.cmd == "snapshot":
        sys.exit(0 if snapshot(cfg, store, a.platform) else 1)
    elif a.cmd == "report":
        out = Path(a.out or Path(cfg.get("report_dir", "reports")) / "audit.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(store, cfg))
        print(f"wrote {out}")
    elif a.cmd == "log-exit":
        ok = store.add_manual_exit(a.platform, a.email, a.org, a.reason, a.date, approx=not a.date)
        print("recorded" if ok else "already recorded")
    elif a.cmd == "import-exits":
        import csv
        n = dup = 0
        with open(a.csv, newline="") as fh:
            for row in csv.DictReader(fh):
                added = store.add_manual_exit(row["platform"], row["email"], row.get("organization", ""),
                                              row.get("reason", ""), row.get("date", ""), row.get("name", ""),
                                              approx=row.get("approx", "1") not in ("0", "false", "no"))
                n, dup = n + added, dup + (not added)
        print(f"recorded {n}, skipped {dup} already recorded")
    elif a.cmd == "serve":
        from .server import serve
        serve(cfg, snapshot, a.port, not a.no_open)
    elif a.cmd == "stale":
        rows, kept = stale(store, cfg)
        for r in rows:
            age = f"{r['days_since']}d" if r["days_since"] is not None else "never"
            print(f"{'INT' if r['internal'] else 'ext'}  {r['platform']:<10} {r['email']:<38} last login: {age}")
        print(f"\n{len(rows)} need a decision, {len(kept)} approved to stay")
    elif a.cmd == "keep":
        store.keep(a.email, a.platform, a.text, a.review_after)
        print("recorded")
    elif a.cmd == "unkeep":
        print(f"removed {store.unkeep(a.email, a.platform)} exception(s)")
    elif a.cmd == "pending":
        for e in pending_reasons(store):
            print(f"#{e['id']:<5} {e['detected_at']}  {e['platform']:<10} {e['event_type']:<12} {e['email']}")
    elif a.cmd == "watchlist":
        for r in watchlist(store):
            why = "; ".join(l["reason"] or "NO REASON" for l in r["left"])
            print(f"{r['email']}: left {[l['platform'] for l in r['left']]} ({why}) -> remove from {r['remove_from']}")
    elif a.cmd == "reason":
        n = store.set_reason(a.text, event_id=a.event, email=a.email)
        print(f"updated {n} event(s)")
    elif a.cmd == "resolve":
        store.resolve(a.email, a.platform, a.note)
        print("marked resolved")


if __name__ == "__main__":
    main()
