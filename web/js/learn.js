import { h, clear } from "./ui.js";
import { saveMap, mapFor } from "./midi.js";

/**
 * Teach Blendbook a controller it doesn't know: move each control when asked, and it records which MIDI message
 * that control sends. Faders and LOW EQs are the ones that matter most (they drive the blend timer and the
 * both-basses warning); everything else can be skipped.
 */
const STEPS = [
  { id: "d1.fader", label: "Deck 1 channel fader", deck: 1, param: "fader", ask: "Move the deck 1 (left) channel fader up and down." },
  { id: "d2.fader", label: "Deck 2 channel fader", deck: 2, param: "fader", ask: "Move the deck 2 (right) channel fader up and down." },
  { id: "d1.low", label: "Deck 1 EQ LOW", deck: 1, param: "eq", band: "low", ask: "Turn deck 1's LOW EQ knob." },
  { id: "d2.low", label: "Deck 2 EQ LOW", deck: 2, param: "eq", band: "low", ask: "Turn deck 2's LOW EQ knob." },
  { id: "d1.mid", label: "Deck 1 EQ MID", deck: 1, param: "eq", band: "mid", ask: "Turn deck 1's MID EQ knob." },
  { id: "d2.mid", label: "Deck 2 EQ MID", deck: 2, param: "eq", band: "mid", ask: "Turn deck 2's MID EQ knob." },
  { id: "d1.hi", label: "Deck 1 EQ HI", deck: 1, param: "eq", band: "hi", ask: "Turn deck 1's HI EQ knob." },
  { id: "d2.hi", label: "Deck 2 EQ HI", deck: 2, param: "eq", band: "hi", ask: "Turn deck 2's HI EQ knob." },
  { id: "crossfader", label: "Crossfader", deck: null, param: "crossfader", ask: "Move the crossfader side to side." },
  { id: "d1.play", label: "Deck 1 play", deck: 1, param: "play", note: true, ask: "Press deck 1's PLAY button." },
  { id: "d2.play", label: "Deck 2 play", deck: 2, param: "play", note: true, ask: "Press deck 2's PLAY button." },
];
const REQUIRED = new Set(["d1.fader", "d2.fader", "d1.low", "d2.low"]);
const SETTLE_MS = 900; // keep listening this long after the first message, to tell 14-bit pairs from 7-bit

