"""
Online music catalogs for set planning: key and energy for your streamed tracks, and new tracks like yours.

All of these are free and need no key or account:
- Apple's Search API (itunes.apple.com): songs, genres, store links and 30 s previews. About 20 calls a minute, so
  digs are paced and budgeted. Previews are streamed by the browser, never saved, and always sit next to a store badge.
- MusicBrainz (musicbrainz.org): turns an artist name into an id. About 1 request a second.
- ListenBrainz (labs.api.listenbrainz.org): artists people listen to in the same sessions, by MusicBrainz id. At most
  1 call a second. It leans on popularity and knows little about small artists, so results are filtered by genre and
  Apple's search fills in when it has nothing.
- ReccoBeats (api.reccobeats.com): Spotify-style audio features (tempo, key, energy) and a Spotify link, found by
  title search + artist match. Its own recommendations ignore genre, so Blendbook doesn't use them.

Only track titles and artist names leave this computer, and only when you ask (Fill in key & energy, Dig online,
Search, Preview). Responses are cached in Blendbook's database, so repeat lookups are instant and work offline.

Everything that comes back is untrusted: links are only kept if they point at the expected https hosts, and titles
are only ever shown as text.
"""

from __future__ import annotations

import json
import re
import ssl
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

import rb

RECCO = "https://api.reccobeats.com/v1"
APPLE = "https://itunes.apple.com"
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
LISTENBRAINZ = "https://labs.api.listenbrainz.org"
LB_POPULARITY = "https://api.listenbrainz.org/1/popularity/artist"
LB_ALGORITHM = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
USER_AGENT = "Blendbook/1.0 ( https://github.com/ujjaidasani/blendbook )"
TIMEOUT_SEC = 20
DAY = 86400
CACHE_TTL = {"features": 90 * DAY, "track": 90 * DAY, "search": 14 * DAY, "artist": 30 * DAY, "related": 14 * DAY,
             "songs": 7 * DAY}
SERVICES = {"api.reccobeats.com": "ReccoBeats", "itunes.apple.com": "Apple's catalog", "musicbrainz.org": "MusicBrainz",
            "labs.api.listenbrainz.org": "ListenBrainz", "api.listenbrainz.org": "ListenBrainz"}
# seconds between request starts: Apple allows about 20 calls a minute, MusicBrainz and ListenBrainz 1 a second
MIN_INTERVAL = {"api.reccobeats.com": 0.2, "itunes.apple.com": 3.2, "musicbrainz.org": 1.1,
                "labs.api.listenbrainz.org": 1.1, "api.listenbrainz.org": 1.1}
PARALLEL = {"api.reccobeats.com": 3, "itunes.apple.com": 1, "musicbrainz.org": 1,
            "labs.api.listenbrainz.org": 1, "api.listenbrainz.org": 1}  # requests in flight at once
BUSY_WAIT = {"itunes.apple.com": 30.0}  # seconds to wait when a catalog says slow down and doesn't say for how long
RECCO_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SPOTIFY_ID = re.compile(r"[A-Za-z0-9]{22}")
SPOTIFY_URL = re.compile(r"https://open\.spotify\.com/track/[A-Za-z0-9]{22}")
APPLE_PREVIEW = re.compile(r"https://audio-ssl\.itunes\.apple\.com/[^\s\"'<>]+")
APPLE_LINK = re.compile(r"https://(music|itunes)\.apple\.com/[^\s\"'<>]+")
MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TEMPO_MATCH_PCT = 4.0  # an online match must agree with rekordbox's own BPM (or half / double) this closely

# Spotify-style pitch class (0 = C) to Camelot, for minor (mode 0) and major (mode 1)
CAMELOT_MINOR = ["5A", "12A", "7A", "2A", "9A", "4A", "11A", "6A", "1A", "8A", "3A", "10A"]
CAMELOT_MAJOR = ["8B", "3B", "10B", "5B", "12B", "7B", "2B", "9B", "4B", "11B", "6B", "1B"]


class OnlineError(RuntimeError):
    """A catalog couldn't be reached or refused the request. The message is safe to show."""


# ---------- HTTP ----------

def _ssl_context() -> ssl.SSLContext:
    try:  # use the Mac's own trust store; python.org builds ship without CA certificates
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


_ctx = _ssl_context()
_slots = {h: threading.BoundedSemaphore(n) for h, n in PARALLEL.items()}
_spacing = {h: threading.Lock() for h in MIN_INTERVAL}
_last_start: dict[str, float] = {}


def _wait_turn(host: str) -> None:
    with _spacing[host]:
        wait = _last_start.get(host, 0) + MIN_INTERVAL[host] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_start[host] = time.monotonic()


def _busy(host: str, code: int) -> bool:
    """How each service says slow down: 429, 503 from MusicBrainz, and 403 from Apple."""
    return code in (429, 503) or (code == 403 and host == "itunes.apple.com")


