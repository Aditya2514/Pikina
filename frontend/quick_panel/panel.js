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

const commandHistory = [];
let historyIndex = -1;

const cmdSuggestions = document.getElementById("cmd-suggestions");
let suggestionTimeout = null;
let currentSuggestions = [];
let selectedSuggestionIndex = -1;

input.addEventListener("input", () => {
  const q = input.value;
  if (!q.trim()) {
    cmdSuggestions.classList.add("hidden");
    currentSuggestions = [];
    return;
  }

  clearTimeout(suggestionTimeout);
  suggestionTimeout = setTimeout(async () => {
    try {
      const res = await fetch(`${BACKEND}/api/suggest?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      currentSuggestions = data.suggestions || [];
      renderSuggestions();
    } catch (e) {}
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
      input.value = sugg.text;
      cmdSuggestions.classList.add("hidden");
      input.focus();
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

// ── Input bindings ──
input.addEventListener('keydown', (e) => {
  const isSuggestionsOpen = !cmdSuggestions.classList.contains("hidden") && currentSuggestions.length > 0;
  
  if (isSuggestionsOpen) {
    const items = cmdSuggestions.querySelectorAll(".suggestion-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (selectedSuggestionIndex === -1) input.dataset.original = input.value;
      selectedSuggestionIndex = (selectedSuggestionIndex + 1) % currentSuggestions.length;
      updateSuggestionSelection(items);
      input.value = currentSuggestions[selectedSuggestionIndex].text;
      return;
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (selectedSuggestionIndex === -1) {
        input.dataset.original = input.value;
        selectedSuggestionIndex = currentSuggestions.length - 1;
      } else {
        selectedSuggestionIndex = selectedSuggestionIndex - 1;
      }
      if (selectedSuggestionIndex < 0) {
        selectedSuggestionIndex = -1;
        input.value = input.dataset.original || '';
        updateSuggestionSelection(items);
      } else {
        updateSuggestionSelection(items);
        input.value = currentSuggestions[selectedSuggestionIndex].text;
      }
      return;
    } else if (e.key === "Enter" || e.key === "Tab") {
      if (selectedSuggestionIndex >= 0) {
        e.preventDefault();
        input.value = currentSuggestions[selectedSuggestionIndex].text;
        cmdSuggestions.classList.add("hidden");
        return;
      }
    }
  } else if (commandHistory.length > 0) {
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (historyIndex > 0) {
        historyIndex--;
        input.value = commandHistory[historyIndex];
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (historyIndex < commandHistory.length - 1) {
        historyIndex++;
        input.value = commandHistory[historyIndex];
      } else {
        historyIndex = commandHistory.length;
        input.value = "";
      }
    }
  }

  if (e.key === 'Enter') {
    const cmd = input.value.trim();
    if (cmd) {
      if (commandHistory[commandHistory.length - 1] !== cmd) {
        commandHistory.push(cmd);
      }
      historyIndex = commandHistory.length;
      runCommand(cmd);
      input.value = '';
      cmdSuggestions.classList.add("hidden");
    }
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
