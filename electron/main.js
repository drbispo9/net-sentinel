'use strict';

/*
 * NetSentinel — casca Electron (processo principal).
 *
 * Responsabilidade EXCLUSIVA desta casca:
 *   1. Exibir a aplicação web servida pelo FastAPI.
 *   2. Reagir a alertas emitidos pelo frontend (via IPC) disparando
 *      notificações nativas do SO.
 *   3. Sobrepor a tela com um overlay em alertas críticos.
 *
 * O endereço do backend é CONFIGURÁVEL pelo usuário (tela de configuração +
 * persistência em config.json). Assim o mesmo executável serve para apontar
 * para qualquer servidor onde o FastAPI esteja rodando — local ou remoto.
 *
 * NENHUMA lógica de monitoramento vive aqui. O Electron apenas REAGE a
 * eventos; nunca os origina. O backend continua sendo o FastAPI.
 */

const path = require('path');
const http = require('http');
const fs = require('fs');
const { app, BrowserWindow, ipcMain, Notification, Menu } = require('electron');

const PRELOAD = path.join(__dirname, 'preload.js');
const OVERLAY_FILE = path.join(__dirname, 'overlay.html');
const CONFIG_SCREEN = path.join(__dirname, 'config.html');
const ICON_FILE = path.join(__dirname, 'assets', 'icon.png');

const DEFAULT_URL = 'http://localhost:8000';
// Overlay fecha sozinho após este tempo, caso não seja dispensado.
const OVERLAY_TIMEOUT_MS = 15_000;
// Intervalo de re-tentativa enquanto o backend ainda não responde.
const SERVER_POLL_MS = 2_000;

/** @type {BrowserWindow | null} */
let mainWindow = null;
/** @type {BrowserWindow | null} */
let overlayWindow = null;
/** @type {NodeJS.Timeout | null} */
let overlayTimer = null;
/** @type {NodeJS.Timeout | null} */
let serverPollTimer = null;
/** URL do backend escolhida pelo usuário (null = ainda não configurado). */
let serverUrl = null;
/** Caminho do config.json — definido após o app ficar pronto. */
let configPath = null;

// ── Persistência da configuração ────────────────────────────────────────────────
function readConfig() {
  try {
    const data = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    return data && typeof data === 'object' ? data : {};
  } catch {
    return {};
  }
}

function writeConfig(cfg) {
  try {
    fs.writeFileSync(configPath, JSON.stringify(cfg, null, 2));
  } catch (err) {
    console.error('[config] falha ao salvar:', err.message);
  }
}

/**
 * Normaliza o que o usuário digitou em uma origem http(s) válida.
 *   "192.168.0.50"            → "http://192.168.0.50:8000"
 *   "meu-servidor:8000"       → "http://meu-servidor:8000"
 *   "https://host"            → "https://host"
 * Retorna null se não for possível interpretar.
 */
function normalizeUrl(raw) {
  let s = String(raw || '').trim();
  if (!s) return null;
  if (!/^https?:\/\//i.test(s)) s = 'http://' + s;
  try {
    const u = new URL(s);
    // Sem porta explícita em http → assume a porta padrão do FastAPI.
    if (!u.port && u.protocol === 'http:') u.port = '8000';
    return u.origin; // descarta qualquer path/query
  } catch {
    return null;
  }
}

// ── Disponibilidade do servidor ─────────────────────────────────────────────────
function pingServer(url) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (!settled) {
        settled = true;
        resolve(value);
      }
    };

    // autoSelectFamily (Happy Eyeballs) tenta IPv6 e IPv4 em paralelo e usa o
    // que conectar primeiro. Essencial porque o Electron 28 embute o Node 18,
    // onde esse comportamento NÃO é o padrão: sem isso, "localhost" tenta só
    // ::1 (IPv6) e trava quando o backend escuta apenas em 127.0.0.1 (IPv4).
    let req;
    try {
      req = http.get(
        url,
        { timeout: 2000, autoSelectFamily: true, autoSelectFamilyAttemptTimeout: 300 },
        (res) => {
          res.resume(); // qualquer resposta HTTP = servidor no ar
          done(true);
        }
      );
    } catch {
      done(false);
      return;
    }
    req.on('error', () => done(false));
    req.on('timeout', () => {
      req.destroy();
      done(false);
    });
  });
}

