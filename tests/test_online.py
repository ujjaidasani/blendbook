"""Online catalogs (online.py) and plans that hold online tracks. No network: every catalog call goes to a fake
router with canned Apple / MusicBrainz / ListenBrainz / ReccoBeats answers. Reuses test_security's throwaway database."""

import json
import re
import sqlite3
import sys
import time
import unittest
import urllib.error
import urllib.parse
from email.message import Message
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_security import db, server, track  # noqa: E402  (points db.DB_PATH at a temp dir first)

import online  # noqa: E402
import planner  # noqa: E402
import rb  # noqa: E402

R1, R2, R3, R4 = (f"{n}" * 8 + "-1111-2222-3333-444444444444" for n in "abcd")
MB1, MB2, BIG = (f"{n}" * 8 + "-1111-2222-3333-444444444444" for n in "efa")
SPOT = "https://open.spotify.com/track/" + "A" * 22


def recco(rid, title, artists, ms=300_000, href=SPOT):
    return {"id": rid, "trackTitle": title, "artists": [{"id": R4, "name": a} for a in artists],
            "durationMs": ms, "href": href, "isrc": "X"}


def feat(rid, tempo, key=9, mode=0, energy=0.8):
    return {"id": rid, "tempo": tempo, "key": key, "mode": mode, "energy": energy, "danceability": 0.7, "valence": 0.3}


def song(tid, title, artist, genre="Techno", ms=300_000):
    return {"wrapperType": "track", "kind": "song", "trackId": tid, "trackName": title, "artistName": artist,
            "trackTimeMillis": ms, "primaryGenreName": genre,
            "trackViewUrl": f"https://music.apple.com/us/album/x/{tid}?i={tid}&uo=4",
            "previewUrl": f"https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/{tid}.m4a"}


class FakeCatalog:
    """Routes catalog URLs to canned answers and counts calls."""

    def __init__(self):
        self.calls = []
        self.search = {}     # ReccoBeats title search text -> tracks
        self.features = {}   # ReccoBeats / Spotify id -> features
        self.artists = {}    # ReccoBeats artist search text -> [{id, name}]
        self.artist_tracks = {}
        self.by_artist = {}  # Apple artist search -> songs
        self.apple_search = {}  # Apple free-text search -> songs
        self.lookup = {}     # Apple track id -> songs
        self.mb = {}         # MusicBrainz artist name -> MBID
        self.mb_rows = {}    # MusicBrainz artist name -> full rows, when a test needs tags or several artists
        self.similar = {}    # MBID -> similar artist names
        self.similar_ids = {}  # similar artist name -> its MBID, when a test needs them apart
        self.listeners = {}  # MBID -> ListenBrainz users
        self.fail = {}       # host -> OnlineError message

    def __call__(self, url, body=None):
        self.calls.append(url)
        u = urllib.parse.urlsplit(url)
        if u.hostname in self.fail:
            raise online.OnlineError(self.fail[u.hostname])
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.hostname == "api.reccobeats.com":
            if u.path == "/v1/track/search":
                return {"content": self.search.get(q["searchText"], [])}
            if u.path == "/v1/audio-features":
                return {"content": [self.features[i] for i in q["ids"].split(",") if i in self.features]}
            if u.path == "/v1/artist/search":
                return {"content": self.artists.get(q["searchText"], [])}
            if u.path.endswith("/track"):
                return {"content": self.artist_tracks.get(u.path.split("/")[3], []), "totalPages": 1}
        if u.hostname == "itunes.apple.com":
            if u.path == "/lookup":
                return {"results": self.lookup.get(int(q["id"]), [])}
            return {"results": (self.by_artist if q.get("attribute") == "artistTerm" else self.apple_search).get(q["term"], [])}
        if u.hostname == "musicbrainz.org":
            name = re.fullmatch(r'artist:"(.*)"', q["query"]).group(1)
            rows = self.mb_rows.get(name) or ([{"id": self.mb[name], "name": name, "score": 100}] if name in self.mb else [])
            return {"artists": rows}
        if u.hostname == "labs.api.listenbrainz.org":
            return [{"artist_mbid": self.similar_ids.get(n, MB2), "name": n, "score": 10}
                    for n in self.similar.get(q["artist_mbids"], [])]
        if u.hostname == "api.listenbrainz.org":
            return [{"artist_mbid": m, "total_user_count": self.listeners[m], "total_listen_count": 1}
                    for m in body["artist_mbids"] if m in self.listeners]
        raise AssertionError(f"unexpected URL {url}")


class OnlineTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCatalog()
        self._orig = online._http_json
        online._http_json = self.fake
        with server.store.conn:  # every test starts with an empty cache
            server.store.conn.execute("DELETE FROM online_cache")

    def tearDown(self):
        online._http_json = self._orig


class MatchingTests(unittest.TestCase):
    def test_normalising_titles_and_artists(self):
        self.assertEqual(online.norm("Innerbloom (Extended Mix)"), "innerbloom")
        self.assertEqual(online.norm("Sweet Disposition (feat. Andy Ruddy)"), "sweet disposition")
        self.assertEqual(online.norm("RÜFÜS DU SOL"), "rufus du sol")
        self.assertEqual(online.artist_names("ARTBAT & Benny Benassi"), {"artbat", "benny benassi"})
        self.assertEqual(online.title_score("Last Night - Anyma x Layton Giordani Remix",
                                            "Last Night (Anyma x Layton Giordani Remix)"), 1.0)

    def test_camelot_and_tempo(self):
        self.assertEqual((online.camelot(9, 0), online.camelot(0, 1), online.camelot(-1, 0)), ("8A", "8B", None))
        self.assertTrue(online.same_tempo(64.1, 128))
        self.assertTrue(online.same_tempo(127, 128))
        self.assertFalse(online.same_tempo(120, 128))

    def test_library_index_accepts_partial_artist_credits(self):
        idx = online.LibraryIndex([{"id": "7", "title": "Love is Gonna Save Us", "artist": "ARTBAT & Benny Benassi"}])
        self.assertEqual(idx.find("Love Is Gonna Save Us", "ARTBAT"), "7")
        self.assertIsNone(idx.find("Love Is Gonna Save Us", "DBL"))
        self.assertIn(("love is gonna save us (Extended Mix)", "Benny Benassi"), idx)

    def test_untrusted_links_are_dropped(self):
        t = online._clean_track(recco(R1, "X", ["A"], href="javascript:alert(1)"))
        self.assertIsNone(t["spotify_url"])
        self.assertIsNone(online._clean_track({"id": "../../etc", "trackTitle": "X"}))
        d = online._apple_track({**song(5, "X", "A"), "previewUrl": "https://evil.example/x.mp3",
                                 "trackViewUrl": "javascript:alert(1)"})
        self.assertEqual((d["preview_url"], d["apple_url"]), (None, None))
        self.assertIsNone(online._apple_track({"kind": "podcast", "trackId": 5}))
        with self.assertRaises(online.OnlineError):
            online._http_json("https://evil.example/api")


class LibraryMatchTests(OnlineTestCase):
    def test_match_needs_artist_title_and_tempo(self):
        self.fake.search["Innerbloom"] = [recco(R1, "Innerbloom", ["Someone Else"]),
                                          recco(R2, "Innerbloom", ["RÜFÜS DU SOL"]),
                                          recco(R3, "Innerbloom - Radio Edit", ["RÜFÜS DU SOL"])]
        self.fake.features = {R2: feat(R2, 122.0, key=9, mode=1), R3: feat(R3, 140.0)}
        t = {**track("1", 122), "title": "Innerbloom", "artist": "RÜFÜS DU SOL", "source": "apple-music"}
        m = online.match_library_track(server.store, t)
        self.assertEqual((m["recco_id"], m["key"], m["bpm"]), (R2, "11B", 122.0))
        # same title and artist but the tempo disagrees with rekordbox: not trusted
        self.fake.features = {R2: feat(R2, 100.0), R3: feat(R3, 140.0)}
        with server.store.conn:
            server.store.conn.execute("DELETE FROM online_cache")
        self.assertIsNone(online.match_library_track(server.store, t))

    def test_short_titles_fall_back_to_the_artist_catalog(self):
        self.fake.search["Nova"] = [recco(R1, "Nova", ["Somebody"])]
        self.fake.artists["Tale Of Us"] = [{"id": R3, "name": "Tale Of Us"}, {"id": R4, "name": "Tale Of Us Tribute"}]
        self.fake.artist_tracks[R3] = [recco(R2, "Nova", ["Tale Of Us"])]
        self.fake.features = {R2: feat(R2, 123.0)}
        t = {**track("2", 123), "title": "Nova", "artist": "Tale Of Us", "source": "apple-music"}
        self.assertEqual(online.match_library_track(server.store, t)["recco_id"], R2)
        self.assertFalse(any(R4 in u for u in self.fake.calls), "only exact artist-name matches are browsed")

    def test_responses_are_cached(self):
        self.fake.features = {R1: feat(R1, 124)}
        online.features(server.store, [R1])
        n = len(self.fake.calls)
        self.assertEqual(online.features(server.store, [R1])[R1]["bpm"], 124)
        self.assertEqual(len(self.fake.calls), n)


