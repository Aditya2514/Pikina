/**
 * Starts the Python backend (`pikina-server`) when the HUD opens and nothing is
 * listening yet, and stops it again when the HUD quits. If you already run the
 * backend yourself, it is left alone.
 *
 * Where it looks for the server, in order:
 *   1. PIKINA_SERVER_CMD (full path to an executable)
 *   2. a virtualenv in the repository (.venv/ or venv/)
 *   3. `pikina-server` on PATH
 */
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const BACKEND_URL = 'http://127.0.0.1:5001';
const REPO_ROOT = path.join(__dirname, '..', '..');
let child = null;

async function isUp() {
  try {
    await fetch(`${BACKEND_URL}/api/status`, { signal: AbortSignal.timeout(1000) });
    return true; // any HTTP answer (even 401) means the server is there
  } catch (_) {
    return false;
  }
}

function candidates() {
  const exe = process.platform === 'win32' ? 'pikina-server.exe' : 'pikina-server';
  const bin = process.platform === 'win32' ? 'Scripts' : 'bin';
  const list = [];
  if (process.env.PIKINA_SERVER_CMD) list.push(process.env.PIKINA_SERVER_CMD);
  for (const venv of ['.venv', 'venv']) {
    const p = path.join(REPO_ROOT, venv, bin, exe);
    if (fs.existsSync(p)) list.push(p);
  }
  list.push('pikina-server');
  return list;
}

function trySpawn(cmd) {
  return new Promise((resolve) => {
    const proc = spawn(cmd, [], { stdio: 'inherit', windowsHide: true });
    proc.once('error', () => resolve(null));    // not found
    proc.once('spawn', () => resolve(proc));
  });
}

async function ensureBackend() {
  if (await isUp()) return 'external';
  for (const cmd of candidates()) {
    const proc = await trySpawn(cmd);
    if (!proc) continue;
    child = proc;
    child.once('exit', () => { child = null; });
    for (let i = 0; i < 40 && child; i++) {
      if (await isUp()) return 'started';
      await new Promise((r) => setTimeout(r, 500));
    }
    return child ? 'slow' : 'crashed';
  }
  console.error('[Backend] Could not find pikina-server. Install it with: pip install -e .  (or set PIKINA_SERVER_CMD)');
  return 'missing';
}

function stopBackend() {
  if (child) {
    child.kill();
    child = null;
  }
}

module.exports = { ensureBackend, stopBackend, BACKEND_URL };
