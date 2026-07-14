from __future__ import annotations


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Player</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #050507;
      --case: #121019;
      --edge: #352653;
      --screen: #05050a;
      --text: #f5efff;
      --muted: #a08fbc;
      --purple: #be7aff;
      --cyan: #5ddeff;
      font-family: ui-monospace, "Cascadia Mono", "Consolas", monospace;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 50% 0%, rgba(97, 53, 160, .22), transparent 36%),
        var(--bg);
      color: var(--text);
      display: grid;
      place-items: center;
      overflow: hidden;
    }
    main {
      width: min(96vw, 560px);
      display: grid;
      gap: 14px;
      justify-items: center;
    }
    .case {
      background: linear-gradient(180deg, #171320, #0c0d12);
      border: 1px solid var(--edge);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 24px 80px rgba(0, 0, 0, .58);
    }
    canvas {
      width: min(84vw, 360px);
      aspect-ratio: 3 / 4;
      display: block;
      background: var(--screen);
      border: 1px solid #51406b;
      image-rendering: pixelated;
    }
    canvas:focus { outline: none; }
    .hint {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      text-align: center;
      user-select: none;
    }
    .hint a {
      color: var(--cyan);
      text-decoration: none;
    }
    @media (max-height: 680px) {
      .case { padding: 10px; }
      canvas { width: min(62vh, 340px); }
      .hint { font-size: 11px; }
    }
  </style>
</head>
<body>
  <main>
    <div class="case">
      <canvas id="lcd" width="240" height="320" tabindex="0" aria-label="Player 240 by 320 LCD screen"></canvas>
    </div>
    <div class="hint">
      Home: arrows focus controls, Enter selects, Space play.<br>
      Menu: Up / Down move, Left back, Right / Enter open. M manager. R LCD reset.
    </div>
  </main>

<script>
const W = 240;
const H = 320;
const canvas = document.getElementById("lcd");
const ctx = canvas.getContext("2d");
ctx.imageSmoothingEnabled = false;

function focusSimulator() {
  try {
    canvas.focus({ preventScroll: true });
  } catch {
    canvas.focus();
  }
}

let screen = null;
let requestId = 0;
let inputBusy = false;
let refreshBusy = false;
let nextPollAt = 0;
let toast = "";
let toastUntil = 0;
const coverCache = new Map();
let hotZones = [];

const colors = {
  bg: "#05050a",
  panel: "#0d0e16",
  header: "#110b1f",
  row: "#0d0e16",
  rowHot: "#47237b",
  edge: "#be7aff",
  text: "#f5efff",
  muted: "#a08fbc",
  cyan: "#5ddeff",
  purple: "#be7aff"
};

function homeTheme() {
  return { ...colors, grid: "#191128", ...(screen && screen.theme ? screen.theme : {}) };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || response.statusText);
  return data;
}

function assetUrl(path) {
  if (!path) return "";
  return String(path).replace(/^\.\.\/assets\//, "/assets/");
}

function coverImage(path) {
  const url = assetUrl(path);
  if (!url) return null;
  if (coverCache.has(url)) return coverCache.get(url);
  const image = new Image();
  image.onload = () => render();
  image.src = url;
  coverCache.set(url, image);
  return image;
}

function coverSource(item) {
  if (!item) return "";
  return item.album_cover_pixel || item.cover_pixel || item.cover || item.album_cover || item.cover_lcd || "";
}

function clear() {
  ctx.fillStyle = colors.bg;
  ctx.fillRect(0, 0, W, H);
}

function text(value, x, y, size = 12, color = colors.text, weight = "normal") {
  ctx.fillStyle = color;
  ctx.font = `${weight} ${size}px ui-monospace, Consolas, monospace`;
  ctx.textBaseline = "top";
  ctx.fillText(String(value || ""), x, y);
}

function roundRect(x, y, w, h, radius, fill, stroke, lineWidth = 1) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + w - radius, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + radius);
  ctx.lineTo(x + w, y + h - radius);
  ctx.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
  ctx.lineTo(x + radius, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - radius);
  ctx.lineTo(x, y + radius);
  ctx.quadraticCurveTo(x, y, x + radius, y);
  ctx.closePath();
  if (fill) {
    ctx.fillStyle = fill;
    ctx.fill();
  }
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lineWidth;
    ctx.stroke();
  }
}

function marquee(value, x, y, width, size, color, background, frame, weight = "normal") {
  const label = String(value || "");
  ctx.font = `${weight} ${size}px ui-monospace, Consolas, monospace`;
  const measured = ctx.measureText(label).width;
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, width, size + 7);
  ctx.clip();
  ctx.fillStyle = background;
  ctx.fillRect(x, y, width, size + 7);
  ctx.fillStyle = color;
  ctx.textBaseline = "top";
  if (measured <= width) {
    ctx.fillText(label, x, y);
  } else {
    const loop = label + "    ";
    const loopWidth = Math.max(1, ctx.measureText(loop).width);
    const offset = (frame * 3) % loopWidth;
    ctx.fillText(loop + loop, x - offset, y);
  }
  ctx.restore();
}

