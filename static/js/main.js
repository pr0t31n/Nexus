/* Nexus — Central Hub — main.js */
'use strict';

/* ── Theme / Palette ────────────────────────────────────────────────────────── */
var _KEY_PALETTE = 'nexus-palette';
var _KEY_MODE    = 'nexus-mode';

var PALETTES_META = [
  // Professional
  { id: 'tuxedo',      label: 'Tuxedo',       d: ['#0a0a0a','#e0e0e0'],   l: ['#ffffff','#111111'] },
  { id: 'meridian',    label: 'Meridian',     d: ['#0e1628','#4a88cc'],   l: ['#f6f9ff','#1a4a8c'] },
  { id: 'alabaster',   label: 'Alabaster',    d: ['#121416','#9caab8'],   l: ['#faf8f5','#2c3038'] },
  { id: 'leadership',  label: 'Leadership',   d: ['#0b1428','#f5c842'],   l: ['#fdf8f0','#c8940a'] },
  { id: 'blueprint',   label: 'Blueprint',    d: ['#061830','#5090e0'],   l: ['#f0f5ff','#1a4898'] },
  { id: 'resume',      label: 'Resume',       d: ['#141008','#c8a860'],   l: ['#faf6ee','#1c2840'] },
  // Casual
  { id: 'foundry',     label: 'Foundry',      d: ['#0d0d0d','#f0a500'],   l: ['#f5f0e8','#c88000'] },
  { id: 'forge',       label: 'Forge',        d: ['#0a0804','#c8921f'],   l: ['#ede0c4','#8b5e10'] },
  { id: 'frigid',      label: 'Frigid',       d: ['#040e14','#30b0cc'],   l: ['#e4f0f8','#0a6888'] },
  { id: 'bloom',       label: 'Bloom',        d: ['#120e18','#c490c8'],   l: ['#faf6ff','#9060a8'] },
  { id: 'prism',       label: 'Prism',        d: ['#0c0f2e','#c084fc'],   l: ['#c4b5fd','#7c3aed'] },
  { id: 'ember',       label: 'Ember',        d: ['#140206','#e83020'],   l: ['#fff4ee','#b82010'] },
  { id: 'oldglory',    label: 'Old Glory',    d: ['#010410','#f03050'],   l: ['#e0e4f0','#021a5c'] },
  { id: 'alchemist',   label: 'Alchemist',    d: ['#020606','#50c8a0'],   l: ['#daf0ec','#0a2820'] },
  // Nerdy
  { id: 'coruscant',   label: 'Coruscant',    d: ['#050007','#ff2040'],   l: ['#f5f8fe','#1a4a8c'] },
  { id: 'retro',       label: 'Retro',        d: ['#020a02','#33ff33'],   l: ['#0d0700','#ffaa00'] },
  { id: 'jarvis',      label: 'JARVIS',       d: ['#020c1b','#00d0ff'],   l: ['#d0e8f8','#0068c8'] },
  { id: 'hal9000',     label: 'HAL-9000',     d: ['#040202','#aa1800'],   l: ['#f8f5f2','#aa1800'] },
  { id: 'neuromancer', label: 'Neuromancer',  d: ['#06030c','#d010b8'],   l: ['#faf0ff','#b010a0'] },
  { id: 'deepspace',   label: 'Deep Space',   d: ['#030610','#9070f0'],   l: ['#f0f4ff','#5040c0'] },
  { id: 'bladerunner', label: 'Blade Runner', d: ['#0a0810','#ff8c00'],   l: ['#f4f0e8','#c06800'] },
  { id: 'synthwave',   label: 'Synthwave',    d: ['#0d0018','#ff0880'],   l: ['#fce8ff','#c00060'] },
  // Natural
  { id: 'espresso',    label: 'Espresso',     d: ['#130c06','#c07030'],   l: ['#fef8ee','#8b4a10'] },
  { id: 'terracotta',  label: 'Terracotta',   d: ['#180806','#d45828'],   l: ['#faf2e8','#b04020'] },
  { id: 'sage',        label: 'Sage',         d: ['#060c06','#78b860'],   l: ['#f4f9f0','#2a6830'] },
  { id: 'blush',       label: 'Blush',        d: ['#160608','#d06878'],   l: ['#fdf4f5','#8a2848'] },
];
var NEXUS_PALETTES = PALETTES_META.map(function(p) { return p.id; });

