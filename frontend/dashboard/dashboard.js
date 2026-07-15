/**
 * Pikina OS — Dashboard JavaScript
 * Arc Reactor animation + live data polling from Python backend.
 */

'use strict';

const BACKEND = (window.pikina && window.pikina.BACKEND) || 'http://localhost:5001';

let calendarMiniObj = null;
let calendarDetailedObj = null;

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
  if (reactorStateEl) {
    reactorStateEl.className = `reactor-state ${state === 'idle' ? '' : state}`;
    reactorStateEl.textContent = state.toUpperCase();
  }
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

  // Intercept utility commands to update UI components live
  if (status === 'ok') {
    if (result.tool === 'utility.stopwatch') {
      const act = result.action;
      if (act === 'start') {
        const btn = document.getElementById('timer-start');
        if (btn) btn.click();
      } else if (act === 'pause') {
        const btn = document.getElementById('timer-pause');
        if (btn) btn.click();
      } else if (act === 'reset') {
        const btn = document.getElementById('timer-reset');
        if (btn) btn.click();
      }
    } else if (result.tool === 'utility.calculate') {
      const screen = document.getElementById('calc-screen');
      if (screen) {
        screen.textContent = Number(result.result.toFixed(6));
        const tab = document.getElementById('tab-calc');
        if (tab) tab.click();
      }
    }
  }

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

    const handleOpenFile = (filePath) => {
      if (window.pikina && window.pikina.openFile) {
        window.pikina.openFile(filePath);
      }
    };

    const handleOpenFolder = (filePath) => {
      if (window.pikina && window.pikina.openFolder) {
        window.pikina.openFolder(filePath);
      }
    };

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
  } else if (status === 'success' && result.results && result.results.length > 0) {
    cmdResult.innerHTML = '';
    
    result.results.forEach((memString, index) => {
        const memoryBox = document.createElement('div');
        memoryBox.style.cursor = 'pointer';
        memoryBox.style.whiteSpace = 'nowrap';
        memoryBox.style.overflow = 'hidden';
        memoryBox.style.textOverflow = 'ellipsis';
        memoryBox.style.maxWidth = '100%';
        memoryBox.style.textAlign = 'left';
        memoryBox.style.color = '#00d4ff';
        memoryBox.style.padding = '4px 0';
        if (index > 0) memoryBox.style.borderTop = '1px solid rgba(0, 212, 255, 0.1)';
        memoryBox.textContent = `→ ${memString}`;
        
        // Toggle full text on click
        memoryBox.onclick = () => {
           if (memoryBox.style.whiteSpace === 'nowrap') {
               memoryBox.style.whiteSpace = 'pre-wrap';
               memoryBox.style.padding = '8px';
               memoryBox.style.border = '1px solid rgba(0, 212, 255, 0.3)';
               memoryBox.style.background = 'rgba(0, 212, 255, 0.05)';
           } else {
               memoryBox.style.whiteSpace = 'nowrap';
               memoryBox.style.padding = '4px 0';
               memoryBox.style.border = 'none';
               if (index > 0) memoryBox.style.borderTop = '1px solid rgba(0, 212, 255, 0.1)';
               memoryBox.style.background = 'transparent';
           }
        };
        cmdResult.appendChild(memoryBox);
    });
  } else {
    cmdResult.textContent = messages[status] || `→ ${JSON.stringify(result).slice(0, 100)}`;
  }

  // Publish to event log
  await pollEvents();
  
  // Reload deadlines (todos) and refetch calendar events
  try {
    loadDeadlines();
    if (calendarMiniObj) {
      calendarMiniObj.refetchEvents();
    }
    if (calendarDetailedObj) {
      calendarDetailedObj.refetchEvents();
    }
  } catch (e) {}
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
  cmdResult.textContent = '';
  cmdResult.className = 'cmd-result';
  cmdResult.innerHTML = '';
  
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


// ============================================================
// CHRONOLOGY (CALENDAR) INTEGRATION
// ============================================================

