"""
Rekordbox data layer for Blendbook.

Reads the user's real rekordbox 6/7 library (master.db) through pyrekordbox and turns
the History playlists into session analytics, cross-session insights, and live
next-track suggestions.

master.db is never opened in place: we copy it to a cache dir whenever its mtime
changes, so rekordbox can keep writing while we read.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import shutil
import statistics
import threading
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import paths
import rb_analysis

logging.getLogger("pyrekordbox").setLevel(logging.ERROR)

RB_DIR = paths.RB_DIR
CACHE_DIR = paths.DATA_DIR

# The newest rekordbox Blendbook has been checked against. A newer one may still work; if reading fails, it's the suspect.
TESTED_REKORDBOX = (7, 2, 19)

# Key matching uses rekordbox's own keys only (rb_key). Online keys are shown as estimates (≈8A) but never make a blend
# harmonic or a clash: on 111 tracks with both, they matched rekordbox 28% of the time and clashed with it 47%.

# A track whose tempo differs by more than this from the previous one is a "BPM jump".
BPM_JUMP_PCT = 6.0
# Shorter than this on the deck counts as a quick cut.
QUICK_CUT_SEC = 75
# A gap between plays longer than this probably means a pause, not time on deck.
PAUSE_GAP_SEC = 15 * 60
# Next track logged later than this % of the previous track's length: it had probably ended.
GAP_PCT = 105
# The live session is "active" if rekordbox logged a track this recently.
LIVE_WINDOW_SEC = 20 * 60
# ...and only until the last logged track has run out plus this much slack: the next one is logged about a minute
# after it starts, and a loop or a long outro can hold the old one past its end.
ENDED_GRACE_SEC = 150


# Fewer tempo matches than this and the suggester widens out: open-format DJs change tempo with cuts and echoes.
MIN_CLOSE_MATCHES = 3
CLOSE_TEMPO_PCT = 8

# The same sound goes by many labels ("Hip-Hop/Rap", "Rap", "Trap"), so exact genre text isn't enough to compare tracks.
GENRE_FAMILIES = {
    "hip-hop": ("hip-hop", "hip hop", "hiphop", "rap", "trap", "drill", "grime", "boom bap"),
    "r&b": ("r&b", "rnb", "soul", "neo-soul", "funk"),
    "latin": ("latin", "reggaeton", "salsa", "bachata", "merengue", "cumbia", "dembow", "regional mexican"),
    "afro": ("afrobeat", "afrobeats", "afro", "afropop", "afro-fusion", "amapiano", "gqom", "highlife"),
    "caribbean": ("dancehall", "reggae", "soca", "dub"),
    "bass": ("drum & bass", "drum and bass", "drum n bass", "d&b", "dnb", "jungle", "dubstep", "bass", "garage", "breakbeat"),
    "club": ("house", "techno", "trance", "minimal", "tech", "dance", "edm", "electronic", "electronica", "club", "disco", "hardcore", "hardstyle"),
    "pop": ("pop", "k-pop", "j-pop", "bollywood"),
    "rock": ("rock", "metal", "punk", "indie", "alternative", "grunge"),
    "country": ("country", "folk", "americana", "bluegrass"),
    "jazz": ("jazz", "blues", "swing"),
    "classical": ("classical", "orchestral", "opera", "soundtrack"),
}
_FAMILY_RE = {f: re.compile(r"(?<![a-z])(?:" + "|".join(map(re.escape, words)) + r")(?![a-z])")
              for f, words in GENRE_FAMILIES.items()}


@functools.lru_cache(maxsize=2048)
def genre_families(genre: str | None) -> frozenset:
    g = (genre or "").lower()
    return frozenset(f for f, rx in _FAMILY_RE.items() if rx.search(g))


def same_sound(a: str | None, b: str | None) -> bool:
    """Whether two genre labels are the same sound: identical, or in the same family."""
    return bool(a and b and (a == b or genre_families(a) & genre_families(b)))


def rekordbox_version() -> tuple[int, int, int] | None:
    """The version rekordbox last ran as, from its settings file (LaunchedVersion 70219 is 7.2.19)."""
    for path in paths.RB_SETTINGS:
        try:
            m = re.search(r'name="LaunchedVersion" val="(\d{5,6})"', path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if m:
            n = int(m.group(1))
            return n // 10000, n // 100 % 100, n % 100
    return None


def _source(folder_path: str | None) -> str:
    if not folder_path:
        return "file"
    head = folder_path.split(":", 1)[0]
    return head if head in ("apple-music", "spotify", "tidal", "beatport", "soundcloud") else "file"


def _stream_id(folder_path: str | None) -> str | None:
    """The streaming service's own track id: 'spotify:track:<id>' / 'apple-music:tracks:<id>'."""
    parts = (folder_path or "").split(":")
    return parts[2] if len(parts) == 3 and parts[0] in ("spotify", "apple-music") and parts[2].isalnum() else None


@functools.lru_cache(maxsize=65536)
def _norm_title(title: str | None) -> str:
    """Title without mix/remaster tags, for spotting the same song twice: 'Endlied (Original Mix)' == 'Endlied'."""
    t = re.sub(r"\s*[\(\[][^)\]]*(original|extended|radio|remaster|edit|version)[^)\]]*[\)\]]", "", (title or "").lower())
    return re.sub(r"\s*-\s*(original mix|remastered.*|radio edit)$", "", t).strip()


