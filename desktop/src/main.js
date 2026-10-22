/**
 * Pikina OS — Electron Main Process
 * Manages windows, global hotkeys, and tray icon.
 */

const { app, BrowserWindow, globalShortcut, Tray, Menu, ipcMain, nativeImage, session } = require('electron');
const path = require('path');
const fs   = require('fs');
const { tokenFile } = require('./paths');
const { ensureBackend, stopBackend } = require('./backend');

const BACKEND_URL = 'http://127.0.0.1:5001';
const RENDERER    = path.join(__dirname, 'renderer');

// The backend writes a random API token into the data folder on first start and
// rejects requests without it. Read it fresh each time so a backend that starts
// after the HUD still works.
function apiToken() {
  try { return fs.readFileSync(tokenFile(), 'utf8').trim(); } catch (_) { return ''; }
}
const IS_DEV      = process.argv.includes('--dev');

let dashboardWindow = null;
let panelWindow     = null;
let tray            = null;
let trayActivityPaused = false;  // what the tray's "Pause activity log" tick shows
let trayActivityTimer  = null;
let trayMenuOpen       = false;
let trayMenuStale      = false;
let trayActivitySeq    = 0;      // bumped by each tray toggle; older status reads are dropped

// Single Instance Lock: Prevent duplicate processes from spawning and choking resources
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
  return;
} else {
  app.on('second-instance', (event, commandLine, workingDirectory) => {
    // Launching Pikina again always brings the dashboard back, even after ✕ closed it
    // (the app keeps running for the quick panel, and many Linux desktops have no tray).
    if (dashboardWindow) {
      if (dashboardWindow.isMinimized()) dashboardWindow.restore();
      dashboardWindow.show();
      dashboardWindow.focus();
    } else if (app.isReady()) {
      createDashboard();
    }
  });
}

// ---------------------------------------------------------------------------
// Dashboard window
// ---------------------------------------------------------------------------

function createDashboard() {
  // Load icon safely — don't crash if file is missing/corrupt
  let winIcon;
  try {
    winIcon = nativeImage.createFromPath(path.join(RENDERER, 'shared', 'icon.png'));
    if (winIcon.isEmpty()) winIcon = nativeImage.createEmpty();
  } catch (_) {
    winIcon = nativeImage.createEmpty();
  }

  dashboardWindow = new BrowserWindow({
    width:  1280,
    height: 800,
    minWidth:  1024,
    minHeight: 640,
    backgroundColor: '#020810',
    frame: false,
    titleBarStyle: 'hidden',
    transparent: false,
    resizable: true,
    show: false,  // Hidden until ready-to-show fires
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
    },
    icon: winIcon,
  });

  // Guarantee the window becomes visible once DOM is ready
  dashboardWindow.once('ready-to-show', () => {
    console.log('[Main] ready-to-show fired — showing dashboard');
    dashboardWindow.center();
    dashboardWindow.setAlwaysOnTop(true); // Force to front of all windows
    dashboardWindow.show();
    dashboardWindow.focus();
    // Release always-on-top after 3 seconds
    setTimeout(() => {
      if (dashboardWindow) dashboardWindow.setAlwaysOnTop(false);
    }, 3000);
  });

  // Fallback: force-show after 5s if ready-to-show never fires
  setTimeout(() => {
    if (dashboardWindow && !dashboardWindow.isVisible()) {
      console.log('[Main] Fallback: forcing dashboard show after timeout');
      dashboardWindow.show();
      dashboardWindow.focus();
    }
  }, 5000);

  // Log any page load failures
  dashboardWindow.webContents.on('did-fail-load', (event, errorCode, errorDesc) => {
    console.error('[Main] Dashboard failed to load:', errorCode, errorDesc);
  });

  dashboardWindow.loadFile(path.join(RENDERER, 'dashboard', 'index.html'));

  if (IS_DEV) {
    dashboardWindow.webContents.openDevTools({ mode: 'detach' });
  }

  dashboardWindow.on('closed', () => { dashboardWindow = null; });
}

// ---------------------------------------------------------------------------
// Quick Panel window
// ---------------------------------------------------------------------------