function initMiniCalendar() {
  const el = document.getElementById('calendar-mini');
  if (!el || typeof FullCalendar === 'undefined') return;

  calendarMiniObj = new FullCalendar.Calendar(el, {
    initialView: 'dayGridMonth',
    headerToolbar: {
      left: '',
      center: 'title',
      right: ''
    },
    editable: false,
    selectable: false,
    dayMaxEvents: 1, // Restrict to max 1 event visually in mini calendar
    events: async function(info, successCallback, failureCallback) {
      try {
        const start = info.startStr;
        const end = info.endStr;
        const res = await fetch(`${BACKEND}/api/calendar?start=${start}&end=${end}`, { signal: AbortSignal.timeout(3000) });
        const events = await res.json();
        
        const mapped = events.map(ev => {
          const type = (ev.extendedProps && ev.extendedProps.type) || 'personal';
          return {
            ...ev,
            className: `fc-event-${type}`
          };
        });
        successCallback(mapped);
      } catch (err) {
        failureCallback(err);
      }
    }
  });
  calendarMiniObj.render();
}

function initDetailedCalendar() {
  const el = document.getElementById('calendar-detailed');
  if (!el || typeof FullCalendar === 'undefined') return;

  const updateEventDropOrResize = async (info) => {
    const ev = info.event;
    const yr = ev.start.getFullYear();
    const mo = String(ev.start.getMonth() + 1).padStart(2, '0');
    const dy = String(ev.start.getDate()).padStart(2, '0');
    const dateStr = `${yr}-${mo}-${dy}`;
    const timeStr = ev.allDay ? null : ev.start.toTimeString().split(' ')[0].substring(0, 5);

    const type = (ev.extendedProps && ev.extendedProps.type) || 'personal';
    const recurring = (ev.extendedProps && ev.extendedProps.recurring) || 'none';
    const source = (ev.extendedProps && ev.extendedProps.source) || 'user';
    const desc = (ev.extendedProps && ev.extendedProps.description) || '';

    try {
      const res = await fetch(`${BACKEND}/api/capability`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool: 'calendar.update_event',
          params: {
            event_id: ev.id,
            title: ev.title,
            date: dateStr,
            time: timeStr,
            type: type,
            recurring: recurring,
            source: source,
            description: desc
          }
        })
      });
      const data = await res.json();
      if (data.status !== 'ok') {
        alert(`Failed to save event changes: ${data.reason || 'Unknown error'}`);
        info.revert();
      } else {
        if (calendarMiniObj) calendarMiniObj.refetchEvents();
      }
    } catch (err) {
      alert(`Network error saving calendar changes: ${err.message}`);
      info.revert();
    }
  };

  calendarDetailedObj = new FullCalendar.Calendar(el, {
    initialView: 'dayGridMonth',
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek'
    },
    editable: true,
    selectable: true,
    selectMirror: true,
    dayMaxEvents: 3,
    select: function(info) {
      const startStr = info.startStr.split('T')[0];
      let startStrTime = '';
      if (info.startStr.includes('T')) {
        startStrTime = info.startStr.split('T')[1].substring(0, 5);
      }
      
      const createModal = document.getElementById('panel-create-event-modal');
      if (!createModal) return;

      document.getElementById('create-event-error').innerText = '';
      document.getElementById('create-event-title').value = '';
      document.getElementById('create-event-date').value = startStr;
      document.getElementById('create-event-time').value = startStrTime;
      document.getElementById('create-event-type').value = 'personal';
      document.getElementById('create-event-recurring').value = 'none';
      document.getElementById('create-event-desc').value = '';

      createModal.classList.remove('hidden');
      createModal.style.display = 'block';
      
      // Unselect grid
      calendarDetailedObj.unselect();
    },
    eventDrop: updateEventDropOrResize,
    eventResize: updateEventDropOrResize,
    events: async function(info, successCallback, failureCallback) {
      try {
        const start = info.startStr;
        const end = info.endStr;
        const res = await fetch(`${BACKEND}/api/calendar?start=${start}&end=${end}`, { signal: AbortSignal.timeout(3000) });
        const events = await res.json();
        
        const mapped = events.map(ev => {
          const type = (ev.extendedProps && ev.extendedProps.type) || 'personal';
          return {
            ...ev,
            className: `fc-event-${type}`
          };
        });
        successCallback(mapped);
      } catch (err) {
        failureCallback(err);
      }
    },
    eventClick: function(info) {
      const ev = info.event;
      // Fix date timezone offset shift using local Date properties
      const yr = ev.start.getFullYear();
      const mo = String(ev.start.getMonth() + 1).padStart(2, '0');
      const dy = String(ev.start.getDate()).padStart(2, '0');
      const startStr = `${yr}-${mo}-${dy}`;
      
      const timeStr = ev.allDay ? '' : ev.start.toTimeString().split(' ')[0].substring(0, 5);
      const type = (ev.extendedProps && ev.extendedProps.type) || 'personal';
      const source = (ev.extendedProps && ev.extendedProps.source) || 'user';
      const recurring = (ev.extendedProps && ev.extendedProps.recurring) || 'none';
      const description = (ev.extendedProps && ev.extendedProps.description) || '';
      
      const detailBody = document.getElementById('event-detail-body');
      const detailModal = document.getElementById('panel-event-detail-modal');
      const toggleBtn = document.getElementById('btn-edit-event-toggle');
      
      if (!detailBody || !detailModal) return;

      function renderViewMode() {
        if (toggleBtn) toggleBtn.style.display = 'block'; // Show Edit Icon
        detailBody.innerHTML = `
          <div style="margin-bottom: 12px; border-bottom: 1px solid rgba(0, 212, 255, 0.15); padding-bottom: 6px;">
            <strong style="color: var(--c-cyan); font-family: 'Orbitron', sans-serif; font-size: 13px;">${ev.title}</strong>
          </div>
          <div style="margin-bottom: 8px; font-size: 11px;">
            <span style="color: #778f9f; font-weight: bold; width: 80px; display: inline-block;">DATE:</span>
            <span style="color: #fff;">${startStr}</span>
          </div>
          <div style="margin-bottom: 8px; font-size: 11px;">
            <span style="color: #778f9f; font-weight: bold; width: 80px; display: inline-block;">TIME:</span>
            <span style="color: #fff;">${timeStr || 'All Day'}</span>
          </div>
          <div style="margin-bottom: 8px; font-size: 11px;">
            <span style="color: #778f9f; font-weight: bold; width: 80px; display: inline-block;">TYPE:</span>
            <span style="color: #fff;">${type.toUpperCase()}</span>
          </div>
          <div style="margin-bottom: 12px; font-size: 11px;">
            <span style="color: #778f9f; font-weight: bold; width: 80px; display: inline-block;">RECURRING:</span>
            <span style="color: #fff;">${recurring.toUpperCase()}</span>
          </div>
          <div style="border-top: 1px solid rgba(0, 212, 255, 0.1); padding-top: 8px;">
            <span style="color: #778f9f; font-size: 10px; font-weight: bold; display: block; margin-bottom: 4px;">DESCRIPTION:</span>
            <div style="color: #ccc; white-space: pre-wrap; font-size: 11px; max-height: 120px; overflow-y: auto; background: rgba(0,0,0,0.15); padding: 8px; border: 1px solid rgba(0,212,255,0.1); border-radius: 2px;">
              ${description || 'No description provided.'}
            </div>
          </div>
        `;
      }

      function renderEditMode() {
        if (toggleBtn) toggleBtn.style.display = 'none'; // Hide Edit Icon during editing
        
        // Generate time options dropdown
        let timeOptionsHtml = `<option value="" ${!timeStr ? 'selected' : ''}>All Day</option>`;
        let isStandardTime = !timeStr;
        for (let h = 0; h < 24; h++) {
          for (let m of ['00', '30']) {
            const hh = String(h).padStart(2, '0');
            const timeVal = `${hh}:${m}`;
            const selected = (timeStr === timeVal) ? 'selected' : '';
            if (selected) isStandardTime = true;
            timeOptionsHtml += `<option value="${timeVal}" ${selected}>${timeVal}</option>`;
          }
        }

        detailBody.innerHTML = `
          <input type="hidden" id="edit-event-id" value="${ev.id}">
          <input type="hidden" id="edit-event-source" value="${source}">
          
          <div style="margin-bottom: 10px;">
            <label style="color: var(--c-cyan); font-weight: bold; display: block; margin-bottom: 2px; font-size: 10px;">TITLE</label>
            <input type="text" id="edit-event-title" class="cmd-input" placeholder="${ev.title.replace(/"/g, '&quot;')}" value="${ev.title.replace(/"/g, '&quot;')}" style="width: 100%; box-sizing: border-box; padding: 6px; font-family: inherit; font-size: 11px; background: rgba(0,0,0,0.3); border: 1px solid rgba(0, 212, 255, 0.2); color: #fff;">
          </div>
          
          <div style="display: flex; gap: 8px; margin-bottom: 10px;">
            <div style="flex: 1;">
              <label style="color: var(--c-cyan); font-weight: bold; display: block; margin-bottom: 2px; font-size: 10px;">DATE</label>
              <input type="date" id="edit-event-date" class="cmd-input" value="${startStr}" style="width: 100%; box-sizing: border-box; padding: 5px; font-family: inherit; font-size: 11px; background: rgba(0,0,0,0.3); border: 1px solid rgba(0, 212, 255, 0.2); color: #fff;">
            </div>
            <div style="flex: 1;">
              <label style="color: var(--c-cyan); font-weight: bold; display: block; margin-bottom: 2px; font-size: 10px;">TIME</label>
              <select id="edit-event-time" class="cmd-input" style="width: 100%; box-sizing: border-box; padding: 6px; font-family: inherit; font-size: 11px; background: #020810; border: 1px solid rgba(0, 212, 255, 0.2); color: #fff;">
                ${timeOptionsHtml}
                <option value="custom" ${!isStandardTime ? 'selected' : ''}>Custom...</option>
              </select>
              <input type="text" id="edit-event-time-custom" class="cmd-input" placeholder="e.g. 14:15" value="${!isStandardTime ? timeStr : ''}" style="display: ${!isStandardTime ? 'block' : 'none'}; margin-top: 4px; width: 100%; box-sizing: border-box; padding: 5px; font-family: inherit; font-size: 11px; background: rgba(0,0,0,0.3); border: 1px solid rgba(0,212,255,0.2); color: #fff;">
            </div>
          </div>
          
          <div style="display: flex; gap: 8px; margin-bottom: 10px;">
            <div style="flex: 1;">
              <label style="color: var(--c-cyan); font-weight: bold; display: block; margin-bottom: 2px; font-size: 10px;">TYPE</label>
              <select id="edit-event-type" class="cmd-input" style="width: 100%; box-sizing: border-box; padding: 6px; font-family: inherit; font-size: 11px; background: #020810; border: 1px solid rgba(0, 212, 255, 0.2); color: #fff;">
                <option value="personal" ${type === 'personal' ? 'selected' : ''}>PERSONAL</option>
                <option value="work" ${type === 'work' ? 'selected' : ''}>WORK</option>
                <option value="holiday" ${type === 'holiday' ? 'selected' : ''}>HOLIDAY</option>
                <option value="other" ${type === 'other' ? 'selected' : ''}>OTHER</option>
              </select>
            </div>
            <div style="flex: 1;">
              <label style="color: var(--c-cyan); font-weight: bold; display: block; margin-bottom: 2px; font-size: 10px;">RECURRING</label>
              <select id="edit-event-recurring" class="cmd-input" style="width: 100%; box-sizing: border-box; padding: 6px; font-family: inherit; font-size: 11px; background: #020810; border: 1px solid rgba(0, 212, 255, 0.2); color: #fff;">
                <option value="none" ${recurring === 'none' ? 'selected' : ''}>NONE</option>
                <option value="daily" ${recurring === 'daily' ? 'selected' : ''}>DAILY</option>
                <option value="weekly" ${recurring === 'weekly' ? 'selected' : ''}>WEEKLY</option>
                <option value="monthly" ${recurring === 'monthly' ? 'selected' : ''}>MONTHLY</option>
                <option value="yearly" ${recurring === 'yearly' ? 'selected' : ''}>YEARLY</option>
              </select>
            </div>
          </div>

          <div style="margin-bottom: 14px;">
            <label style="color: var(--c-cyan); font-weight: bold; display: block; margin-bottom: 2px; font-size: 10px;">DESCRIPTION</label>
            <textarea id="edit-event-desc" class="cmd-input" placeholder="${description.replace(/"/g, '&quot;')}" style="width: 100%; box-sizing: border-box; padding: 6px; font-family: inherit; font-size: 11px; background: rgba(0,0,0,0.3); border: 1px solid rgba(0, 212, 255, 0.2); color: #fff; height: 50px; resize: vertical;">${description.replace(/"/g, '&quot;')}</textarea>
          </div>
          
          <div style="display: flex; gap: 8px; width: 100%;">
            <button class="cmd-send" id="btn-edit-event-save" style="flex: 1; padding: 6px; font-size: 10px; font-family: inherit;">SAVE CHANGES</button>
            <button class="cmd-send" id="btn-edit-event-delete" style="flex: 1; padding: 6px; font-size: 10px; font-family: inherit; background: rgba(255, 45, 85, 0.1); border-color: rgba(255, 45, 85, 0.3); color: #ff2d55;">DELETE</button>
          </div>
        `;

        // Bind Custom Time change listener
        const timeSelect = document.getElementById('edit-event-time');
        const customTimeInput = document.getElementById('edit-event-time-custom');
        if (timeSelect && customTimeInput) {
          timeSelect.onchange = () => {
            if (timeSelect.value === 'custom') {
              customTimeInput.style.display = 'block';
              customTimeInput.focus();
            } else {
              customTimeInput.style.display = 'none';
            }
          };
        }

        // Bind Save click
        document.getElementById('btn-edit-event-save').onclick = async () => {
          const id = document.getElementById('edit-event-id').value;
          const source = document.getElementById('edit-event-source').value;
          
          // Use input values or fallback to original values if left empty/blank
          const title = document.getElementById('edit-event-title').value.trim() || ev.title;
          const date = document.getElementById('edit-event-date').value || startStr;
          
          let selectedTime = document.getElementById('edit-event-time').value;
          if (selectedTime === 'custom') {
            selectedTime = document.getElementById('edit-event-time-custom').value.trim() || null;
          }
          
          const typeVal = document.getElementById('edit-event-type').value || type;
          const recurringVal = document.getElementById('edit-event-recurring').value || recurring;
          const descriptionVal = document.getElementById('edit-event-desc').value.trim() || description;

          try {
            const res = await fetch(`${BACKEND}/api/capability`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                tool: 'calendar.update_event',
                params: { event_id: id, title, date, time, type: typeVal, recurring: recurringVal, source, description: descriptionVal }
              })
            });
            const data = await res.json();
            if (data.status === 'ok') {
              detailModal.classList.add('hidden');
              detailModal.style.display = 'none';
              if (calendarMiniObj) calendarMiniObj.refetchEvents();
              if (calendarDetailedObj) calendarDetailedObj.refetchEvents();
            } else {
              alert(`Error saving: ${data.reason || 'Unknown error'}`);
            }
          } catch (e) {
            alert(`Network error: ${e.message}`);
          }
        };

        // Bind Delete click
        document.getElementById('btn-edit-event-delete').onclick = async () => {
          const id = document.getElementById('edit-event-id').value;
          if (!confirm("Are you sure you want to delete this event?")) return;

          try {
            const res = await fetch(`${BACKEND}/api/capability`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                tool: 'calendar.remove_event',
                params: { text: id }
              })
            });
            const data = await res.json();
            if (data.status === 'ok') {
              detailModal.classList.add('hidden');
              detailModal.style.display = 'none';
              if (calendarMiniObj) calendarMiniObj.refetchEvents();
              if (calendarDetailedObj) calendarDetailedObj.refetchEvents();
            } else {
              alert(`Error deleting: ${data.reason || 'Unknown error'}`);
            }
          } catch (e) {
            alert(`Network error: ${e.message}`);
          }
        };
      }

      // Initial show in View-Only mode
      renderViewMode();

      // Bind toggle to Edit mode
      if (toggleBtn) {
        toggleBtn.onclick = () => {
          renderEditMode();
        };
      }

      detailModal.classList.remove('hidden');
      detailModal.style.display = 'block';
    }
  });
  calendarDetailedObj.render();
}