/** The mapping a burst of messages from one moved control describes, or null if it isn't one control. */
export function entryFromBurst(step, msgs, taken) {
  if (step.note) {
    const m = msgs.find((x) => x.type === "noteon" && !taken.has(`n:${x.status}:${x.data1}`));
    return m ? { id: step.id, label: step.label, deck: step.deck, param: step.param, kind: "note", status: m.status, note: m.data1 } : null;
  }
  // the control that sent the most CC messages; jog wheels and other noise send far fewer during one sweep
  const counts = new Map();
  for (const m of msgs) {
    if (m.type !== "cc" || taken.has(`c:${m.status}:${m.data1}`)) continue;
    const k = `${m.status}:${m.data1}`;
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  if (!counts.size) return null;
  const [top] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
  let [status, cc] = top.split(":").map(Number);
  // 14-bit controls send an MSB (0-31) and an LSB 32 higher; whichever showed up most, pair it with its partner
  if (cc >= 32 && cc < 64 && counts.has(`${status}:${cc - 32}`)) cc -= 32;
  const base = { id: step.id, label: step.label, deck: step.deck, param: step.param, ...(step.band && { band: step.band }) };
  if (cc < 32 && counts.has(`${status}:${cc + 32}`)) return { ...base, kind: "cc14", status, msb: cc, lsb: cc + 32 };
  return { ...base, kind: "cc7", status, cc };
}

function takenKeys(entries) {
  const taken = new Set();
  for (const e of entries) {
    if (e.kind === "note") taken.add(`n:${e.status}:${e.note}`);
    else if (e.kind === "cc7") taken.add(`c:${e.status}:${e.cc}`);
    else { taken.add(`c:${e.status}:${e.msb}`); taken.add(`c:${e.status}:${e.lsb}`); }
  }
  return taken;
}

/** Open the learn dialog for the connected controller. onDone(learnedCount) runs after it closes. */
export function learnController(mon, deviceName, onDone = () => {}) {
  const entries = [];
  let i = 0;
  let burst = null;
  let timer = null;

  const progress = h("p", { class: "muted small" });
  const ask = h("p", { class: "learn-ask", "aria-live": "polite" });
  const heard = h("p", { class: "muted small", role: "status" });
  const skipBtn = h("button", { class: "btn", type: "button" }, "Skip");
  const backBtn = h("button", { class: "btn btn-quiet", type: "button" }, "Back");
  const body = h("div", { class: "sheet-body" });
  const dlg = h("dialog", { class: "sheet", "aria-label": "Teach Blendbook your controller" }, body);

  function finish(save) {
    mon.tapRaw(null);
    clearTimeout(timer);
    if (save && entries.length) saveMap(deviceName, entries);
    mon.remap();
    dlg.close();
    setTimeout(() => dlg.remove(), 400);  // after it has faded out
    onDone(save ? entries.length : 0);
  }

  function summary() {
    mon.tapRaw(null);
    const missing = STEPS.filter((s) => REQUIRED.has(s.id) && !entries.some((e) => e.id === s.id));
    clear(body,
      h("h2", {}, entries.length ? `Learned ${entries.length} controls` : "Nothing learned"),
      h("ul", { class: "list" }, entries.map((e) => h("li", {}, h("span", { class: "track-line" }, e.label),
        h("span", { class: "right" }, e.kind === "note" ? "button" : e.kind === "cc14" ? "14-bit" : "7-bit")))),
      missing.length > 0 && h("div", { class: "banner warn", style: { marginTop: "12px" } },
        h("div", {}, `Without ${missing.map((s) => s.label).join(", ")}, the blend timer and both-basses warning won't be accurate.`)),
      h("div", { class: "sheet-actions" },
        h("button", { class: "btn btn-quiet", type: "button", onclick: () => finish(false) }, "Cancel"),
        h("button", { class: "btn", type: "button", onclick: () => { entries.length = 0; i = 0; step(); } }, "Start over"),
        h("button", { class: "btn btn-primary", type: "button", disabled: !entries.length, onclick: () => finish(true) }, "Save")));
  }

  function step() {
    clearTimeout(timer);
    burst = null;
    if (i >= STEPS.length) return summary();
    const s = STEPS[i];
    progress.textContent = `Step ${i + 1} of ${STEPS.length}${REQUIRED.has(s.id) ? "" : " · optional"}`;
    ask.textContent = s.ask;
    heard.textContent = "Waiting for the controller…";
    backBtn.disabled = i === 0;
    clear(body,
      h("h2", {}, `Teach Blendbook your ${deviceName}`),
      progress, ask, heard,
      h("div", { class: "sheet-actions" },
        h("button", { class: "btn btn-quiet", type: "button", onclick: () => finish(false) }, "Cancel"),
        backBtn, skipBtn));
    const taken = takenKeys(entries);
    mon.tapRaw((m) => {
      // ignore releases and the controls already learned (still settling from the last step)
      if (m.type === "noteoff" || taken.has(`${m.type === "cc" ? "c" : "n"}:${m.status}:${m.data1}`)) return;
      if (!burst) {
        burst = [];
        heard.textContent = "Got it, keep moving it for a moment…";
        timer = setTimeout(() => {
          const entry = entryFromBurst(s, burst, taken);
          if (!entry) {
            heard.textContent = s.note ? "That wasn't a button press. Try again." : "That control is already used. Move the one asked for.";
            burst = null;
            return;
          }
          entries.push(entry);
          i += 1;
          step();
        }, s.note ? 150 : SETTLE_MS);
      }
      burst.push(m);
    });
  }

  skipBtn.addEventListener("click", () => { i += 1; step(); });
  backBtn.addEventListener("click", () => {
    i = Math.max(0, i - 1);
    const at = entries.findIndex((e) => e.id === STEPS[i].id);
    if (at >= 0) entries.splice(at, 1);
    step();
  });
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); finish(false); });

  document.body.append(dlg);
  dlg.showModal();
  step();
}

/** Forget a learned map, so the controller goes back to the built-in one (or none). */
export function forgetController(mon, deviceName) {
  saveMap(deviceName, null);
  mon.remap();
  return mapFor(deviceName).source;
}