function clearServerPoll() {
  if (serverPollTimer) {
    clearTimeout(serverPollTimer);
    serverPollTimer = null;
  }
}

// ── Tela de espera ────────────────────────────────────────────────────────────
// Exibida enquanto o backend configurado ainda não responde. Inline (data URL)
// para não depender de arquivo extra. Tem um botão para voltar à configuração.
function waitingHtml(targetUrl) {
  return (
    'data:text/html;charset=utf-8,' +
    encodeURIComponent(`<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>NetSentinel</title>
<style>
  html,body{height:100%;margin:0}
  body{display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:20px;background:#080d1a;color:#d0e4ff;
    font-family:'Segoe UI',system-ui,-apple-system,sans-serif;}
  .ring{width:46px;height:46px;border-radius:50%;
    border:3px solid rgba(99,179,255,0.18);border-top-color:#63b3ff;
    animation:spin 0.9s linear infinite;}
  h1{font-size:18px;font-weight:600;margin:0}
  p{font-size:12px;margin:0;color:#607090}
  button{margin-top:10px;background:#1b3058;color:#b8d0f0;
    border:0.5px solid rgba(99,179,255,0.3);border-radius:8px;
    padding:10px 22px;font-size:13px;cursor:pointer}
  button:hover{background:#244070}
  @keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body>
  <div class="ring"></div>
  <h1>Aguardando servidor…</h1>
  <p>Tentando conectar a ${targetUrl}</p>
  <button onclick="window.electronAPI && window.electronAPI.showConfig()">Trocar servidor</button>
</body></html>`)
  );
}

function showConfigScreen() {
  clearServerPoll();
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadFile(CONFIG_SCREEN);
  }
}

// Tenta carregar o backend; se ainda não responde, mostra a tela de espera e
// reagenda a verificação a cada SERVER_POLL_MS. Sem URL → tela de configuração.
async function loadWhenReady() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (!serverUrl) {
    showConfigScreen();
    return;
  }

  const up = await pingServer(serverUrl);
  if (!mainWindow || mainWindow.isDestroyed()) return;

  if (up) {
    clearServerPoll();
    mainWindow.loadURL(serverUrl);
  } else {
    const current = mainWindow.webContents.getURL();
    if (!current.startsWith('data:text/html')) {
      mainWindow.loadURL(waitingHtml(serverUrl));
    }
    clearServerPoll();
    serverPollTimer = setTimeout(loadWhenReady, SERVER_POLL_MS);
  }
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    frame: true,
    titleBarStyle: 'default',
    title: 'NetSentinel',
    icon: ICON_FILE,
    backgroundColor: '#080d1a',
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false, // necessário para carregar o backend remoto sem bloqueios
      // Permite o alarme sonoro tocar automaticamente, sem exigir um clique
      // prévio do usuário. Sem isso o Electron bloqueia audio.play() e o
      // alerta "sobe silenciado" mesmo com o som habilitado.
      autoplayPolicy: 'no-user-gesture-required',
    },
  });

  mainWindow.on('closed', () => {
    clearServerPoll();
    mainWindow = null;
  });

  // Sem servidor configurado → abre direto a tela de configuração.
  if (serverUrl) {
    loadWhenReady();
  } else {
    showConfigScreen();
  }
}

