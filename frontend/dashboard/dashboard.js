/**
 * Pikina OS — Dashboard JavaScript
 * Arc Reactor animation + live data polling from Python backend.
 */

'use strict';

const BACKEND = (window.pikina && window.pikina.BACKEND) || 'http://localhost:5001';

// ============================================================
// ARC REACTOR — Canvas animation
// ============================================================

const canvas  = document.getElementById('reactor-canvas');
const ctx     = canvas ? canvas.getContext('2d') : null;
const CX      = canvas ? canvas.width  / 2 : 0;
const CY      = canvas ? canvas.height / 2 : 0;

// Reactor state: idle | thinking | listening
let reactorState = 'idle';

const STATE_COLORS = {
  idle:      { primary: '#00d4ff', glow: 'rgba(0,212,255,0.55)',  sweep: 'rgba(0,212,255,0.12)' },
  thinking:  { primary: '#ff8c00', glow: 'rgba(255,140,0,0.55)',  sweep: 'rgba(255,140,0,0.12)' },
  listening: { primary: '#00ff88', glow: 'rgba(0,255,136,0.55)',  sweep: 'rgba(0,255,136,0.12)' },
};

// Ring definitions (radius, speed_deg_per_frame, direction, style)
const RINGS = [
  { r: 222, speed:  0.15, dir:  1, type: 'dashed',   segments: 72,  dash: 3,  gap: 2,  width: 1   },
  { r: 208, speed:  0.10, dir: -1, type: 'solid',     width: 1.5                                    },
  { r: 192, speed:  0.25, dir:  1, type: 'ticks',     count: 48,  len: 6, width: 1               },
  { r: 172, speed:  0.18, dir: -1, type: 'sectors',   segments: 12, fill: 0.7, width: 2          },
  { r: 148, speed:  0.30, dir:  1, type: 'dashed',    segments: 36, dash: 4,  gap: 3,  width: 1.5 },
  { r: 128, speed:  0.12, dir: -1, type: 'solid',     width: 2                                     },
  { r: 110, speed:  0.40, dir:  1, type: 'ticks',     count: 24,  len: 8, width: 1.5            },
  { r:  90, speed:  0.08, dir: -1, type: 'dashed',    segments: 18, dash: 5,  gap: 4,  width: 2   },
];

const ringAngles = RINGS.map(() => 0);
let   sweepAngle = 0;
let   corePulse  = 0;
let   frameCount = 0;