def _http_json(url: str, body=None):
    host = urllib.parse.urlsplit(url).hostname
    if host not in MIN_INTERVAL:  # never a URL built from someone else's input
        raise OnlineError(f"Not an allowed catalog host: {host}")
    name = SERVICES[host]
    with _slots[host]:  # a few requests at a time per catalog, politely spaced
        for attempt in (1, 2, 3):
            _wait_turn(host)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            if body is not None:
                req.data = json.dumps(body).encode()
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, context=_ctx, timeout=TIMEOUT_SEC) as res:
                    body = res.read(5_000_000)
                return json.loads(body)
            except urllib.error.HTTPError as e:
                if _busy(host, e.code) and attempt < 3:
                    try:
                        retry = float(e.headers.get("Retry-After") or 0)
                    except ValueError:
                        retry = 0.0
                    time.sleep(min(30.0, max(BUSY_WAIT.get(host, 5.0), retry)))
                    continue
                if _busy(host, e.code):
                    raise OnlineError(f"{name} is busy right now. Try again in a minute.")
                if e.code in (400, 404):  # e.g. a search term the catalog won't take: no results
                    return None
                raise OnlineError(f"{name} returned an error ({e.code}).")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise OnlineError(f"Couldn't reach {name}. Are you online? ({getattr(e, 'reason', e)})")
            except json.JSONDecodeError:
                raise OnlineError(f"{name} sent something Blendbook couldn't read.")
        raise OnlineError(f"{name} is busy right now. Try again in a minute.")


def _cached(store, kind: str, url: str):
    hit = store.cache_get(url, CACHE_TTL[kind])
    if hit is not None:
        return hit
    data = _http_json(url)
    if data is not None:
        store.cache_put(url, data)
    return data


# ---------- matching ----------

_DROP = re.compile(r"\b(original mix|extended mix|extended version|radio edit|remastered( \d{4})?|\d{4} remaster|"
                   r"original version|album version|mixed)\b")
_FEAT = re.compile(r"[\(\[]?\s*\b(feat|ft|featuring|with)\b\.?[^\)\]]*[\)\]]?")


