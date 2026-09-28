#!/usr/bin/env node
/**
 * SciWeave workflow flowchart.
 *
 * Pure Node, no dependencies: builds the diagram as SVG, writes
 * flowchart.svg and flowchart.html, then rasterises flowchart.png with
 * headless Chrome / Edge (same approach as crazyAI's assets/flowchart).
 *
 *   node assets/flowchart/flowchart.js          # -> assets/flowchart/flowchart.{svg,html,png}
 *   SCALE=3 node assets/flowchart/flowchart.js  # sharper PNG
 */

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { pathToFileURL } = require("url");

// ------------------------------------------------------------- palette (from the logo)
const C = {
  bg0: "#0e1533", bg1: "#1a2350", glow: "#26306a",
  card: "#1c2552", cardEdge: "#34407e",
  claude: "#f0a94a",    // amber: Claude does it
  you: "#e2644a",       // coral: you decide
  tool: "#44b3ad",      // teal: sciweave does it
  see: "#4f8fdb",       // blue: you see it
  red: "#e5534b", green: "#52b36b", stale: "#ec835a", star: "#fab219",
  text: "#f3f5ff", muted: "#aab2d8", line: "#8791c8",
};

// ------------------------------------------------------------- icons (24x24 stroke paths)
const ICON = {
  chat: "M4 5h16v10H9l-5 4zM8 9h8M8 12h5",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  ask: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5V14M12 17.2v.1",
  plan: "M14 3H6v18h12V7zM14 3v4h4M9 12h6M9 16h6",
  check: "M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6zM8.5 12l2.5 2.5 4.5-5",
  db: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  net: "M6 6a2.5 2.5 0 1 0 0 .1M18 5a2.5 2.5 0 1 0 0 .1M12 18a2.5 2.5 0 1 0 0 .1M8 7l3 9M16 7l-3 9M8.5 5.6h7",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
};

// ------------------------------------------------------------- steps
const steps = [
  { n: 1, title: "WORK", who: "you + Claude", kind: "claude", icon: "chat", file: "any project",
    lines: ["analysis in a Claude session:", "pipelines, models, tables, plots, text", "\u201cadd this to my SciWeave project\u201d"] },
  { n: 2, title: "INSPECT", who: "Claude \u00b7 sciweave skill", kind: "claude", icon: "search", file: "SCIWEAVE.md",
    lines: ["reads the project map, then", "inventories outputs, scripts,", "configs & commands of the session"] },
  { n: 3, title: "ASK", who: "Claude \u2192 you", kind: "you", icon: "ask", file: "2\u20134 questions",
    lines: ["keeper or intermediate?", "which figure / table number?", "only what it can't read itself"] },
  { n: 4, title: "PLAN", who: "Claude", kind: "claude", icon: "plan", file: "plan.json",
    lines: ["nodes: copy keepers, reference", "intermediates, never raw data", "links + result-changing params"] },
  { n: 5, title: "CHECK", who: "sciweave plan check", kind: "tool", icon: "check", file: "ERROR \u00b7 WARN",
    lines: ["paths, cycles, unknown ids,", "cosmetic params (font_size, dpi),", "outputs with no provenance"] },
  { n: 6, title: "APPLY", who: "sciweave plan apply", kind: "tool", icon: "db", file: ".sciweave/graph.json",
    lines: ["snapshot versions, log history", "(who \u00b7 when \u00b7 why), keep the plan,", "roll back on any failure"] },
  { n: 7, title: "WEAVE", who: "the provenance network", kind: "tool", icon: "net", file: "SCIWEAVE.md",
    lines: ["every node knows its sources,", "params & script version; parents", "change \u2192 dependents turn stale"] },
  { n: 8, title: "SEE", who: "sciweave serve / export", kind: "see", icon: "eye", file: "dashboard",
    lines: ["force \u00b7 radial \u00b7 lineage views", "articles \u00b7 history \u00b7 ask", "save \u00b7 mark final \u00b7 restore"] },
];