// Initialize calendars
initMiniCalendar();
initDetailedCalendar();

// Toggle Chronology Modal (Popup detailed view)
const btnOpenCalendarModal = document.getElementById('panel-calendar');
const modalCalendar = document.getElementById('panel-calendar-modal');
const btnCloseCalendarModal = document.getElementById('modal-close-calendar');

if (btnOpenCalendarModal && modalCalendar && modalBackdrop) {
  btnOpenCalendarModal.addEventListener('click', () => {
    modalCalendar.classList.remove('hidden');
    modalCalendar.style.display = 'flex';
    modalBackdrop.classList.remove('hidden');
    
    // Force FullCalendar to recalculate bounds after making container visible
    setTimeout(() => {
      if (calendarDetailedObj) {
        calendarDetailedObj.updateSize();
      }
    }, 50);
  });
}

if (btnCloseCalendarModal) {
  btnCloseCalendarModal.addEventListener('click', () => {
    modalCalendar.classList.add('hidden');
    modalCalendar.style.display = 'none';
    modalBackdrop.classList.add('hidden');
  });
}

const modalEventDetail = document.getElementById('panel-event-detail-modal');
const btnCloseEventDetail = document.getElementById('modal-close-event-detail');

if (btnCloseEventDetail && modalEventDetail) {
  btnCloseEventDetail.addEventListener('click', () => {
    modalEventDetail.classList.add('hidden');
    modalEventDetail.style.display = 'none';
  });
}