function createPanel() {
  panelWindow = new BrowserWindow({
    width:  480,
    height: 340,
    frame:  false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable:   false,
    transparent: true,
    show:        false,
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
    },
  });

  panelWindow.loadFile(path.join(RENDERER, 'quick_panel', 'panel.html'));

  // Hide instead of destroy when user closes/blurs
  panelWindow.on('blur',   () => panelWindow && panelWindow.hide());
  panelWindow.on('closed', () => { panelWindow = null; });
}

// ---------------------------------------------------------------------------
// Toggle panel
// ---------------------------------------------------------------------------

function togglePanel() {
  if (!panelWindow) { createPanel(); }

  if (panelWindow.isVisible()) {
    panelWindow.hide();
  } else {
    // Center on screen
    const { screen } = require('electron');
    const display = screen.getPrimaryDisplay();
    const { width, height } = display.workAreaSize;
    panelWindow.setPosition(
      Math.floor(width  / 2 - 240),
      Math.floor(height / 2 - 170),
    );
    panelWindow.show();
    panelWindow.focus();
  }
}

// ---------------------------------------------------------------------------
// System tray
// ---------------------------------------------------------------------------

function createTray() {
  // Use a minimal 16x16 icon; fallback to empty image if file not found
  const iconPath = path.join(RENDERER, 'shared', 'tray_icon.png');
  let icon;
  try {
    icon = nativeImage.createFromPath(iconPath);
  } catch {
    icon = nativeImage.createEmpty();
  }

  tray = new Tray(icon);
  tray.setToolTip('Pikina OS');
  rebuildTrayMenu();
  tray.on('double-click', () => dashboardWindow ? dashboardWindow.show() : createDashboard());

  // Keep the "Pause activity log" tick in sync with the backend (it can also be
  // changed from the dashboard). Linux tray menus emit no "opened" event, so poll.
  refreshTrayActivity();
  trayActivityTimer = setInterval(refreshTrayActivity, 15000);
}

function rebuildTrayMenu() {
  if (!tray || tray.isDestroyed()) return;
  // Don't swap the menu out while Windows/macOS have it on screen: rebuild once
  // it closes. (Linux tray menus are updated by calling setContextMenu again.)
  if (trayMenuOpen) { trayMenuStale = true; return; }
  trayMenuStale = false;
  const menu = Menu.buildFromTemplate([
    { label: 'Open Dashboard',  click: () => dashboardWindow ? dashboardWindow.show() : createDashboard() },
    { label: 'Quick Panel',     click: togglePanel },
    { type:  'separator' },
    { label: 'Pause activity log', type: 'checkbox', checked: trayActivityPaused, click: toggleTrayActivityPause },
    { type:  'separator' },
    { label: 'Quit Pikina',     click: () => app.quit() },
  ]);
  menu.on('menu-will-show', () => {
    if (process.platform !== 'linux') trayMenuOpen = true;
    refreshTrayActivity();
  });
  menu.on('menu-will-close', () => {
    trayMenuOpen = false;
    if (trayMenuStale) setImmediate(rebuildTrayMenu);
  });
  tray.setContextMenu(menu);
}

async function refreshTrayActivity() {
  const seq = trayActivitySeq;
  try {
    const res  = await fetch(`${BACKEND_URL}/api/status`, {
      headers: { 'X-Pikina-Token': apiToken() },
      signal:  AbortSignal.timeout(3000),
    });
    const data = await res.json();
    if (!res.ok || !data || !data.activity) return;
    // A tray toggle started after this read: its answer is newer than ours.
    if (seq !== trayActivitySeq) return;
    const paused = !!data.activity.paused;
    if (paused !== trayActivityPaused) {
      trayActivityPaused = paused;
      rebuildTrayMenu();
    }
  } catch (_) { /* backend offline: keep the last known state */ }
}

async function toggleTrayActivityPause(menuItem) {
  // Electron flips the tick before calling us, so menuItem.checked is the
  // opposite of what the user saw: that is the state they asked for.
  const want = menuItem ? !!menuItem.checked : !trayActivityPaused;
  trayActivitySeq++;
  try {
    const res  = await fetch(`${BACKEND_URL}/api/activity/settings`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-Pikina-Token': apiToken() },
      body:    JSON.stringify({ paused: want }),
      signal:  AbortSignal.timeout(5000),
    });
    const data = await res.json();
    if (res.ok && data && data.activity) trayActivityPaused = !!data.activity.paused;
  } catch (_) { /* offline: fall through and show the last known state */ }
  trayActivitySeq++;  // also drop reads sent while the POST was in flight
  // Always rebuild (the menu has closed once an item was clicked): on failure
  // (offline, kill switch) this puts the tick back.
  trayMenuOpen = false;
  rebuildTrayMenu();
}

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------

