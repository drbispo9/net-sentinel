'use strict';

/*
 * start.js — lançador de DESENVOLVIMENTO.
 *
 * Fluxo:
 *   1. Sobe o FastAPI (uvicorn) como processo filho.
 *   2. Faz polling em http://localhost:8000 até o servidor responder.
 *   3. Só então abre o Electron (electron .).
 *   4. Ao fechar o Electron, mata o processo do FastAPI.
 *
 * Observação: este script NÃO contém lógica de monitoramento e não sobe
 * servidor Node — apenas orquestra o ciclo de vida do backend Python e da
 * casca Electron em ambiente de desenvolvimento.
 */

const path = require('path');
const http = require('http');
const fs = require('fs');
const { spawn } = require('child_process');

const APP_URL = 'http://localhost:8000';
const HOST = '127.0.0.1';
const PORT = '8000';
const POLL_INTERVAL_MS = 1_000;
const POLL_TIMEOUT_MS = 60_000;

// Raiz do projeto = pasta-pai de electron/.
const PROJECT_ROOT = path.resolve(__dirname, '..');
const IS_WINDOWS = process.platform === 'win32';

/** Localiza o executável do uvicorn dentro do venv, conforme o SO. */
function resolveUvicorn() {
  const candidates = IS_WINDOWS
    ? [
        path.join(PROJECT_ROOT, 'venv', 'Scripts', 'uvicorn.exe'),
        path.join(PROJECT_ROOT, '.venv', 'Scripts', 'uvicorn.exe'),
      ]
    : [
        path.join(PROJECT_ROOT, 'venv', 'bin', 'uvicorn'),
        path.join(PROJECT_ROOT, '.venv', 'bin', 'uvicorn'),
      ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  // Fallback: confia no uvicorn disponível no PATH.
  return IS_WINDOWS ? 'uvicorn.exe' : 'uvicorn';
}

/** Resolve quando o servidor responder (ou rejeita após o timeout). */
function waitForServer() {
  const deadline = Date.now() + POLL_TIMEOUT_MS;

  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(APP_URL, { timeout: 1500 }, (res) => {
        res.resume();
        resolve();
      });
      req.on('error', retry);
      req.on('timeout', () => {
        req.destroy();
        retry();
      });
    };

    const retry = () => {
      if (Date.now() >= deadline) {
        reject(new Error('Timeout aguardando o FastAPI em ' + APP_URL));
        return;
      }
      setTimeout(attempt, POLL_INTERVAL_MS);
    };

    attempt();
  });
}

let backend = null;
let electronProc = null;
let shuttingDown = false;

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;

  if (backend && !backend.killed) {
    // No Windows, mata a árvore de processos do uvicorn; senão SIGTERM.
    if (IS_WINDOWS) {
      spawn('taskkill', ['/pid', String(backend.pid), '/t', '/f']);
    } else {
      backend.kill('SIGTERM');
    }
  }
  process.exit(typeof code === 'number' ? code : 0);
}

async function main() {
  const uvicorn = resolveUvicorn();
  console.log('[start] Iniciando FastAPI:', uvicorn);

  backend = spawn(
    uvicorn,
    ['backend.main:app', '--host', HOST, '--port', PORT],
    { cwd: PROJECT_ROOT, stdio: 'inherit' }
  );

  backend.on('error', (err) => {
    console.error('[start] Falha ao iniciar o uvicorn:', err.message);
    console.error('[start] Verifique se as dependências Python estão instaladas (pip install -r requirements.txt).');
    shutdown(1);
  });

  backend.on('exit', (code) => {
    if (!shuttingDown) {
      console.error('[start] FastAPI encerrou inesperadamente (code ' + code + ').');
      shutdown(code || 1);
    }
  });

  console.log('[start] Aguardando servidor em', APP_URL, '...');
  try {
    await waitForServer();
  } catch (err) {
    console.error('[start]', err.message);
    shutdown(1);
    return;
  }
  console.log('[start] Servidor no ar. Abrindo Electron…');

  // `require('electron')` em contexto Node devolve o caminho do executável.
  const electronBin = require('electron');
  electronProc = spawn(electronBin, ['.'], {
    cwd: __dirname,
    stdio: 'inherit',
  });

  electronProc.on('exit', (code) => {
    console.log('[start] Electron encerrado. Finalizando FastAPI.');
    shutdown(code || 0);
  });
}

// Encerra tudo de forma limpa em sinais do SO.
process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));

main();