function drawRing(ring, angle, colors) {
  const { r, type, width } = ring;

  ctx.save();
  ctx.translate(CX, CY);
  ctx.rotate(angle * Math.PI / 180);

  ctx.strokeStyle = colors.primary;
  ctx.lineWidth   = width;
  ctx.shadowColor = colors.glow;
  ctx.shadowBlur  = 8;

  switch (type) {

    case 'solid': {
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.stroke();
      break;
    }

    case 'dashed': {
      const { segments, dash, gap } = ring;
      const totalCirc = 2 * Math.PI * r;
      const segAngle  = (2 * Math.PI) / segments;
      const dashAngle = (dash / (dash + gap)) * segAngle;
      for (let i = 0; i < segments; i++) {
        const start = i * segAngle;
        const end   = start + dashAngle;
        ctx.beginPath();
        ctx.arc(0, 0, r, start, end);
        ctx.stroke();
      }
      break;
    }

    case 'ticks': {
      const { count, len } = ring;
      for (let i = 0; i < count; i++) {
        const a = (i / count) * Math.PI * 2;
        const inner = r - len;
        ctx.beginPath();
        ctx.moveTo(inner * Math.cos(a), inner * Math.sin(a));
        ctx.lineTo(r     * Math.cos(a), r     * Math.sin(a));
        ctx.stroke();
      }
      break;
    }

    case 'sectors': {
      const { segments, fill } = ring;
      const segAngle  = (2 * Math.PI) / segments;
      const fillAngle = segAngle * fill;
      const innerR    = r * 0.72;
      ctx.fillStyle = colors.primary;
      ctx.globalAlpha = 0.18;
      for (let i = 0; i < segments; i++) {
        const start = i * segAngle;
        const end   = start + fillAngle;
        ctx.beginPath();
        ctx.arc(0, 0, r,     start, end);
        ctx.arc(0, 0, innerR, end, start, true);
        ctx.closePath();
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.stroke();
      break;
    }
  }

  ctx.restore();
}

function drawSweep(angle, colors) {
  ctx.save();
  ctx.translate(CX, CY);
  ctx.rotate(angle * Math.PI / 180);

  const sweepR     = 168;
  const sweepWidth = Math.PI / 5; // 36 degrees

  const grad = ctx.createConicalGradient
    ? null  // not standard; use radial + clip instead
    : null;

  // Draw as a filled sector
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.arc(0, 0, sweepR, -sweepWidth, 0);
  ctx.closePath();

  const radGrad = ctx.createRadialGradient(0, 0, 0, 0, 0, sweepR);
  radGrad.addColorStop(0,   'rgba(0,0,0,0)');
  radGrad.addColorStop(0.4, colors.sweep);
  radGrad.addColorStop(1.0, colors.sweep.replace('0.12', '0.25'));

  ctx.fillStyle = radGrad;
  ctx.fill();

  // Leading edge line
  ctx.strokeStyle = colors.primary;
  ctx.lineWidth   = 1.5;
  ctx.globalAlpha = 0.5;
  ctx.shadowColor = colors.glow;
  ctx.shadowBlur  = 10;
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(sweepR, 0);
  ctx.stroke();

  ctx.globalAlpha = 1;
  ctx.restore();
}

function drawCore(pulse, colors) {
  // Innermost orb — the "arc reactor core"
  const coreR = 40;
  const glow  = Math.sin(pulse) * 0.3 + 0.7; // 0.4..1.0

  ctx.save();
  ctx.translate(CX, CY);

  // Outer glow halo
  const halo = ctx.createRadialGradient(0, 0, coreR * 0.3, 0, 0, coreR * 2.5);
  halo.addColorStop(0,   colors.primary + 'bb');
  halo.addColorStop(0.5, colors.glow);
  halo.addColorStop(1,   'rgba(0,0,0,0)');
  ctx.globalAlpha = glow * 0.6;
  ctx.beginPath();
  ctx.arc(0, 0, coreR * 2.5, 0, Math.PI * 2);
  ctx.fillStyle = halo;
  ctx.fill();
  ctx.globalAlpha = 1;

  // Core circle
  const core = ctx.createRadialGradient(0, 0, 0, 0, 0, coreR);
  core.addColorStop(0,    '#ffffff');
  core.addColorStop(0.3,  colors.primary);
  core.addColorStop(0.7,  colors.glow);
  core.addColorStop(1,    'rgba(0,20,40,0.8)');
  ctx.beginPath();
  ctx.arc(0, 0, coreR, 0, Math.PI * 2);
  ctx.fillStyle   = core;
  ctx.shadowColor = colors.primary;
  ctx.shadowBlur  = 30 * glow;
  ctx.fill();

  // Inner ring on core
  ctx.strokeStyle = 'rgba(255,255,255,0.4)';
  ctx.lineWidth   = 1;
  ctx.shadowBlur  = 0;
  ctx.beginPath();
  ctx.arc(0, 0, coreR * 0.65, 0, Math.PI * 2);
  ctx.stroke();

  ctx.restore();
}

function drawStatusText(colors) {
  // "SYSTEM DIAGNOSTICS" text on the data ring (r≈100)
  const textR = 106;
  ctx.save();
  ctx.translate(CX, CY);
  ctx.font = '700 7px "Orbitron", sans-serif';
  ctx.fillStyle   = colors.primary;
  ctx.globalAlpha = 0.45;
  ctx.letterSpacing = '3px';

  const text     = 'SYSTEM DIAGNOSTICS · PIKINA OS ·';
  const charAngle = (2 * Math.PI) / text.length;

  for (let i = 0; i < text.length; i++) {
    ctx.save();
    ctx.rotate(i * charAngle);
    ctx.translate(0, -textR);
    ctx.rotate(Math.PI / 2);
    ctx.fillText(text[i], 0, 0);
    ctx.restore();
  }

  ctx.globalAlpha = 1;
  ctx.restore();
}

function drawFrame(ts) {
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const colors = STATE_COLORS[reactorState] || STATE_COLORS.idle;

  // Update angles
  RINGS.forEach((ring, i) => {
    ringAngles[i] += ring.speed * ring.dir;
  });

  sweepAngle += 1.2;
  corePulse  += 0.04;
  frameCount++;

  // Draw outer status ring (always-on glow)
  ctx.save();
  ctx.beginPath();
  ctx.arc(CX, CY, 234, 0, Math.PI * 2);
  ctx.strokeStyle = colors.primary;
  ctx.lineWidth   = 2;
  ctx.shadowColor = colors.glow;
  ctx.shadowBlur  = 18;
  ctx.globalAlpha = 0.35 + Math.sin(corePulse * 0.5) * 0.08;
  ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.restore();

  // Draw all rings
  RINGS.forEach((ring, i) => drawRing(ring, ringAngles[i], colors));

  // Radar sweep (above rings, below core)
  drawSweep(sweepAngle, colors);

  // Circular text
  drawStatusText(colors);

  // Core orb (topmost)
  drawCore(corePulse, colors);

  requestAnimationFrame(drawFrame);
}

if (canvas) {
  requestAnimationFrame(drawFrame);
}


// ============================================================
// CLOCK
// ============================================================

function updateClock() {
  const now   = new Date();
  const time  = now.toLocaleTimeString('en-GB', { hour12: false });
  const date  = now.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase();
  document.getElementById('clock-time').textContent = time;
  document.getElementById('clock-date').textContent = date;
}

updateClock();
setInterval(updateClock, 1000);


// ============================================================
// BACKEND POLLING
// ============================================================

let backendOnline = false;

function setBackendStatus(online, profile) {
  backendOnline = online;
  const dot   = document.getElementById('status-dot');
  const label = document.getElementById('status-label');
  const tier  = document.getElementById('status-tier');

  if (online) {
    dot.className   = 'status-dot online';
    label.textContent = 'SYSTEM ONLINE';
    tier.textContent  = 'TIER 1 ACTIVE';
  } else {
    dot.className   = 'status-dot offline';
    label.textContent = 'BACKEND OFFLINE — Start python backend_server.py';
    tier.textContent  = 'STANDALONE';
  }

  if (profile) {
    document.getElementById('detail-profile').textContent = profile.toUpperCase();
  }
}

// ── Telemetry ──
async function pollTelemetry() {
  try {
    const res  = await fetch(`${BACKEND}/api/telemetry`, { signal: AbortSignal.timeout(3000) });
    const data = await res.json();

    setBackendStatus(true, null);

    // CPU
    setBar('bar-cpu', data.cpu.percent);
    document.getElementById('val-cpu').textContent   = `${data.cpu.percent}%`;
    document.getElementById('detail-cores').textContent = data.cpu.core_count;

    // RAM
    setBar('bar-ram', data.ram.percent);
    document.getElementById('val-ram').textContent    = `${data.ram.percent}%`;
    document.getElementById('detail-ram').textContent = `${data.ram.used_gb} / ${data.ram.total_gb} GB`;

    // Disk
    setBar('bar-disk', data.disk.percent);
    document.getElementById('val-disk').textContent   = `${data.disk.percent}%`;
    document.getElementById('detail-disk').textContent = `${data.disk.used_gb} / ${data.disk.total_gb} GB`;

    // Battery
    if (data.battery) {
      document.getElementById('row-battery').style.display = '';
      setBar('bar-bat', data.battery.percent);
      document.getElementById('val-bat').textContent = `${data.battery.percent}%`;
    } else {
      document.getElementById('row-battery').style.display = 'none';
    }

  } catch {
    setBackendStatus(false, null);
  }
}

function setBar(id, pct) {
  const bar = document.getElementById(id);
  if (!bar) return;
  bar.style.width = `${Math.min(100, pct)}%`;
  bar.classList.remove('bar-warn', 'bar-crit');
  if (pct > 90) bar.classList.add('bar-crit');
  else if (pct > 70) bar.classList.add('bar-warn');
}

// ── Weather ──
async function pollWeather() {
  try {
    const res  = await fetch(`${BACKEND}/api/weather`, { signal: AbortSignal.timeout(6000) });
    const data = await res.json();

    if (data.error) {
      document.getElementById('weather-desc').textContent = data.error;
      return;
    }

    document.getElementById('weather-temp').textContent = `${data.temp_c}°`;
    document.getElementById('weather-icon').textContent = weatherEmoji(data.description);
    document.getElementById('weather-desc').textContent = data.description.toUpperCase();
    document.getElementById('w-city').textContent       = `${data.city}, ${data.country}`;
    document.getElementById('w-feels').textContent      = `${data.feels_like}°`;
    document.getElementById('w-humidity').textContent   = `${data.humidity}%`;
    document.getElementById('w-wind').textContent       = `${data.wind_kmh} km/h`;

  } catch {
    document.getElementById('weather-desc').textContent = 'WEATHER UNAVAILABLE';
  }
}

function weatherEmoji(desc) {
  const d = desc.toLowerCase();
  if (d.includes('thunder'))   return '⛈';
  if (d.includes('drizzle'))   return '🌦';
  if (d.includes('rain'))      return '🌧';
  if (d.includes('snow'))      return '❄';
  if (d.includes('mist') || d.includes('fog') || d.includes('haze')) return '🌫';
  if (d.includes('cloud'))     return '☁';
  if (d.includes('clear'))     return '☀';
  return '◈';
}

document.getElementById('panel-weather').addEventListener('click', () => {
  const city = document.getElementById('w-city').textContent;
  if (!city || city === '--') return;
  const url = `https://openweathermap.org/find?q=${encodeURIComponent(city)}`;
  if (window.pikina && window.pikina.openUrl) {
    window.pikina.openUrl(url);
  } else {
    window.open(url, '_blank');
  }
});

// ── Events ──
async function pollEvents() {
  try {
    const res  = await fetch(`${BACKEND}/api/events?since=30`, { signal: AbortSignal.timeout(3000) });
    const data = await res.json();

    const list  = document.getElementById('event-list');
    const count = document.getElementById('event-count');
    count.textContent = `${data.count} events`;

    if (!data.events || data.events.length === 0) {
      list.innerHTML = '<div class="event-item event-empty">No events yet.</div>';
      return;
    }

    // Show last 12 events, newest first
    const events = [...data.events].reverse().slice(0, 12);
    list.innerHTML = events.map(ev => {
      const ts  = ev.timestamp.substring(11, 19);
      const cls = ev.provenance === 'TRUSTED_COMMAND' ? 'event-trusted' : 'event-untrusted';
      const topic = ev.topic.replace('.', '​.'); // zero-width space for wrapping
      return `<div class="event-item ${cls}">
        <span class="event-topic">${topic}</span>
        <span>${ts} · ${ev.provenance.replace('_', ' ')}</span>
      </div>`;
    }).join('');

  } catch { /* silent */ }
}

// ── Deadlines ──
async function loadDeadlines() {
  try {
    const res  = await fetch(`${BACKEND}/api/deadlines`, { signal: AbortSignal.timeout(3000) });
    const data = await res.json();
    const list = document.getElementById('deadline-list');

    if (!data.deadlines || data.deadlines.length === 0) {
      list.innerHTML = '<div class="deadline-empty">No objectives.</div>';
      return;
    }

    list.innerHTML = data.deadlines.map(d => `
      <div class="deadline-item priority-${d.priority || 'low'}">
        <div class="deadline-title">${d.title}</div>
        <div class="deadline-due">DUE ${d.due} · ${(d.priority || 'low').toUpperCase()}</div>
      </div>
    `).join('');

  } catch {
    document.getElementById('deadline-list').innerHTML =
      '<div class="deadline-empty">Could not load objectives.</div>';
  }
}

// ── Status poll (for profile) ──
async function pollStatus() {
  try {
    const res  = await fetch(`${BACKEND}/api/status`, { signal: AbortSignal.timeout(3000) });
    const data = await res.json();
    document.getElementById('detail-profile').textContent = (data.profile || '--').toUpperCase();
  } catch { /* silent */ }
}


// ============================================================
// COMMAND EXECUTION
// ============================================================

const cmdInput  = document.getElementById('cmd-input');
const cmdSend   = document.getElementById('cmd-send');
const cmdResult = document.getElementById('cmd-result');
const reactorStateEl = document.getElementById('reactor-state');

function setReactorState(state) {
  reactorState = state;
  reactorStateEl.className = `reactor-state ${state === 'idle' ? '' : state}`;
  reactorStateEl.textContent = state.toUpperCase();
}
const commandHistory = [];
let historyIndex = -1;

async function executeCommand(text) {
  if (!text.trim()) return;

  if (commandHistory[commandHistory.length - 1] !== text.trim()) {
    commandHistory.push(text.trim());
  }
  historyIndex = commandHistory.length;

  setReactorState('thinking');
  cmdResult.textContent = 'Executing...';
  cmdResult.className   = 'cmd-result';

  let result;
  try {
    if (window.pikina && window.pikina.sendCommand) {
      result = await window.pikina.sendCommand(text);
    } else {
      const res = await fetch(`${BACKEND}/api/command`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ text, source: 'user_typed' }),
      });
      result = await res.json();
    }
  } catch (err) {
    result = { status: 'error', reason: `Backend unreachable: ${err.message}` };
  }

  const status = result.status || 'unknown';
  setReactorState('idle');

  const messages = {
    ok:       `✓ ${result.message || result.launched || JSON.stringify(result).slice(0, 80)}`,
    denied:   `⊘ Denied — ${result.reason || 'user declined'}`,
    rejected: `⊘ Rejected — ${result.reason || 'untrusted provenance'}`,
    no_match: `? ${result.message || 'No route matched'}`,
    error:    `✗ ${result.reason || 'Unknown error'}`,
  };

  cmdResult.className   = `cmd-result ${['ok'].includes(status) ? 'ok' : ['error', 'denied', 'rejected'].includes(status) ? 'error' : ''}`;

  if (status === 'ok' && result.results && Array.isArray(result.results)) {
    cmdResult.innerHTML = '';
    const header = document.createElement('div');
    header.textContent = `✓ Found ${result.count} files for '${result.pattern}':`;
    header.style.marginBottom = '8px';
    cmdResult.appendChild(header);

    result.results.slice(0, 5).forEach(path => {
      const row = document.createElement('div');
      row.style.display = 'flex';
      row.style.justifyContent = 'space-between';
      row.style.alignItems = 'center';
      row.style.padding = '4px';
      row.style.borderBottom = '1px solid rgba(0,255,136,0.15)';

      const pSpan = document.createElement('span');
      pSpan.textContent = path.length > 55 ? '...' + path.slice(-52) : path;
      pSpan.title = path;
      pSpan.style.fontFamily = 'monospace';
      pSpan.style.fontSize = '12px';
      pSpan.style.color = '#ccc';
      pSpan.style.cursor = 'pointer';
      pSpan.onclick = () => handleOpenFile(path);
      pSpan.onmouseover = () => { pSpan.style.color = '#00ff88'; pSpan.style.textDecoration = 'underline'; };
      pSpan.onmouseout = () => { pSpan.style.color = '#ccc'; pSpan.style.textDecoration = 'none'; };

      const btnGroup = document.createElement('div');
      btnGroup.style.display = 'flex';
      btnGroup.style.gap = '6px';
      btnGroup.style.flexShrink = '0';
      
      const btnF = document.createElement('button');
      btnF.textContent = 'OPEN';
      btnF.className = 'action-btn';
      btnF.style.padding = '3px 8px';
      btnF.style.fontSize = '10px';
      btnF.onclick = () => handleOpenFile(path);

      const btnD = document.createElement('button');
      btnD.textContent = 'FOLDER';
      btnD.className = 'action-btn';
      btnD.style.padding = '3px 8px';
      btnD.style.fontSize = '10px';
      btnD.onclick = () => handleOpenFolder(path);

      btnGroup.appendChild(btnF);
      btnGroup.appendChild(btnD);
      row.appendChild(pSpan);
      row.appendChild(btnGroup);
      cmdResult.appendChild(row);
    });

    if (result.count > 5) {
      const more = document.createElement('div');
      more.textContent = `+ ${result.count - 5} more matches...`;
      more.style.fontSize = '11px';
      more.style.opacity = '0.6';
      more.style.marginTop = '6px';
      cmdResult.appendChild(more);
    }
  } else {
    cmdResult.textContent = messages[status] || `→ ${JSON.stringify(result).slice(0, 100)}`;
  }

  // Publish to event log
  await pollEvents();
}

