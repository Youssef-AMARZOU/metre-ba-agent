const { app, BrowserWindow, ipcMain, dialog, shell, Menu, session } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const crypto = require('crypto');

// Jeton secret de session, partagé uniquement avec le backend Python
// (variable d'environnement) et injecté dans les requêtes de l'UI.
const API_TOKEN = crypto.randomBytes(32).toString('hex');
const TOKEN_HEADER = 'X-PlanBA-Token';

let mainWindow;
let splashWindow;
let pythonProcess = null;
let apiReady = false;
let appUrl = null;

const DEFAULT_PORT = 8765;
const HEALTH_TIMEOUT = 30000;
const HEALTH_INTERVAL = 500;

// ─── Find a free port ───
function findAvailablePort() {
  return new Promise((resolve) => {
    const server = require('net').createServer();
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
    server.on('error', () => resolve(DEFAULT_PORT));
  });
}

// ─── Health check ───
function checkHealth(url) {
  return new Promise((resolve) => {
    http.get(url + '/health', { timeout: 3000 }, (res) => {
      let data = '';
      res.on('data', (chunk) => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data).status === 'ok'); }
        catch { resolve(false); }
      });
    }).on('error', () => resolve(false));
  });
}

// ─── Wait for backend to be ready ───
async function waitForHealth(url, onProgress) {
  const start = Date.now();
  while (Date.now() - start < HEALTH_TIMEOUT) {
    if (await checkHealth(url)) return true;
    await new Promise(r => setTimeout(r, HEALTH_INTERVAL));
    if (onProgress) onProgress();
  }
  return false;
}

// ─── Splash window ───
function createSplash() {
  splashWindow = new BrowserWindow({
    width: 600, height: 500,
    frame: false, transparent: false,
    resizable: false, center: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false,
    },
  });
  splashWindow.loadFile(path.join(__dirname, 'src', 'splash.html'));
}

// ─── Eval in splash ───
function evalInSplash(js) {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.webContents.executeJavaScript(js).catch(() => {});
  }
}

function bootStage(id, status, detail) {
  const escaped = (detail || '').replace(/'/g, "\\'").replace(/\n/g, ' ');
  evalInSplash(`if(typeof bootStage==='function') bootStage('${id}','${status}','${escaped}');`);
}

function reportFatal(message) {
  const escaped = message.replace(/'/g, "\\'").replace(/\n/g, ' ');
  evalInSplash(`if(typeof setError==='function') setError('${escaped}');`);
}

// ─── Main window ───
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1400, height: 900,
    minWidth: 1100, minHeight: 700,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false,
    },
    titleBarStyle: 'hiddenInset',
    title: 'Métré BA Agent',
    icon: path.join(__dirname, 'assets', 'icon.png'),
  });
  mainWindow.loadFile(path.join(__dirname, 'src', 'index.html'));
}