def norm(s: str | None) -> str:
    """Lowercase, no accents, no punctuation, no 'feat. …' and no version words that don't change the music."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = _FEAT.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = _DROP.sub(" ", s)
    return " ".join(s.split())


def artist_names(s: str | list | None) -> set[str]:
    parts = s if isinstance(s, list) else re.split(r",|&|\band\b|\bx\b|\bvs\.?\b|\bfeat\.?\b|\bft\.?\b|/|;", s or "", flags=re.I)
    return {n for n in (norm(p) for p in parts) if n}


def title_score(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    return len(ta & tb) / len(ta | tb)


def same_tempo(a: float | None, b: float | None, pct: float = TEMPO_MATCH_PCT) -> bool:
    """True if two BPMs agree, allowing for one analysis landing on half or double time."""
    if not a or not b:
        return False
    return any(abs(a * m - b) / b * 100 <= pct for m in (0.5, 1.0, 2.0))


def camelot(pitch_class, mode) -> str | None:
    if not isinstance(pitch_class, int) or not 0 <= pitch_class <= 11 or mode not in (0, 1):
        return None
    return (CAMELOT_MAJOR if mode == 1 else CAMELOT_MINOR)[pitch_class]


def _clean_track(t: dict) -> dict | None:
    """A ReccoBeats track reduced to what Blendbook uses, with links checked."""
    rid = t.get("id")
    if not isinstance(rid, str) or not RECCO_ID.fullmatch(rid):
        return None
    href = t.get("href") if isinstance(t.get("href"), str) and SPOTIFY_URL.fullmatch(t.get("href")) else None
    artists = [a.get("name") for a in t.get("artists") or [] if isinstance(a, dict) and isinstance(a.get("name"), str)]
    dur = t.get("durationMs")
    return {"recco_id": rid, "title": str(t.get("trackTitle") or "Untitled")[:200], "artists": artists[:6],
            "artist": ", ".join(artists[:4]) or "Unknown artist",
            "length": int(dur / 1000) if isinstance(dur, (int, float)) and dur > 0 else None,
            "spotify_url": href, "isrc": t.get("isrc") if isinstance(t.get("isrc"), str) else None}


# ---------- ReccoBeats ----------

def search_tracks(store, title: str, size: int = 25) -> list[dict]:
    """Catalog tracks whose title matches (ReccoBeats searches titles only, not artists)."""
    q = urllib.parse.urlencode({"searchText": title[:120], "size": max(1, min(50, size))})
    data = _cached(store, "search", f"{RECCO}/track/search?{q}") or {}
    return [c for c in (_clean_track(t) for t in data.get("content") or [] if isinstance(t, dict)) if c]


def artist_catalog(store, name: str, max_artists: int = 3, max_pages: int = 4):
    """Tracks by artists with exactly this name, page by page. The catalog often has several same-named artist
    entries (one per Spotify artist id), so up to max_artists of them are tried."""
    want = norm(name)
    if not want:
        return
    q = urllib.parse.urlencode({"searchText": name[:80], "size": 10})
    data = _cached(store, "search", f"{RECCO}/artist/search?{q}") or {}
    ids = [a["id"] for a in data.get("content") or []
           if isinstance(a, dict) and isinstance(a.get("id"), str) and RECCO_ID.fullmatch(a["id"])
           and norm(a.get("name")) == want][:max_artists]
    for aid in ids:
        for page in range(max_pages):
            q = urllib.parse.urlencode({"size": 50, "page": page})
            data = _cached(store, "track", f"{RECCO}/artist/{aid}/track?{q}") or {}
            for t in data.get("content") or []:
                c = _clean_track(t) if isinstance(t, dict) else None
                if c:
                    yield c
            if page + 1 >= (data.get("totalPages") or 0):
                break


def features(store, ids: list[str]) -> dict[str, dict]:
    """Tempo / key / energy by ReccoBeats id (or Spotify id), 40 per request."""
    out = {}
    ids = [i for i in dict.fromkeys(ids) if RECCO_ID.fullmatch(i) or SPOTIFY_ID.fullmatch(i)]
    for start in range(0, len(ids), 40):
        batch = ids[start:start + 40]
        data = _cached(store, "features", f"{RECCO}/audio-features?ids={','.join(batch)}") or {}
        for f in data.get("content") or []:
            if not isinstance(f, dict) or not isinstance(f.get("id"), str):
                continue
            tempo = f.get("tempo")
            feat = {"bpm": round(float(tempo), 1) if isinstance(tempo, (int, float)) and 40 <= tempo <= 250 else None,
                    "key": camelot(f.get("key"), f.get("mode")),
                    **{k: round(float(f[k]), 3) for k in ("energy", "danceability", "valence")
                       if isinstance(f.get(k), (int, float)) and 0 <= f[k] <= 1}}
            out[f["id"]] = feat
            href = f.get("href")
            if isinstance(href, str) and SPOTIFY_URL.fullmatch(href):
                out[href.rsplit("/", 1)[1]] = feat  # also reachable by the Spotify id that was asked for
    return out


def match_library_track(store, t: dict) -> dict | None:
    """Find a rekordbox track in the catalog. Returns {recco_id or spotify_id, ...features} or None.

    A candidate must share an artist and (nearly) the title, and its tempo must agree with rekordbox's analysis:
    that last check is what keeps a same-named track by someone else from lending you the wrong key."""
    if t.get("source") == "spotify" and t.get("stream_id") and SPOTIFY_ID.fullmatch(t["stream_id"]):
        f = features(store, [t["stream_id"]]).get(t["stream_id"])
        if f and (not t.get("bpm") or same_tempo(f.get("bpm"), t["bpm"])):
            return {"recco_id": None, "spotify_url": f"https://open.spotify.com/track/{t['stream_id']}", **f}
        return None
    want_artists = artist_names(t.get("artist"))
    base = re.split(r"\s[\(\[]|\s-\s", t.get("title") or "")[0].strip()
    if not base or not want_artists:
        return None
    seen, cands = set(), []

    def consider(candidates):
        for c in candidates:
            if c["recco_id"] in seen:
                continue
            seen.add(c["recco_id"])
            score = title_score(t["title"], c["title"])
            if score < 0.75 or not (want_artists & artist_names(c["artists"])):
                continue
            if t.get("length") and c["length"]:
                score += 0.2 if abs(t["length"] - c["length"]) <= 3 else 0.0
            cands.append((score, c))

    def verified():
        cands.sort(key=lambda x: -x[0])
        feats = features(store, [c["recco_id"] for _, c in cands[:8]])
        for _, c in cands[:8]:
            f = feats.get(c["recco_id"])
            if f and (not t.get("bpm") or same_tempo(f.get("bpm"), t["bpm"])):
                return {"recco_id": c["recco_id"], "spotify_url": c["spotify_url"], **f}
        return None

    for query in dict.fromkeys([base, t["title"]]):
        consider(search_tracks(store, query, 50))
        if any(s >= 1.0 for s, _ in cands):
            break
    hit = verified() if cands else None
    if hit:
        return hit
    # short titles ("Nova", "Horizon") drown in same-named tracks: look through the artist's own catalog instead
    first_artists = [p for p in re.split(r",|&|\bx\b|\bfeat\.?|\bft\.?", t["artist"], flags=re.I) if norm(p)][:2]
    for name in first_artists:
        consider(artist_catalog(store, name.strip()))
        if cands:
            hit = verified()
            if hit:
                return hit
    return None


# ---------- catalog tracks: tempo & key for something not in your library ----------

def catalog_features(store, title: str, artist: str, length: int | None = None) -> dict | None:
    """Tempo / key / energy (+ Spotify link) for a catalog track, by title search + artist match. With no rekordbox
    BPM to check against, the title must match exactly (version words aside); the closest length wins."""
    want = artist_names(artist)
    base = re.split(r"\s[\(\[]|\s-\s", title or "")[0].strip()
    if not base or not want:
        return None
    best = None
    for c in search_tracks(store, base, 50):
        if title_score(title, c["title"]) < 1.0 or not (want & artist_names(c["artists"])):
            continue
        gap = abs(length - c["length"]) if length and c["length"] else 999
        if best is None or gap < best[0]:
            best = (gap, c)
    if not best:
        return None
    c = best[1]
    f = features(store, [c["recco_id"]]).get(c["recco_id"])
    return {"spotify_url": c["spotify_url"], **f} if f else None


def _country(country: str | None) -> str:
    return country.lower() if re.fullmatch(r"[A-Za-z]{2}", country or "") else "us"


def apple_search_url(title: str, artist: str, country: str = "us") -> str:
    """Apple Music search for this track: rekordbox streams your Apple Music, so that's where you'd add it."""
    return f"https://music.apple.com/{_country(country)}/search?" + urllib.parse.urlencode({"term": f"{artist} {title}"[:150]})


def spotify_search_url(title: str, artist: str) -> str:
    """Spotify search for this track, for when ReccoBeats has no direct link."""
    return "https://open.spotify.com/search/" + urllib.parse.quote(f"{artist} {title}"[:150], safe="")


def beatport_search_url(title: str, artist: str) -> str:
    """Beatport search for this track. Just a link: Beatport's API is closed to apps like this one."""
    return "https://www.beatport.com/search?" + urllib.parse.urlencode({"q": f"{artist} {title}"[:150]})


