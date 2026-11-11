#!/usr/bin/env python3
"""
Blendbook server: serves the web app and a JSON API over the user's real rekordbox library.

Run with the project venv (pyrekordbox + anthropic):  .venv/bin/python server.py
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
import math
import os
import re
import threading
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import paths

if __name__ == "__main__":
    paths.migrate_legacy()  # before db/rb are imported: they open files in the data folder

import coach_local
import diagnostics
import goals as goals_mod
import mixlog
import online
import planner
import recordings
import security
import updates
from phone import PhoneMode
from db import Store
from rb import LIVE_WINDOW_SEC, RekordboxLibrary

WEB_DIR = paths.WEB_DIR
CONFIG_PATH = paths.CONFIG_PATH
PORT = int(os.environ.get("PORT", "8080"))
MODEL = "claude-opus-5-5"
VERSION = "1.0.0"

library = RekordboxLibrary()
store = Store()
phone = PhoneMode(library, plan_lookup=lambda track_id, played: plan_context(track_id, played))
MAX_BODY = 2_000_000
MAX_EVENTS = 1000


CLIENT_SESSION = re.compile(r"[A-Za-z0-9-]{1,64}")  # a browser-made UUID; also used in file names


class BadRequest(ValueError):
    """Raised by request parsing helpers; becomes a 400 with this message."""


def text_field(payload: dict, key: str, max_len: int) -> str:
    v = payload.get(key)
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise BadRequest(f"{key} should be text")
    return str(v).strip()[:max_len]


def id_list(payload: dict, key: str, max_items: int = 200) -> list[str]:
    v = payload.get(key) or []
    if not isinstance(v, list):
        raise BadRequest(f"{key} should be a list")
    return [str(i)[:40] for i in v[:max_items] if isinstance(i, (str, int)) and not isinstance(i, bool)]


def num(value, default=None, lo=-math.inf, hi=math.inf) -> float | None:
    """A finite number clamped to [lo, hi], or default if value isn't one."""
    if isinstance(value, bool):
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v)) if math.isfinite(v) else default


def all_details(since: datetime | None = None) -> list[dict]:
    """Every set, oldest first, with controller data attached. With `since`, only the sets from then on, plus the
    last set whenever it was: each detail is a lot of work, and the weekly digest needs no more."""
    sessions = library.sessions
    if since:
        sessions = [s for s in sessions[:-1] if s["started_at"] >= since] + sessions[-1:]
    return [session_detail(s["id"]) for s in sessions]


def session_detail(session_id: str) -> dict | None:
    detail = library.session_detail(session_id)
    if not detail:
        return None
    return recordings.enrich(mixlog.enrich(detail, store), store)


def plan_context(track_id: str | None, played: list[str]) -> dict | None:
    """If the track playing is in a saved plan: where you are in it and what the plan says comes next.

    A track can sit in several plans; the one you've played most of tonight wins, then the newest."""
    if not track_id or track_id not in library.tracks:
        return None
    played_set = set(played)
    best = None
    for p in store.set_plans():  # newest first
        ids = [t for t in resolve_ids(p["track_ids"]) if library.track(t)]
        if track_id not in ids:
            continue
        overlap = len(played_set & set(ids))
        if best is None or overlap > best[0]:
            best = (overlap, p, ids)
    if not best:
        return None
    overlap, p, ids = best
    i = ids.index(track_id)
    upcoming = [t for t in ids[i + 1:] if t not in played_set]
    cur_bpm = library.tracks[track_id]["bpm"]

    def brief(tid):
        t = with_links(library._brief(tid))
        t["bpm_delta_pct"] = round(100 * (t["bpm"] - cur_bpm) / cur_bpm, 1) if t["bpm"] and cur_bpm else None
        return t

    return {"plan_id": p["id"], "name": p["name"], "position": i + 1, "length": len(ids),
            "played_from_plan": overlap, "next": brief(upcoming[0]) if upcoming else None,
            "next_position": ids.index(upcoming[0]) + 1 if upcoming else None,
            "then": [brief(t) for t in upcoming[1:3]]}


