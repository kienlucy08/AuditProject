from datetime import datetime, timezone

import requests

from .models import Record

REQUIRED = {"id", "email", "name", "status"}


def collect_postgres(cfg) -> dict:
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(host=cfg["host"], port=cfg.get("port", 5432), dbname=cfg["dbname"],
                            user=cfg["user"], password=cfg["password"], connect_timeout=15)
    try:
        conn.set_session(readonly=True, autocommit=True)   # this tool never writes to your DB
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(cfg["query"])
            rows = cur.fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        missing = REQUIRED - set(r)
        if missing:
            raise ValueError(f"Postgres query must return columns {sorted(REQUIRED)}; missing {sorted(missing)}")
        extras = {k: v for k, v in r.items() if k not in REQUIRED | {"reason", "last_login", "inactive_since"}}
        ll = r.get("last_login")
        ins = r.get("inactive_since")
        out[str(r["id"])] = Record(key=str(r["id"]), email=(r["email"] or "").strip().lower(),
                                   name=r["name"] or "", status=(r["status"] or "active").lower(),
                                   attrs={k: "" if v is None else str(v) for k, v in extras.items()},
                                   reason=r.get("reason") or "",
                                   inactive_since=ins.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ins, "strftime") else (ins or ""),
                                   last_login=ll.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ll, "strftime") else (ll or ""))
    return out


def _arcgis_token(cfg):
    base = cfg["portal_url"].rstrip("/")
    if cfg.get("client_id"):
        r = requests.post(f"{base}/sharing/rest/oauth2/token", timeout=30, data={
            "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
            "grant_type": "client_credentials", "f": "json"})
    else:
        r = requests.post(f"{base}/sharing/rest/generateToken", timeout=30, data={
            "username": cfg["username"], "password": cfg["password"],
            "referer": base, "client": "referer", "f": "json"})
    r.raise_for_status()
    body = r.json()
    if "access_token" in body:
        return body["access_token"]
    if "token" in body:
        return body["token"]
    raise RuntimeError(f"ArcGIS auth failed: {body.get('error', body)}")


def collect_arcgis(cfg) -> dict:
    base = cfg["portal_url"].rstrip("/")
    token = _arcgis_token(cfg)
    # Esri's built-in role ids are fixed; custom roles are looked up below.
    roles = {"iAAAAAAAAAAAAAAA": "Viewer", "iBBBBBBBBBBBBBBB": "Data Editor"}
    try:
        rr = requests.get(f"{base}/sharing/rest/portals/self/roles", timeout=60,
                          params={"f": "json", "token": token, "num": 100}).json()
        roles.update({r["id"]: r["name"] for r in rr.get("roles", [])})
    except Exception:
        pass
    out, start = {}, 1
    while start != -1:
        r = requests.get(f"{base}/sharing/rest/portals/self/users", timeout=60,
                         params={"f": "json", "token": token, "num": 100, "start": start})
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"ArcGIS users call failed: {body['error']}")
        for u in body.get("users", []):
            ms = u.get("lastLogin") or -1          # epoch ms; -1 = never logged in
            last = (datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    if ms and ms > 0 else "")
            out[u["username"]] = Record(
                last_login=last,
                key=u["username"], email=(u.get("email") or "").strip().lower(),
                name=u.get("fullName", ""),
                status="inactive" if u.get("disabled") else "active",
                attrs={"role": roles.get(u.get("roleId") or u.get("role"), u.get("roleId") or u.get("role", "")),
                       "user_type": u.get("userLicenseTypeId") or u.get("userType", ""),
                       "level": u.get("level", ""),
                       "user_license_type": u.get("userLicenseTypeId", "")})
        start = body.get("nextStart", -1)
    return out


COLLECTORS = {"postgres": collect_postgres, "arcgis": collect_arcgis}