def library_key(title: str, artist: str | list) -> str:
    return f"{norm(title)}|{','.join(sorted(artist_names(artist)))}"


class LibraryIndex:
    """'Is this catalog track already in my library?' Same title (version words aside) and at least one shared
    artist: rekordbox may credit "ARTBAT & Benny Benassi" where a catalog says "ARTBAT"."""

    def __init__(self, tracks):
        self.by_title: dict[str, list[tuple[set, str]]] = {}
        for t in tracks:
            self.by_title.setdefault(norm(t["title"]), []).append((artist_names(t["artist"]), t["id"]))

    def find(self, title: str, artist: str | list) -> str | None:
        want = artist_names(artist)
        return next((tid for names, tid in self.by_title.get(norm(title), []) if names & want), None)

    def __contains__(self, title_artist: tuple) -> bool:
        return self.find(*title_artist) is not None


# ---------- Apple: songs, genres, previews ----------

def _apple_track(t: dict) -> dict | None:
    if not isinstance(t, dict) or t.get("kind") != "song" or not isinstance(t.get("trackId"), int):
        return None
    ms = t.get("trackTimeMillis")
    preview, link, genre = t.get("previewUrl"), t.get("trackViewUrl"), t.get("primaryGenreName")
    return {"apple_id": t["trackId"], "title": str(t.get("trackName") or "Untitled")[:200],
            "artist": str(t.get("artistName") or "Unknown artist")[:120],
            "length": ms // 1000 if isinstance(ms, int) and ms > 0 else None,
            "genre": genre[:60] if isinstance(genre, str) and genre else None,
            "apple_url": link if isinstance(link, str) and APPLE_LINK.fullmatch(link) else None,
            "preview_url": preview if isinstance(preview, str) and APPLE_PREVIEW.fullmatch(preview) else None}


def apple_songs_url(term: str, country: str, by_artist: bool = False, limit: int = 25) -> str:
    params = {"term": term[:150], "media": "music", "entity": "song", "limit": limit, "country": _country(country)}
    if by_artist:
        params["attribute"] = "artistTerm"
    return f"{APPLE}/search?" + urllib.parse.urlencode(params)


def apple_songs(store, url: str) -> list[dict]:
    data = _cached(store, "songs", url) or {}
    return [c for c in (_apple_track(t) for t in data.get("results") or []) if c]


def apple_preview(apple_id: int, country: str = "us") -> str | None:
    """A fresh preview link, for when an older one has stopped working (asked for fresh, so it isn't cached)."""
    data = _http_json(f"{APPLE}/lookup?id={int(apple_id)}&entity=song&country={_country(country)}") or {}
    t = next((c for c in (_apple_track(r) for r in data.get("results") or []) if c), None)
    return t["preview_url"] if t else None


def search(store, q: str, library_index: LibraryIndex, country: str = "us", limit: int = 8) -> dict:
    """Free-text catalog search ("artist title"), each result with tempo and key where they can be found."""
    hits = apple_songs(store, apple_songs_url(q, country))
    seen, out = set(), []
    for t in hits:
        k = library_key(t["title"], t["artist"])
        if k in seen:
            continue
        seen.add(k)
        out.append({**t, "in_library": (t["title"], t["artist"]) in library_index})
        if len(out) >= limit:
            break
    problem = _add_features(store, out)
    return {"tracks": [_result(t, why=[]) for t in out],
            "notice": f"{problem} Tempo and key are missing for now." if problem else None}


