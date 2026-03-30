/* ─── メインアプリケーションJavaScript ─────────────────── */

// ─── State ───────────────────────────────────────────────
let destinations = [];
let lines = [];
let operators = [];
let ws = null;
let reconnectTimer = null;

// ─── Init ─────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  showPage('dashboard');
  connectWS();
  refreshDashboard();
  refreshCarrierCapacity();
  loadDialInterval();
  setInterval(refreshDashboard, 10000);
  setInterval(refreshCarrierCapacity, 30000);
});

// ─── Page Navigation ──────────────────────────────────────
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item, .bottom-nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById(`page-${name}`).classList.add('active');
  document.querySelectorAll(`[data-page="${name}"]`).forEach(el => el.classList.add('active'));

  if (name === 'dashboard') { refreshDashboard(); refreshCarrierCapacity(); }
  if (name === 'destinations') loadDestinations();
  if (name === 'lines') loadLines();
  if (name === 'operators') loadOperators();
  if (name === 'logs') loadLogs();
}

// ─── WebSocket ────────────────────────────────────────────
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/status`);

  ws.onopen = () => {
    setWsStatus(true);
    clearTimeout(reconnectTimer);
  };

  ws.onclose = () => {
    setWsStatus(false);
    reconnectTimer = setTimeout(connectWS, 3000);
  };

  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    handleWsEvent(data);
  };
}

function setWsStatus(connected) {
  const dot = document.querySelector('.ws-dot');
  const label = document.querySelector('.ws-label');
  dot.className = `ws-dot ${connected ? 'connected' : 'disconnected'}`;
  label.textContent = connected ? '接続済み' : '再接続中...';
}

function handleWsEvent(data) {
  const feed = document.getElementById('liveFeed');
  const now = new Date().toLocaleTimeString('ja-JP');
  let msg = '';
  let color = '';

  switch (data.type) {
    case 'engine':
      msg = `エンジン ${data.running ? '▶ 起動' : '⏹ 停止'}`;
      color = data.running ? 'var(--success)' : 'var(--danger)';
      break;
    case 'call_started':
      msg = `📞 発信開始: [${data.dest_name}] ${data.to} (${data.carrier})`;
      color = 'var(--primary)';
      updateActiveCalls(1);
      break;
    case 'call_ended':
      msg = `📴 通話終了: ${data.call_sid?.slice(0,8)}... 結果:${data.result} ${data.duration_sec}秒`;
      color = data.result === 'connected' ? 'var(--success)' : 'var(--text-muted)';
      updateActiveCalls(-1);
      break;
    case 'call_transferred':
      msg = `🔀 転送: ${data.call_sid?.slice(0,8)}... → ${data.operator_name}`;
      color = 'var(--warning)';
      break;
    case 'call_update':
      msg = `🔄 更新: ${data.call_sid?.slice(0,8)}... ${data.status} → ${data.result}`;
      break;
    default:
      msg = JSON.stringify(data);
  }

  const item = document.createElement('div');
  item.className = 'feed-item';
  item.innerHTML = `<span class="feed-time">${now}</span><span class="feed-msg" style="color:${color || 'inherit'}">${msg}</span>`;
  feed.prepend(item);

  // 最大100件
  while (feed.children.length > 100) feed.lastChild.remove();
}

function updateActiveCalls(delta) {
  const el = document.getElementById('statActiveCalls');
  const current = parseInt(el.textContent) || 0;
  const next = Math.max(0, current + delta);
  el.textContent = next;
  document.getElementById('activePulse').className = `stat-pulse ${next > 0 ? 'active' : ''}`;
}

function clearLiveFeed() {
  document.getElementById('liveFeed').innerHTML = '';
}

// ─── Dashboard ────────────────────────────────────────────
async function refreshDashboard() {
  const data = await api('GET', '/api/dashboard/stats');
  if (!data) return;

  document.getElementById('statActiveCalls').textContent = data.active_calls;
  document.getElementById('statConnected').textContent = data.connected_calls_today;
  document.getElementById('statTotal').textContent = data.total_calls_today;
  document.getElementById('statDuration').innerHTML = `${Math.round(data.total_duration_today/60)}<small>分</small>`;
  document.getElementById('statCost').textContent = `$${data.estimated_cost_today.toFixed(2)}`;
  const rate = data.total_calls_today > 0
    ? Math.round(data.connected_calls_today / data.total_calls_today * 100) : 0;
  document.getElementById('statRate').innerHTML = `${rate}<small>%</small>`;

  document.getElementById('activePulse').className =
    `stat-pulse ${data.active_calls > 0 ? 'active' : ''}`;

  const btn = document.getElementById('btnEngine');
  const icon = document.getElementById('btnEngineIcon');
  const label = document.getElementById('btnEngineLabel');
  if (data.engine_running) {
    btn.classList.add('running');
    icon.textContent = '⏹';
    label.textContent = '停止';
  } else {
    btn.classList.remove('running');
    icon.textContent = '▶';
    label.textContent = '発信開始';
  }

  // Destinations status
  const destEl = document.getElementById('destStatus');
  destEl.innerHTML = data.destinations.map(d => `
    <div class="status-item">
      <div>
        <div class="status-item-name">${d.name} — ${d.phone_number}</div>
        <div class="status-item-info">割当: ${d.allocated_lines}回線</div>
      </div>
      <span class="badge ${d.is_active ? 'badge-success' : 'badge-muted'}">
        ${d.is_active ? '稼働中' : '停止'}
      </span>
    </div>
  `).join('') || '<div class="status-item" style="color:var(--text-muted)">宛先なし</div>';

  // Operators status
  const opEl = document.getElementById('opStatus');
  opEl.innerHTML = data.operators.map(o => `
    <div class="status-item">
      <div>
        <div class="status-item-name">${o.name}</div>
        <div class="status-item-info">優先度: ${o.priority}</div>
      </div>
      <span class="badge ${o.is_available ? 'badge-success' : 'badge-warning'}">
        ${o.is_available ? '空き' : '通話中'}
      </span>
    </div>
  `).join('') || '<div class="status-item" style="color:var(--text-muted)">担当者なし</div>';
}

async function refreshCarrierCapacity() {
  const el = document.getElementById('carrierCapacity');
  el.innerHTML = '<span style="color:var(--text-muted);font-size:13px">取得中...</span>';
  const data = await api('GET', '/api/dashboard/carrier-capacity');
  if (!data) { el.innerHTML = '<span style="color:var(--danger);font-size:13px">取得失敗</span>'; return; }

  const cards = [];

  function buildCarrierCard(label, c) {
    if (c.error) {
      return `
        <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:16px 20px;min-width:200px">
          <div style="font-size:11px;color:var(--text-muted);margin-bottom:8px;font-weight:600;letter-spacing:.5px">${label}</div>
          <div style="color:var(--danger);font-size:13px">${c.error}</div>
        </div>`;
    }
    const numRows = (c.numbers || []).map(n => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);gap:16px">
        <span style="font-size:13px;font-family:monospace;color:var(--text)">${n.number}</span>
        <span style="font-size:11px;color:var(--text-muted);flex:1;padding-left:8px">${n.friendly_name || ''}</span>
        <span style="font-size:11px;font-weight:600;color:${n.in_use ? 'var(--warning)' : 'var(--success)'}">${n.in_use ? '使用中' : '空き'}</span>
      </div>
    `).join('');
    return `
      <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:16px 20px;flex:1;min-width:300px">
        <div style="font-size:11px;color:var(--text-muted);margin-bottom:12px;font-weight:600;letter-spacing:.5px">${label}</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;text-align:center;margin-bottom:16px">
          <div>
            <div style="font-size:22px;font-weight:700;color:var(--text)">${c.owned}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">所有番号</div>
          </div>
          <div>
            <div style="font-size:22px;font-weight:700;color:var(--warning)">${c.active}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">使用中</div>
          </div>
          <div>
            <div style="font-size:22px;font-weight:700;color:var(--success)">${c.available}</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">空き</div>
          </div>
        </div>
        ${numRows || '<div style="font-size:13px;color:var(--text-muted)">番号なし</div>'}
      </div>`;
  }

  if (data.twilio)  cards.push(buildCarrierCard('TWILIO', data.twilio));
  if (data.telnyx)  cards.push(buildCarrierCard('TELNYX', data.telnyx));
  el.innerHTML = cards.join('');
}

