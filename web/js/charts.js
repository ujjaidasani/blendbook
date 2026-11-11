// Blendbook Chart Module
// Framework-free inline SVG charts styled strictly with design tokens.
// No innerHTML with data strings. Fully responsive and accessible.

const SVG_NS = "http://www.w3.org/2000/svg";

// ---------------------------------------------------------------------------
// Shared Tooltip
// ---------------------------------------------------------------------------
let sharedTooltip = null;

function getSharedTooltip() {
  if (typeof document === "undefined") return null;
  if (!sharedTooltip) {
    sharedTooltip = document.createElement("div");
    sharedTooltip.className = "db-chart-tooltip";
    Object.assign(sharedTooltip.style, {
      position: "fixed",
      zIndex: "99999",
      pointerEvents: "none",
      opacity: "0",
      transform: "translateY(4px)",
      transition: "opacity 120ms ease, transform 120ms ease",
      background: "var(--surface-3)",
      color: "var(--fg)",
      fontFamily: "var(--font)",
      fontSize: "12px",
      lineHeight: "1.4",
      padding: "6px 10px",
      borderRadius: "var(--r-sm)",
      border: "1px solid var(--line-strong)",
      boxShadow: "0 6px 20px rgba(0, 0, 0, 0.45)",
      fontVariantNumeric: "tabular-nums",
      maxWidth: "300px",
      wordBreak: "break-word",
      display: "none"
    });
    document.body.appendChild(sharedTooltip);
  }
  return sharedTooltip;
}

function showTooltip(content, clientX, clientY) {
  const tip = getSharedTooltip();
  if (!tip) return;
  tip.replaceChildren();

  if (typeof content === "string") {
    tip.textContent = content;
  } else if (Array.isArray(content)) {
    content.forEach((line, idx) => {
      if (idx > 0) tip.appendChild(document.createElement("br"));
      tip.appendChild(document.createTextNode(String(line)));
    });
  } else if (content instanceof Node) {
    tip.appendChild(content);
  }

  tip.style.display = "block";
  // Force layout before positioning so client dimensions are known
  tip.getBoundingClientRect();
  tip.style.opacity = "1";
  tip.style.transform = "translateY(0)";
  positionTooltip(clientX, clientY);
}

function positionTooltip(clientX, clientY) {
  const tip = getSharedTooltip();
  if (!tip || tip.style.display === "none") return;

  const rect = tip.getBoundingClientRect();
  const pad = 10;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let left = clientX + 12;
  let top = clientY - rect.height - 8;

  // Flip horizontally if overflowing right
  if (left + rect.width + pad > vw) {
    left = clientX - rect.width - 12;
  }
  // Clamp left
  if (left < pad) left = pad;

  // Flip vertically if overflowing top
  if (top < pad) {
    top = clientY + 16;
  }
  // Clamp bottom
  if (top + rect.height + pad > vh) {
    top = vh - rect.height - pad;
  }

  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function hideTooltip() {
  if (sharedTooltip) {
    sharedTooltip.style.opacity = "0";
    sharedTooltip.style.transform = "translateY(4px)";
    setTimeout(() => {
      if (sharedTooltip && sharedTooltip.style.opacity === "0") {
        sharedTooltip.style.display = "none";
      }
    }, 120);
  }
}

// ---------------------------------------------------------------------------
// SVG and DOM Helpers
// ---------------------------------------------------------------------------
function createSvgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v != null) {
      if (k === "style" && typeof v === "object") {
        Object.assign(el.style, v);
      } else {
        el.setAttribute(k, String(v));
      }
    }
  }
  return el;
}

function createTextEl(text, attrs = {}) {
  const el = createSvgEl("text", attrs);
  el.textContent = text != null ? String(text) : "";
  return el;
}

function addAnimationDefs(svg) {
  const defs = createSvgEl("defs");
  const style = createSvgEl("style");
  style.textContent = `
    @keyframes dbFadeIn { from { opacity: 0; } to { opacity: 1; } }
    @media (prefers-reduced-motion: no-preference) {
      .db-fade-in { animation: dbFadeIn 300ms var(--ease-out, ease-out); }
    }
    .db-mark:focus { outline: none; }
    .db-mark:focus-visible { stroke: var(--fg) !important; stroke-width: 2px !important; }
  `;
  defs.appendChild(style);
  svg.appendChild(defs);
}