function drawCover(item, x, y, size, palette = colors) {
  roundRect(x, y, size, size, 4, palette.panel || "#120f1b", palette.edge || colors.edge, 2);
  const source = coverSource(item);
  const image = coverImage(source);
  if (image && image.complete) {
    ctx.drawImage(image, x + 3, y + 3, size - 6, size - 6);
    ctx.strokeStyle = palette.text || colors.text;
    ctx.lineWidth = 1;
    ctx.strokeRect(x + .5, y + .5, size - 1, size - 1);
    return;
  }
  ctx.strokeStyle = palette.purple || "#6440ac";
  ctx.strokeRect(x + 8.5, y + 8.5, size - 17, size - 17);
  text("NO ART", x + 14, y + Math.floor(size / 2) - 8, 10, palette.text || colors.text, "bold");
}

function drawCassette(frame, paused, y = 170) {
  roundRect(20, y, 200, 55, 6, colors.panel, "#8252db", 2);
  roundRect(48, y + 14, 144, 26, 3, null, colors.cyan, 1);
  ctx.strokeStyle = colors.text;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(79, y + 26, 14, 0, Math.PI * 2);
  ctx.arc(161, y + 26, 14, 0, Math.PI * 2);
  ctx.stroke();
  ctx.strokeStyle = colors.cyan;
  ctx.beginPath();
  ctx.moveTo(95, y + 26);
  ctx.lineTo(146, y + 26);
  ctx.stroke();
  ctx.fillStyle = colors.text;
  if (paused) {
    ctx.fillRect(113, y + 18, 5, 16);
    ctx.fillRect(124, y + 18, 5, 16);
  } else {
    ctx.beginPath();
    ctx.moveTo(112, y + 16);
    ctx.lineTo(112, y + 36);
    ctx.lineTo(132, y + 26);
    ctx.closePath();
    ctx.fill();
  }
}

function controlButton(label, x, y, width, action, active = false, focused = false, palette = colors) {
  const fill = focused ? palette.rowHot : active ? palette.rowHot : palette.panel;
  const stroke = focused ? palette.cyan : active ? palette.text : palette.edge;
  roundRect(x, y, width, 26, 5, fill, stroke, focused ? 2 : 1);
  if (focused) {
    ctx.strokeStyle = palette.text;
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 4.5, y + 4.5, width - 9, 17);
  }
  ctx.font = "bold 14px ui-monospace, Consolas, monospace";
  const labelWidth = ctx.measureText(label).width;
  text(label, x + Math.max(4, Math.floor((width - labelWidth) / 2)), y + 5, 14, palette.text, "bold");
  hotZones.push({ x, y, width, height: 26, action });
}

function headerButton(label, x, action, focused = false, width = 54, palette = colors) {
  roundRect(x, 7, width, 20, 4, focused ? palette.rowHot : palette.panel, focused ? palette.cyan : palette.edge, focused ? 2 : 1);
  ctx.font = "bold 9px ui-monospace, Consolas, monospace";
  const labelWidth = ctx.measureText(label).width;
  text(label, x + Math.floor((width - labelWidth) / 2), 12, 9, palette.text, "bold");
  hotZones.push({ x, y: 7, width, height: 20, action });
}

function drawVisualizer(frame, paused, palette) {
  const baseY = 224;
  ctx.save();
  ctx.globalAlpha = paused ? 0.62 : 1;
  ctx.strokeStyle = palette.grid;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(18, baseY + 2.5);
  ctx.lineTo(222, baseY + 2.5);
  ctx.stroke();

  for (let i = 0; i < 30; i++) {
    const x = 17 + i * 7;
    const phase = frame * 0.42 + i * 0.74;
    const beat = Math.sin(phase) * 0.55 + Math.sin(phase * 0.37 + i) * 0.45;
    const lift = paused ? 3 : 7 + Math.abs(beat) * 19 + ((frame + i * 5) % 9);
    const barHeight = Math.max(3, Math.min(30, lift));
    ctx.fillStyle = i % 4 === 0 ? palette.purple : i % 3 === 0 ? palette.text : palette.cyan;
    ctx.fillRect(x, baseY - barHeight, 4, barHeight);
    ctx.fillStyle = palette.bg;
    ctx.fillRect(x + 1, baseY - barHeight + 2, 2, Math.max(0, barHeight - 4));
    ctx.fillStyle = i % 4 === 0 ? palette.purple : palette.cyan;
    ctx.fillRect(x, baseY + 5, 4, 2);
  }

  ctx.strokeStyle = palette.text;
  ctx.globalAlpha = paused ? 0.32 : 0.74;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = 18; x <= 222; x += 6) {
    const y = baseY - 15 + Math.sin(frame * 0.5 + x * 0.09) * (paused ? 2 : 7);
    if (x === 18) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.restore();
}