# ---------- MusicBrainz + ListenBrainz: similar artists ----------

# Voters' tags that say where an artist sits. Dance, electronic and the like say nothing: they cover both sides.
FLOOR_TAG = re.compile(r"house|techno|trance|minimal|disco|rave|hardcore|hardstyle|psy|goa")
AWAY_TAG = re.compile(r"pop\b|edm|indie|rock|downtempo|ambient|chill|trip.?hop|idm|jazz|soul|hip.?hop|r&b|folk|lounge")


def _mb_artist(store, name: str, sounds=()) -> dict | None:
    """The MusicBrainz artist with exactly this name. If several share it, the one tagged with the plan's sound wins,
    then MusicBrainz's own best match."""
    want = norm(name)
    escaped = name[:80].replace("\\", "\\\\").replace('"', '\\"')
    q = urllib.parse.urlencode({"query": f'artist:"{escaped}"', "fmt": "json", "limit": 5})
    data = _cached(store, "artist", f"{MUSICBRAINZ}/artist/?{q}") or {}
    exact = [a for a in data.get("artists") or [] if isinstance(a, dict) and isinstance(a.get("id"), str)
             and MBID.fullmatch(a["id"]) and norm(a.get("name")) == want]

    def fits(a) -> bool:
        tags = [t.get("name") for t in a.get("tags") or [] if isinstance(t, dict)]
        return any(rb.same_sound(t, s) for t in tags for s in sounds)

    return max(exact, key=lambda a: (fits(a), a.get("score") or 0)) if exact else None


def artist_mbid(store, name: str, sounds=()) -> str | None:
    a = _mb_artist(store, name, sounds)
    return a["id"] if a else None


def artist_tags(store, name: str, sounds=()) -> dict[str, int]:
    """MusicBrainz's tags for this artist, with their votes."""
    tags = (_mb_artist(store, name, sounds) or {}).get("tags") or []
    return {t["name"].lower(): t["count"] for t in tags if isinstance(t, dict) and isinstance(t.get("name"), str)
            and isinstance(t.get("count"), int) and t["count"] > 0}


def leans_away(tags: dict[str, int]) -> bool:
    """Whether voters place this artist away from the club: at least as many votes for pop, EDM, downtempo and the
    like as for house, techno or trance. Apple files much of that under Dance or Electronic, so its genre can't tell.
    No tags is no evidence: small artists rarely have any."""
    away = sum(n for t, n in tags.items() if AWAY_TAG.search(t))
    return away > 0 and away >= sum(n for t, n in tags.items() if FLOOR_TAG.search(t))