if (modalBackdrop) {
  modalBackdrop.addEventListener('click', () => {
    if (modalCalendar) {
      modalCalendar.classList.add('hidden');
      modalCalendar.style.display = 'none';
    }
    if (modalEventDetail) {
      modalEventDetail.classList.add('hidden');
      modalEventDetail.style.display = 'none';
    }
    const createModal = document.getElementById('panel-create-event-modal');
    if (createModal) {
      createModal.classList.add('hidden');
      createModal.style.display = 'none';
    }
  });
}

// Bind Create Event controls
const btnSaveNewEvent = document.getElementById('btn-save-new-event');
if (btnSaveNewEvent) {
  btnSaveNewEvent.onclick = async () => {
    const title = document.getElementById('create-event-title').value.trim();
    const date = document.getElementById('create-event-date').value;
    const timeVal = document.getElementById('create-event-time').value || null;
    const typeVal = document.getElementById('create-event-type').value;
    const recurringVal = document.getElementById('create-event-recurring').value;
    const descriptionVal = document.getElementById('create-event-desc').value.trim();

    const errDiv = document.getElementById('create-event-error');
    if (!title) {
      errDiv.innerText = "Error: Event title is required.";
      return;
    }
    if (!date) {
      errDiv.innerText = "Error: Event date is required.";
      return;
    }

    try {
      const res = await fetch(`${BACKEND}/api/capability`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool: 'calendar.add_event',
          params: {
            title: title,
            date: date,
            time: timeVal,
            type: typeVal,
            recurring: recurringVal,
            description: descriptionVal
          }
        })
      });
      const data = await res.json();
      if (data.status === 'ok') {
        const createModal = document.getElementById('panel-create-event-modal');
        if (createModal) {
          createModal.classList.add('hidden');
          createModal.style.display = 'none';
        }
        
        if (calendarMiniObj) calendarMiniObj.refetchEvents();
        if (calendarDetailedObj) calendarDetailedObj.refetchEvents();
      } else {
        errDiv.innerText = `Error: ${data.reason || 'Unknown error'}`;
      }
    } catch (e) {
      errDiv.innerText = `Network Error: ${e.message}`;
    }
  };
}