class DigTests(OnlineTestCase):
    def dig(self, seeds, lo=122, hi=126, index=None, **kw):
        job = online.DigJob(server.store, seeds, lo, hi, index or online.LibraryIndex([]), **kw)
        for _ in range(100):
            if job.status()["status"] != "running":
                break
            time.sleep(0.05)
        return job.status()

    def test_dig_keeps_tempo_fits_and_skips_your_library(self):
        self.fake.mb["Innellea"] = MB1
        self.fake.similar[MB1] = ["Mind Against", "Colyn", "Somebody", "Nobody"]
        self.fake.by_artist = {"Mind Against": [song(1, "FLORAL", "Mind Against")],
                               "Colyn": [song(2, "Owned Already", "Colyn")],
                               "Somebody": [song(3, "Way Too Fast", "Somebody")],
                               "Nobody": [song(4, "Mystery Tempo", "Nobody")]}
        self.fake.search = {"FLORAL": [recco(R1, "FLORAL", ["Mind Against"])],
                            "Way Too Fast": [recco(R2, "Way Too Fast", ["Somebody"])]}
        self.fake.features = {R1: feat(R1, 124.0, key=9, mode=1), R2: feat(R2, 174.0)}
        index = online.LibraryIndex([{"id": "9", "title": "Owned Already", "artist": "Colyn & Friend"}])
        st = self.dig([{"title": "Inside Your Mind", "artist": "Innellea", "genre": "Melodic Techno"}], index=index)
        self.assertEqual(st["status"], "done", st)
        titles = [t["title"] for t in st["tracks"]]
        self.assertEqual(titles[0], "FLORAL")             # tempo fits
        self.assertFalse(any("harmonic" in r or "plan's keys" in r for r in st["tracks"][0]["reasons"]))  # online key
        self.assertEqual(st["tracks"][0]["key"], "11B")
        self.assertIn("Mystery Tempo", titles)            # no tempo data: kept, flagged
        self.assertNotIn("Owned Already", titles)         # already in the library
        self.assertNotIn("Way Too Fast", titles)          # 174 BPM doesn't fit 122–126
        self.assertTrue(all(t["id"].startswith("web:am") for t in st["tracks"]))
        self.assertTrue(st["tracks"][0]["preview_url"].startswith("https://audio-ssl.itunes.apple.com/"))
        self.assertTrue(st["tracks"][0]["apple_url"].startswith("https://music.apple.com/"))
        self.assertFalse(any("deezer" in u or "dzcdn" in u for u in self.fake.calls))

    def test_includes_the_plan_artists_own_songs(self):
        self.fake.by_artist["Innellea"] = [song(5, "Vigilans", "Innellea")]
        st = self.dig([{"title": "Other", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual([t["title"] for t in st["tracks"]], ["Vigilans"])
        self.assertIn("more Innellea", st["tracks"][0]["reasons"])

    def test_own_songs_hold_their_place_against_a_full_list_of_similar_artists(self):
        self.fake.mb["Innellea"] = MB1
        names = [f"Artist {i}" for i in range(12)]
        self.fake.similar[MB1] = names
        self.fake.by_artist = {n: [song(100 * i + j, f"S{i}-{j}", n) for j in range(5)] for i, n in enumerate(names, 1)}
        self.fake.by_artist["Innellea"] = [song(5, "Vigilans", "Innellea")]
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertIn("Vigilans", [t["title"] for t in st["tracks"]])

    def test_popular_artists_outside_the_plans_sound_are_dropped(self):
        self.fake.mb["Innellea"] = MB1
        self.fake.similar[MB1] = ["Rihanna", "Colyn"]
        self.fake.by_artist = {"Rihanna": [song(1, "Umbrella", "Rihanna", genre="Pop")],
                               "Colyn": [song(2, "Resolve", "Colyn", genre="Dance")]}
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Melodic House & Techno"}])
        self.assertEqual([t["title"] for t in st["tracks"]], ["Resolve"])

    def test_with_no_rekordbox_genre_apple_files_the_plan_artists(self):
        self.fake.mb["Innellea"] = MB1
        self.fake.similar[MB1] = ["Rihanna", "Colyn"]
        self.fake.by_artist = {"Innellea": [song(9, "Vigilans", "Innellea", genre="Techno")],
                               "Rihanna": [song(1, "Umbrella", "Rihanna", genre="Pop")],
                               "Colyn": [song(2, "Resolve", "Colyn", genre="Dance")]}
        st = self.dig([{"title": "X", "artist": "Innellea"}])
        self.assertEqual({t["title"] for t in st["tracks"]}, {"Vigilans", "Resolve"})

    def club_plan(self, *similar):
        """A plan around Innellea, with these similar artists and one Dance song each: Apple's label can't separate them."""
        self.fake.mb["Innellea"] = MB1
        self.fake.similar[MB1] = list(similar)
        self.fake.by_artist = {n: [song(i, f"Song {i}", n, genre="Dance")] for i, n in enumerate(similar, 1)}

    def tag(self, name, **votes):
        self.fake.mb_rows[name] = [{"id": MB2, "name": name, "score": 100, "tags": [
            {"name": t.replace("_", " "), "count": n} for t, n in votes.items()]}]

    def test_voters_tags_tell_club_artists_from_what_apple_files_under_dance(self):
        tags = {"Tycho": {"downtempo": 8, "ambient": 4, "deep house": 1, "electronic": 2},
                "The Chainsmokers": {"electro house": 5, "house": 1, "edm": 4, "electropop": 2, "pop": 1},
                "Metronomy": {"synth-pop": 5, "indie rock": 3}}
        for name, votes in tags.items():
            self.assertTrue(online.leans_away(votes), name)
        for votes in ({"melodic techno": 2, "house": 1}, {"house": 1, "deep house": 1, "melodic techno": 1, "edm": 1},
                      {"psytrance": 20, "goa trance": 7, "triphop": 2}, {"electronic": 3, "dance": 2}, {}):
            self.assertFalse(online.leans_away(votes), votes)

    def test_similar_artists_away_from_the_club_are_dropped_before_apple_is_asked(self):
        self.club_plan("Tycho", "Woo York", "Tiny Act")
        self.tag("Tycho", downtempo=8, ambient=4)
        self.tag("Woo York", melodic_techno=2)
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual({t["artist"] for t in st["tracks"]}, {"Woo York", "Tiny Act"})  # no tags: kept
        self.assertFalse(any("Tycho" in u for u in self.fake.calls if "itunes.apple.com" in u))

    def test_a_crossover_plan_keeps_crossover_artists(self):
        self.club_plan("Kygo")
        self.tag("Innellea", edm=4, dance_pop=2)
        self.tag("Kygo", edm=6)
        self.fake.mb_rows["Innellea"][0]["id"] = MB1
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Dance"}])
        self.assertEqual([t["artist"] for t in st["tracks"]], ["Kygo"])

    def famous(self, plan_listeners):
        """Tiesto is much better known than Woo York, and Innellea has this many listeners."""
        self.club_plan("Tiesto", "Woo York")
        self.fake.similar_ids = {"Tiesto": BIG, "Woo York": MB2}
        self.fake.listeners = {BIG: 134_000, MB2: 500, MB1: plan_listeners}

    def test_household_names_are_dropped_before_apple_is_asked(self):
        self.famous(plan_listeners=700)
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual([t["artist"] for t in st["tracks"]], ["Woo York"])
        self.assertFalse(any("Tiesto" in u for u in self.fake.calls if "itunes.apple.com" in u))
        self.assertEqual(sum("api.listenbrainz.org" in u and "labs" not in u for u in self.fake.calls), 1)

    def test_a_plan_with_big_names_keeps_big_names(self):
        self.famous(plan_listeners=60_000)
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual({t["artist"] for t in st["tracks"]}, {"Tiesto", "Woo York"})

    def test_listener_counts_are_cached(self):
        self.fake.listeners = {MB1: 5}
        self.assertEqual(online.artist_listeners(server.store, [MB1, MB2]), {MB1: 5, MB2: 0})
        self.assertEqual(online.artist_listeners(server.store, [MB1, MB2, "nonsense"]), {MB1: 5, MB2: 0})
        self.assertEqual(len(self.fake.calls), 1)

    def test_with_no_listener_counts_the_dig_says_so_and_carries_on(self):
        self.famous(plan_listeners=700)
        self.fake.fail["api.listenbrainz.org"] = "ListenBrainz is busy right now. Try again in a minute."
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual({t["artist"] for t in st["tracks"]}, {"Tiesto", "Woo York"})
        self.assertIn("may have got through", st["notice"])

    def test_outside_club_music_the_tags_arent_used(self):
        self.club_plan("Fela Kuti")
        self.fake.by_artist["Fela Kuti"] = [song(1, "Zombie", "Fela Kuti", genre="Afrobeat")]
        self.tag("Fela Kuti", jazz=9)
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Afrobeats"}])
        self.assertEqual([t["title"] for t in st["tracks"]], ["Zombie"])

    def test_no_artist_takes_over_the_results(self):
        self.fake.by_artist["Innellea"] = [song(i, f"Song {i}", "Innellea" if i % 2 else "Innellea & Friend") for i in range(1, 7)]
        self.fake.apple_search["Innellea"] = [song(i, f"More {i}", "Innellea") for i in range(10, 16)]
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual(len(st["tracks"]), online.DigJob.SONGS_PER_ARTIST)

    def test_when_musicbrainz_goes_down_the_dig_says_so_and_carries_on(self):
        self.club_plan("Woo York")
        real = self.fake

        def flaky(url, body=None):
            if "musicbrainz.org" in url and "Woo" in url:
                raise online.OnlineError("MusicBrainz is busy right now.")
            return real(url, body)
        online._http_json = flaky
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual(st["status"], "done", st)
        self.assertEqual([t["artist"] for t in st["tracks"]], ["Woo York"])
        self.assertIn("may have got through", st["notice"])

    def test_apple_search_stands_in_when_there_are_no_similar_artists(self):
        self.fake.mb["Tiny Act"] = MB1   # MusicBrainz knows them, ListenBrainz has nothing
        self.fake.apple_search["Tiny Act"] = [song(1, "Dub (Tiny Act Remix)", "Someone", genre="Techno"),
                                              song(2, "Ballad", "Crooner", genre="Country")]
        st = self.dig([{"title": "X", "artist": "Tiny Act", "genre": "Techno"}])
        self.assertEqual(st["status"], "done", st)
        self.assertEqual([t["title"] for t in st["tracks"]], ["Dub (Tiny Act Remix)"])

    def test_a_dig_stays_inside_apples_budget_and_shows_progress(self):
        self.fake.mb["Innellea"] = MB1
        names = [f"Artist {i}" for i in range(40)]
        self.fake.similar[MB1] = names
        self.fake.by_artist = {n: [] for n in names}
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        apple = [u for u in self.fake.calls if "itunes.apple.com" in u]
        self.assertLessEqual(len(apple), online.DigJob.APPLE_CALLS)
        self.assertEqual(st["artists_done"], st["artists_total"])
        self.assertGreater(st["artists_total"], 1)

    def test_a_second_dig_is_cached_and_costs_nothing(self):
        self.fake.mb["Innellea"] = MB1
        self.fake.similar[MB1] = ["Colyn"]
        self.fake.by_artist = {"Colyn": [song(2, "Resolve", "Colyn")]}
        seeds = [{"title": "X", "artist": "Innellea", "genre": "Techno"}]
        self.dig(seeds)
        first = [u for u in self.fake.calls if "reccobeats" not in u]
        self.dig(seeds)
        self.assertEqual([u for u in self.fake.calls if "reccobeats" not in u], first)

    def test_similar_artist_failure_is_shown_and_the_dig_carries_on(self):
        self.fake.fail["labs.api.listenbrainz.org"] = "ListenBrainz is busy right now. Try again in a minute."
        self.fake.mb["Innellea"] = MB1
        self.fake.by_artist["Innellea"] = [song(5, "Vigilans", "Innellea")]
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual(st["status"], "done", st)
        self.assertIn("ListenBrainz is busy", st["notice"])
        self.assertEqual([t["title"] for t in st["tracks"]], ["Vigilans"])

    def test_tempo_lookup_failure_is_shown(self):
        self.fake.fail["api.reccobeats.com"] = "Couldn't reach ReccoBeats. Are you online? (down)"
        self.fake.by_artist["Innellea"] = [song(5, "Vigilans", "Innellea")]
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual(st["status"], "done", st)
        self.assertIn("Couldn't reach ReccoBeats", st["notice"])
        self.assertIn("tempo unknown", st["tracks"][0]["reasons"])

    def test_an_outage_stops_the_tempo_lookups_early(self):
        self.fake.fail["api.reccobeats.com"] = "Couldn't reach ReccoBeats."
        self.fake.by_artist["Innellea"] = [song(i, f"Song {i}", "Innellea") for i in range(1, 6)]
        self.fake.mb["Innellea"] = MB1
        self.fake.similar[MB1] = ["Colyn"]
        self.fake.by_artist["Colyn"] = [song(i, f"Colyn {i}", "Colyn") for i in range(10, 15)]
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual((st["status"], len(st["tracks"])), ("done", 2 * online.DigJob.SONGS_PER_ARTIST))
        self.assertLessEqual(len([u for u in self.fake.calls if "reccobeats" in u]), 5)

    def test_apple_failure_is_a_clear_error(self):
        self.fake.fail["itunes.apple.com"] = "Apple's catalog is busy right now. Try again in a minute."
        st = self.dig([{"title": "X", "artist": "Innellea", "genre": "Techno"}])
        self.assertEqual((st["status"], st["error"]), ("error", "Apple's catalog is busy right now. Try again in a minute."))


class CatalogTests(OnlineTestCase):
    def test_search_uses_apple_and_keeps_every_link(self):
        self.fake.apple_search["boris house"] = [song(1, "House Music", "Boris Brejcha"),
                                                 song(2, "House Music (Extended Mix)", "Boris Brejcha")]
        found = online.search(server.store, "boris house", online.LibraryIndex([]), "gb")
        self.assertEqual([t["id"] for t in found["tracks"]], ["web:am1"])   # same song, another release
        self.assertIsNone(found["notice"])
        self.assertIn("country=gb", self.fake.calls[0])
        t = server.with_links({**found["tracks"][0]})
        self.assertTrue(t["apple_url"].startswith("https://music.apple.com/us/album/"))  # the store page, not a search
        self.assertTrue(t["beatport_url"].startswith("https://www.beatport.com/search?"))
        self.assertTrue(t["spotify_url"].startswith("https://open.spotify.com/search/"))

    def test_a_fresh_preview_comes_from_apple_uncached(self):
        self.fake.lookup[77] = [song(77, "X", "A")]
        for _ in (1, 2):
            self.assertTrue(online.apple_preview(77).startswith("https://audio-ssl.itunes.apple.com/"))
        self.assertEqual(len(self.fake.calls), 2)

    def test_same_named_artists_go_by_the_plans_sound(self):
        self.fake.mb_rows["Push"] = [{"id": MB1, "name": "Push", "score": 100, "tags": [{"name": "rock"}]},
                                     {"id": MB2, "name": "Push", "score": 90, "tags": [{"name": "trance"}]}]
        self.assertEqual(online.artist_mbid(server.store, "Push", ["Techno"]), MB2)
        self.assertEqual(online.artist_mbid(server.store, "Pushing", ["Techno"]), None)

    def test_names_cannot_break_out_of_the_musicbrainz_query(self):
        online.artist_mbid(server.store, 'Bad"Name\\')
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.fake.calls[-1]).query)["query"][0]
        self.assertEqual(q, 'artist:"Bad\\"Name\\\\"')


class PacingTests(unittest.TestCase):
    """The real HTTP path, with the network and the waits faked."""

    def setUp(self):
        self.sleeps = []
        self._sleep, self._urlopen = online.time.sleep, online.urllib.request.urlopen
        online.time.sleep = self.sleeps.append
        online._last_start.clear()
        self.addCleanup(self.restore)

    def restore(self):
        online.time.sleep, online.urllib.request.urlopen = self._sleep, self._urlopen

    def refuse(self, code, headers=None):
        def urlopen(req, **kw):
            self.ua = req.get_header("User-agent")
            raise urllib.error.HTTPError(req.full_url, code, "no", Message(), None)
        online.urllib.request.urlopen = urlopen

    def test_slow_down_answers_become_clear_messages(self):
        for url, code, says in [(f"{online.MUSICBRAINZ}/artist/", 503, "MusicBrainz is busy"),
                                (f"{online.APPLE}/search?term=x", 403, "Apple's catalog is busy"),
                                ("https://labs.api.listenbrainz.org/similar-artists/json", 429, "ListenBrainz is busy")]:
            self.refuse(code)
            with self.assertRaises(online.OnlineError) as e:
                online._http_json(url)
            self.assertIn(says, str(e.exception))

    def test_requests_name_blendbook(self):
        self.refuse(500)
        with self.assertRaises(online.OnlineError):
            online._http_json(f"{online.MUSICBRAINZ}/artist/")
        self.assertEqual(self.ua, "Blendbook/1.0 ( https://github.com/ujjaidasani/blendbook )")

    def test_apple_is_paced_under_its_limit(self):
        self.assertGreaterEqual(online.MIN_INTERVAL["itunes.apple.com"], 60 / 20)
        self.assertGreaterEqual(online.MIN_INTERVAL["musicbrainz.org"], 1)
        self.assertGreaterEqual(online.MIN_INTERVAL["labs.api.listenbrainz.org"], 1)


class MigrationTests(unittest.TestCase):
    def test_old_online_tracks_lose_only_what_pointed_at_deezer(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (0)")
        for sql in db.MIGRATIONS[:3]:
            conn.executescript(sql)
        conn.execute("UPDATE schema_version SET version = 3")
        conn.execute("INSERT INTO online_tracks VALUES ('web:dz777', ?, 1)", (json.dumps(
            {"id": "web:dz777", "deezer_id": 777, "title": "Old", "artist": "A", "bpm": 126.0,
             "deezer_url": "https://www.deezer.com/track/777", "spotify_url": SPOT}),))
        conn.execute("INSERT INTO online_tracks VALUES ('web:bad', 'not json', 1)")
        conn.execute("INSERT INTO online_cache VALUES ('https://api.deezer.com/track/1', '{}', 1)")
        conn.execute("INSERT INTO online_cache VALUES ('https://api.reccobeats.com/v1/x', '{}', 1)")
        db.migrate(conn)
        old = json.loads(conn.execute("SELECT data FROM online_tracks WHERE id = 'web:dz777'").fetchone()[0])
        self.assertEqual(old, {"id": "web:dz777", "title": "Old", "artist": "A", "bpm": 126.0, "spotify_url": SPOT})
        self.assertEqual(conn.execute("SELECT data FROM online_tracks WHERE id = 'web:bad'").fetchone()[0], "not json")
        self.assertEqual([r[0] for r in conn.execute("SELECT key FROM online_cache")], ["https://api.reccobeats.com/v1/x"])
        self.assertEqual(conn.execute("SELECT version FROM schema_version").fetchone()[0], len(db.MIGRATIONS))


class EnricherTests(OnlineTestCase):
    def test_fills_keys_most_played_first_and_overlays_the_library(self):
        lib = rb.RekordboxLibrary(Path("/nonexistent"))
        lib.tracks = {t["id"]: {**t, "rb_key": None, "stream_id": None} for t in
                      [{**track("1", 122), "title": "Innerbloom", "artist": "RÜFÜS DU SOL"},
                       {**track("2", 128), "title": "Unknown Thing", "artist": "Nobody"}]}
        self.fake.search["Innerbloom"] = [recco(R2, "Innerbloom", ["RÜFÜS DU SOL"])]
        self.fake.features = {R2: feat(R2, 122.0, key=9, mode=1)}
        updates = []
        e = online.Enricher()
        e.start(lib, server.store, lambda: updates.append(1))
        e.thread.join(10)
        st = e.status()
        self.assertEqual((st["running"], st["done"], st["matched"]), (False, 2, 1))
        rows = server.store.track_online()
        self.assertEqual((rows["1"]["status"], rows["1"]["key"], rows["2"]["status"]), ("matched", "11B", "not_found"))
        lib.set_online(rows, {})
        self.assertEqual((lib.tracks["1"]["key"], lib.tracks["1"]["key_source"]), ("11B", "online"))
        # rekordbox's own analysis always wins over online data
        lib.tracks["1"].update(rb_key="5A", key="5A", key_source="rekordbox")
        lib.set_online(rows, {})
        self.assertEqual(lib.tracks["1"]["key"], "5A")


class PlansWithOnlineTracks(OnlineTestCase):
    WEB = "web:am777"
    OLD = "web:dz555"  # saved before catalog tracks came from Apple

    def setUp(self):
        super().setUp()
        server.library.tracks = {t["id"]: {**t, "rb_key": None} for t in
                                 [track("1", 124), track("2", 125), track("3", 126), track("5", 128)]}
        server.library.sessions = []
        server.library._cache = {}
        server._index_cache.clear()
        server.remember_online([{"id": self.WEB, "apple_id": 777, "title": "Welcome To The Underworld",
                                 "artist": "Massano", "bpm": 126.0, "key": "10B", "length": 300, "spotify_url": SPOT,
                                 "apple_url": "https://music.apple.com/us/album/x/777?i=777",
                                 "preview_url": "https://audio-ssl.itunes.apple.com/x.m4a"},
                                {"id": self.OLD, "title": "Older Find", "artist": "Colyn", "bpm": 124.0, "length": 300}])

    def test_online_track_is_kept_without_its_expiring_preview(self):
        t = server.library.track(self.WEB)
        self.assertEqual((t["title"], t["genre"], t.get("preview_url")), ("Welcome To The Underworld", None, None))
        brief = server.with_links(server.library._brief(self.WEB))
        self.assertTrue(brief["online"])
        self.assertEqual(brief["apple_url"], "https://music.apple.com/us/album/x/777?i=777")
        self.assertTrue(brief["beatport_url"].startswith("https://www.beatport.com/"))

    def test_plans_saved_with_old_ids_still_load_and_export(self):
        old = server.with_links(server.library._brief(self.OLD))
        self.assertEqual(old["title"], "Older Find")
        self.assertIn("/search?", old["apple_url"])  # no store page on record, so a search link
        self.assertTrue(old["spotify_url"].startswith("https://open.spotify.com/search/"))
        pid = server.store.add_set_plan("Old one", ["1", self.OLD, "3"], "warmup")
        sent = {}
        orig = planner.export_playlist
        planner.export_playlist = lambda rb_dir, name, ids: sent.update(ids=ids) or {"playlist_id": "56", "name": name}
        try:
            st, data, _ = self._get("/api/plans")
            self.assertEqual(st, 200)
            plan = next(p for p in data["plans"] if p["id"] == pid)
            self.assertEqual([t["id"] for t in plan["tracks"]], ["1", self.OLD, "3"])
            st, data, _ = self._post(f"/api/plans/{pid}/export", {"confirm": True})
            self.assertEqual(st, 200, data)
            self.assertEqual(sent["ids"], ["1", "3"])
            self.assertEqual([t["title"] for t in data["not_in_library"]], ["Older Find"])
        finally:
            planner.export_playlist = orig
            server.store.delete_set_plan(pid)

    def test_planner_keeps_a_locked_online_track_and_chains_from_it(self):
        plan = planner.plan_set(server.library, "1", 4, "warmup", [], "some", {"2": self.WEB})
        ids = [t["id"] for t in plan["tracks"]]
        self.assertEqual(ids[2], self.WEB)
        self.assertEqual(plan["tracks"][2]["reasons"], ["added from online"])
        self.assertEqual(len(ids), 4)

    def test_export_skips_online_tracks_until_they_are_in_your_library(self):
        pid = server.store.add_set_plan("Tonight", ["1", self.WEB, "3"], "warmup")
        sent = {}
        orig = planner.export_playlist
        planner.export_playlist = lambda rb_dir, name, ids: sent.update(ids=ids) or {"playlist_id": "55", "name": name}
        try:
            st, data, _ = self._post(f"/api/plans/{pid}/export", {"confirm": True})
            self.assertEqual(st, 200)
            self.assertEqual(sent["ids"], ["1", "3"])
            self.assertEqual([t["title"] for t in data["not_in_library"]], ["Welcome To The Underworld"])
            # once you add it in rekordbox, the plan picks up the library copy
            server.library.tracks["9"] = {**track("9", 126, "Welcome To The Underworld", "Massano"), "rb_key": None}
            server._index_cache.clear()
            self.assertEqual(server.resolve_ids(["1", self.WEB]), ["1", "9"])
        finally:
            planner.export_playlist = orig
            server.store.delete_set_plan(pid)

    def test_only_one_dig_runs_at_a_time(self):
        class Running:
            def status(self):
                return {"status": "running", "tracks": []}
        server.dig_jobs["busy"] = Running()
        try:
            st, data, _ = self._post("/api/online/dig", {"track_ids": ["1"], "bpm_min": 120, "bpm_max": 126})
            self.assertEqual(st, 409, data)
        finally:
            server.dig_jobs.pop("busy", None)

    def test_online_endpoints_validate_input(self):
        self.assertEqual(self._post("/api/online/dig", {"track_ids": [], "bpm_min": 120, "bpm_max": 126})[0], 400)
        self.assertEqual(self._post("/api/online/dig", {"track_ids": ["1"], "bpm_min": 130, "bpm_max": 120})[0], 400)
        self.assertEqual(self._post("/api/online/preview", {"apple_id": "../x"})[0], 400)
        self.assertEqual(self._post("/api/online/search", {"q": "a"})[1], {"tracks": []})
        st, data, _ = self._get("/api/online/dig/zzzz")
        self.assertEqual(st, 404)

    def _post(self, path, body):
        return self._req("POST", path, body)

    def _get(self, path):
        return self._req("GET", path, None)

    def _req(self, method, path, body):
        import http.client
        import threading
        if not hasattr(PlansWithOnlineTracks, "httpd"):
            PlansWithOnlineTracks.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=PlansWithOnlineTracks.httpd.serve_forever, daemon=True).start()
        port = PlansWithOnlineTracks.httpd.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Host": f"localhost:{port}", "Content-Type": "application/json"})
        res = conn.getresponse()
        data = json.loads(res.read() or b"null")
        conn.close()
        return res.status, data, res


if __name__ == "__main__":
    unittest.main()
