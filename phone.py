"""
Phone view: a second, read-only server on the LAN, off by default.

Turning it on (from Settings on the laptop) starts a listener on 0.0.0.0:8181 (or the next free port) and shows a 6-digit pairing code.
A phone that enters the code gets a token. The phone server only serves the phone page and one read-only state
endpoint; settings, the API key, plans and rekordbox export stay on the laptop-only server (127.0.0.1:8080).
Everything resets when Blendbook restarts.
"""

from __future__ import annotations

import hmac
import json
import math
import secrets
import socket
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import os

import security
import paths

PREFERRED_PORT = int(os.environ.get("BLENDBOOK_PHONE_PORT", "8181"))
MAX_BAD_CODES = 10
MIXER_STALE_SEC = 120
WEB_DIR = paths.WEB_DIR
ALLOWED_FILES = {"/m": "m.html", "/m.html": "m.html", "/css/tokens.css": "css/tokens.css",
                 "/css/app.css": "css/app.css", "/js/mobile.js": "js/mobile.js"}


class PhoneMode:
    def __init__(self, library, plan_lookup=None):
        self.library = library
        self.plan_lookup = plan_lookup  # (track_id, played_ids) -> server.plan_context's dict, or None
        self.server: ThreadingHTTPServer | None = None
        self.port: int | None = None
        self.code: str | None = None
        self.tokens: set[str] = set()
        self.bad_codes = 0
        self.mixer: dict | None = None
        self.mixer_at = 0.0
        self.lock = threading.Lock()

    # ---------- control (laptop side) ----------

    @property
    def enabled(self) -> bool:
        return self.server is not None

    def status(self) -> dict:
        return {"enabled": self.enabled, "code": self.code,
                "url": f"http://{lan_ip()}:{self.port}/m" if self.enabled else None, "paired": len(self.tokens)}

    def enable(self) -> dict:
        with self.lock:
            if not self.server:
                mode = self

                class Handler(PhoneHandler):
                    phone = mode

                # another dev server may own the preferred port: take the next free one
                for port in [*range(PREFERRED_PORT, PREFERRED_PORT + 20), 0]:
                    try:
                        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
                        break
                    except OSError:
                        continue
                if not self.server:
                    raise RuntimeError("Couldn't open a port for the phone view.")
                self.port = self.server.server_address[1]
                threading.Thread(target=self.server.serve_forever, daemon=True).start()
            self.code = f"{secrets.randbelow(10 ** 6):06d}"
            self.bad_codes = 0
        return self.status()

    def disable(self) -> dict:
        with self.lock:
            if self.server:
                srv, self.server = self.server, None
                threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()
            self.code = None
            self.port = None
            self.tokens.clear()
        return self.status()

    def set_mixer(self, state) -> None:
        self.mixer, self.mixer_at = clean_mixer(state), time.time()

    # ---------- phone side ----------

    def pair(self, code: str) -> str | None:
        with self.lock:
            if not self.code:
                return None
            if hmac.compare_digest(str(code or "").strip(), self.code):
                token = secrets.token_urlsafe(24)
                self.tokens.add(token)
                return token
            self.bad_codes += 1
            if self.bad_codes >= MAX_BAD_CODES:  # someone is guessing: turn phone mode off
                threading.Thread(target=self.disable, daemon=True).start()
            return None

    def valid(self, token: str | None) -> bool:
        return bool(token) and any(hmac.compare_digest(token, t) for t in list(self.tokens))

    def state(self) -> dict:
        live = self.library.live()
        np_ = live.get("now_playing")
        mixer = None
        age = time.time() - self.mixer_at
        if self.mixer and age < MIXER_STALE_SEC:
            mixer = {"updated_ms_ago": int(age * 1000), **self.mixer}
        plan = self._plan(live)
        plan_next_id = (plan or {}).pop("next_id", None)
        return {
            "now_playing": {**{k: np_.get(k) for k in ("title", "artist", "bpm", "genre", "length", "since_logged_sec")},
                            "active": live.get("active")} if np_ else None,
            "plan": plan,
            "suggestions": [{k: s.get(k) for k in ("title", "artist", "bpm", "bpm_delta_pct", "reasons")}
                            for s in (live.get("suggestions") or []) if s.get("id") != plan_next_id][:3],
            "session": {k: (live.get("session") or {}).get(k) for k in ("track_count", "started_at", "track_pace_sec")}
                       if live.get("session") else None,
            "mixer": mixer,
        }

    def _plan(self, live: dict) -> dict | None:
        """The saved plan the playing track is in, cut down to what the phone shows (plus next_id, for state())."""
        if not self.plan_lookup or not live.get("active"):
            return None
        try:
            ctx = self.plan_lookup(live["now_playing"]["id"], live["session"]["played_ids"])
        except Exception:  # the plan is extra: never let it blank the rest of the phone view
            return None
        if not ctx:
            return None
        nxt = ctx.get("next")
        return {"name": ctx.get("name"), "next_position": ctx.get("next_position"), "length": ctx.get("length"),
                "next": {k: nxt.get(k) for k in ("title", "artist", "bpm", "bpm_delta_pct")} if nxt else None,
                "next_id": nxt.get("id") if nxt else None}