const btnCloseCreateEvent = document.getElementById('modal-close-create-event');
if (btnCloseCreateEvent) {
  btnCloseCreateEvent.onclick = () => {
    const createModal = document.getElementById('panel-create-event-modal');
    if (createModal) {
      createModal.classList.add('hidden');
      createModal.style.display = 'none';
    }
  };
}

// ============================================================
// UTILITIES — Calculator and Stopwatch
// ============================================================

// 1. Collapsible Toggling and View Switching
const btnUtilCalc = document.getElementById('btn-utility-calc');
const btnUtilTimer = document.getElementById('btn-utility-timer');
const utilExpandContainer = document.getElementById('utilities-expand-container');
const calcView = document.getElementById('util-calc-view');
const timeView = document.getElementById('util-time-view');

let currentActiveUtil = null; // 'calc' or 'timer' or null (collapsed)

function toggleUtility(target) {
  if (currentActiveUtil === target) {
    // Collapse
    utilExpandContainer.style.display = 'none';
    currentActiveUtil = null;
    if (btnUtilCalc) {
      btnUtilCalc.style.background = 'none';
      btnUtilCalc.style.borderColor = 'rgba(0, 212, 255, 0.15)';
    }
    if (btnUtilTimer) {
      btnUtilTimer.style.background = 'none';
      btnUtilTimer.style.borderColor = 'rgba(0, 212, 255, 0.15)';
    }
  } else {
    // Expand & Swap
    utilExpandContainer.style.display = 'block';
    currentActiveUtil = target;
    
    if (target === 'calc') {
      if (calcView) calcView.style.display = 'block';
      if (timeView) timeView.style.display = 'none';
      
      if (btnUtilCalc) {
        btnUtilCalc.style.background = 'rgba(0, 212, 255, 0.15)';
        btnUtilCalc.style.borderColor = 'var(--c-cyan)';
      }
      if (btnUtilTimer) {
        btnUtilTimer.style.background = 'none';
        btnUtilTimer.style.borderColor = 'rgba(0, 212, 255, 0.15)';
      }
    } else {
      if (calcView) calcView.style.display = 'none';
      if (timeView) timeView.style.display = 'flex';
      
      if (btnUtilTimer) {
        btnUtilTimer.style.background = 'rgba(0, 212, 255, 0.15)';
        btnUtilTimer.style.borderColor = 'var(--c-cyan)';
      }
      if (btnUtilCalc) {
        btnUtilCalc.style.background = 'none';
        btnUtilCalc.style.borderColor = 'rgba(0, 212, 255, 0.15)';
      }
    }
  }
}

