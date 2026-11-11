import { h, clear, api, fmtBpm, fmtDuration, fmtTime, relTime, trackLine, fmtKey, emptyState, speak } from "./ui.js";
import { coachBox, chartsReady } from "./coach.js";
import { prefs, toast } from "./app.js";

const POLL_MS = 5000;
const DIRECTIONS = [["down", "Bring it down"], ["hold", "Hold energy"], ["up", "Lift it"]];

// Controller state survives tab switches so a running set isn't lost.
const controller = { mon: null, status: null, state: null, events: [], transitions: [], view: null };
// phone mode: mixer state is relayed to the server so a paired phone can show it
const phoneRelay = { on: false, lastSent: 0, timer: null };
function relayMixer(force = false) {
  if (!phoneRelay.on) return;
  const now = Date.now();
  if (!force && now - phoneRelay.lastSent < 330) return;
  phoneRelay.lastSent = now;
  fetch("/api/mixer-state", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state: controller.mon ? controller.state : null }) }).catch(() => {});
}

// live listening survives tab switches like the controller does
const SOURCE_KEY = "db_listen_source";
const stored = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const store = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
const listening = { session: null, level: null, seconds: 0, error: null, view: null, inputs: [],
  source: stored(SOURCE_KEY, "system") };

// ---------- slot clock: when your set has to end, and how many more tracks fit ----------
const SLOT_KEY = "db_slot_end";
const slot = {
  get end() { try { return localStorage.getItem(SLOT_KEY) || ""; } catch { return ""; } },
  set end(v) { try { if (v) localStorage.setItem(SLOT_KEY, v); else localStorage.removeItem(SLOT_KEY); } catch {} },
};

// "01:00" means the next 01:00 (a slot past midnight), but up to 2 h after it still counts so overtime shows.
export function slotEndDate(hhmm, now = new Date()) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || "");
  if (!m) return null;
  const end = new Date(now);
  end.setHours(Number(m[1]), Number(m[2]), 0, 0);
  if (now - end > 2 * 3600e3) end.setDate(end.getDate() + 1);
  return end;
}

// leftSec: until the slot ends. trackLeftSec: estimated time left on the current track. paceSec: time per track.
export function slotStatus(leftSec, trackLeftSec, paceSec) {
  const mins = (s) => (s < 60 ? "under a minute" : `${Math.round(s / 60)} min`);
  if (leftSec <= 0) return { tone: "bad", text: `Your slot ended ${leftSec > -60 ? "just now" : `${mins(-leftSec)} ago`}.`, more: 0 };
  const more = Math.max(0, Math.round((leftSec - (trackLeftSec || 0)) / (paceSec || 210)));
  if (more === 0) return { tone: "warn", text: `${mins(leftSec)} left. Let this one ride out, or bring your closer in now.`, more };
  if (more === 1) return { tone: "warn", text: `${mins(leftSec)} left: room for one more. Make it your closer.`, more };
  return { tone: "", text: `${mins(leftSec)} left in your slot, room for about ${more} more tracks.`, more };
}

// ---------- mix log: every controller event is kept and sent to the server in batches ----------
const FLUSH_MS = 5000;
const OUTBOX_MAX = 5000;
const mixlog = { session: null, outbox: [], sending: false, saved: 0, failed: false, timer: null };

function queueMixEvent(e) {
  mixlog.outbox.push({ ...e, uid: crypto.randomUUID(), ts: e.ts || Date.now() });
  if (mixlog.outbox.length > OUTBOX_MAX) mixlog.outbox.splice(0, mixlog.outbox.length - OUTBOX_MAX);
}

async function flushMixLog() {
  if (mixlog.sending || !mixlog.outbox.length || !mixlog.session) return;
  mixlog.sending = true;
  const batch = mixlog.outbox.slice(0, 500);
  try {
    const res = await fetch("/api/mixlog", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_session: mixlog.session, events: batch }),
    });
    if (!res.ok) throw new Error(res.status);
    mixlog.outbox.splice(0, batch.length);
    mixlog.saved += batch.length;
    mixlog.failed = false;
  } catch {
    mixlog.failed = true; // kept in the outbox; uids make the retry safe
  } finally {
    mixlog.sending = false;
    controller.view?.updateSaveStatus();
  }
}

function beaconMixLog() {
  if (!mixlog.outbox.length || !mixlog.session || !navigator.sendBeacon) return;
  const body = JSON.stringify({ client_session: mixlog.session, events: mixlog.outbox.slice(0, 500) });
  if (navigator.sendBeacon("/api/mixlog", new Blob([body], { type: "application/json" }))) mixlog.outbox.splice(0, 500);
}