class PhoneHandler(SimpleHTTPRequestHandler):
    phone: PhoneMode = None  # set per server

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        for name, value in security.security_headers(WEB_DIR):
            self.send_header(name, value)
        super().end_headers()

    def _json(self, data, status=200):
        raw = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/phone/state":
            if not self.phone.valid(self.headers.get("X-Blendbook-Token")):
                return self._json({"error": "Pair this phone first"}, 401)
            return self._json(self.phone.state())
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/m")
            self.end_headers()
            return
        rel = ALLOWED_FILES.get(path)
        if not rel:
            return self._json({"error": "Not available on the phone"}, 404)
        f = WEB_DIR / rel
        if not f.exists():
            return self._json({"error": "Not found"}, 404)
        data = f.read_bytes()
        ctype = {"html": "text/html", "css": "text/css", "js": "text/javascript"}[f.suffix[1:]]
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self):
        # the base class would answer HEAD from the folder Blendbook was started in, telling anyone on the Wi-Fi
        # whether a file exists there, and its size and date
        self.send_response(405)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/pair":
            return self._json({"error": "Not available on the phone"}, 404)
        try:
            length = max(0, min(int(self.headers.get("Content-Length") or 0), 1024))
            code = json.loads(self.rfile.read(length) or b"{}").get("code")
        except (ValueError, AttributeError):
            code = None
        token = self.phone.pair(code)
        if not token:
            return self._json({"error": "That code didn't match. Check Blendbook's Settings on your laptop."}, 403)
        return self._json({"token": token})


def _unit(v) -> float | None:
    """A 0..1 control position, or None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return round(max(0.0, min(1.0, float(v))), 3)


def _secs(v) -> float:
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return round(max(0.0, min(3600.0, float(v))), 1) if ok else 0.0


def clean_mixer(m) -> dict | None:
    """Keep only the fields the phone shows, with the types it expects. The laptop page sends this, but anything
    the laptop server accepts should be treated as untrusted before it's relayed to another device."""
    if not isinstance(m, dict):
        return None
    decks = {}
    raw_decks = m.get("decks") if isinstance(m.get("decks"), dict) else {}
    for n, d in raw_decks.items():
        if str(n) not in ("1", "2", "3", "4") or not isinstance(d, dict):
            continue
        eq = d.get("eq") if isinstance(d.get("eq"), dict) else {}
        decks[str(n)] = {"playing": bool(d.get("playing")), "audible": bool(d.get("audible")),
                         "fader": _unit(d.get("fader")), "low": _unit(eq.get("low", d.get("low")))}
    t = m.get("currentTransition")
    transition = None
    if isinstance(t, dict):
        deck = lambda v: int(v) if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 4 else None  # noqa: E731
        moves = t.get("eqMoves")
        transition = {"from": deck(t.get("from")), "to": deck(t.get("to")), "elapsedSec": _secs(t.get("elapsedSec")),
                      "bassOverlapSec": _secs(t.get("bassOverlapSec")),
                      "eqMoves": int(_secs(moves)) if moves is not None else 0}
    return {"bassClash": bool(m.get("bassClash")), "currentTransition": transition, "decks": decks}


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
