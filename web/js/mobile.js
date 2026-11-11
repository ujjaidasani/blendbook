// Phone view for a set. Plain http on a LAN IP is not a secure context, so wake lock
// is feature-detected. Web MIDI, clipboard, and crypto.randomUUID are not used.

const TOKEN_KEY = "blendbook.phone.token";
const SLOT_KEY = "blendbook.phone.slotEnd";
const POLL_MS = 1000;
const DEMO_MS = 2000;
const LOST_AFTER = 3;
const PAIR_TIMEOUT_MS = 12000;
const POLL_TIMEOUT_MS = 8000;

const demo = new URLSearchParams(location.search).get("demo") === "1";

const $ = (id) => document.getElementById(id);
const pairView = $("pairView");
const liveView = $("liveView");
const pairForm = $("pairForm");
const codeInput = $("code");
const pairError = $("pairError");
const pairBtn = $("pairBtn");
const dot = $("dot");
const connLabel = $("connLabel");
const lostBanner = $("lostBanner");
const clashBanner = $("clashBanner");
const nowTitle = $("nowTitle");
const nowArtist = $("nowArtist");
const nowMeta = $("nowMeta");
const nowBpm = $("nowBpm");
const nowGenre = $("nowGenre");
const mixLine = $("mixLine");
const noController = $("noController");
const mixerEl = $("mixer");
const nextEmpty = $("nextEmpty");
const nextSection = $("nextSection");
const nextHeading = $("nextHeading");
const sleepNote = $("sleepNote");
const slotLine = $("slotLine");
const slotInput = $("slotEnd");
const slotClear = $("slotClear");
const planEl = $("plan");
const planCard = $("planCard");
const planName = $("planName");
const planCount = $("planCount");
const planTitle = $("planTitle");
const planMeta = $("planMeta");
const planDelta = $("planDelta");
const planDone = $("planDone");
const rows = [...document.querySelectorAll("#nextList .suggestion")].map((el) => ({
  el,
  title: el.querySelector(".track-title"),
  sub: el.querySelector(".track-meta"),
  delta: el.querySelector(".delta"),
}));

let memoryToken = "";
let wantWake = false;
let sentinel = null;
let timer = 0;
let busy = false;
let fails = 0;
let lastGood = null;
let demoIndex = 0;
let lastDemoState = null;
let shownState = null;
let memorySlot = "";

// The laptop keeps its slot end in its own browser; this phone keeps its own. Demo mode never touches storage.
const slot = {
  get end() {
    if (demo) return memorySlot;
    try {
      return localStorage.getItem(SLOT_KEY) || memorySlot;
    } catch {
      return memorySlot;
    }
  },
  set end(v) {
    memorySlot = v || "";
    if (demo) return;
    try {
      if (v) localStorage.setItem(SLOT_KEY, v);
      else localStorage.removeItem(SLOT_KEY);
    } catch {
      /* private mode or blocked storage; memorySlot still covers this tab */
    }
  },
};

// slotEndDate and slotStatus are copied from live.js: the phone server only serves this one script.
// "01:00" means the next 01:00 (a slot past midnight), but up to 2 h after it still counts so overtime shows.
function slotEndDate(hhmm, now = new Date()) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || "");
  if (!m) return null;
  const end = new Date(now);
  end.setHours(Number(m[1]), Number(m[2]), 0, 0);
  if (now - end > 2 * 3600e3) end.setDate(end.getDate() + 1);
  return end;
}

// leftSec: until the slot ends. trackLeftSec: estimated time left on the current track. paceSec: time per track.
function slotStatus(leftSec, trackLeftSec, paceSec) {
  const mins = (s) => (s < 60 ? "under a minute" : `${Math.round(s / 60)} min`);
  if (leftSec <= 0) return { tone: "bad", text: `Your slot ended ${leftSec > -60 ? "just now" : `${mins(-leftSec)} ago`}.`, more: 0 };
  const more = Math.max(0, Math.round((leftSec - (trackLeftSec || 0)) / (paceSec || 210)));
  if (more === 0) return { tone: "warn", text: `${mins(leftSec)} left. Let this one ride out, or bring your closer in now.`, more };
  if (more === 1) return { tone: "warn", text: `${mins(leftSec)} left: room for one more. Make it your closer.`, more };
  return { tone: "", text: `${mins(leftSec)} left in your slot, room for about ${more} more tracks.`, more };
}

function digits(value) {
  return String(value || "").replace(/\D/g, "").slice(0, 6);
}

function fmtBpm(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function fmtDelta(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const rounded = Math.round(n * 10) / 10;
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return `${rounded > 0 ? "+" : ""}${text}%`;
}

function pct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.round(Math.min(1, Math.max(0, n)) * 100);
}

function readToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || memoryToken || "";
  } catch {
    return memoryToken;
  }
}

