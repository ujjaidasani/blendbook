import { api, postJSON, h } from "./ui.js";
import { mountLive } from "./live.js";
import { mountSessions } from "./sessions.js";
import { mountInsights } from "./insights.js";
import { mountPlan } from "./plan.js";

const views = { live: mountLive, sessions: mountSessions, insights: mountInsights, plan: mountPlan };
const viewEl = document.getElementById("view");
let current = null;

export const prefs = {
  get speak() { try { return localStorage.getItem("db_speak") === "1"; } catch { return false; } },
  set speak(v) { try { localStorage.setItem("db_speak", v ? "1" : "0"); } catch {} },
};

export const appState = { status: null };

// Where people can chip in for Blendbook (Buy Me a Coffee / GitHub Sponsors). The link stays hidden while empty.
export const SUPPORT_URL = "";
export const RELEASES_URL = "https://github.com/ujjaidasani/blendbook/releases";

/** What a new user needs to do before Blendbook has anything to show, or null once it does. */
export function setupHelp(st = appState.status) {
  if (!st) return null;
  const step = (title, ...body) => h("li", {}, h("b", {}, title), " ", ...body);
  if (!st.found) {
    return h("div", { class: "setup" },
      h("h2", {}, "Let's find your rekordbox library"),
      h("p", { class: "muted" }, `Blendbook looked in ${st.rekordbox_dir} and found nothing.`),
      h("ol", {},
        step("Install rekordbox 6 or 7", "and open it once, so it creates its library."),
        step("Play a few tracks.", "rekordbox logs them under History, and that's what Blendbook reads."),
        step("Library somewhere else?", "Choose its folder below.")),
      h("button", { class: "btn btn-sm", type: "button", onclick: () => chooseFolder() }, "Choose folder…"),
      h("p", { class: "muted small" }, "Blendbook checks again every 30 seconds; this page updates by itself."));
  }
  if (st.error) {
    return h("div", { class: "setup" },
      h("h2", {}, "Blendbook can't read your rekordbox library"),
      h("p", { class: "muted" }, st.error),
      h("p", {}, st.newer_rekordbox
        ? `You're on rekordbox ${st.rekordbox_version}, and Blendbook has been tested up to ${st.tested_version}, so that update is the likely cause. Your library is fine: Blendbook only reads a copy of it. `
        : "If rekordbox updated recently, a newer Blendbook may be needed. ",
      h("a", { href: RELEASES_URL, target: "_blank", rel: "noopener" }, "Check for updates"), "."));
  }
  if (!st.sessions) {
    return h("div", { class: "setup" },
      h("h2", {}, `Found ${st.tracks} tracks. Now play a set.`),
      h("ol", {},
        step("Open rekordbox in Performance mode", "and play a few tracks, as you normally would."),
        step("About a minute into each track,", "rekordbox logs it under History, and it shows up here."),
        step("Plug in your controller", "(DDJ-FLX4, or any other with “Use another controller” on this page) to get the mixer view.")),
      h("p", { class: "muted small" }, "Meanwhile, the Plan page can already build a set from your library."));
  }
  return null;
}