// ------------------------------------------------------------- layout
const W = 1700, H = 1000;
const boxW = 282, boxH = 178, gap = 42;
const rowTop = 250, rowBottom = 650;
const leftPad = (W - (5 * boxW + 4 * gap)) / 2;
const pos = {};
for (let i = 0; i < 5; i++) pos[i + 1] = { x: leftPad + i * (boxW + gap), y: rowTop };
pos[6] = { x: pos[5].x, y: rowBottom };
pos[7] = { x: pos[4].x, y: rowBottom };
pos[8] = { x: pos[3].x, y: rowBottom };
const color = (k) => C[k];

// ------------------------------------------------------------- svg helpers
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const SANS = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif";
const MONO = "'JetBrains Mono', Consolas, Menlo, monospace";
const text = (x, y, s, o = {}) =>
  `<text x="${x}" y="${y}" font-size="${o.size || 14}" font-weight="${o.weight || 400}" fill="${o.fill || C.text}" ` +
  `text-anchor="${o.anchor || "start"}" font-family="${o.mono ? MONO : SANS}" letter-spacing="${o.spacing || 0}" ` +
  `opacity="${o.opacity ?? 1}">${esc(s)}</text>`;
const icon = (name, x, y, size, stroke, width = 1.9) =>
  `<g transform="translate(${x},${y}) scale(${size / 24})"><path d="${ICON[name]}" fill="none" stroke="${stroke}" ` +
  `stroke-width="${width * 24 / size}" stroke-linecap="round" stroke-linejoin="round"/></g>`;

function pill(cx, y, label, col, o = {}) {
  const w = Math.max(64, label.length * (o.mono ? 7.6 : 7.0) + 26);
  return `<rect x="${cx - w / 2}" y="${y - 15}" width="${w}" height="24" rx="12" fill="${o.fill || C.bg0}" stroke="${col}" stroke-width="1.3"/>` +
    text(cx, y + 2, label, { size: 12, fill: o.textFill || col, anchor: "middle", mono: o.mono, weight: 600 });
}

function card(s) {
  const { x, y } = pos[s.n];
  const col = color(s.kind);
  let out = `<g filter="url(#shadow)">`;
  out += `<rect x="${x}" y="${y}" width="${boxW}" height="${boxH}" rx="18" fill="url(#cardGrad)" stroke="${C.cardEdge}" stroke-width="1.5"/>`;
  out += `</g>`;
  out += `<rect x="${x + 18}" y="${y}" width="${boxW - 36}" height="5" rx="2.5" fill="${col}"/>`;
  // number badge with a white "sticker" ring like the logo
  out += `<circle cx="${x + 34}" cy="${y + 42}" r="20" fill="#ffffff"/>`;
  out += `<circle cx="${x + 34}" cy="${y + 42}" r="16.5" fill="${col}"/>`;
  out += text(x + 34, y + 48, s.n, { size: 17, weight: 800, fill: C.bg0, anchor: "middle" });
  out += text(x + 64, y + 44, s.title, { size: 20, weight: 800, spacing: 0.8 });
  out += text(x + 64, y + 64, s.who, { size: 12.5, weight: 600, fill: col });
  // icon, top-right
  out += `<circle cx="${x + boxW - 36}" cy="${y + 40}" r="20" fill="${col}" fill-opacity="0.14" stroke="${col}" stroke-opacity="0.5"/>`;
  out += icon(s.icon, x + boxW - 48, y + 28, 24, col);
  s.lines.forEach((l, i) => { out += text(x + 24, y + 98 + i * 20, l, { size: 13.2, fill: C.muted }); });
  out += pill(x + boxW - 20 - Math.max(64, s.file.length * 7.6 + 26) / 2, y + boxH - 20, s.file, col, { mono: true });
  return out;
}

