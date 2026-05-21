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
let wsSocket = null;
let wsReconnectTimer = null;
let pollTimer = null;
let alertActive = false;
let editingDeviceId = null;
let currentDetailsDeviceId = null;
let currentDetailsDevice = null;
let currentView = 'WEB';
let titleFlashInterval = null;
let swRegistration = null;
const ORIGINAL_TITLE = 'NetSentinel — Dashboard';

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
const detailsOverlay      = $('modal-details-overlay');
const btnDetailsClose     = $('btn-details-close');
const detailsName         = $('details-name');
const detailsStatusBadge  = $('details-status-badge');
const detailsAddress      = $('details-address');
const detailsUptime       = $('details-uptime');
const detailsLastChange   = $('details-last-change');
const detailsEventsList   = $('details-events-list');
const detailsL7Section    = $('details-l7-section');
const detailsL7Chart      = $('details-l7-chart');
const detailsL7Timestamp  = $('details-l7-timestamp');
const detailsSparkline    = $('details-sparkline');
const detailsTotalMsBox   = $('details-total-ms-box');
const detailsTotalMs      = $('details-total-ms');

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
  // Normalize incoming status labels from WebSocket
  if (msg.status === 'online') msg.status = 'UP';
  if (msg.status === 'offline') msg.status = 'DOWN';

  if (msg.type === 'status_change' || msg.type === 'status_update') {
    const device = devices.find(d => d.id === msg.device_id);
    if (device) {
      const oldStatus = device.status;
      const statusChanged = msg.status && device.status !== msg.status;

      if (msg.status) device.status = msg.status;
      if (msg.response_time_ms !== undefined) device.response_time_ms = msg.response_time_ms;
      if (msg.dns_ms !== undefined) device.dns_ms = msg.dns_ms;

      renderDevices();
      updateStats();

      if (detailsOverlay.classList.contains('open') && currentDetailsDevice && currentDetailsDevice.id === msg.device_id) {
        refreshDeviceDetails(true);
      }

      // Only handle alerts/toasts for actual status changes to avoid repetitive audio/toasts
      if (msg.type === 'status_change' && statusChanged) {
        if (device.status === 'DOWN' || device.status === 'CRITICAL_LOCK' || device.status === 'CRITICAL_OVERLOAD') {
          if (!device.is_muted) {
            triggerAlert(msg.device_name || device.name, device.status);
          }
        } else if (device.status === 'UP') {
          showToast(`✅ ${msg.device_name || device.name} voltou online!`, 'success');
          checkAndStopAlert();
        }
      }
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

    if (detailsOverlay.classList.contains('open') && currentDetailsDevice) {
      refreshDeviceDetails(true);
    }
  } catch (err) {
    console.error('[API] fetchDevices failed:', err);
    if (devices.length === 0) renderEmptyState('Não foi possível conectar ao servidor.', true);
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
      if (device) openDeviceDetails(id, device.name, device.address, device.status, device.device_type);
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

  const typeLabel = d.device_type === 'WEB' ? 'Web' : 'Hardware';
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

  const isOnline = d.status === 'UP';
  const showResponseTime = isOnline && d.response_time_ms !== null && d.response_time_ms !== undefined;
  let responseTimeHtml = '';
  if (showResponseTime) {
    const color = getLatencyColor(d.response_time_ms);
    const bg = hexToRgba(color, 0.08);
    const border = hexToRgba(color, 0.2);
    responseTimeHtml = `<span class="response-time" style="color: ${color}; background: ${bg}; border: 0.5px solid ${border};">${d.response_time_ms}ms</span>`;
  }

  return `
    <div class="device-card card-${statusClass} ${d.is_muted ? 'card-muted' : ''}" data-id="${d.id}">
      <div class="card-header">
        <div class="card-info">
          <div class="card-title">${escHtml(d.name)} ${mutedIndicator}</div>
          <div class="card-address">${escHtml(d.address)}</div>
        </div>
        <div class="status-container">
          <span class="card-status-badge ${badgeClass}">${statusLabel}</span>
          ${responseTimeHtml}
        </div>
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
  currentDetailsDeviceId = null;
  currentDetailsDevice = null;
}

/* ────────────────────────────────────────────────
   Device Details — Stats + L7 Performance
──────────────────────────────────────────────── */
async function refreshDeviceDetails(silent = false) {
  if (!currentDetailsDevice) return;
  const { id, name, address, deviceType } = currentDetailsDevice;

  // If not silent, show loading states
  if (!silent) {
    detailsUptime.textContent = '...';
    detailsLastChange.textContent = '--';
    detailsEventsList.innerHTML = '<div class="events-empty"><div class="spinner" style="width:20px;height:20px;margin:0 auto;"></div></div>';
    if (deviceType === 'WEB') {
      detailsL7Chart.innerHTML = '<div class="l7-loading"><div class="spinner" style="width:20px;height:20px;"></div></div>';
      detailsSparkline.innerHTML = '<div class="l7-loading"><div class="spinner" style="width:16px;height:16px;"></div></div>';
    }
  }

  // Spin the reload button icon if present and not a silent refresh
  const reloadBtnSvg = $('btn-details-reload')?.querySelector('svg');
  if (reloadBtnSvg && !silent) {
    reloadBtnSvg.classList.add('spinning');
  }

  // Update badge and total response time from global state
  const updatedDev = devices.find(d => d.id === id);
  if (updatedDev) {
    const bCls = { UP: 'badge-up', DOWN: 'badge-down', WARNING: 'badge-warning' }[updatedDev.status] || '';
    const lbl  = { UP: '● Online', DOWN: '● Offline', WARNING: '● Alerta' }[updatedDev.status] || updatedDev.status;
    detailsStatusBadge.className = `card-status-badge ${bCls}`;
    detailsStatusBadge.textContent = lbl;

    if (updatedDev.device_type === 'WEB') {
      if (updatedDev.status === 'UP' && updatedDev.response_time_ms != null) {
        detailsTotalMs.textContent = `${Math.round(updatedDev.response_time_ms)}ms`;
        detailsTotalMs.style.color = getLatencyColor(updatedDev.response_time_ms);
      } else {
        detailsTotalMs.textContent = 'N/A';
        detailsTotalMs.style.color = 'var(--color-text-muted)';
      }
    }
  }

  const isWeb = deviceType === 'WEB';
  const statsPromise = fetch(`${API_BASE}/api/devices/${id}/stats`).then(r => r.ok ? r.json() : Promise.reject(r.status));
  const perfPromise  = isWeb
    ? fetch(`${API_BASE}/api/devices/${id}/performance?limit=20`).then(r => r.ok ? r.json() : Promise.reject(r.status))
    : Promise.resolve([]);

  try {
    const [statsData, perfData] = await Promise.allSettled([statsPromise, perfPromise]);

    // ── Stats ──────────────────────────────────────────────────────────────
    if (statsData.status === 'fulfilled') {
      const data = statsData.value;
      detailsUptime.textContent = `${data.uptime_percentage.toFixed(1)}%`;
      detailsLastChange.textContent = data.last_status_change ? formatTime(data.last_status_change) : 'N/A';

      if (data.recent_events && data.recent_events.length > 0) {
        detailsEventsList.innerHTML = data.recent_events.map(ev => {
          const time   = formatTime(ev.timestamp);
          const oldTag = statusTag(ev.old_status);
          const newTag = statusTag(ev.new_status);
          return `
            <div class="event-row" style="grid-template-columns:80px 1fr; padding:0.5rem 1rem;">
              <span class="event-time">${time}</span>
              <span class="event-transition">
                <span class="${oldTag.cls}">${oldTag.label}</span>
                <span class="arrow-icon">→</span>
                <span class="${newTag.cls}">${newTag.label}</span>
              </span>
            </div>`;
        }).join('');
      } else {
        detailsEventsList.innerHTML = '<div class="events-empty">Nenhum evento registrado.</div>';
      }
    } else {
      detailsUptime.textContent = '--%';
      detailsEventsList.innerHTML = '<div class="events-empty">Erro ao carregar dados.</div>';
    }

    // ── L7 Performance ─────────────────────────────────────────────────────
    if (isWeb && perfData.status === 'fulfilled') {
      const logs = perfData.value; // newest first
      renderL7Chart(logs);
      renderSparkline(logs);
    } else if (isWeb) {
      detailsL7Chart.innerHTML = '<div class="events-empty">Dados de performance ainda não disponíveis (aguarde o próximo ciclo de checagem).</div>';
      detailsSparkline.innerHTML = '';
    }

    // Force total response time to N/A if device is DOWN, regardless of the historical performance logs
    if (updatedDev && updatedDev.status !== 'UP') {
      detailsTotalMs.textContent = 'N/A';
      detailsTotalMs.style.color = 'var(--color-text-muted)';
    }

  } catch (err) {
    console.error('[API] refreshDeviceDetails failed:', err);
  } finally {
    if (reloadBtnSvg) {
      setTimeout(() => {
        reloadBtnSvg.classList.remove('spinning');
      }, 500);
    }
  }
}

async function openDeviceDetails(id, name, address, status, deviceType) {
  currentDetailsDeviceId = id;
  currentDetailsDevice = { id, name, address, status, deviceType };

  // Set initial modal details
  detailsName.textContent = name;
  detailsAddress.textContent = address;

  const bCls = { UP: 'badge-up', DOWN: 'badge-down', WARNING: 'badge-warning' }[status] || '';
  const lbl  = { UP: '● Online', DOWN: '● Offline', WARNING: '● Alerta' }[status] || status;
  detailsStatusBadge.className = `card-status-badge ${bCls}`;
  detailsStatusBadge.textContent = lbl;

  // Show or hide L7 section based on device type
  const isWeb = deviceType === 'WEB';
  detailsL7Section.style.display  = isWeb ? '' : 'none';
  detailsTotalMsBox.style.display = isWeb ? '' : 'none';

  detailsOverlay.classList.add('open');

  await refreshDeviceDetails(false);
}

/* ── L7 Bar Chart ── */
function renderL7Chart(logs) {
  if (!logs || logs.length === 0) {
    detailsL7Chart.innerHTML = '<div class="events-empty">Dados de performance ainda não disponíveis.</div>';
    detailsTotalMs.textContent = '--';
    return;
  }

  const latest = logs[0]; // most recent check
  const total  = latest.total_ms || 1;

  // Show total in stats row
  if (latest.total_ms != null) {
    detailsTotalMs.textContent = `${Math.round(latest.total_ms)}ms`;
    const col = getLatencyColor(latest.total_ms);
    detailsTotalMs.style.color = col;
  }

  const segments = [
    { key: 'dns_ms',      label: 'DNS + TCP',       color: '#63b3ff' },
    { key: 'ssl_ms',      label: 'TLS/SSL',          color: '#a78bfa' },
    { key: 'ttfb_ms',     label: 'TTFB (Servidor)',  color: '#f59e0b' },
    { key: 'download_ms', label: 'Download',         color: '#63b3ff' },
  ];

  detailsL7Chart.innerHTML = segments.map(seg => {
    const val = latest[seg.key];
    if (val == null || val <= 0) return ''; // skip missing or zero values
    const pct = Math.min(100, (val / total) * 100);
    const valColor = getLatencyColor(val);
    return `
      <div class="l7-bar-row">
        <div class="l7-bar-label">
          <span class="l7-bar-dot" style="background:${seg.color};"></span>
          <span class="l7-bar-name">${seg.label}</span>
        </div>
        <div class="l7-bar-track">
          <div class="l7-bar-fill" style="width:0%; background:${seg.color};" data-pct="${pct.toFixed(1)}"></div>
        </div>
        <span class="l7-bar-value" style="color:${valColor};">${Math.round(val)}ms</span>
      </div>`;
  }).join('');

  // Animate bars after DOM paint
  requestAnimationFrame(() => {
    detailsL7Chart.querySelectorAll('.l7-bar-fill').forEach(bar => {
      bar.style.width = bar.dataset.pct + '%';
    });
  });
}

/* ── Sparkline ── */
function renderSparkline(logs) {
  if (!logs || logs.length === 0) {
    detailsSparkline.innerHTML = '<div class="events-empty" style="padding:0.75rem;">Sem histórico suficiente.</div>';
    return;
  }

  // Logs come newest-first — reverse for left-to-right timeline
  const ordered = [...logs].reverse().filter(l => l.total_ms != null);
  if (ordered.length === 0) {
    detailsSparkline.innerHTML = '<div class="events-empty" style="padding:0.75rem;">Sem histórico suficiente.</div>';
    return;
  }

  const maxVal  = Math.max(...ordered.map(l => l.total_ms), 1);
  const barH    = 52; // px height of the chart area

  const bars = ordered.map(l => {
    const h   = Math.max(4, Math.round((l.total_ms / maxVal) * barH));
    const col = getLatencyColor(l.total_ms);
    const ts  = formatTime(l.timestamp);
    return `<div class="spark-bar" style="height:${h}px; background:${col};" title="${Math.round(l.total_ms)}ms @ ${ts}"></div>`;
  }).join('');

  const minMs  = Math.round(Math.min(...ordered.map(l => l.total_ms)));
  const maxMs  = Math.round(maxVal);
  const avgMs  = Math.round(ordered.reduce((s, l) => s + l.total_ms, 0) / ordered.length);

  detailsSparkline.innerHTML = `
    <div class="spark-meta">
      <span>Mín: <strong style="color:#4dd9a0">${minMs}ms</strong></span>
      <span>Méd: <strong style="color:#63b3ff">${avgMs}ms</strong></span>
      <span>Máx: <strong style="color:#f07070">${maxMs}ms</strong></span>
    </div>
    <div class="spark-chart">${bars}</div>
    <div class="spark-axis">
      <span>← ${ordered.length} checks atrás</span>
      <span>Agora →</span>
    </div>`;
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
function getLatencyColor(ms) {
  if (ms === null || ms === undefined || ms < 0) return '#607090';
  if (ms <= 300) return '#4dd9a0';
  if (ms <= 800) return '#f0b050';
  return '#f07070';
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function escHtml(str) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(str));
  return d.innerHTML;
}

function formatTime(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', second: '2-digit' });
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

  // Periodic REST polling (fallback / sync)
  pollTimer = setInterval(fetchDevices, POLL_INTERVAL_MS);

  // WebSocket real-time
  connectWS();

  // Button bindings
  btnAdd?.addEventListener('click', openModal);
  btnModalCancel?.addEventListener('click', closeModal);
  btnDetailsClose?.addEventListener('click', closeDetailsModal);
  $('btn-details-close-footer')?.addEventListener('click', closeDetailsModal);
  $('btn-details-reload')?.addEventListener('click', () => refreshDeviceDetails(false));
  $('btn-details-download-pdf')?.addEventListener('click', () => {
    if (currentDetailsDeviceId) {
      window.location.href = `${API_BASE}/api/devices/${currentDetailsDeviceId}/report/pdf`;
    }
  });
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