def similar_artists(store, mbid: str) -> list[dict]:
    """Artists as {name, mbid}, closest first."""
    if not MBID.fullmatch(mbid):
        return []
    q = urllib.parse.urlencode({"artist_mbids": mbid, "algorithm": LB_ALGORITHM})
    data = _cached(store, "related", f"{LISTENBRAINZ}/similar-artists/json?{q}") or []
    return [{"name": a["name"][:120], "mbid": a.get("artist_mbid")} for a in data
            if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"]]


def artist_listeners(store, mbids: list[str]) -> dict[str, int]:
    """How many ListenBrainz users have played each artist, in one request. It's a small sample of all listening, so
    this only compares artists with each other. 0 for an artist it doesn't know."""
    mbids = [m for m in dict.fromkeys(mbids) if isinstance(m, str) and MBID.fullmatch(m)]
    known = {m: store.cache_get(f"{LB_POPULARITY}#{m}", CACHE_TTL["artist"]) for m in mbids}
    missing = [m for m, n in known.items() if n is None]
    if missing:
        rows = _http_json(LB_POPULARITY, {"artist_mbids": missing}) or []
        heard = {r.get("artist_mbid"): r.get("total_user_count") for r in rows if isinstance(r, dict)}
        for m in missing:
            n = heard.get(m)
            known[m] = n if isinstance(n, int) else 0
            store.cache_put(f"{LB_POPULARITY}#{m}", known[m])
    return known


def _add_features(store, tracks: list[dict], on_each=None) -> str | None:
    """Look up tempo / key for each track, 3 at a time. Returns what went wrong, if the lookup service failed: after a
    few failures in a row the rest are skipped, so an outage doesn't cost a 20 s timeout per track."""
    lock = threading.Lock()
    queue = list(tracks)
    problem = []
    failures = [0]

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                t = queue.pop(0)
            f = None
            if failures[0] < 3:
                try:
                    f = catalog_features(store, t["title"], t["artist"], t["length"])
                    failures[0] = 0
                except OnlineError as e:
                    problem[:] = [str(e)]
                    failures[0] += 1
            t["features"] = f or {}
            t["checked"] = True
            if on_each:
                on_each()

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(3)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return problem[0] if problem else None


def _result(t: dict, why: list[str], feel: float = 1.0) -> dict:
    f = t.get("features") or {}
    bpm = f.get("bpm")
    reasons = []
    if bpm:
        reasons.append(f"{bpm:g} BPM" + ("" if feel == 1 else f" ({'double' if feel == 0.5 else 'half'}-time)"))
    if f.get("key"):
        reasons.append(f"key ≈{f['key']}")
    reasons += why
    return {"id": f"web:am{t['apple_id']}", "apple_id": t["apple_id"], "title": t["title"], "artist": t["artist"],
            "bpm": bpm, "key": f.get("key"), "energy": f.get("energy"), "length": t["length"], "genre": t.get("genre"),
            "spotify_url": f.get("spotify_url"), "apple_url": t.get("apple_url"), "preview_url": t.get("preview_url"),
            "in_library": t.get("in_library", False), "online": True, "reasons": reasons}


# ---------- digging: new tracks for a plan ----------

def lead_artist(name: str) -> str:
    return re.split(r",|&|\bx\b|\bfeat\.?|\bft\.?", name, flags=re.I)[0].strip()


class DigJob:
    """Finds new tracks for a plan in the background, so results can show up while tempos are still being checked.

    Similar artists come from ListenBrainz, and every song comes from Apple: the plan artists' own, then their similar
    artists'. Apple's search stands in for an artist ListenBrainz knows nothing about. Songs that aren't the plan's
    sound are dropped, and so are similar artists MusicBrainz places away from the club or who are far better known
    than the plan's own, because ListenBrainz leans on what's popular. The best ones get a tempo / key lookup, and only
    tracks that fit the plan's tempo window are kept."""

    SEED_ARTISTS = 5
    SONGS_PER_ARTIST = 3  # also the most any one artist gets in the results
    APPLE_CALLS = 18      # Apple allows about 20 a minute: a dig from scratch stays under that, cached answers are free
    ENOUGH = 70           # candidates: stop spending Apple calls once there are this many
    FEW = 15              # candidates: fewer than this after everything, and Apple's genre search has a go
    MAX_SONG_SEC = 20 * 60  # longer than this is a DJ mix, not a track
    TO_CHECK = 45
    HOUSEHOLD = 20_000    # ListenBrainz listeners: above this a similar artist isn't much of a find, however good
    OUTSIZED = 3          # ...unless the plan has big names too: then it's this many times its biggest artist

    def __init__(self, store, seeds: list[dict], bpm_lo: float, bpm_hi: float, library_index: LibraryIndex,
                 limit: int = 30, country: str = "us"):
        self.store, self.seeds, self.lo, self.hi = store, seeds, bpm_lo, bpm_hi
        self.library_index, self.limit, self.country = library_index, limit, country
        self.id = f"{time.time_ns():x}"
        self.cands: dict[int, dict] = {}
        self.sounds = list(dict.fromkeys(s["genre"] for s in seeds if s.get("genre")))  # rekordbox's labels come first
        self.apple_left = self.APPLE_CALLS
        self.tags_down = False
        self.own_mbids: list[str] = []
        self.state = {"id": self.id, "status": "running", "step": "Starting", "artists_done": 0, "artists_total": 0,
                      "checked": 0, "to_check": 0, "tracks": [], "error": None, "notice": None}
        self.lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def status(self) -> dict:
        with self.lock:
            return {**self.state, "tracks": list(self.state["tracks"])}

    def _add(self, t: dict, rel: float, why: str) -> None:
        if (t["title"], t["artist"]) in self.library_index:
            return
        k = library_key(t["title"], t["artist"])
        c = self.cands.get(t["apple_id"])
        if c is None:
            if any(x["match_key"] == k for x in self.cands.values()):  # same song, another release
                return
            c = self.cands[t["apple_id"]] = {**t, "match_key": k, "rel": 0.0, "why": []}
        c["rel"] += rel
        if why not in c["why"]:
            c["why"].append(why)

    def _notice(self, text: str) -> None:
        with self.lock:
            self.state["notice"] = " ".join(filter(None, [self.state["notice"], text]))

    def _songs(self, term: str, by_artist: bool = False) -> list[dict]:
        """Apple's songs for a search, as long as this dig has Apple calls left."""
        url = apple_songs_url(term, self.country, by_artist)
        if self.store.cache_get(url, CACHE_TTL["songs"]) is None:
            if self.apple_left <= 0:
                return []
            self.apple_left -= 1
        return [t for t in apple_songs(self.store, url) if not t["length"] or t["length"] <= self.MAX_SONG_SEC]

    def _fits(self, t: dict) -> bool:
        return not t["genre"] or not self.sounds or any(rb.same_sound(t["genre"], g) for g in self.sounds)

    def _leans_away(self, name: str) -> bool:
        if self.tags_down:
            return False
        try:
            return leans_away(artist_tags(self.store, name, self.sounds))
        except OnlineError as e:
            self.tags_down = True
            self._notice(f"{e} Some artists outside the plan's sound may have got through.")
            return False

    def _by(self, name: str) -> list[dict]:
        """This artist's songs that fit the plan's sound, best known first."""
        want = artist_names(name)
        found = [t for t in self._songs(name, by_artist=True) if want <= artist_names(t["artist"])]
        return [t for t in found if self._fits(t)][:self.SONGS_PER_ARTIST]

    def _similar(self, artists: list[str]) -> tuple[dict[str, dict], list[str]]:
        """Similar artists by name, and the plan artists ListenBrainz had nothing for."""
        similar: dict[str, dict] = {}
        unknown = []
        for i, name in enumerate(artists):
            with self.lock:
                self.state["step"] = f"Finding artists like {name}"
            try:
                mbid = artist_mbid(self.store, name, self.sounds)
                found = similar_artists(self.store, mbid) if mbid else []
            except OnlineError as e:
                self._notice(f"{e} Showing songs from Apple's search instead of similar artists.")
                return similar, unknown + artists[i:]
            if mbid:
                self.own_mbids.append(mbid)
            if not found:
                unknown.append(name)
            for rank, other in enumerate(found):
                r = similar.setdefault(norm(other["name"]), {**other, "like": [], "rank": rank})
                r["like"].append(name)
                r["rank"] = min(r["rank"], rank)
        return similar, unknown

    def _not_outsized(self, todo: list[dict]) -> list[dict]:
        """Without the similar artists far better known than anyone in the plan."""
        try:
            heard = artist_listeners(self.store, self.own_mbids + [a["mbid"] for a in todo])
        except OnlineError as e:
            self._notice(f"{e} Some artists outside the plan's sound may have got through.")
            return todo
        most = max((heard.get(m, 0) for m in self.own_mbids), default=0)
        return [a for a in todo if heard.get(a["mbid"], 0) <= max(self.HOUSEHOLD, most * self.OUTSIZED)]

    def _collect(self, artists: list[str]) -> None:
        similar, unknown = self._similar(artists)
        seeds = {norm(a) for a in artists}
        todo = sorted((a for k, a in similar.items() if k not in seeds), key=lambda a: (-len(a["like"]), a["rank"]))
        with self.lock:
            self.state["artists_total"] = min(self.APPLE_CALLS, len(artists) + len(unknown) + len(todo))

        def done_one(step: str) -> None:
            with self.lock:
                self.state["step"] = step
                self.state["artists_done"] = min(self.state["artists_done"] + 1, self.state["artists_total"])

        own: Counter = Counter()
        for name in artists:
            done_one(f"Looking up songs by {name}")
            songs = self._songs(name, by_artist=True)
            mine = [t for t in songs if artist_names(name) <= artist_names(t["artist"])]
            own.update(t["genre"] for t in mine if t["genre"])
            for rank, t in enumerate([t for t in mine if self._fits(t)][:self.SONGS_PER_ARTIST]):
                self._add(t, 3.0 - rank / 5, f"more {name}")
        if not self.sounds:  # nothing from rekordbox: go by what Apple files the plan's artists under
            self.sounds = [g for g, _ in own.most_common(2)]
        for name in unknown:  # ListenBrainz doesn't know them: see who Apple's search turns up around them
            done_one(f"Searching Apple around {name}")
            for rank, t in enumerate(t for t in self._songs(name) if self._fits(t)):
                self._add(t, 1.0 - rank / 40, f"turns up with {name}")
        # a plan that's mostly crossover artists wants more of them
        strict = any("club" in rb.genre_families(s) for s in self.sounds) \
            and sum(map(self._leans_away, artists)) * 2 <= len(artists)
        for a in self._not_outsized(todo) if strict else todo:
            if self.apple_left <= 0 or len(self.cands) >= self.ENOUGH:
                break
            if strict and self._leans_away(a["name"]):
                continue
            done_one(f"Looking up songs by {a['name']}")
            base = 1.0 + len(a["like"]) + max(0.0, 1 - a["rank"] / 20)
            why = f"{a['name']} is like {' and '.join(a['like'][:2])}"
            for rank, t in enumerate(self._by(a["name"])):
                self._add(t, base - rank / 5, why)
        if len(self.cands) < self.FEW and self.sounds:
            done_one(f"Searching Apple for {self.sounds[0]}")
            for rank, t in enumerate(t for t in self._songs(self.sounds[0]) if self._fits(t)):
                self._add(t, 0.5 - rank / 50, f"Apple search for {self.sounds[0]}")

    def _run(self):
        try:
            primary = {}
            for s in self.seeds:  # the plan's artists, most frequent first
                name = lead_artist(s["artist"])
                if norm(name):
                    primary[name] = primary.get(name, 0) + 1
            artists = sorted(primary, key=lambda n: -primary[n])[:self.SEED_ARTISTS]
            try:
                self._collect(artists)
            except OnlineError as e:
                if not self.cands:
                    raise
                self._notice(f"{e} Showing what was found before that.")
            ranked = sorted(self.cands.values(), key=lambda c: -c["rel"])[:self.TO_CHECK]
            with self.lock:
                self.state.update(step="Checking tempo and key", to_check=len(ranked))

            def progress():
                with self.lock:
                    self.state["checked"] += 1
                    self.state["tracks"] = self._ranked(ranked)

            problem = _add_features(self.store, ranked, progress)
            if problem:
                self._notice(f"{problem} Some tempos and keys are missing, so check those by ear.")
            with self.lock:
                self.state.update(status="done", step="Done", tracks=self._ranked(ranked))
        except OnlineError as e:
            with self.lock:
                self.state.update(status="error", error=str(e), tracks=self._ranked(list(self.cands.values())))
        except Exception as e:  # never leave the page spinning
            with self.lock:
                self.state.update(status="error", error=f"Digging failed: {e}")

    def _ranked(self, cands: list[dict]) -> list[dict]:
        lo, hi = self.lo * 0.97, self.hi * 1.03
        mid = (self.lo + self.hi) / 2
        out = []
        for c in cands:
            if not c.get("checked"):
                continue
            f = c.get("features") or {}
            bpm = f.get("bpm")
            feel = next((m for m in (1.0, 0.5, 2.0) if bpm and lo <= bpm * m <= hi), None)
            if bpm and feel is None:
                continue  # tempo known and doesn't fit this plan
            score = c["rel"] * 4 + (10 - abs(bpm * feel - mid) / mid * 100 if feel else -6)
            why = list(c["why"][:1])
            if not bpm:
                why.insert(0, "tempo unknown")
            r = _result(c, why, feel or 1.0)
            r["score"] = round(score, 1)
            out.append(r)
        out.sort(key=lambda r: -r["score"])
        per_artist: Counter = Counter()
        kept = []
        for r in out:
            per_artist[norm(lead_artist(r["artist"]))] += 1
            if per_artist[norm(lead_artist(r["artist"]))] <= self.SONGS_PER_ARTIST:
                kept.append(r)
        return kept[:self.limit]


# ---------- filling in key & energy for the library ----------

class Enricher:
    """Background job: match library tracks without a key to the catalog, a few seconds per track, most-played
    first. Safe to stop and restart; finished tracks aren't looked up again for 30 days."""

    RECHECK_SEC = 30 * DAY

    def __init__(self):
        self.thread: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.state = {"running": False, "stopping": False, "done": 0, "total": 0, "matched": 0, "current": None,
                      "error": None}

    def start(self, lib, store, on_update) -> dict:
        if self.thread and self.thread.is_alive():
            return self.status()
        known = store.track_online()
        now = time.time()
        _, counts = lib._session_sets()
        todo = [t for t in lib.tracks.values()
                if not t["is_sample"] and not t.get("rb_key")
                and not (t["id"] in known and known[t["id"]]["status"] != "error"
                         and now - known[t["id"]]["checked_at"] < self.RECHECK_SEC)]
        todo.sort(key=lambda t: -counts[t["id"]])
        self.stop_flag.clear()
        self.state.update(running=True, stopping=False, done=0, total=len(todo), matched=0, current=None, error=None)
        self.thread = threading.Thread(target=self._run, args=(todo, store, on_update), daemon=True)
        self.thread.start()
        return self.status()

    def stop(self) -> dict:
        """Finishes the tracks already being looked up (a few seconds), then stops."""
        self.stop_flag.set()
        if self.state["running"]:
            self.state["stopping"] = True
        return self.status()

    def status(self) -> dict:
        return dict(self.state)

    WORKERS = 3

    def _run(self, todo, store, on_update):
        queue = list(reversed(todo))  # pop() from the end = most-played first
        lock = threading.Lock()
        failures = [0]

        def worker():
            while not self.stop_flag.is_set():
                with lock:
                    if not queue:
                        return
                    t = queue.pop()
                    self.state["current"] = t["title"]
                try:
                    m = match_library_track(store, t)
                except OnlineError as e:
                    store.set_track_online(t["id"], "error")
                    with lock:
                        failures[0] += 1
                        self.state["done"] += 1
                        if failures[0] >= 8:  # offline or blocked: stop instead of hammering
                            self.state["error"] = str(e)
                            self.stop_flag.set()
                    continue
                store.set_track_online(t["id"], "matched" if m else "not_found", **(m or {}))
                with lock:
                    failures[0] = 0
                    self.state["done"] += 1
                    self.state["matched"] += bool(m)
                    if self.state["done"] % 10 == 0:
                        on_update()

        try:
            workers = [threading.Thread(target=worker, daemon=True) for _ in range(self.WORKERS)]
            for w in workers:
                w.start()
            for w in workers:
                w.join()
        finally:
            self.state.update(running=False, stopping=False, current=None)
            on_update()