function arrow(d, o = {}) {
  const col = o.color || C.line;
  const id = "ah" + col.replace("#", "");
  let s = `<path d="${d}" fill="none" stroke="${col}" stroke-width="${o.width || 2.4}" ` +
    `${o.dash ? `stroke-dasharray="${o.dash}"` : ""} marker-end="url(#${id})" ${o.glow ? 'filter="url(#glow)"' : ""}/>`;
  if (o.label) s += pill(o.labelAt[0], o.labelAt[1] + 4, o.label, col, { textFill: o.labelFill });
  return s;
}

// ------------------------------------------------------------- logo (embedded if present)
let logo = "";
const logoPath = path.join(__dirname, "..", "logo-256.png");
if (fs.existsSync(logoPath)) logo = "data:image/png;base64," + fs.readFileSync(logoPath).toString("base64");

// ------------------------------------------------------------- compose
const markerColors = [C.line, C.claude, C.you, C.tool, C.see, C.stale];
let svg = `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
svg += `<defs>
  <radialGradient id="bg" cx="42%" cy="40%" r="75%"><stop offset="0" stop-color="${C.glow}"/><stop offset="0.55" stop-color="${C.bg1}"/><stop offset="1" stop-color="${C.bg0}"/></radialGradient>
  <linearGradient id="cardGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#222c5e"/><stop offset="1" stop-color="${C.card}"/></linearGradient>
  <pattern id="dots" width="26" height="26" patternUnits="userSpaceOnUse"><circle cx="1.5" cy="1.5" r="1.2" fill="#ffffff" fill-opacity="0.045"/></pattern>
  <filter id="shadow" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#000" flood-opacity="0.35"/></filter>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <clipPath id="logoClip"><rect x="0" y="0" width="74" height="74" rx="16"/></clipPath>
  ${markerColors.map((c) => `<marker id="ah${c.replace("#", "")}" viewBox="0 -5 10 10" refX="8" refY="0" markerWidth="8" markerHeight="8" orient="auto"><path d="M0,-4.5L9,0L0,4.5Z" fill="${c}"/></marker>`).join("")}
