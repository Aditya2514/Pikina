/**
 * Pikina OS — Electron Main Process
 * Manages windows, global hotkeys, and tray icon.
 */

const { app, BrowserWindow, globalShortcut, Tray, Menu, ipcMain, nativeImage } = require('electron');
const path = require('path');

const BACKEND_URL = 'http://localhost:5001';
const IS_DEV      = process.argv.includes('--dev');

let dashboardWindow = null;
let panelWindow     = null;
let tray            = null;

// Single Instance Lock: Prevent duplicate processes from spawning and choking resources
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
  return;
} else {
  app.on('second-instance', (event, commandLine, workingDirectory) => {
    if (dashboardWindow) {
      if (dashboardWindow.isMinimized()) dashboardWindow.restore();
      dashboardWindow.show();
      dashboardWindow.focus();
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
    winIcon = nativeImage.createFromPath(path.join(__dirname, 'shared', 'icon.png'));
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

  dashboardWindow.loadFile(path.join(__dirname, 'dashboard', 'index.html'));

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

  panelWindow.loadFile(path.join(__dirname, 'quick_panel', 'panel.html'));

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
  const iconPath = path.join(__dirname, 'shared', 'tray_icon.png');
  let icon;
  try {
    icon = nativeImage.createFromPath(iconPath);
  } catch {
    icon = nativeImage.createEmpty();
  }

  tray = new Tray(icon);
  tray.setToolTip('Pikina OS');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Dashboard',  click: () => dashboardWindow ? dashboardWindow.show() : createDashboard() },
    { label: 'Quick Panel',     click: togglePanel },
    { type:  'separator' },
    { label: 'Quit Pikina',     click: () => app.quit() },
  ]));
  tray.on('double-click', () => dashboardWindow ? dashboardWindow.show() : createDashboard());
}

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------

// Forward command from renderer to Python backend
ipcMain.handle('send-command', async (_, text) => {
  try {
    const res = await fetch(`${BACKEND_URL}/api/command`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ text, source: 'user_typed' }),
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
ipcMain.on('kill-switch', () => {
  console.log('[Main] Kill-switch triggered — halting all daemons.');
  // Phase 2+ will halt daemon processes here
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

app.whenReady().then(() => {
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
  globalShortcut.unregisterAll();
});

app.on('window-all-closed', () => {
  // Keep running in tray on Windows/Linux
  if (process.platform === 'darwin') app.quit();
});