cmdInput.addEventListener('keydown', (e) => {
  const isSuggestionsOpen = !cmdSuggestions.classList.contains("hidden") && currentSuggestions.length > 0;
  
  if (isSuggestionsOpen) {
    const items = cmdSuggestions.querySelectorAll(".suggestion-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (selectedSuggestionIndex === -1) cmdInput.dataset.original = cmdInput.value;
      selectedSuggestionIndex = (selectedSuggestionIndex + 1) % currentSuggestions.length;
      updateSuggestionSelection(items);
      cmdInput.value = currentSuggestions[selectedSuggestionIndex].text;
      return;
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (selectedSuggestionIndex === -1) {
        cmdInput.dataset.original = cmdInput.value;
        selectedSuggestionIndex = currentSuggestions.length - 1;
      } else {
        selectedSuggestionIndex = selectedSuggestionIndex - 1;
      }
      
      if (selectedSuggestionIndex < 0) {
        selectedSuggestionIndex = -1;
        cmdInput.value = cmdInput.dataset.original || '';
        updateSuggestionSelection(items);
      } else {
        updateSuggestionSelection(items);
        cmdInput.value = currentSuggestions[selectedSuggestionIndex].text;
      }
      return;
    } else if (e.key === "Enter" || e.key === "Tab") {
      if (selectedSuggestionIndex >= 0) {
        e.preventDefault();
        cmdInput.value = currentSuggestions[selectedSuggestionIndex].text;
        cmdSuggestions.classList.add("hidden");
        return;
      }
    }
  } else if (commandHistory.length > 0) {
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (historyIndex > 0) {
        historyIndex--;
        cmdInput.value = commandHistory[historyIndex];
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (historyIndex < commandHistory.length - 1) {
        historyIndex++;
        cmdInput.value = commandHistory[historyIndex];
      } else {
        historyIndex = commandHistory.length;
        cmdInput.value = "";
      }
    }
  }

  if (e.key === 'Enter') {
    const text = cmdInput.value.trim();
    if (text) {
      cmdSuggestions.classList.add("hidden");
      executeCommand(text);
      cmdInput.value = '';
    }
  }
  if (e.key === 'Escape') {
    cmdInput.value = '';
    cmdResult.textContent = '';
    cmdResult.className = 'cmd-result';
    cmdSuggestions.classList.add("hidden");
  }
});

