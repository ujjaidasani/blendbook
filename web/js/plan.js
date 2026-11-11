import { h, clear, api, postJSON, fmtBpm, fmtDuration, trackLine, emptyState, fmtDate } from "./ui.js";
import { toast } from "./app.js";

const SHAPES = [["warmup", "Warm-up"], ["build", "Build"], ["peak", "Peak"], ["journey", "Journey"], ["cooldown", "Cool-down"]];
const FRESH = [["none", "Only played"], ["some", "A few new"], ["lots", "Lots new"]];

export function mountPlan(root) {
  const state = { start: null, length: 12, shape: "warmup", fresh: "some", include: [], plan: null, locked: {} };

  const startEl = h("div");
  const includeEl = h("div", { class: "chips", style: { marginTop: "8px" } });
  const resultEl = h("div", { class: "stack" });
  const savedEl = h("div");
  const onlineEl = h("section", { class: "card" });
  const keysEl = h("section", { class: "card" });
  const tasteEl = h("section", { class: "card" });
  const tasteAdds = new Map(); // track id -> its Add button
  const timers = { dig: null, keys: null };

  const seg = (items, key) => {
    const buttons = items.map(([k, label]) => h("button", {
      type: "button", "aria-pressed": String(state[key] === k),
      onclick: () => { state[key] = k; buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(items[i][0] === k))); },
    }, label));
    return h("div", { class: "segmented", role: "group" }, buttons);
  };

  const lengthInput = h("input", { type: "number", min: "4", max: "40", value: "12", "aria-label": "Number of tracks", style: { width: "80px" },
    oninput: () => { state.length = Number(lengthInput.value) || 12; } });
  const buildBtn = h("button", { class: "btn btn-primary", type: "button", onclick: () => build() }, "Build set");

  root.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Plan"),
        h("p", {}, "Build a set from your library, chaining transitions you've already played, along the energy shape you want."))),
    h("div", { class: "grid live-grid" },
      h("div", { class: "stack" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("h2", {}, "Set up")),
          h("h3", { style: { marginBottom: "6px" } }, "Open with"), startEl,
          h("div", { class: "plan-row" }, h("h3", {}, "Tracks"), lengthInput),
          h("div", { class: "plan-row" }, h("h3", {}, "Energy"), seg(SHAPES, "shape")),
          h("div", { class: "plan-row" }, h("h3", {}, "New music"), seg(FRESH, "fresh")),
          h("h3", { style: { margin: "16px 0 6px" } }, "Must include (optional)"),
          trackSearch((t) => { if (!state.include.some((x) => x.id === t.id)) state.include.push(t); renderInclude(); }, "Add a track you want in the set…"),
          includeEl,
          h("div", { style: { marginTop: "16px" } }, buildBtn)),
        resultEl),
      h("div", { class: "stack" }, tasteEl, onlineEl, savedEl, keysEl)));

  function renderStart() {
    clear(startEl, state.start
      ? h("div", { class: "suggestion top" }, trackLine(state.start),
        h("button", { class: "btn btn-sm btn-quiet", onclick: () => { state.start = null; renderStart(); } }, "Change"))
      : trackSearch((t) => { state.start = t; renderStart(); }, "Search your library for an opener…"));
  }

  function renderInclude() {
    clear(includeEl, state.include.map((t) => h("button", { class: "chip chip-accent", type: "button", title: "Remove",
      onclick: () => { state.include = state.include.filter((x) => x.id !== t.id); renderInclude(); } }, `${t.title} ✕`)));
    for (const [id, button] of tasteAdds) {  // the taste card's buttons always say what's in the set
      const on = state.include.some((x) => x.id === id);
      button.textContent = on ? "Added" : "Add";
      button.setAttribute("aria-pressed", String(on));
    }
  }

  async function build() {
    if (!state.start) { toast("Pick an opening track first."); return; }
    buildBtn.disabled = true;
    clear(resultEl, h("div", { class: "card" }, h("div", { class: "skeleton" })));
    try {
      state.plan = await postJSON("/api/plan", {
        start_id: state.start.id, length: state.length, shape: state.shape, fresh: state.fresh,
        include_ids: state.include.map((t) => t.id), locked: state.locked,
      });
      renderPlan();
    } catch (e) {
      clear(resultEl, h("div", { class: "card" }, emptyState("Couldn't build a set", e.message)));
    } finally {
      buildBtn.disabled = false;
    }
  }

  function renderPlan() {
    const p = state.plan;
    renderOnline();
    const total = p.tracks.reduce((a, t) => a + (t.length || 0), 0);
    const withEnergy = p.tracks.some((t) => t.energy != null);
    const nameInput = h("input", { type: "text", value: `${SHAPES.find((s) => s[0] === p.shape)?.[1]} from ${p.tracks[0].title}`.slice(0, 80), "aria-label": "Plan name" });
    clear(resultEl, h("section", { class: "card" },
      h("div", { class: "card-head" },
        h("div", {}, h("h2", {}, `${p.tracks.length} tracks`),
          h("p", {}, `${fmtBpm(p.tracks[0].bpm)} → ${fmtBpm(p.tracks.at(-1).bpm)} BPM · about ${Math.round(total / 60 * 0.75)} min mixed`)),
        h("button", { class: "btn btn-sm", onclick: build }, "Re-roll unlocked")),
      p.warnings.map((w) => h("div", { class: "banner warn", style: { marginBottom: "8px" } }, w)),
      withEnergy && energyStrip(p),
      h("ol", { class: "list" }, p.tracks.map((t, i) => planRow(t, i))),
      h("div", { class: "plan-actions" }, nameInput,
        h("button", { class: "btn btn-primary", onclick: async () => {
          try {
            await postJSON("/api/plans", { name: nameInput.value, track_ids: p.tracks.map((t) => t.id), shape: p.shape });
            toast("Plan saved");
            loadSaved();
          } catch (e) { toast(e.message); }
        } }, "Save plan"),
        h("button", { class: "btn", onclick: () => copyText(p.tracks) }, "Copy as text"))));
  }

  function planRow(t, i) {
    const locked = state.locked[i] === t.id;
    const altEl = h("div");
    return h("li", { style: { flexWrap: "wrap" } },
      h("span", { class: "rank" }, i + 1),
      trackLine(t),
      h("span", { class: "right" }, energyMeter(t.energy), `${fmtBpm(t.bpm)}`, h("div", { class: "muted" }, t.times_played ? `${t.times_played} sets` : "new")),
      i > 0 && h("div", { style: { display: "flex", gap: "4px" } },
        h("button", { class: "btn btn-sm btn-quiet", "aria-pressed": String(locked), title: "Keep this track when re-rolling",
          onclick: () => { if (locked) delete state.locked[i]; else state.locked[i] = t.id; renderPlan(); } }, locked ? "Locked" : "Lock"),
        h("button", { class: "btn btn-sm btn-quiet", onclick: () => showAlternatives(i, altEl) }, "Swap")),
      h("div", { class: "chips", style: { flexBasis: "100%", paddingLeft: "34px" } },
        t.online && h("span", { class: "chip chip-info" }, "Not in your library yet"),
        t.reasons.map((r) => h("span", { class: "chip" }, r)),
        t.online && whereToFind(t)),
      altEl);
  }

  async function showAlternatives(i, el) {
    if (el.childElementCount) { clear(el); return; }
    const p = state.plan;
    const { tracks } = await postJSON("/api/plan/alternatives", {
      prev_id: p.tracks[i - 1].id, target_bpm: p.curve[i], exclude: p.tracks.map((t) => t.id),
    });
    clear(el, h("ul", { class: "list", style: { flexBasis: "100%", paddingLeft: "34px" } }, tracks.map((alt) => h("li", {},
      trackLine(alt), h("button", { class: "btn btn-sm", onclick: () => {
        p.tracks[i] = { ...alt, target_bpm: p.curve[i], target_energy: p.energy_curve?.[i], reasons: alt.reasons, times_played: alt.times_played };
        state.locked[i] = alt.id;
        renderPlan();
      } }, "Use")))));
    el.style.flexBasis = "100%";
  }

  async function loadSaved() {
    let data;
    try { data = await api("/api/plans"); } catch (e) { clear(savedEl, h("div", { class: "card" }, emptyState("Couldn't load plans", e.message))); return; }
    clear(savedEl, h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Saved plans"),
        h("p", {}, data.rekordbox_running ? "rekordbox is open. Close it to send a plan to rekordbox." : "Send a plan to rekordbox as a playlist in a Blendbook folder."))),
      data.plans.length ? h("ul", { class: "list" }, data.plans.map((p) => h("li", { style: { flexWrap: "wrap" } },
        h("div", { class: "track-line", style: { flex: 1 } },
          h("div", { class: "track-title" }, p.name),
          h("div", { class: "track-meta" }, `${p.tracks.length} tracks · saved ${fmtDate(p.created_at.replace(" ", "T") + "Z")}`,
            p.exported_playlist_id ? " · in rekordbox" : "",
            p.tracks.some((t) => t.online) ? ` · ${p.tracks.filter((t) => t.online).length} to add from streaming` : "")),
        h("div", { style: { display: "flex", gap: "4px" } },
          p.exported_playlist_id
            ? h("button", { class: "btn btn-sm btn-quiet", onclick: () => confirmUnexport(p) }, "Remove from rekordbox")
            : h("button", { class: "btn btn-sm", disabled: data.rekordbox_running, onclick: () => confirmExport(p) }, "Send to rekordbox"),
          h("button", { class: "btn btn-sm btn-quiet", onclick: () => copyText(p.tracks) }, "Copy"),
          !p.exported_playlist_id && h("button", { class: "btn btn-sm btn-quiet", onclick: async () => { await postJSON(`/api/plans/${p.id}/delete`, {}); loadSaved(); } }, "Delete")))))
        : h("p", { class: "muted" }, "Build a set and save it to see it here.")));
  }

  function confirmExport(p) {
    const fromOnline = p.tracks.filter((t) => t.online);
    confirmDialog({
      title: "Send to rekordbox?",
      body: [
        `Blendbook will create a playlist called “${p.name}” with ${p.tracks.length - fromOnline.length} tracks, inside a “Blendbook” folder in your rekordbox library.`,
        fromOnline.length > 0 && `${fromOnline.length} track${fromOnline.length > 1 ? "s" : ""} you found online ${fromOnline.length > 1 ? "aren't" : "isn't"} in your library yet, so ${fromOnline.length > 1 ? "they" : "it"} can't go in: add ${fromOnline.length > 1 ? "them" : "it"} from rekordbox's Apple Music or Spotify browser. Once ${fromOnline.length > 1 ? "they're" : "it's"} in your library, Blendbook includes ${fromOnline.length > 1 ? "them" : "it"} automatically next time.`,
        "Nothing else in your library changes. A backup of your rekordbox library is saved first, in the backups folder inside Blendbook's data folder.",
        "Keep rekordbox closed until it's done.",
      ].filter(Boolean),
      action: "Create playlist",
      run: async () => {
        const r = await postJSON(`/api/plans/${p.id}/export`, { confirm: true });
        toast(r.not_in_library?.length ? `Created “${r.name}”. Add the ${r.not_in_library.length} online track${r.not_in_library.length > 1 ? "s" : ""} from streaming.` : `Created “${r.name}” in rekordbox`);
        loadSaved();
      },
    });
  }

  function confirmUnexport(p) {
    confirmDialog({
      title: "Remove from rekordbox?",
      body: [`Deletes the “${p.name}” playlist from the Blendbook folder in rekordbox. Your tracks stay in your library, and the plan stays here.`],
      action: "Remove playlist",
      run: async () => { await postJSON(`/api/plans/${p.id}/unexport`, { confirm: true }); toast("Playlist removed from rekordbox"); loadSaved(); },
    });
  }

  // ---------- find new music online ----------

  const dig = { id: null, status: null, step: null, artists_done: 0, artists_total: 0, checked: 0, to_check: 0, tracks: [], error: null, notice: null, unknownOpen: false, mode: null };
  const player = new Audio();
  player.preload = "none";
  let previewing = null; // apple_id playing
  player.addEventListener("ended", () => { previewing = null; renderOnline(); });
  const searchInput = h("input", { type: "search", placeholder: "Search the catalog: artist, title…", "aria-label": "Search the online catalog", dataset: { key: "search" } });
  const searchForm = h("form", { class: "ask", onsubmit: (e) => { e.preventDefault(); searchOnline(); } },
    searchInput, h("button", { class: "btn", type: "submit" }, "Search"));

  function renderOnline() {
    const p = state.plan;
    const focusKey = onlineEl.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    const running = dig.status === "running";
    const fits = dig.tracks.filter((t) => t.bpm);
    const unknown = dig.tracks.filter((t) => !t.bpm);
    const [done, total] = dig.to_check ? [dig.checked, dig.to_check] : [dig.artists_done, dig.artists_total];
    const status = running
      ? h("div", { class: "dig-status" },
        h("p", { class: "small" }, dig.to_check ? `Checking tempo and key: ${dig.checked} of ${dig.to_check}`
          : total ? `${dig.step} (${done} of ${total})` : `${dig.step}…`),
        h("div", { class: "progress" }, h("i", { style: { width: `${total ? Math.round((100 * done) / total) : 6}%` } })),
        dig.mode === "dig" && !dig.to_check && h("p", { class: "small muted" }, "Apple allows about 20 lookups a minute, so a new dig takes a minute or two."))
      : dig.error ? h("p", { class: "small", role: "alert", style: { color: "var(--bad)" } }, dig.error) : null;
    const notice = dig.notice && h("p", { class: "small", role: "status", style: { color: "var(--warn)", marginTop: "8px" } }, dig.notice);
    clear(onlineEl,
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Find new music"),
        h("p", {}, "Tracks like the ones in your set: similar artists from ListenBrainz, songs from Apple's catalog, and estimated tempo and key from ReccoBeats. Add them to the plan, then pull them in from rekordbox's Apple Music or Spotify browser."))),
      h("button", { class: "btn btn-primary", type: "button", disabled: !p || running, onclick: startDig, dataset: { key: "dig" },
        title: p ? "" : "Build a set first" }, running && dig.mode === "dig" ? "Digging…" : p ? "Dig around this set" : "Build a set to dig around it"),
      h("div", { style: { marginTop: "12px" } }, searchForm),
      status,
      notice,
      fits.length > 0 && h("div", {},
        dig.mode === "dig" && h("h3", { class: "list-label" }, `Fits ${fmtBpm(Math.min(...p.curve))}–${fmtBpm(Math.max(...p.curve))} BPM`),
        h("ul", { class: "list" }, fits.map(onlineRow))),
      unknown.length > 0 && h("details", { class: "dig-unknown", open: dig.mode === "search" || dig.unknownOpen, ontoggle: (e) => { dig.unknownOpen = e.target.open; } },
        h("summary", { class: "small muted" }, `${unknown.length} more without tempo data: check them by ear`),
        h("ul", { class: "list" }, unknown.map(onlineRow))),
      !running && dig.status === "done" && !dig.tracks.length && h("p", { class: "muted small", style: { marginTop: "12px" } },
        dig.mode === "search" ? "Nothing found. Try the artist and title together." : "Nothing new fits this set's tempo. Try a different energy shape."),
      h("p", { class: "muted small", style: { marginTop: "12px" } },
        "Only artist names and titles are sent, to Apple, MusicBrainz, ListenBrainz and ReccoBeats, and only when you dig or search. Previews are provided courtesy of iTunes."));
    // everything above was rebuilt, so put the keyboard back where it was
    if (focusKey) onlineEl.querySelector(`[data-key="${CSS.escape(focusKey)}"]`)?.focus();
  }

  function onlineRow(t) {
    const added = state.plan?.tracks.some((x) => x.id === t.id);
    const playing = previewing === t.apple_id;
    return h("li", { style: { flexWrap: "wrap" } },
      h("button", { class: "btn btn-sm btn-quiet preview-btn", type: "button", "aria-pressed": String(playing), disabled: !t.preview_url, dataset: { key: `preview-${t.apple_id}` },
        title: t.preview_url ? null : "No preview for this track",
        "aria-label": t.preview_url ? `${playing ? "Stop" : "Play"} a preview of ${t.title}` : `No preview of ${t.title}`, onclick: () => togglePreview(t) }, playing ? "■" : "▶"),
      trackLine(t),
      t.in_library ? h("span", { class: "chip" }, "In your library")
        : state.plan && h("button", { class: "btn btn-sm", type: "button", disabled: added, onclick: () => addToPlan(t) }, added ? "Added" : "Add"),
      h("div", { class: "chips", style: { flexBasis: "100%", paddingLeft: "44px", alignItems: "center" } },
        h("a", { class: "store-badge", href: t.apple_url, target: "_blank", rel: "noopener noreferrer", "aria-label": `Get ${t.title} on iTunes` },
          h("img", { src: "img/get-it-on-itunes.svg", alt: "Get it on iTunes", width: "110", height: "32" })),
        t.reasons.filter((r) => !/^[\d.]+ BPM$|^key /.test(r)).map((r) => h("span", { class: "chip" }, r)), whereToFind({ ...t, apple_url: null })));
  }

  async function togglePreview(t) {
    if (previewing === t.apple_id) {
      player.pause();
      previewing = null;
      renderOnline();
      return;
    }
    previewing = t.apple_id;
    renderOnline();
    try {
      // preview links can stop working: if an older one fails, ask for a fresh one
      try {
        player.src = t.preview_url;
        await player.play();
      } catch {
        const r = await postJSON("/api/online/preview", { apple_id: t.apple_id });
        t.preview_url = r.preview_url;
        if (previewing !== t.apple_id) return;
        player.src = r.preview_url;
        await player.play();
      }
    } catch (e) {
      if (previewing === t.apple_id) previewing = null;
      toast(`Couldn't play a preview: ${e.message}`);
      renderOnline();
    }
  }

  async function startDig() {
    const p = state.plan;
    clearTimeout(timers.dig);
    Object.assign(dig, { id: null, status: "running", step: "Starting", artists_done: 0, artists_total: 0, checked: 0, to_check: 0, tracks: [], error: null, notice: null, unknownOpen: false, mode: "dig" });
    renderOnline();
    try {
      const r = await postJSON("/api/online/dig", {
        track_ids: p.tracks.map((t) => t.id), bpm_min: Math.min(...p.curve), bpm_max: Math.max(...p.curve),
      });
      dig.id = r.id;
      pollDig();
    } catch (e) {
      Object.assign(dig, { status: "error", error: e.message });
      renderOnline();
    }
  }

  async function pollDig() {
    try {
      Object.assign(dig, await api(`/api/online/dig/${dig.id}`));
    } catch (e) {
      Object.assign(dig, { status: "error", error: e.message });
    }
    renderOnline();
    if (dig.status === "running") timers.dig = setTimeout(pollDig, 1200);
    else if (dig.status === "done") toast(dig.tracks.length ? `Found ${dig.tracks.length} tracks to try` : "Nothing new fits this set");
  }

  async function searchOnline() {
    const q = searchInput.value.trim();
    if (q.length < 2) return;
    clearTimeout(timers.dig);
    Object.assign(dig, { id: null, status: "running", step: `Searching for “${q}”`, artists_done: 0, artists_total: 0, checked: 0, to_check: 0, tracks: [], error: null, notice: null, unknownOpen: false, mode: "search" });
    renderOnline();
    try {
      const r = await postJSON("/api/online/search", { q });
      Object.assign(dig, { status: "done", tracks: r.tracks, notice: r.notice });
    } catch (e) {
      Object.assign(dig, { status: "error", error: e.message });
    }
    renderOnline();
  }

  // Put an online track where the plan's tempo is closest to it, lock it there, and keep the other locks in place.
  function addToPlan(t) {
    const p = state.plan;
    if (!p || p.tracks.some((x) => x.id === t.id)) return;
    const asPlayed = (target) => [1, 0.5, 2].map((m) => t.bpm * m).reduce((a, b) => (Math.abs(b - target) < Math.abs(a - target) ? b : a));
    let at = p.tracks.length;
    if (t.bpm) {
      let best = Infinity;
      for (let i = 1; i <= p.tracks.length; i++) {
        const target = p.curve[Math.min(i, p.curve.length - 1)];
        const gap = Math.abs(asPlayed(target) - target);
        if (gap < best) { best = gap; at = i; }
      }
    }
    const target = p.curve[Math.min(at, p.curve.length - 1)];
    const targetEnergy = p.energy_curve?.[Math.min(at, p.energy_curve.length - 1)];
    p.tracks.splice(at, 0, { ...t, target_bpm: target, target_energy: targetEnergy, reasons: ["added from online", ...t.reasons.filter((r) => !r.startsWith("key")).slice(0, 2)], times_played: 0 });
    p.curve.splice(at, 0, target);
    p.energy_curve?.splice(at, 0, targetEnergy);
    const locked = {};
    for (const [k, v] of Object.entries(state.locked)) locked[Number(k) >= at ? Number(k) + 1 : Number(k)] = v;
    locked[at] = t.id;
    state.locked = locked;
    state.length = p.tracks.length;
    lengthInput.value = String(state.length);
    renderPlan();
    toast(`Added “${t.title}” as track ${at + 1}`);
  }

  // ---------- your taste ----------

  // What you lean on, what you've forgotten and what you've never played, one click from this set
  async function renderTaste() {
    let d;
    try { d = await api("/api/insights"); } catch { return; }
    tasteAdds.clear();
    const toggle = (t) => {
      if (t.id === state.start?.id) { toast("That's already your opener."); return; }
      const on = state.include.some((x) => x.id === t.id);
      state.include = on ? state.include.filter((x) => x.id !== t.id) : [...state.include, t];
      renderInclude();
    };
    const group = (title, tracks, side) => tracks.length > 0 && h("div", {},
      h("h3", { class: "list-label" }, title),
      h("ul", { class: "list" }, tracks.slice(0, 3).map((t) => h("li", {}, trackLine(t), side(t)))));
    const add = (t) => {
      const b = h("button", { class: "btn btn-sm", type: "button", "aria-label": `Add ${t.title} to this set`, "aria-pressed": "false", onclick: () => toggle(t) }, "Add");
      tasteAdds.set(t.id, b);
      return b;
    };
    clear(tasteEl,
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Your taste"), h("p", {}, "Change up your sets with what's already in your library.")),
        h("a", { class: "btn btn-sm btn-quiet", href: "#insights" }, "See all in Insights")),
      group("Forgotten favourites", d.dormant_favourites, add),
      group("Never played out", d.never_played, add),
      group("Leaning on", d.leaning_on, (t) => h("span", { class: "right" }, `${t.sets} of last ${t.of} sets`)));
    renderInclude();
  }

  // ---------- keys ----------

  async function renderKeys() {
    clearTimeout(timers.keys);
    let st;
    try { st = await api("/api/online"); } catch { return; }
    const c = st.coverage;
    const e = st.enrich;
    const pct = Math.round((100 * c.rekordbox_keys) / Math.max(1, c.tracks));
    const allChecked = c.checked + c.rekordbox_keys >= c.tracks;
    const left = c.no_rekordbox_key || {};
    const apple = left["apple-music"] || 0;
    const step = (n, title, how) => n > 0 && h("li", {}, h("b", {}, `${n} ${title}: `), how);
    clear(keysEl,
      h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Keys"),
        h("p", {}, "Harmonic picks and clash warnings use rekordbox's keys only, so the more tracks you analyse there, the better they get."))),
      h("div", { class: "meter vol", role: "progressbar", "aria-label": "Tracks with a key from rekordbox", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 },
        h("span", {}, "Done"), h("div", { class: "bar" }, h("i", { style: { width: `${pct}%`, background: "var(--accent)" } })), h("span", {}, `${pct}%`)),
      h("p", { class: "small muted", style: { marginTop: "8px" } }, `${c.rekordbox_keys} of ${c.tracks} tracks have a key from rekordbox.`),
      pct < 100 && h("ul", { class: "key-steps small" },
        step(left.file, "local files", "select them in rekordbox and choose Analyze Track."),
        step(apple, "Apple Music tracks", `select them and choose Analyze Track. It takes about ${Math.max(1, Math.round(apple / 120))} hour${apple > 180 ? "s" : ""}, so leave rekordbox open and online.`),
        step(left.spotify, "Spotify tracks", "remove them from Collection and reload them from Spotify, as Analyze Track isn't available."),
        step(left.other, "other streamed tracks", "select them in rekordbox and choose Analyze Track.")),
      c.rekordbox_energy > 0 && h("p", { class: "small muted", style: { marginTop: "8px" } }, `Energy for ${c.rekordbox_energy} tracks comes from rekordbox's waveforms, compared across your library.`),
      h("details", { class: "key-estimates" },
        h("summary", { class: "small muted" }, `Online estimates (${c.from_online})`),
        h("p", { class: "small muted", style: { marginTop: "6px" } }, "Keys found online are rough hints, shown as ≈8A. They matched rekordbox on only about 1 in 4 tracks, so they never count as a match."),
        e.running ? h("p", { class: "small", style: { marginTop: "6px" } }, e.stopping
          ? "Stopping after the tracks being looked up right now…"
          : `Looking up ${e.done} of ${e.total}, most-played first. You can keep using Blendbook.`)
          : e.error ? h("p", { class: "small", role: "alert", style: { color: "var(--bad)", marginTop: "6px" } }, e.error)
            : e.total ? h("p", { class: "small muted", style: { marginTop: "6px" } }, `Last run found ${e.matched} of ${e.done} tracks.`) : null,
        h("div", { style: { marginTop: "8px" } },
          h("button", { class: "btn btn-sm", type: "button", disabled: e.stopping, onclick: async () => {
            try { await postJSON("/api/online/enrich", { action: e.running ? "stop" : "start" }); } catch (err) { toast(err.message); }
            renderKeys();
          } }, e.stopping ? "Stopping…" : e.running ? "Stop" : allChecked ? "Check again" : c.checked ? "Continue" : "Look up estimates"))));
    if (e.running) timers.keys = setTimeout(renderKeys, 2000);
  }

  renderStart();
  renderOnline();
  renderKeys();
  renderTaste();
  loadSaved();
  return {
    destroy() {
      clearTimeout(timers.dig);
      clearTimeout(timers.keys);
      player.pause();
    },
  };
}