// ─── System Settings ──────────────────────────────────────
async function loadDialInterval() {
  const data = await api('GET', '/api/dashboard/settings');
  if (data) {
    document.getElementById('dialIntervalInput').value = data.dial_interval_sec;
  }
}

async function saveDialInterval() {
  const val = parseFloat(document.getElementById('dialIntervalInput').value);
  if (isNaN(val) || val < 1) return;
  const data = await api('PUT', '/api/dashboard/settings', { dial_interval_sec: val });
  if (data) {
    const msg = document.getElementById('dialIntervalMsg');
    msg.style.display = 'inline';
    setTimeout(() => { msg.style.display = 'none'; }, 2000);
  }
}

async function toggleEngine() {
  const btn = document.getElementById('btnEngine');
  const running = btn.classList.contains('running');
  await api('POST', running ? '/api/dashboard/stop' : '/api/dashboard/start');
  await refreshDashboard();
}

// ─── Destinations ─────────────────────────────────────────
async function loadDestinations() {
  destinations = await api('GET', '/api/destinations') || [];
  const tbody = document.querySelector('#destTable tbody');
  tbody.innerHTML = destinations.map(d => `
    <tr>
      <td>${d.id}</td>
      <td><strong>${d.name}</strong></td>
      <td>${d.phone_number}</td>
      <td>${d.allocated_lines}</td>
      <td>
        <span class="badge ${d.is_active ? 'badge-success' : 'badge-muted'}">${d.is_active ? '有効' : '無効'}</span>
        ${d.transfer_on_ringing ? '<span class="badge" style="background:rgba(255,179,71,0.15);color:var(--warning)">即時転送</span>' : ''}
      </td>
      <td>
        <button class="btn btn-ghost btn-sm" onclick="editDest(${d.id})">編集</button>
        <button class="btn btn-sm" style="background:rgba(255,77,109,0.15);color:var(--danger);border:none;" onclick="deleteDest(${d.id})">削除</button>
      </td>
    </tr>
  `).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:24px">データなし</td></tr>`;
}