// ── Menu do app ─────────────────────────────────────────────────────────────────
function buildMenu() {
  const template = [
    {
      label: 'Servidor',
      submenu: [
        {
          label: 'Configurar servidor…',
          accelerator: 'CmdOrCtrl+Shift+S',
          click: () => showConfigScreen(),
        },
        { type: 'separator' },
        { role: 'reload', label: 'Recarregar' },
        { role: 'forceReload', label: 'Forçar recarregar' },
        { role: 'toggleDevTools', label: 'Ferramentas de desenvolvedor' },
        { type: 'separator' },
        { role: 'quit', label: 'Sair' },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ── Overlay de alerta crítico ───────────────────────────────────────────────────
function clearOverlayTimer() {
  if (overlayTimer) {
    clearTimeout(overlayTimer);
    overlayTimer = null;
  }
}

function closeOverlay() {
  clearOverlayTimer();
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.close();
  }
}

function showOverlay(data) {
  // Reaproveita a janela de overlay já aberta, só atualizando os dados.
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    clearOverlayTimer();
    overlayWindow.webContents.send('overlay-data', data);
    overlayWindow.show();
    overlayWindow.focus();
    overlayTimer = setTimeout(closeOverlay, OVERLAY_TIMEOUT_MS);
    return;
  }

  overlayWindow = new BrowserWindow({
    fullscreen: true,
    alwaysOnTop: true,
    frame: false,
    // OPACO de propósito: a combinação transparent:true + fullscreen:true não
    // renderiza de forma confiável no Windows (a janela some). O fundo do
    // overlay.html já era 93% opaco, então visualmente é praticamente igual —
    // e como alarme de tela cheia, cobrir tudo é até melhor.
    transparent: false,
    backgroundColor: '#080d1a',
    resizable: false,
    movable: false,
    skipTaskbar: true,
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Mantém o overlay acima de tudo — inclusive de janelas fullscreen alheias —
  // e mesmo que a janela principal esteja minimizada.
  overlayWindow.setAlwaysOnTop(true, 'screen-saver');

  overlayWindow.loadFile(OVERLAY_FILE);

  // O listener registrado pelo preload só existe após o documento carregar;
  // por isso os dados são enviados apenas no did-finish-load.
  overlayWindow.webContents.once('did-finish-load', () => {
    if (overlayWindow && !overlayWindow.isDestroyed()) {
      overlayWindow.webContents.send('overlay-data', data);
    }
  });

  overlayWindow.on('closed', () => {
    clearOverlayTimer();
    overlayWindow = null;
  });

  overlayTimer = setTimeout(closeOverlay, OVERLAY_TIMEOUT_MS);
}

// ── Notificação nativa do SO ────────────────────────────────────────────────────
function showNotification(data) {
  if (!Notification.isSupported()) return;

  const name = data && data.name ? String(data.name) : 'Serviço';
  const bodyParts = [];
  if (data && data.url) bodyParts.push(String(data.url));
  if (data && data.time) bodyParts.push(`Detectado às ${data.time}`);

  const notification = new Notification({
    title: `⚠️ ${name} está offline`,
    body: bodyParts.join('\n') || 'Falha de conexão detectada.',
    icon: ICON_FILE,
    urgency: 'critical', // efetivo no Linux; ignorado nas demais plataformas
  });

  notification.on('click', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  notification.show();
}

// ── IPC ───────────────────────────────────────────────────────────────────────
ipcMain.on('alert', (_event, data) => {
  const payload = data || {};
  // critical:false → apenas notificação. critical:true → notificação + overlay.
  showNotification(payload);
  if (payload.critical === true) {
    showOverlay(payload);
  }
});

ipcMain.on('close-overlay', () => {
  closeOverlay();
});

// Endereço atual (ou um palpite) para pré-preencher a tela de configuração.
ipcMain.handle('get-server-url', () => serverUrl || readConfig().url || DEFAULT_URL);

// O usuário enviou um novo endereço pela tela de configuração.
ipcMain.on('set-server-url', (_event, raw) => {
  const url = normalizeUrl(raw);
  if (!url) {
    showConfigScreen(); // entrada inválida → reapresenta o formulário
    return;
  }
  serverUrl = url;
  writeConfig({ url });
  loadWhenReady();
});

ipcMain.on('show-config', () => showConfigScreen());

// ── Instância única ───────────────────────────────────────────────────────────
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    configPath = path.join(app.getPath('userData'), 'config.json');
    serverUrl = normalizeUrl(readConfig().url) || null;

    buildMenu();
    createMainWindow();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    });
  });

  // Ao fechar a janela principal, encerra o app por completo.
  app.on('window-all-closed', () => {
    app.quit();
  });
}