function fmtDuration(sec) {
  if (sec == null || isNaN(sec)) return "—";
  sec = Math.round(sec);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function fmtBpm(b) {
  if (b == null || isNaN(b)) return "—";
  return Number(b).toFixed(b % 1 ? 1 : 0);
}

// ---------------------------------------------------------------------------
// 1. BPM Journey
// ---------------------------------------------------------------------------
export function bpmJourney(el, tracks = [], opts = {}) {
  let currentTracks = Array.isArray(tracks) ? tracks : [];
  let currentOpts = { ...opts };
  let currentWidth = 0;
  let disposed = false;

  function render() {
    if (disposed) return;
    el.replaceChildren();

    const rect = el.getBoundingClientRect();
    const width = Math.max(300, rect.width || el.clientWidth || 600);
    currentWidth = width;
    const height = 220;

    const svg = createSvgEl("svg", {
      role: "img",
      viewBox: `0 0 ${width} ${height}`,
      class: "db-fade-in",
      style: {
        width: "100%",
        height: `${height}px`,
        display: "block",
        overflow: "visible"
      }
    });
    addAnimationDefs(svg);

    const validTracks = currentTracks.filter(t => t && t.bpm != null && !isNaN(t.bpm));

    // Summary for aria-label
    let ariaSummary = "BPM journey chart with no data";
    if (validTracks.length > 0) {
      const bpms = validTracks.map(t => Number(t.bpm));
      const minB = Math.min(...bpms);
      const maxB = Math.max(...bpms);
      const jumps = [];
      if (Array.isArray(currentOpts.transitions)) {
        currentOpts.transitions.forEach(tr => {
          if (tr && Array.isArray(tr.flags) && tr.flags.includes("bpm_jump")) {
            const trackN = tr.n != null ? tr.n : (tr.from && tr.from.n);
            if (trackN != null) jumps.push(trackN);
          }
        });
      }
      let jumpText = "steady tempo";
      if (jumps.length === 1) {
        jumpText = `one big jump at track ${jumps[0]}`;
      } else if (jumps.length > 1) {
        jumpText = `${jumps.length} big jumps`;
      }
      ariaSummary = `BPM across ${currentTracks.length} tracks, from ${fmtBpm(minB)} to ${fmtBpm(maxB)}, ${jumpText}`;
    }
    svg.setAttribute("aria-label", ariaSummary);

    if (validTracks.length === 0) {
      const emptyText = createTextEl("No track BPM data available", {
        x: width / 2,
        y: height / 2,
        "text-anchor": "middle",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 13px;"
      });
      svg.appendChild(emptyText);
      el.appendChild(svg);
      return;
    }

    const marginTop = 20;
    const marginBottom = 34;
    const marginLeft = 44;
    const marginRight = 24;
    const plotWidth = Math.max(10, width - marginLeft - marginRight);
    const plotHeight = Math.max(10, height - marginTop - marginBottom);

    // X domain: offset_sec if all tracks have offset_sec, otherwise track n
    const hasAllOffsets = currentTracks.length > 1 &&
      currentTracks.every(t => t && typeof t.offset_sec === "number" && !isNaN(t.offset_sec));

    let minX, maxX;
    if (hasAllOffsets) {
      const offsets = currentTracks.map(t => t.offset_sec);
      minX = Math.min(...offsets);
      maxX = Math.max(...offsets);
    } else {
      const ns = currentTracks.map((t, i) => (t && typeof t.n === "number" ? t.n : i + 1));
      minX = Math.min(...ns);
      maxX = Math.max(...ns);
    }
    if (maxX === minX) maxX = minX + 1;

    function getX(track, index) {
      if (hasAllOffsets) {
        const val = track.offset_sec != null ? track.offset_sec : minX;
        return marginLeft + ((val - minX) / (maxX - minX)) * plotWidth;
      }
      const n = (track && typeof track.n === "number") ? track.n : (index + 1);
      return marginLeft + ((n - minX) / (maxX - minX)) * plotWidth;
    }

    // Y domain: tight domain (min-3 .. max+3)
    const rawMinBpm = Math.min(...validTracks.map(t => Number(t.bpm)));
    const rawMaxBpm = Math.max(...validTracks.map(t => Number(t.bpm)));
    let minBpm = Math.floor(rawMinBpm - 3);
    let maxBpm = Math.ceil(rawMaxBpm + 3);
    if (minBpm === maxBpm) {
      minBpm -= 3;
      maxBpm += 3;
    }

    function getY(bpm) {
      return marginTop + plotHeight - ((bpm - minBpm) / (maxBpm - minBpm)) * plotHeight;
    }

    // Y Axis Gridlines & Ticks
    const gridGroup = createSvgEl("g", { class: "grid-lines" });
    const bpmSpan = maxBpm - minBpm;
    let step = 5;
    if (bpmSpan > 35) step = 10;
    else if (bpmSpan <= 12) step = 2;

    const startTick = Math.ceil(minBpm / step) * step;
    for (let b = startTick; b <= maxBpm; b += step) {
      const y = getY(b);
      const line = createSvgEl("line", {
        x1: marginLeft,
        y1: y,
        x2: width - marginRight,
        y2: y,
        stroke: "var(--line)",
        "stroke-width": "1"
      });
      gridGroup.appendChild(line);

      const label = createTextEl(String(b), {
        x: marginLeft - 8,
        y: y + 4,
        "text-anchor": "end",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 11px; font-variant-numeric: tabular-nums;"
      });
      gridGroup.appendChild(label);
    }
    svg.appendChild(gridGroup);

    // X Axis Ticks
    const xGroup = createSvgEl("g", { class: "x-axis" });
    if (hasAllOffsets) {
      // Time ticks every 5 or 10 minutes
      const spanSec = maxX - minX;
      const tickStepSec = spanSec > 2400 ? 600 : 300;
      for (let sec = 0; sec <= maxX; sec += tickStepSec) {
        if (sec < minX) continue;
        const x = marginLeft + ((sec - minX) / (maxX - minX)) * plotWidth;
        const tickLine = createSvgEl("line", {
          x1: x,
          y1: height - marginBottom,
          x2: x,
          y2: height - marginBottom + 4,
          stroke: "var(--line)",
          "stroke-width": "1"
        });
        xGroup.appendChild(tickLine);

        const m = Math.floor(sec / 60);
        const text = createTextEl(`${m}m`, {
          x,
          y: height - marginBottom + 16,
          "text-anchor": "middle",
          fill: "var(--muted)",
          style: "font-family: var(--font); font-size: 11px; font-variant-numeric: tabular-nums;"
        });
        xGroup.appendChild(text);
      }
    } else {
      // Track number ticks
      const nSpan = maxX - minX;
      const nStep = nSpan > 20 ? 5 : (nSpan > 10 ? 2 : 1);
      for (let n = minX; n <= maxX; n += nStep) {
        const x = marginLeft + ((n - minX) / (maxX - minX)) * plotWidth;
        const tickLine = createSvgEl("line", {
          x1: x,
          y1: height - marginBottom,
          x2: x,
          y2: height - marginBottom + 4,
          stroke: "var(--line)",
          "stroke-width": "1"
        });
        xGroup.appendChild(tickLine);

        const text = createTextEl(`#${n}`, {
          x,
          y: height - marginBottom + 16,
          "text-anchor": "middle",
          fill: "var(--muted)",
          style: "font-family: var(--font); font-size: 11px; font-variant-numeric: tabular-nums;"
        });
        xGroup.appendChild(text);
      }
    }
    svg.appendChild(xGroup);

    // Line segments
    const linesGroup = createSvgEl("g", { class: "segments" });
    const transitions = Array.isArray(currentOpts.transitions) ? currentOpts.transitions : [];

    for (let i = 0; i < currentTracks.length - 1; i++) {
      const t1 = currentTracks[i];
      const t2 = currentTracks[i + 1];
      if (!t1 || t1.bpm == null || !t2 || t2.bpm == null) continue;

      const x1 = getX(t1, i);
      const y1 = getY(t1.bpm);
      const x2 = getX(t2, i + 1);
      const y2 = getY(t2.bpm);

      // Check if flagged bpm_jump in transitions
      let isJump = false;
      const tr = transitions.find(item =>
        (item.from && item.to && item.from.id != null && item.from.id === t1.id && item.to.id === t2.id) ||
        (item.n != null && (item.n === t1.n || item.n === i + 1))
      ) || transitions[i];

      if (tr && Array.isArray(tr.flags) && tr.flags.includes("bpm_jump")) {
        isJump = true;
      }

      const segment = createSvgEl("line", {
        x1,
        y1,
        x2,
        y2,
        stroke: isJump ? "var(--bad)" : "var(--accent)",
        "stroke-width": isJump ? "2.5" : "2",
        "stroke-linecap": "round"
      });
      linesGroup.appendChild(segment);
    }
    svg.appendChild(linesGroup);

    // Dots & Rings
    const dotsGroup = createSvgEl("g", { class: "dots" });
    currentTracks.forEach((track, index) => {
      if (!track || track.bpm == null || isNaN(track.bpm)) return;

      const cx = getX(track, index);
      const cy = getY(track.bpm);
      const trackN = track.n != null ? track.n : (index + 1);
      const isHighlighted = currentOpts.highlightN != null && trackN === currentOpts.highlightN;

      const dotGroup = createSvgEl("g", {
        class: "db-mark",
        tabindex: "0",
        role: "button",
        "aria-label": `Track ${trackN}: ${track.title || "Untitled"} by ${track.artist || "Unknown"}, ${fmtBpm(track.bpm)} BPM`,
        style: "cursor: pointer;"
      });

      // Highlight Ring
      if (isHighlighted) {
        const ring = createSvgEl("circle", {
          cx,
          cy,
          r: 9,
          fill: "none",
          stroke: "var(--fg)",
          "stroke-width": "2",
          class: "highlight-ring"
        });
        dotGroup.appendChild(ring);
      }

      // Invisible hit area for easy hover/tap
      const hitArea = createSvgEl("circle", {
        cx,
        cy,
        r: 14,
        fill: "transparent",
        stroke: "none"
      });
      dotGroup.appendChild(hitArea);

      // Dot circle
      const dot = createSvgEl("circle", {
        cx,
        cy,
        r: isHighlighted ? 5 : 4,
        fill: isHighlighted ? "var(--fg)" : "var(--surface)",
        stroke: "var(--accent)",
        "stroke-width": "2",
        style: "transition: r 120ms ease, fill 120ms ease;"
      });
      dotGroup.appendChild(dot);

      // Tooltip: "#n Title — Artist · 124 BPM"
      const titleStr = track.title || "Untitled";
      const artistStr = track.artist || "Unknown";
      const tooltipText = `#${trackN} ${titleStr} — ${artistStr} · ${fmtBpm(track.bpm)} BPM`;

      const selectTrack = () => {
        if (typeof currentOpts.onSelect === "function") {
          currentOpts.onSelect(trackN);
        }
      };

      dotGroup.addEventListener("click", selectTrack);
      dotGroup.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectTrack();
        }
      });

      dotGroup.addEventListener("pointerenter", (e) => {
        showTooltip(tooltipText, e.clientX, e.clientY);
      });
      dotGroup.addEventListener("pointermove", (e) => {
        positionTooltip(e.clientX, e.clientY);
      });
      dotGroup.addEventListener("pointerleave", hideTooltip);

      dotGroup.addEventListener("focus", () => {
        const rectBox = dotGroup.getBoundingClientRect();
        showTooltip(tooltipText, rectBox.left + rectBox.width / 2, rectBox.top);
      });
      dotGroup.addEventListener("blur", hideTooltip);

      dotsGroup.appendChild(dotGroup);
    });
    svg.appendChild(dotsGroup);

    el.appendChild(svg);
  }

  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const w = entry.contentRect.width;
      if (w > 0 && Math.abs(w - currentWidth) >= 1) {
        render();
      }
    }
  });
  ro.observe(el);

  render();

  return {
    update(newData, newOpts) {
      if (newData !== undefined && newData !== null) {
        currentTracks = Array.isArray(newData) ? newData : [];
      }
      if (newOpts) {
        currentOpts = { ...currentOpts, ...newOpts };
      }
      render();
    },
    destroy() {
      disposed = true;
      ro.disconnect();
      hideTooltip();
      el.replaceChildren();
    }
  };
}

// ---------------------------------------------------------------------------
// 2. On-Deck Bars
// ---------------------------------------------------------------------------
export function onDeckBars(el, tracks = [], opts = {}) {
  let currentTracks = Array.isArray(tracks) ? tracks : [];
  let currentOpts = { ...opts };
  let currentWidth = 0;
  let disposed = false;

  function render() {
    if (disposed) return;
    el.replaceChildren();

    const rect = el.getBoundingClientRect();
    const width = Math.max(300, rect.width || el.clientWidth || 600);
    currentWidth = width;

    const rowHeight = 32;
    const paddingY = 8;
    const totalHeight = Math.max(80, currentTracks.length * rowHeight + paddingY * 2);

    const svg = createSvgEl("svg", {
      role: "img",
      viewBox: `0 0 ${width} ${totalHeight}`,
      class: "db-fade-in",
      style: {
        width: "100%",
        height: `${totalHeight}px`,
        display: "block",
        overflow: "visible"
      }
    });
    addAnimationDefs(svg);

    // Aria summary
    const avgSec = currentTracks.length
      ? Math.round(currentTracks.reduce((acc, t) => acc + (t.on_deck_sec || 0), 0) / currentTracks.length)
      : 0;
    svg.setAttribute("aria-label", `On-deck playback bars for ${currentTracks.length} tracks, average ${avgSec}s on deck.`);

    if (currentTracks.length === 0) {
      const emptyText = createTextEl("No track duration data", {
        x: width / 2,
        y: totalHeight / 2,
        "text-anchor": "middle",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 13px;"
      });
      svg.appendChild(emptyText);
      el.appendChild(svg);
      return;
    }

    // Responsive columns
    const leftMargin = 8;
    const rightMargin = 8;
    const rightWidth = 92;
    const leftWidth = Math.min(320, Math.max(110, width * 0.42));
    const barX = leftMargin + leftWidth + 12;
    const barWidth = Math.max(20, width - barX - rightWidth - rightMargin);

    const maxSec = Math.max(1, ...currentTracks.map(t => Math.max(t?.length || 0, t?.on_deck_sec || 0)));

    currentTracks.forEach((track, index) => {
      const trackN = track.n != null ? track.n : (index + 1);
      const rowY = paddingY + index * rowHeight;
      const isHighlighted = currentOpts.highlightN != null && trackN === currentOpts.highlightN;

      const rowG = createSvgEl("g", {
        class: "db-mark track-row",
        tabindex: "0",
        role: "button",
        style: "cursor: pointer;"
      });

      // Background rect for row hover/highlight
      const bgRect = createSvgEl("rect", {
        x: 2,
        y: rowY,
        width: width - 4,
        height: rowHeight,
        rx: 4,
        style: {
          fill: isHighlighted ? "var(--surface-2)" : "transparent",
          stroke: isHighlighted ? "var(--line-strong)" : "transparent",
          strokeWidth: "1px",
          transition: "fill 120ms ease, stroke 120ms ease"
        }
      });
      rowG.appendChild(bgRect);

      // Left indicator strip for highlighted row
      if (isHighlighted) {
        const strip = createSvgEl("rect", {
          x: 2,
          y: rowY + 5,
          width: 3,
          height: rowHeight - 10,
          rx: 1.5,
          style: "fill: var(--accent);"
        });
        rowG.appendChild(strip);
      }

      // Left label: "n  Title" (truncate with ellipsis)
      const fullLeft = `${trackN}  ${track.title || "Untitled"}`;
      const availWidth = leftWidth - 8;
      const approxCharWidth = 6.6;
      const maxChars = Math.max(5, Math.floor(availWidth / approxCharWidth));
      let displayLeft = fullLeft;
      if (fullLeft.length > maxChars) {
        displayLeft = fullLeft.slice(0, maxChars - 1).trimEnd() + "…";
      }

      const leftText = createTextEl(displayLeft, {
        x: leftMargin + 4,
        y: rowY + rowHeight / 2 + 4,
        fill: isHighlighted ? "var(--accent)" : "var(--fg)",
        style: `font-family: var(--font); font-size: 12px; font-weight: ${isHighlighted ? "600" : "400"};`
      });
      rowG.appendChild(leftText);

      // Right label: "3:21 · 82%". null on_deck_sec shows "—".
      let rightTextStr = "—";
      if (track.on_deck_sec != null) {
        const timePart = fmtDuration(track.on_deck_sec);
        const pctPart = track.pct_played != null ? `${Math.round(track.pct_played)}%` : "—";
        rightTextStr = `${timePart} · ${pctPart}`;
      }

      const rightText = createTextEl(rightTextStr, {
        x: width - rightMargin - 4,
        y: rowY + rowHeight / 2 + 4,
        "text-anchor": "end",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 11px; font-variant-numeric: tabular-nums;"
      });
      rowG.appendChild(rightText);

      // Bars: faint track behind it = length, foreground bar = on_deck_sec
      const barH = 10;
      const barYPos = rowY + (rowHeight - barH) / 2;

      // Faint length bar behind
      if (track.length != null && track.length > 0) {
        const faintW = Math.max(3, (track.length / maxSec) * barWidth);
        const faintRect = createSvgEl("rect", {
          x: barX,
          y: barYPos,
          width: faintW,
          height: barH,
          rx: barH / 2,
          style: "fill: var(--surface-2); stroke: var(--line); stroke-width: 0.5px;"
        });
        rowG.appendChild(faintRect);
      }

      // Foreground on_deck_sec bar
      // Tint rule: on_deck_sec < 75 -> --warn, pct_played >= 95 -> --info, else --accent
      let barFill = "var(--accent)";
      if (track.on_deck_sec != null && track.on_deck_sec < 75) {
        barFill = "var(--warn)";
      } else if (track.pct_played != null && track.pct_played > 105) {
        barFill = "var(--bad)"; // next track came in after this one ended: possible gap
      } else if (track.pct_played != null && track.pct_played >= 95) {
        barFill = "var(--info)";
      }

      if (track.on_deck_sec != null && track.on_deck_sec > 0) {
        const deckW = Math.max(3, Math.min(barWidth, (track.on_deck_sec / maxSec) * barWidth));
        const deckRect = createSvgEl("rect", {
          x: barX,
          y: barYPos,
          width: deckW,
          height: barH,
          rx: barH / 2,
          style: `fill: ${barFill};`
        });
        rowG.appendChild(deckRect);
      }

      // Tooltip and accessibility
      const artist = track.artist || "Unknown";
      const tooltipLines = [
        `#${trackN} ${track.title || "Untitled"} — ${artist}`,
        `On deck: ${track.on_deck_sec != null ? fmtDuration(track.on_deck_sec) : "—"} (${track.pct_played != null ? Math.round(track.pct_played) + "%" : "—"})`,
        `Length: ${track.length != null ? fmtDuration(track.length) : "—"}`
      ];
      rowG.setAttribute("aria-label", `Track ${trackN}: ${track.title || "Untitled"} by ${artist}, on deck ${rightTextStr}`);

      const selectRow = () => {
        if (typeof currentOpts.onSelect === "function") {
          currentOpts.onSelect(trackN);
        }
      };

      rowG.addEventListener("click", selectRow);
      rowG.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectRow();
        }
      });

      rowG.addEventListener("pointerenter", (e) => {
        showTooltip(tooltipLines, e.clientX, e.clientY);
      });
      rowG.addEventListener("pointermove", (e) => {
        positionTooltip(e.clientX, e.clientY);
      });
      rowG.addEventListener("pointerleave", hideTooltip);

      rowG.addEventListener("focus", () => {
        const b = rowG.getBoundingClientRect();
        showTooltip(tooltipLines, b.left + b.width / 2, b.top);
      });
      rowG.addEventListener("blur", hideTooltip);

      svg.appendChild(rowG);
    });

    el.appendChild(svg);
  }

  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const w = entry.contentRect.width;
      if (w > 0 && Math.abs(w - currentWidth) >= 1) {
        render();
      }
    }
  });
  ro.observe(el);

  render();

  return {
    update(newData, newOpts) {
      if (newData !== undefined && newData !== null) {
        currentTracks = Array.isArray(newData) ? newData : [];
      }
      if (newOpts) {
        currentOpts = { ...currentOpts, ...newOpts };
      }
      render();
    },
    destroy() {
      disposed = true;
      ro.disconnect();
      hideTooltip();
      el.replaceChildren();
    }
  };
}