def current_set_ids() -> list[str]:
    """Track ids of the set rekordbox is logging right now (empty if nothing is live)."""
    if not library.sessions:
        return []
    plays = library.sessions[-1]["plays"]
    since = (datetime.now() - plays[-1]["played_at"]).total_seconds()
    return [p["track_id"] for p in plays] if 0 <= since < LIVE_WINDOW_SEC else []


# ---------- online catalogs (online.py) ----------

enricher = online.Enricher()
dig_jobs: dict[str, online.DigJob] = {}
_index_cache: dict = {}


def refresh_online() -> None:
    library.set_online(store.track_online(), store.online_tracks())


def library_index() -> online.LibraryIndex:
    """Spots online results that are already in your library (rebuilt when rekordbox's library changes)."""
    if _index_cache.get("at") != (library.loaded_at, len(library.tracks)):
        _index_cache.update(at=(library.loaded_at, len(library.tracks)), value=online.LibraryIndex(
            t for t in library.tracks.values() if not t["is_sample"]))
    return _index_cache["value"]


def resolve_ids(ids: list[str]) -> list[str]:
    """Plan tracks found online become their rekordbox id once you've added them to your library."""
    index = library_index()
    out = []
    for tid in ids:
        t = library.online_tracks.get(tid) if tid.startswith("web:") else None
        out.append((index.find(t["title"], t["artist"]) or tid) if t else tid)
    return out


def store_country() -> str:
    """Two-letter Apple Music storefront for links: Settings, else this Mac's region, else us."""
    c = str(load_config().get("store_country") or "")
    if re.fullmatch(r"[a-z]{2}", c):
        return c
    if "mac" not in _index_cache:
        try:
            if paths.IS_MAC:
                import subprocess
                loc = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=3).stdout
            else:
                import locale
                loc = locale.getlocale()[0] or ""  # en_GB on most systems, English_United Kingdom on Windows
            m = re.search(r"_([A-Z]{2})\b", loc)
            _index_cache["mac"] = m.group(1).lower() if m else "us"
        except Exception:
            _index_cache["mac"] = "us"
    return _index_cache["mac"]


def with_links(t: dict) -> dict:
    """Where to find an online track: rekordbox streams Apple Music and Spotify, so link both."""
    if t.get("online"):
        t["apple_url"] = t.get("apple_url") or online.apple_search_url(t["title"], t["artist"], store_country())
        t["spotify_url"] = t.get("spotify_url") or online.spotify_search_url(t["title"], t["artist"])
        t["beatport_url"] = online.beatport_search_url(t["title"], t["artist"])
    return t


def remember_online(tracks: list[dict]) -> None:
    """Keep online results so a plan can hold them. Previews are for streaming only, so those are never kept."""
    keep = ("id", "apple_id", "title", "artist", "bpm", "key", "energy", "genre", "length", "spotify_url", "apple_url")
    rows = [{k: t.get(k) for k in keep} for t in tracks if str(t.get("id", "")).startswith("web:")]
    if rows:
        store.put_online_tracks(rows)
        refresh_online()


def online_status() -> dict:
    music = [t for t in library.tracks.values() if not t["is_sample"]]
    checked = store.track_online()
    no_key = Counter(t["source"] if t["source"] in ("apple-music", "spotify", "file") else "other"
                     for t in music if not t.get("rb_key"))
    return {"enrich": enricher.status(), "country": store_country(),
            "coverage": {"tracks": len(music), "with_key": sum(1 for t in music if t["key"]),
                         "from_online": sum(1 for t in music if t.get("key_source") == "online"),
                         "rekordbox_keys": sum(1 for t in music if t.get("rb_key")),
                         "no_rekordbox_key": dict(no_key),
                         "rekordbox_energy": sum(1 for t in music if t.get("energy_source") == "rekordbox"),
                         "checked": sum(1 for t in music if t["id"] in checked and checked[t["id"]]["status"] != "error")}}


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    # the file holds the API key: create it owner-only from the start, then swap it in atomically
    CONFIG_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)


def valid_rekordbox_folder(path: str) -> Path:
    """The folder holding rekordbox's master.db: the one given, or a rekordbox folder inside it."""
    folder = Path(path).expanduser()
    for candidate in (folder, folder / "rekordbox"):
        if (candidate / "master.db").is_file():
            return candidate
    raise BadRequest("That folder has no rekordbox library in it (it should contain master.db).")