function openDestModal(id) {
  document.getElementById('destId').value = '';
  document.getElementById('destForm').reset();
  document.getElementById('destActive').checked = true;
  document.getElementById('destModalTitle').textContent = '発信先追加';
  openModal('destModal');
}

function editDest(id) {
  const d = destinations.find(x => x.id === id);
  if (!d) return;
  document.getElementById('destId').value = d.id;
  document.getElementById('destName').value = d.name;
  document.getElementById('destPhone').value = d.phone_number;
  document.getElementById('destLines').value = d.allocated_lines;
  document.getElementById('destActive').checked = d.is_active;
  document.getElementById('destTransferOnRinging').checked = d.transfer_on_ringing || false;
  document.getElementById('destModalTitle').textContent = '発信先編集';
  openModal('destModal');
}

async function saveDest(event) {
  event.preventDefault();
  const id = document.getElementById('destId').value;
  const payload = {
    name: document.getElementById('destName').value,
    phone_number: document.getElementById('destPhone').value,
    allocated_lines: parseInt(document.getElementById('destLines').value),
    is_active: document.getElementById('destActive').checked,
    transfer_on_ringing: document.getElementById('destTransferOnRinging').checked,
  };
  if (id) {
    await api('PUT', `/api/destinations/${id}`, payload);
  } else {
    await api('POST', '/api/destinations', payload);
  }
  closeModal('destModal');
  loadDestinations();
}

async function deleteDest(id) {
  if (!confirm('この宛先を削除しますか？')) return;
  await api('DELETE', `/api/destinations/${id}`);
  loadDestinations();
}