// ---------------------------------------------------------------------------
// 3. Session Timeline
// ---------------------------------------------------------------------------
export function sessionTimeline(el, timeline = [], opts = {}) {
  let currentTimeline = Array.isArray(timeline) ? timeline : [];
  let currentOpts = { metric: "tracks", ...opts };
  let currentWidth = 0;
  let disposed = false;

  function render() {
    if (disposed) return;
    el.replaceChildren();

    const rect = el.getBoundingClientRect();
    const width = Math.max(300, rect.width || el.clientWidth || 600);
    currentWidth = width;
    const height = 220;

    const svg = createSvgEl("svg", {
      role: "img",
      viewBox: `0 0 ${width} ${height}`,
      class: "db-fade-in",
      style: {
        width: "100%",
        height: `${height}px`,
        display: "block",
        overflow: "visible"
      }
    });
    addAnimationDefs(svg);

    const metric = currentOpts.metric || "tracks";
    const validSessions = currentTimeline.filter(s => s && s.date);

    svg.setAttribute("aria-label", `Session timeline showing ${metric} across ${validSessions.length} sessions.`);

    if (validSessions.length === 0) {
      const emptyText = createTextEl("No session timeline data", {
        x: width / 2,
        y: height / 2,
        "text-anchor": "middle",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 13px;"
      });
      svg.appendChild(emptyText);
      el.appendChild(svg);
      return;
    }

    const marginTop = 20;
    const marginBottom = 34;
    const marginLeft = 46;
    const marginRight = 20;
    const plotWidth = Math.max(10, width - marginLeft - marginRight);
    const plotHeight = Math.max(10, height - marginTop - marginBottom);

    // Time domain (real time spacing)
    const times = validSessions.map(s => new Date(`${s.date}T12:00:00`).getTime());
    const minTime = Math.min(...times);
    const maxTime = Math.max(...times);
    const bufferTime = 4 * 24 * 3600 * 1000; // 4 days buffer
    const startTime = minTime - bufferTime;
    const endTime = maxTime + bufferTime;
    const timeSpan = Math.max(1, endTime - startTime);

    function getBaseX(time) {
      return marginLeft + ((time - startTime) / timeSpan) * plotWidth;
    }

    // Offset same-date sessions so they don't overlap
    const dateCounts = {};
    validSessions.forEach(s => {
      dateCounts[s.date] = (dateCounts[s.date] || 0) + 1;
    });
    const dateIndices = {};

    // Y domain based on metric
    let minVal = 0;
    let maxVal = 1;
    let yTickValues = [];

    if (metric === "bpm") {
      const validMins = validSessions.map(s => s.bpm_min).filter(b => typeof b === "number" && !isNaN(b));
      const validMaxs = validSessions.map(s => s.bpm_max).filter(b => typeof b === "number" && !isNaN(b));
      minVal = validMins.length ? Math.floor(Math.min(...validMins) - 3) : 115;
      maxVal = validMaxs.length ? Math.ceil(Math.max(...validMaxs) + 3) : 155;
      if (maxVal === minVal) {
        minVal -= 3;
        maxVal += 3;
      }
      const span = maxVal - minVal;
      const step = span > 30 ? 10 : 5;
      const start = Math.ceil(minVal / step) * step;
      for (let b = start; b <= maxVal; b += step) {
        yTickValues.push({ val: b, label: String(b) });
      }
    } else if (metric === "smooth") {
      minVal = 0;
      maxVal = 100;
      yTickValues = [
        { val: 0, label: "0%" },
        { val: 50, label: "50%" },
        { val: 100, label: "100%" }
      ];
    } else if (metric === "duration") {
      minVal = 0;
      const maxSec = Math.max(...validSessions.map(s => s.duration_sec || 0));
      maxVal = Math.max(600, Math.ceil(maxSec / 600) * 600);
      const half = maxVal / 2;
      yTickValues = [
        { val: 0, label: "0m" },
        { val: half, label: `${Math.round(half / 60)}m` },
        { val: maxVal, label: `${Math.round(maxVal / 60)}m` }
      ];
    } else {
      // "tracks"
      minVal = 0;
      const maxT = Math.max(...validSessions.map(s => s.tracks || 0));
      maxVal = Math.max(5, Math.ceil(maxT / 5) * 5);
      const half = Math.round(maxVal / 2);
      yTickValues = [
        { val: 0, label: "0" },
        { val: half, label: String(half) },
        { val: maxVal, label: String(maxVal) }
      ];
    }

    function getY(v) {
      if (v == null || isNaN(v)) return marginTop + plotHeight;
      return marginTop + plotHeight - ((v - minVal) / (maxVal - minVal)) * plotHeight;
    }

    // Gridlines and Y ticks
    const gridG = createSvgEl("g", { class: "grid-lines" });
    yTickValues.forEach(t => {
      const y = getY(t.val);
      const line = createSvgEl("line", {
        x1: marginLeft,
        y1: y,
        x2: width - marginRight,
        y2: y,
        stroke: "var(--line)",
        "stroke-width": "1"
      });
      gridG.appendChild(line);

      const label = createTextEl(t.label, {
        x: marginLeft - 8,
        y: y + 4,
        "text-anchor": "end",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 11px; font-variant-numeric: tabular-nums;"
      });
      gridG.appendChild(label);
    });
    svg.appendChild(gridG);

    // Month tick labels
    const monthG = createSvgEl("g", { class: "month-ticks" });
    const curDate = new Date(minTime);
    curDate.setDate(1);
    curDate.setHours(0, 0, 0, 0);

    const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let lastMonthX = -100;

    while (curDate.getTime() <= maxTime + 15 * 24 * 3600 * 1000) {
      const mTime = curDate.getTime();
      const mX = getBaseX(mTime);

      if (mX >= marginLeft && mX <= width - marginRight) {
        if (mX - lastMonthX > 32) {
          const mLine = createSvgEl("line", {
            x1: mX,
            y1: height - marginBottom,
            x2: mX,
            y2: height - marginBottom + 4,
            stroke: "var(--line)",
            "stroke-width": "1"
          });
          monthG.appendChild(mLine);

          const mText = createTextEl(monthNames[curDate.getMonth()], {
            x: mX,
            y: height - marginBottom + 16,
            "text-anchor": "middle",
            fill: "var(--muted)",
            style: "font-family: var(--font); font-size: 11px;"
          });
          monthG.appendChild(mText);
          lastMonthX = mX;
        }
      }
      curDate.setMonth(curDate.getMonth() + 1);
    }
    svg.appendChild(monthG);

    // Render Sessions
    const sessionsG = createSvgEl("g", { class: "sessions" });

    validSessions.forEach(session => {
      const sDate = session.date;
      const countOnDate = dateCounts[sDate] || 1;
      const idxOnDate = dateIndices[sDate] || 0;
      dateIndices[sDate] = idxOnDate + 1;

      let offsetPx = 0;
      if (countOnDate > 1) {
        offsetPx = (idxOnDate - (countOnDate - 1) / 2) * 7;
      }

      const sTime = new Date(`${session.date}T12:00:00`).getTime();
      const x = Math.round(getBaseX(sTime) + offsetPx);
      const isSelected = currentOpts.selectedId != null && String(session.id) === String(currentOpts.selectedId);

      const sessG = createSvgEl("g", {
        class: "db-mark session-mark",
        tabindex: "0",
        role: "button",
        style: "cursor: pointer;"
      });

      // Background selection pillar
      if (isSelected) {
        const pillar = createSvgEl("rect", {
          x: x - 8,
          y: marginTop,
          width: 16,
          height: plotHeight,
          rx: 4,
          style: "fill: var(--surface-3); opacity: 0.7;"
        });
        sessG.appendChild(pillar);
      }

      // Invisible hit area
      const hitArea = createSvgEl("rect", {
        x: x - 6,
        y: marginTop,
        width: 12,
        height: plotHeight,
        fill: "transparent"
      });
      sessG.appendChild(hitArea);

      // Tooltip text formatting
      let metricSummary = "";
      if (metric === "bpm") {
        metricSummary = `BPM ${session.bpm_min}–${session.bpm_max} (median ${session.bpm_median})`;
      } else if (metric === "duration") {
        metricSummary = `${fmtDuration(session.duration_sec)} duration · ${session.tracks} tracks`;
      } else if (metric === "smooth") {
        metricSummary = `${session.smooth_tempo_pct}% smooth tempo`;
      } else {
        metricSummary = `${session.tracks} tracks`;
      }
      const tooltipLines = [
        `Session ${session.date}`,
        metricSummary
      ];

      if (metric === "bpm") {
        // "bpm" draws a min-max range bar with a median dot
        const yMin = getY(session.bpm_min != null ? session.bpm_min : minVal);
        const yMax = getY(session.bpm_max != null ? session.bpm_max : maxVal);
        const yMed = getY(session.bpm_median != null ? session.bpm_median : (session.bpm_min || minVal));

        const barLine = createSvgEl("line", {
          x1: x,
          y1: yMin,
          x2: x,
          y2: yMax,
          stroke: isSelected ? "var(--accent-2)" : "var(--accent)",
          "stroke-width": isSelected ? "4" : "2.5",
          "stroke-linecap": "round"
        });
        sessG.appendChild(barLine);

        const medianDot = createSvgEl("circle", {
          cx: x,
          cy: yMed,
          r: isSelected ? 4 : 2.5,
          fill: isSelected ? "var(--fg)" : "var(--accent-2)"
        });
        sessG.appendChild(medianDot);
      } else {
        // The others draw columns
        let val = 0;
        if (metric === "smooth") val = session.smooth_tempo_pct || 0;
        else if (metric === "duration") val = session.duration_sec || 0;
        else val = session.tracks || 0;

        const yVal = getY(val);
        const baselineY = getY(0);
        const colH = Math.max(2, baselineY - yVal);
        const colW = Math.max(4, Math.min(8, plotWidth / (validSessions.length * 1.3)));

        let colColor = "var(--accent)";
        if (isSelected) {
          colColor = "var(--accent-2)";
        } else if (metric === "smooth") {
          colColor = "var(--good)";
        }

        const colRect = createSvgEl("rect", {
          x: x - colW / 2,
          y: yVal,
          width: colW,
          height: colH,
          rx: 1.5,
          style: `fill: ${colColor}; transition: fill 120ms ease;`
        });
        sessG.appendChild(colRect);
      }

      sessG.setAttribute("aria-label", `Session on ${session.date}: ${metricSummary}`);

      const selectSession = () => {
        if (typeof currentOpts.onSelect === "function") {
          currentOpts.onSelect(session.id);
        }
      };

      sessG.addEventListener("click", selectSession);
      sessG.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectSession();
        }
      });

      sessG.addEventListener("pointerenter", (e) => {
        showTooltip(tooltipLines, e.clientX, e.clientY);
      });
      sessG.addEventListener("pointermove", (e) => {
        positionTooltip(e.clientX, e.clientY);
      });
      sessG.addEventListener("pointerleave", hideTooltip);

      sessG.addEventListener("focus", () => {
        const b = sessG.getBoundingClientRect();
        showTooltip(tooltipLines, b.left + b.width / 2, b.top);
      });
      sessG.addEventListener("blur", hideTooltip);

      sessionsG.appendChild(sessG);
    });
    svg.appendChild(sessionsG);

    el.appendChild(svg);
  }

  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const w = entry.contentRect.width;
      if (w > 0 && Math.abs(w - currentWidth) >= 1) {
        render();
      }
    }
  });
  ro.observe(el);

  render();

  return {
    update(newData, newOpts) {
      if (newData !== undefined && newData !== null) {
        currentTimeline = Array.isArray(newData) ? newData : [];
      }
      if (newOpts) {
        currentOpts = { ...currentOpts, ...newOpts };
      }
      render();
    },
    destroy() {
      disposed = true;
      ro.disconnect();
      hideTooltip();
      el.replaceChildren();
    }
  };
}