var PALETTE_CATEGORIES = [
  { id: 'professional', label: 'Professional', palettes: ['tuxedo','meridian','alabaster','leadership','blueprint','resume'] },
  { id: 'casual',       label: 'Casual',       palettes: ['foundry','forge','frigid','bloom','prism','ember','oldglory','alchemist'] },
  { id: 'nerdy',        label: 'Nerdy',        palettes: ['coruscant','retro','jarvis','hal9000','neuromancer','deepspace','bladerunner','synthwave'] },
  { id: 'natural',      label: 'Natural',      palettes: ['espresso','terracotta','sage','blush'] },
];

function _nxLoadPref(key)      { try { return localStorage.getItem(key); } catch(e) { return null; } }
function _nxSavePref(key, val) { try { localStorage.setItem(key, val);   } catch(e) {} }

function _nxSwatchHtml(colors) {
  return colors.map(function(c) {
    return '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;' +
           'background:' + c + ';border:1px solid rgba(128,128,128,.3);flex-shrink:0"></span>';
  }).join('');
}

function applyTheme(palette, mode) {
  var html = document.documentElement;
  html.setAttribute('data-palette', palette);
  html.setAttribute('data-mode',    mode);

  var themeChk = document.getElementById('themeToggle');
  if (themeChk && themeChk.type === 'checkbox') themeChk.checked = (mode === 'dark');

  var sel = document.getElementById('paletteSelect');
  if (sel) sel.value = palette;

  _nxUpdatePickerUI(palette, mode);
  _nxSavePref(_KEY_PALETTE, palette);
  _nxSavePref(_KEY_MODE,    mode);
}

function _nxUpdatePickerUI(palette, mode) {
  var label    = document.getElementById('palettePickerLabel');
  var swatches = document.getElementById('palettePickerSwatches');
  if (!label || !swatches) return;
  var meta = PALETTES_META.filter(function(p) { return p.id === palette; })[0] || PALETTES_META[0];
  var cols = mode === 'dark' ? meta.d : meta.l;
  label.textContent  = meta.label;
  swatches.innerHTML = _nxSwatchHtml(cols);
}

function _nxBuildDropdown() {
  var ul = document.getElementById('palettePickerDropdown');
  if (!ul || ul.children.length) return;
  var mode = document.documentElement.getAttribute('data-mode') || 'dark';

  ul.innerHTML = PALETTE_CATEGORIES.map(function(cat) {
    var itemsHtml = cat.palettes.map(function(pid) {
      var meta = PALETTES_META.filter(function(p) { return p.id === pid; })[0];
      if (!meta) return '';
      var cols = mode === 'dark' ? meta.d : meta.l;
      return '<li role="option" data-palette="' + meta.id + '"' +
             ' style="display:flex;align-items:center;gap:.45rem;padding:.32rem .6rem;' +
             'cursor:pointer;white-space:nowrap;font-size:.75rem">' +
             _nxSwatchHtml(cols) +
             '<span>' + meta.label + '</span></li>';
    }).join('');
    return '<li class="palette-cat-item" data-cat="' + cat.id + '">' +
           '<div class="palette-cat-header" role="presentation">' +
           '<span>' + cat.label + '</span>' +
           '<span class="cat-arrow">&#x25b8;</span></div>' +
           '<ul class="palette-cat-sub" role="group" aria-label="' + cat.label + ' themes">' +
           itemsHtml + '</ul></li>';
  }).join('');

  ul.querySelectorAll('li[data-palette]').forEach(function(li) {
    li.addEventListener('click', function(e) {
      e.stopPropagation();
      applyTheme(li.dataset.palette, document.documentElement.getAttribute('data-mode') || 'dark');
      _nxClosePicker();
    });
  });

  ul.querySelectorAll('.palette-cat-header').forEach(function(header) {
    header.addEventListener('click', function(e) {
      e.stopPropagation();
      var item   = header.closest('.palette-cat-item');
      var isOpen = item.classList.contains('open');
      ul.querySelectorAll('.palette-cat-item.open').forEach(function(i) { i.classList.remove('open'); });
      if (!isOpen) item.classList.add('open');
    });
  });
}

function _nxRefreshSwatches(mode) {
  var ul = document.getElementById('palettePickerDropdown');
  if (!ul) return;
  ul.querySelectorAll('li[data-palette]').forEach(function(li) {
    var meta = PALETTES_META.filter(function(p) { return p.id === li.dataset.palette; })[0];
    if (!meta) return;
    var cols = mode === 'dark' ? meta.d : meta.l;
    var spans = li.querySelectorAll('span[style*="border-radius:50%"]');
    spans.forEach(function(s, i) { if (cols[i]) s.style.background = cols[i]; });
  });
}

