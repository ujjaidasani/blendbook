import { h, clear, postJSON, renderMarkdown, speak, listen } from "./ui.js";
import { prefs } from "./app.js";

// charts.js is written separately; the app keeps working (without charts) if it fails to load.
export const chartsReady = import("./charts.js").catch((e) => {
  console.warn("charts.js unavailable", e);
  return null;
});

/**
 * Ask box: text or voice question, answered from the given scope's real data.
 * getPayload() returns {scope, session_id?, controller?}.
 */
export function coachBox({ title = "Ask Blendbook", placeholder, quick = [], getPayload, compact = false }) {
  const input = h("input", { type: "text", placeholder: placeholder || "Ask about your mixing…", "aria-label": title, enterkeyhint: "send" });
  const answer = h("div", { "aria-live": "polite" });
  const sendBtn = h("button", { class: "btn btn-primary", type: "submit" }, "Ask");
  let rec = null;
  const micBtn = h("button", { class: "btn", type: "button", "aria-label": "Ask by voice", title: "Ask by voice" }, "Voice");

  async function ask(q) {
    q = (q || "").trim();
    if (!q) return;
    input.value = q;
    sendBtn.disabled = true;
    clear(answer, h("div", { class: "answer muted" }, "Thinking…"));
    try {
      const r = await postJSON("/api/coach", { question: q, ...getPayload() });
      clear(answer, h("div", { class: "answer" },
        renderMarkdown(r.answer),
        h("div", { class: "answer-source" }, r.source === "local" ? "Built-in coach (no AI key)" : "Claude Opus 5.5 · from your rekordbox data"),
        r.warning && h("div", { class: "answer-source" }, r.warning)));
      if (prefs.speak) speak(r.answer);
    } catch (e) {
      clear(answer, h("div", { class: "answer" }, `Couldn't get an answer: ${e.message}`));
    } finally {
      sendBtn.disabled = false;
    }
  }

  micBtn.addEventListener("click", () => {
    if (rec) { rec.stop(); return; }
    micBtn.classList.add("recording");
    micBtn.textContent = "Listening…";
    rec = listen({
      onResult: (text, final) => { input.value = text; if (final) ask(text); },
      onEnd: () => { rec = null; micBtn.classList.remove("recording"); micBtn.textContent = "Voice"; },
      onError: (msg) => clear(answer, h("div", { class: "answer" }, msg)),
    });
    if (!rec) { micBtn.classList.remove("recording"); micBtn.textContent = "Voice"; }
  });

  const form = h("form", { class: "ask", onsubmit: (e) => { e.preventDefault(); ask(input.value); } }, input, micBtn, sendBtn);
  return h("section", { class: compact ? "" : "card" },
    h("div", { class: "card-head" }, h("h2", {}, title)),
    form,
    quick.length && h("div", { class: "quick-qs" }, quick.map((q) => h("button", { class: "btn btn-sm", type: "button", onclick: () => ask(q) }, q))),
    answer);
}