// Energy (0..1, from online data) as five rising steps, next to the BPM. Nothing when it isn't known.
function energyMeter(e) {
  if (e == null) return null;
  const lit = Math.max(1, Math.round(e * 5));
  return h("span", { class: "energy-meter", role: "img", "aria-label": `Energy ${lit} of 5`, title: `Energy ${e.toFixed(2)}` },
    [1, 2, 3, 4, 5].map((n) => h("i", { class: n <= lit ? "on" : null, style: { height: `${2 + 2 * n}px` } })));
}

// Each track's energy as a column, with the shape's target energy as a tick across it.
function energyStrip(p) {
  const shape = SHAPES.find((s) => s[0] === p.shape)?.[1] || "Plan";
  const target = p.energy_curve || [];
  const known = p.tracks.filter((t) => t.energy != null).length;
  const fmt = (e) => (e == null ? "unknown" : e.toFixed(2));
  return h("figure", { class: "energy-strip" },
    h("div", { class: "energy-cols", role: "img",
      "aria-label": `Energy across the set: ${known} of ${p.tracks.length} tracks have energy data. The ${shape} target runs from ${fmt(target[0])} to ${fmt(target.at(-1))}.` },
      p.tracks.map((t, i) => h("div", { class: "energy-col", title: `${i + 1}. ${t.title}: energy ${fmt(t.energy)}${target[i] != null ? `, target ${fmt(target[i])}` : ""}` },
        t.energy != null && h("i", { style: { height: `${Math.round(t.energy * 100)}%` } }),
        target[i] != null && h("b", { style: { bottom: `${Math.round(target[i] * 100)}%` } })))),
    h("figcaption", { class: "small muted" },
      h("span", { class: "energy-key" }), "Track energy",
      h("span", { class: "energy-key target" }), `${shape} target`,
      known < p.tracks.length && h("span", { class: "energy-note" }, `${p.tracks.length - known} without energy data`)));
}