function _nxOpenPicker() {
  var picker = document.getElementById('palettePicker');
  var dd     = document.getElementById('palettePickerDropdown');
  if (!picker || !dd) return;
  _nxBuildDropdown();
  _nxRefreshSwatches(document.documentElement.getAttribute('data-mode') || 'dark');
  dd.classList.remove('drop-up');
  picker.setAttribute('aria-expanded', 'true');
  dd.style.display = 'block';
  var rect = dd.getBoundingClientRect();
  if (rect.bottom > (window.innerHeight || document.documentElement.clientHeight)) {
    dd.classList.add('drop-up');
  }
}

function _nxClosePicker() {
  var picker = document.getElementById('palettePicker');
  var dd     = document.getElementById('palettePickerDropdown');
  if (picker) picker.setAttribute('aria-expanded', 'false');
  if (dd) {
    dd.style.display = 'none';
    dd.classList.remove('drop-up');
    dd.querySelectorAll('.palette-cat-item.open').forEach(function(i) { i.classList.remove('open'); });
  }
}

function _nxTogglePicker() {
  var dd = document.getElementById('palettePickerDropdown');
  if (!dd) return;
  dd.style.display === 'block' ? _nxClosePicker() : _nxOpenPicker();
}

(function _nxInitTheme() {
  var savedPalette = _nxLoadPref(_KEY_PALETTE);
  var savedMode    = _nxLoadPref(_KEY_MODE);
  var palette = NEXUS_PALETTES.indexOf(savedPalette) !== -1 ? savedPalette : 'resume';
  var mode    = (savedMode === 'light' || savedMode === 'dark') ? savedMode : 'light';
  applyTheme(palette, mode);
})();

document.addEventListener('change', function(e) {
  if (e.target.id === 'themeToggle') {
    var palette = document.documentElement.getAttribute('data-palette') || 'resume';
    var newMode = e.target.checked ? 'dark' : 'light';
    applyTheme(palette, newMode);
    _nxRefreshSwatches(newMode);
  }
});

document.addEventListener('click', function(e) {
  if (e.target.closest('#palettePickerDropdown')) return;
  if (e.target.closest('#palettePicker')) { _nxTogglePicker(); return; }
  _nxClosePicker();
});

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') _nxClosePicker();
});

/* ── Scan trigger ───────────────────────────────────────────────────────────── */
function triggerScan() {
  var btn = document.getElementById('scanBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Scanning\u2026'; }
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
  lock:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/><circle cx="12" cy="16" r="1" fill="currentColor"/></svg>',
  crucible: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4 H19"/><path d="M7 4 L9 17 Q12 21 15 17 L17 4"/><path d="M10 21 Q11 19 12 21 Q13 19 14 21"/></svg>',
};

/* ── Service order with updated descriptions ────────────────────────────────── */
var SVC_ORDER = [
  {id: 'gauntlet',   name: 'Gauntlet',   icon: 'chip',   desc: 'Hardware'},
  {id: 'spiderfoot', name: 'SpiderFoot', icon: 'spider', desc: 'OSINT Automation'},
  {id: 'vantage',    name: 'Vantage',    icon: 'scope',  desc: 'Network & OSINT'},
  {id: 'quiver',     name: 'Quiver',     icon: 'quiver', desc: 'Payload Manager'},
  {id: 'spectre',    name: 'Spectre',    icon: 'wave',   desc: 'Wireless'},
  {id: 'nessus',     name: 'Nessus',     icon: 'scan',   desc: 'Vulnerability Scanner'},
  {id: 'hitman',   name: 'HiTMan',   icon: 'lock',     desc: 'Net, OSINT & Reporting'},
  {id: 'crucible',   name: 'Crucible',   icon: 'crucible', desc: 'Web & API Assessment'},
];

/* ── HTML escaping ──────────────────────────────────────────────────────────── */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

/* ── Detect IP-as-hostname (unresolved) ─────────────────────────────────────── */
function isUnknownHost(hostname) {
  return /^\d+\.\d+\.\d+\.\d+$/.test(hostname);
}

/* ── Sort instances alphabetically by hostname ──────────────────────────────── */
function sortInstances(items) {
  return items.slice().sort(function(a, b) {
    var ha = a.hostname.toLowerCase(), hb = b.hostname.toLowerCase();
    return ha < hb ? -1 : ha > hb ? 1 : 0;
  });
}