if (btnUtilCalc && btnUtilTimer && utilExpandContainer && calcView && timeView) {
  btnUtilCalc.onclick = () => toggleUtility('calc');
  btnUtilTimer.onclick = () => toggleUtility('timer');
}

// 2. Calculator Logic
const calcScreen = document.getElementById('calc-screen');
let displayExpression = '';
let codeExpression = '';

function clearCalcExpression() {
  displayExpression = '';
  codeExpression = '';
  if (calcScreen) calcScreen.textContent = '0';
}

function backspaceCalcExpression() {
  if (displayExpression.length > 0) {
    displayExpression = displayExpression.slice(0, -1);
    if (codeExpression.length > 0) {
      codeExpression = codeExpression.slice(0, -1);
    }
    if (calcScreen) {
      calcScreen.textContent = displayExpression || '0';
    }
  }
}

function appendCalcExpression(displayVal, codeVal) {
  if (calcScreen) {
    if (calcScreen.textContent === '0' || calcScreen.textContent === 'ERR') {
      displayExpression = '';
      codeExpression = '';
    }
  }
  displayExpression += displayVal;
  codeExpression += codeVal;
  if (calcScreen) {
    calcScreen.textContent = displayExpression;
  }
}

function evaluateCalcExpression() {
  try {
    if (codeExpression.trim()) {
      // Expose math functions as variables to make execution more robust
      const mathScope = {
        sin: Math.sin,
        cos: Math.cos,
        tan: Math.tan,
        log: Math.log,
        sqrt: Math.sqrt,
        PI: Math.PI,
        E: Math.E
      };
      const result = new Function(
        ...Object.keys(mathScope),
        `return (${codeExpression})`
      )(...Object.values(mathScope));
      
      const roundedResult = Number(Number(result).toFixed(8));
      if (calcScreen) calcScreen.textContent = isNaN(roundedResult) ? 'ERR' : roundedResult;
      displayExpression = isNaN(roundedResult) ? '' : String(roundedResult);
      codeExpression = isNaN(roundedResult) ? '' : String(roundedResult);
    }
  } catch (e) {
    if (calcScreen) calcScreen.textContent = 'ERR';
    displayExpression = '';
    codeExpression = '';
  }
}