</defs>`;
svg += `<rect width="${W}" height="${H}" fill="url(#bg)"/><rect width="${W}" height="${H}" fill="url(#dots)"/>`;

// title
{
  const title = "SciWeave \u00b7 everything you generate remembers how it was made";
  const titleW = title.length * 18.6;          // ~width at 36px bold
  const tx = W / 2 + (logo ? 50 : 0);
  if (logo) {
    const lx = tx - titleW / 2 - 118;
    svg += `<g transform="translate(${lx},40)"><image href="${logo}" xlink:href="${logo}" width="74" height="74" clip-path="url(#logoClip)"/></g>`;
  }
  svg += text(tx, 88, title, { size: 36, weight: 800, anchor: "middle", spacing: 0.3 });
  svg += text(W / 2, 128, "Claude records it  \u00b7  SciWeave checks it  \u00b7  you see how every table, figure, model, text and file was made, with which parameters, and whether it is still up to date",
    { size: 15.5, fill: C.muted, anchor: "middle" });
}

// zone: the Claude session (cards 1-4) and the sciweave gate (5)
svg += `<rect x="${pos[1].x - 18}" y="${rowTop - 46}" width="${pos[4].x + boxW - pos[1].x + 36}" height="${boxH + 70}" rx="22" fill="${C.claude}" fill-opacity="0.04" stroke="${C.claude}" stroke-opacity="0.45" stroke-dasharray="7 6"/>`;
svg += text(pos[1].x + 2, rowTop - 20, "IN YOUR CLAUDE CODE SESSION \u00b7 the sciweave skill runs the protocol", { size: 13, weight: 800, fill: C.claude, spacing: 0.8 });
svg += `<rect x="${pos[8].x - 18}" y="${rowBottom - 46}" width="${pos[6].x + boxW - pos[8].x + 36}" height="${boxH + 70}" rx="22" fill="${C.tool}" fill-opacity="0.04" stroke="${C.tool}" stroke-opacity="0.45" stroke-dasharray="7 6"/>`;
svg += text(pos[8].x + 2, rowBottom - 20, "IN YOUR PROJECT \u00b7 .sciweave/ next to your data, nothing moved", { size: 13, weight: 800, fill: C.tool, spacing: 0.8 });

// top-row arrows
for (let i = 1; i < 5; i++) {
  const y = rowTop + boxH / 2;
  svg += arrow(`M${pos[i].x + boxW + 6},${y} L${pos[i + 1].x - 6},${y}`, { color: i === 4 ? C.tool : C.line });
}
// check -> fix -> plan loop (over the top)
{
  const x5 = pos[5].x + boxW / 2 + 40, x4 = pos[4].x + boxW / 2;
  const yTop = rowTop - 64;
  svg += arrow(`M${x5},${rowTop - 2} L${x5},${yTop} L${x4},${yTop} L${x4},${rowTop - 52}`,
    { color: C.you, dash: "8 6", width: 2.4, label: "errors \u2192 Claude fixes the plan \u2192 check again", labelAt: [(x5 + x4) / 2, yTop] });
}
// 5 -> 6 down: approval gate
{
  const x = pos[5].x + boxW / 2;
  svg += arrow(`M${x},${rowTop + boxH + 6} L${x},${rowBottom - 54}`, { color: C.you, width: 2.6,
    label: "you approve \u2713", labelAt: [x, (rowTop + boxH + rowBottom - 46) / 2] });
}
// bottom row arrows (right to left)
for (const [a, b] of [[6, 7], [7, 8]]) {
  const y = rowBottom + boxH / 2;
  svg += arrow(`M${pos[a].x - 6},${y} L${pos[b].x + boxW + 6},${y}`, { color: C.tool });
}
// feedback loop: 8 -> back up to 1 (something changes upstream), routed through the gap between rows
{
  const sx = pos[8].x + 60, sy = rowBottom - 50;
  const midY = (rowTop + boxH + 24 + rowBottom - 46) / 2 + 6;
  const ex = pos[1].x + boxW / 2;
  svg += arrow(`M${sx},${sy} L${sx},${midY + 14} Q${sx},${midY} ${sx - 14},${midY} L${ex + 14},${midY} ` +
    `Q${ex},${midY} ${ex},${midY - 14} L${ex},${rowTop + boxH + 10}`, { color: C.stale, dash: "3 7", width: 2.6, glow: true });
  svg += pill((sx + ex) / 2, midY + 4, "change upstream → dependents turn stale → regenerate & save again", C.stale);
}

// cards on top
steps.forEach((s) => { svg += card(s); });

// inset: what sciweave keeps (a tiny network), bottom-left
{
  const x = leftPad, y = rowBottom - 20, w = pos[8].x - 60 - x, h = boxH + 42;
  svg += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="20" fill="${C.bg0}" fill-opacity="0.72" stroke="${C.cardEdge}"/>`;
  svg += text(x + 22, y + 32, "WHAT THE NETWORK REMEMBERS", { size: 13, weight: 800, spacing: 0.8 });
  const ny = y + 94;
  const nodes = [
    { x: x + 50, lab: "RAW1", sub: "raw data", col: C.see, ic: "db" },
    { x: x + 165, lab: "PL1", sub: "pipeline", col: C.claude, ic: "net" },
    { x: x + 280, lab: "R1", sub: "model / result", col: C.tool, ic: "plan" },
    { x: x + 395, lab: "T2", sub: "table v3 \u2605", col: C.tool, ic: "plan", stale: true },
    { x: x + 510, lab: "A1", sub: "article", col: "#c3c7de", ic: "plan" },
  ];
  const link = (a, b, col, lab) => {
    let s = `<path d="M${a.x + 22},${ny} L${b.x - 26},${ny}" stroke="${col}" stroke-width="2.4" marker-end="url(#ah${col.replace("#", "")})"/>`;
    if (lab) s += text((a.x + b.x) / 2, ny - 14, lab, { size: 11, fill: col, anchor: "middle", mono: true, weight: 600 });
    return s;
  };
  svg += link(nodes[0], nodes[1], C.line);
  svg += link(nodes[1], nodes[2], C.red, "L=10, cov=0.95");
  svg += link(nodes[2], nodes[3], C.green, "");
  svg += link(nodes[3], nodes[4], C.line, "Table 2");
  nodes.forEach((n) => {
    if (n.stale) svg += `<circle cx="${n.x}" cy="${ny}" r="25" fill="none" stroke="${C.stale}" stroke-width="3" filter="url(#glow)"/>`;
    svg += `<circle cx="${n.x}" cy="${ny}" r="20" fill="#ffffff"/><circle cx="${n.x}" cy="${ny}" r="17" fill="${n.col}"/>`;
    svg += icon(n.ic, n.x - 9, ny - 9, 18, C.bg0, 2.2);
    svg += text(n.x, ny + 42, n.lab, { size: 13, weight: 800, anchor: "middle" });
    svg += text(n.x, ny + 58, n.sub, { size: 11.5, fill: C.muted, anchor: "middle" });
  });
  // legend lines
  const ly = y + h - 22;
  svg += `<line x1="${x + 24}" y1="${ly - 4}" x2="${x + 50}" y2="${ly - 4}" stroke="${C.red}" stroke-width="2.6"/>`;
  svg += text(x + 58, ly, "has parameters", { size: 12, fill: C.muted });
  svg += `<line x1="${x + 172}" y1="${ly - 4}" x2="${x + 198}" y2="${ly - 4}" stroke="${C.green}" stroke-width="2.6"/>`;
  svg += text(x + 206, ly, "none", { size: 12, fill: C.muted });
  svg += `<circle cx="${x + 268}" cy="${ly - 4}" r="7" fill="none" stroke="${C.stale}" stroke-width="2.4"/>`;
  svg += text(x + 282, ly, "stale: a parent changed", { size: 12, fill: C.muted });
  svg += text(x + 448, ly, "\u2605 final version", { size: 12, fill: C.star });
}

