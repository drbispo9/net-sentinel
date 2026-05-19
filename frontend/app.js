/**
 * NetSentinel Visual — Frontend Application
 * REST polling + WebSocket real-time updates
 */

/* ─── Config ─── */
const API_BASE = 'http://127.0.0.1:8000';
const WS_URL   = 'ws://127.0.0.1:8000/ws';
const POLL_INTERVAL_MS = 15_000;   // Fallback REST polling
const TOAST_DURATION_MS = 5000;

/* ─── State ─── */
let devices = [];
let eventLogs = [];
let wsSocket = null;
let wsReconnectTimer = null;
let pollTimer = null;
let alertActive = false;
let editingDeviceId = null;
let currentView = 'WEB';
let titleFlashInterval = null;
let swRegistration = null;
const ORIGINAL_TITLE = 'NetSentinel Visual — Dashboard';

/* ─── DOM Refs ─── */
const $ = (id) => document.getElementById(id);

const grid         = $('devices-grid');
const statTotal    = $('stat-total');
const statUp       = $('stat-up');
const statDown     = $('stat-down');
const globalPulse  = $('global-pulse');
const btnSilenciar = $('btn-silenciar');
const btnAdd       = $('btn-add-device');
const toastCon     = $('toast-container');
const wsStatusDot  = $('ws-dot');
const wsStatusText = $('ws-status-text');
const eventsList   = $('events-list');
const audioAlert   = $('audio-alert');
const sectionTitle = $('devices-section-title');
const navBtns      = document.querySelectorAll('.nav-btn');

// Modal
const modalOverlay = $('modal-overlay');
const modalTitle   = $('modal-title');
const formDevice   = $('form-device');
const inputName    = $('input-name');
const inputType    = $('input-type');
const inputAddr    = $('input-address');
const inputDeviceId = $('input-device-id');
const btnModalCancel = $('btn-modal-cancel');
const btnModalSave   = $('btn-modal-save');

// Details Modal
const detailsOverlay = $('modal-details-overlay');
const btnDetailsClose = $('btn-details-close');
const detailsName = $('details-name');
const detailsStatusBadge = $('details-status-badge');
const detailsAddress = $('details-address');
const detailsUptime = $('details-uptime');
const detailsLastChange = $('details-last-change');
const detailsEventsList = $('details-events-list');

/* ────────────────────────────────────────────────
   WebSocket
──────────────────────────────────────────────── */
function connectWS() {
  setWsStatus('connecting');

  wsSocket = new WebSocket(WS_URL);

  wsSocket.addEventListener('open', () => {
    setWsStatus('connected');
    clearTimeout(wsReconnectTimer);
    console.log('[WS] Connected');
  });

  wsSocket.addEventListener('message', (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      handleWsMessage(msg);
    } catch (e) {
      console.warn('[WS] Bad message', evt.data);
    }
  });

  wsSocket.addEventListener('close', () => {
    setWsStatus('disconnected');
    console.warn('[WS] Disconnected — reconnecting in 5s');
    wsReconnectTimer = setTimeout(connectWS, 5000);
  });

  wsSocket.addEventListener('error', () => {
    wsSocket.close();
  });
}

function handleWsMessage(msg) {
  if (msg.type === 'status_change') {
    // Update device in local state immediately
    const device = devices.find(d => d.id === msg.device_id);
    if (device) {
      const oldStatus = device.status;
      device.status = msg.status;

      // Push synthetic event
      pushEvent({
        device_name: msg.device_name || device.name,
        old_status: oldStatus,
        new_status: msg.status,
        timestamp: new Date().toISOString(),
      });

      renderDevices();
      updateStats();
    }

    if (msg.status === 'DOWN' || msg.status === 'CRITICAL_LOCK') {
      const dev = devices.find(d => d.id === msg.device_id);
      if (!dev || !dev.is_muted) {
        triggerAlert(msg.device_name || 'Dispositivo', msg.status);
      }
    } else if (msg.status === 'UP') {
      showToast(`✅ ${msg.device_name || 'Dispositivo'} voltou online!`, 'success');
      checkAndStopAlert();
    }
  }
}

