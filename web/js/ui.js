// Small DOM + formatting helpers shared by every view. Data strings only ever go through textContent.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, v);
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

export async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export function postJSON(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export function fmtDuration(sec) {
  if (sec == null) return "—";
  sec = Math.round(sec);
  const hrs = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (hrs) return `${hrs}h ${String(m).padStart(2, "0")}m`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function fmtMinutes(sec) {
  if (sec == null) return "—";
  const m = Math.round(sec / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m} min`;
}

export function fmtBpm(b) {
  return b == null ? "—" : Number(b).toFixed(b % 1 ? 1 : 0);
}

/** "124–128", or just "124" when every track was the same tempo. */
export function fmtBpmRange(lo, hi) {
  return fmtBpm(lo) === fmtBpm(hi) ? fmtBpm(lo) : `${fmtBpm(lo)}–${fmtBpm(hi)}`;
}

export function fmtDate(iso, opts = { weekday: "short", month: "short", day: "numeric" }) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, opts);
}

export function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function relTime(sec) {
  if (sec == null) return "";
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
  const days = Math.round(sec / 86400);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

export const FLAG_LABELS = {
  bpm_jump: ["Tempo jump", "bad"],
  key_clash: ["Key clash", "bad"],
  quick_cut: ["Quick cut", "warn"],
  played_out: ["Played out", "info"],
  gap: ["Possible gap", "warn"],
  genre_switch: ["Genre switch", "muted"],
  repeat: ["Repeat", "warn"],
};

export function flagChip(flag) {
  const [label, tone] = FLAG_LABELS[flag] || [flag, "muted"];
  return h("span", { class: `chip chip-${tone}` }, label);
}

// Keys found online are estimates (two sources can disagree by a Camelot step), so they read "≈8A";
// rekordbox's own analysis reads plain "8A".
export function fmtKey(t) {
  if (!t?.key) return null;
  return t.key_source === "online" || t.online ? `≈${t.key}` : t.key;
}

export function trackLine(t, extra) {
  return h("div", { class: "track-line" },
    h("div", { class: "track-title" }, t.title),
    h("div", { class: "track-meta" }, [t.artist, t.bpm ? `${fmtBpm(t.bpm)} BPM` : null, fmtKey(t), t.genre].filter(Boolean).join(" · ")),
    extra);
}

export function emptyState(title, detail) {
  return h("div", { class: "empty" }, h("div", { class: "empty-title" }, title), detail && h("p", {}, detail));
}

export function stat(label, value, sub) {
  return h("div", { class: "stat" },
    h("div", { class: "stat-value" }, value),
    h("div", { class: "stat-label" }, label),
    sub && h("div", { class: "stat-sub" }, sub));
}

// Minimal markdown for coach answers: paragraphs, "- " bullets, **bold**. Built with nodes, never innerHTML.
export function renderMarkdown(text) {
  const root = h("div", { class: "md" });
  let list = null;
  for (const raw of (text || "").split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) { list = null; continue; }
    const bullet = line.match(/^\s*[-*•]\s+(.*)/) || line.match(/^\s*\d+\.\s+(.*)/);
    const content = inline(bullet ? bullet[1] : line.replace(/^#+\s*/, ""));
    if (bullet) {
      if (!list) { list = h("ul"); root.append(list); }
      list.append(h("li", {}, content));
    } else {
      list = null;
      root.append(h("p", {}, content));
    }
  }
  return root;
}

function inline(s) {
  return s.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part) =>
    part.startsWith("**") && part.endsWith("**") ? h("strong", {}, part.slice(2, -2)) : part);
}

// Speech: optional voice in and out for hands-on-the-decks use.
export function speak(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.replace(/\*\*/g, "").replace(/^\s*-\s+/gm, ""));
  u.rate = 1.05;
  window.speechSynthesis.speak(u);
}

export function listen({ onResult, onEnd, onError }) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { onError?.("Voice input isn't supported in this browser. Try Chrome."); return null; }
  const rec = new SR();
  rec.lang = navigator.language || "en-US";
  rec.interimResults = true;
  rec.onresult = (e) => {
    const r = e.results[e.results.length - 1];
    onResult?.(r[0].transcript, r.isFinal);
  };
  rec.onerror = (e) => onError?.(e.error === "not-allowed" ? "Microphone access was blocked." : `Voice input error: ${e.error}`);
  rec.onend = () => onEnd?.();
  rec.start();
  return rec;
}
