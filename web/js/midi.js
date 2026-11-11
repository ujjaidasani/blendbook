/**
 * Live controller listener.
 *
 * CoreMIDI lets rekordbox own the controller while the browser reads the same
 * port. This module turns that stream into mixer state and a few mix analytics.
 *
 * The built-in map matches the DDJ-FLX4 / DDJ-400 layout. Any other controller
 * works once Blendbook has learned its faders and EQs (learn.js); learned maps
 * are kept per device name in this browser.
 */

/** @typedef {"cc14" | "cc7" | "note"} MapKind */

/**
 * Editable control table. 14-bit rows use MSB then LSB (LSB = MSB + 0x20).
 * `invert` is the tempo fader: physical up sends a high value and means slower.
 *
 * VERIFY with the lab page. Move one hardware control at a time and check the label.
 */
export const FLX4_MAP = [
  { id: "d1.fader", label: "Deck 1 channel fader", deck: 1, param: "fader", kind: "cc14", status: 0xB0, msb: 0x13, lsb: 0x33 },
  { id: "d2.fader", label: "Deck 2 channel fader", deck: 2, param: "fader", kind: "cc14", status: 0xB1, msb: 0x13, lsb: 0x33 },
  { id: "d1.trim", label: "Deck 1 trim", deck: 1, param: "trim", kind: "cc14", status: 0xB0, msb: 0x04, lsb: 0x24 },
  { id: "d2.trim", label: "Deck 2 trim", deck: 2, param: "trim", kind: "cc14", status: 0xB1, msb: 0x04, lsb: 0x24 },
  { id: "d1.hi", label: "Deck 1 EQ HI", deck: 1, param: "eq", band: "hi", kind: "cc14", status: 0xB0, msb: 0x07, lsb: 0x27 },
  { id: "d2.hi", label: "Deck 2 EQ HI", deck: 2, param: "eq", band: "hi", kind: "cc14", status: 0xB1, msb: 0x07, lsb: 0x27 },
  { id: "d1.mid", label: "Deck 1 EQ MID", deck: 1, param: "eq", band: "mid", kind: "cc14", status: 0xB0, msb: 0x0B, lsb: 0x2B },
  { id: "d2.mid", label: "Deck 2 EQ MID", deck: 2, param: "eq", band: "mid", kind: "cc14", status: 0xB1, msb: 0x0B, lsb: 0x2B },
  { id: "d1.low", label: "Deck 1 EQ LOW", deck: 1, param: "eq", band: "low", kind: "cc14", status: 0xB0, msb: 0x0F, lsb: 0x2F },
  { id: "d2.low", label: "Deck 2 EQ LOW", deck: 2, param: "eq", band: "low", kind: "cc14", status: 0xB1, msb: 0x0F, lsb: 0x2F },
  { id: "d1.tempo", label: "Deck 1 tempo", deck: 1, param: "tempo", kind: "cc14", status: 0xB0, msb: 0x00, lsb: 0x20, invert: true },
  { id: "d2.tempo", label: "Deck 2 tempo", deck: 2, param: "tempo", kind: "cc14", status: 0xB1, msb: 0x00, lsb: 0x20, invert: true },
  { id: "d1.cfx", label: "Deck 1 CFX", deck: 1, param: "cfx", kind: "cc14", status: 0xB6, msb: 0x17, lsb: 0x37 },
  { id: "d2.cfx", label: "Deck 2 CFX", deck: 2, param: "cfx", kind: "cc14", status: 0xB6, msb: 0x18, lsb: 0x38 },
  { id: "crossfader", label: "Crossfader", deck: null, param: "crossfader", kind: "cc14", status: 0xB6, msb: 0x1F, lsb: 0x3F },
  { id: "d1.play", label: "Deck 1 play", deck: 1, param: "play", kind: "note", status: 0x90, note: 0x0B },
  { id: "d2.play", label: "Deck 2 play", deck: 2, param: "play", kind: "note", status: 0x91, note: 0x0B },
  { id: "d1.cue", label: "Deck 1 cue", deck: 1, param: "cue", kind: "note", status: 0x90, note: 0x0C },
  { id: "d2.cue", label: "Deck 2 cue", deck: 2, param: "cue", kind: "note", status: 0x91, note: 0x0C },
  { id: "d1.jog", label: "Deck 1 jog touch", deck: 1, param: "jog", kind: "note", status: 0x90, note: 0x36 },
  { id: "d2.jog", label: "Deck 2 jog touch", deck: 2, param: "jog", kind: "note", status: 0x91, note: 0x36 },
];

