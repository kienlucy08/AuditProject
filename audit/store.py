import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY, platform TEXT, taken_at TEXT, user_count INTEGER, baseline INTEGER
);
CREATE TABLE IF NOT EXISTS members (            -- last known state per platform user
  platform TEXT, key TEXT, email TEXT, name TEXT, status TEXT, attrs TEXT,
  first_seen TEXT, last_seen TEXT, last_login TEXT DEFAULT '',
  PRIMARY KEY (platform, key)
);
CREATE TABLE IF NOT EXISTS exceptions (         -- "ok to stay" decisions, with the why
  email TEXT, platform TEXT, reason TEXT, approved_at TEXT, review_after TEXT,
  PRIMARY KEY (email, platform)                 -- platform '*' = all platforms
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, platform TEXT, key TEXT, email TEXT, name TEXT,
  event_type TEXT, field TEXT, old_value TEXT, new_value TEXT,
  detected_at TEXT, reason TEXT DEFAULT '', reason_source TEXT DEFAULT '',
  approx INTEGER DEFAULT 0          -- 1 = date backfilled from source data, not observed
);
CREATE TABLE IF NOT EXISTS resolutions (        -- watchlist items you've finished
  email TEXT, platform TEXT, resolved_at TEXT, note TEXT,
  PRIMARY KEY (email, platform)
);
CREATE INDEX IF NOT EXISTS ev_email ON events(email);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        if "approx" not in {r[1] for r in self.db.execute("PRAGMA table_info(events)")}:
            self.db.execute("ALTER TABLE events ADD COLUMN approx INTEGER DEFAULT 0")

    def previous(self, platform):
        rows = self.db.execute("SELECT * FROM members WHERE platform=?", (platform,))
        return {r["key"]: dict(email=r["email"], name=r["name"], status=r["status"],
                               attrs=json.loads(r["attrs"] or "{}")) for r in rows}

    def has_snapshot(self, platform):
        return self.db.execute("SELECT 1 FROM snapshots WHERE platform=?", (platform,)).fetchone() is not None

    def apply(self, platform, cur, events, baseline):
        """Atomically record the snapshot, events and new member state."""
        t = now()
        with self.db:
            self.db.execute("INSERT INTO snapshots(platform,taken_at,user_count,baseline) VALUES(?,?,?,?)",
                            (platform, t, len(cur), int(baseline)))
            if not baseline:
                self.db.executemany(
                    "INSERT INTO events(platform,key,email,name,event_type,field,old_value,new_value,"
                    "detected_at,reason,reason_source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    [(e.platform, e.key, e.email, e.name, e.event_type, e.field, e.old_value,
                      e.new_value, t, e.reason, e.reason_source) for e in events])
            if baseline:   # backfill exits that happened before we started watching, if the source knows when
                self.db.executemany(
                    "INSERT INTO events(platform,key,email,name,event_type,field,old_value,new_value,"
                    "detected_at,reason,reason_source,approx) VALUES(?,?,?,?,'deactivated','status','active','inactive',?,?,?,1)",
                    [(platform, k, r.email, r.name, r.inactive_since, r.reason, "source" if r.reason else "")
                     for k, r in cur.items() if r.status == "inactive" and r.inactive_since])
            for key, r in cur.items():
                self.db.execute(
                    "INSERT INTO members(platform,key,email,name,status,attrs,first_seen,last_seen,last_login) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,key) DO UPDATE SET "
                    "email=excluded.email,name=excluded.name,status=excluded.status,"
                    "attrs=excluded.attrs,last_seen=excluded.last_seen,last_login=excluded.last_login",
                    (platform, key, r.email, r.name, r.status, json.dumps(r.attrs), t, t, r.last_login))
            gone = [e.key for e in events if e.event_type == "removed"]
            for key in gone:
                self.db.execute("UPDATE members SET status='removed' WHERE platform=? AND key=?",
                                (platform, key))

    def set_reason(self, reason, event_id=None, email=None, only_missing=True):
        q, args = "UPDATE events SET reason=?, reason_source='manual' WHERE ", [reason]
        if event_id is not None:
            q += "id=?"; args.append(event_id)
        else:
            q += "lower(email)=? AND event_type IN ('removed','deactivated')"; args.append(email.lower())
            if only_missing:
                q += " AND reason=''"
        with self.db:
            return self.db.execute(q, args).rowcount

    def resolve(self, email, platform, note):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO resolutions VALUES(?,?,?,?)",
                            (email.lower(), platform, now(), note))

    def add_manual_exit(self, platform, email, org, reason, date="", name="", approx=True):
        """Record an exit the snapshots could never see (e.g. backend deletes). Safe to re-run:
        the same (platform, email, org) is only logged once."""
        email = email.strip().lower()
        dup = self.db.execute("SELECT 1 FROM events WHERE platform=? AND lower(email)=? AND event_type='removed' "
                              "AND field='organization' AND old_value=?", (platform, email, org)).fetchone()
        if dup:
            return False
        when = f"{date} 00:00:00" if date and len(date) == 10 else (date or now())
        with self.db:
            self.db.execute(
                "INSERT INTO events(platform,key,email,name,event_type,field,old_value,new_value,detected_at,"
                "reason,reason_source,approx) VALUES(?,?,?,?,'removed','organization',?,'removed',?,?,?,?)",
                (platform, f"manual:{email}:{org}", email, name, org, when, reason, "manual" if reason else "", int(approx)))
        return True

    def keep(self, email, platform, reason, review_after=""):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO exceptions VALUES(?,?,?,?,?)",
                            (email.lower(), platform or "*", reason, now(), review_after or ""))

    def unkeep(self, email, platform):
        with self.db:
            return self.db.execute("DELETE FROM exceptions WHERE email=? AND platform=?",
                                   (email.lower(), platform or "*")).rowcount

    def exceptions(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM exceptions ORDER BY email")]

    def events(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY detected_at DESC, id DESC")]

    def members(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM members")]

    def resolutions(self):
        return {(r["email"], r["platform"]): dict(r) for r in self.db.execute("SELECT * FROM resolutions")}

    def snapshots(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM snapshots ORDER BY taken_at DESC LIMIT 50")]