// ─── App menu ───
function buildMenu() {
  const template = [
    {
      label: 'Métré BA',
      submenu: [
        { label: 'À propos', click: () => mainWindow?.webContents.send('show-about') },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'Édition',
      submenu: [
        { role: 'undo', label: 'Annuler' },
        { role: 'redo', label: 'Rétablir' },
        { type: 'separator' },
        { role: 'cut', label: 'Couper' },
        { role: 'copy', label: 'Copier' },
        { role: 'paste', label: 'Coller' },
        { role: 'selectAll', label: 'Tout sélectionner' },
      ],
    },
    {
      label: 'Affichage',
      submenu: [
        { role: 'reload', label: 'Recharger' },
        { role: 'forceReload', label: 'Recharger (forcer)' },
        { role: 'toggleDevTools', label: 'Outils de développement' },
        { type: 'separator' },
        { role: 'resetZoom', label: 'Réinitialiser le zoom' },
        { role: 'zoomIn', label: 'Zoomer' },
        { role: 'zoomOut', label: 'Dézoomer' },
        { type: 'separator' },
        { role: 'togglefullscreen', label: 'Plein écran' },
      ],
    },
    {
      label: 'Fenêtre',
      submenu: [
        { role: 'minimize', label: 'Réduire' },
        { role: 'maximize', label: 'Agrandir' },
        { role: 'close', label: 'Fermer' },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ─── Start Python backend ───
function startPythonBackend(port) {
  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  const backendDir = path.join(__dirname, '..');

  pythonProcess = spawn(pythonCmd, [
    '-m', 'uvicorn', 'backend.api:app',
    '--host', '127.0.0.1', '--port', String(port),
  ], {
    cwd: backendDir,
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PLANBA_API_TOKEN: API_TOKEN },
  });

  pythonProcess.stdout.on('data', (data) => {
    const msg = data.toString();
    if (msg.includes('Uvicorn running')) {
      apiReady = true;
      if (mainWindow) mainWindow.webContents.send('api-ready');
    }
  });

  pythonProcess.stderr.on('data', (data) => {
    const msg = data.toString();
    if (msg.includes('Uvicorn running')) {
      apiReady = true;
      if (mainWindow) mainWindow.webContents.send('api-ready');
    }
  });

  pythonProcess.on('close', (code) => {
    apiReady = false;
    pythonProcess = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend-stopped');
    }
  });

  pythonProcess.on('error', (err) => {
    reportFatal('Impossible de démarrer le backend: ' + err.message);
  });
}

// ─── App launch sequence ───
app.whenReady().then(async () => {
  buildMenu();
  createSplash();

  const port = await findAvailablePort();
  appUrl = `http://127.0.0.1:${port}`;

  // Ajoute le jeton à toutes les requêtes de l'UI vers le backend local,
  // sans l'exposer au code de la page (renderer).
  session.defaultSession.webRequest.onBeforeSendHeaders(
    { urls: [`http://127.0.0.1:${port}/*`] },
    (details, callback) => {
      details.requestHeaders[TOKEN_HEADER] = API_TOKEN;
      callback({ requestHeaders: details.requestHeaders });
    });

  bootStage('sidecar', 'active', 'Démarrage du serveur...');
  startPythonBackend(port);

  bootStage('health', 'active', 'Vérification...');

  const healthy = await waitForHealth(appUrl, () => {
    // Optional: update progress detail
  });

  if (!healthy) {
    bootStage('health', 'failed', 'Timeout');
    reportFatal('Le serveur backend n\'a pas répondu à temps. Vérifiez que Python et les dépendances sont installés.');
    return;
  }

  bootStage('sidecar', 'done', 'Serveur prêt');
  bootStage('health', 'done', `Port ${port}`);
  bootStage('addons', 'active', 'Chargement...');

  // Check addons
  try {
    const data = await new Promise((resolve, reject) => {
      http.get(appUrl + '/addons', { timeout: 5000, headers: { [TOKEN_HEADER]: API_TOKEN } }, (res) => {
        let body = '';
        res.on('data', (chunk) => body += chunk);
        res.on('end', () => {
          try { resolve(JSON.parse(body)); }
          catch { resolve(null); }
        });
      }).on('error', reject);
    });

    if (data && data.installed) {
      const count = Object.values(data.installed).filter(v => v).length;
      const total = Object.keys(data.installed).length;
      bootStage('addons', 'done', `${count}/${total} installés`);
    } else {
      bootStage('addons', 'done', 'Prêt');
    }
  } catch {
    bootStage('addons', 'done', 'Prêt');
  }

  // Transition to main window
  await new Promise(r => setTimeout(r, 600));

  createMainWindow();

  mainWindow.webContents.on('did-finish-load', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
      splashWindow = null;
    }
    mainWindow.show();
    mainWindow.webContents.send('app-url', appUrl);
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
});

app.on('window-all-closed', () => {
  if (pythonProcess) pythonProcess.kill();
  app.quit();
});

app.on('before-quit', () => {
  if (pythonProcess) pythonProcess.kill();
});

// ─── IPC handlers ───
ipcMain.handle('select-pdf', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [
      { name: 'Plans', extensions: ['pdf', 'png', 'jpg', 'jpeg'] },
      { name: 'PDF', extensions: ['pdf'] },
      { name: 'Images', extensions: ['png', 'jpg', 'jpeg'] },
    ],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('select-xlsx', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [{ name: 'Excel', extensions: ['xlsx', 'xlsm'] }],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('open-output', async (event, filePath) => {
  shell.showItemInFolder(filePath);
});

ipcMain.handle('download-file', async (event, filePath) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: path.basename(filePath),
  });
  if (!result.canceled) {
    fs.copyFileSync(filePath, result.filePath);
    return result.filePath;
  }
  return null;
});

ipcMain.handle('get-api-status', () => apiReady);
ipcMain.handle('get-app-url', () => appUrl);

ipcMain.handle('open-external', async (event, url) => {
  const lower = url.toLowerCase();
  if (lower.startsWith('http://') || lower.startsWith('https://') || lower.startsWith('mailto:')) {
    shell.openExternal(url);
  }
});

ipcMain.handle('open-log-file', () => {
  const logPath = path.join(require('os').homedir(), '.metre-ba-agent', 'launcher.log');
  if (fs.existsSync(logPath)) shell.showItemInFolder(logPath);
});

ipcMain.handle('use-local-server', () => {
  app.restart();
});