const ccIndex = new Map();
const noteIndex = new Map();

/** Make `map` the one every monitor reads (there's only ever one controller connected). */
export function useMap(map) {
  ccIndex.clear();
  noteIndex.clear();
  for (const entry of map || []) {
    if (entry.kind === "cc14") {
      ccIndex.set(`${entry.status}:${entry.msb}`, { entry, part: "msb" });
      ccIndex.set(`${entry.status}:${entry.lsb}`, { entry, part: "lsb" });
    } else if (entry.kind === "cc7") {
      ccIndex.set(`${entry.status}:${entry.cc}`, { entry, part: "cc7" });
    } else {
      noteIndex.set(`${entry.status}:${entry.note}`, entry);
    }
  }
}
useMap(FLX4_MAP);

const MAPS_KEY = "db_midi_maps";
const BUILTIN = /FLX4|DDJ-400/i;

/** Learned maps, by MIDI device name. */
export function savedMaps() {
  try { return JSON.parse(localStorage.getItem(MAPS_KEY) || "{}") || {}; } catch { return {}; }
}

export function saveMap(deviceName, map) {
  const all = savedMaps();
  if (map) all[deviceName] = map; else delete all[deviceName];
  try { localStorage.setItem(MAPS_KEY, JSON.stringify(all)); } catch {}
}

/** The map for a device: one learned for it, else the built-in one for FLX4 / DDJ-400, else none. */
export function mapFor(deviceName) {
  const learned = savedMaps()[deviceName];
  if (Array.isArray(learned) && learned.length) return { map: learned, source: "learned" };
  if (BUILTIN.test(deviceName || "")) return { map: FLX4_MAP, source: "builtin" };
  return { map: null, source: null };
}

const STATE_INTERVAL_MS = 1000 / 30;
const BASS_HOLD_MS = 1500;
const BASS_LOW = 0.65;
const AUDIBLE_MIN = 0.08;
const SLAM_MS = 250;
const NO_EQ_MS = 16000;
const HARD_CUT_SEC = 4;
const PAIR_MS = 20;
const BASS_OVERLAP_WARN_SEC = 3;