function renderHome(frame) {
  hotZones = [];
  const state = screen.state || {};
  const current = screen.current_track || {};
  const palette = homeTheme();
  const title = current.name || "No track";
  const artist = current.artist || current.album || "Player";
  const queue = state.queue_name || "All Songs";
  const paused = !!state.paused;
  const volume = Number(state.volume || 0);
  const focus = Math.max(0, Math.min(3, Number(screen.cursor ?? state.ui_cursor ?? 0) || 0));

  ctx.fillStyle = palette.bg;
  ctx.fillRect(0, 0, W, H);

  ctx.strokeStyle = palette.grid;
  ctx.lineWidth = 1;
  for (let y = 48; y < 276; y += 12) {
    ctx.beginPath();
    ctx.moveTo(12, y + .5);
    ctx.lineTo(228, y + .5);
    ctx.stroke();
  }

  ctx.fillStyle = palette.header;
  ctx.fillRect(0, 0, W, 40);
  text("PLAYER", 12, 8, 15, palette.text, "bold");
  headerButton("MENU", 90, "menu", focus === 0, 54, palette);

  const coverX = 40;
  const coverY = 52;
  const coverSize = 160;
  roundRect(29, 43, 182, 182, 7, palette.panel, palette.grid, 1);
  drawCover(current, coverX, coverY, coverSize, palette);
  ctx.strokeStyle = palette.cyan;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(31, 74); ctx.lineTo(31, 51); ctx.lineTo(54, 51);
  ctx.moveTo(209, 74); ctx.lineTo(209, 51); ctx.lineTo(186, 51);
  ctx.moveTo(31, 202); ctx.lineTo(31, 225); ctx.lineTo(54, 225);
  ctx.moveTo(209, 202); ctx.lineTo(209, 225); ctx.lineTo(186, 225);
  ctx.stroke();

  text("NOW PLAYING", 18, 230, 9, palette.purple, "bold");
  marquee(title, 14, 244, 212, 17, palette.text, palette.bg, frame, "bold");
  marquee(`${artist} / ${queue}`, 14, 265, 212, 10, palette.muted, palette.bg, Math.floor(frame / 2));

  drawVisualizer(frame, paused, palette);

  text(`${Number(screen.library_count || 0)} SONGS`, 14, 42, 9, palette.muted, "bold");
  text(`VOL ${String(Math.round(volume)).padStart(2, "0")}`, 180, 42, 9, palette.muted, "bold");
  controlButton("|<", 14, 288, 58, "previous", false, focus === 1, palette);
  controlButton(paused ? ">" : "||", 80, 288, 80, "playpause", true, focus === 2, palette);
  controlButton(">|", 168, 288, 58, "next", false, focus === 3, palette);
}

function detailItem() {
  const selected = Array.isArray(screen.items) ? screen.items[Number(screen.cursor || 0)] : null;
  return screen.current_playlist || screen.current_album || selected || screen.current_track || null;
}

function renderRows(frame, top) {
  const items = Array.isArray(screen.items) ? screen.items : [];
  const cursor = Number(screen.cursor || 0);
  const rowHeight = 34;
  const maxRows = Math.max(1, Math.floor((H - top - 8) / rowHeight));
  const start = Math.max(0, Math.min(cursor - 1, Math.max(0, items.length - maxRows)));

  for (let row = 0; row < maxRows; row++) {
    const itemIndex = start + row;
    const item = items[itemIndex];
    if (!item) break;
    const y = top + row * rowHeight;
    const selected = itemIndex === cursor;
    const bg = selected ? colors.rowHot : colors.row;
    roundRect(8, y, 224, 28, 5, bg, selected ? colors.purple : "#231f2f", 1);

    let textX = 18;
    const source = coverSource(item);
    const image = coverImage(source);
    if (image && image.complete) {
      ctx.drawImage(image, 16, y + 3, 22, 22);
      textX = 45;
    } else if (item.type === "album" || item.type === "playlist") {
      ctx.strokeStyle = colors.text;
      ctx.strokeRect(16.5, y + 6.5, 12, 12);
      textX = 38;
    }
    marquee(item.label || "", textX, y + 6, 221 - textX, 12, selected ? "#ffffff" : "#d0c4e7", bg, frame, selected ? "bold" : "normal");
  }
}

