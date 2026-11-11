import { h, clear, api, fmtDate, fmtTime, fmtMinutes, fmtDuration, fmtBpm, fmtBpmRange, stat, flagChip, emptyState } from "./ui.js";
import { coachBox, chartsReady } from "./coach.js";
import { toast } from "./app.js";

// "00:00 Artist – Title" per line, the format mix uploads (SoundCloud, Mixcloud, YouTube) expect. Every track is
// logged about the same time after it starts, so offsets from the first one line up with the recording.
export function tracklistText(d) {
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = (sec) => {
    const hrs = Math.floor(sec / 3600);
    const mm = Math.floor((sec % 3600) / 60);
    return hrs ? `${hrs}:${pad(mm)}:${pad(sec % 60)}` : `${pad(mm)}:${pad(sec % 60)}`;
  };
  const long = d.tracks.at(-1).offset_sec >= 3600;
  return d.tracks.map((t) => `${long && t.offset_sec < 3600 ? "0:" : ""}${stamp(t.offset_sec)} ${t.artist} – ${t.title}`).join("\n");
}

function audioLine(a, onPlay) {
  const bits = [];
  if (a.beat_drift === "drift") bits.push(h("span", { class: "chip chip-bad" }, "Beats drifted"));
  if (a.silence_sec) bits.push(h("span", { class: "chip chip-bad" }, `${a.silence_sec}s silence`));
  if (a.loudness_step_db != null && Math.abs(a.loudness_step_db) >= 3)
    bits.push(h("span", { class: "chip chip-warn" }, `${a.loudness_step_db > 0 ? "+" : ""}${a.loudness_step_db} dB`));
  return h("div", { class: "chips", style: { marginTop: "6px", alignItems: "center" } },
    h("span", { class: "chip chip-accent" }, "Audio"),
    bits.length ? bits : h("span", { class: "small muted" }, a.beat_drift === "steady" ? "beats steady, level even" : "level even"),
    a.at_rec_sec != null && h("button", { class: "btn btn-sm btn-quiet", type: "button",
      onclick: (e) => { e.stopPropagation(); onPlay(Math.max(0, a.at_rec_sec - 8)); } }, "▶ Listen"));
}

function controllerLine(c) {
  const bass = c.bass_overlap_sec ?? 0;
  const parts = [`${Math.round(c.duration_sec)}s blend`, bass >= 1 ? `basses up ${Math.round(bass)}s` : "clean low end", `${c.eq_moves ?? 0} EQ move${c.eq_moves === 1 ? "" : "s"}`];
  return h("div", { class: "small", style: { marginTop: "6px", color: bass >= 5 ? "var(--bad)" : "var(--fg-2)" },
    title: c.verdict || "" },
    h("span", { class: "chip chip-accent", style: { marginRight: "6px" } }, "FLX4"),
    parts.join(" · "), c.confidence === "low" ? " (approx. match)" : "");
}

