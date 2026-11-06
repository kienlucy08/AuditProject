"""Local web UI: serves the audit dashboard and records decisions. Standard library only.

Binds to 127.0.0.1 and rejects requests whose Host/Origin isn't local, so other sites open in
your browser can't post decisions to it.
"""
import contextlib
import io
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .report import build_data, render
from .store import Store


def make_handler(cfg, snapshot_fn):
    lock = threading.Lock()          # one writer at a time

    class H(BaseHTTPRequestHandler):
        server_version = "audit"

        def log_message(self, *a):   # quiet
            pass

        def _local_only(self):
            port = self.server.server_address[1]
            ok = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in ok:
                return False
            origin = self.headers.get("Origin")
            return origin is None or urlparse(origin).netloc in ok

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj))

        def do_GET(self):
            if not self._local_only():
                return self._json(403, {"error": "forbidden"})
            store = Store(cfg.get("database", "audit.db"))
            if self.path in ("/", "/index.html"):
                self._send(200, render(store, cfg, live=True), "text/html; charset=utf-8")
            elif self.path == "/api/data":
                self._json(200, build_data(store, cfg))
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._local_only() or "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._json(403, {"error": "forbidden"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                with lock:
                    store = Store(cfg.get("database", "audit.db"))
                    self._json(200, self.route(store, body))
            except (ValueError, KeyError) as exc:
                self._json(400, {"error": f"bad request: {exc}"})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def route(self, store, b):
            p = self.path
            if p == "/api/keep":          # b: items=[{email,platform}], reason, review_after
                reason = b["reason"].strip()
                if not reason:
                    raise ValueError("a reason is required")
                for it in b["items"]:
                    store.keep(it["email"], it.get("platform") or "*", reason, b.get("review_after") or "")
                return {"saved": len(b["items"])}
            if p == "/api/unkeep":
                return {"removed": store.unkeep(b["email"], b.get("platform") or "*")}
            if p == "/api/reason":        # b: event_id | email, reason
                reason = b["reason"].strip()
                if not reason:
                    raise ValueError("a reason is required")
                n = store.set_reason(reason, event_id=b.get("event_id"), email=b.get("email"),
                                     only_missing=False if b.get("event_id") else True)
                return {"updated": n}
            if p == "/api/resolve":
                store.resolve(b["email"], b["platform"], b.get("note") or "removed")
                return {"ok": True}
            if p == "/api/refresh":       # run a fresh snapshot (needs the DB tunnel / ArcGIS reachable)
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    ok = snapshot_fn(cfg, store)
                return {"ok": ok, "log": (out.getvalue() + err.getvalue()).strip()}
            raise KeyError(f"unknown action {p}")

    return H


def serve(cfg, snapshot_fn, port=8765, open_browser=True):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, snapshot_fn))
    url = f"http://127.0.0.1:{port}/"
    print(f"Audit UI running at {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
