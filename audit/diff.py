"""Pure diff logic: previous known state + current fetch -> events."""
from .models import Event, Record


def diff(platform: str, prev: dict, cur: dict) -> list:
    """prev: key -> {email,name,status,attrs}  (status may be 'removed')
    cur:  key -> Record
    """
    events = []

    def ev(rec_key, email, name, etype, **kw):
        events.append(Event(platform, rec_key, email, name, etype, **kw))

    for key, rec in cur.items():
        old = prev.get(key)
        src = dict(reason=rec.reason, reason_source="source" if rec.reason else "")
        if old is None:
            ev(key, rec.email, rec.name, "added", new_value=rec.status)
            continue
        if old["status"] == "removed":
            ev(key, rec.email, rec.name, "readded", new_value=rec.status)
            continue
        if old["status"] != rec.status:
            etype = "deactivated" if rec.status == "inactive" else "reactivated"
            ev(key, rec.email, rec.name, etype, field="status",
               old_value=old["status"], new_value=rec.status, **src)
        for f in sorted(set(old["attrs"]) | set(rec.attrs)):
            o, n = old["attrs"].get(f), rec.attrs.get(f)
            if str(o) != str(n):
                ev(key, rec.email, rec.name, "changed", field=f,
                   old_value="" if o is None else str(o),
                   new_value="" if n is None else str(n))
        if old["email"] != rec.email:
            ev(key, rec.email, rec.name, "changed", field="email",
               old_value=old["email"], new_value=rec.email)

    for key, old in prev.items():
        if key not in cur and old["status"] != "removed":
            ev(key, old["email"], old["name"], "removed",
               old_value=old["status"], new_value="removed")
    return events