// ─── Lines ────────────────────────────────────────────────
async function loadLines() {
  lines = await api('GET', '/api/lines') || [];
  destinations = await api('GET', '/api/destinations') || [];
  const destMap = Object.fromEntries(destinations.map(d => [d.id, d.name]));
  const tbody = document.querySelector('#lineTable tbody');
  tbody.innerHTML = lines.map(l => `
    <tr>
      <td>${l.id}</td>
      <td><span class="badge ${l.carrier === 'twilio' ? 'carrier-twilio' : 'carrier-telnyx'}">${l.carrier}</span></td>
      <td>${l.from_number}</td>
      <td>${l.destination_id ? destMap[l.destination_id] || l.destination_id : '—'}</td>
      <td>${l.priority}</td>
      <td><span class="badge ${l.is_active ? 'badge-success' : 'badge-muted'}">${l.is_active ? '有効' : '無効'}</span></td>
      <td>
        <button class="btn btn-ghost btn-sm" onclick="editLine(${l.id})">編集</button>
        <button class="btn btn-sm" style="background:rgba(255,77,109,0.15);color:var(--danger);border:none;" onclick="deleteLine(${l.id})">削除</button>
      </td>
    </tr>
  `).join('') || `<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:24px">データなし</td></tr>`;
}

async function openLineModal() {
  destinations = await api('GET', '/api/destinations') || [];
  populateDestSelect('lineDest', true);
  document.getElementById('lineId').value = '';
  document.getElementById('lineForm').reset();
  document.getElementById('lineActive').checked = true;
  document.getElementById('lineModalTitle').textContent = '回線追加';
  openModal('lineModal');
}

async function editLine(id) {
  const l = lines.find(x => x.id === id);
  if (!l) return;
  destinations = await api('GET', '/api/destinations') || [];
  populateDestSelect('lineDest', true);
  document.getElementById('lineId').value = l.id;
  document.getElementById('lineCarrier').value = l.carrier;
  document.getElementById('lineFrom').value = l.from_number;
  document.getElementById('lineDest').value = l.destination_id || '';
  document.getElementById('linePriority').value = l.priority;
  document.getElementById('lineActive').checked = l.is_active;
  document.getElementById('lineModalTitle').textContent = '回線編集';
  openModal('lineModal');
}

async function saveLine(event) {
  event.preventDefault();
  const id = document.getElementById('lineId').value;
  const destVal = document.getElementById('lineDest').value;
  const payload = {
    carrier: document.getElementById('lineCarrier').value,
    from_number: document.getElementById('lineFrom').value,
    destination_id: destVal ? parseInt(destVal) : null,
    priority: parseInt(document.getElementById('linePriority').value),
    is_active: document.getElementById('lineActive').checked,
  };
  if (id) {
    await api('PUT', `/api/lines/${id}`, payload);
  } else {
    await api('POST', '/api/lines', payload);
  }
  closeModal('lineModal');
  loadLines();
}

async function deleteLine(id) {
  if (!confirm('この回線を削除しますか？')) return;
  await api('DELETE', `/api/lines/${id}`);
  loadLines();
}

// ─── Operators ────────────────────────────────────────────
async function loadOperators() {
  operators = await api('GET', '/api/operators') || [];
  const tbody = document.querySelector('#opTable tbody');
  tbody.innerHTML = operators.map(o => `
    <tr>
      <td>${o.id}</td>
      <td>${o.name}</td>
      <td>${o.phone_number}</td>
      <td>${o.priority}</td>
      <td><span class="badge ${o.is_available ? 'badge-success' : 'badge-warning'}">${o.is_available ? '空き' : '通話中'}</span></td>
      <td>
        <button class="btn btn-ghost btn-sm" onclick="editOp(${o.id})">編集</button>
        ${!o.is_available ? `<button class="btn btn-sm" style="background:rgba(255,179,71,0.15);color:var(--warning);border:none;" onclick="forceHangup(${o.id})">強制切断</button>` : ''}
        <button class="btn btn-sm" style="background:rgba(255,77,109,0.15);color:var(--danger);border:none;" onclick="deleteOp(${o.id})">削除</button>
      </td>
    </tr>
  `).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:24px">データなし</td></tr>`;
}

function openOpModal() {
  document.getElementById('opId').value = '';
  document.getElementById('opForm').reset();
  document.getElementById('opActive').checked = true;
  document.getElementById('opModalTitle').textContent = '担当者追加';
  openModal('opModal');
}