def _camelot(scale_name: str | None) -> str | None:
    if not scale_name:
        return None
    s = scale_name.strip()
    names = {
        "Abm": "1A", "G#m": "1A", "B": "1B", "Ebm": "2A", "D#m": "2A", "F#": "2B", "Gb": "2B",
        "Bbm": "3A", "A#m": "3A", "Db": "3B", "C#": "3B", "Fm": "4A", "Ab": "4B", "G#": "4B",
        "Cm": "5A", "Eb": "5B", "D#": "5B", "Gm": "6A", "Bb": "6B", "A#": "6B", "Dm": "7A",
        "F": "7B", "Am": "8A", "C": "8B", "Em": "9A", "G": "9B", "Bm": "10A", "D": "10B",
        "F#m": "11A", "Gbm": "11A", "A": "11B", "C#m": "12A", "Dbm": "12A", "E": "12B",
    }
    return names.get(s, s)


def key_relation(k1: str | None, k2: str | None) -> str | None:
    """Camelot relation between two keys: same / relative / adjacent / boost / clash, or None if unknown."""
    if not k1 or not k2:
        return None
    try:
        n1, l1 = int(k1[:-1]), k1[-1]
        n2, l2 = int(k2[:-1]), k2[-1]
    except ValueError:
        return None
    diff = min((n1 - n2) % 12, (n2 - n1) % 12)
    if diff == 0:
        return "same" if l1 == l2 else "relative"
    if diff == 1 and l1 == l2:
        return "adjacent"
    if diff == 2 and l1 == l2:
        return "boost"
    return "clash"


