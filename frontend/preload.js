/**
 * Pikina OS — Preload Script
 * Secure bridge between the main process and renderer.
 * Exposes only the minimal API surface needed by the UI.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pikina', {
  // Send a typed command to the Python backend via main process
  sendCommand: (text) => ipcRenderer.invoke('send-command', text),

  // Signal to hide or toggle the quick panel
  hidePanel:   () => ipcRenderer.send('hide-panel'),
  togglePanel: () => ipcRenderer.send('toggle-panel'),

  // Trigger the global kill-switch
  killSwitch: () => ipcRenderer.send('kill-switch'),

  // Window controls (for frameless dashboard)
  windowMinimize: () => ipcRenderer.send('window-minimize'),
  windowMaximize: () => ipcRenderer.send('window-maximize'),
  windowClose:    () => ipcRenderer.send('window-close'),

  // Native file actions
  openFile:   (path) => ipcRenderer.send('open-file', path),
  openFolder: (path) => ipcRenderer.send('open-folder', path),
  openUrl:    (url)  => ipcRenderer.send('open-url', url),

  // Direct HTTP fetch to Python backend (renderer -> backend, no main involvement)
  BACKEND: 'http://localhost:5001',
});