cmdSend.addEventListener('click', () => {
  const text = cmdInput.value.trim();
  if (text) {
    cmdSuggestions.classList.add("hidden");
    executeCommand(text);
    cmdInput.value = '';
  }
});


// ============================================================
// QUICK ACTION BUTTONS
// ============================================================

document.querySelectorAll('.action-btn[data-cmd]').forEach(btn => {
  btn.addEventListener('click', () => {
    const cmd = btn.dataset.cmd;
    executeCommand(cmd);
  });
});

document.getElementById('act-panel').addEventListener('click', () => {
  if (window.pikina && window.pikina.togglePanel) {
    window.pikina.togglePanel();
  }
});

document.getElementById('act-events').addEventListener('click', () => {
  pollEvents();
});

document.getElementById('act-kill').addEventListener('click', () => {
  if (window.pikina) {
    window.pikina.killSwitch();
  }
  setReactorState('idle');
  cmdResult.textContent = '⏻ Kill-switch triggered — all daemons halted.';
  cmdResult.className   = 'cmd-result error';
});


// ============================================================
// WINDOW CONTROLS (frameless)
// ============================================================

document.getElementById('btn-min').addEventListener('click',   () => window.pikina && window.pikina.windowMinimize());
document.getElementById('btn-max').addEventListener('click',   () => window.pikina && window.pikina.windowMaximize());
document.getElementById('btn-close').addEventListener('click', () => window.pikina && window.pikina.windowClose());