addEventListener("pagehide", beaconMixLog);
addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") beaconMixLog(); });

export function mountLive(root) {
  let direction = "hold";
  let live = null;
  let manualTrack = null; // a track picked by hand, when rekordbox hasn't logged one yet
  let manualAt = 0;
  let refreshing = false;
  let timer = null;
  let pip = null;
  let spark = null;
  let lastAnnounced = null;
  let lastSuggestions = [];
  let plan = null; // where the playing track sits in a saved plan, if it's in one

  const nowEl = h("section", { class: "card now-card" }, h("div", { class: "skeleton" }));
  // The card redraws every 5 s; screen readers only hear what changed meaningfully: a new track, or the slot clock
  // turning into a warning.
  const announcer = h("p", { class: "sr-only", "aria-live": "polite" });
  let announced = { id: null, tone: null };
  const nextEl = h("div");
  const dirButtons = DIRECTIONS.map(([key, label]) => h("button", {
    type: "button", "aria-pressed": String(key === direction),
    onclick: () => {
      direction = key;
      dirButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(DIRECTIONS[i][0] === key)));
      refresh();
    },
  }, label));

  const searchInput = h("input", { type: "search", placeholder: "Search your library…", "aria-label": "Pick the track that's playing" });
  const searchResults = h("ul", { class: "list" });
  let searchTimer = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      const q = searchInput.value.trim();
      if (!q) return clear(searchResults);
      const { tracks } = await api(`/api/search?q=${encodeURIComponent(q)}`);
      clear(searchResults, tracks.slice(0, 6).map((t) => h("li", {},
        trackLine(t),
        h("button", { class: "btn btn-sm", onclick: () => { manualTrack = t; manualAt = Date.now(); searchInput.value = ""; clear(searchResults); refresh(); } }, "Playing"))));
    }, 180);
  });

  const controllerEl = h("section", { class: "card" });
  const listenEl = h("section", { class: "card" });
  const pipBtn = h("button", { class: "btn", type: "button", onclick: openMini }, "Pop-out mini view");
  const slotInput = h("input", { type: "time", value: slot.end, "aria-label": "When your set has to end" });
  const slotClear = h("button", { class: "btn btn-sm btn-quiet", type: "button", hidden: !slot.end, "aria-label": "Clear the set end time",
    onclick: () => { slotInput.value = ""; setSlot(""); } }, "Clear");
  slotInput.addEventListener("change", () => setSlot(slotInput.value));
  function setSlot(v) {
    slot.end = v;
    slotClear.hidden = !v;
    if (live) { renderNow(); updateMini(lastSuggestions); }
  }

  root.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Live"),
        h("p", {}, "Follows your set as rekordbox logs it, suggests what to play next from your own library and habits, and watches your mixer moves on the controller.")),
      h("div", { class: "head-actions" },
        h("label", { class: "slot-field", title: "Blendbook counts down to it and tells you how many more tracks fit" },
          h("span", {}, "Set ends at"), slotInput, slotClear),
        pipBtn)),
    announcer,
    h("div", { class: "grid live-grid" },
      h("div", { class: "stack" },
        nowEl,
        h("section", { class: "card" },
          h("div", { class: "card-head" },
            h("div", {}, h("h2", {}, "Play next"), h("p", {}, "Ranked by tempo fit, genre, and what you've played after this before.")),
            h("div", { class: "segmented", role: "group", "aria-label": "Energy direction" }, dirButtons)),
          nextEl)),
      h("div", { class: "stack" },
        controllerEl,
        listenEl,
        coachBox({
          title: "Ask mid-set",
          placeholder: "e.g. What's a safe next track?",
          quick: ["What should I play next?", "Lift it", "Bring it down", "What key works here?", "How do I get out of this track?", "How's my set going?"],
          getPayload: () => ({ scope: "live", controller: controllerSummary() }),
        }),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Not logged yet?"),
            h("p", {}, "rekordbox adds a track to history about a minute after it starts. Pick it here to get suggestions right away."))),
          searchInput, searchResults))));

  async function refresh() {
    if (refreshing) return; // a slow answer must not be overwritten by, or overwrite, a newer one
    refreshing = true;
    try {
      await syncLive();
    } finally {
      refreshing = false;
    }
  }

  async function syncLive() {
    try {
      live = await api(`/api/live?direction=${direction}`);
    } catch (e) {
      clear(nowEl, emptyState("Can't reach Blendbook's server", e.message));
      return;
    }
    // Anything rekordbox logs after a hand pick replaces it, however long this page was out of sight.
    if (manualTrack && live.active && new Date(live.now_playing.logged_at).getTime() > manualAt) manualTrack = null;
    let suggestions = live.suggestions || [];
    plan = live.plan || null;
    if (manualTrack) {
      ({ suggestions, plan } = await api(`/api/suggest?track_id=${encodeURIComponent(manualTrack.id)}&direction=${direction}`));
    }
    phoneRelay.on = !!live.phone_enabled;
    clearInterval(phoneRelay.timer);
    if (phoneRelay.on) phoneRelay.timer = setInterval(() => relayMixer(true), 2000); // heartbeat while idle
    renderNow();
    renderNext(suggestions, plan);
    lastSuggestions = suggestions;
    updateMini(suggestions);
    const pill = document.getElementById("statusPill");
    if (live.active) { pill.className = "status-pill live"; pill.textContent = "Live set"; }

    const id = live.now_playing?.id;
    if (live.active && id && lastAnnounced && id !== lastAnnounced && prefs.speak && (plan?.next || suggestions[0])) {
      speak(plan?.next ? `Now playing ${live.now_playing.title}. Next in your plan: ${plan.next.title}.`
        : `Now playing ${live.now_playing.title}. Good next: ${suggestions[0].title}.`);
    }
    lastAnnounced = id;
  }

  function renderNow() {
    const np = manualTrack || live.now_playing;
    if (!np) {
      clear(nowEl, emptyState("No sets yet", "Start playing in rekordbox; Blendbook picks it up automatically."));
      return;
    }
    const sinceSec = manualTrack ? null : live.now_playing.since_logged_sec;
    const elapsedOnTrack = sinceSec != null ? sinceSec + 60 : null; // rekordbox logs ~1 min in
    const pct = np.length && elapsedOnTrack != null ? Math.min(100, (100 * elapsedOnTrack) / np.length) : null;
    const remaining = np.length && elapsedOnTrack != null ? Math.max(0, np.length - elapsedOnTrack) : null;
    const isLive = live.active || manualTrack;
    const slotNow = slotLine(live.active && !manualTrack ? remaining : null);
    if (np.id !== announced.id || (slotNow?.tone || null) !== announced.tone) {
      const parts = [];
      if (np.id !== announced.id && announced.id !== null) parts.push(`${manualTrack ? "Picked" : "Now playing"}: ${np.title} by ${np.artist}.`);
      if (slotNow?.tone && slotNow.tone !== announced.tone) parts.push(slotNow.text);
      if (parts.length) announcer.textContent = parts.join(" ");
      announced = { id: np.id, tone: slotNow?.tone || null };
    }

    const sparkEl = h("div", { class: "chart", style: { marginTop: "16px" } });
    clear(nowEl,
      h("div", { class: "now-eyebrow" },
        h("span", { class: `chip ${isLive ? "chip-bad" : ""}` }, manualTrack ? "Picked by you" : live.active ? "Now playing" : "Last played"),
        !live.active && !manualTrack && h("span", {}, `${relTime(sinceSec)} · rekordbox isn't logging a set right now`)),
      h("div", { class: "now-title" }, np.title),
      h("div", { class: "now-artist" }, np.artist),
      h("div", { class: "now-facts" },
        h("div", {}, h("b", {}, fmtBpm(np.bpm)), h("span", {}, "BPM")),
        np.key && h("div", { title: np.key_source === "online" ? "Found online: a rough estimate that doesn't count for harmonic picks. Analyze the track in rekordbox for a real key." : "From rekordbox's analysis" },
          h("b", {}, fmtKey(np)), h("span", {}, "Key")),
        h("div", {}, h("b", {}, np.genre || "—"), h("span", {}, "Genre")),
        h("div", {}, h("b", {}, fmtDuration(np.length)), h("span", {}, "Length")),
        live.active && !manualTrack && remaining != null && h("div", {}, h("b", {}, `~${fmtDuration(remaining)}`), h("span", {}, "Left (estimate)")),
        live.session && live.active && h("div", {}, h("b", {}, live.session.track_count), h("span", {}, `tracks since ${fmtTime(live.session.started_at)}`))),
      live.active && !manualTrack && pct != null && h("div", { class: "progress", role: "progressbar", "aria-label": "Estimated position in track", "aria-valuenow": Math.round(pct), "aria-valuemin": 0, "aria-valuemax": 100 }, h("i", { style: { width: `${pct}%` } })),
      slotNow && h("div", { class: `banner slot-banner ${slotNow.tone}` },
        h("div", {}, h("b", {}, `Ends ${fmtTime(slotNow.end.toISOString())}. `), slotNow.text)),
      live.active && live.bpm_path?.length > 1 && sparkEl,
      live.active && live.last_transition && h("p", { class: "muted small", style: { marginTop: "12px" } },
        `Last mix: ${live.last_transition.from.title} → ${live.last_transition.to.title}`,
        live.last_transition.bpm_delta_pct != null ? ` (${live.last_transition.bpm_delta_pct > 0 ? "+" : ""}${live.last_transition.bpm_delta_pct}% tempo)` : ""));

    if (live.active && live.bpm_path?.length > 1) {
      chartsReady.then((c) => { if (c) { spark?.destroy?.(); spark = c.sparkline(sparkEl, live.bpm_path, { color: "var(--accent)" }); } });
    }
  }

  // Countdown to the end of the slot, with how many more tracks fit at this set's pace. null when no end is set.
  function slotLine(trackLeftSec) {
    const end = slotEndDate(slot.end);
    if (!end) return null;
    return { end, ...slotStatus((end - Date.now()) / 1000, trackLeftSec, live?.session?.track_pace_sec) };
  }

  function renderNext(suggestions, plan) {
    const fromPlan = plan?.next ? h("div", { class: "plan-next" },
      h("div", { class: "suggestion top" },
        h("div", { class: "track-line" },
          h("div", { class: "now-eyebrow" }, `Next in your plan · ${plan.name}`),
          h("div", { class: "track-title", style: { marginTop: "4px" } }, plan.next.title),
          h("div", { class: "track-meta" }, [plan.next.artist, plan.next.bpm ? `${fmtBpm(plan.next.bpm)} BPM` : null, fmtKey(plan.next), plan.next.genre].filter(Boolean).join(" · "))),
        plan.next.bpm_delta_pct != null && h("div", { class: "delta" }, `${plan.next.bpm_delta_pct > 0 ? "+" : ""}${plan.next.bpm_delta_pct}%`),
        h("div", { class: "chips" },
          h("span", { class: "chip chip-accent" }, `track ${plan.next_position} of ${plan.length}`),
          plan.then.length > 0 && h("span", { class: "chip" }, `then ${plan.then.map((t) => t.title).join(", ")}`))),
      h("h3", { class: "list-label" }, "Other good fits"))
      : plan ? h("p", { class: "muted small", style: { marginBottom: "12px" } }, `That's the last track of “${plan.name}”. From here it's your call:`) : null;
    if (plan?.next) suggestions = suggestions.filter((s) => s.id !== plan.next.id);
    if (!suggestions.length) {
      clear(nextEl, fromPlan, emptyState("No close matches", "Nothing in your library is within 8% of this tempo. Try a different energy direction."));
      return;
    }
    clear(nextEl, fromPlan, suggestions.slice(0, 8).map((s, i) => h("div", { class: `suggestion${i === 0 && !plan?.next ? " top" : ""}` },
      trackLine(s),
      h("div", { class: "delta" }, `${s.bpm_delta_pct > 0 ? "+" : ""}${s.bpm_delta_pct}%`),
      h("div", { class: "chips" }, s.reasons.map((r, j) => h("span", { class: `chip ${r.startsWith("key clash") ? "chip-warn" : j === 0 && r.startsWith("you've") ? "chip-accent" : ""}` }, r))))));
  }

  // ---------- FLX4 controller ----------

  // Built once per connection; updateMixer() then patches values in place so buttons stay clickable at 30 Hz.
  let ui = null;

  function renderController() {
    ui = null;
    if (!controller.mon) {
      clear(controllerEl,
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Controller"),
          h("p", {}, "Reads your faders, EQs and crossfader alongside rekordbox (Chrome or Edge). The DDJ-FLX4 and DDJ-400 work right away; any other MIDI controller after a one-minute setup. Nothing is sent back to the controller."))),
        h("button", { class: "btn btn-primary", onclick: connectController }, "Connect controller"),
        h("a", { class: "btn btn-quiet", href: "midi-lab.html", target: "_blank", rel: "noopener", style: { marginLeft: "8px" } }, "Test without hardware"));
      return;
    }
    const meter = (label, cls = "") => {
      const fill = h("i");
      const val = h("span", {}, "—");
      return { el: h("div", { class: `meter ${cls}` }, h("span", {}, label), h("div", { class: "bar" }, fill), val), fill, val };
    };
    const strip = (n) => {
      const m = { vol: meter("Vol", "vol"), hi: meter("Hi"), mid: meter("Mid"), low: meter("Low", "low") };
      const playBtn = h("button", {
        class: "btn btn-sm", type: "button", "aria-pressed": "false",
        title: "Blendbook guesses play state from the PLAY button. Tap to correct it.",
        onclick: () => controller.mon?.setPlaying(n, !controller.state?.decks?.[n]?.playing),
      }, "Stopped");
      return { m, playBtn, el: h("div", { class: `strip d${n}` }, h("h4", {}, h("b", {}, `Deck ${n}`), playBtn), m.vol.el, m.hi.el, m.mid.el, m.low.el) };
    };
    const s1 = strip(1), s2 = strip(2);
    const xfMark = h("i", { style: { width: "4px", background: "var(--fg)" } });
    ui = {
      statusText: h("p", {}),
      mapBox: h("div", { style: { marginBottom: "12px" } }),
      clash: h("div", { class: "banner bad", role: "alert", hidden: true, style: { marginBottom: "12px" } },
        h("div", {}, h("b", {}, "Both basses are up. "), "Pull one LOW EQ down so the kicks don't fight.")),
      transTitle: h("b"),
      transSub: h("div", { class: "muted small" }),
      log: h("ul", { class: "list event-log" }),
      logWrap: h("div", { style: { marginTop: "12px" }, hidden: true }),
      saveStatus: h("p", { class: "muted small", style: { marginTop: "12px" }, role: "status" }),
      strips: { 1: s1, 2: s2 },
      xfMark,
    };
    ui.trans = h("div", { class: "banner", hidden: true, style: { marginBottom: "12px" } }, h("div", {}, ui.transTitle, ui.transSub));
    ui.logWrap.append(h("h3", { style: { marginBottom: "4px" } }, "Mix log"), ui.log);
    clear(controllerEl,
      h("div", { class: "card-head" },
        h("div", {}, h("h2", {}, controller.status?.deviceName || "Controller"), ui.statusText),
        h("button", { class: "btn btn-sm btn-quiet", onclick: disconnectController }, "Disconnect")),
      ui.mapBox, ui.clash, ui.trans,
      h("div", { class: "mixer" }, s1.el, s2.el,
        h("div", { class: "strip xfader" },
          h("div", { class: "meter" }, h("span", {}, "1"), h("div", { class: "bar" }, xfMark), h("span", {}, "2")))),
      ui.logWrap, ui.saveStatus);
    updateStatus();
    updateSaveStatus();
    updateMixer();
    updateLog();
  }

  function updateStatus() {
    if (!ui) return;
    const st = controller.status;
    ui.statusText.textContent = st?.connected ? `Connected: ${st.deviceName}. Tap a deck's state if it's wrong.`
      : st?.error || "Waiting for the controller… plug it in or check it's on.";
    const inputs = controller.mon?.listInputs() || [];
    const teach = () => import("./learn.js").then(({ learnController }) =>
      learnController(controller.mon, st.deviceName, (n) => n && toast(`Saved: Blendbook now knows your ${st.deviceName}.`)));
    const forget = () => import("./learn.js").then(({ forgetController }) => {
      forgetController(controller.mon, st.deviceName);
      toast("Forgot the learned controls.");
    });
    const picker = inputs.length > 1 && h("label", { class: "field", style: { marginTop: "8px" } }, h("span", {}, "MIDI input"),
      h("select", { onchange: (e) => controller.mon.useInput(e.target.value) },
        inputs.map((p) => h("option", { value: p.id, selected: p.name === st?.deviceName }, p.name))));
    clear(ui.mapBox,
      st?.connected && !st.mapSource && h("div", { class: "banner warn" }, h("div", {},
        h("b", {}, `Blendbook doesn't know the ${st.deviceName} yet. `),
        "Show it your faders and EQs once (about a minute) and the mixer view, blend timer and bass warning work with it.",
        h("div", { style: { marginTop: "8px" } }, h("button", { class: "btn btn-sm btn-primary", type: "button", onclick: teach }, "Teach Blendbook this controller")))),
      st?.connected && st.mapSource === "learned" && h("p", { class: "muted small" }, "Using the controls you taught Blendbook. ",
        h("button", { class: "btn btn-sm btn-quiet", type: "button", onclick: teach }, "Teach again"),
        h("button", { class: "btn btn-sm btn-quiet", type: "button", onclick: forget }, "Forget")),
      st?.connected && st.mapSource === "builtin" && h("p", { class: "muted small" }, "Using the built-in DDJ-FLX4 / DDJ-400 map. A control reads wrong? ",
        h("button", { class: "btn btn-sm btn-quiet", type: "button", onclick: teach }, "Teach it yourself")),
      picker);
  }

  function updateMixer() {
    if (!ui) return;
    const st = controller.state;
    for (const n of [1, 2]) {
      const d = st?.decks?.[n];
      const { m, playBtn } = ui.strips[n];
      for (const [key, v] of [["vol", d?.fader], ["hi", d?.eq?.hi], ["mid", d?.eq?.mid], ["low", d?.eq?.low]]) {
        m[key].fill.style.width = `${Math.round((v ?? 0) * 100)}%`;
        m[key].val.textContent = v == null ? "—" : Math.round(v * 100);
      }
      m.low.el.classList.toggle("hot", !!st?.bassClash);
      const label = d?.playing ? (d.audible ? "On air" : "Playing") : "Stopped";
      if (playBtn.textContent !== label) playBtn.textContent = label;
      playBtn.setAttribute("aria-pressed", String(!!d?.playing));
    }
    ui.xfMark.style.marginLeft = `calc(${Math.round((st?.crossfader ?? 0.5) * 100)}% - 2px)`;
    ui.clash.hidden = !st?.bassClash;
    const t = st?.currentTransition;
    ui.trans.hidden = !t;
    if (t) {
      ui.transTitle.textContent = `Mixing deck ${t.from} → deck ${t.to}: ${Math.round(t.elapsedSec)}s`;
      ui.transSub.textContent = `Bass overlap ${Math.round(t.bassOverlapSec)}s · ${t.eqMoves} EQ moves`;
    }
  }

  function updateSaveStatus() {
    if (!ui) return;
    ui.saveStatus.textContent = mixlog.failed
      ? `Couldn't save the mix log (${mixlog.outbox.length} events waiting). Retrying every few seconds.`
      : mixlog.saved
        ? "Mix log saved. It will show on this set in Sessions once rekordbox logs the tracks."
        : "Your blends are saved with this set as you play.";
  }

  function updateLog() {
    if (!ui) return;
    ui.logWrap.hidden = controller.events.length === 0;
    clear(ui.log, controller.events.slice(0, 12).map((e) => h("li", {},
      h("time", {}, new Date(e.ts).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" }).replace(/\s?[AP]M$/i, "")),
      h("span", {}, e.text))));
  }

  let mixerQueued = false;
  function queueMixer() {
    if (mixerQueued) return;
    mixerQueued = true;
    // A timer, not requestAnimationFrame: rAF stops while rekordbox covers this window. With the mini view open,
    // use its timer: it's the visible window, so the browser doesn't slow it down.
    (pip || window).setTimeout(() => { mixerQueued = false; updateMixer(); updateMini(lastSuggestions); }, 50);
  }

  async function connectController() {
    if (!navigator.requestMIDIAccess) {
      toast("This browser can't read MIDI. Open Blendbook in Chrome or Edge.");
      return;
    }
    let mod;
    try {
      mod = await import("./midi.js");
    } catch (e) {
      toast("Controller module failed to load.");
      return;
    }
    controller.mon = mod.createFlx4Monitor({
      onState: (s) => { controller.state = s; controller.view?.queueMixer(); relayMixer(); },
      onStatus: (s) => { controller.status = s; controller.view?.updateStatus(); },
      onEvent: (e) => {
        queueMixEvent(e);
        const text = describeEvent(e);
        if (!text) return;
        if (e.type === "transition_end") controller.transitions.push(e);
        controller.events.unshift({ ts: e.ts || Date.now(), text });
        controller.events.length = Math.min(controller.events.length, 60);
        if (e.type === "bass_clash" && prefs.speak) speak("Both basses up.");
        controller.view?.updateLog();
      },
    });
    mixlog.session = crypto.randomUUID();
    clearInterval(mixlog.timer);
    mixlog.timer = setInterval(flushMixLog, FLUSH_MS);
    renderController();
    try {
      await controller.mon.start();
    } catch (e) {
      controller.status = { connected: false, error: e.message };
      updateStatus();
    }
  }

  function disconnectController() {
    flushMixLog();
    clearInterval(mixlog.timer);
    controller.mon?.stop();
    Object.assign(controller, { mon: null, status: null, state: null });
    relayMixer(true);
    renderController();
  }

  // ---------- mini window (always-on-top next to rekordbox) ----------

  async function openMini() {
    if (!("documentPictureInPicture" in window)) {
      toast("The mini view needs Chrome or Edge 116+.");
      return;
    }
    try {
      pip = await window.documentPictureInPicture.requestWindow({ width: 340, height: 300 });
    } catch (e) {
      toast(`Couldn't open the mini view (${e.message}). Try again from a normal Chrome window.`);
      return;
    }
    for (const href of ["css/tokens.css", "css/app.css"]) {
      pip.document.head.append(Object.assign(pip.document.createElement("link"), { rel: "stylesheet", href: new URL(href, location.href).href }));
    }
    pip.document.body.style.padding = "12px";
    // When rekordbox covers this window the browser treats it as hidden and stops the 5 s refresh below, which is
    // exactly when the mini view matters. So the mini view, which stays visible, drives the refresh then.
    pip.setInterval(refresh, POLL_MS); // its timer isn't slowed down while the main page is hidden
    pip.addEventListener("pagehide", () => { pip = null; });
    refresh();
  }

  function updateMini(suggestions) {
    if (!pip) return;
    const np = manualTrack || live?.now_playing;
    const st = controller.state;
    const sinceSec = manualTrack ? null : live?.now_playing?.since_logged_sec;
    const trackLeft = live?.active && np?.length && sinceSec != null ? Math.max(0, np.length - sinceSec - 60) : null;
    const sl = slotLine(trackLeft);
    const delta = (s) => (s.bpm_delta_pct == null ? "" : `${s.bpm_delta_pct > 0 ? "+" : ""}${s.bpm_delta_pct}%`);
    pip.document.body.replaceChildren(
      st?.bassClash ? h("div", { class: "banner bad", style: { marginBottom: "8px" } }, h("b", {}, "Both basses up")) : "",
      sl ? h("div", { class: `banner ${sl.tone}`, style: { marginBottom: "8px", padding: "8px 12px" } }, h("div", { class: "small" }, sl.text)) : "",
      np ? h("div", {},
        h("div", { class: "now-eyebrow" }, live?.active ? "Now playing" : "Last played"),
        h("div", { class: "track-title", style: { fontSize: "1.05rem", marginTop: "4px" } }, np.title),
        h("div", { class: "track-meta" }, `${np.artist} · ${fmtBpm(np.bpm)} BPM`)) : "",
      plan?.next ? h("div", { class: "suggestion top", style: { marginTop: "12px" } },
        h("div", { class: "track-line" }, h("div", { class: "now-eyebrow" }, `Plan · ${plan.next_position} of ${plan.length}`),
          h("div", { class: "track-title" }, plan.next.title), h("div", { class: "track-meta" }, plan.next.artist)),
        h("div", { class: "delta" }, delta(plan.next))) : "",
      h("h3", { style: { margin: "14px 0 4px" } }, plan?.next ? "Or" : "Play next"),
      h("ol", { class: "list" }, suggestions.filter((s) => s.id !== plan?.next?.id).slice(0, plan?.next ? 3 : 4).map((s) => h("li", {}, trackLine(s), h("span", { class: "right" }, delta(s))))));
  }

  function controllerSummary() {
    if (!controller.state) return null;
    return {
      bassClash: controller.state.bassClash,
      currentTransition: controller.state.currentTransition,
      recentTransitions: controller.transitions.slice(-5),
    };
  }

  let listenUi = null;
  function renderListen() {
    const on = !!listening.session;
    listenUi = { text: h("p", {}), fill: h("i", { style: { background: "var(--accent)" } }), val: h("span", {}, "—"),
      err: h("p", { class: "small", style: { color: "var(--bad)", marginTop: "8px" }, role: "alert" }) };
    const sourceSel = h("select", { "aria-label": "Sound from", disabled: on },
      h("option", { value: "system" }, "This computer's sound"),
      h("option", { value: "" }, "Default microphone"),
      ...listening.inputs.map((d) => h("option", { value: d.id }, d.label)),
      !listening.inputs.length && h("option", { value: "unlock" }, "Other input (BlackHole, VB-CABLE, line-in)…"));
    sourceSel.value = listening.inputs.some((d) => d.id === listening.source) || ["system", ""].includes(listening.source) ? listening.source : "system";
    sourceSel.addEventListener("change", async () => {
      if (sourceSel.value === "unlock") return unlockSources();
      listening.source = sourceSel.value; store(SOURCE_KEY, sourceSel.value); updateListen();
    });
    clear(listenEl,
      h("div", { class: "card-head" },
        h("div", {}, h("h2", {}, "Listen"), listenUi.text),
        h("button", { class: `btn ${on ? "" : "btn-primary"}`, onclick: toggleListen }, on ? "Stop listening" : "Start listening")),
      h("label", { class: "field" }, h("span", {}, "Sound from"), sourceSel),
      on && h("div", { class: "meter vol" }, h("span", {}, "Level"), h("div", { class: "bar" }, listenUi.fill), listenUi.val),
      listenUi.err);
    updateListen();
  }

  function updateListen() {
    if (!listenUi) return;
    const s = listening.seconds;
    const clock = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    listenUi.text.textContent = !listening.session
      ? (listening.source === "system"
        ? "Measures your set's loudness and bass, mid and high balance. Only those numbers are kept, never the audio. This source hears your computer's own output, so it can drop out if rekordbox plays through your controller: a mic or line-in works too."
        : "Measures your set's loudness and bass, mid and high balance from this input. Only those numbers are kept, never the audio.")
      : `Listening for ${clock}. Only levels are saved, never audio.`;
    const lvl = listening.level;
    listenUi.fill.style.width = `${Math.round(Math.max(0, ((lvl ?? -60) + 60) / 60) * 100)}%`;
    listenUi.val.textContent = lvl == null ? "—" : `${Math.round(lvl)} dB`;
    listenUi.err.textContent = listening.error
      || (listening.session && listening.silentFor >= 10
        ? "No sound has arrived for over 10 seconds. If music is playing, this source isn't hearing it."
        : "");
  }

  // Chrome only lists inputs after mic access is allowed; ask, then pick a loopback input if there is one
  async function unlockSources() {
    listening.error = null;
    try {
      const { unlockInputs } = await import("./listen.js");
      listening.inputs = await unlockInputs();
      const loop = listening.inputs.find((d) => /BlackHole|Loopback|CABLE Output|VB-Audio/i.test(d.label));
      listening.source = loop ? loop.id : listening.source;
      store(SOURCE_KEY, listening.source);
    } catch (e) {
      listening.error = "Microphone access was blocked, so Chrome won't list inputs. Allow it in Chrome's address bar.";
    }
    renderListen();
  }

  async function stopListening() {
    const session = listening.session;
    if (!session) return;
    Object.assign(listening, { session: null, level: null, seconds: 0 });
    if (listening.view) renderListen();
    await session.stop();
  }

  async function toggleListen() {
    if (listening.session) return stopListening();
    listening.error = null;
    try {
      const { startListening, audioInputs } = await import("./listen.js");
      listening.session = await startListening({
        source: listening.source,
        onLevel: ({ db, seconds, silentFor }) => {
          Object.assign(listening, { level: db, seconds, silentFor });
          listening.view?.();
        },
        onError: (msg) => { listening.error = msg; listening.view?.(); },
        onEnded: () => { listening.error = "The sound source stopped (sharing ended or the device was unplugged)."; stopListening(); },
      });
      listening.inputs = await audioInputs().catch(() => listening.inputs);
    } catch (e) {
      listening.error = e.name === "NotAllowedError"
        ? (listening.source === "system" ? "Screen sharing was cancelled or blocked." : "Microphone access was blocked. Allow it in Chrome's address bar.")
        : e.message;
    }
    renderListen();
  }

  listening.view = updateListen;
  renderListen();
  import("./listen.js").then(({ audioInputs }) => audioInputs()).then((inputs) => {
    listening.inputs = inputs;
    if (listening.view && !listening.session) renderListen();
  }).catch(() => {});
  controller.view = { queueMixer, updateStatus, updateLog, updateSaveStatus };
  renderController();
  refresh();
  // Keeps going behind other windows: rekordbox covering Chrome counts as hidden, and spoken updates still matter then.
  timer = setInterval(refresh, POLL_MS);
  const onVisible = () => { if (!document.hidden) refresh(); }; // browsers slow timers down in hidden pages
  document.addEventListener("visibilitychange", onVisible);
  return {
    destroy() {
      document.removeEventListener("visibilitychange", onVisible);
      controller.view = null;
      listening.view = null;
      clearInterval(timer);
      spark?.destroy?.();
      pip?.close();
    },
  };
}

function describeEvent(e) {
  switch (e.type) {
    case "transition_start": return `Started mixing deck ${e.from} → deck ${e.to}`;
    case "transition_end": return e.verdict || `Mix done in ${Math.round(e.durationSec)}s`;
    case "bass_clash": return "Both LOW EQs up while both decks were on air";
    case "warning": return e.message;
    default: return null;
  }
}