function storeToken(token) {
  memoryToken = token || "";
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode or blocked storage; memoryToken still covers this tab */
  }
}

function authHeaders(extra) {
  const headers = { Accept: "application/json", ...extra };
  const token = readToken();
  if (token) headers["X-Blendbook-Token"] = token;
  return headers;
}

function setPairError(msg) {
  pairError.hidden = !msg;
  pairError.textContent = msg || "";
  codeInput.setAttribute("aria-invalid", msg ? "true" : "false");
}

function show(which) {
  pairView.hidden = which !== "pair";
  liveView.hidden = which !== "live";
  if (which === "live") {
    wantWake = true;
    acquireWake();
    liveView.focus({ preventScroll: true });
  } else {
    releaseWake();
    codeInput.focus();
  }
}

function acquireWake() {
  if (!wantWake || document.hidden) return;
  if (typeof navigator.wakeLock?.request !== "function") {
    sleepNote.hidden = false;
    return;
  }
  if (sentinel) {
    sleepNote.hidden = true;
    return;
  }
  navigator.wakeLock.request("screen").then((lock) => {
    if (!wantWake || document.hidden) {
      lock.release().catch(() => {});
      return;
    }
    sentinel = lock;
    sleepNote.hidden = true;
    lock.addEventListener("release", () => {
      if (sentinel === lock) sentinel = null;
    });
  }).catch(() => {
    sleepNote.hidden = false;
  });
}

function releaseWake() {
  wantWake = false;
  const lock = sentinel;
  sentinel = null;
  if (lock) lock.release().catch(() => {});
}

function paintMeter(meter, value, hot) {
  const p = pct(value);
  meter.classList.toggle("hot", hot);
  const fill = meter.querySelector("i");
  const bar = meter.querySelector(".bar");
  fill.style.width = `${p}%`;
  meter.querySelector("[data-val]").textContent = String(p);
  bar.setAttribute("aria-valuenow", String(p));
}

function paintDeck(root, deck, clash) {
  const air = root.querySelector(".air");
  const playing = !!deck?.playing;
  const onAir = playing && !!deck?.audible;
  air.textContent = !playing ? "Stopped" : onAir ? "On air" : "Playing";
  air.className = `air ${!playing ? "stop" : onAir ? "on" : "play"}`;
  paintMeter(root.querySelector(".meter.vol"), deck?.fader, false);
  paintMeter(root.querySelector(".meter.low"), deck?.low, !!clash);
}

function renderSlot(state) {
  const end = slotEndDate(slot.end);
  slotClear.hidden = !end;
  slotLine.hidden = !end || !state;
  if (slotLine.hidden) return;
  const np = state.now_playing;
  const since = Number(np?.since_logged_sec);
  const length = Number(np?.length);
  // rekordbox logs a track about a minute after it starts
  const trackLeft = np?.active && length > 0 && Number.isFinite(since) ? Math.max(0, length - since - 60) : null;
  const status = slotStatus((end - Date.now()) / 1000, trackLeft, Number(state.session?.track_pace_sec) || null);
  slotLine.className = status.tone ? `slot-line ${status.tone}` : "slot-line";
  slotLine.textContent = status.text;
}

function renderPlan(plan) {
  const next = plan?.next || null;
  planEl.hidden = !plan;
  planCard.hidden = !next;
  planDone.hidden = !plan || !!next;
  const name = plan?.name ? String(plan.name) : "your plan";
  if (next) {
    planName.textContent = `Next in ${name}`;
    planCount.textContent = plan.next_position && plan.length ? `${plan.next_position} of ${plan.length}` : "";
    planTitle.textContent = next.title || "Untitled";
    planMeta.textContent = [next.artist ? String(next.artist) : "", next.bpm ? `${fmtBpm(next.bpm)} BPM` : ""]
      .filter(Boolean).join(" · ") || "—";
    planDelta.textContent = fmtDelta(next.bpm_delta_pct);
  } else if (plan) {
    planDone.textContent = `Last track of ${name}. Your call from here.`;
  }
}

// With a plan to follow, the suggestions become the alternatives: two of them, so the page still fits one screen.
function renderNext(list, quietEmpty, planNext) {
  const items = (Array.isArray(list) ? list : []).slice(0, planNext ? 2 : 3);
  nextHeading.textContent = planNext ? "Or" : "Play next";
  nextSection.hidden = planNext && items.length === 0;
  rows[0].el.classList.toggle("top", !planNext);
  rows.forEach((row, i) => {
    const item = items[i];
    row.el.hidden = !item;
    if (!item) return;
    row.title.textContent = item.title || "Untitled";
    const reason = Array.isArray(item.reasons) && item.reasons[0] ? String(item.reasons[0]) : "";
    const artist = item.artist ? String(item.artist) : "";
    row.sub.textContent = [artist, reason].filter(Boolean).join(" · ") || "—";
    row.delta.textContent = fmtDelta(item.bpm_delta_pct);
  });
  nextEmpty.hidden = quietEmpty || items.length > 0;
}