/* ────────────────────────────────────────────────
   REST API
──────────────────────────────────────────────── */
async function fetchDevices() {
  try {
    const res = await fetch(`${API_BASE}/api/devices`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    mergeDevices(data);
    renderDevices();
    updateStats();
  } catch (err) {
    console.error('[API] fetchDevices failed:', err);
    if (devices.length === 0) renderEmptyState('Não foi possível conectar ao servidor.', true);
  }
}

async function fetchEvents() {
  try {
    const res = await fetch(`${API_BASE}/api/events`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    eventLogs = data;
    renderEvents();
  } catch (err) {
    console.error('[API] fetchEvents failed:', err);
  }
}

async function addDevice(payload) {
  const res = await fetch(`${API_BASE}/api/devices`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function deleteDevice(id) {
  const res = await fetch(`${API_BASE}/api/devices/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

async function updateDeviceAPI(id, payload) {
  const res = await fetch(`${API_BASE}/api/devices/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function toggleMuteDevice(id) {
  const device = devices.find(d => d.id === id);
  if (!device) return;
  const newMuted = !device.is_muted;
  try {
    const updated = await updateDeviceAPI(id, { is_muted: newMuted });
    Object.assign(device, updated);
    renderDevices();
    updateStats();
    showToast(newMuted ? `🔇 "${device.name}" silenciado.` : `🔊 "${device.name}" alerta reativado.`, 'info');
    if (newMuted) {
      checkAndStopAlert();
    } else if (device.status === 'DOWN' || device.status === 'CRITICAL_LOCK') {
      // Re-trigger alert when unmuting a device that is still DOWN or in CRITICAL_LOCK
      triggerAlert(device.name, device.status);
    }
  } catch (err) {
    showToast(`Erro: ${err.message}`, 'error');
  }
}

/* ────────────────────────────────────────────────
   State Merging
──────────────────────────────────────────────── */
function mergeDevices(incoming) {
  // Keep WS-updated statuses if server hasn't caught up yet
  incoming.forEach(d => {
    const existing = devices.find(e => e.id === d.id);
    if (!existing) {
      devices.push(d);
    } else {
      Object.assign(existing, d);
    }
  });
  // Remove deleted
  const ids = new Set(incoming.map(d => d.id));
  devices = devices.filter(d => ids.has(d.id));
}

/* ────────────────────────────────────────────────
   Render — Devices Grid
──────────────────────────────────────────────── */
function renderDevices() {
  const viewDevices = devices.filter(d => d.device_type === currentView);

  if (viewDevices.length === 0) {
    renderEmptyState();
    return;
  }

  // Sort: DOWN first, then WARNING, then UP
  const sorted = [...viewDevices].sort((a, b) => {
    const order = { DOWN: 0, WARNING: 1, UP: 2 };
    return (order[a.status] ?? 3) - (order[b.status] ?? 3);
  });

  grid.innerHTML = sorted.map(d => deviceCardHTML(d)).join('');

  // Bind delete buttons
  grid.querySelectorAll('.btn-delete').forEach(btn => {
    btn.addEventListener('click', () => handleDelete(Number(btn.dataset.id), btn.dataset.name));
  });

  // Bind edit buttons
  grid.querySelectorAll('.btn-edit').forEach(btn => {
    btn.addEventListener('click', () => openEditModal(Number(btn.dataset.id)));
  });

  // Bind mute/unmute buttons
  grid.querySelectorAll('.btn-mute').forEach(btn => {
    btn.addEventListener('click', () => toggleMuteDevice(Number(btn.dataset.id)));
  });

  // Bind card clicks for details
  grid.querySelectorAll('.device-card').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.btn-delete') || e.target.closest('.btn-edit') || e.target.closest('.btn-mute')) return;
      const id = Number(card.dataset.id);
      const device = devices.find(d => d.id === id);
      if (device) openDeviceDetails(id, device.name, device.address, device.status);
    });
  });
}

function deviceCardHTML(d) {
  const statusClass = d.status.toLowerCase();
  const statusLabel = {
    UP: '● Online',
    DOWN: '● Offline',
    WARNING: '● Alerta',
  }[d.status] || d.status;

  const badgeClass = {
    UP: 'badge-up',
    DOWN: 'badge-down',
    WARNING: 'badge-warning',
  }[d.status] || '';

  const typeLabel = d.device_type === 'WEB' ? '🌐 Web' : '🖥️ Hardware';
  const failures = d.failure_count > 0
    ? `<span class="card-failures">Falhas: <span>${d.failure_count}</span></span>`
    : `<span class="card-failures" style="color:var(--color-up)">Sem falhas</span>`;

  const muteBtnIcon = d.is_muted
    ? '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><line x1="23" y1="9" x2="17" y2="15"></line><line x1="17" y1="9" x2="23" y2="15"></line>'
    : '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path>';

  const muteBtn = d.status === 'DOWN'
    ? `<button class="btn-action ${d.is_muted ? 'btn-mute muted' : 'btn-mute'}" data-id="${d.id}" title="${d.is_muted ? 'Desilenciar' : 'Silenciar'}">
         <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${muteBtnIcon}</svg>
       </button>`
    : '';

  const mutedIndicator = d.is_muted ? '<span class="muted-indicator" title="Alerta silenciado">🔇</span>' : '';

  return `
    <div class="device-card card-${statusClass} ${d.is_muted ? 'card-muted' : ''}" data-id="${d.id}">
      <div class="card-header">
        <div class="card-info">
          <div class="card-title">${escHtml(d.name)} ${mutedIndicator}</div>
          <div class="card-address">${escHtml(d.address)}</div>
        </div>
        <span class="card-status-badge ${badgeClass}">${statusLabel}</span>
      </div>
      <div class="card-meta">
        <span class="card-type-badge">${typeLabel}</span>
        ${failures}
      </div>
      <div class="card-actions">
        <button class="btn-action btn-edit" data-id="${d.id}" title="Editar">
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
        </button>
        ${muteBtn}
        <button class="btn-action btn-delete" data-id="${d.id}" data-name="${escHtml(d.name)}" title="Remover">
          <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14H6L5 6"></path><path d="M10 11v6M14 11v6"></path><path d="M9 6V4h6v2"></path></svg>
        </button>
      </div>
    </div>
  `;
}

function renderEmptyState(msg = 'Nenhum dispositivo cadastrado.', isError = false) {
  grid.innerHTML = `
    <div class="empty-state">
      <span class="empty-icon">${isError ? '⚠️' : '📡'}</span>
      <p class="empty-text" style="${isError ? 'color:var(--color-down)' : ''}">${escHtml(msg)}</p>
      <p class="empty-hint">Clique em "Adicionar Dispositivo" para começar.</p>
    </div>
  `;
}

/* ────────────────────────────────────────────────
   Render — Stats
──────────────────────────────────────────────── */
function updateStats() {
  const viewDevices = devices.filter(d => d.device_type === currentView);
  
  const total   = viewDevices.length;
  const upCount = viewDevices.filter(d => d.status === 'UP').length;
  const downCount = viewDevices.filter(d => d.status === 'DOWN').length;

  animateNumber(statTotal, total);
  animateNumber(statUp, upCount);
  animateNumber(statDown, downCount);

  // Global pulse color
  globalPulse.className = 'pulse-indicator ' + (
    downCount > 0 ? 'status-down' :
    viewDevices.some(d => d.status === 'WARNING') ? 'status-warning' : 'status-up'
  );
}

function animateNumber(el, target) {
  const current = parseInt(el.textContent) || 0;
  if (current === target) return;
  const step = target > current ? 1 : -1;
  let val = current;
  const t = setInterval(() => {
    val += step;
    el.textContent = val;
    if (val === target) clearInterval(t);
  }, 40);
}

/* ────────────────────────────────────────────────
   Render — Event Logs
──────────────────────────────────────────────── */
function pushEvent(ev) {
  eventLogs.unshift(ev); // newest first
  if (eventLogs.length > 50) eventLogs.pop();
  renderEvents();
}

function renderEvents() {
  if (eventLogs.length === 0) {
    eventsList.innerHTML = '<div class="events-empty">Nenhum evento registrado ainda.</div>';
    return;
  }

  eventsList.innerHTML = eventLogs.map(ev => {
    const time = formatTime(ev.timestamp);
    const oldTag = statusTag(ev.old_status);
    const newTag = statusTag(ev.new_status);

    return `
      <div class="event-row">
        <span class="event-time">${time}</span>
        <span class="event-device">${escHtml(ev.device_name)}</span>
        <span class="event-transition">
          <span class="${oldTag.cls}">${oldTag.label}</span>
          <span class="arrow-icon">→</span>
          <span class="${newTag.cls}">${newTag.label}</span>
        </span>
      </div>
    `;
  }).join('');
}

function statusTag(s) {
  const map = {
    UP:      { cls: 'status-tag-up',      label: 'Online'  },
    DOWN:    { cls: 'status-tag-down',     label: 'Offline' },
    WARNING: { cls: 'status-tag-warning',  label: 'Alerta'  },
  };
  return map[s] || { cls: '', label: s };
}

/* ────────────────────────────────────────────────
   Alert System
──────────────────────────────────────────────── */
function triggerAlert(deviceName, status = 'DOWN') {
  alertActive = true;
  btnSilenciar.classList.remove('hidden');
  
  const alertMsg = status === 'CRITICAL_LOCK' 
    ? `🚨 ${deviceName} está em LOCK CRÍTICO!` 
    : `🚨 ${deviceName} está OFFLINE!`;
    
  showToast(alertMsg, 'error');

  if (audioAlert) {
    audioAlert.play().catch(() => {
      console.warn('[Audio] Autoplay blocked by browser');
    });
  }

  // Attempt to bring the window to the foreground
  window.focus();

  // Flash the document title to attract attention on the tab bar
  startTitleFlash(alertMsg);

  // Show OS notification via Service Worker (reliable click-to-focus)
  if (swRegistration && Notification.permission === 'granted') {
    swRegistration.showNotification(alertMsg, {
      body: 'Clique para abrir o painel do NetSentinel',
      requireInteraction: true
    });
  } else if ('Notification' in window && Notification.permission === 'granted') {
    // Fallback: direct Notification (less reliable for focus)
    const notif = new Notification(alertMsg, {
      body: 'Clique para abrir o painel do NetSentinel',
      requireInteraction: true
    });
    notif.onclick = function() {
      window.focus();
      notif.close();
    };
  }
}

function silenciarAlerta() {
  alertActive = false;
  btnSilenciar.classList.add('hidden');
  stopTitleFlash();
  if (audioAlert) {
    audioAlert.pause();
    audioAlert.currentTime = 0;
  }
}

/* ── Title Flash (tab bar visual cue) ── */
function startTitleFlash(msg) {
  if (titleFlashInterval) return; // already flashing
  let showAlert = true;
  titleFlashInterval = setInterval(() => {
    document.title = showAlert ? `⚠️ ALERTA — ${msg}` : ORIGINAL_TITLE;
    showAlert = !showAlert;
  }, 1000);
  // Auto-stop when user focuses the window
  window.addEventListener('focus', stopTitleFlash, { once: true });
}

function stopTitleFlash() {
  if (titleFlashInterval) {
    clearInterval(titleFlashInterval);
    titleFlashInterval = null;
    document.title = ORIGINAL_TITLE;
  }
}

/** Check if any DOWN or CRITICAL_LOCK device is still un-muted; if not, stop audio */
function checkAndStopAlert() {
  const hasUnmutedAlert = devices.some(d => (d.status === 'DOWN' || d.status === 'CRITICAL_LOCK') && !d.is_muted);
  if (!hasUnmutedAlert && alertActive) {
    silenciarAlerta();
  }
}

/* ────────────────────────────────────────────────
   CRUD — Add / Delete
──────────────────────────────────────────────── */
function openModal() {
  editingDeviceId = null;
  modalTitle.textContent = 'Adicionar Dispositivo';
  formDevice.reset();
  inputDeviceId.value = '';
  inputType.value = currentView;
  btnModalSave.textContent = 'Adicionar';
  modalOverlay.classList.add('open');
  inputName.focus();
}

function openEditModal(id) {
  const device = devices.find(d => d.id === id);
  if (!device) return;
  editingDeviceId = id;
  modalTitle.textContent = 'Editar Dispositivo';
  inputDeviceId.value = id;
  inputName.value = device.name;
  inputType.value = device.device_type;
  inputAddr.value = device.address;
  btnModalSave.textContent = 'Salvar';
  modalOverlay.classList.add('open');
  inputName.focus();
}

function closeModal() {
  modalOverlay.classList.remove('open');
  editingDeviceId = null;
}

function closeDetailsModal() {
  detailsOverlay.classList.remove('open');
}

async function openDeviceDetails(id, name, address, status) {
  detailsName.textContent = name;
  detailsAddress.textContent = address;
  
  const bCls = { UP: 'badge-up', DOWN: 'badge-down', WARNING: 'badge-warning' }[status] || '';
  const lbl = { UP: '● Online', DOWN: '● Offline', WARNING: '● Alerta' }[status] || status;
  detailsStatusBadge.className = `card-status-badge ${bCls}`;
  detailsStatusBadge.textContent = lbl;

  detailsUptime.textContent = '...';
  detailsLastChange.textContent = '--';
  detailsEventsList.innerHTML = '<div class="events-empty"><div class="spinner" style="width:20px;height:20px;margin:0 auto;"></div></div>';
  
  detailsOverlay.classList.add('open');

  try {
    const res = await fetch(`${API_BASE}/api/devices/${id}/stats`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    
    detailsUptime.textContent = `${data.uptime_percentage.toFixed(1)}%`;
    detailsLastChange.textContent = data.last_status_change ? formatTime(data.last_status_change) : 'N/A';
    
    if (data.recent_events && data.recent_events.length > 0) {
      detailsEventsList.innerHTML = data.recent_events.map(ev => {
        const time = formatTime(ev.timestamp);
        const oldTag = statusTag(ev.old_status);
        const newTag = statusTag(ev.new_status);
        return `
          <div class="event-row" style="grid-template-columns: 80px 1fr; padding: 0.5rem 1rem;">
            <span class="event-time">${time}</span>
            <span class="event-transition">
              <span class="${oldTag.cls}">${oldTag.label}</span>
              <span class="arrow-icon">→</span>
              <span class="${newTag.cls}">${newTag.label}</span>
            </span>
          </div>
        `;
      }).join('');
    } else {
      detailsEventsList.innerHTML = '<div class="events-empty">Nenhum evento registrado.</div>';
    }
  } catch(err) {
    console.error('[API] fetchStats failed:', err);
    detailsUptime.textContent = '--%';
    detailsEventsList.innerHTML = `<div class="events-empty">Erro ao carregar dados.</div>`;
  }
}

async function handleFormSubmit(e) {
  e.preventDefault();

  const name    = inputName.value.trim();
  const type    = inputType.value;
  const address = inputAddr.value.trim();

  if (!name || !address) {
    showToast('Preencha todos os campos.', 'warning');
    return;
  }

  btnModalSave.disabled = true;
  btnModalSave.textContent = 'Salvando...';

  try {
    if (editingDeviceId) {
      // Edit mode
      const updated = await updateDeviceAPI(editingDeviceId, { name, device_type: type, address });
      const idx = devices.findIndex(d => d.id === editingDeviceId);
      if (idx !== -1) Object.assign(devices[idx], updated);
      renderDevices();
      updateStats();
      closeModal();
      showToast(`✏️ "${name}" atualizado com sucesso!`, 'success');
    } else {
      // Add mode
      const newDevice = await addDevice({ name, device_type: type, address });
      devices.push(newDevice);
      renderDevices();
      updateStats();
      closeModal();
      showToast(`✅ "${name}" adicionado com sucesso!`, 'success');
    }
  } catch (err) {
    showToast(`Erro: ${err.message}`, 'error');
  } finally {
    btnModalSave.disabled = false;
    btnModalSave.textContent = editingDeviceId ? 'Salvar' : 'Adicionar';
  }
}

async function handleDelete(id, name) {
  if (!confirm(`Remover "${name}" do monitoramento?`)) return;

  const card = grid.querySelector(`[data-id="${id}"]`);
  if (card) card.classList.add('removing');

  try {
    await deleteDevice(id);
    setTimeout(() => {
      devices = devices.filter(d => d.id !== id);
      renderDevices();
      updateStats();
      showToast(`"${name}" removido.`, 'info');
    }, 300);
  } catch (err) {
    if (card) card.classList.remove('removing');
    showToast(`Erro ao remover: ${err.message}`, 'error');
  }
}

/* ────────────────────────────────────────────────
   Toast Notifications
──────────────────────────────────────────────── */
function showToast(message, type = 'info') {
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<div class="toast-dot"></div><span>${escHtml(message)}</span>`;
  toastCon.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('removing');
    setTimeout(() => toast.remove(), 350);
  }, TOAST_DURATION_MS);
}

/* ────────────────────────────────────────────────
   WS Status Display
──────────────────────────────────────────────── */
function setWsStatus(state) {
  const labels = {
    connected:    'Tempo real',
    connecting:   'Conectando...',
    disconnected: 'Desconectado',
  };
  wsStatusDot.className  = `ws-dot ${state}`;
  wsStatusText.textContent = labels[state] || state;
}

/* ────────────────────────────────────────────────
   Utilities
──────────────────────────────────────────────── */
function escHtml(str) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(str));
  return d.innerHTML;
}

function formatTime(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '--:--:--';
  }
}

/* ────────────────────────────────────────────────
   Init
──────────────────────────────────────────────── */
function init() {
  // Register Service Worker for reliable notification click handling
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').then(function(reg) {
      swRegistration = reg;
      console.log('[SW] Service Worker registered');
    }).catch(function(err) {
      console.warn('[SW] Registration failed:', err);
    });
  }

  // Request OS Notification permission
  if ('Notification' in window && Notification.permission !== 'granted' && Notification.permission !== 'denied') {
    Notification.requestPermission();
  }

  // Initial load
  fetchDevices();
  fetchEvents();

  // Periodic REST polling (fallback / sync)
  pollTimer = setInterval(fetchDevices, POLL_INTERVAL_MS);

  // WebSocket real-time
  connectWS();

  // Render empty events
  renderEvents();

  // Button bindings
  btnAdd?.addEventListener('click', openModal);
  btnModalCancel?.addEventListener('click', closeModal);
  btnDetailsClose?.addEventListener('click', closeDetailsModal);
  formDevice?.addEventListener('submit', handleFormSubmit);
  btnSilenciar?.addEventListener('click', silenciarAlerta);

  // Nav Tab bindings
  navBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      if (currentView === btn.dataset.view) return;
      
      navBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      
      currentView = btn.dataset.view;
      sectionTitle.textContent = currentView === 'WEB' ? 'Dispositivos web' : 'Infraestrutura de hardware';
      
      renderDevices();
      updateStats();
    });
  });

  // Close modal on overlay click
  modalOverlay?.addEventListener('click', (e) => {
    if (e.target === modalOverlay) closeModal();
  });
  detailsOverlay?.addEventListener('click', (e) => {
    if (e.target === detailsOverlay) closeDetailsModal();
  });

  // Close modal on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (modalOverlay.classList.contains('open')) closeModal();
      if (detailsOverlay.classList.contains('open')) closeDetailsModal();
    }
  });
}

document.addEventListener('DOMContentLoaded', init);