function renderMenu(frame) {
  hotZones = [];
  const path = screen.path || ["Home"];
  if (path.length === 1) {
    renderHome(frame);
    return;
  }

  ctx.fillStyle = colors.header;
  ctx.fillRect(0, 0, W, 42);
  marquee(screen.breadcrumb || "Home", 10, 8, 220, 13, colors.text, colors.header, frame, "bold");
  text(screen.message || "Ready", 12, 28, 9, colors.muted);

  const item = detailItem();
  const hasDetail = !!coverSource(item);
  let rowsTop = 52;
  if (hasDetail) {
    drawCover(item, 12, 52, 86);
    marquee(item.name || item.label || item.album || "Selected", 108, 58, 120, 15, colors.text, colors.bg, frame, "bold");
    const sub = item.artist || item.queue_name || "";
    if (sub) marquee(sub, 108, 84, 120, 11, colors.muted, colors.bg, Math.floor(frame / 2));
    const count = item.count || (Array.isArray(item.track_ids) ? item.track_ids.length : "");
    if (count) text(`${count} tracks`, 108, 111, 10, colors.cyan, "bold");
    rowsTop = 150;
  }
  renderRows(frame, rowsTop);
}

function renderLoading(message) {
  clear();
  ctx.fillStyle = colors.header;
  ctx.fillRect(0, 0, W, 52);
  text("PLAYER", 16, 15, 22, colors.text, "bold");
  text(String(message || "LOADING").toUpperCase(), 34, 231, 13, colors.cyan);
  roundRect(16, 210, 208, 60, 6, null, colors.cyan, 2);
}

function render() {
  if (!screen) {
    renderLoading("loading");
    return;
  }
  const frame = Math.floor(Date.now() / 150);
  clear();
  renderMenu(frame);
  if (toast && Date.now() < toastUntil) {
    roundRect(18, 148, 204, 28, 4, "#211331", colors.purple, 1);
    text(toast, 28, 155, 11, colors.text, "bold");
  }
}

async function refresh() {
  if (inputBusy || refreshBusy) return;
  refreshBusy = true;
  const id = ++requestId;
  try {
    const data = await api("/api/screen");
    if (id !== requestId) return;
    screen = data;
    render();
  } finally {
    refreshBusy = false;
  }
}

async function input(action) {
  inputBusy = true;
  const id = ++requestId;
  try {
    const data = await api("/api/input", {
      method: "POST",
      body: JSON.stringify({ action })
    });
    if (id === requestId) {
      screen = data;
      render();
      nextPollAt = Date.now() + 900;
    }
  } finally {
    inputBusy = false;
  }
}

async function reinitDisplay() {
  toast = "LCD RESET SENT";
  toastUntil = Date.now() + 1200;
  render();
  await api("/api/display/reinit");
}

canvas.addEventListener("click", event => {
  focusSimulator();
  const rect = canvas.getBoundingClientRect();
  const x = (event.clientX - rect.left) * (canvas.width / rect.width);
  const y = (event.clientY - rect.top) * (canvas.height / rect.height);
  const hit = hotZones.find(zone =>
    x >= zone.x && x <= zone.x + zone.width && y >= zone.y && y <= zone.y + zone.height
  );
  if (hit) input(hit.action);
});
canvas.addEventListener("pointerdown", focusSimulator);
window.addEventListener("focus", focusSimulator);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) focusSimulator();
});

function handleKeydown(event) {
  if (event.repeat) return;
  if (event.key === "ArrowUp") { event.preventDefault(); input("up"); }
  else if (event.key === "ArrowDown") { event.preventDefault(); input("down"); }
  else if (event.key === "ArrowLeft") { event.preventDefault(); input("left"); }
  else if (event.key === "ArrowRight") { event.preventDefault(); input("right"); }
  else if (event.key === "Enter") { event.preventDefault(); input("select"); }
  else if (event.key === " ") { event.preventDefault(); input("playpause"); }
  else if (event.key === "," || event.key.toLowerCase() === "p") { event.preventDefault(); input("previous"); }
  else if (event.key === "." || event.key.toLowerCase() === "n") { event.preventDefault(); input("next"); }
  else if (event.key === "Escape" || event.key === "Backspace") { event.preventDefault(); input("back"); }
  else if (event.key.toLowerCase() === "m") { event.preventDefault(); window.location.href = "/manage"; }
  else if (event.key.toLowerCase() === "r") { event.preventDefault(); reinitDisplay().catch(() => {}); }
}

document.addEventListener("keydown", handleKeydown, { capture: true });

renderLoading("loading");
focusSimulator();
refresh().catch(error => renderLoading(error.message.slice(0, 16)));
setInterval(() => {
  if (screen) render();
  if (Date.now() >= nextPollAt && !inputBusy && !refreshBusy) {
    nextPollAt = Date.now() + 2000;
    refresh().catch(() => {});
  }
}, 150);
</script>
</body>
</html>"""