function render(state, { lost }) {
  lostBanner.hidden = !lost;
  shownState = state;
  const np = state?.now_playing || null;
  const active = !!np?.active;
  const plan = active ? state.plan || null : null;
  renderSlot(state);
  renderPlan(plan);

  if (!state) {
    connLabel.textContent = "Connecting…";
    dot.className = lost ? "dot lost" : "dot idle";
    nowTitle.textContent = "Connecting…";
    nowArtist.hidden = true;
    nowArtist.textContent = "";
    nowMeta.hidden = true;
    mixLine.hidden = true;
    noController.hidden = true;
    mixerEl.hidden = true;
    clashBanner.hidden = true;
    renderNext([], true, false);
    return;
  }

  connLabel.textContent = active ? "Live" : "Last set";
  dot.className = `dot ${lost ? "lost" : active ? "ok" : "idle"}`;

  if (!np) {
    nowTitle.textContent = "No sets yet";
    nowArtist.hidden = true;
    nowArtist.textContent = "";
    nowMeta.hidden = true;
  } else {
    nowTitle.textContent = np.title || "Untitled";
    nowArtist.hidden = !np.artist;
    nowArtist.textContent = np.artist || "";
    nowMeta.hidden = false;
    nowBpm.textContent = fmtBpm(np.bpm);
    nowGenre.textContent = np.genre || "—";
  }

  const mixer = state.mixer || null;
  clashBanner.hidden = !mixer?.bassClash;
  const tr = mixer?.currentTransition;
  if (tr && tr.from != null && tr.to != null) {
    const elapsed = Math.round(Number(tr.elapsedSec) || 0);
    const overlap = Math.round(Number(tr.bassOverlapSec) || 0);
    mixLine.hidden = false;
    mixLine.textContent = `Deck ${tr.from} → ${tr.to} · ${elapsed}\u00a0s · bass overlap ${overlap}\u00a0s`;
  } else {
    mixLine.hidden = true;
    mixLine.textContent = "";
  }

  mixerEl.hidden = !mixer;
  noController.hidden = !!mixer;
  if (mixer) {
    paintDeck($("deck1"), mixer.decks?.["1"], mixer.bassClash);
    paintDeck($("deck2"), mixer.decks?.["2"], mixer.bassClash);
  }

  renderNext(state.suggestions, false, !!plan?.next);
}

function signOut() {
  storeToken("");
  fails = 0;
  lastGood = null;
  stopLoop();
  show("pair");
  setPairError("");
  codeInput.value = "";
}

function stopLoop() {
  if (timer) clearInterval(timer);
  timer = 0;
}

function startLoop() {
  stopLoop();
  const tick = demo ? demoTick : () => { pollTick(); };
  tick();
  if (document.hidden) return;
  timer = setInterval(tick, demo ? DEMO_MS : POLL_MS);
}

async function pollTick() {
  if (busy || demo) return;
  busy = true;
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), POLL_TIMEOUT_MS);
  try {
    const res = await fetch("/api/phone/state", {
      headers: authHeaders(),
      cache: "no-store",
      signal: ctrl.signal,
    });
    if (res.status === 401) {
      signOut();
      return;
    }
    const data = await res.json().catch(() => null);
    if (!res.ok || !data || typeof data !== "object") throw new Error("bad");
    fails = 0;
    lastGood = data;
    render(data, { lost: false });
  } catch {
    fails += 1;
    render(lastGood, { lost: fails >= LOST_AFTER });
  } finally {
    clearTimeout(timeout);
    busy = false;
  }
}

const NEXT = [
  {
    title: "Universal Consciousness",
    artist: "Amelie Lens",
    bpm: 137,
    bpm_delta_pct: 0.7,
    reasons: ["Same energy, played after this"],
  },
  {
    title: "Space Date",
    artist: "Paula Temple",
    bpm: 134,
    bpm_delta_pct: -1.5,
    reasons: ["Key match"],
  },
  {
    title: "Age of Love",
    artist: "Age of Love",
    bpm: 138.5,
    bpm_delta_pct: 1.8,
    reasons: ["A step up"],
  },
];

const SESSION = { track_count: 9, started_at: "2026-10-17T20:01:00", track_pace_sec: 240 };

// the server leaves the plan's next track out of the suggestions, so the demo does too
const PLAN = { name: "Friday peak", next_position: 7, length: 12, next: NEXT[0] };
const PLAN_NEXT = NEXT.slice(1);