// ---------------------------------------------------------------------------
// 4. Genre Bar
// ---------------------------------------------------------------------------
export function genreBar(el, genres = [], opts = {}) {
  let currentGenres = Array.isArray(genres) ? genres : [];
  let currentOpts = { max: 6, ...opts };
  let currentWidth = 0;
  let disposed = false;

  const GENRE_PALETTE = [
    "var(--accent)",    // cyan / deck 1
    "var(--accent-2)",  // orange / deck 2
    "var(--good)",      // green
    "var(--info)",      // purple
    "var(--warn)",      // yellow
    "var(--bad)"        // red
  ];

  function render() {
    if (disposed) return;
    el.replaceChildren();

    const rect = el.getBoundingClientRect();
    const width = Math.max(300, rect.width || el.clientWidth || 600);
    currentWidth = width;

    // Process genres with max limit
    const max = currentOpts.max != null ? currentOpts.max : 6;
    const sorted = [...currentGenres]
      .filter(g => Array.isArray(g) && g[1] > 0)
      .sort((a, b) => b[1] - a[1]);

    let items = [];
    if (sorted.length > max) {
      const top = sorted.slice(0, max - 1);
      const otherCount = sorted.slice(max - 1).reduce((acc, it) => acc + (it[1] || 0), 0);
      items = [...top];
      if (otherCount > 0) {
        items.push(["Other", otherCount]);
      }
    } else {
      items = sorted;
    }

    const totalCount = items.reduce((acc, it) => acc + it[1], 0);

    // Initial estimation of height; will refine after legend layout
    const barY = 8;
    const barH = 22;
    const barRx = 6;
    const legendStartY = barY + barH + 16;

    // SVG root
    const svg = createSvgEl("svg", {
      role: "img",
      class: "db-fade-in",
      style: {
        width: "100%",
        display: "block",
        overflow: "visible"
      }
    });
    addAnimationDefs(svg);

    const summaryStr = items.map(it => `${it[1]} ${it[0]}`).join(", ");
    svg.setAttribute("aria-label", `Genre breakdown: ${summaryStr || "no data"}`);

    if (totalCount === 0 || items.length === 0) {
      const emptyH = 60;
      svg.setAttribute("viewBox", `0 0 ${width} ${emptyH}`);
      svg.style.height = `${emptyH}px`;
      const emptyText = createTextEl("No genre data available", {
        x: width / 2,
        y: emptyH / 2,
        "text-anchor": "middle",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 13px;"
      });
      svg.appendChild(emptyText);
      el.appendChild(svg);
      return;
    }

    // Clip path for rounded outer bar
    const clipId = `genre-clip-${Math.random().toString(36).slice(2, 9)}`;
    const defs = createSvgEl("defs");
    const clipPath = createSvgEl("clipPath", { id: clipId });
    const clipRect = createSvgEl("rect", {
      x: 0,
      y: barY,
      width,
      height: barH,
      rx: barRx
    });
    clipPath.appendChild(clipRect);
    defs.appendChild(clipPath);
    svg.appendChild(defs);

    // 100% Stacked bar container clipped to rounded rectangle
    const barGroup = createSvgEl("g", {
      "clip-path": `url(#${clipId})`
    });

    let cumX = 0;
    const segmentsG = createSvgEl("g", { class: "segments" });

    items.forEach((item, index) => {
      const [name, count] = item;
      const isOther = name === "Other";
      const color = isOther ? "var(--muted)" : GENRE_PALETTE[index % GENRE_PALETTE.length];
      const pct = (count / totalCount) * 100;
      const segW = (count / totalCount) * width;

      const segRect = createSvgEl("rect", {
        class: "db-mark",
        x: cumX,
        y: barY,
        width: segW,
        height: barH,
        tabindex: "0",
        role: "graphics-symbol",
        "aria-label": `${name}: ${count} tracks (${pct.toFixed(1)}%)`,
        style: `fill: ${color}; cursor: pointer; stroke: var(--bg); stroke-width: 1px;`
      });

      const tooltipText = `${name} · ${count} track${count === 1 ? "" : "s"} (${pct.toFixed(1)}%)`;

      segRect.addEventListener("pointerenter", (e) => {
        showTooltip(tooltipText, e.clientX, e.clientY);
      });
      segRect.addEventListener("pointermove", (e) => {
        positionTooltip(e.clientX, e.clientY);
      });
      segRect.addEventListener("pointerleave", hideTooltip);

      segRect.addEventListener("focus", () => {
        const b = segRect.getBoundingClientRect();
        showTooltip(tooltipText, b.left + b.width / 2, b.top);
      });
      segRect.addEventListener("blur", hideTooltip);

      barGroup.appendChild(segRect);
      cumX += segW;
    });
    svg.appendChild(barGroup);

    // Responsive Legend below bar with counts
    const legendG = createSvgEl("g", { class: "legend" });
    let curLegendX = 4;
    let curLegendY = legendStartY;
    const rowLineHeight = 22;

    items.forEach((item, index) => {
      const [name, count] = item;
      const isOther = name === "Other";
      const color = isOther ? "var(--muted)" : GENRE_PALETTE[index % GENRE_PALETTE.length];
      const pct = (count / totalCount) * 100;
      const tooltipText = `${name} · ${count} track${count === 1 ? "" : "s"} (${pct.toFixed(1)}%)`;

      // Approximate text width: 8px swatch + 6px gap + name chars*7 + 6px gap + count chars*7 + 16px padding
      const approxItemWidth = 14 + (name.length + String(count).length + 4) * 7.2 + 16;

      if (curLegendX + approxItemWidth > width && curLegendX > 4) {
        curLegendX = 4;
        curLegendY += rowLineHeight;
      }

      const itemG = createSvgEl("g", {
        class: "db-mark legend-item",
        tabindex: "0",
        role: "graphics-symbol",
        "aria-label": `${name}: ${count} tracks (${pct.toFixed(1)}%)`,
        style: "cursor: pointer;"
      });

      // Swatch
      const swatch = createSvgEl("rect", {
        x: curLegendX,
        y: curLegendY - 9,
        width: 10,
        height: 10,
        rx: 2,
        style: `fill: ${color};`
      });
      itemG.appendChild(swatch);

      // Name label
      const nameText = createTextEl(name, {
        x: curLegendX + 16,
        y: curLegendY,
        fill: "var(--fg)",
        style: "font-family: var(--font); font-size: 12px;"
      });
      itemG.appendChild(nameText);

      // Count label
      const countText = createTextEl(String(count), {
        x: curLegendX + 16 + (name.length * 7.2) + 6,
        y: curLegendY,
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 12px; font-variant-numeric: tabular-nums;"
      });
      itemG.appendChild(countText);

      itemG.addEventListener("pointerenter", (e) => {
        showTooltip(tooltipText, e.clientX, e.clientY);
      });
      itemG.addEventListener("pointermove", (e) => {
        positionTooltip(e.clientX, e.clientY);
      });
      itemG.addEventListener("pointerleave", hideTooltip);

      itemG.addEventListener("focus", () => {
        const b = itemG.getBoundingClientRect();
        showTooltip(tooltipText, b.left + b.width / 2, b.top);
      });
      itemG.addEventListener("blur", hideTooltip);

      legendG.appendChild(itemG);
      curLegendX += approxItemWidth;
    });

    svg.appendChild(legendG);

    const totalHeight = curLegendY + 12;
    svg.setAttribute("viewBox", `0 0 ${width} ${totalHeight}`);
    svg.style.height = `${totalHeight}px`;

    el.appendChild(svg);
  }

  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const w = entry.contentRect.width;
      if (w > 0 && Math.abs(w - currentWidth) >= 1) {
        render();
      }
    }
  });
  ro.observe(el);

  render();

  return {
    update(newData, newOpts) {
      if (newData !== undefined && newData !== null) {
        currentGenres = Array.isArray(newData) ? newData : [];
      }
      if (newOpts) {
        currentOpts = { ...currentOpts, ...newOpts };
      }
      render();
    },
    destroy() {
      disposed = true;
      ro.disconnect();
      hideTooltip();
      el.replaceChildren();
    }
  };
}