// footer
svg += text(W / 2, H - 48, "one JSON contract (.sciweave/graph.json) \u00b7 versions snapshotted \u00b7 every change logged with who, when and why \u00b7 works offline",
  { size: 13.5, fill: C.muted, anchor: "middle" });
svg += text(W / 2, H - 22, "sciweave init . --bare   \u00b7   sciweave plan check plan.json   \u00b7   sciweave trace T2   \u00b7   sciweave serve",
  { size: 12.5, fill: C.line, anchor: "middle", mono: true });
svg += `</svg>`;

// ------------------------------------------------------------- write + rasterise
const out = __dirname;
const svgPath = path.join(out, "flowchart.svg");
const htmlPath = path.join(out, "flowchart.html");
const pngPath = path.join(out, "flowchart.png");
fs.writeFileSync(svgPath, svg);
fs.writeFileSync(htmlPath, `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:${C.bg0}}svg{display:block}</style></head><body>${svg}</body></html>`);
console.log("wrote", svgPath, "and", htmlPath);

const scale = Number(process.env.SCALE || 2);
const candidates = [
  process.env.CHROME,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
].filter(Boolean);
let done = false;
for (const bin of candidates) {
  try {
    if (fs.existsSync(pngPath)) fs.unlinkSync(pngPath);
    execFileSync(bin, ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
      `--force-device-scale-factor=${scale}`, `--window-size=${W},${H}`, `--screenshot=${pngPath}`,
      pathToFileURL(htmlPath).href], { stdio: "ignore", timeout: 60000 });
    done = fs.existsSync(pngPath);
    if (done) { console.log("wrote", pngPath, `(${W * scale}x${H * scale}, via ${path.basename(bin)})`); break; }
  } catch (e) { /* try next */ }
}
if (!done) {
  console.error("could not rasterise: install Chrome/Edge (or set CHROME=...); flowchart.svg is still usable");
  process.exit(1);
}
