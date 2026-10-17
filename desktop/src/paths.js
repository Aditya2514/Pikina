/**
 * Pikina's per-user data folder — must match src/pikina/paths.py.
 *   Windows  %LOCALAPPDATA%\Pikina
 *   macOS    ~/Library/Application Support/Pikina
 *   Linux    $XDG_DATA_HOME/pikina (default ~/.local/share/pikina)
 * PIKINA_DATA_DIR overrides it.
 */
const os = require('os');
const path = require('path');

function dataDir() {
  const override = process.env.PIKINA_DATA_DIR;
  if (override) return override.replace(/^~(?=$|[\\/])/, os.homedir());
  if (process.platform === 'win32') {
    return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'Pikina');
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'Pikina');
  }
  return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'), 'pikina');
}

const tokenFile = () => path.join(dataDir(), '.api_token');

module.exports = { dataDir, tokenFile };