export function mountSessions(root, params) {
  let sessions = [];
  let selectedId = params[0] || null;
  let detail = null;
  let selectedN = null;
  const charts = [];

  const listEl = h("nav", { class: "card session-list", "aria-label": "Sessions" }, h("div", { class: "skeleton" }));
  const detailEl = h("div", { class: "stack" }, h("div", { class: "card" }, h("div", { class: "skeleton" })));

  root.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Sessions"), h("p", {}, "Every set from your rekordbox history, read track by track."))),
    h("div", { class: "split" }, listEl, detailEl));

  async function loadList() {
    try {
      ({ sessions } = await api("/api/sessions"));
    } catch (e) {
      clear(listEl, emptyState("Couldn't load sessions", e.message));
      return;
    }
    if (!sessions.length) {
      clear(listEl, emptyState("No sets yet", "Play a set in rekordbox and it will show up here."));
      clear(detailEl);
      return;
    }
    if (!selectedId || !sessions.some((s) => s.id === selectedId)) selectedId = sessions[0].id;
    renderList();
    loadDetail(selectedId);
  }

  function renderList() {
    const byMonth = new Map();
    for (const s of sessions) {
      const key = fmtDate(s.started_at, { month: "long", year: "numeric" });
      if (!byMonth.has(key)) byMonth.set(key, []);
      byMonth.get(key).push(s);
    }
    clear(listEl, [...byMonth].map(([month, items]) => [
      h("h3", { style: { padding: "12px 12px 4px" } }, month),
      items.map((s) => {
        const st = s.stats;
        return h("button", {
          class: "session-item", "aria-current": s.id === selectedId ? "true" : "false",
          onclick: () => { location.hash = `#sessions/${s.id}`; },
        },
          h("div", { class: "row" },
            h("span", { class: "date" }, fmtDate(s.started_at),
              s.has_controller && h("span", { class: "chip chip-accent", style: { marginLeft: "8px" }, title: "Has FLX4 mix data" }, "FLX4")),
            h("span", { class: "meta" }, fmtTime(s.started_at))),
          h("div", { class: "meta" },
            `${st.track_count} track${st.track_count === 1 ? "" : "s"} · ${fmtMinutes(st.duration_sec)} · ${fmtBpmRange(st.bpm_min, st.bpm_max)} BPM`));
      }),
    ]));
  }

  let goalsCache = null;
  const goalsReady = api("/api/goals").then((g) => { goalsCache = g.goals; }).catch(() => { goalsCache = []; });

  function goalLine(d) {
    const goal = goalsCache?.find((g) => g.focus);
    if (!goal) return null;
    const pt = goal.points.find((p) => p.session_id === d.id);
    if (!pt || pt.value == null) return null;
    const ok = goal.direction === "at_most" ? pt.value <= goal.target : pt.value >= goal.target;
    const fmt = (v) => (goal.unit === "%" ? `${v}%` : goal.unit === "s" ? `${v}s` : `${v}`);
    return h("div", { class: `banner ${ok ? "" : "warn"}`, style: { marginTop: "12px" } },
      h("div", {}, h("b", {}, ok ? "Goal met. " : "Goal missed. "),
        `${goal.label}: ${fmt(pt.value)} this set (goal ${goal.direction === "at_most" ? "≤" : "≥"} ${fmt(goal.target)}).`));
  }

  async function loadDetail(id) {
    selectedN = null;
    clear(detailEl, h("div", { class: "card" }, h("div", { class: "skeleton" })));
    try {
      [detail] = await Promise.all([api(`/api/sessions/${id}`), goalsReady]);
    } catch (e) {
      clear(detailEl, h("div", { class: "card" }, emptyState("Couldn't load this set", e.message)));
      return;
    }
    renderDetail();
  }

  function select(n) {
    selectedN = selectedN === n ? null : n;
    charts.forEach((c) => c.update?.(undefined, { highlightN: selectedN }));
    detailEl.querySelectorAll("tr[data-n]").forEach((tr) => tr.classList.toggle("selected", Number(tr.dataset.n) === selectedN));
    if (selectedN != null) detailEl.querySelector(`tr[data-n="${selectedN}"]`)?.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }

  let seekAudio = null;

  function renderDetail() {
    charts.splice(0).forEach((c) => c.destroy?.());
    const d = detail;
    const st = d.stats;
    const journeyEl = h("div", { class: "chart", style: { minHeight: "220px" } });
    const barsEl = h("div", { class: "chart" });
    const genreEl = h("div", { class: "chart" });
    const energyEl = h("div", { class: "chart", style: { minHeight: "180px" } });
    const audioEl = d.audio?.audio_url ? h("audio", { controls: true, preload: "metadata", src: d.audio.audio_url, style: { width: "100%", marginTop: "12px" } }) : null;
    seekAudio = audioEl ? (sec) => { audioEl.currentTime = sec; audioEl.play().catch(() => {}); audioEl.scrollIntoView({ block: "nearest" }); } : null;

    const transRows = d.transitions.map((t) => {
      const delta = t.bpm_delta_pct;
      return h("tr", { "data-n": t.n + 1, onclick: () => select(t.n + 1) },
        h("td", { class: "num muted" }, fmtDuration(t.at_sec)),
        h("td", {},
          h("div", { class: "track-title", title: t.from.title }, t.from.title),
          h("div", { class: "track-meta", title: t.to.title }, "→ ", t.to.title),
          t.controller && controllerLine(t.controller),
          t.audio?.in_recording && audioLine(t.audio, (sec) => seekAudio?.(sec))),
        h("td", { class: "num" }, `${fmtBpm(t.from.bpm)} → ${fmtBpm(t.to.bpm)}`,
          delta != null && h("div", { class: `small ${delta > 0 ? "delta-up" : delta < 0 ? "delta-down" : "muted"}` }, `${delta > 0 ? "+" : ""}${delta}%`)),
        h("td", {}, h("div", { class: "chips" }, t.flags.filter((f) => f !== "genre_switch").map(flagChip),
          t.flags.includes("genre_switch") && h("span", { class: "chip", title: `${t.from.genre} → ${t.to.genre}` }, `${t.from.genre} → ${t.to.genre}`))));
    });

    clear(detailEl,
      h("section", { class: "card" },
        h("div", { class: "card-head" },
          h("div", {},
            h("h2", {}, fmtDate(d.started_at, { weekday: "long", month: "long", day: "numeric", year: "numeric" })),
            h("p", {}, `Started ${fmtTime(d.started_at)} · opened with “${d.tracks[0].title}”, closed with “${d.tracks.at(-1).title}”`)),
          h("button", { class: "btn btn-sm", type: "button", title: "Timestamped, ready to paste under a mix upload",
            onclick: () => (navigator.clipboard?.writeText(tracklistText(d)) || Promise.reject())
              .then(() => toast(`Copied ${d.tracks.length} track${d.tracks.length === 1 ? "" : "s"} with timestamps`), () => toast("Couldn't copy")) }, "Copy tracklist")),
        h("div", { class: "stats" },
          stat("Tracks", st.track_count, st.first_time_tracks ? `${st.first_time_tracks} first-time plays` : null),
          stat("Length", fmtMinutes(st.duration_sec), "estimated"),
          stat("Avg time per track", fmtDuration(st.avg_on_deck_sec)),
          stat("Tempo", `${fmtBpm(st.bpm_start)} → ${fmtBpm(st.bpm_end)}`, `range ${fmtBpmRange(st.bpm_min, st.bpm_max)}`),
          stat("Smooth tempo changes", st.smooth_tempo_pct == null ? "—" : `${st.smooth_tempo_pct}%`, "within 3% BPM"),
          stat("Big tempo jumps", st.bpm_jumps, "over 6%"),
          d.controller?.seen && stat("Bass clash", `${d.controller.bass_clash_sec}s`,
            `${d.controller.heavy_bass_blends} of ${d.controller.blends} blends · FLX4`),
          d.controller?.seen && stat("Typical blend", d.controller.median_blend_sec ? `${d.controller.median_blend_sec}s` : "—",
            d.controller.hard_cuts ? `${d.controller.hard_cuts} hard cuts` : "measured on FLX4")),
        goalLine(d)),

      d.observations.length && h("section", { class: "card" },
        h("div", { class: "card-head" }, h("h2", {}, "What stood out")),
        h("div", { class: "obs" }, d.observations.map((o) => h("div", { class: `obs-item ${o.kind}` },
          h("div", {},
            h("b", {}, o.title, o.source && h("span", { class: "chip chip-accent", style: { marginLeft: "8px" } },
              o.source === "controller" ? "FLX4" : "Audio")),
            h("p", {}, o.detail),
            o.at_n && h("button", { class: "btn btn-sm btn-quiet", onclick: () => select(o.at_n) }, `Show track ${o.at_n}`)))))),

      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Tempo journey"), h("p", {}, "BPM of each track in play order. Red segments are jumps over 6%."))),
        journeyEl),

      d.audio ? h("section", { class: "card" },
        h("div", { class: "card-head" },
          h("div", {}, h("h2", {}, "How it sounded"),
            h("p", {}, `${d.audio.kind === "mic" ? "Measured live through the mic" : `From ${d.audio.name}`}. Loudness on top, bass / mid / high balance below. Red dots: drift or silence. Yellow: a level jump.`
              + (d.audio.alignment === "low" && d.audio.kind === "file" ? " Lined up by the recording's start time." : "")))),
        energyEl,
        audioEl)
      : h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "How it sounded"),
          h("p", {}, "No recording for this set. Press REC in rekordbox while you play, or drop a mix recording into Blendbook's Recordings folder, and Blendbook will check every blend for drifting beats, level jumps and silence.")))),

      h("div", { class: "grid two" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Time on deck"),
            h("p", {}, "From one track being logged to the next, against the track's length."))),
          barsEl,
          h("div", { class: "chips", style: { marginTop: "12px" } },
            h("span", { class: "chip chip-accent" }, "Normal"),
            h("span", { class: "chip chip-warn" }, "Under 75s"),
            h("span", { class: "chip chip-info" }, "Played to the end"),
            h("span", { class: "chip chip-bad" }, "Possible gap"))),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("h2", {}, "Sound")),
          genreEl,
          st.keys_known === 0 && h("p", { class: "muted small", style: { marginTop: "12px" } },
            "No rekordbox keys for these tracks yet. Run Analyze Track on them in rekordbox to turn on harmonic checks."))),

      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Transitions"), h("p", {}, "Select a row to find it on the charts."))),
        h("div", { class: "table-wrap" },
          h("table", {},
            h("thead", {}, h("tr", {}, h("th", { class: "num" }, "At"), h("th", {}, "Mix"), h("th", { class: "num" }, "BPM"), h("th", {}, "Notes"))),
            h("tbody", {}, transRows))),
        d.controller?.unmatched?.length > 0 && h("details", { style: { marginTop: "12px" } },
          h("summary", { class: "muted small" }, `${d.controller.unmatched.length} blend${d.controller.unmatched.length > 1 ? "s" : ""} the FLX4 saw that couldn't be placed on a track change`),
          h("ul", { class: "list" }, d.controller.unmatched.map((b) => h("li", {},
            h("span", { class: "rank" }, fmtTime(b.started_at)),
            h("span", { style: { flex: 1 } }, b.verdict || `${Math.round(b.duration_sec)}s blend`)))))),

      coachBox({
        title: "Ask about this set",
        placeholder: "e.g. Where did the energy dip?",
        quick: ["What should I fix first?", "How was my tempo flow?", "Which transitions were riskiest?"],
        getPayload: () => ({ scope: "session", session_id: d.id }),
      }));

    chartsReady.then((c) => {
      if (!c || detail !== d) {
        if (!c) [journeyEl, barsEl, genreEl].forEach((el) => clear(el, h("p", { class: "muted small" }, "Charts unavailable.")));
        return;
      }
      charts.push(
        c.bpmJourney(journeyEl, d.tracks, { transitions: d.transitions, highlightN: selectedN, onSelect: select }),
        c.onDeckBars(barsEl, d.tracks, { highlightN: selectedN, onSelect: select }),
        c.genreBar(genreEl, st.genres, { max: 6 }));
      if (d.audio && c.energyLane) {
        api(d.audio.features_url).then((feat) => {
          if (detail !== d) return;
          const transitions = d.transitions.filter((t) => t.audio?.in_recording).map((t) => ({
            n: t.n + 1, at_sec: t.audio.at_rec_sec, label: `${t.from.title} → ${t.to.title}`, flags: t.flags, audio: t.audio,
          }));
          charts.push(c.energyLane(energyEl, feat, { transitions, highlightN: selectedN, onSelect: select,
            onSeek: (sec) => seekAudio?.(sec) }));
        }).catch(() => clear(energyEl, h("p", { class: "muted small" }, "Couldn't load the recording analysis.")));
      }
    });
  }

  loadList();
  return {
    onParams([id]) {
      if (id && id !== selectedId) {
        selectedId = id;
        renderList();
        loadDetail(id);
      }
    },
    destroy() { charts.forEach((c) => c.destroy?.()); },
  };
}