// ---------------------------------------------------------------------------
// 5. Sparkline
// ---------------------------------------------------------------------------
export function sparkline(el, values = [], opts = {}) {
  let currentValues = Array.isArray(values) ? values : [];
  let currentOpts = { color: "var(--accent)", ...opts };
  let currentWidth = 0;
  let disposed = false;

  function render() {
    if (disposed) return;
    el.replaceChildren();

    const rect = el.getBoundingClientRect();
    const width = Math.max(80, rect.width || el.clientWidth || 160);
    currentWidth = width;
    const height = 28; // Exact spec: 28px tall, no axes, last point dotted

    const svg = createSvgEl("svg", {
      role: "img",
      viewBox: `0 0 ${width} ${height}`,
      class: "db-fade-in",
      style: {
        width: "100%",
        height: `${height}px`,
        display: "block",
        overflow: "visible"
      }
    });
    addAnimationDefs(svg);

    const nums = currentValues.filter(v => typeof v === "number" && !isNaN(v));
    const strokeColor = currentOpts.color || "var(--accent)";

    if (nums.length === 0) {
      svg.setAttribute("aria-label", "Empty sparkline");
      el.appendChild(svg);
      return;
    }

    const minVal = Math.min(...nums);
    const maxVal = Math.max(...nums);
    const lastVal = nums[nums.length - 1];

    svg.setAttribute("aria-label", `Sparkline trend ending at ${fmtBpm(lastVal)}, range from ${fmtBpm(minVal)} to ${fmtBpm(maxVal)}`);

    const padX = 4;
    const padY = 4;
    const plotW = Math.max(1, width - padX * 2);
    const plotH = Math.max(1, height - padY * 2);
    const range = maxVal === minVal ? 1 : maxVal - minVal;

    if (nums.length === 1) {
      const cx = padX + plotW / 2;
      const cy = padY + plotH / 2;
      const singleDot = createSvgEl("circle", {
        cx,
        cy,
        r: 3,
        fill: strokeColor
      });
      svg.appendChild(singleDot);
      el.appendChild(svg);
      return;
    }

    // Build path
    const points = nums.map((val, idx) => {
      const x = padX + (idx / (nums.length - 1)) * plotW;
      const y = padY + plotH - ((val - minVal) / range) * plotH;
      return [x, y];
    });

    const d = points.reduce((acc, pt, i) => `${acc} ${i === 0 ? "M" : "L"} ${pt[0].toFixed(1)},${pt[1].toFixed(1)}`, "");

    const path = createSvgEl("path", {
      d,
      fill: "none",
      stroke: strokeColor,
      "stroke-width": "1.75",
      "stroke-linecap": "round",
      "stroke-linejoin": "round"
    });
    svg.appendChild(path);

    // Last point dotted
    const [lastX, lastY] = points[points.length - 1];
    const dotHalo = createSvgEl("circle", {
      cx: lastX,
      cy: lastY,
      r: 5,
      fill: "none",
      stroke: strokeColor,
      "stroke-width": "1",
      opacity: "0.35"
    });
    svg.appendChild(dotHalo);

    const dot = createSvgEl("circle", {
      cx: lastX,
      cy: lastY,
      r: 3,
      fill: strokeColor
    });
    svg.appendChild(dot);

    // Interactive tooltip
    const tooltipText = `Latest: ${fmtBpm(lastVal)} · Min: ${fmtBpm(minVal)} · Max: ${fmtBpm(maxVal)}`;
    svg.setAttribute("tabindex", "0");
    svg.style.cursor = "pointer";

    svg.addEventListener("pointerenter", (e) => {
      showTooltip(tooltipText, e.clientX, e.clientY);
    });
    svg.addEventListener("pointermove", (e) => {
      positionTooltip(e.clientX, e.clientY);
    });
    svg.addEventListener("pointerleave", hideTooltip);

    svg.addEventListener("focus", () => {
      const b = svg.getBoundingClientRect();
      showTooltip(tooltipText, b.left + b.width / 2, b.top);
    });
    svg.addEventListener("blur", hideTooltip);

    el.appendChild(svg);
  }

  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const w = entry.contentRect.width;
      if (w > 0 && Math.abs(w - currentWidth) >= 1) {
        render();
      }
    }
  });
  ro.observe(el);

  render();

  return {
    update(newData, newOpts) {
      if (newData !== undefined && newData !== null) {
        currentValues = Array.isArray(newData) ? newData : [];
      }
      if (newOpts) {
        currentOpts = { ...currentOpts, ...newOpts };
      }
      render();
    },
    destroy() {
      disposed = true;
      ro.disconnect();
      hideTooltip();
      el.replaceChildren();
    }
  };
}

// ---------------------------------------------------------------------------
// Helpers for Energy Lane & Goal Trend
// ---------------------------------------------------------------------------
function fmtMinutes(sec) {
  if (sec == null || isNaN(sec)) return "0m";
  sec = Math.round(sec);
  if (sec < 3600) {
    const m = Math.floor(sec / 60);
    return `${m}m`;
  }
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${String(m).padStart(2, "0")}m`;
}

function fmtShortDate(dateStr) {
  if (!dateStr) return "";
  if (typeof dateStr === "string") {
    const parts = dateStr.trim().split("-");
    if (parts.length === 3) {
      const day = parseInt(parts[2], 10);
      const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      const mIdx = parseInt(parts[1], 10) - 1;
      if (!isNaN(day) && mIdx >= 0 && mIdx < 12) {
        return `${day} ${months[mIdx]}`;
      }
    }
  }
  const d = new Date(dateStr);
  if (!isNaN(d.getTime())) {
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${d.getDate()} ${months[d.getMonth()]}`;
  }
  return String(dateStr);
}