/* ── Status badge ───────────────────────────────────────────────────────────── */
function statusBadge(scanning) {
  if (scanning === null || scanning === undefined) {
    return '<span class="status-badge status-na">N/A</span>';
  }
  return scanning
    ? '<span class="status-badge status-scanning">SCANNING</span>'
    : '<span class="status-badge status-idle">IDLE</span>';
}

/* ── Render a single instance card (collapsed by default) ───────────────────── */
function renderCard(inst) {
  var unknown = isUnknownHost(inst.hostname);
  var nameClass = 'card-display-name' + (unknown ? ' is-unknown' : '');
  var displayName = unknown ? '-UNKNOWN-' : esc(inst.hostname);

  var ipsHtml = (inst.ips || []).map(function(ip) {
    return '<span class="card-ip">' + esc(ip) + '</span>';
  }).join('');
  var ipLabel = (inst.ips && inst.ips.length > 1) ? 'IPs' : 'IP';

  var extraLinks = '';
  if (inst.extra_urls && inst.extra_urls.length) {
    extraLinks = '<div class="card-extra-links">' +
      inst.extra_urls.map(function(u) {
        return '<a href="' + esc(u) + '" target="_blank" rel="noopener" class="card-extra-link">' + esc(u) + '</a>';
      }).join('') + '</div>';
  }

  var scanRing = inst.scanning ? '<div class="card-scanning-ring"></div>' : '';

  return '<div class="instance-card" data-id="' + esc(inst.id) + '" data-url="' + esc(inst.url) + '">' +
    scanRing +
    '<div class="card-collapsed-bar">' +
      '<span class="card-chevron">&#x203A;</span>' +
      '<span class="' + nameClass + '">' + displayName + '</span>' +
      '<div class="card-collapsed-right">' +
        statusBadge(inst.scanning) +
        '<button class="card-visit-btn" title="Open ' + esc(inst.name) + '" data-url="' + esc(inst.url) + '">&#8599;</button>' +
      '</div>' +
    '</div>' +
    '<div class="card-expanded-content">' +
      '<div class="card-body">' +
        '<div class="card-row"><span class="card-label">HOSTNAME</span><span class="card-value">' + esc(inst.hostname) + '</span></div>' +
        '<div class="card-row"><span class="card-label">' + ipLabel + '</span><div class="card-ips">' + ipsHtml + '</div></div>' +
        '<div class="card-row"><span class="card-label">PORT</span><span class="card-value">' + esc(inst.port) + '</span></div>' +
      '</div>' +
      '<div class="card-footer">' +
        '<a href="' + esc(inst.url) + '" target="_blank" rel="noopener" class="card-link">Open ' + esc(inst.name) + ' <span class="card-link-arrow">&#8599;</span></a>' +
        extraLinks +
      '</div>' +
    '</div>' +
  '</div>';
}

/* ── Render the full grid ───────────────────────────────────────────────────── */
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

  var sortedSvcs = SVC_ORDER.slice().sort(function(a, b) {
    return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
  });

  var html = sortedSvcs.map(function(svc) {
    var items = byId[svc.id];
    if (!items || !items.length) return '';
    var sorted = sortInstances(items);
    var icon = ICONS[svc.icon] || ICONS.scope;
    var header =
      '<div class="service-col-header">' +
        '<div class="service-col-icon">' + icon + '</div>' +
        '<div class="service-col-meta">' +
          '<div class="service-col-name">' + esc(svc.name) + '</div>' +
          '<div class="service-col-desc">' + esc(svc.desc) + '</div>' +
        '</div>' +
        '<span class="service-col-count">' + sorted.length + '</span>' +
      '</div>';
    return '<div class="service-col" data-svc="' + esc(svc.id) + '">' +
      header + sorted.map(renderCard).join('') + '</div>';
  }).join('');

  return html || '<div class="empty-state">' +
    '<div class="empty-state-icon">&#9685;</div>' +
    '<div class="empty-state-msg">No matches. Clear the search to show all instances.</div>' +
    '</div>';
}

/* ── Search suggestions (custom dropdown — substring match for IPs + hostnames) ─ */
var _allSuggestions = [];

function buildSuggestions(instances) {
  var seen = {};
  _allSuggestions = [];
  instances.forEach(function(inst) {
    [inst.name, inst.hostname].forEach(function(val) {
      if (val && !seen[val] && !isUnknownHost(val)) {
        seen[val] = true;
        _allSuggestions.push(val);
      }
    });
    (inst.ips || []).forEach(function(ip) {
      if (ip && !seen[ip]) {
        seen[ip] = true;
        _allSuggestions.push(ip);
      }
    });
  });
}