// ============================================================
// POLLING SCHEDULE
// ============================================================

// Initial loads
pollTelemetry();
pollWeather();
pollEvents();
loadDeadlines();
pollStatus();

// Recurring intervals
setInterval(pollTelemetry, 2000);   // Telemetry every 2s
setInterval(pollEvents,    5000);   // Events every 5s
setInterval(pollWeather,   5 * 60 * 1000);  // Weather every 5min
setInterval(pollStatus,    10000);  // Status every 10s


// ---------------------------------------------------------------------------
// Smart Autocomplete & Graph Cache Integration
// ---------------------------------------------------------------------------

async function cachePath(path, isDir) {
  try {
    await fetch(`${window.pikina ? window.pikina.BACKEND : "http://localhost:5001"}/api/cache/path`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path: path, is_dir: isDir})
    });
  } catch(e) {}
}

function handleOpenFile(path) {
  cachePath(path, false);
  if (window.pikina) window.pikina.openFile(path);
}

function handleOpenFolder(path) {
  cachePath(path, true);
  if (window.pikina) window.pikina.openFolder(path);
}

const cmdSuggestions = document.getElementById("cmd-suggestions");
let suggestionTimeout = null;
let currentSuggestions = [];
let selectedSuggestionIndex = -1;

cmdInput.addEventListener("input", () => {
  const q = cmdInput.value;
  if (!q.trim()) {
    cmdSuggestions.classList.add("hidden");
    currentSuggestions = [];
    return;
  }

  clearTimeout(suggestionTimeout);
  suggestionTimeout = setTimeout(async () => {
    try {
      const res = await fetch(`${window.pikina ? window.pikina.BACKEND : "http://localhost:5001"}/api/suggest?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      currentSuggestions = data.suggestions || [];
      renderSuggestions();
    } catch (e) {
      console.error(e);
    }
  }, 300);
});

function renderSuggestions() {
  if (currentSuggestions.length === 0) {
    cmdSuggestions.classList.add("hidden");
    return;
  }
  
  selectedSuggestionIndex = -1;
  cmdSuggestions.innerHTML = "";
  cmdSuggestions.classList.remove("hidden");

  currentSuggestions.forEach((sugg, idx) => {
    const item = document.createElement("div");
    item.className = "suggestion-item";
    item.onclick = () => {
      cmdInput.value = sugg.text;
      cmdSuggestions.classList.add("hidden");
      cmdInput.focus();
    };

    const txt = document.createElement("span");
    txt.className = "sugg-text";
    txt.textContent = sugg.text;
    item.appendChild(txt);

    if (sugg.desc) {
      const desc = document.createElement("span");
      desc.className = "sugg-desc";
      desc.textContent = sugg.desc;
      item.appendChild(desc);
    }

    cmdSuggestions.appendChild(item);
  });
}

function updateSuggestionSelection(items) {
  items.forEach((item, idx) => {
    if (idx === selectedSuggestionIndex) {
      item.classList.add("selected");
    } else {
      item.classList.remove("selected");
    }
  });
}


// -- Modal Events --
const btnEvents = document.getElementById("act-events");
const modalEvents = document.getElementById("panel-events");
const modalBackdrop = document.getElementById("modal-backdrop");
const btnCloseEvents = document.getElementById("modal-close-events");

if (btnEvents) {
  btnEvents.addEventListener("click", () => {
    modalEvents.classList.remove("hidden");
    modalBackdrop.classList.remove("hidden");
    pollEvents();
  });
}
if (btnCloseEvents) {
  btnCloseEvents.addEventListener("click", () => {
    modalEvents.classList.add("hidden");
    modalBackdrop.classList.add("hidden");
  });
}
if (modalBackdrop) {
  modalBackdrop.addEventListener("click", () => {
    modalEvents.classList.add("hidden");
    modalBackdrop.classList.add("hidden");
  });
}


// -- Settings & Wallpaper --
const btnSettings = document.getElementById("btn-settings");
const modalSettings = document.getElementById("panel-settings");
const btnCloseSettings = document.getElementById("modal-close-settings");
const inputWallpaperUrl = document.getElementById("settings-wallpaper-url");
const btnSaveSettings = document.getElementById("settings-save-btn");
const statusSettings = document.getElementById("settings-status");

function applyWallpaper(url) {
  if (url) {
    document.body.style.backgroundImage = `url("${url}")`;
  } else {
    document.body.style.backgroundImage = "none";
  }
}

async function loadSettings() {
  try {
    const res = await fetch(`${BACKEND}/api/settings`);
    const data = await res.json();
    if (data.wallpaperUrl) {
      applyWallpaper(data.wallpaperUrl);
      inputWallpaperUrl.value = data.wallpaperUrl;
    }
  } catch(e) {}
}

if (btnSettings) {
  btnSettings.addEventListener("click", () => {
    modalSettings.classList.remove("hidden");
    modalBackdrop.classList.remove("hidden");
  });
}
if (btnCloseSettings) {
  btnCloseSettings.addEventListener("click", () => {
    modalSettings.classList.add("hidden");
    modalBackdrop.classList.add("hidden");
    statusSettings.textContent = "";
  });
}

// Extend backdrop close logic
if (modalBackdrop) {
  modalBackdrop.addEventListener("click", () => {
    if (modalSettings) modalSettings.classList.add("hidden");
  });
}

if (btnSaveSettings) {
  btnSaveSettings.addEventListener("click", async () => {
    const url = inputWallpaperUrl.value.trim();
    statusSettings.textContent = "Saving...";
    try {
      const res = await fetch(`${BACKEND}/api/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wallpaperUrl: url })
      });
      if (res.ok) {
        statusSettings.textContent = "Saved!";
        applyWallpaper(url);
      } else {
        statusSettings.textContent = "Failed to save.";
      }
    } catch(e) {
      statusSettings.textContent = "Error saving settings.";
    }
  });
}

if (window.pikina && window.pikina.onTriggerWallpaper) {
  window.pikina.onTriggerWallpaper(async () => {
    try {
      const res = await fetch(`${BACKEND}/api/wallpaper/random`, { method: "POST" });
      const data = await res.json();
      if (data.wallpaperUrl) {
        applyWallpaper(data.wallpaperUrl);
        inputWallpaperUrl.value = data.wallpaperUrl;
      }
    } catch(e) {}
  });
}

loadSettings();

