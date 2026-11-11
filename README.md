<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/logo-dark.svg">
    <img alt="Blendbook" src=".github/logo-light.svg" width="288" height="60">
  </picture>
</h1>

**For dance music and techno DJs who want better taste and better sets.** Blendbook reads your real rekordbox library
and history, shows you what you actually play and what you've forgotten, helps you plan a set around your own best
transitions, and finds music that fits it.

Works on Mac and Windows, with rekordbox 6 or 7 and any MIDI controller. Your library stays on your computer. **[blendbook.app](https://blendbook.app)**

- **Plan your set:** pick an opener and an energy shape, and Blendbook builds the set from transitions you've proven
  before, then sends it to rekordbox as a playlist.
- **Grow your taste:** your signature blends, the favourites you've dropped, the tracks you've never played out, and
  new music that fits the set you're building.
- **Learn from every set:** what happened, where tempo or energy slipped, and habits to work on over time.
- **Play with confidence:** what's next, your plan's next track, and bass and blend warnings while you mix.

## Download

**[Get the latest version](https://github.com/ujjaidasani/blendbook/releases/latest)**

| | Download | Then |
|---|---|---|
| **Mac, Apple Silicon** (M1 and later) | `Blendbook-…-mac-arm64.dmg` | Open it and drag Blendbook to Applications |
| **Mac, Intel** | `Blendbook-…-mac-x86_64.dmg` | Open it and drag Blendbook to Applications |
| **Windows** 10 or 11 | `Blendbook-…-windows-setup.exe` | Run it. No admin rights needed |

**[SETUP.md](SETUP.md)** has the full guide: the app, running from source on Mac and Windows, a prompt you can paste
into an AI coding assistant to have it set Blendbook up for you, and what to do when something goes wrong.

Blendbook isn't signed with a paid Apple or Microsoft certificate yet, so the first open needs one extra step:

- **Mac:** open Blendbook once and dismiss the warning. Then go to **System Settings → Privacy & Security**, scroll down,
  and press **Open Anyway** next to Blendbook.
- **Windows:** on the "Windows protected your PC" screen, press **More info**, then **Run anyway**.

Blendbook opens in your browser. Use **Chrome or Edge**: the controller, Listen and mini view features need them.
It stays running as a small icon in the menu bar (Mac) or system tray (Windows); use that icon to reopen it or quit.

You'll need rekordbox with at least one set in its history. Everything else is optional:
an API key for free-form coaching, and `ffmpeg` for analysing recordings.

## What it does

**rekordbox first.** Tempo, key and energy come from rekordbox's own analysis wherever it has them, with no internet
needed. Related dance genres count as one sound (Deep House, Tech House, Dance), and energy is read from rekordbox's
waveforms and compared across your own library. Anything found online is an estimate and is marked as one (≈8A).

**Beatport.** Tracks you stream or buy from Beatport work like the rest of your rekordbox library, and every track you
find has a link to it on Beatport. Blendbook doesn't connect to Beatport itself: its API is limited to approved partners.

**Live**
- Follows your set as rekordbox logs it: what's playing, roughly how much is left, and the tempo path so far.
  rekordbox adds a track to history about a minute after it starts; if you want suggestions sooner, pick the track by hand.
- **Play next**: ranks your library by tempo fit (half/double time included), genre, key when rekordbox has it, and,
  most usefully, *what you've played after this track before*. Pick "Bring it down / Hold / Lift it" to steer energy.
- **Mixer view** (Chrome/Edge): listens to your controller over MIDI at the same time as rekordbox, flags both basses
  being up, times each blend, and keeps a mix log. The DDJ-FLX4 and DDJ-400 work right away. For anything else, press
  **Teach Blendbook this controller**, move each fader and EQ when asked, and you're done in about a minute.
  `midi-lab.html` lets you check the mapping, or simulate a controller without one.
- **Following your plan**: when the playing track is in a saved plan, "Next in your plan" shows the plan's next
  track (skipping any you've already played) above the usual suggestions.
- **Slot clock**: set "Set ends at" and Blendbook counts down to it, tells you how many more tracks fit at this set's
  pace, and warns you when it's time for your closer.
- **Pop-out mini view**: a small always-on-top window to keep beside rekordbox. It keeps updating while rekordbox
  covers the main window, and shows the slot clock and your plan's next track.
- **Ask mid-set** by typing or voice; answers can be read aloud (Settings). Works with no API key: ask what's next, to
  lift it or bring it down, about keys, tempo or time left, how the set's going, the last blend, how to get out of this
  track, or your plan.

**Mix log**: while the FLX4 is connected, every blend it sees (length, how long both basses were up, EQ moves) is
saved in Blendbook's data folder and attached to the matching rekordbox set, so it shows up on the Sessions page.
Matching assumes rekordbox logs a track about 60 s after it starts; change it with `BLENDBOOK_LOG_LAG_SEC` if your
timing differs.

**Recordings**: press REC in rekordbox, or drop any mix recording into `Blendbook Recordings` in your Music folder, and
Blendbook analyses it within a minute: loudness, bass/mid/high balance, silences, and whether the beats drifted in each
blend. The set's page then shows a "How it sounded" lane and a Listen button on every transition. Blendbook keeps only
numbers, not a copy of the audio. Needs `ffmpeg` (Mac: `brew install ffmpeg`, Windows: `winget install ffmpeg`). Files
named with a date and time (`2026-10-16 19-59-47.wav`) line up most reliably.

**Listen** (Live page): measures your set's loudness and bass, mid and high balance from a mic, a line-in or your
computer's sound, so streamed sets get a "How it sounded" lane too. Only the numbers are kept, never the audio. rekordbox
doesn't allow recording streamed tracks, so Blendbook doesn't record them either.

**Sessions**: every history playlist, with tempo journey, time on deck per track, tempo jumps, quick cuts,
tracks played to the very end, genre switches, first-time plays, and plain-language takeaways tied to specific tracks.
**Copy tracklist** gives you a timestamped list (`00:04:55 Artist – Title`) to paste under a mix upload.

**Goals**: pick habits to work on (gaps between tracks, tempo jumps, bass clash time…) on the Insights page. Every
set is checked against them, with a trend chart and a "since last week" summary with one thing to drill.

**Plan**: build a set from an opening track and an energy shape (warm-up, build, peak, journey, cool-down). It chains
transitions you've actually played before. Lock tracks, swap any one, save, and **Send to rekordbox**: this creates a
playlist inside a "Blendbook" folder in your rekordbox library. It only works while rekordbox is closed, backs up
your library to Blendbook's data folder first, and never touches other playlists.

**Find new music** (Plan page): **Dig around this set** finds artists like the ones in your plan with MusicBrainz and
ListenBrainz, skips artists MusicBrainz's voters place away from the club and household names far better known than your plan's artists, pulls their songs and your plan artists' own
from Apple's catalog, keeps the ones that match your plan's sound and tempo, with at most three tracks from any one
artist, and looks up estimated tempo, key and energy from ReccoBeats. Apple allows about 20 lookups a minute,
so a new dig takes a minute or two; repeat digs are much faster. ListenBrainz leans towards popular artists and knows
less about small ones, so those digs lean on Apple's search instead. Each track has a 30-second preview, provided
courtesy of iTunes and streamed, never saved, plus Apple, Spotify and Beatport links and **Add**, which slots it in
where the plan's tempo fits and locks it there. There's also a free-text catalog search. Online tracks stay in the
plan and are marked "not in your library yet"; once you add one in rekordbox's Apple Music or Spotify browser,
Blendbook uses your library copy (including when you send the plan to rekordbox).

**Keys** (Plan page): harmonic picks and clash warnings use rekordbox's keys only. Streamed tracks often have no key
there at first, so run **Analyze Track** on them in rekordbox (it takes a while: about 2 Apple Music tracks a minute).
**Fill in keys** can look tracks up online as a hint, shown as ≈8A, but those never count as a match: on 111 tracks with
both, online keys matched rekordbox only 28% of the time.

**Phone view**: Settings → "Allow phone on this Wi-Fi" shows an address and a 6-digit code. Your phone gets a
read-only screen with now playing, what's next and mixer warnings. Off by default, off again after a restart.

**Insights**: habits across all sets: signature transitions, go-to openers, most-played tracks and artists,
favourites you've stopped playing, and the part of your library you've never played out.

## AI coach

Works without a key: every number and takeaway is computed on your computer from your data, and the built-in coach
answers the common mid-set questions. Add your own API key in **Settings** to ask anything.
You pay your AI provider directly for what you use. The key is stored only on your computer, and when you ask a question,
that question and the relevant set data go to your AI provider.

## Your data and privacy

There's no account, no analytics and no tracking. Blendbook never uploads your library.

| Where | What's there |
|---|---|
| Mac: `~/Library/Application Support/Blendbook`, Windows: `%APPDATA%\Blendbook` | Mix log, plans, goals, settings, a working copy of your rekordbox library, backups |
| `Blendbook Recordings` in your Music folder | Any mix recordings you drop in |

Data leaves your computer only when you use a feature that needs it:

- **Finding music and Fill in keys** send track titles and artist names (nothing else) to Apple, MusicBrainz,
  ListenBrainz and ReccoBeats. Playing a preview streams it from Apple to your browser.
- **The AI coach** (only with your key) sends your question and set data to your AI provider.
- **Check for new versions** is off until you turn it on in Settings. Then Blendbook asks GitHub for the latest version once a day, and sends nothing about you or your library.
- **Copy diagnostics** in Settings only copies text to your clipboard, so nothing is sent unless you paste it somewhere. It has your setup and recent log lines, with your home folder hidden. Have a read before sending it.

## Security

Blendbook only listens on `127.0.0.1`, but web pages open in the same browser can still try to talk to it. So it:

- answers only requests addressed to `localhost` / `127.0.0.1` (blocks DNS-rebinding sites from reading your data),
- accepts changes (POST) only as JSON from its own pages (blocks other sites from, say, writing a playlist into
  rekordbox or swapping your API key),
- sends a strict Content-Security-Policy and refuses to be framed,
- keeps its data folder readable by your user only.

Answers from online catalogs are treated as untrusted: only expected https links are kept, and previews may only load
from Apple's preview server.

The phone view is plain HTTP on your Wi-Fi: anyone on the same network could read what it shows (now playing, next
tracks, mixer warnings). It can't change anything, and it's off until you turn it on.

## Limits worth knowing

- Blendbook reads a **copy** of rekordbox's `master.db`. The only time it writes to rekordbox is **Send to rekordbox**, which
  needs rekordbox closed, backs up your library first, and only touches its own "Blendbook" playlist folder.
- rekordbox logs a track about a minute after it starts, so Live lags by that much unless you pick the track by hand.
- Time on deck is measured between history entries, so it's an estimate.
- Streamed tracks often have no key in rekordbox until you analyse them there, and Spotify tracks have to be removed
  and reloaded instead. Tracks without a key can still be planned around, with less to go on.
- On Windows, a MIDI controller can often only be opened by one app at a time, so the mixer view may not connect while
  rekordbox is using the controller. Everything else works. Windows MIDI Services, in recent Windows 11 builds, lifts this.
- A rekordbox update can change its library format. If Blendbook stops reading it, check for a new version.

## Trouble?

- **"rekordbox not found"**: open rekordbox once. If your library is somewhere unusual, go to **Settings → Choose folder…** and pick the folder holding `master.db`.
- **Controller doesn't show up**: use Chrome or Edge, and allow MIDI access when asked.
- **Recordings aren't analysed**: install `ffmpeg`, then restart Blendbook.
- **Blendbook stopped reading rekordbox after an update**: your library is fine, as Blendbook only reads a copy of it. Check for a newer Blendbook.
- **Something else**: press **Settings → Copy diagnostics** and paste it into [a new issue](https://github.com/ujjaidasani/blendbook/issues).

## Support Blendbook

Blendbook is free and open source. If it helps your sets, you can chip in from **Settings → Support Blendbook**.

## Run from source

Needs Python 3.12 or newer.

```bash
git clone https://github.com/ujjaidasani/blendbook.git
cd blendbook
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

On a Mac you can double-click `Start Blendbook.command` instead. Windows commands, an AI setup prompt and
troubleshooting are in [SETUP.md](SETUP.md).

- **Tests:** `.venv/bin/python -m unittest discover -s tests`
- **Build the Mac app:** `pip install -r requirements-build.txt`, then `./packaging/build_mac.sh`
- **Release:** push a tag like `v1.0.0`. GitHub Actions tests and builds the Mac and Windows apps, starts each one as a check, and creates a draft release.

## Files

- `app.py`: starts Blendbook (port, browser, tray icon); `paths.py`: where everything lives on each platform
- `server.py`: local web server and JSON API
- `rb.py`: rekordbox reader and all the analytics (sessions, insights, suggestions)
- `coach_local.py`: the built-in coach
- `rb_analysis.py`: energy from rekordbox's analysis files
- `updates.py`, `diagnostics.py`: the optional version check, and the report behind Copy diagnostics
- `db.py`: Blendbook's own database (mix log, goals, set plans, recordings), with migrations
- `mixlog.py`: matches controller blends to rekordbox transitions
- `goals.py`: habit metrics, goal trends and the weekly digest
- `planner.py`: set builder and the guarded rekordbox playlist export
- `audio.py`, `recordings.py`: recording analysis, and matching recordings to sets
- `phone.py`: the opt-in, read-only phone server
- `security.py`: request guards and security headers shared by both servers
- `online.py`: online catalogs (Apple for songs and previews, MusicBrainz and ListenBrainz for similar artists,
  ReccoBeats for tempo / key / energy)
- `packaging/`: app builds; `tests/`: unit tests
- `web/`: the app (`js/live.js`, `js/sessions.js`, `js/insights.js`, `js/charts.js`, `js/midi.js`, `js/learn.js`, `js/listen.js`)

## License

[MIT](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md).

Blendbook is an independent project, not affiliated with or endorsed by AlphaTheta, Pioneer DJ, Apple, Spotify, Beatport, MetaBrainz or Anthropic.
rekordbox and DDJ are trademarks of their owners.
