'use strict';

/*
 * Preload — ponte segura entre o frontend web (renderer) e o processo
 * principal do Electron. Roda com contextIsolation:true, então NADA do Node
 * vaza para a página; só o objeto `electronAPI` abaixo fica exposto.
 *
 * O frontend detecta o ambiente Electron checando `window.electronAPI`.
 * No navegador comum esse objeto é `undefined` e a app funciona normalmente.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // Marca estática para o frontend identificar que está rodando no Electron.
  isElectron: true,

  /**
   * Emite um alerta para o processo principal.
   * @param {{
   *   name: string,      // nome do dispositivo/serviço
   *   url: string,       // url ou host
   *   time: string,      // horário formatado
   *   critical: boolean, // true = overlay + notificação; false = só notificação
   *   type: string       // "web" | "database" | "infrastructure"
   * }} data
   */
  sendAlert(data) {
    ipcRenderer.send('alert', data);
  },

  /**
   * Registra um callback para receber os dados do overlay (usado por overlay.html).
   * @param {(data: any) => void} callback
   */
  onOverlayData(callback) {
    ipcRenderer.on('overlay-data', (_event, data) => callback(data));
  },

  /** Pede ao processo principal que feche o overlay. */
  closeOverlay() {
    ipcRenderer.send('close-overlay');
  },

  // ── Configuração do endereço do backend (tela config.html) ──────────────────

  /** Retorna a URL atual do backend (ou um palpite) para pré-preencher o campo. */
  getServerUrl() {
    return ipcRenderer.invoke('get-server-url');
  },

  /** Define o endereço do backend; o processo principal salva e tenta carregar. */
  setServerUrl(url) {
    ipcRenderer.send('set-server-url', url);
  },

  /** Reabre a tela de configuração do servidor. */
  showConfig() {
    ipcRenderer.send('show-config');
  },
});