// Online tracks: link to where a DJ can get them: Beatport, or the streaming services rekordbox plays.
function whereToFind(t) {
  const link = (href, label) => typeof href === "string" && href.startsWith("https://")
    ? h("a", { class: "chip chip-link", href, target: "_blank", rel: "noopener noreferrer" }, `${label} ↗`) : null;
  return [link(t.beatport_url, "Beatport"), link(t.apple_url, "Apple Music"), link(t.spotify_url, "Spotify")];
}

function trackSearch(onPick, placeholder) {
  const input = h("input", { type: "search", placeholder, "aria-label": placeholder });
  const results = h("ul", { class: "list" });
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (!q) return clear(results);
      const { tracks } = await api(`/api/search?q=${encodeURIComponent(q)}`);
      clear(results, tracks.slice(0, 6).map((t) => h("li", {}, trackLine(t),
        h("button", { class: "btn btn-sm", type: "button", onclick: () => { input.value = ""; clear(results); onPick(t); } }, "Pick"))));
    }, 180);
  });
  return h("div", {}, input, results);
}

function copyText(tracks) {
  const text = tracks.map((t, i) => `${i + 1}. ${t.artist} – ${t.title} (${fmtBpm(t.bpm)} BPM${t.length ? `, ${fmtDuration(t.length)}` : ""})`).join("\n");
  (navigator.clipboard?.writeText(text) || Promise.reject()).then(() => toast("Copied"), () => toast("Couldn't copy"));
}

function confirmDialog({ title, body, action, run }) {
  const err = h("p", { class: "small", style: { color: "var(--bad)" }, role: "alert" });
  const go = h("button", { class: "btn btn-primary", type: "button" }, action);
  const dlg = h("dialog", { class: "sheet", "aria-label": title },
    h("div", { class: "sheet-body" }, h("h2", {}, title), body.map((b) => h("p", { class: "muted", style: { marginTop: "8px" } }, b)), err,
      h("div", { class: "sheet-actions" },
        h("button", { class: "btn", type: "button", onclick: () => dlg.close() }, "Cancel"), go)));
  go.addEventListener("click", async () => {
    go.disabled = true;
    try { await run(); dlg.close(); } catch (e) { err.textContent = e.message; go.disabled = false; }
  });
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}
