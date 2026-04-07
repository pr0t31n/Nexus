/* Nexus — Central Hub — main.js */
'use strict';

/* ── Theme ─────────────────────────────────────────────────────────────────── */
function toggleTheme() {
  var html = document.documentElement;
  var next = html.getAttribute('data-mode') === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-mode', next);
  try { localStorage.setItem('nexus-mode', next); } catch(e) {}
}

/* ── Scan trigger ───────────────────────────────────────────────────────────── */
function triggerScan() {
  var btn = document.getElementById('scanBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Scanning…'; }
  if (window._socket) {
    window._socket.emit('request_scan');
  } else {
    fetch('/api/scan', { method: 'POST' });
  }
  setTimeout(function() {
    if (btn) { btn.disabled = false; btn.textContent = 'Scan Now'; }
  }, 5000);
}

/* ── Icon SVGs (mirrors template macros for JS rendering) ────────────────────── */
var ICONS = {
  chip:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="10" height="10" rx="1"/><rect x="9" y="9" width="6" height="4" rx=".5"/><line x1="5" y1="9" x2="7" y2="9"/><line x1="5" y1="12" x2="7" y2="12"/><line x1="5" y1="15" x2="7" y2="15"/><line x1="17" y1="9" x2="19" y2="9"/><line x1="17" y1="12" x2="19" y2="12"/><line x1="17" y1="15" x2="19" y2="15"/><line x1="9" y1="5" x2="9" y2="7"/><line x1="12" y1="5" x2="12" y2="7"/><line x1="15" y1="5" x2="15" y2="7"/><line x1="9" y1="17" x2="9" y2="19"/><line x1="12" y1="17" x2="12" y2="19"/><line x1="15" y1="17" x2="15" y2="19"/></svg>',
  spider: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><line x1="12" y1="9" x2="12" y2="3"/><line x1="9" y1="10" x2="4" y2="6"/><line x1="9" y1="12" x2="3" y2="12"/><line x1="9" y1="14" x2="4" y2="18"/><line x1="12" y1="15" x2="12" y2="21"/><line x1="15" y1="14" x2="20" y2="18"/><line x1="15" y1="12" x2="21" y2="12"/><line x1="15" y1="10" x2="20" y2="6"/></svg>',
  scope:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><line x1="12" y1="2" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="2" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22" y2="12"/></svg>',
  quiver: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="12" y1="12" x2="12" y2="3"/><polyline points="10,5 12,3 14,5"/><path d="M9 12 C8.5 16 8.5 19 9 22 L15 22 C15.5 19 15.5 16 15 12 Z"/><ellipse cx="12" cy="12" rx="3.5" ry="1.2"/></svg>',
  wave:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M1 12 Q4 8 7 12 T13 12 T19 12 T25 12"/><path d="M4 17 Q6 14 8 17"/><path d="M4 7 Q6 4 8 7"/><path d="M16 17 Q18 14 20 17"/><path d="M16 7 Q18 4 20 7"/></svg>',
  scan:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><circle cx="12" cy="15" r="2"/><line x1="6" y1="6" x2="8" y2="6"/></svg>',
  lock:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/><circle cx="12" cy="16" r="1" fill="currentColor"/></svg>',
};

/* ── Card rendering ─────────────────────────────────────────────────────────── */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

function statusBadge(scanning) {
  if (scanning === null || scanning === undefined) {
    return '<span class="status-badge status-na">N/A</span>';
  }
  return scanning
    ? '<span class="status-badge status-scanning">SCANNING</span>'
    : '<span class="status-badge status-idle">IDLE</span>';
}