function editOp(id) {
  const o = operators.find(x => x.id === id);
  if (!o) return;
  document.getElementById('opId').value = o.id;
  document.getElementById('opName').value = o.name;
  document.getElementById('opPhone').value = o.phone_number;
  document.getElementById('opPriority').value = o.priority;
  document.getElementById('opActive').checked = o.is_active;
  document.getElementById('opModalTitle').textContent = '担当者編集';
  openModal('opModal');
}

async function saveOp(event) {
  event.preventDefault();
  const id = document.getElementById('opId').value;
  const payload = {
    name: document.getElementById('opName').value,
    phone_number: document.getElementById('opPhone').value,
    priority: parseInt(document.getElementById('opPriority').value),
    is_active: document.getElementById('opActive').checked,
  };
  if (id) {
    await api('PUT', `/api/operators/${id}`, payload);
  } else {
    await api('POST', '/api/operators', payload);
  }
  closeModal('opModal');
  loadOperators();
}

async function deleteOp(id) {
  if (!confirm('この担当者を削除しますか？')) return;
  await api('DELETE', `/api/operators/${id}`);
  loadOperators();
}

async function forceHangup(id) {
  if (!confirm('この担当者への転送を強制切断しますか？')) return;
  await api('POST', `/api/operators/${id}/force-hangup`);
  loadOperators();
}

// ─── Logs ─────────────────────────────────────────────────
async function loadLogs() {
  const result = document.getElementById('logResultFilter').value;
  const dateFrom = document.getElementById('logDateFrom').value;
  const dateTo = document.getElementById('logDateTo').value;

  let url = '/api/call-logs?limit=100';
  if (result) url += `&result=${result}`;
  if (dateFrom) url += `&date_from=${dateFrom}`;
  if (dateTo) url += `&date_to=${dateTo}`;

  const logs = await api('GET', url) || [];
  const tbody = document.querySelector('#logTable tbody');

  const resultBadge = (r) => {
    const map = {
      connected: 'badge-success',
      busy: 'badge-danger',
      congested: 'badge-warning',
      failed: 'badge-danger',
      'no-answer': 'badge-muted',
      calling: 'badge-primary',
    };
    const labels = {
      connected: '接続', busy: '話中', congested: '混雑',
      failed: '失敗', 'no-answer': '不応答', calling: '発信中',
    };
    return `<span class="badge ${map[r] || 'badge-muted'}">${labels[r] || r || '—'}</span>`;
  };

  tbody.innerHTML = logs.map(l => {
    const calledAt = l.called_at ? new Date(l.called_at).toLocaleString('ja-JP') : '—';
    const dest = l.to_number || '—';
    const duration = l.duration_sec != null ? `${l.duration_sec}秒` : '—';
    const cost = l.cost != null ? `$${l.cost.toFixed(4)}` : '—';
    return `
      <tr>
        <td>${l.id}</td>
        <td>${calledAt}</td>
        <td>${dest}</td>
        <td><span class="badge ${l.carrier === 'twilio' ? 'carrier-twilio' : l.carrier === 'telnyx' ? 'carrier-telnyx' : 'badge-muted'}">${l.carrier || '—'}</span></td>
        <td>${resultBadge(l.result)}</td>
        <td>${duration}</td>
        <td>${cost}</td>
        <td>${l.transfer_to || '—'}</td>
      </tr>
    `;
  }).join('') || `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:24px">データなし</td></tr>`;
}

// ─── Helpers ──────────────────────────────────────────────
function populateDestSelect(selectId, includeEmpty = false) {
  const sel = document.getElementById(selectId);
  sel.innerHTML = includeEmpty ? '<option value="">未割当</option>' : '';
  destinations.forEach(d => {
    sel.innerHTML += `<option value="${d.id}">${d.name} (${d.phone_number})</option>`;
  });
}

function openModal(id) {
  document.getElementById(id).classList.add('open');
}
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}

async function api(method, path, body) {
  try {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    if (res.status === 204) return null;
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      console.error(`API error ${res.status}:`, err);
      return null;
    }
    return await res.json();
  } catch (e) {
    console.error('API fetch error:', e);
    return null;
  }
}