def pick_folder() -> str | None:
    """The computer's own folder picker: the page can't ask a browser for a real path. None if it was cancelled."""
    import subprocess

    if paths.IS_MAC:
        cmd = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose the folder that holds rekordbox\'s master.db")']
    elif paths.IS_WINDOWS:
        cmd = ["powershell", "-NoProfile", "-Command",
               "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; "
               "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"]
    else:
        raise BadRequest("Set REKORDBOX_DIR instead: there's no folder picker on this system.")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **paths.NO_WINDOW)
    return out.stdout.strip() or None


def apply_saved_folder() -> None:
    """The folder chosen in Settings, unless it's gone or REKORDBOX_DIR says otherwise."""
    saved = load_config().get("rekordbox_dir")
    if saved and not os.environ.get("REKORDBOX_DIR") and (Path(saved) / "master.db").is_file():
        library.rb_dir = Path(saved)


def anthropic_client():
    """Return an Anthropic client if any credential is available, else None."""
    try:
        import anthropic
    except ImportError:
        return None
    key = load_config().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return anthropic.Anthropic(api_key=key)
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return anthropic.Anthropic()
    return None


COACH_SYSTEM = """You are Blendbook, a sharp, friendly DJ coach for a rekordbox DJ (usually on a Pioneer DDJ controller).
You are given JSON pulled from the DJ's real rekordbox history (and, when live, from their controller).
Ground every claim in that data: cite track numbers, titles, BPMs, times. Never invent keys, scores, or
audio details that are not in the data. If something can't be known from the data, say so briefly.
Be concrete about controller moves (LOW/MID/HI EQ, channel faders, filter, Beat FX, loops)
only when relevant. Keep it tight: short paragraphs or a few bullets, no preamble."""


