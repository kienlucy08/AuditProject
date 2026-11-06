from audit.diff import diff
from audit.models import Record
from audit.report import watchlist
from audit.store import Store


def R(key, email, status="active", reason="", **attrs):
    return Record(key=key, email=email, name=key, status=status, attrs=attrs, reason=reason)


def prev_of(*recs):
    return {r.key: dict(email=r.email, name=r.name, status=r.status, attrs=r.attrs) for r in recs}


def types(evts):
    return sorted((e.event_type, e.key) for e in evts)


def test_added_removed_deactivated_changed():
    prev = prev_of(R("a", "a@x.com", role="user"), R("b", "b@x.com"), R("c", "c@x.com"))
    cur = {"a": R("a", "a@x.com", role="admin"),
           "b": R("b", "b@x.com", status="inactive", reason="left company"),
           "d": R("d", "d@x.com")}
    evts = diff("p", prev, cur)
    assert types(evts) == [("added", "d"), ("changed", "a"), ("deactivated", "b"), ("removed", "c")]
    b = next(e for e in evts if e.key == "b")
    assert b.reason == "left company" and b.reason_source == "source"


def test_readded_after_removal_and_no_noise():
    prev = prev_of(R("a", "a@x.com"))
    prev["a"]["status"] = "removed"
    assert types(diff("p", prev, {"a": R("a", "a@x.com")})) == [("readded", "a")]
    assert diff("p", prev_of(R("a", "a@x.com")), {"a": R("a", "a@x.com")}) == []


def test_watchlist_cross_platform(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.apply("survey123", {"a": R("a", "pat@x.com"), "z": R("z", "z@x.com")}, [], True)
    s.apply("arcgis", {"pat": R("pat", "pat@x.com"), "z": R("z", "z@x.com")}, [], True)
    cur = {"z": R("z", "z@x.com")}
    s.apply("survey123", cur, diff("survey123", s.previous("survey123"), cur), False)
    wl = watchlist(s)
    assert [(r["email"], r["remove_from"]) for r in wl] == [("pat@x.com", ["arcgis"])]
    assert wl[0]["left"][0]["reason"] == ""
    s.set_reason("left company", email="pat@x.com")
    assert watchlist(s)[0]["left"][0]["reason"] == "left company"
    s.resolve("pat@x.com", "arcgis", "removed")
    assert watchlist(s) == []


def test_stale_internal_external_and_keep(tmp_path):
    from datetime import datetime, timezone
    from audit.report import stale
    cfg = {"internal_domains": ["fieldsync.io"], "stale_days_internal": 60, "stale_days_external": 180}
    s = Store(str(tmp_path / "t.db"))
    old, mid = "2026-01-01 00:00:00", "2026-08-20 00:00:00"
    recs = {k: R(k, e) for k, e in [("a", "a@fieldsync.io"), ("b", "b@fieldsync.io"), ("c", "c@client.com"),
                                    ("d", "d@client.com"), ("e", "e@fieldsync.io")]}
    for k, ll in [("a", old), ("b", "2026-11-01 00:00:00"), ("c", mid), ("d", old), ("e", "")]:
        recs[k].last_login = ll
    s.apply("p", recs, [], True)
    today = datetime(2026, 11, 6, tzinfo=timezone.utc)
    rows, kept = stale(s, cfg, today)
    assert [r["email"] for r in rows] == ["e@fieldsync.io", "a@fieldsync.io", "d@client.com"]  # c (78d, external) ok
    s.keep("a@fieldsync.io", "*", "exec demo", "")
    rows, kept = stale(s, cfg, today)
    assert [r["email"] for r in kept] == ["a@fieldsync.io"] and kept[0]["keep_reason"] == "exec demo"


def test_multi_org_person_is_judged_per_platform(tmp_path):
    from datetime import datetime, timezone
    from audit.report import stale
    s = Store(str(tmp_path / "t.db"))
    a1, a2 = R("1:orgA", "x@fieldsync.io", organization="A"), R("1:orgB", "x@fieldsync.io", organization="B")
    a1.last_login, a2.last_login = "2026-01-01 00:00:00", "2026-11-01 00:00:00"
    s.apply("p", {"1:orgA": a1, "1:orgB": a2}, [], True)
    cfg = {"internal_domains": ["fieldsync.io"]}
    assert stale(s, cfg, datetime(2026, 11, 6, tzinfo=timezone.utc))[0] == []   # recent in orgB -> not stale


def test_leaving_one_org_is_not_leaving_platform(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    both = {"1:A": R("1:A", "x@x.com"), "1:B": R("1:B", "x@x.com")}
    s.apply("p", both, [], True)
    cur = {"1:B": both["1:B"]}
    s.apply("p", cur, diff("p", s.previous("p"), cur), False)
    assert watchlist(s) == []


def test_manual_exit_dedupes_and_feeds_watchlist(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.apply("arcgis", {"pat": R("pat", "pat@x.com")}, [], True)      # still active on ArcGIS
    assert s.add_manual_exit("fieldsync", "Pat@x.com", "K2Towers", "inactive cleanup", "2026-10-01")
    assert not s.add_manual_exit("fieldsync", "pat@x.com", "K2Towers", "again", "2026-10-02")   # deduped
    wl = watchlist(s)
    assert [(r["email"], r["remove_from"]) for r in wl] == [("pat@x.com", ["arcgis"])]
    assert wl[0]["left"][0]["platform"] == "fieldsync" and wl[0]["left"][0]["reason"] == "inactive cleanup"