function route(force = false) {
  const [name, ...rest] = (location.hash.slice(1) || "plan").split("/");
  const mount = views[name] || views.plan;
  document.querySelectorAll(".tabs a").forEach((a) => {
    if (a.dataset.route === (views[name] ? name : "plan")) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  // Until rekordbox has something to read, every page shows how to get there (Plan only needs a library)
  const help = setupHelp();
  if (help && !(name === "plan" && appState.status.found && !appState.status.error && appState.status.tracks)) {
    current?.destroy?.();
    current = { name, setup: true };
    viewEl.replaceChildren(help);
    return;
  }
  if (!force && current?.name === name && current.onParams) {
    current.onParams(rest);
    return;
  }
  current?.destroy?.();
  viewEl.replaceChildren();
  current = { name, ...(mount(viewEl, rest) || {}) };
  window.scrollTo(0, 0); // a new page starts at the top
  viewEl.focus({ preventScroll: true });
}

export async function chooseFolder(reset = false) {
  try {
    const r = await postJSON("/api/rekordbox/folder", reset ? { reset: true } : {});
    if (r.cancelled) return;
    await refreshStatus();
    route(true);
    toast(r.found ? `Found ${r.tracks} tracks and ${r.sessions} sets` : "No rekordbox library there yet");
  } catch (e) {
    toast(e.message);
  }
}

export function toast(msg) {
  document.querySelector(".toast")?.remove();
  // a popover, so it shows above an open dialog and its backdrop too
  const t = h("div", { class: "toast", role: "status", popover: "manual" }, msg);
  document.body.append(t);
  t.showPopover?.();
  setTimeout(() => t.remove(), 3200);
}

async function refreshStatus() {
  const pill = document.getElementById("statusPill");
  try {
    const st = await api("/api/status");
    appState.status = st;
    if (current?.setup) route(true); // still setting up: show the next step, or the real page once it's ready
    if (!st.found) {
      pill.className = "status-pill err";
      pill.textContent = "rekordbox not found";
    } else if (st.error) {
      pill.className = "status-pill err";
      pill.textContent = "Can't read rekordbox";
      pill.title = st.error;
    } else {
      pill.className = "status-pill ok";
      pill.textContent = `rekordbox · ${st.sessions} sets`;
      pill.title = `${st.tracks} tracks · updated ${st.loaded_at}`;
    }
  } catch {
    pill.className = "status-pill err";
    pill.textContent = "Server offline";
  }
}

function showUpdate(update) {
  const link = document.getElementById("updateLink");
  link.hidden = !update;
  if (update) { link.href = update.url; link.textContent = `Update to ${update.version}`; }
}

function initSettings() {
  const dlg = document.getElementById("settingsDialog");
  const input = document.getElementById("apiKeyInput");
  const aiStatus = document.getElementById("aiStatus");
  const speakToggle = document.getElementById("speakToggle");

  const fill = () => {
    const st = appState.status;
    document.getElementById("settingsLibrary").textContent = st
      ? (st.found ? `${st.tracks} tracks and ${st.sessions} history sessions from ${st.rekordbox_dir}. Blendbook reads a copy, and only writes to rekordbox when you press Send to rekordbox on the Plan page.` : `No library at ${st.rekordbox_dir}.`)
      : "—";
    aiStatus.textContent = st?.ai === "claude" ? "Claude is connected." : "No key set: using the built-in coach.";
    document.getElementById("aboutText").textContent = st
      ? `Blendbook ${st.version}, reading rekordbox ${st.rekordbox_version || "(version unknown)"}${st.newer_rekordbox ? `, newer than the ${st.tested_version} it was tested with` : ""}. Your Blendbook data (mix log, plans, goals, settings) is in ${st.data_dir}; recordings go to ${st.recordings_dir}. Nothing is sent anywhere unless you use an online feature.`
      : "—";
    speakToggle.checked = prefs.speak;
    document.getElementById("clearKeyBtn").hidden = st?.ai !== "claude";
    input.value = "";
  };

  const phoneBox = document.getElementById("phoneBox");
  const renderPhone = (st) => {
    phoneBox.replaceChildren(
      st.enabled
        ? h("div", {},
          h("p", {}, "On your phone, open ", h("b", {}, st.url), " and enter the code ", h("b", { style: { fontSize: "1.2rem", letterSpacing: "0.12em" } }, st.code), "."),
          h("p", { class: "muted small", style: { marginTop: "6px" } }, st.paired ? `${st.paired} phone${st.paired > 1 ? "s" : ""} paired.` : "No phone paired yet."),
          h("button", { class: "btn btn-sm", type: "button", style: { marginTop: "8px" }, onclick: () => setPhone(false) }, "Turn off phone view"))
        : h("button", { class: "btn btn-sm", type: "button", onclick: () => setPhone(true) }, "Allow phone on this Wi-Fi"));
  };
  const setPhone = async (enable) => {
    try { renderPhone(await postJSON("/api/phone", { enable })); } catch (e) { toast(e.message); }
  };

  document.getElementById("settingsBtn").addEventListener("click", async () => {
    await refreshStatus();
    fill();
    api("/api/phone").then(renderPhone).catch(() => phoneBox.replaceChildren());
    dlg.showModal();
  });
  speakToggle.addEventListener("change", () => { prefs.speak = speakToggle.checked; });

  const save = async (key) => {
    try {
      const r = await postJSON("/api/settings", { anthropic_api_key: key });
      await refreshStatus();
      toast(r.ai === "claude" ? "Claude connected" : "Using the built-in coach");
    } catch (e) {
      toast(e.message);
    }
  };
  document.getElementById("settingsForm").addEventListener("submit", (e) => {
    if (input.value.trim()) save(input.value.trim());
  });
  document.getElementById("clearKeyBtn").addEventListener("click", async () => { await save(""); fill(); });

  document.getElementById("pickFolderBtn").addEventListener("click", async () => { await chooseFolder(); fill(); });
  document.getElementById("resetFolderBtn").addEventListener("click", async () => { await chooseFolder(true); fill(); });

  const updateToggle = document.getElementById("updateToggle");
  api("/api/update").then((u) => { updateToggle.checked = u.enabled; showUpdate(u.update); }).catch(() => {});
  updateToggle.addEventListener("change", async () => {
    try {
      await postJSON("/api/settings", { check_updates: updateToggle.checked });
      showUpdate(updateToggle.checked ? (await api("/api/update")).update : null);
    } catch (e) { toast(e.message); }
  });

  document.getElementById("diagnosticsBtn").addEventListener("click", async () => {
    try {
      const { text } = await api("/api/diagnostics");
      await navigator.clipboard.writeText(text);
      toast("Copied. Have a read before you send it.");
    } catch (e) {
      toast(`Couldn't copy the diagnostics: ${e.message}`);
    }
  });

  const support = document.getElementById("supportLink");
  if (SUPPORT_URL) { support.href = SUPPORT_URL; support.hidden = false; }
  document.getElementById("quitBtn").addEventListener("click", async () => {
    if (!confirm("Quit Blendbook? The mix log stops until you open it again.")) return;
    try { await postJSON("/api/quit", {}); } catch {}
    dlg.close();
    viewEl.replaceChildren(h("div", { class: "setup" }, h("h2", {}, "Blendbook has quit"),
      h("p", { class: "muted" }, "You can close this tab. Open Blendbook again from Applications (or the Start menu) when you need it.")));
  });
}

window.addEventListener("hashchange", () => route());
initSettings();
setInterval(refreshStatus, 30000);
refreshStatus().then(async () => {
  // Blendbook opens on Plan, unless a set is running right now
  if (!location.hash) {
    try { if ((await api("/api/live")).active) history.replaceState(null, "", "#live"); } catch {}
  }
  route();
});