function track(active, since) {
  return {
    title: "In the Beginning There Was Rhythm (Original Mix)",
    artist: "Charlotte de Witte",
    bpm: 136,
    genre: "Techno",
    length: 390,
    since_logged_sec: since,
    active,
  };
}

const DEMO_FRAMES = [
  {
    name: "no-controller",
    lost: false,
    state: { now_playing: track(false, 200), suggestions: NEXT, session: SESSION, mixer: null },
  },
  {
    name: "blending",
    lost: false,
    state: {
      now_playing: track(true, 110),
      plan: PLAN,
      suggestions: PLAN_NEXT,
      session: SESSION,
      mixer: {
        updated_ms_ago: 300,
        bassClash: false,
        currentTransition: { from: 1, to: 2, elapsedSec: 12.3, bassOverlapSec: 2.1, eqMoves: 3 },
        decks: {
          "1": { playing: true, audible: true, fader: 0.9, low: 0.4 },
          "2": { playing: true, audible: true, fader: 0.55, low: 0.3 },
        },
      },
    },
  },
  {
    name: "bass-clash",
    lost: false,
    state: {
      now_playing: track(true, 128),
      plan: PLAN,
      suggestions: PLAN_NEXT,
      session: SESSION,
      mixer: {
        updated_ms_ago: 120,
        bassClash: true,
        currentTransition: { from: 1, to: 2, elapsedSec: 18.4, bassOverlapSec: 6.2, eqMoves: 5 },
        decks: {
          "1": { playing: true, audible: true, fader: 0.9, low: 0.82 },
          "2": { playing: true, audible: false, fader: 0.7, low: 0.75 },
        },
      },
    },
  },
  { name: "lost", lost: true, state: null },
];

function demoTick() {
  const frame = DEMO_FRAMES[demoIndex % DEMO_FRAMES.length];
  demoIndex += 1;
  if (frame.state) lastDemoState = frame.state;
  document.documentElement.dataset.demoState = frame.name;
  render(lastDemoState, { lost: frame.lost });
}

async function submitPair(code) {
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), PAIR_TIMEOUT_MS);
  try {
    const res = await fetch("/api/pair", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ code }),
      cache: "no-store",
      signal: ctrl.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      storeToken("");
      const error = new Error(data.error || "Pair again.");
      error.status = 401;
      throw error;
    }
    if (!res.ok) {
      const error = new Error(data.error || (res.status === 403 ? "That code didn't work." : "Couldn't pair."));
      error.status = res.status;
      throw error;
    }
    if (!data.token) throw Object.assign(new Error("Couldn't pair."), { status: res.status });
    storeToken(String(data.token));
  } finally {
    clearTimeout(timeout);
  }
}

function bindPair() {
  codeInput.addEventListener("input", () => {
    const clean = digits(codeInput.value);
    if (codeInput.value !== clean) codeInput.value = clean;
    if (!pairError.hidden) setPairError("");
  });
  pairForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const code = digits(codeInput.value);
    if (code.length !== 6) {
      setPairError("Enter the 6-digit code.");
      codeInput.focus();
      return;
    }
    pairBtn.disabled = true;
    pairBtn.textContent = "Pairing…";
    submitPair(code).then(() => {
      setPairError("");
      fails = 0;
      lastGood = null;
      show("live");
      render(null, { lost: false });
      startLoop();
    }).catch((err) => {
      const offline = !err || err.name === "TypeError" || err.name === "AbortError" || !err.status;
      setPairError(offline ? "Can't reach Blendbook. Same Wi-Fi as the laptop?" : (err.message || "Couldn't pair."));
      codeInput.focus();
    }).finally(() => {
      pairBtn.disabled = false;
      pairBtn.textContent = "Pair";
    });
  });
}

function bindSlot() {
  slotInput.value = slot.end;
  slotInput.addEventListener("change", () => {
    slot.end = slotInput.value;
    renderSlot(shownState);
  });
  slotClear.addEventListener("click", (event) => {
    slot.end = "";
    slotInput.value = "";
    renderSlot(shownState);
    // Clear just hid itself: keep keyboard focus nearby (not on a tap, where iOS would open the time picker)
    if (event.detail === 0) slotInput.focus();
  });
}

function onVisibility() {
  if (document.hidden) {
    stopLoop();
    return;
  }
  if (wantWake) acquireWake();
  if (!liveView.hidden) startLoop();
}

bindPair();
document.addEventListener("visibilitychange", onVisibility);

if (demo) {
  const end = new Date(Date.now() + 47 * 60e3);
  memorySlot = `${String(end.getHours()).padStart(2, "0")}:${String(end.getMinutes()).padStart(2, "0")}`;
}
bindSlot();

if (demo) {
  show("live");
  startLoop();
} else if (readToken()) {
  show("live");
  render(null, { lost: false });
  startLoop();
} else {
  show("pair");
}
