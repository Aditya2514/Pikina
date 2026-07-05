'use strict';

const BACKEND = (window.pikina && window.pikina.BACKEND) || 'http://localhost:5001';

const ring       = document.getElementById('status-ring');
const statusText = document.getElementById('status-text');
const input      = document.getElementById('panel-input');
const result     = document.getElementById('panel-result');

// ── State ──
function setState(state) {
  ring.className = `status-ring ${state === 'idle' ? '' : state}`;
  statusText.textContent = state.toUpperCase();
  statusText.style.color = {
    idle:      'var(--c-cyan)',
    thinking:  'var(--c-warn)',
    listening: 'var(--c-ok)',
  }[state] || 'var(--c-cyan)';
}

// ── Command ──
async function runCommand(text) {
  if (!text.trim()) return;

  setState('thinking');
  result.textContent = '';
  result.className   = 'panel-result';

  let res;
  try {
    if (window.pikina && window.pikina.sendCommand) {
      res = await window.pikina.sendCommand(text);
    } else {
      const r = await fetch(`${BACKEND}/api/command`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ text, source: 'user_typed' }),
      });
      res = await r.json();
    }
  } catch (err) {
    res = { status: 'error', reason: `Backend offline: ${err.message}` };
  }

  setState('idle');

  const status = res.status || 'unknown';
  const messages = {
    ok:       `✓ ${res.launched || res.message || 'Done'}`,
    denied:   `⊘ Denied`,
    rejected: `⊘ Rejected — untrusted provenance`,
    no_match: `? No route matched: "${text}"`,
    error:    `✗ ${res.reason || 'Error'}`,
  };

  result.textContent = messages[status] || `→ ${JSON.stringify(res).slice(0,80)}`;
  result.className   = `panel-result ${status === 'ok' ? 'ok' : 'error'}`;
}

// ── Input bindings ──
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    const cmd = input.value.trim();
    if (cmd) { runCommand(cmd); input.value = ''; }
  }
  if (e.key === 'Escape') {
    if (window.pikina) window.pikina.hidePanel();
  }
});

// ── Action buttons ──
document.querySelectorAll('.qb[data-cmd]').forEach(btn => {
  btn.addEventListener('click', () => runCommand(btn.dataset.cmd));
});

document.getElementById('qb-mic').addEventListener('click', () => {
  result.textContent = '🎤 Mic voice loop (Deferred to Phase 5)';
  result.className   = 'panel-result';
});

document.getElementById('qb-tasks').addEventListener('click', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/deadlines`);
    const d = await r.json();
    const count = (d.deadlines || []).length;
    result.textContent = `✓ ${count} active objectives`;
    result.className   = 'panel-result ok';
  } catch {
    result.textContent = 'Backend offline';
    result.className   = 'panel-result error';
  }
});

document.getElementById('qb-snip').addEventListener('click', () => {
  result.textContent = '✂ Vision proxy / snip (Deferred to Phase 5)';
  result.className   = 'panel-result';
});

document.getElementById('qb-kill').addEventListener('click', () => {
  if (window.pikina) window.pikina.killSwitch();
  result.textContent = '⏻ Kill-switch triggered — router & daemons halted.';
  result.className   = 'panel-result error';
  setState('idle');
});

document.getElementById('qb-log').addEventListener('click', async () => {
  try {
    const r = await fetch(`${BACKEND}/api/events?since=30`);
    const d = await r.json();
    result.textContent = `≡ ${d.count} events logged in last 30 min`;
    result.className   = 'panel-result ok';
  } catch {
    result.textContent = 'Backend offline';
    result.className   = 'panel-result error';
  }
});

// Focus input on load
input.focus();