class RekordboxLibrary:
    """In-memory snapshot of the rekordbox library, reloaded when master.db changes."""

    def __init__(self, rb_dir: Path = RB_DIR):
        self.rb_dir = rb_dir
        self._lock = threading.Lock()
        self._mtime = None
        self.loaded_at = None
        self.error = None
        self.tracks: dict[str, dict] = {}
        self.sessions: list[dict] = []  # oldest first
        self._cache: dict = {}  # derived data, cleared on every reload
        self.online_overlay: dict[str, dict] = {}  # track_id -> key / energy found online (online.py)
        self.online_tracks: dict[str, dict] = {}  # 'web:…' catalog tracks used in plans, not in rekordbox
        self._energy = rb_analysis.EnergyReader()

    # ---------- loading ----------

    @property
    def db_path(self) -> Path:
        return self.rb_dir / "master.db"

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            try:
                # rekordbox runs SQLite in WAL mode: new history rows land in master.db-wal and
                # master.db itself only changes on a checkpoint, so watch both files.
                mtime = self.db_path.stat().st_mtime
                wal = Path(str(self.db_path) + "-wal")
                try:
                    st = wal.stat()
                    mtime = (mtime, st.st_mtime, st.st_size)
                except FileNotFoundError:  # rekordbox removes it when it closes
                    pass
            except FileNotFoundError:
                self.error = f"rekordbox library not found at {self.db_path}"
                return
            if not force and mtime == self._mtime:
                return
            try:
                self._load(mtime)
                self.error = None
            except Exception as exc:  # keep serving the previous snapshot
                self.error = f"Could not read rekordbox library: {exc}"

    def _load(self, mtime: float | tuple) -> None:
        from pyrekordbox.masterdb import MasterDatabase

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        snap = CACHE_DIR / "master.db"
        shutil.copy2(self.db_path, snap)
        # Not -shm: rekordbox updates it through shared memory (its mtime never moves), so a copy can be older than
        # the WAL and hide the newest plays. Without it SQLite rebuilds the index from the WAL's committed frames.
        Path(str(snap) + "-shm").unlink(missing_ok=True)
        wal = Path(str(self.db_path) + "-wal")
        if wal.exists():
            shutil.copy2(wal, Path(str(snap) + "-wal"))
        else:
            Path(str(snap) + "-wal").unlink(missing_ok=True)

        db = MasterDatabase(str(snap))
        try:
            tracks = {}
            for c in db.get_content().all():
                key = None
                try:
                    key = _camelot(c.Key.ScaleName) if c.Key else None
                except Exception:
                    pass
                tracks[str(c.ID)] = {
                    "id": str(c.ID),
                    "title": c.Title or "Untitled",
                    "artist": c.ArtistName or "Unknown artist",
                    "bpm": round((c.BPM or 0) / 100, 1) or None,
                    "key": key,
                    "rb_key": key,  # rekordbox's own analysis; "key" may be filled in from online data
                    "key_source": "rekordbox" if key else None,
                    "analysis_path": c.AnalysisDataPath or None,
                    "energy": None,
                    "genre": c.GenreName or None,
                    "length": int(c.Length or 0) or None,
                    "source": _source(c.FolderPath),
                    "stream_id": _stream_id(c.FolderPath),
                    "rating": int(c.Rating or 0),
                    "year": c.ReleaseYear or None,
                    "added": c.StockDate or None,
                    # rekordbox's bundled sampler sounds and demo tracks aren't music the DJ chose
                    "is_sample": "/rekordbox/Sampler/" in (c.FolderPath or "") or "/PioneerDJ/Demo Tracks/" in (c.FolderPath or ""),
                }

            sessions = []
            for h in db.get_history().all():
                if h.Attribute != 0:  # 1 = year/month folder
                    continue
                songs = sorted(db.get_history_songs(HistoryID=h.ID).all(), key=lambda s: s.TrackNo or 0)
                plays = []
                for s in songs:
                    cid = str(s.ContentID)
                    if cid not in tracks or not s.created_at:
                        continue
                    plays.append({"track_id": cid, "played_at": s.created_at})
                if not plays:
                    continue
                sessions.append({
                    "id": str(h.ID),
                    "name": h.Name,
                    "started_at": plays[0]["played_at"],
                    "plays": plays,
                })
        finally:
            db.close()

        sessions.sort(key=lambda s: s["started_at"])
        for tid, energy in self._energy.energies(self.rb_dir, tracks).items():
            tracks[tid].update(energy=energy, energy_source="rekordbox")
        self._apply_overlay(tracks, self.online_overlay)
        self.tracks = tracks
        self.sessions = sessions
        self._mtime = mtime
        self.loaded_at = datetime.now()
        self._cache = {}

    # ---------- online data (online.py) ----------

    @staticmethod
    def _apply_overlay(tracks: dict, overlay: dict) -> None:
        for tid, o in overlay.items():
            t = tracks.get(tid)
            if not t or o.get("status") != "matched":
                continue
            if not t.get("rb_key") and o.get("key"):
                t["key"], t["key_source"] = o["key"], "online"
            if t.get("energy_source") != "rekordbox":  # rekordbox's analysis comes first, as with keys
                t["energy"], t["energy_source"] = o.get("energy"), "online"

    def set_online(self, overlay: dict[str, dict], online_tracks: dict[str, dict]) -> None:
        """Key / energy found online for library tracks, and the catalog tracks saved into plans."""
        self.online_overlay = overlay
        # every field a library track has, so the planner and suggester can treat both alike
        defaults = {"title": "Untitled", "artist": "Unknown artist", "bpm": None, "key": None, "genre": None,
                    "length": None, "energy": None, "spotify_url": None, "stream_id": None}
        self.online_tracks = {tid: {**defaults, **t, "id": tid, "online": True, "is_sample": False, "source": "online",
                                    "rating": 0, "year": None, "added": None, "rb_key": None,
                                    "key_source": "online" if t.get("key") else None}
                              for tid, t in online_tracks.items()}
        self._apply_overlay(self.tracks, overlay)

    def track(self, track_id: str) -> dict | None:
        """A library track, or a catalog track from a plan."""
        return self.tracks.get(track_id) or self.online_tracks.get(track_id)

    # ---------- per-session analysis ----------

    def _played_tracks(self, session: dict) -> list[dict]:
        plays = session["plays"]
        out = []
        start = plays[0]["played_at"]
        for i, p in enumerate(plays):
            t = self.tracks[p["track_id"]]
            on_deck = None
            if i + 1 < len(plays):
                gap = (plays[i + 1]["played_at"] - p["played_at"]).total_seconds()
                on_deck = gap if 0 < gap < PAUSE_GAP_SEC else None
            pct = round(100 * on_deck / t["length"]) if on_deck and t["length"] else None
            out.append({
                **t,
                "n": i + 1,
                "played_at": p["played_at"].isoformat(timespec="seconds"),
                "offset_sec": int((p["played_at"] - start).total_seconds()),
                "on_deck_sec": int(on_deck) if on_deck is not None else None,
                "pct_played": pct,
            })
        return out

    def _transitions(self, played: list[dict]) -> list[dict]:
        out = []
        seen = set()
        for a, b in zip(played, played[1:]):
            seen.add(a["id"])
            delta = pct = None
            if a["bpm"] and b["bpm"]:
                delta = round(b["bpm"] - a["bpm"], 1)
                pct = round(100 * delta / a["bpm"], 1)
            flags = []
            if pct is not None and abs(pct) > BPM_JUMP_PCT:
                flags.append("bpm_jump")
            if a["genre"] and b["genre"] and not same_sound(a["genre"], b["genre"]):
                flags.append("genre_switch")
            if a["on_deck_sec"] is not None and a["on_deck_sec"] < QUICK_CUT_SEC:
                flags.append("quick_cut")
            if a["pct_played"] is not None and a["pct_played"] > GAP_PCT:
                # the next track came in after this one had already ended
                flags.append("gap")
            elif a["pct_played"] is not None and a["pct_played"] >= 95:
                flags.append("played_out")
            rel = key_relation(a.get("rb_key"), b.get("rb_key"))
            if rel == "clash":
                flags.append("key_clash")
            if b["id"] in seen:
                flags.append("repeat")
            out.append({
                "n": a["n"],
                "at_sec": b["offset_sec"],
                "from": {k: a[k] for k in ("id", "title", "artist", "bpm", "key", "genre")},
                "to": {k: b[k] for k in ("id", "title", "artist", "bpm", "key", "genre")},
                "bpm_delta": delta,
                "bpm_delta_pct": pct,
                "key_relation": rel,
                "flags": flags,
            })
        return out

    def _session_stats(self, session: dict, played: list[dict], transitions: list[dict]) -> dict:
        bpms = [t["bpm"] for t in played if t["bpm"]]
        on_deck = [t["on_deck_sec"] for t in played if t["on_deck_sec"]]
        last = played[-1]
        est_tail = min(last["length"] or 240, 300)
        duration = (played[-1]["offset_sec"] + est_tail) if len(played) > 1 else (last["length"] or 0)
        deltas = [abs(t["bpm_delta_pct"]) for t in transitions if t["bpm_delta_pct"] is not None]
        smooth = [d for d in deltas if d <= 3]
        genres = Counter(t["genre"] or "Unknown" for t in played)
        sources = Counter(t["source"] for t in played)

        # first-time plays: tracks no earlier session had
        first = self._first_played()
        fresh = sum(1 for t in played if first.get(t["id"], session["started_at"]) >= session["started_at"])

        return {
            "track_count": len(played),
            "duration_sec": int(duration),
            "avg_on_deck_sec": int(statistics.mean(on_deck)) if on_deck else None,
            "median_on_deck_sec": int(statistics.median(on_deck)) if on_deck else None,
            "bpm_min": min(bpms) if bpms else None,
            "bpm_max": max(bpms) if bpms else None,
            "bpm_start": bpms[0] if bpms else None,
            "bpm_end": bpms[-1] if bpms else None,
            "bpm_median": statistics.median(bpms) if bpms else None,
            "smooth_tempo_pct": round(100 * len(smooth) / len(deltas)) if deltas else None,
            "bpm_jumps": sum(1 for t in transitions if "bpm_jump" in t["flags"]),
            "genre_switches": sum(1 for t in transitions if "genre_switch" in t["flags"]),
            "quick_cuts": sum(1 for t in transitions if "quick_cut" in t["flags"]),
            "played_out": sum(1 for t in transitions if "played_out" in t["flags"]),
            "gaps": sum(1 for t in transitions if "gap" in t["flags"]),
            "first_time_tracks": fresh,
            "genres": genres.most_common(),
            "sources": dict(sources),
            "keys_known": sum(1 for t in played if t.get("rb_key")),
        }

    def _observations(self, played: list[dict], transitions: list[dict], stats: dict) -> list[dict]:
        """Plain-language takeaways, each tied to concrete evidence in the session."""
        obs = []
        # Hopping sounds and tempos is the craft in an open-format set, and short plays are the point of a quick mix:
        # judged by a house set's standards, both would read as mistakes.
        sounds = {f for t in played for f in genre_families(t["genre"])}
        open_format = len(sounds) >= 3 and len(transitions) >= 4 and stats["genre_switches"] >= len(transitions) / 2
        jumps = [t for t in transitions if "bpm_jump" in t["flags"]]
        if jumps:
            worst = max(jumps, key=lambda t: abs(t["bpm_delta_pct"]))
            obs.append({
                "kind": "info" if open_format else "warn",
                "title": f"{len(jumps)} big tempo jump{'s' if len(jumps) > 1 else ''}",
                "detail": f"Biggest: {worst['from']['title']} ({worst['from']['bpm']:g}) into {worst['to']['title']} "
                          f"({worst['to']['bpm']:g}), {worst['bpm_delta_pct']:+.1f}%. "
                          + ("Normal for an open-format set: keep these on a clean cut, an echo out or a sample."
                             if open_format else "Jumps over ~6% are hard to blend; bridge with a track in between, "
                             "or use an echo-out / loop-and-cut instead of a long blend."),
                "at_n": worst["n"] + 1,  # the incoming track
            })
        if stats["quick_cuts"] >= 2:
            quick = [t for t in played if t["on_deck_sec"] is not None and t["on_deck_sec"] < QUICK_CUT_SEC]
            if len(quick) > len(played) / 2:  # most of the set: that's a style, not a problem
                obs.append({
                    "kind": "info",
                    "title": f"Quick-mix pace: {len(quick)} of {len(played)} tracks under {QUICK_CUT_SEC}s",
                    "detail": "At this pace the skill is landing each change on the hook. Listen back for the ones that came in late.",
                    "at_n": None,
                })
            else:
                obs.append({
                    "kind": "warn",
                    "title": f"{len(quick)} tracks got less than {QUICK_CUT_SEC}s",
                    "detail": "Very short plays usually mean a track didn't work or the next one was loaded in a hurry: "
                              + ", ".join(t["title"] for t in quick[:4]) + ". Pre-pick the next track while the current one builds.",
                    "at_n": quick[0]["n"],
                })
        if stats["gaps"]:
            gap_tracks = [t for t in played if t["pct_played"] is not None and t["pct_played"] > GAP_PCT]
            g = max(gap_tracks, key=lambda t: t["on_deck_sec"] - t["length"])
            obs.append({
                "kind": "warn",
                "title": f"{len(gap_tracks)} possible gap{'s' if len(gap_tracks) > 1 else ''} between tracks",
                "detail": f"The next track started after the previous one should have ended. Longest: {g['title']} is "
                          f"{g['length'] // 60}:{g['length'] % 60:02d} long, but the next track came {g['on_deck_sec'] // 60}:{g['on_deck_sec'] % 60:02d} later. "
                          "That's either silence or a deliberate pause. Load the next track before the current one hits its outro, "
                          "or hit a 4-beat loop to buy time.",
                "at_n": g["n"],
            })
        if stats["played_out"] >= 3:
            obs.append({
                "kind": "info",
                "title": f"{stats['played_out']} tracks played almost to the end",
                "detail": "Letting tracks run past ~95% often means you're mixing in on the outro's last bars. "
                          "Try starting the next track at the outro's first phrase so the blend has 16–32 bars to breathe.",
                "at_n": None,
            })
        if stats["smooth_tempo_pct"] is not None and stats["smooth_tempo_pct"] >= 75 and len(transitions) >= 4:
            clean = stats["bpm_jumps"] == 0
            obs.append({
                "kind": "good",
                "title": f"Tempo flow was tight ({stats['smooth_tempo_pct']}% of changes within 3%)",
                "detail": (f"You moved {stats['bpm_start']:g} → {stats['bpm_end']:g} BPM without lurching. That's what keeps a floor locked in."
                           if clean else "Apart from the jump above, tempo changes were small enough to blend comfortably."),
                "at_n": None,
            })
        if stats["bpm_start"] and stats["bpm_end"] and len(played) >= 6:
            drift = stats["bpm_end"] - stats["bpm_start"]
            if abs(drift) >= 4:
                obs.append({
                    "kind": "info",
                    "title": f"Set {'built up' if drift > 0 else 'wound down'} {abs(drift):g} BPM",
                    "detail": f"Started at {stats['bpm_start']:g}, finished at {stats['bpm_end']:g}.",
                    "at_n": None,
                })
        if open_format:
            obs.append({
                "kind": "good",
                "title": f"Open-format set across {len(sounds)} sounds",
                "detail": f"{stats['genre_switches']} genre moves. Listen back for the ones that kept the room with you: "
                          "those are the moves worth keeping.",
                "at_n": None,
            })
        elif stats["genre_switches"] >= max(3, len(transitions) // 2):
            obs.append({
                "kind": "info",
                "title": f"Genre changed {stats['genre_switches']} times",
                "detail": "Frequent genre hops can feel like channel-surfing. Grouping 3–4 tracks of one sound before moving on gives the set a story.",
                "at_n": None,
            })
        if stats["first_time_tracks"] and len(played) >= 5:
            share = round(100 * stats["first_time_tracks"] / len(played))
            obs.append({
                "kind": "good" if share >= 30 else "info",
                "title": f"{share}% new to your sets",
                "detail": f"{stats['first_time_tracks']} of {len(played)} tracks had never appeared in an earlier history.",
                "at_n": None,
            })
        if stats["sources"].get("apple-music", 0) + stats["sources"].get("spotify", 0) == len(played) \
                and not stats["keys_known"]:
            obs.append({
                "kind": "info",
                "title": "Streamed tracks only",
                "detail": "None of these tracks has a key from rekordbox yet, so harmonic checks are off for this set. "
                          "Run Analyze Track on them in rekordbox to turn them on.",
                "at_n": None,
            })
        return obs

    def session_summary(self, session: dict) -> dict:
        """Kept until the library reloads, since the session list and Insights both need every one. Returns a
        copy, so a caller can add to it."""
        summaries = self._cache.setdefault("summaries", {})
        if session["id"] not in summaries:
            played = self._played_tracks(session)
            transitions = self._transitions(played)
            stats = self._session_stats(session, played, transitions)
            summaries[session["id"]] = {
                "id": session["id"],
                "name": session["name"],
                "started_at": session["started_at"].isoformat(timespec="seconds"),
                "stats": stats,
                "opener": played[0]["title"],
                "closer": played[-1]["title"],
            }
        return dict(summaries[session["id"]])

    def session_detail(self, session_id: str) -> dict | None:
        session = next((s for s in self.sessions if s["id"] == session_id), None)
        if not session:
            return None
        played = self._played_tracks(session)
        transitions = self._transitions(played)
        stats = self._session_stats(session, played, transitions)
        return {
            "id": session["id"],
            "name": session["name"],
            "started_at": session["started_at"].isoformat(timespec="seconds"),
            "stats": stats,
            "tracks": played,
            "transitions": transitions,
            "observations": self._observations(played, transitions, stats),
        }

    def list_sessions(self) -> list[dict]:
        return [self.session_summary(s) for s in reversed(self.sessions)]

    # ---------- cross-session insights ----------

    def _transition_graph(self) -> dict[str, Counter]:
        if "graph" in self._cache:
            return self._cache["graph"]
        graph: dict[str, Counter] = defaultdict(Counter)
        for s in self.sessions:
            ids = [p["track_id"] for p in s["plays"]]
            for a, b in zip(ids, ids[1:]):
                if a != b:
                    graph[a][b] += 1
        self._cache["graph"] = graph
        return graph

    def _session_sets(self) -> tuple[list[set], Counter]:
        """Track-id set per session, and in how many sessions each track appears."""
        if "sets" not in self._cache:
            sets = [{p["track_id"] for p in s["plays"]} for s in self.sessions]
            counts = Counter()
            for ids in sets:
                counts.update(ids)
            self._cache["sets"] = (sets, counts)
        return self._cache["sets"]

    def _first_played(self) -> dict[str, datetime]:
        """When each track first appeared in a session. Built once per load: working it out per session made
        the session list slow down with the square of the number of sets."""
        if "first" not in self._cache:
            first = {}
            for s in self.sessions:  # oldest first, so the first one seen is the earliest
                for p in s["plays"]:
                    first.setdefault(p["track_id"], s["started_at"])
            self._cache["first"] = first
        return self._cache["first"]

    def insights(self) -> dict:
        play_counts = Counter()
        session_counts = Counter()
        artist_counts = Counter()
        genre_counts = Counter()
        openers = Counter()
        last_played: dict[str, str] = {}
        timeline = []
        for s in self.sessions:
            ids = [p["track_id"] for p in s["plays"]]
            play_counts.update(ids)
            session_counts.update(set(ids))
            openers[ids[0]] += 1
            for i in ids:
                t = self.tracks[i]
                artist_counts[t["artist"]] += 1
                genre_counts[t["genre"] or "Unknown"] += 1
                last_played[i] = s["started_at"].date().isoformat()
            summ = self.session_summary(s)
            timeline.append({
                "id": s["id"],
                "date": s["started_at"].date().isoformat(),
                "tracks": summ["stats"]["track_count"],
                "duration_sec": summ["stats"]["duration_sec"],
                "bpm_median": summ["stats"]["bpm_median"],
                "bpm_min": summ["stats"]["bpm_min"],
                "bpm_max": summ["stats"]["bpm_max"],
                "smooth_tempo_pct": summ["stats"]["smooth_tempo_pct"],
                "avg_on_deck_sec": summ["stats"]["avg_on_deck_sec"],
                "bpm_jumps": summ["stats"]["bpm_jumps"],
            })

        graph = self._transition_graph()
        pairs = []
        for a, nexts in graph.items():
            for b, n in nexts.items():
                if n >= 2:
                    pairs.append({"from": self._brief(a), "to": self._brief(b), "count": n})
        pairs.sort(key=lambda p: -p["count"])

        def top_tracks(counter, n=12):
            return [{**self._brief(i), "plays": c, "sessions": session_counts[i], "last_played": last_played.get(i)}
                    for i, c in counter.most_common(n)]

        played_ids = set(play_counts)
        music = [t for t in self.tracks.values() if not t["is_sample"]]
        unplayed = [t for t in music if t["id"] not in played_ids]
        unplayed.sort(key=lambda t: t["added"] or "", reverse=False)

        # tracks you used to play a lot but haven't touched in the last 5 sessions
        recent = set()
        for s in self.sessions[-5:]:
            recent.update(p["track_id"] for p in s["plays"])
        dormant = [i for i, c in session_counts.most_common() if c >= 3 and i not in recent][:10]
        # tracks in most of your last 5 sets: the ones you may be leaning on
        recent_sets = self.sessions[-5:]
        in_recent = Counter(i for s in recent_sets for i in {p["track_id"] for p in s["plays"]})
        leaning_on = [(i, c) for i, c in in_recent.most_common() if c >= 3 and not self.tracks[i]["is_sample"]][:10]

        durations = [t["duration_sec"] for t in timeline if t["tracks"] >= 3]
        smooth = [t["smooth_tempo_pct"] for t in timeline if t["smooth_tempo_pct"] is not None]
        return {
            "totals": {
                "sessions": len(self.sessions),
                "plays": sum(play_counts.values()),
                "unique_tracks_played": len(played_ids),
                "library_size": len(music),
                "library_coverage_pct": round(100 * len(played_ids & {t["id"] for t in music}) / max(1, len(music))),
                "first_session": self.sessions[0]["started_at"].date().isoformat() if self.sessions else None,
                "last_session": self.sessions[-1]["started_at"].date().isoformat() if self.sessions else None,
                "median_set_minutes": round(statistics.median(durations) / 60) if durations else None,
                "median_smooth_tempo_pct": round(statistics.median(smooth)) if smooth else None,
            },
            "timeline": timeline,
            "top_tracks": top_tracks(play_counts),
            "top_artists": [{"artist": a, "plays": c} for a, c in artist_counts.most_common(13) if a != "Unknown artist"][:12],
            "top_genres": [{"genre": g, "plays": c} for g, c in genre_counts.most_common(10)],
            "go_to_openers": [{**self._brief(i), "times": c} for i, c in openers.most_common(5) if c >= 2],
            "signature_transitions": pairs[:15],
            "leaning_on": [{**self._brief(i), "sets": c, "of": len(recent_sets)} for i, c in leaning_on],
            "dormant_favourites": [{**self._brief(i), "sessions": session_counts[i], "last_played": last_played.get(i)} for i in dormant],
            "never_played": [self._brief(t["id"]) | {"added": t["added"]} for t in unplayed[:40]],
            "never_played_count": len(unplayed),
        }

    def _brief(self, track_id: str) -> dict:
        t = self.track(track_id)
        brief = {k: t.get(k) for k in ("id", "title", "artist", "bpm", "key", "key_source", "genre", "length", "energy",
                                         "energy_source")}
        if t.get("online"):  # not in rekordbox yet: say where to find it
            brief.update(online=True, spotify_url=t.get("spotify_url"), apple_url=t.get("apple_url"))
        return brief

    # ---------- live ----------

    def live(self, direction: str = "hold", limit: int = 10) -> dict:
        """Current session state from rekordbox's history, plus ranked next-track suggestions."""
        if not self.sessions:
            return {"active": False, "reason": "No rekordbox history yet."}
        session = self.sessions[-1]
        last_play = session["plays"][-1]
        now = datetime.now()
        since = (now - last_play["played_at"]).total_seconds()
        current = self.tracks[last_play["track_id"]]
        window = LIVE_WINDOW_SEC
        if current.get("length"):
            window = min(window, current["length"] + ENDED_GRACE_SEC)
        active = 0 <= since < window
        played_ids = [p["track_id"] for p in session["plays"]]
        played = self._played_tracks(session)
        transitions = self._transitions(played)
        on_deck = [t["on_deck_sec"] for t in played if t["on_deck_sec"]]

        return {
            "active": active,
            "session": {
                "id": session["id"],
                "name": session["name"],
                "started_at": session["started_at"].isoformat(timespec="seconds"),
                "track_count": len(played_ids),
                "elapsed_sec": int((now - session["started_at"]).total_seconds()),
                "played_ids": played_ids,
                # how long a track usually lasts: this set's pace once it has one, else your usual pace
                "track_pace_sec": int(statistics.median(on_deck)) if len(on_deck) >= 3 else self.typical_on_deck_sec(),
            },
            "now_playing": {
                **self._brief(current["id"]),
                "logged_at": last_play["played_at"].isoformat(timespec="seconds"),
                "since_logged_sec": int(since),
            },
            "recent": [self._brief(i) for i in reversed(played_ids[-6:-1])],
            "bpm_path": [t["bpm"] for t in played],
            "last_transition": transitions[-1] if transitions else None,
            "suggestions": self.suggest(current["id"], exclude=set(played_ids), direction=direction,
                                        recent_genres=[self.tracks[i]["genre"] for i in played_ids[-4:]], limit=limit),
        }

    def typical_on_deck_sec(self) -> int | None:
        """Median time per track across every set (cached until the library reloads)."""
        if "pace" not in self._cache:
            vals = [t["on_deck_sec"] for s in self.sessions for t in self._played_tracks(s) if t["on_deck_sec"]]
            self._cache["pace"] = int(statistics.median(vals)) if vals else None
        return self._cache["pace"]

    def suggest(self, current_id: str, exclude: set | None = None, direction: str = "hold",
                recent_genres: list | None = None, limit: int = 10, target_bpm: float | None = None) -> list[dict]:
        """Rank library tracks as the next track after current_id.

        Uses only real signals: tempo distance (with an energy direction), key compatibility when
        both keys are known, genre continuity, and the user's own history of what they've played
        after this track before.
        """
        exclude = exclude or set()
        cur = self.track(current_id)
        if not cur:
            return []
        graph = self._transition_graph()
        after_cur = graph.get(current_id, Counter())
        # what follows tracks that follow cur — second-degree "you like this flow"
        second = Counter()
        for b, n in after_cur.items():
            for c, m in graph.get(b, {}).items():
                second[c] += n * m
        sets, session_counts = self._session_sets()
        together = Counter()  # sets where a track appeared alongside the current one
        for ids in sets:
            if current_id in ids:
                together.update(ids)
        recent_genres = [g for g in (recent_genres or []) if g]

        target = target_bpm or cur["bpm"] or 0
        if target_bpm:
            pass  # the planner sets the tempo itself
        elif direction == "up":
            target *= 1.02
        elif direction == "down":
            target *= 0.98

        # a library has many tracks but few genres and keys: work each one out once per call
        genre_fit: dict[str | None, tuple[int, str | None]] = {}
        key_fit: dict[str | None, str | None] = {}

        def fit_genre(genre: str | None) -> tuple[int, str | None]:
            if genre not in genre_fit:
                if cur["genre"] and genre == cur["genre"]:
                    genre_fit[genre] = (12, f"same genre ({genre})")
                elif same_sound(cur["genre"], genre):
                    genre_fit[genre] = (9, f"same sound ({genre})")
                elif any(same_sound(genre, g) for g in recent_genres):
                    genre_fit[genre] = (6, None)
                else:
                    genre_fit[genre] = (0, None)
            return genre_fit[genre]

        def judge(t: dict, pct: float, feel: float, explain: bool) -> tuple[float, str | None, list[str]]:
            """Score t as the next track. The reasons are worded only when asked for: wording them for every track
            made set plans slow in big libraries."""
            tid = t["id"]
            score = 40 * max(0.0, 1 - abs(pct) / CLOSE_TEMPO_PCT)
            if target and direction == "up" and t["bpm"] * feel > cur["bpm"]:
                score += 6
            if target and direction == "down" and t["bpm"] * feel < cur["bpm"]:
                score += 6
            if t.get("rb_key") not in key_fit:
                key_fit[t.get("rb_key")] = key_relation(cur.get("rb_key"), t.get("rb_key"))
            rel = key_fit[t.get("rb_key")]
            if rel in ("same", "relative", "adjacent"):
                score += 15
            elif rel == "boost":
                score += 6
            elif rel == "clash":
                score -= 12
            genre_bonus, genre_why = fit_genre(t["genre"])
            score += genre_bonus
            n_after, n_second, n_together = after_cur.get(tid, 0), second.get(tid, 0), together.get(tid, 0)
            if n_after:
                score += 25 + 5 * min(n_after, 3)
            elif n_second:
                score += min(12, 4 * n_second)
            elif n_together:
                score += min(9, 3 * n_together)
            # exact tempo matches edge out near ones when everything else ties
            score -= abs(pct) * 0.5
            sc = session_counts.get(tid, 0)
            if sc == 0:
                score += 4
            elif sc >= 5:
                score -= 4  # nudge away from crutch tracks
            if not explain:
                return score, rel, []

            # a 75 BPM track after a 150 one is a match, but say so: it blends on every other beat
            bpm_txt = f"{t['bpm']:g} BPM" if feel == 1 else \
                f"{t['bpm']:g} BPM ({'double' if feel == 0.5 else 'half'}-time, feels like {round(t['bpm'] * feel, 1):g})"
            if not target:
                reasons = [f"{bpm_txt} (this track has no BPM, so go by ear)"]
            elif abs(pct) <= 2:
                reasons = [f"{bpm_txt}, easy blend"]
            elif abs(pct) <= 5:
                reasons = [f"{bpm_txt} ({pct:+.0f}%)"]
            elif abs(pct) <= CLOSE_TEMPO_PCT:
                reasons = [f"{bpm_txt}, needs a pitch ride ({pct:+.0f}%)"]
            else:
                reasons = [f"{bpm_txt}, big tempo change ({pct:+.0f}%): cut or echo out"]
            if rel in ("same", "relative", "adjacent"):
                reasons.append(f"harmonic ({cur['key']}→{t['key']})")
            elif rel == "boost":
                reasons.append(f"energy key lift ({cur['key']}→{t['key']})")
            if genre_why:
                reasons.append(genre_why)
            if n_after:
                reasons.insert(0, f"you've played this after it {n_after}×")
            elif n_second:
                reasons.append("fits a flow you've used before")
            elif n_together:
                reasons.append(f"in {n_together} of your sets with this track")
            if sc == 0:
                reasons.append("never played out yet")
            if rel == "clash":  # a warning, so it must survive the 3-reason cut
                reasons.insert(min(2, len(reasons)), f"key clash ({cur['key']}→{t['key']})")
            return score, rel, reasons[:3]

        def rank(max_pct: float | None) -> list[tuple]:
            scored = []
            for t in self.tracks.values():
                if t["id"] in exclude or t["id"] == current_id or t["is_sample"] or not t["bpm"]:
                    continue
                pct, feel = 0.0, 1.0
                if target:  # no tempo to go on (the playing track has no BPM): rank on everything else
                    pct = 100 * (t["bpm"] - target) / target
                    # also allow half/double time matches
                    for mult in (0.5, 2.0):
                        alt = 100 * (t["bpm"] * mult - target) / target
                        if abs(alt) < abs(pct):
                            pct, feel = alt, mult
                if max_pct is not None and abs(pct) > max_pct:
                    continue
                score, _, _ = judge(t, pct, feel, explain=False)
                scored.append((round(score, 1), t, pct, feel))
            return scored

        scored = rank(CLOSE_TEMPO_PCT)
        if len(scored) < MIN_CLOSE_MATCHES:
            scored = rank(None)
        scored.sort(key=lambda x: -x[0])
        # the same song is often in the library more than once (an Apple Music and a Spotify copy): keep the best one
        own = (_norm_title(cur["title"]), (cur["artist"] or "").lower())
        seen, unique = set(), []
        for score, t, pct, feel in scored:
            if len(unique) >= limit:
                break
            k = (_norm_title(t["title"]), (t["artist"] or "").lower())
            if k not in seen and k != own:
                seen.add(k)
                _, rel, reasons = judge(t, pct, feel, explain=True)
                unique.append({**self._brief(t["id"]), "score": score, "bpm_delta_pct": round(pct, 1),
                               "key_relation": rel, "times_played": session_counts.get(t["id"], 0), "reasons": reasons})
        return unique

    def use_folder(self, folder: Path) -> None:
        """Read the library from this folder from now on. The old one stays up until the new one has loaded: pages
        may be reading it right now."""
        self.rb_dir = folder
        self.refresh(force=True)

    def status(self) -> dict:
        version = rekordbox_version()
        return {
            "rekordbox_dir": str(self.rb_dir),
            "found": self.db_path.exists(),
            "loaded_at": self.loaded_at.isoformat(timespec="seconds") if self.loaded_at else None,
            "tracks": len(self.tracks),
            "sessions": len(self.sessions),
            "error": self.error,
            "rekordbox_version": ".".join(map(str, version)) if version else None,
            "tested_version": ".".join(map(str, TESTED_REKORDBOX)),
            "newer_rekordbox": bool(version and version > TESTED_REKORDBOX),
        }

    def search(self, q: str, limit: int = 20) -> list[dict]:
        q = q.lower().strip()
        if not q:
            return []
        hits = [t for t in self.tracks.values()
                if not t["is_sample"] and (q in t["title"].lower() or q in t["artist"].lower())]
        return [self._brief(t["id"]) for t in hits[:limit]]
