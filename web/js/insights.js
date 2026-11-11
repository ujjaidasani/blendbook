import { h, clear, api, postJSON, fmtDate, fmtBpm, fmtDuration, stat, trackLine, emptyState, renderMarkdown } from "./ui.js";
import { coachBox, chartsReady } from "./coach.js";

const METRICS = [["tracks", "Tracks"], ["duration", "Length"], ["bpm", "Tempo range"], ["smooth", "Smooth tempo %"]];

export function mountInsights(root) {
  const charts = [];
  const body = h("div", { class: "stack" }, h("div", { class: "card" }, h("div", { class: "skeleton" })));
  root.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Insights"), h("p", {}, "Patterns across all your sets: habits, go-to moves, and music you're sitting on."))),
    body);

  api("/api/insights").then(render).catch((e) => clear(body, h("div", { class: "card" }, emptyState("Couldn't load insights", e.message))));

  function render(d) {
    const t = d.totals;
    if (!t.sessions) {
      clear(body, h("div", { class: "card" }, emptyState("No sets yet", "Play a set in rekordbox and come back.")));
      return;
    }
    let metric = "tracks";
    const timelineEl = h("div", { class: "chart", style: { minHeight: "200px" } });
    const metricButtons = METRICS.map(([key, label]) => h("button", {
      type: "button", "aria-pressed": String(key === metric),
      onclick: () => {
        metric = key;
        metricButtons.forEach((b, i) => b.setAttribute("aria-pressed", String(METRICS[i][0] === key)));
        charts[0]?.update(undefined, { metric });
      },
    }, label));

    const list = (items, render, empty) => items.length
      ? h("ol", { class: "list" }, items.map((it, i) => h("li", {}, h("span", { class: "rank" }, i + 1), ...render(it))))
      : h("p", { class: "muted small" }, empty);

    const goalsEl = h("div", { class: "stack" });
    clear(body,
      goalsEl,
      h("div", { class: "stats" },
        stat("Sets", t.sessions, `${fmtDate(t.first_session, { month: "short", year: "numeric" })} – ${fmtDate(t.last_session, { month: "short", year: "numeric" })}`),
        stat("Tracks played", t.plays, `${t.unique_tracks_played} different`),
        stat("Typical set", t.median_set_minutes ? `${t.median_set_minutes} min` : "—", "median length"),
        stat("Smooth tempo", t.median_smooth_tempo_pct != null ? `${t.median_smooth_tempo_pct}%` : "—", "median per set"),
        stat("Library played", `${t.library_coverage_pct}%`, `${t.never_played_count ?? d.never_played_count} never played`)),

      h("div", { class: "grid three" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Leaning on"), h("p", {}, "In at least 3 of your last 5 sets."))),
          list(d.leaning_on, (tr) => [trackLine(tr), h("span", { class: "right" }, `${tr.sets} of ${tr.of}`)], "Nothing yet: you're keeping your sets fresh.")),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Forgotten favourites"), h("p", {}, "Played in 3+ sets, but not in your last 5."))),
          list(d.dormant_favourites, (tr) => [trackLine(tr), h("span", { class: "right" }, `${tr.sessions} sets`)], "Nothing forgotten: you're rotating your favourites.")),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, `Never played out (${d.never_played_count})`), h("p", {}, "In your library but never in a set, oldest first."))),
          list(d.never_played.slice(0, 10), (tr) => [trackLine(tr), h("span", { class: "right" }, tr.length ? fmtDuration(tr.length) : "")], "You've played everything in your library."))),

      h("section", { class: "card" },
        h("div", { class: "card-head" },
          h("div", {}, h("h2", {}, "Your sets over time"), h("p", {}, "Select a set to open it.")),
          h("div", { class: "segmented", role: "group", "aria-label": "Metric" }, metricButtons)),
        timelineEl),

      h("div", { class: "grid two" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Signature transitions"), h("p", {}, "Pairs you've mixed back-to-back more than once."))),
          list(d.signature_transitions.slice(0, 8), (p) => [
            h("div", { class: "track-line", style: { flex: 1 } },
              h("div", { class: "track-title" }, p.from.title),
              h("div", { class: "track-meta" }, "→ ", p.to.title, ` · ${fmtBpm(p.from.bpm)} → ${fmtBpm(p.to.bpm)} BPM`)),
            h("span", { class: "right" }, `${p.count}×`)],
          "No repeated pairs yet. As you play more sets, your go-to blends show up here.")),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Most played"), h("p", {}, "Across every set."))),
          list(d.top_tracks.slice(0, 8), (tr) => [trackLine(tr), h("span", { class: "right" }, `${tr.plays}×`, h("div", { class: "muted" }, fmtDate(tr.last_played, { month: "short", day: "numeric" })))], "—"))),

      h("div", { class: "grid two" },
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Go-to openers"), h("p", {}, "Tracks you start sets with."))),
          list(d.go_to_openers, (tr) => [trackLine(tr), h("span", { class: "right" }, `${tr.times}×`)], "You open with something different every time.")),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Top artists"))),
          list(d.top_artists.slice(0, 8), (a) => [h("div", { class: "track-line", style: { flex: 1 } }, h("div", { class: "track-title" }, a.artist)), h("span", { class: "right" }, `${a.plays} plays`)], "—"))),

      coachBox({
        title: "Ask about your DJing",
        placeholder: "e.g. Am I getting better at keeping tempo steady?",
        quick: ["What are my habits?", "What should I practise this week?", "Am I relying on the same tracks?"],
        getPayload: () => ({ scope: "insights" }),
      }));

    loadGoals(goalsEl);

    chartsReady.then((c) => {
      if (!c) return clear(timelineEl, h("p", { class: "muted small" }, "Charts unavailable."));
      charts.push(c.sessionTimeline(timelineEl, d.timeline, { metric, onSelect: (id) => { location.hash = `#sessions/${id}`; } }));
    });
  }

  // ---------- habit goals + weekly digest ----------

  const goalCharts = [];
  async function loadGoals(el) {
    let g, dg;
    try {
      [g, dg] = await Promise.all([api("/api/goals"), api("/api/digest")]);
    } catch (e) {
      clear(el, h("div", { class: "card" }, emptyState("Couldn't load goals", e.message)));
      return;
    }
    goalCharts.splice(0).forEach((c) => c.destroy?.());
    const refresh = () => loadGoals(el);
    clear(el,
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", {}, h("h2", {}, "Since last week"),
          h("p", {}, "From your last 7 days of sets."))),
        renderMarkdown(dg.lines.map((l) => `- ${l}`).join("\n")),
        dg.standout?.session_id && h("a", { class: "btn btn-sm btn-quiet", style: { marginTop: "8px" },
          href: `#sessions/${dg.standout.session_id}` }, "Open that set")),
      h("section", { class: "card" },
        h("div", { class: "card-head" },
          h("div", {}, h("h2", {}, "Goals"), h("p", {}, "Pick a habit to work on. Each set is checked against it."))),
        g.goals.length ? h("div", { class: "grid two" }, g.goals.map((goal) => goalCard(goal, refresh)))
          : h("p", { class: "muted" }, "No goals yet. A good first one: no gaps between tracks."),
        addGoalForm(g.metrics, g.goals, refresh)));
  }

  let hasGoalTrend = false;
  chartsReady.then((c) => { hasGoalTrend = !!c?.goalTrend; });

  function goalCard(goal, refresh) {
    const sign = goal.direction === "at_most" ? "≤" : "≥";
    const fmt = (v) => (v == null ? "—" : goal.unit === "%" ? `${v}%` : goal.unit === "s" ? `${v}s` : `${v}`);
    const chartEl = h("div", { class: "chart", style: { minHeight: "160px" } });
    const trendChip = { improving: ["Improving", "good"], slipping: ["Slipping", "bad"], steady: ["Steady", "muted"] }[goal.trend];
    chartsReady.then((c) => {
      if (!c) return;
      if (c.goalTrend) {
        goalCharts.push(c.goalTrend(chartEl, { metric: goal.metric, label: goal.label, unit: goal.unit, target: goal.target,
          direction: goal.direction, points: goal.points }, { last: 10, onSelect: (id) => { location.hash = `#sessions/${id}`; } }));
      } else {
        goalCharts.push(c.sparkline(chartEl, goal.points.map((p) => p.value).filter((v) => v != null).slice(-10), { color: "var(--accent)" }));
      }
    });
    return h("div", { class: "stat", style: { padding: "16px" } },
      h("div", { class: "card-head", style: { marginBottom: "8px" } },
        h("div", {},
          h("b", {}, goal.label, " ", sign, " ", fmt(goal.target)),
          h("div", { class: "muted small" }, goal.help)),
        h("div", { class: "chips" },
          goal.focus ? h("span", { class: "chip chip-accent" }, "Focus") : null,
          // the trend chart states the trend itself; only show it here when the chart can't
          !hasGoalTrend && trendChip && h("span", { class: `chip chip-${trendChip[1]}` }, trendChip[0]))),
      h("div", { class: "small", style: { color: "var(--fg-2)" } },
        goal.recent_count ? `Met in ${goal.met_recent} of your last ${goal.recent_count} sets · latest ${fmt(goal.latest)}`
          : "No sets with this data yet."),
      chartEl,
      h("div", { style: { display: "flex", gap: "8px", marginTop: "8px" } },
        !goal.focus && h("button", { class: "btn btn-sm", onclick: async () => { await postJSON(`/api/goals/${goal.id}/focus`, {}); refresh(); } }, "Make focus"),
        h("button", { class: "btn btn-sm btn-quiet", onclick: async () => { await postJSON(`/api/goals/${goal.id}/archive`, {}); refresh(); } }, "Remove")));
  }

  function addGoalForm(metrics, existing, refresh) {
    const taken = new Set(existing.map((g) => g.metric));
    const options = Object.entries(metrics).filter(([k]) => !taken.has(k));
    if (!options.length) return null;
    const select = h("select", { "aria-label": "Habit to track" },
      options.map(([k, m]) => h("option", { value: k }, m.label)));
    const target = h("input", { type: "number", min: "0", step: "1", "aria-label": "Target", style: { width: "90px" } });
    const signEl = h("span", { class: "muted" });
    const sync = () => {
      const m = metrics[select.value];
      target.value = m.default;
      signEl.textContent = `${m.direction === "at_most" ? "at most" : "at least"}${m.unit === "%" ? " (%)" : m.unit === "s" ? " (seconds)" : ""}`;
    };
    select.addEventListener("change", sync);
    sync();
    const err = h("p", { class: "small", style: { color: "var(--bad)" }, role: "alert" });
    return h("form", { class: "goal-form", onsubmit: async (e) => {
      e.preventDefault();
      try { await postJSON("/api/goals", { metric: select.value, target: Number(target.value) }); refresh(); }
      catch (ex) { err.textContent = ex.message; }
    } }, select, signEl, target, h("button", { class: "btn btn-primary btn-sm", type: "submit" }, "Add goal"), err);
  }

  return { destroy() { charts.forEach((c) => c.destroy?.()); goalCharts.forEach((c) => c.destroy?.()); } };
}