// Forward command from renderer to Python backend. `origin` tells the activity
// log where the command was typed; `source` must stay 'user_typed' (trust).
ipcMain.handle('send-command', async (event, text) => {
  const fromPanel = panelWindow && !panelWindow.isDestroyed()
    && event.sender === panelWindow.webContents;
  const origin = fromPanel ? 'quick_panel' : 'typed';
  try {
    const res = await fetch(`${BACKEND_URL}/api/command`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-Pikina-Token': apiToken() },
      body:    JSON.stringify({ text, source: 'user_typed', origin }),
    });
    return await res.json();
  } catch (err) {
    return { status: 'error', reason: `Backend unreachable: ${err.message}` };
  }
});

// Pass hide-panel and toggle-panel signals
ipcMain.on('hide-panel',   () => { if (panelWindow) panelWindow.hide(); });
ipcMain.on('toggle-panel', () => togglePanel());

// Pass kill-switch signal
ipcMain.on('kill-switch', async () => {
  console.log('[Main] Kill-switch triggered — halting all actions and daemons.');
  try {
    await fetch(`${BACKEND_URL}/api/killswitch`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-Pikina-Token': apiToken() },
      body:    '{}',
    });
  } catch (err) {
    console.error('[Main] Kill-switch could not reach backend:', err.message);
  }
});

// Window controls from frameless dashboard
ipcMain.on('window-minimize', () => { if (dashboardWindow) dashboardWindow.minimize(); });
ipcMain.on('window-maximize', () => {
  if (!dashboardWindow) return;
  dashboardWindow.isMaximized() ? dashboardWindow.restore() : dashboardWindow.maximize();
});
ipcMain.on('window-close', () => { if (dashboardWindow) dashboardWindow.close(); });

// Native file system interactions
const { shell } = require('electron');
ipcMain.on('open-file',   (_, filePath) => shell.openPath(filePath));
ipcMain.on('open-folder', (_, filePath) => shell.showItemInFolder(filePath));
ipcMain.on('open-url',    (_, url)      => shell.openExternal(url).catch(err => console.error('Failed to open URL:', err)));

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(async () => {
  // Start the Python backend unless one is already running, and give it a few seconds
  // before the dashboard opens, so the HUD's first requests don't all fail. If it is slow
  // the HUD opens anyway, shows "BACKEND OFFLINE", and loads everything once it answers.
  const backendReady = ensureBackend().then((state) => { console.log(`[Main] Backend: ${state}`); return state; });
  await Promise.race([backendReady, new Promise((resolve) => setTimeout(resolve, 8000))]);

  // Attach the API token to every renderer request to the backend.
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: ['http://localhost:5001/*', 'http://127.0.0.1:5001/*'] },
    (details, callback) => {
      details.requestHeaders['X-Pikina-Token'] = apiToken();
      callback({ requestHeaders: details.requestHeaders });
    },
  );

  createDashboard();
  createPanel();
  createTray();

  // Global hotkey: Ctrl+Shift+Space -> toggle quick panel
  globalShortcut.register('CommandOrControl+Shift+Space', togglePanel);
  console.log('[Main] Global hotkey registered: Ctrl+Shift+Space');

  // Global hotkey: Ctrl+Shift+W -> toggle wallpaper
  globalShortcut.register('CommandOrControl+Shift+W', () => {
    if (dashboardWindow) {
      dashboardWindow.webContents.send('trigger-wallpaper');
    }
  });
  console.log('[Main] Global hotkey registered: Ctrl+Shift+W');

  app.on('activate', () => { if (!dashboardWindow) createDashboard(); });
});

app.on('will-quit', () => {
  if (trayActivityTimer) clearInterval(trayActivityTimer);
  globalShortcut.unregisterAll();
  stopBackend();
});

app.on('window-all-closed', () => {
  app.quit();
});