function renderCard(inst) {
  var icon = ICONS[inst.icon] || ICONS.scope;
  var scanRing = inst.scanning ? '<div class="card-scanning-ring"></div>' : '';
  var extraLinks = '';
  if (inst.extra_urls && inst.extra_urls.length) {
    extraLinks = '<div class="card-extra-links">' +
      inst.extra_urls.map(function(u) {
        return '<a href="' + esc(u) + '" target="_blank" rel="noopener" class="card-extra-link">' + esc(u) + '</a>';
      }).join('') + '</div>';
  }
  var ipLabels = (inst.ips && inst.ips.length > 1) ? 'IPs' : 'IP';
  var ipsHtml = (inst.ips || []).map(function(ip) {
    return '<span class="card-ip">' + esc(ip) + '</span>';
  }).join('');

  return '<div class="instance-card" data-id="' + esc(inst.id) + '">' +
    scanRing +
    '<div class="card-header">' +
      '<div class="card-icon">' + icon + '</div>' +
      '<div class="card-titles">' +
        '<div class="card-name">' + esc(inst.name) + '</div>' +
        '<div class="card-desc">' + esc(inst.desc) + '</div>' +
      '</div>' +
      statusBadge(inst.scanning) +
    '</div>' +
    '<div class="card-body">' +
      '<div class="card-row"><span class="card-label">HOSTNAME</span><span class="card-value">' + esc(inst.hostname) + '</span></div>' +
      '<div class="card-row"><span class="card-label">' + ipLabels + '</span><div class="card-ips">' + ipsHtml + '</div></div>' +
      '<div class="card-row"><span class="card-label">PORT</span><span class="card-value">' + esc(inst.port) + '</span></div>' +
    '</div>' +
    '<div class="card-footer">' +
      '<a href="' + esc(inst.url) + '" target="_blank" rel="noopener" class="card-link">Open ' + esc(inst.name) + ' <span class="card-link-arrow">&#8599;</span></a>' +
      extraLinks +
    '</div>' +
  '</div>';
}

/* ── Service order (mirrors SERVICES in app.py) ─────────────────────────────── */
var SVC_ORDER = [
  {id: 'gauntlet',   name: 'Gauntlet',   desc: 'Device Security Assessment', icon: 'chip'},
  {id: 'spiderfoot', name: 'SpiderFoot', desc: 'OSINT Automation',            icon: 'spider'},
  {id: 'vantage',    name: 'Vantage',    desc: 'Recon Platform',              icon: 'scope'},
  {id: 'quiver',     name: 'Quiver',     desc: 'Pentest Hardware Manager',    icon: 'quiver'},
  {id: 'spectre',    name: 'Spectre',    desc: 'Wireless Assessment',         icon: 'wave'},
  {id: 'nessus',     name: 'Nessus',     desc: 'Vulnerability Scanner',       icon: 'scan'},
  {id: 'mullig4n',   name: 'Mullig4n',   desc: 'Security Management',         icon: 'lock'},
];

function renderGrid(instances) {
  if (!instances || !instances.length) {
    return '<div class="empty-state">' +
      '<div class="empty-state-icon">&#9685;</div>' +
      '<div class="empty-state-msg">No instances found. Click <strong>Scan Now</strong> to search all interfaces.</div>' +
      '</div>';
  }
  var byId = {};
  instances.forEach(function(inst) {
    if (!byId[inst.id]) byId[inst.id] = [];
    byId[inst.id].push(inst);
  });
  return SVC_ORDER.map(function(svc) {
    var items = byId[svc.id];
    if (!items || !items.length) return '';
    var icon = ICONS[svc.icon] || ICONS.scope;
    var header =
      '<div class="service-col-header">' +
        '<div class="service-col-icon">' + icon + '</div>' +
        '<div class="service-col-meta">' +
          '<div class="service-col-name">' + esc(svc.name) + '</div>' +
          '<div class="service-col-desc">' + esc(svc.desc) + '</div>' +
        '</div>' +
        '<span class="service-col-count">' + items.length + '</span>' +
      '</div>';
    return '<div class="service-col" data-svc="' + esc(svc.id) + '">' +
      header + items.map(renderCard).join('') + '</div>';
  }).join('');
}

/* ── SocketIO live updates ──────────────────────────────────────────────────── */
(function() {
  if (typeof io === 'undefined') return;

  var socket = io({ transports: ['websocket', 'polling'] });
  window._socket = socket;

  socket.on('instances_update', function(instances) {
    var grid = document.getElementById('instanceGrid');
    var count = document.getElementById('instanceCount');
    var scanEl = document.getElementById('lastScan');
    if (!grid) return;
    if (count) count.textContent = instances.length;
    if (scanEl) scanEl.textContent = 'Last scan: ' + new Date().toLocaleTimeString('en-US', {hour12:false}) + ' local';
    grid.innerHTML = renderGrid(instances);
    var btn = document.getElementById('scanBtn');
    if (btn) { btn.disabled = false; btn.textContent = 'Scan Now'; }
  });
})();