// ---------------------------------------------------------------------------
// 6. Energy Lane
// ---------------------------------------------------------------------------
export function energyLane(el, data = {}, opts = {}) {
  let currentData = data || {};
  let currentOpts = { ...opts };
  let currentWidth = 0;
  let disposed = false;

  function render() {
    if (disposed) return;
    el.replaceChildren();

    const rect = el.getBoundingClientRect();
    const width = Math.max(300, rect.width || el.clientWidth || 600);
    currentWidth = width;
    const height = width < 480 ? 150 : 180;

    const svg = createSvgEl("svg", {
      role: "img",
      viewBox: `0 0 ${width} ${height}`,
      class: "db-fade-in",
      style: {
        width: "100%",
        height: `${height}px`,
        display: "block",
        overflow: "visible",
        cursor: "crosshair"
      }
    });
    addAnimationDefs(svg);

    const features = (currentData && currentData.features) ? currentData.features : (currentData || {});
    const loudnessDb = Array.isArray(features.loudness_db) ? features.loudness_db : [];
    const bands = features.bands || { low: [], mid: [], high: [] };
    const silences = Array.isArray(features.silences) ? features.silences : [];
    const hopSec = typeof features.hop_sec === "number" && features.hop_sec > 0 ? features.hop_sec : 1;
    const durationSec = typeof features.duration_sec === "number" && features.duration_sec > 0
      ? features.duration_sec
      : (loudnessDb.length > 0 ? loudnessDb.length * hopSec : 0);

    const transitions = Array.isArray(currentOpts.transitions)
      ? currentOpts.transitions
      : (Array.isArray(currentData.transitions) ? currentData.transitions : []);

    // Summary for aria-label
    let ariaSummary = "Energy and audio lane with no data";
    if (loudnessDb.length > 0) {
      const transCount = transitions.length;
      const silCount = silences.length;
      ariaSummary = `Audio energy lane over ${fmtMinutes(durationSec)}: loudness (-40 dB to 0 dB), 3-band balance, ${transCount} transitions, ${silCount} silence periods`;
    }
    svg.setAttribute("aria-label", ariaSummary);

    if (loudnessDb.length === 0 && durationSec === 0) {
      const emptyText = createTextEl("No audio features data available", {
        x: width / 2,
        y: height / 2,
        "text-anchor": "middle",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 13px;"
      });
      svg.appendChild(emptyText);
      el.appendChild(svg);
      return;
    }

    const marginLeft = 40;
    const marginRight = 18;
    const marginTop = 20; // Room for transition marker dots at top
    const marginBottom = 24; // Room for x-axis minutes
    const plotWidth = Math.max(10, width - marginLeft - marginRight);
    const plotHeight = Math.max(10, height - marginTop - marginBottom);

    // Split plot area into top half (loudness) and bottom half (band balance)
    const gap = 6;
    const availPlotHeight = Math.max(10, plotHeight - gap);
    const loudnessHeight = Math.round(availPlotHeight * 0.52);
    const loudnessTop = marginTop;
    const loudnessFloor = loudnessTop + loudnessHeight;

    const bandsTop = loudnessFloor + gap;
    const bandsFloor = marginTop + plotHeight;
    const bandsHeight = Math.max(1, bandsFloor - bandsTop);

    function getX(sec) {
      if (durationSec <= 0) return marginLeft;
      const s = Math.max(0, Math.min(durationSec, sec));
      return marginLeft + (s / durationSec) * plotWidth;
    }

    // Downsampling to pixel width
    const numBuckets = Math.max(2, Math.min(loudnessDb.length, Math.floor(plotWidth)));
    const bucketPoints = [];

    const lowArr = Array.isArray(bands.low) ? bands.low : [];
    const midArr = Array.isArray(bands.mid) ? bands.mid : [];
    const highArr = Array.isArray(bands.high) ? bands.high : [];

    for (let k = 0; k < numBuckets; k++) {
      const tStart = (k / numBuckets) * durationSec;
      const tEnd = ((k + 1) / numBuckets) * durationSec;
      const iStart = Math.floor(tStart / hopSec);
      let iEnd = Math.min(loudnessDb.length, Math.ceil(tEnd / hopSec));
      if (iEnd <= iStart) iEnd = Math.min(loudnessDb.length, iStart + 1);

      let sumDb = 0;
      let sumLow = 0;
      let sumMid = 0;
      let sumHigh = 0;
      let count = 0;

      for (let i = iStart; i < iEnd; i++) {
        sumDb += (typeof loudnessDb[i] === "number" && !isNaN(loudnessDb[i])) ? loudnessDb[i] : -40;
        sumLow += (typeof lowArr[i] === "number" && !isNaN(lowArr[i])) ? lowArr[i] : 0;
        sumMid += (typeof midArr[i] === "number" && !isNaN(midArr[i])) ? midArr[i] : 0;
        sumHigh += (typeof highArr[i] === "number" && !isNaN(highArr[i])) ? highArr[i] : 0;
        count++;
      }

      const meanDb = count > 0 ? sumDb / count : -40;
      const meanLow = count > 0 ? sumLow / count : 0;
      const meanMid = count > 0 ? sumMid / count : 0;
      const meanHigh = count > 0 ? sumHigh / count : 0;

      const clampedDb = Math.max(-40, Math.min(0, meanDb));
      const yLoudness = loudnessFloor - ((clampedDb - (-40)) / 40) * loudnessHeight;

      const bandSum = meanLow + meanMid + meanHigh;
      let yLow = bandsFloor;
      let yMid = bandsFloor;
      let yHigh = bandsFloor;

      if (bandSum > 0) {
        const lowFrac = meanLow / bandSum;
        const midFrac = meanMid / bandSum;
        yLow = bandsFloor - lowFrac * bandsHeight;
        yMid = yLow - midFrac * bandsHeight;
        yHigh = bandsTop;
      }

      const x = marginLeft + (k / (numBuckets - 1)) * plotWidth;
      bucketPoints.push({ x, yLoudness, yLow, yMid, yHigh });
    }

    // 1. Loudness Area & Line (Top Half)
    const loudnessGroup = createSvgEl("g", { class: "loudness-group" });

    // Subtle dB level labels on the left
    const dbTicks = [
      { val: 0, label: "0" },
      { val: -20, label: "-20" },
      { val: -40, label: "-40" }
    ];
    dbTicks.forEach(tick => {
      const y = loudnessFloor - ((tick.val - (-40)) / 40) * loudnessHeight;
      const tickLine = createSvgEl("line", {
        x1: marginLeft - 3,
        y1: y,
        x2: marginLeft,
        y2: y,
        stroke: "var(--line)",
        "stroke-width": "1"
      });
      loudnessGroup.appendChild(tickLine);

      const tickText = createTextEl(tick.label, {
        x: marginLeft - 5,
        y: y + 3,
        "text-anchor": "end",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 9px; font-variant-numeric: tabular-nums;"
      });
      loudnessGroup.appendChild(tickText);
    });

    // Floor line for loudness
    const loudnessFloorLine = createSvgEl("line", {
      x1: marginLeft,
      y1: loudnessFloor,
      x2: width - marginRight,
      y2: loudnessFloor,
      stroke: "var(--line)",
      "stroke-width": "1"
    });
    loudnessGroup.appendChild(loudnessFloorLine);

    // Loudness filled area path
    if (bucketPoints.length > 0) {
      let areaD = `M ${bucketPoints[0].x.toFixed(1)},${loudnessFloor.toFixed(1)} `;
      for (const pt of bucketPoints) {
        areaD += `L ${pt.x.toFixed(1)},${pt.yLoudness.toFixed(1)} `;
      }
      areaD += `L ${bucketPoints[bucketPoints.length - 1].x.toFixed(1)},${loudnessFloor.toFixed(1)} Z`;

      const loudnessArea = createSvgEl("path", {
        d: areaD,
        fill: "var(--accent)",
        "fill-opacity": "0.2"
      });
      loudnessGroup.appendChild(loudnessArea);

      // Loudness top stroke line
      let lineD = "";
      bucketPoints.forEach((pt, i) => {
        lineD += `${i === 0 ? "M" : "L"} ${pt.x.toFixed(1)},${pt.yLoudness.toFixed(1)} `;
      });
      const loudnessPath = createSvgEl("path", {
        d: lineD,
        fill: "none",
        stroke: "var(--accent)",
        "stroke-width": "1.5",
        "stroke-linejoin": "round"
      });
      loudnessGroup.appendChild(loudnessPath);
    }
    svg.appendChild(loudnessGroup);

    // 2. Band Balance (Bottom Half: 3 thin stacked areas: low / mid / high in --accent-2 / --info / --good)
    const bandsGroup = createSvgEl("g", { class: "bands-group" });

    // Subtle labels for HI / MID / LO on left
    const bandLabels = [
      { text: "HI", y: bandsTop + bandsHeight * 0.22 },
      { text: "MID", y: bandsTop + bandsHeight * 0.56 },
      { text: "LO", y: bandsTop + bandsHeight * 0.88 }
    ];
    bandLabels.forEach(bl => {
      const lbl = createTextEl(bl.text, {
        x: marginLeft - 5,
        y: bl.y,
        "text-anchor": "end",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 8px; font-weight: 500;"
      });
      bandsGroup.appendChild(lbl);
    });

    if (bucketPoints.length > 0) {
      // High band (top: between yHigh and yMid) in --good
      let highD = `M ${bucketPoints[0].x.toFixed(1)},${bucketPoints[0].yHigh.toFixed(1)} `;
      for (let i = 1; i < bucketPoints.length; i++) {
        highD += `L ${bucketPoints[i].x.toFixed(1)},${bucketPoints[i].yHigh.toFixed(1)} `;
      }
      for (let i = bucketPoints.length - 1; i >= 0; i--) {
        highD += `L ${bucketPoints[i].x.toFixed(1)},${bucketPoints[i].yMid.toFixed(1)} `;
      }
      highD += "Z";
      const highPath = createSvgEl("path", {
        d: highD,
        fill: "var(--good)",
        "fill-opacity": "0.32"
      });
      bandsGroup.appendChild(highPath);

      // Mid band (middle: between yMid and yLow) in --info
      let midD = `M ${bucketPoints[0].x.toFixed(1)},${bucketPoints[0].yMid.toFixed(1)} `;
      for (let i = 1; i < bucketPoints.length; i++) {
        midD += `L ${bucketPoints[i].x.toFixed(1)},${bucketPoints[i].yMid.toFixed(1)} `;
      }
      for (let i = bucketPoints.length - 1; i >= 0; i--) {
        midD += `L ${bucketPoints[i].x.toFixed(1)},${bucketPoints[i].yLow.toFixed(1)} `;
      }
      midD += "Z";
      const midPath = createSvgEl("path", {
        d: midD,
        fill: "var(--info)",
        "fill-opacity": "0.32"
      });
      bandsGroup.appendChild(midPath);

      // Low band (bottom: between yLow and bandsFloor) in --accent-2
      let lowD = `M ${bucketPoints[0].x.toFixed(1)},${bucketPoints[0].yLow.toFixed(1)} `;
      for (let i = 1; i < bucketPoints.length; i++) {
        lowD += `L ${bucketPoints[i].x.toFixed(1)},${bucketPoints[i].yLow.toFixed(1)} `;
      }
      lowD += `L ${bucketPoints[bucketPoints.length - 1].x.toFixed(1)},${bandsFloor.toFixed(1)} `;
      lowD += `L ${bucketPoints[0].x.toFixed(1)},${bandsFloor.toFixed(1)} Z`;
      const lowPath = createSvgEl("path", {
        d: lowD,
        fill: "var(--accent-2)",
        "fill-opacity": "0.32"
      });
      bandsGroup.appendChild(lowPath);

      // Thin boundary lines between the bands for clarity
      let divHighD = "";
      let divMidD = "";
      bucketPoints.forEach((pt, i) => {
        divHighD += `${i === 0 ? "M" : "L"} ${pt.x.toFixed(1)},${pt.yHigh.toFixed(1)} `;
        divMidD += `${i === 0 ? "M" : "L"} ${pt.x.toFixed(1)},${pt.yMid.toFixed(1)} `;
      });
      bandsGroup.appendChild(createSvgEl("path", {
        d: divHighD,
        fill: "none",
        stroke: "var(--good)",
        "stroke-width": "0.75",
        "stroke-opacity": "0.6"
      }));
      bandsGroup.appendChild(createSvgEl("path", {
        d: divMidD,
        fill: "none",
        stroke: "var(--info)",
        "stroke-width": "0.75",
        "stroke-opacity": "0.6"
      }));
    }
    svg.appendChild(bandsGroup);

    // 3. Silences as --bad translucent bands across full height
    const silencesGroup = createSvgEl("g", { class: "silences-group" });
    silences.forEach(sil => {
      if (!Array.isArray(sil) || sil.length < 2) return;
      const [startSec, endSec] = sil;
      const sx1 = getX(startSec);
      const sx2 = getX(endSec);
      const sw = Math.max(2, sx2 - sx1);

      const silRect = createSvgEl("rect", {
        x: sx1,
        y: marginTop,
        width: sw,
        height: plotHeight,
        fill: "var(--bad)",
        "fill-opacity": "0.25",
        style: "cursor: pointer;"
      });
      silencesGroup.appendChild(silRect);

      if (sw >= 28) {
        const silLabel = createTextEl("silence", {
          x: sx1 + sw / 2,
          y: marginTop + plotHeight / 2,
          "text-anchor": "middle",
          "dominant-baseline": "middle",
          fill: "var(--bad)",
          style: "font-family: var(--font); font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; pointer-events: none;"
        });
        silencesGroup.appendChild(silLabel);
      }

      const silTip = `Silence: ${fmtDuration(startSec)} – ${fmtDuration(endSec)} (${Math.round(endSec - startSec)}s)`;
      silRect.addEventListener("pointerenter", (e) => showTooltip(silTip, e.clientX, e.clientY));
      silRect.addEventListener("pointermove", (e) => positionTooltip(e.clientX, e.clientY));
      silRect.addEventListener("pointerleave", hideTooltip);
    });
    svg.appendChild(silencesGroup);

    // 4. Transition Markers
    const transitionsGroup = createSvgEl("g", { class: "transitions-group" });
    const isHighlightedN = currentOpts.highlightN != null ? currentOpts.highlightN : null;

    transitions.forEach(tr => {
      if (!tr || tr.at_sec == null) return;
      const tx = getX(tr.at_sec);
      const audio = tr.audio || {};
      const isHigh = isHighlightedN != null && tr.n === isHighlightedN;

      // Color logic:
      // --bad if beat_drift === "drift" or silence_sec > 0
      // --warn if |loudness_step_db| >= 3
      // otherwise --muted
      let dotColor = "var(--muted)";
      if (audio.beat_drift === "drift" || (audio.silence_sec != null && audio.silence_sec > 0)) {
        dotColor = "var(--bad)";
      } else if (audio.loudness_step_db != null && Math.abs(audio.loudness_step_db) >= 3) {
        dotColor = "var(--warn)";
      }

      // Vertical 1px --line-strong line
      const line = createSvgEl("line", {
        x1: tx,
        y1: marginTop,
        x2: tx,
        y2: marginTop + plotHeight,
        stroke: isHigh ? "var(--fg)" : "var(--line-strong)",
        "stroke-width": isHigh ? "2" : "1",
        "pointer-events": "none"
      });
      transitionsGroup.appendChild(line);

      // Tooltip parts:
      // "Beats drifted · +3.4 dB · 4 s silence" (only parts that apply; "beats steady" when steady, "beat match unclear" when unclear)
      const parts = [];
      if (audio.beat_drift === "drift") {
        parts.push("Beats drifted");
      } else if (audio.beat_drift === "steady") {
        parts.push("beats steady");
      } else if (audio.beat_drift === "unclear") {
        parts.push("beat match unclear");
      }

      if (audio.loudness_step_db != null && !isNaN(audio.loudness_step_db)) {
        const db = Number(audio.loudness_step_db);
        parts.push(`${db > 0 ? "+" : ""}${db.toFixed(1)} dB`);
      }

      if (audio.silence_sec != null && audio.silence_sec > 0) {
        parts.push(`${audio.silence_sec} s silence`);
      }

      const tipTitle = tr.label || `Transition #${tr.n}`;
      const tipDesc = parts.length > 0 ? parts.join(" · ") : "";
      const tooltipContent = tipDesc ? [tipTitle, tipDesc] : tipTitle;

      // Dot group
      const dotGroup = createSvgEl("g", {
        class: "db-mark",
        tabindex: "0",
        role: "button",
        "aria-label": `${tipTitle}${tipDesc ? ": " + tipDesc : ""}`,
        style: "cursor: pointer;"
      });

      const dotCy = marginTop;

      // Invisible larger hit circle for comfortable clicking / tapping
      const hitCircle = createSvgEl("circle", {
        cx: tx,
        cy: dotCy,
        r: 10,
        fill: "transparent"
      });
      dotGroup.appendChild(hitCircle);

      if (isHigh) {
        const halo = createSvgEl("circle", {
          cx: tx,
          cy: dotCy,
          r: 8,
          fill: "none",
          stroke: dotColor,
          "stroke-width": "2",
          opacity: "0.6"
        });
        dotGroup.appendChild(halo);
      }

      const dot = createSvgEl("circle", {
        cx: tx,
        cy: dotCy,
        r: isHigh ? 5.5 : 4,
        fill: dotColor,
        stroke: isHigh ? "var(--fg)" : "var(--surface)",
        "stroke-width": "1"
      });
      dotGroup.appendChild(dot);

      function selectTransition() {
        if (typeof currentOpts.onSelect === "function") {
          currentOpts.onSelect(tr.n);
        }
      }

      dotGroup.addEventListener("click", (e) => {
        e.stopPropagation();
        selectTransition();
      });

      dotGroup.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          e.stopPropagation();
          selectTransition();
        }
      });

      dotGroup.addEventListener("pointerenter", (e) => {
        showTooltip(tooltipContent, e.clientX, e.clientY);
      });
      dotGroup.addEventListener("pointermove", (e) => {
        positionTooltip(e.clientX, e.clientY);
      });
      dotGroup.addEventListener("pointerleave", hideTooltip);

      dotGroup.addEventListener("focus", () => {
        const svgBox = svg.getBoundingClientRect();
        showTooltip(tooltipContent, svgBox.left + tx, svgBox.top + dotCy - 8);
      });
      dotGroup.addEventListener("blur", hideTooltip);

      transitionsGroup.appendChild(dotGroup);
    });
    svg.appendChild(transitionsGroup);

    // 5. X Axis (Minutes: 0m … 55m, or 1h 05m past an hour)
    const xAxisGroup = createSvgEl("g", { class: "x-axis-group" });

    // Baseline
    const xBaseLine = createSvgEl("line", {
      x1: marginLeft,
      y1: bandsFloor,
      x2: width - marginRight,
      y2: bandsFloor,
      stroke: "var(--line)",
      "stroke-width": "1"
    });
    xAxisGroup.appendChild(xBaseLine);

    // Pick tick interval based on durationSec and width
    let tickStepSec = 300; // 5 min
    if (durationSec <= 600) tickStepSec = 60; // 1 min
    else if (durationSec <= 1800) tickStepSec = 300; // 5 min
    else if (durationSec <= 3600) tickStepSec = 600; // 10 min
    else if (durationSec <= 7200) tickStepSec = 900; // 15 min
    else tickStepSec = 1800; // 30 min

    if (width < 480) {
      tickStepSec *= 2;
    }

    for (let t = 0; t <= durationSec; t += tickStepSec) {
      const tx = getX(t);
      if (tx < marginLeft - 2 || tx > width - marginRight + 2) continue;

      const tickMark = createSvgEl("line", {
        x1: tx,
        y1: bandsFloor,
        x2: tx,
        y2: bandsFloor + 4,
        stroke: "var(--line)",
        "stroke-width": "1"
      });
      xAxisGroup.appendChild(tickMark);

      const tickLabel = createTextEl(fmtMinutes(t), {
        x: tx,
        y: bandsFloor + 16,
        "text-anchor": "middle",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 11px; font-variant-numeric: tabular-nums;"
      });
      xAxisGroup.appendChild(tickLabel);
    }
    svg.appendChild(xAxisGroup);

    // Clicking or tapping empty chart area calls onSeek(sec)
    svg.addEventListener("click", (e) => {
      if (e.target.closest(".db-mark") || e.target.closest("button")) return;
      const svgBox = svg.getBoundingClientRect();
      const clickX = e.clientX - svgBox.left;
      if (clickX >= marginLeft && clickX <= width - marginRight) {
        const frac = Math.max(0, Math.min(1, (clickX - marginLeft) / plotWidth));
        const seekSec = Math.round(frac * durationSec);
        if (typeof currentOpts.onSeek === "function") {
          currentOpts.onSeek(seekSec);
        }
      }
    });

    el.appendChild(svg);
  }

  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const w = entry.contentRect.width;
      if (w > 0 && Math.abs(w - currentWidth) >= 1) {
        render();
      }
    }
  });
  ro.observe(el);

  render();

  return {
    update(newData, newOpts) {
      if (newData !== undefined && newData !== null) {
        currentData = newData;
      }
      if (newOpts) {
        currentOpts = { ...currentOpts, ...newOpts };
      }
      render();
    },
    destroy() {
      disposed = true;
      ro.disconnect();
      hideTooltip();
      el.replaceChildren();
    }
  };
}