function showSuggestions(q) {
  var box = document.getElementById('searchSuggestions');
  if (!box) return;
  if (!q) { box.innerHTML = ''; box.classList.remove('open'); return; }
  var lq = q.toLowerCase();
  var matches = _allSuggestions.filter(function(s) {
    return s.toLowerCase().indexOf(lq) !== -1;
  }).slice(0, 12);
  if (!matches.length) { box.innerHTML = ''; box.classList.remove('open'); return; }
  box.innerHTML = matches.map(function(s) {
    return '<div class="search-suggestion" data-val="' + esc(s) + '">' + esc(s) + '</div>';
  }).join('');
  box.classList.add('open');
}

function hideSuggestions() {
  var box = document.getElementById('searchSuggestions');
  if (box) { box.innerHTML = ''; box.classList.remove('open'); }
}

/* ── Filter + re-render ─────────────────────────────────────────────────────── */
function applyFilter(query) {
  var grid = document.getElementById('instanceGrid');
  if (!grid) return;
  var q = (query || '').toLowerCase().trim();
  var pool = window._allInstances || [];
  var filtered = q ? pool.filter(function(inst) {
    return inst.name.toLowerCase().indexOf(q) !== -1 ||
           inst.hostname.toLowerCase().indexOf(q) !== -1 ||
           inst.id.toLowerCase().indexOf(q) !== -1 ||
           (inst.ips || []).some(function(ip) { return ip.indexOf(q) !== -1; });
  }) : pool;
  grid.innerHTML = renderGrid(filtered);
}

/* ── Full grid update (called by SocketIO and DOMContentLoaded) ─────────────── */
function updateGrid(instances) {
  window._allInstances = instances;
  var count = document.getElementById('instanceCount');
  var scanEl = document.getElementById('lastScan');
  if (count) count.textContent = instances.length;
  if (scanEl) scanEl.textContent = 'Last scan: ' + new Date().toLocaleTimeString('en-US', {hour12: false}) + ' local';
  buildSuggestions(instances);
  var searchInput = document.getElementById('searchInput');
  applyFilter(searchInput ? searchInput.value : '');
}

/* ── Event delegation: expand/collapse + quick-visit ───────────────────────── */
function initGrid() {
  var grid = document.getElementById('instanceGrid');
  if (!grid) return;

  grid.addEventListener('click', function(e) {
    var visitBtn = e.target.closest('.card-visit-btn');
    if (visitBtn) {
      e.stopPropagation();
      var url = visitBtn.getAttribute('data-url');
      if (url) window.open(url, '_blank', 'noopener');
      return;
    }

    var bar = e.target.closest('.card-collapsed-bar');
    if (!bar) return;
    var card = bar.closest('.instance-card');
    if (!card) return;
    var col = card.closest('.service-col');
    var wasExpanded = card.classList.contains('expanded');

    if (col) {
      col.querySelectorAll('.instance-card.expanded').forEach(function(c) {
        c.classList.remove('expanded');
      });
    }

    if (!wasExpanded) {
      card.classList.add('expanded');
    }
  });

  var searchInput = document.getElementById('searchInput');
  var sugBox = document.getElementById('searchSuggestions');
  if (searchInput) {
    searchInput.addEventListener('input', function() {
      showSuggestions(this.value);
      applyFilter(this.value);
    });
    searchInput.addEventListener('blur', function() {
      setTimeout(hideSuggestions, 150);
    });
    searchInput.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') { hideSuggestions(); this.blur(); }
    });
  }
  if (sugBox) {
    sugBox.addEventListener('click', function(e) {
      var item = e.target.closest('.search-suggestion');
      if (!item) return;
      var val = item.getAttribute('data-val');
      if (searchInput) searchInput.value = val;
      hideSuggestions();
      applyFilter(val);
    });
  }
}

/* ── SocketIO live updates ──────────────────────────────────────────────────── */
(function() {
  if (typeof io === 'undefined') return;
  var socket = io({ transports: ['websocket', 'polling'] });
  window._socket = socket;

  socket.on('instances_update', function(instances) {
    updateGrid(instances);
    var btn = document.getElementById('scanBtn');
    if (btn) { btn.disabled = false; btn.textContent = 'Scan Now'; }
  });
})();

/* ── Bootstrap on page load ─────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', function() {
  var initial = window._initialInstances;
  if (initial && initial.length) {
    window._allInstances = initial;
    buildSuggestions(initial);
    var grid = document.getElementById('instanceGrid');
    if (grid) grid.innerHTML = renderGrid(initial);
  }
  initGrid();
});