document.querySelectorAll('.calc-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const displayVal = btn.dataset.display;
    const codeVal = btn.dataset.code;
    
    if (codeVal === 'C') {
      clearCalcExpression();
    } else if (codeVal === '=') {
      evaluateCalcExpression();
    } else {
      appendCalcExpression(displayVal, codeVal);
    }
  });
});

// Bind keyboard listener for calculator inputs
document.addEventListener('keydown', (e) => {
  // Only process if the calculator widget is currently expanded
  if (currentActiveUtil !== 'calc') return;

  // Ignore keyboard inputs if user is typing in a form input field, textarea or select dropdown
  const activeEl = document.activeElement;
  if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA' || activeEl.tagName === 'SELECT')) {
    return;
  }

  const key = e.key;
  if (/[0-9]/.test(key)) {
    appendCalcExpression(key, key);
    e.preventDefault();
  } else if (key === '.') {
    appendCalcExpression('.', '.');
    e.preventDefault();
  } else if (key === '+') {
    appendCalcExpression('+', '+');
    e.preventDefault();
  } else if (key === '-') {
    appendCalcExpression('-', '-');
    e.preventDefault();
  } else if (key === '*') {
    appendCalcExpression('*', '*');
    e.preventDefault();
  } else if (key === '/') {
    appendCalcExpression('/', '/');
    e.preventDefault();
  } else if (key === '(') {
    appendCalcExpression('(', '(');
    e.preventDefault();
  } else if (key === ')') {
    appendCalcExpression(')', ')');
    e.preventDefault();
  } else if (key === '^') {
    appendCalcExpression('^', '**');
    e.preventDefault();
  } else if (key === '%') {
    appendCalcExpression('%', '/100');
    e.preventDefault();
  } else if (key === 'Enter' || key === '=') {
    evaluateCalcExpression();
    e.preventDefault();
  } else if (key === 'Backspace') {
    backspaceCalcExpression();
    e.preventDefault();
  } else if (key === 'Escape' || key === 'c' || key === 'C') {
    clearCalcExpression();
    e.preventDefault();
  }
});