// ---------------------------------------------------------------------------
// 7. Goal Trend
// ---------------------------------------------------------------------------
export function goalTrend(el, data = {}, opts = {}) {
  let currentData = data || {};
  let currentOpts = { last: 10, ...opts };
  let currentWidth = 0;
  let disposed = false;
  let currentTrend = null;

  function render() {
    if (disposed) return;
    el.replaceChildren();

    const rect = el.getBoundingClientRect();
    const width = Math.max(280, rect.width || el.clientWidth || 500);
    currentWidth = width;
    const height = 160;

    const svg = createSvgEl("svg", {
      role: "img",
      viewBox: `0 0 ${width} ${height}`,
      class: "db-fade-in",
      style: {
        width: "100%",
        height: `${height}px`,
        display: "block",
        overflow: "visible"
      }
    });
    addAnimationDefs(svg);

    const validPoints = (Array.isArray(currentData && currentData.points) ? currentData.points : [])
      .filter(p => p && p.value != null && !isNaN(p.value));

    // Direction-aware trend computation from last 5 vs 5 before
    let computedTrend = null;
    let trendLabel = "";
    let trendColor = "var(--muted)";

    if (validPoints.length >= 10) {
      const recent5 = validPoints.slice(-5);
      const prev5 = validPoints.slice(-10, -5);
      const sumRecent = recent5.reduce((acc, p) => acc + Number(p.value), 0);
      const sumPrev = prev5.reduce((acc, p) => acc + Number(p.value), 0);
      const avgRecent = sumRecent / 5;
      const avgPrev = sumPrev / 5;
      const diff = avgRecent - avgPrev;
      const dir = (currentData && currentData.direction) || "at_most";

      if (Math.abs(diff) < 0.001) {
        computedTrend = "steady";
        trendLabel = "Steady";
        trendColor = "var(--fg-2)";
      } else if (dir === "at_most") {
        if (diff < 0) {
          computedTrend = "improving";
          trendLabel = "Improving";
          trendColor = "var(--good)";
        } else {
          computedTrend = "slipping";
          trendLabel = "Slipping";
          trendColor = "var(--warn)";
        }
      } else {
        if (diff > 0) {
          computedTrend = "improving";
          trendLabel = "Improving";
          trendColor = "var(--good)";
        } else {
          computedTrend = "slipping";
          trendLabel = "Slipping";
          trendColor = "var(--warn)";
        }
      }
    }
    currentTrend = computedTrend;

    const lastCount = typeof currentOpts.last === "number" && currentOpts.last > 0 ? currentOpts.last : 10;
    const displayedPoints = validPoints.slice(-lastCount);

    const targetVal = typeof currentData.target === "number" ? currentData.target : 0;
    const dir = (currentData && currentData.direction) || "at_most";
    const sym = dir === "at_least" ? "≥" : "≤";
    const suffix = (currentData && currentData.unit === "%") ? "%" : "";
    const targetText = `Goal ${sym} ${targetVal}${suffix}`;

    // aria-label
    let ariaSummary = "Goal trend chart with no data";
    if (displayedPoints.length > 0) {
      const latest = displayedPoints[displayedPoints.length - 1];
      ariaSummary = `${currentData.label || "Goal trend"}: ${displayedPoints.length} sets shown, target ${targetText}, latest value ${latest.value}${suffix}, trend ${computedTrend || "not determined"}`;
    }
    svg.setAttribute("aria-label", ariaSummary);

    if (displayedPoints.length === 0) {
      const emptyText = createTextEl("No goal data points available", {
        x: width / 2,
        y: height / 2,
        "text-anchor": "middle",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 13px;"
      });
      svg.appendChild(emptyText);
      el.appendChild(svg);
      return;
    }

    const marginTop = 28;
    const marginBottom = 28;
    const marginLeft = 24;
    const marginRight = 24;
    const plotWidth = Math.max(10, width - marginLeft - marginRight);
    const plotHeight = Math.max(10, height - marginTop - marginBottom);
    const baselineY = marginTop + plotHeight;

    // Y scaling
    const maxVal = Math.max(targetVal, ...displayedPoints.map(p => Number(p.value)), 1);
    const yMax = Math.ceil(maxVal * 1.3);

    function getY(v) {
      return baselineY - (Math.max(0, Number(v)) / yMax) * plotHeight;
    }

    // Header area inside SVG: Title & Trend Note
    if (currentData.label) {
      const titleEl = createTextEl(currentData.label, {
        x: marginLeft,
        y: 16,
        fill: "var(--fg)",
        style: "font-family: var(--font); font-size: 12px; font-weight: 600;"
      });
      svg.appendChild(titleEl);
    }

    if (trendLabel) {
      const trendEl = createSvgEl("text", {
        x: width - marginRight,
        y: 16,
        "text-anchor": "end",
        style: "font-family: var(--font); font-size: 12px; font-weight: 600;"
      });
      const tspanMuted = createSvgEl("tspan", { fill: "var(--muted)" });
      tspanMuted.textContent = "Trend: ";
      trendEl.appendChild(tspanMuted);

      const tspanVal = createSvgEl("tspan", { fill: trendColor });
      tspanVal.textContent = trendLabel;
      trendEl.appendChild(tspanVal);

      svg.appendChild(trendEl);
    }

    // Dashed horizontal target line in --fg-2
    const targetY = getY(targetVal);
    const targetLine = createSvgEl("line", {
      x1: marginLeft,
      y1: targetY,
      x2: width - marginRight,
      y2: targetY,
      stroke: "var(--fg-2)",
      "stroke-width": "1.25",
      "stroke-dasharray": "4 3"
    });
    svg.appendChild(targetLine);

    // Label sits at the left end of the line on a backing, so value labels on the right never collide with it
    const targetLabelY = targetY - 4 < marginTop ? targetY + 12 : targetY - 4;
    const labelW = targetText.length * 6.2 + 10;
    svg.appendChild(createSvgEl("rect", {
      x: marginLeft, y: targetLabelY - 11, width: labelW, height: 15, rx: 4,
      style: "fill: var(--surface-2); opacity: 0.92;"
    }));
    const targetLabelEl = createTextEl(targetText, {
      x: marginLeft + 5,
      y: targetLabelY,
      "text-anchor": "start",
      fill: "var(--fg-2)",
      style: "font-family: var(--font); font-size: 11px; font-weight: 500; font-variant-numeric: tabular-nums;"
    });
    svg.appendChild(targetLabelEl);

    // Baseline
    const baseLine = createSvgEl("line", {
      x1: marginLeft,
      y1: baselineY,
      x2: width - marginRight,
      y2: baselineY,
      stroke: "var(--line)",
      "stroke-width": "1"
    });
    svg.appendChild(baseLine);

    // Columns
    const N = displayedPoints.length;
    const slotW = plotWidth / N;
    const barW = Math.min(32, Math.max(10, slotW * 0.52));

    displayedPoints.forEach((point, idx) => {
      const val = Number(point.value);
      const barH = (val / yMax) * plotHeight;
      const barX = marginLeft + idx * slotW + (slotW - barW) / 2;
      const barY = baselineY - barH;

      const meets = dir === "at_least" ? val >= targetVal : val <= targetVal;
      const colColor = meets ? "var(--good)" : "var(--warn)";

      // Tooltip: "16 Oct · 2 tempo jumps · goal ≤ 1"
      let countPhrase = "";
      if (currentData.metric === "bpm_jumps") {
        countPhrase = val === 1 ? "1 tempo jump" : `${val} tempo jumps`;
      } else if (currentData.unit === "%") {
        countPhrase = `${val}%`;
      } else if (currentData.unit && currentData.unit !== "per set") {
        countPhrase = `${val} ${currentData.unit}`;
      } else {
        countPhrase = `${val} ${currentData.label || ""}`.trim();
      }

      const dateStr = fmtShortDate(point.date);
      const tipText = `${dateStr} · ${countPhrase} · goal ${sym} ${targetVal}${suffix}`;

      const colGroup = createSvgEl("g", {
        class: "db-mark",
        tabindex: "0",
        role: "button",
        "aria-label": tipText,
        style: "cursor: pointer;"
      });

      // Invisible hit area for the slot
      const hitRect = createSvgEl("rect", {
        x: marginLeft + idx * slotW,
        y: marginTop,
        width: slotW,
        height: plotHeight + marginBottom,
        fill: "transparent"
      });
      colGroup.appendChild(hitRect);

      // The column rect
      if (val === 0) {
        const zeroBar = createSvgEl("rect", {
          x: barX,
          y: baselineY - 2.5,
          width: barW,
          height: 2.5,
          rx: "1.25",
          ry: "1.25",
          fill: colColor
        });
        colGroup.appendChild(zeroBar);
      } else {
        const barRect = createSvgEl("rect", {
          x: barX,
          y: barY,
          width: barW,
          height: barH,
          rx: "3",
          ry: "3",
          fill: colColor
        });
        colGroup.appendChild(barRect);
      }

      // Value label above column
      const valLabelY = Math.min(baselineY - 6, barY - 4);
      const valLabel = createTextEl(`${val}${suffix}`, {
        x: barX + barW / 2,
        y: valLabelY,
        "text-anchor": "middle",
        fill: "var(--fg-2)",
        style: "font-family: var(--font); font-size: 11px; font-weight: 500; font-variant-numeric: tabular-nums;"
      });
      colGroup.appendChild(valLabel);

      // Date label below column
      const dateLabel = createTextEl(dateStr, {
        x: barX + barW / 2,
        y: baselineY + 16,
        "text-anchor": "middle",
        fill: "var(--muted)",
        style: "font-family: var(--font); font-size: 11px; font-variant-numeric: tabular-nums;"
      });
      colGroup.appendChild(dateLabel);

      function selectSession() {
        if (typeof currentOpts.onSelect === "function") {
          currentOpts.onSelect(point.session_id);
        }
      }

      colGroup.addEventListener("click", selectSession);
      colGroup.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectSession();
        }
      });

      colGroup.addEventListener("pointerenter", (e) => showTooltip(tipText, e.clientX, e.clientY));
      colGroup.addEventListener("pointermove", (e) => positionTooltip(e.clientX, e.clientY));
      colGroup.addEventListener("pointerleave", hideTooltip);

      colGroup.addEventListener("focus", () => {
        const svgBox = svg.getBoundingClientRect();
        showTooltip(tipText, svgBox.left + barX + barW / 2, svgBox.top + barY - 6);
      });
      colGroup.addEventListener("blur", hideTooltip);

      svg.appendChild(colGroup);
    });

    el.appendChild(svg);
  }

  const ro = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const w = entry.contentRect.width;
      if (w > 0 && Math.abs(w - currentWidth) >= 1) {
        render();
      }
    }
  });
  ro.observe(el);

  render();

  const instance = {
    get trend() {
      return currentTrend;
    },
    update(newData, newOpts) {
      if (newData !== undefined && newData !== null) {
        currentData = newData;
      }
      if (newOpts) {
        currentOpts = { ...currentOpts, ...newOpts };
      }
      render();
    },
    destroy() {
      disposed = true;
      ro.disconnect();
      hideTooltip();
      el.replaceChildren();
    }
  };

  return instance;
}