def coach_answer(question: str, context: dict, scope: str) -> dict:
    live = scope == "live"
    client = anthropic_client()
    if client is None:
        return {"source": "local", "answer": coach_local.answer(question, context, scope)}

    import anthropic

    prompt = f"Data:\n```json\n{json.dumps(context, default=str)[:60000]}\n```\n\nQuestion: {question}"
    if live:
        prompt += "\n\nThe DJ is mid-set: answer in 2-3 sentences they can read in one glance."
    try:
        # mid-set, a late answer is useless: give up fast and let the built-in coach answer instead
        resp = client.with_options(timeout=40.0 if live else 180.0, max_retries=0 if live else 2).beta.messages.create(
            model=MODEL,
            max_tokens=4000 if live else 16000,
            system=COACH_SYSTEM,
            output_config={"effort": "low" if live else "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        return {"source": "local", "answer": coach_local.answer(question, context, scope),
                "warning": "Your Anthropic API key was rejected. Using the built-in coach."}
    except anthropic.RateLimitError:
        return {"source": "local", "answer": coach_local.answer(question, context, scope),
                "warning": "Claude is rate limited right now. Using the built-in coach."}
    except anthropic.APIStatusError as e:
        return {"source": "local", "answer": coach_local.answer(question, context, scope),
                "warning": f"Claude returned an error ({e.status_code}). Using the built-in coach."}
    except anthropic.APITimeoutError:
        return {"source": "local", "answer": coach_local.answer(question, context, scope),
                "warning": "Claude took too long. Using the built-in coach."}
    except anthropic.APIConnectionError:
        return {"source": "local", "answer": coach_local.answer(question, context, scope),
                "warning": "Couldn't reach Claude (offline?). Using the built-in coach."}
    if resp.stop_reason == "refusal":
        return {"source": "local", "answer": coach_local.answer(question, context, scope)}
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        return {"source": "local", "answer": coach_local.answer(question, context, scope),
                "warning": "Claude didn't return an answer. Using the built-in coach."}
    return {"source": MODEL, "answer": text}


def every(seconds: float, job) -> None:
    """Run job forever. A failure is logged and retried: a background loop that dies takes Live down with it."""
    while True:
        try:
            job()
        except Exception:
            traceback.print_exc()
        time.sleep(seconds)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        for name, value in security.security_headers(WEB_DIR):
            self.send_header(name, value)
        super().end_headers()

    def _json(self, data, status: int = 200):
        raw = json.dumps(data, default=str, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _allowed(self) -> bool:
        """Refuse DNS-rebound and cross-site requests before doing anything (see security.py)."""
        port = self.server.server_address[1]
        if not security.allowed_host(self.headers.get("Host"), port):
            self._json({"error": f"Blendbook only answers at http://localhost:{port}"}, 421)
            return False
        if self.command == "POST":
            why = security.same_origin_post(self.headers, port)
            if why:
                self._json({"error": why}, 403)
                return False
        return True

    def _run(self, route):
        try:
            route()
        except BadRequest as e:
            self._json({"error": str(e)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._json({"error": "Something went wrong in Blendbook. Details are in the server log."}, 500)
            except Exception:
                pass

    def do_GET(self):
        if self._allowed():
            self._run(self._get)

    def do_HEAD(self):
        if self._allowed():
            super().do_HEAD()

    def do_POST(self):
        if self._allowed():
            self._run(self._post)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadRequest("Bad Content-Length")
        if length < 0:
            raise BadRequest("Bad Content-Length")
        if length > MAX_BODY:
            raise BadRequest("Request too large")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise BadRequest("Invalid JSON")
        if not isinstance(payload, dict):
            raise BadRequest("Expected a JSON object")
        return payload

    def _send_file_range(self, path: Path):
        """Serve a local recording with HTTP Range support so the browser can seek."""
        if not path.is_file() or path.suffix.lower() not in recordings.audio.AUDIO_EXT:
            return self._json({"error": "File not found"}, 404)
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", (rng or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        else:
            m = None
        length = end - start + 1
        ctype = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac",
                 ".aif": "audio/aiff", ".aiff": "audio/aiff", ".ogg": "audio/ogg", ".webm": "audio/webm"}.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 16, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def _get(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path

        if path == "/api/status":
            st = library.status()
            st["ai"] = "claude" if anthropic_client() else "local"
            st.update(version=VERSION, data_dir=str(paths.DATA_DIR), recordings_dir=str(paths.RECORDINGS_DIR),
                      platform="windows" if paths.IS_WINDOWS else "mac" if paths.IS_MAC else "other")
            return self._json(st)
        if path == "/api/sessions":
            sessions = library.list_sessions()
            for s in sessions:
                s["has_controller"] = mixlog.has_controller_data(store, s)
            return self._json({"sessions": sessions})
        if path == "/api/goals":
            goals = store.goals()
            details = all_details() if goals else []
            return self._json({
                "metrics": goals_mod.METRICS,
                "goals": [goals_mod.goal_view(g, details) for g in goals],
            })
        if path == "/api/plans":
            plans = store.set_plans()
            for p in plans:
                p["tracks"] = [with_links(library._brief(t)) for t in resolve_ids(p["track_ids"]) if library.track(t)]
            return self._json({"plans": plans, "rekordbox_running": planner.rekordbox_running()})
        if path == "/api/online":
            return self._json(online_status())
        if path == "/api/update":
            cfg = load_config()
            return self._json({"enabled": bool(cfg.get("check_updates")),
                               "update": updates.check(VERSION) if cfg.get("check_updates") else None})
        if path == "/api/diagnostics":
            return self._json({"text": diagnostics.report(
                VERSION, library.status(), online_status(), ai=bool(anthropic_client()))})
        m = re.fullmatch(r"/api/online/dig/([0-9a-f]{1,32})", path)
        if m:
            job = dig_jobs.get(m.group(1))
            if not job:
                return self._json({"error": "That search has expired. Dig again."}, 404)
            st = job.status()
            st["tracks"] = [with_links(t) for t in st["tracks"]]
            # remember results as they arrive, so one added to a plan mid-dig is known when the plan is saved
            seen = job.__dict__.setdefault("remembered", set())
            remember_online([t for t in st["tracks"] if t["id"] not in seen])
            seen.update(t["id"] for t in st["tracks"])
            return self._json(st)
        if path == "/api/digest":
            # the digest compares this week with last week
            return self._json(goals_mod.digest(all_details(since=datetime.now() - timedelta(days=14)), store.goals()))
        if path == "/api/recordings":
            return self._json({"recordings": store.recordings(), **recordings.status()})
        m = re.fullmatch(r"/api/recordings/(\d+)/(features|audio)", path)
        if m:
            rec = store.recording(int(m.group(1)))
            if not rec or not rec["features_path"]:
                return self._json({"error": "Recording not found"}, 404)
            if m.group(2) == "features":
                return self._json(json.loads(Path(rec["features_path"]).read_text()))
            return self._send_file_range(Path(rec["path"]))
        if path == "/api/mixlog/status":
            return self._json(store.mix_log_status())
        m = re.fullmatch(r"/api/sessions/(\w+)", path)
        if m:
            detail = session_detail(m.group(1))
            return self._json(detail) if detail else self._json({"error": "Session not found"}, 404)
        if path == "/api/insights":
            return self._json(library.insights())
        if path == "/api/live":
            live = library.live(direction=q.get("direction", "hold"))
            if live.get("active"):
                live["plan"] = plan_context(live["now_playing"]["id"], live["session"]["played_ids"])
            return self._json({**live, "phone_enabled": phone.enabled})
        if path == "/api/phone":
            return self._json(phone.status())
        if path == "/api/suggest":
            played = current_set_ids()
            exclude = set(filter(None, q.get("exclude", "").split(","))) | set(played)
            track_id = q.get("track_id", "")
            return self._json({"suggestions": library.suggest(track_id, exclude=exclude, direction=q.get("direction", "hold")),
                               "plan": plan_context(track_id, played)})
        if path == "/api/search":
            return self._json({"tracks": library.search(q.get("q", ""))})
        if path.startswith("/api/"):
            return self._json({"error": "Not found"}, 404)
        return super().do_GET()

    def _post(self):
        payload = self._read_json()

        if self.path == "/api/rekordbox/folder":
            cfg = load_config()
            if payload.get("reset"):
                cfg.pop("rekordbox_dir", None)
                folder = paths.RB_DIR
            else:
                chosen = text_field(payload, "path", 1000) or pick_folder()
                if not chosen:
                    return self._json({"cancelled": True})
                folder = valid_rekordbox_folder(chosen)
                cfg["rekordbox_dir"] = str(folder)
            save_config(cfg)
            library.use_folder(folder)
            refresh_online()
            return self._json(library.status())

        if self.path == "/api/quit":
            self._json({"ok": True})
            threading.Thread(target=shutdown, daemon=True).start()
            return

        if self.path == "/api/coach":
            question = text_field(payload, "question", 2000)
            if not question:
                return self._json({"error": "Ask a question"}, 400)
            scope = payload.get("scope", "insights")
            if scope == "session":
                context = session_detail(text_field(payload, "session_id", 40)) or {}
            elif scope == "live":
                controller = payload.get("controller") if isinstance(payload.get("controller"), dict) else None
                live = library.live(direction=coach_local.live_direction(question), limit=5)
                np = live.get("now_playing") or {}
                context = {"rekordbox": live, "controller": controller,
                           "plan": plan_context(np.get("id"), (live.get("session") or {}).get("played_ids") or [])}
            else:
                scope = "insights"
                context = library.insights()
            return self._json(coach_answer(question, context, scope))

        if self.path == "/api/plan":
            locked = payload.get("locked")
            try:
                result = planner.plan_set(
                    library, text_field(payload, "start_id", 40), int(num(payload.get("length"), 12, 3, 40)),
                    text_field(payload, "shape", 20) or "warmup", id_list(payload, "include_ids"),
                    text_field(payload, "fresh", 20) or "some",
                    {str(k): str(v) for k, v in locked.items()} if isinstance(locked, dict) else {})
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            return self._json(result)
        if self.path == "/api/plan/alternatives":
            return self._json({"tracks": planner.alternatives(
                library, text_field(payload, "prev_id", 40), num(payload.get("target_bpm"), None, 20, 400),
                set(id_list(payload, "exclude")))})
        if self.path == "/api/online/enrich":
            if payload.get("action") == "stop":
                return self._json({**online_status(), "enrich": enricher.stop()})
            enricher.start(library, store, refresh_online)
            return self._json(online_status())
        if self.path == "/api/online/dig":
            seeds = [library.track(i) for i in id_list(payload, "track_ids") if library.track(i)]
            lo = num(payload.get("bpm_min"), None, 40, 250)
            hi = num(payload.get("bpm_max"), None, 40, 250)
            if not seeds or lo is None or hi is None or lo > hi:
                return self._json({"error": "Build or pick a set first, so Blendbook knows what to dig around."}, 400)
            if any(j.status()["status"] == "running" for j in dig_jobs.values()):
                # one at a time: each dig makes dozens of catalog requests (e.g. two open tabs digging at once)
                return self._json({"error": "Already digging. Wait for that one to finish."}, 409)
            job = online.DigJob(store, seeds, lo, hi, library_index(), country=store_country())
            dig_jobs[job.id] = job
            for old in list(dig_jobs)[:-5]:  # keep the last few
                dig_jobs.pop(old, None)
            return self._json(job.status())
        if self.path == "/api/online/search":
            q = text_field(payload, "q", 150)
            if len(q) < 2:
                return self._json({"tracks": []})
            try:
                found = online.search(store, q, library_index(), store_country())
            except online.OnlineError as e:
                return self._json({"error": str(e)}, 502)
            tracks = [with_links(t) for t in found["tracks"]]
            remember_online(tracks)
            return self._json({"tracks": tracks, "notice": found["notice"]})
        if self.path == "/api/online/preview":
            apple_id = num(payload.get("apple_id"), None, 1, 1e13)
            if apple_id is None:
                return self._json({"error": "Expected apple_id"}, 400)
            try:
                url = online.apple_preview(int(apple_id), store_country())
            except online.OnlineError as e:
                return self._json({"error": str(e)}, 502)
            return self._json({"preview_url": url} if url else {"error": "No preview for this track"}, 200 if url else 404)

        if self.path == "/api/plans":
            ids = [i for i in id_list(payload, "track_ids") if library.track(i)]
            name = text_field(payload, "name", 80) or f"Set plan {datetime.now():%d %b %H:%M}"
            if len(ids) < 2:
                return self._json({"error": "A plan needs at least 2 tracks"}, 400)
            return self._json({"id": store.add_set_plan(name, ids, text_field(payload, "shape", 20) or None)})
        m = re.fullmatch(r"/api/plans/(\d+)/(export|unexport|delete)", self.path)
        if m:
            plan = store.set_plan(int(m.group(1)))
            if not plan:
                return self._json({"error": "Plan not found"}, 404)
            action = m.group(2)
            if action == "delete":
                store.delete_set_plan(plan["id"])
                return self._json({"ok": True})
            if payload.get("confirm") is not True:
                return self._json({"error": "Confirmation required"}, 400)
            try:
                if action == "export":
                    ids = resolve_ids(plan["track_ids"])
                    res = planner.export_playlist(library.rb_dir, plan["name"], [i for i in ids if i in library.tracks])
                    res["not_in_library"] = [with_links(library._brief(i)) for i in ids
                                             if i not in library.tracks and library.track(i)]
                    store.mark_plan_exported(plan["id"], res["playlist_id"])
                    return self._json(res)
                if plan["exported_playlist_id"]:
                    planner.remove_playlist(library.rb_dir, plan["exported_playlist_id"])
                store.mark_plan_exported(plan["id"], None)
                return self._json({"ok": True})
            except RuntimeError as e:
                return self._json({"error": str(e)}, 409)
            except Exception as e:
                return self._json({"error": f"Export failed: {e}. A backup of your rekordbox library from just "
                                            f"before is in {planner.BACKUP_DIR}."}, 500)
        if self.path == "/api/goals":
            metric = payload.get("metric")
            if not isinstance(metric, str) or metric not in goals_mod.METRICS:
                return self._json({"error": "Unknown metric"}, 400)
            target = num(payload.get("target"), None, 0, 100_000)
            if target is None:
                return self._json({"error": "Target must be a number, 0 or more"}, 400)
            direction = goals_mod.METRICS[metric]["direction"]
            first = not store.goals()
            gid = store.add_goal(metric, target, direction, focus=bool(payload.get("focus")) or first)
            return self._json({"id": gid})
        m = re.fullmatch(r"/api/goals/(\d+)/(focus|archive)", self.path)
        if m:
            ok = store.focus_goal(int(m.group(1))) if m.group(2) == "focus" else store.archive_goal(int(m.group(1)))
            return self._json({"ok": ok}, 200 if ok else 404)

        if self.path == "/api/phone":
            try:
                return self._json(phone.enable() if payload.get("enable") is True else phone.disable())
            except RuntimeError as e:
                return self._json({"error": str(e)}, 500)
        if self.path == "/api/mixer-state":
            phone.set_mixer(payload.get("state"))
            return self._json({"ok": True})
        if self.path == "/api/recordings/scan":
            threading.Thread(target=recordings.scan, args=(store,), daemon=True).start()
            return self._json({"started": True})
        if self.path == "/api/listen":
            cs = payload.get("client_session")
            # start_ms between 2001 and 2100: anything else is a broken client clock or junk
            start_ms = num(payload.get("start_ms"), None, 1e12, 4.1e12)
            if not isinstance(cs, str) or not CLIENT_SESSION.fullmatch(cs) or start_ms is None:
                return self._json({"error": "Expected client_session and start_ms"}, 400)
            return self._json(recordings.add_mic_features(store, cs, int(start_ms), payload))
        if self.path == "/api/mixlog":
            events = payload.get("events")
            client_session = payload.get("client_session")
            if not isinstance(events, list) or not isinstance(client_session, str) or not CLIENT_SESSION.fullmatch(client_session):
                return self._json({"error": "Expected client_session and events"}, 400)
            if len(events) > MAX_EVENTS:
                return self._json({"error": f"At most {MAX_EVENTS} events per request"}, 413)
            added = store.add_mix_events(client_session, [e for e in events if isinstance(e, dict)])
            return self._json({"added": added})

        if self.path == "/api/settings":
            cfg = load_config()
            if "store_country" in payload:
                c = text_field(payload, "store_country", 2).lower()
                if c and not re.fullmatch(r"[a-z]{2}", c):
                    return self._json({"error": "Use a two-letter country code, like us or gb"}, 400)
                if c:
                    cfg["store_country"] = c
                else:
                    cfg.pop("store_country", None)
            if "check_updates" in payload:
                cfg["check_updates"] = payload["check_updates"] is True
            if "anthropic_api_key" in payload:
                key = payload["anthropic_api_key"]
                if key is not None and not isinstance(key, str):
                    return self._json({"error": "The key should be text"}, 400)
                key = (key or "").strip()
                if len(key) > 500 or any(c.isspace() for c in key):
                    return self._json({"error": "That doesn't look like an Anthropic API key"}, 400)
                if key:
                    cfg["anthropic_api_key"] = key
                else:
                    cfg.pop("anthropic_api_key", None)
            save_config(cfg)
            return self._json({"ok": True, "ai": "claude" if anthropic_client() else "local"})

        return self._json({"error": "Not found"}, 404)


_server: ThreadingHTTPServer | None = None


def shutdown() -> None:
    """Quit from the page (the packaged app has no window or terminal to close)."""
    time.sleep(0.3)  # let the reply reach the page first
    phone.disable()
    if _server:
        _server.shutdown()


def serve(port: int) -> None:
    global _server
    paths.ensure_data_dir()
    store.cache_prune(120 * 86400)
    refresh_online()
    apply_saved_folder()
    library.refresh()
    threading.Thread(target=every, args=(5, library.refresh), daemon=True).start()
    # on its own thread: analysing a long recording takes a while, and Live must keep following rekordbox meanwhile
    threading.Thread(target=every, args=(30, lambda: recordings.scan(store)), daemon=True).start()
    _server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Blendbook {VERSION} running at http://localhost:{port}  ({library.status()['sessions']} rekordbox sessions)",
          flush=True)
    try:
        _server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _server.server_close()


if __name__ == "__main__":
    serve(PORT)