// 3. Stopwatch / Timer Logic
const timerDisplay = document.getElementById('timer-display');
const btnTimerStart = document.getElementById('timer-start');
const btnTimerPause = document.getElementById('timer-pause');
const btnTimerReset = document.getElementById('timer-reset');

let timerInterval = null;
let timerMs = 0;

function updateTimerDisplay() {
  const ms = Math.floor((timerMs % 1000) / 100);
  const totalSecs = Math.floor(timerMs / 1000);
  const secs = totalSecs % 60;
  const mins = Math.floor(totalSecs / 60) % 60;
  const hrs = Math.floor(totalSecs / 3600);
  
  const pad = (n) => String(n).padStart(2, '0');
  timerDisplay.textContent = `${pad(hrs)}:${pad(mins)}:${pad(secs)}.${ms}`;
}

if (btnTimerStart && btnTimerPause && btnTimerReset) {
  btnTimerStart.addEventListener('click', () => {
    if (timerInterval) return;
    let lastTime = Date.now();
    timerInterval = setInterval(() => {
      const now = Date.now();
      timerMs += (now - lastTime);
      lastTime = now;
      updateTimerDisplay();
    }, 50);
  });
  
  btnTimerPause.addEventListener('click', () => {
    clearInterval(timerInterval);
    timerInterval = null;
  });
  
  btnTimerReset.addEventListener('click', () => {
    clearInterval(timerInterval);
    timerInterval = null;
    timerMs = 0;
    updateTimerDisplay();
  });
}