function clamp01(n) {
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

function round3(n) {
  return Math.round(n * 1000) / 1000;
}

function defaultDeck() {
  return {
    playing: false,
    fader: 0,
    trim: 0.5,
    eq: { hi: 0.5, mid: 0.5, low: 0.5 },
    cfx: 0.5,
    tempo: 0.5,
    audible: false,
    jogTouched: false,
  };
}

/** Linear crossfader. 0 is full deck 1, 1 is full deck 2. */
function crossfaderGain(deck, xf) {
  const x = clamp01(xf);
  return deck === 1 ? 1 - x : x;
}

function messageType(status, data2) {
  const high = status & 0xF0;
  if (high === 0xB0) return "cc";
  if (high === 0x80) return "noteoff";
  if (high === 0x90) return data2 > 0 ? "noteon" : "noteoff";
  return "other";
}

function labelFor(status, data1) {
  const cc = ccIndex.get(`${status}:${data1}`);
  if (cc) return cc.entry.label;
  const note = noteIndex.get(`${status}:${data1}`);
  if (note) return note.label;
  return "unmapped";
}

/**
 * @param {{
 *   onState?: (state: object) => void,
 *   onEvent?: (evt: object) => void,
 *   onStatus?: (status: object) => void,
 *   onRaw?: (msg: object) => void,
 * }} [handlers]
 */
export function createFlx4Monitor(handlers = {}) {
  const onState = handlers.onState || function () {};
  const onEvent = handlers.onEvent || function () {};
  const onStatus = handlers.onStatus || function () {};
  const onRaw = handlers.onRaw || function () {};

  const decks = { 1: defaultDeck(), 2: defaultDeck() };
  let mapSource = "builtin";
  let rawTap = null;
  let crossfader = 0.5;
  let bothAudible = false;
  let bassClash = false;
  let lastActivityTs = null;

  /** @type {null | {
   *   startedAt: number, from: 1|2, to: 1|2, elapsedSec: number,
   *   bassOverlapSec: number, eqMoves: number, lastTick: number,
   *   lowAtStart: Record<number, number>, warnedNoEq: boolean
   * }} */
  let transition = null;

  const ccPair = new Map();
  const faderHist = { 1: [], 2: [] };
  const slamLatched = { 1: false, 2: false };
  const eqGestureAt = {};

  let bassTimer = null;
  let bassSince = null;
  let uiTimer = null;
  let stateTimer = null;
  let lastStateEmit = 0;
  let dirty = false;

  let access = null;
  let bound = null;
  let userLocked = false;
  let supported = false;
  let error = null;
  const queued = [];

  function snapshot() {
    return {
      decks: {
        1: cloneDeck(decks[1]),
        2: cloneDeck(decks[2]),
      },
      crossfader,
      bothAudible,
      bassClash,
      currentTransition: publicTransition(),
      lastActivityTs,
    };
  }

  function cloneDeck(d) {
    return {
      playing: d.playing,
      fader: d.fader,
      trim: d.trim,
      eq: { hi: d.eq.hi, mid: d.eq.mid, low: d.eq.low },
      cfx: d.cfx,
      tempo: d.tempo,
      audible: d.audible,
      jogTouched: d.jogTouched,
    };
  }

  function publicTransition() {
    if (!transition) return null;
    return {
      startedAt: transition.startedAt,
      from: transition.from,
      to: transition.to,
      elapsedSec: round3(transition.elapsedSec),
      bassOverlapSec: round3(transition.bassOverlapSec),
      eqMoves: transition.eqMoves,
    };
  }

  function emitStatus() {
    onStatus({
      supported,
      connected: !!(bound && bound.state === "connected"),
      deviceName: bound ? (bound.name || "MIDI input") : null,
      mapSource,
      error,
    });
  }

  function queue(evt) {
    queued.push(evt);
  }

  function flushEvents() {
    while (queued.length) onEvent(queued.shift());
  }

  function scheduleState() {
    dirty = true;
    const now = performance.now();
    const elapsed = now - lastStateEmit;
    if (elapsed >= STATE_INTERVAL_MS) {
      flushState();
      return;
    }
    if (stateTimer) return;
    stateTimer = setTimeout(flushState, STATE_INTERVAL_MS - elapsed);
  }

  function flushState() {
    if (stateTimer) {
      clearTimeout(stateTimer);
      stateTimer = null;
    }
    if (!dirty) return;
    dirty = false;
    lastStateEmit = performance.now();
    onState(snapshot());
  }

  function isAudible(deck) {
    const d = decks[deck];
    return d.playing && d.fader > AUDIBLE_MIN && crossfaderGain(deck, crossfader) > AUDIBLE_MIN;
  }

  function bassHot() {
    return bothAudible && decks[1].eq.low > BASS_LOW && decks[2].eq.low > BASS_LOW;
  }

  function accumulate(now) {
    if (!transition) return;
    const dt = (now - transition.lastTick) / 1000;
    transition.lastTick = now;
    transition.elapsedSec = (now - transition.startedAt) / 1000;
    if (dt > 0 && dt < 2 && decks[1].eq.low > BASS_LOW && decks[2].eq.low > BASS_LOW) {
      transition.bassOverlapSec += dt;
    }
    if (!transition.warnedNoEq && transition.elapsedSec > NO_EQ_MS / 1000 && transition.eqMoves === 0) {
      transition.warnedNoEq = true;
      queue({
        type: "warning",
        code: "no_eq_on_blend",
        message: "This blend is past 16 seconds with no EQ moves.",
        ts: now,
      });
    }
  }

  function ensureUiTimer() {
    if (uiTimer) return;
    uiTimer = setInterval(() => {
      if (!transition) {
        clearInterval(uiTimer);
        uiTimer = null;
        return;
      }
      const now = Date.now();
      accumulate(now);
      flushEvents();
      scheduleState();
    }, 100);
  }

  function stopUiTimer() {
    if (uiTimer) {
      clearInterval(uiTimer);
      uiTimer = null;
    }
  }

  function startTransition(from, to, now) {
    transition = {
      startedAt: now,
      from,
      to,
      elapsedSec: 0,
      bassOverlapSec: 0,
      eqMoves: 0,
      lastTick: now,
      lowAtStart: { 1: decks[1].eq.low, 2: decks[2].eq.low },
      warnedNoEq: false,
    };
    queue({ type: "transition_start", from, to, ts: now });
    ensureUiTimer();
  }

  function verdictFor(tr) {
    const dur = Math.max(0, Math.round(tr.elapsedSec));
    const bass = Math.max(0, Math.round(tr.bassOverlapSec));
    if (tr.elapsedSec < HARD_CUT_SEC) return `Hard cut (${dur}s)`;
    if (tr.bassOverlapSec >= BASS_OVERLAP_WARN_SEC) {
      return `Both basses up for ${bass}s — swap the LOWs`;
    }
    const fromLow = decks[tr.from].eq.low;
    const toLow = decks[tr.to].eq.low;
    const dropped = fromLow < tr.lowAtStart[tr.from] - 0.15 && fromLow < 0.5;
    if (tr.eqMoves > 0 && dropped && toLow > 0.6) return `Clean ${dur}s blend, bass swapped`;
    if (tr.eqMoves === 0) return `${dur}s blend with no EQ moves`;
    return `${dur}s blend`;
  }

  function endTransition(now) {
    const tr = transition;
    transition = null;
    stopUiTimer();
    queue({
      type: "transition_end",
      from: tr.from,
      to: tr.to,
      ts: now,
      durationSec: round3(tr.elapsedSec),
      bassOverlapSec: round3(tr.bassOverlapSec),
      eqMoves: tr.eqMoves,
      verdict: verdictFor(tr),
    });
  }

  function syncBass(now) {
    if (!bassHot()) {
      if (bassTimer) {
        clearTimeout(bassTimer);
        bassTimer = null;
      }
      bassSince = null;
      if (bassClash) {
        bassClash = false;
        dirty = true;
      }
      return;
    }
    if (bassSince == null) bassSince = now;
    if (bassClash || bassTimer) return;
    const remain = BASS_HOLD_MS - (now - bassSince);
    bassTimer = setTimeout(() => {
      bassTimer = null;
      const t = Date.now();
      if (!bassHot() || bassClash) return;
      bassClash = true;
      const durationSec = round3((t - bassSince) / 1000);
      queue({ type: "bass_clash", ts: t, durationSec });
      queue({
        type: "warning",
        code: "both_lows_up",
        message: "Both LOW EQs are up while both decks are audible.",
        ts: t,
      });
      flushEvents();
      scheduleState();
    }, Math.max(0, remain));
  }

  function noteEq(deck, band, prev, next, now) {
    if (!transition) return;
    if (Math.abs(next - prev) < 0.0005) return;
    const key = `${deck}.${band}`;
    const last = eqGestureAt[key] || 0;
    if (now - last > 200) transition.eqMoves += 1;
    eqGestureAt[key] = now;
  }

  function noteFader(deck, prev, next, now) {
    const hist = faderHist[deck];
    if (next <= 0.05) slamLatched[deck] = false;
    const ramped = hist.some((sample) => sample.v <= 0.02 && now - sample.t <= SLAM_MS);
    const jumped = prev <= 0.02 && next >= 0.98;
    if (transition && !slamLatched[deck] && next >= 0.98 && (jumped || ramped)) {
      slamLatched[deck] = true;
      queue({
        type: "warning",
        code: "fader_slam",
        message: `Deck ${deck} channel fader went from 0 to 1 in under 250 ms.`,
        ts: now,
      });
    }
    hist.push({ t: now, v: prev });
    const cutoff = now - 300;
    while (hist.length && hist[0].t < cutoff) hist.shift();
  }

  function applyNormalized(entry, norm, pending) {
    if (entry.param === "crossfader") {
      crossfader = norm;
      return;
    }
    const deck = decks[entry.deck];
    if (entry.param === "fader") {
      pending.push({ kind: "fader", deck: entry.deck, prev: deck.fader, next: norm });
      deck.fader = norm;
    } else if (entry.param === "trim") {
      deck.trim = norm;
    } else if (entry.param === "cfx") {
      deck.cfx = norm;
    } else if (entry.param === "tempo") {
      deck.tempo = norm;
    } else if (entry.param === "eq") {
      pending.push({ kind: "eq", deck: entry.deck, band: entry.band, prev: deck.eq[entry.band], next: norm });
      deck.eq[entry.band] = norm;
    }
  }

  // 14-bit pairs arrive back to back, but the byte order isn't guaranteed. Pair a byte with its
  // partner when they land within PAIR_MS of each other, in either order; never mix a fresh byte
  // with a stale one, which makes fast fader moves jitter across thresholds.
  function applyCc(entry, part, value, pending) {
    const now = Date.now();
    let slot = ccPair.get(entry.id);
    if (!slot) {
      slot = { msb: null, msbTs: 0, lsb: null, lsbTs: 0 };
      ccPair.set(entry.id, slot);
    }
    let norm;
    if (part === "msb") {
      slot.msb = value;
      if (slot.lsbTs && now - slot.lsbTs <= PAIR_MS) {
        norm = ((value << 7) | slot.lsb) / 16383; // LSB came first
        slot.lsbTs = 0;
        slot.msbTs = 0;
      } else {
        norm = value / 127; // coarse now, refined if the LSB follows
        slot.msbTs = now;
      }
    } else if (part === "cc7") {
      norm = value / 127;
    } else {
      if (slot.msbTs && now - slot.msbTs <= PAIR_MS) {
        norm = ((slot.msb << 7) | value) / 16383; // MSB came first
        slot.msbTs = 0;
      } else {
        slot.lsb = value; // wait for its MSB
        slot.lsbTs = now;
        return;
      }
    }
    if (entry.invert) norm = 1 - norm;
    applyNormalized(entry, clamp01(norm), pending);
  }

  function applyMessage(status, data1, data2, now, pending) {
    const type = messageType(status, data2);
    if (type === "cc") {
      const hit = ccIndex.get(`${status}:${data1}`);
      if (hit) applyCc(hit.entry, hit.part, data2, pending);
      return;
    }
    const note = noteIndex.get(`${status}:${data1}`);
    if (!note) return;
    if (note.param === "play") {
      if (type === "noteon") {
        const next = !decks[note.deck].playing;
        decks[note.deck].playing = next;
        queue({ type: next ? "play" : "pause", deck: note.deck, ts: now });
      }
      return;
    }
    if (note.param === "jog") {
      decks[note.deck].jogTouched = type === "noteon" && data2 > 0;
    }
  }

  function recompute(now, pending) {
    const prev1 = decks[1].audible;
    const prev2 = decks[2].audible;
    decks[1].audible = isAudible(1);
    decks[2].audible = isAudible(2);
    const nowBoth = decks[1].audible && decks[2].audible;
    bothAudible = nowBoth;

    if (transition) accumulate(now);

    if (!transition && nowBoth && ((prev1 && !prev2) || (prev2 && !prev1))) {
      const from = prev1 ? 1 : 2;
      startTransition(from, from === 1 ? 2 : 1, now);
    }

    for (const diff of pending) {
      if (diff.kind === "eq") noteEq(diff.deck, diff.band, diff.prev, diff.next, now);
      else noteFader(diff.deck, diff.prev, diff.next, now);
    }

    if (transition && !nowBoth) endTransition(now);
    syncBass(now);
  }

  function commit(now, pending) {
    recompute(now, pending);
    flushEvents();
    scheduleState();
  }

  function feed(status, data1, data2) {
    const now = Date.now();
    const st = status & 0xFF;
    const d1 = data1 & 0x7F;
    const d2 = (data2 == null ? 0 : data2) & 0x7F;
    lastActivityTs = now;
    if (rawTap) rawTap({ status: st, data1: d1, data2: d2, type: messageType(st, d2), ts: now });
    onRaw({
      status: st,
      data1: d1,
      data2: d2,
      channel: st & 0x0F,
      type: messageType(st, d2),
      ts: now,
      label: labelFor(st, d1),
    });
    const pending = [];
    applyMessage(st, d1, d2, now, pending);
    commit(now, pending);
  }

  function setPlaying(deck, playing) {
    if (deck !== 1 && deck !== 2) return;
    const next = !!playing;
    if (decks[deck].playing === next) return;
    const now = Date.now();
    decks[deck].playing = next;
    lastActivityTs = now;
    queue({ type: next ? "play" : "pause", deck, ts: now });
    commit(now, []);
  }

  function clearAnalytics() {
    if (bassTimer) {
      clearTimeout(bassTimer);
      bassTimer = null;
    }
    if (stateTimer) {
      clearTimeout(stateTimer);
      stateTimer = null;
    }
    bassSince = null;
    transition = null;
    stopUiTimer();
    ccPair.clear();
    faderHist[1] = [];
    faderHist[2] = [];
    slamLatched[1] = false;
    slamLatched[2] = false;
    for (const key of Object.keys(eqGestureAt)) delete eqGestureAt[key];
    decks[1] = defaultDeck();
    decks[2] = defaultDeck();
    crossfader = 0.5;
    bothAudible = false;
    bassClash = false;
    lastActivityTs = null;
    dirty = false;
  }

  function reset() {
    clearAnalytics();
    scheduleState();
  }

  function unbind() {
    if (bound) bound.onmidimessage = null;
    bound = null;
  }

  function bind(id) {
    if (!access) {
      error = "MIDI access isn't open yet.";
      emitStatus();
      return;
    }
    const port = access.inputs.get(id);
    if (!port) {
      error = "That MIDI input is not available.";
      emitStatus();
      return;
    }
    if (bound && bound !== port) bound.onmidimessage = null;
    bound = port;
    applyMapFor(port.name || "MIDI input");
    bound.onmidimessage = (ev) => {
      const data = ev.data;
      if (!data || data.length < 2) return;
      feed(data[0], data[1], data.length > 2 ? data[2] : 0);
    };
    error = null;
    emitStatus();
  }

  function applyMapFor(name) {
    const { map, source } = mapFor(name);
    useMap(map);
    mapSource = source;
    reset();
  }

  // A controller Blendbook knows (built in or learned) first, then anything that looks like a DJ controller
  function pickAuto() {
    if (!access) return;
    if (userLocked && bound && bound.state === "connected") return;
    if (bound && bound.state === "connected" && mapSource) return;
    const ports = [...access.inputs.values()];
    const match = ports.find((port) => mapFor(port.name).source)
      || ports.find((port) => /DDJ|DJ|Traktor|Kontrol|Mixtrack|Inpulse|Hercules|Numark|Denon|Rane|Reloop|Roland/i.test(port.name || ""));
    if (match) bind(match.id);
  }

  async function start() {
    const nav = typeof navigator === "undefined" ? null : navigator;
    if (!nav || typeof nav.requestMIDIAccess !== "function") {
      supported = false;
      error = "Web MIDI isn't available in this browser. The simulator still works.";
      emitStatus();
      return;
    }
    try {
      if (!access) {
        access = await nav.requestMIDIAccess({ sysex: false });
      }
      supported = true;
      error = null;
      access.onstatechange = () => {
        if (bound && bound.state !== "connected") {
          unbind();
          userLocked = false;
        }
        pickAuto();
        emitStatus();
      };
      pickAuto();
      emitStatus();
    } catch (err) {
      supported = true;
      error = (err && err.message) || "MIDI access was denied.";
      emitStatus();
    }
  }

  function stop() {
    unbind();
    userLocked = false;
    if (access) access.onstatechange = null;
    error = null;
    emitStatus();
  }

  function listInputs() {
    if (!access) return [];
    return [...access.inputs.values()].map((port) => ({
      id: port.id,
      name: port.name || "MIDI input",
    }));
  }

  function useInput(id) {
    userLocked = true;
    bind(id);
  }

  /** Learn mode: see every raw message (as {status, data1, data2, type, ts}), or pass null to stop. */
  function tapRaw(fn) {
    rawTap = fn;
  }

  /** Switch to a newly learned (or cleared) map for the connected device. */
  function remap() {
    if (bound) applyMapFor(bound.name || "MIDI input");
    emitStatus();
  }

  return { start, stop, listInputs, useInput, feed, reset, setPlaying, tapRaw, remap };
}
