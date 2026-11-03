/* Nexus v2.0 — Client-side JavaScript
 * Fleet management, palette engine, socket.io, session timeout.
 */
(function () {
  'use strict';

  var _socket = null;
  var _fleetData = { nodes: [], stats: {}, last_scan: null };
  var _expandedNode = null;
  var _statusFilter = '';
  var _sessionTimer = null;
  var _countdownTimer = null;
  var _lastActivity = Date.now();

  var PALETTES = [
    { id: 'resume',   label: 'Resume'   },
    { id: 'midnight', label: 'Midnight'  },
    { id: 'amber',    label: 'Amber'     },
    { id: 'frost',    label: 'Frost'     },
    { id: 'crimson',  label: 'Crimson'   },
  ];

  var SESSION_WARN_MS  = 25 * 60 * 1000;
  var SESSION_EXPIRE_S = 300;

  // ── Helpers ────────────────────────────────────────────────────────

  function _esc(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function _$(id) { return document.getElementById(id); }

  function _json(url, opts) {
    opts = opts || {};
    var o = { headers: {} };
    if (opts.method) o.method = opts.method;
    if (opts.body !== undefined) {
      o.headers['Content-Type'] = 'application/json';
      o.body = JSON.stringify(opts.body);
    }
    return fetch(url, o).then(function (r) { return r.json(); });
  }

  function _relTime(iso) {
    if (!iso) return 'never';
    var d = new Date(iso);
    var s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 5)  return 'just now';
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function _statusDot(status) {
    var cls = 'dot';
    if (status === 'online')   cls += ' dot-online';
    else if (status === 'scanning') cls += ' dot-scanning';
    else cls += ' dot-offline';
    return '<span class="' + cls + '"></span>';
  }

  function _osBadge(os) {
    if (!os) return '';
    var label = os;
    if (os === 'darwin') label = 'macOS';
    else if (os === 'linux') label = 'Linux';
    else if (os.indexOf('win') === 0) label = 'Windows';
    return '<span class="badge badge-os">' + _esc(label) + '</span>';
  }

  // ── Palette Engine ─────────────────────────────────────────────────

  function _initPalette() {
    var sel = _$('paletteSelect');
    if (!sel) return;
    sel.innerHTML = '';
    for (var i = 0; i < PALETTES.length; i++) {
      var o = document.createElement('option');
      o.value = PALETTES[i].id;
      o.textContent = PALETTES[i].label;
      sel.appendChild(o);
    }
    try {
      var saved = localStorage.getItem('nexus-palette');
      if (saved) sel.value = saved;
    } catch (e) {}
    sel.addEventListener('change', function () {
      document.documentElement.setAttribute('data-palette', sel.value);
      try { localStorage.setItem('nexus-palette', sel.value); } catch (e) {}
    });

    var toggle = _$('themeToggle');
    var label = _$('modeLabel');
    if (!toggle) return;
    try {
      var m = localStorage.getItem('nexus-mode');
      if (m === 'dark') { toggle.checked = true; }
    } catch (e) {}
    _syncModeLabel();

    toggle.addEventListener('change', function () {
      var mode = toggle.checked ? 'dark' : 'light';
      document.documentElement.setAttribute('data-mode', mode);
      try { localStorage.setItem('nexus-mode', mode); } catch (e) {}
      _syncModeLabel();
    });
  }

  function _syncModeLabel() {
    var label = _$('modeLabel');
    var toggle = _$('themeToggle');
    if (label && toggle) label.textContent = toggle.checked ? 'Dark' : 'Light';
  }

  // ── Socket.IO ──────────────────────────────────────────────────────

  function _initSocket() {
    if (typeof io === 'undefined') return;
    _socket = io({ transports: ['websocket', 'polling'] });
    _socket.on('connect', function () {});
    _socket.on('fleet_update', function (data) {
      _fleetData = data;
      _renderFleet();
    });
  }

  // ── Fleet Rendering ────────────────────────────────────────────────

  function _renderFleet() {
    _updateStats();
    _updateLastScan();
    _renderGrid();
    _renderNetworksSidebar();
  }

  function _updateStats() {
    var s = _fleetData.stats || {};
    _setText('statTotal',    s.total    || 0);
    _setText('statOnline',   s.online   || 0);
    _setText('statOffline',  s.offline  || 0);
    _setText('statScanning', s.scanning || 0);
    _setText('statEnrolled', s.enrolled || 0);
  }

  function _setText(id, val) {
    var el = _$(id);
    if (!el) return;
    var num = el.querySelector('.stat-num');
    if (num) num.textContent = val;
  }

  function _updateLastScan() {
    var el = _$('lastScan');
    if (el) el.textContent = 'Last scan: ' + _relTime(_fleetData.last_scan);
  }

  function _renderGrid() {
    var grid = _$('fleetGrid');
    if (!grid) return;

    var nodes = _filterNodes(_fleetData.nodes || []);
    if (!nodes.length) {
      grid.innerHTML =
        '<div class="empty-state">' +
          '<div class="empty-state-icon">&#9685;</div>' +
          '<div class="empty-state-msg">No nodes match your filters.</div>' +
        '</div>';
      return;
    }

    var html = '';
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var expanded = _expandedNode === n.id;
      html += _renderNodeCard(n, expanded);
    }
    grid.innerHTML = html;

    var cards = grid.querySelectorAll('.node-card-header');
    for (var j = 0; j < cards.length; j++) {
      cards[j].addEventListener('click', _onCardClick);
    }

    var installBtns = grid.querySelectorAll('.btn-install');
    for (var k = 0; k < installBtns.length; k++) {
      installBtns[k].addEventListener('click', _onInstallClick);
    }
  }

  function _renderNodeCard(node, expanded) {
    var tools = node.tools || [];
    var addrs = node.addresses || [];

    var toolChips = '';
    for (var t = 0; t < tools.length; t++) {
      var tool = tools[t];
      var ver = tool.version ? ' v' + _esc(tool.version) : '';
      var scanCls = tool.scanning ? ' chip-scanning' : '';
      toolChips +=
        '<span class="chip chip-tool' + scanCls + '">' +
          _statusDot(tool.status) +
          _esc(tool.tool_id) + ver +
        '</span>';
    }

    var addrTags = '';
    if (addrs.length) {
      for (var a = 0; a < addrs.length; a++) {
        var addr = addrs[a];
        var laneCls = addr.lane_status === 'up' ? 'badge-up' : 'badge-down';
        addrTags += '<span class="badge ' + laneCls + '">' + _esc(addr.ip) + '</span>';
      }
    }

    var detail = '';
    if (expanded) {
      detail = _renderNodeDetail(node);
    }

    return (
      '<div class="node-card' + (expanded ? ' node-expanded' : '') + '" data-node-id="' + node.id + '">' +
        '<div class="node-card-header" data-node-id="' + node.id + '">' +
          '<div class="node-card-left">' +
            _statusDot(node.status) +
            '<strong class="node-hostname">' + _esc(node.hostname) + '</strong>' +
            '<span class="node-ip">' + _esc(node.ip_primary) + '</span>' +
            _osBadge(node.os_family) +
          '</div>' +
          '<div class="node-card-right">' +
            '<span class="node-tool-count">' + tools.length + ' tool' + (tools.length !== 1 ? 's' : '') + '</span>' +
            '<span class="node-expand-icon">' + (expanded ? '&#9650;' : '&#9660;') + '</span>' +
          '</div>' +
        '</div>' +
        '<div class="node-card-tools">' + toolChips + '</div>' +
        (addrTags ? '<div class="node-card-addrs">' + addrTags + '</div>' : '') +
        detail +
      '</div>'
    );
  }

  function _renderNodeDetail(node) {
    var tools = node.tools || [];
    var addrs = node.addresses || [];

    var toolRows = '';
    for (var t = 0; t < tools.length; t++) {
      var tool = tools[t];
      toolRows +=
        '<tr>' +
          '<td>' + _statusDot(tool.status) + ' ' + _esc(tool.tool_id) + '</td>' +
          '<td>' + (tool.version ? _esc(tool.version) : '-') + '</td>' +
          '<td>' + _esc(tool.proto || '') + '://' + _esc(node.ip_primary) + ':' + (tool.port || '') + '</td>' +
          '<td>' + _esc(tool.status) + (tool.scanning ? ' (scanning)' : '') + '</td>' +
          '<td>' +
            (tool.status === 'online' && tool.proto
              ? '<a href="' + _esc(tool.proto) + '://' + _esc(node.ip_primary) + ':' + tool.port + '" target="_blank" class="btn btn-xs btn-accent">Open</a>'
              : '') +
          '</td>' +
        '</tr>';
    }

    var addrRows = '';
    for (var a = 0; a < addrs.length; a++) {
      var addr = addrs[a];
      addrRows +=
        '<tr>' +
          '<td>' + _esc(addr.ip) + '</td>' +
          '<td>' + _esc(addr.fabric_id || '-') + '</td>' +
          '<td>' + _esc(addr.lane_status || '-') + '</td>' +
        '</tr>';
    }

    var installedIds = {};
    for (var i = 0; i < tools.length; i++) installedIds[tools[i].tool_id] = true;

    var REGISTRY = [
      'gauntlet','vantage','quiver','spectre','crucible','nessus','hitman'
    ];
    var missingBtns = '';
    for (var m = 0; m < REGISTRY.length; m++) {
      if (!installedIds[REGISTRY[m]]) {
        missingBtns +=
          '<button class="btn btn-xs btn-muted btn-install" data-tool="' + REGISTRY[m] + '">' +
            'Install ' + REGISTRY[m] +
          '</button>';
      }
    }

    return (
      '<div class="node-detail">' +
        '<h4>Tools</h4>' +
        '<table class="tbl">' +
          '<thead><tr><th>Tool</th><th>Version</th><th>Address</th><th>Status</th><th></th></tr></thead>' +
          '<tbody>' + toolRows + '</tbody>' +
        '</table>' +
        (addrRows
          ? '<h4>Network Addresses</h4>' +
            '<table class="tbl">' +
              '<thead><tr><th>IP</th><th>Fabric</th><th>Lane</th></tr></thead>' +
              '<tbody>' + addrRows + '</tbody>' +
            '</table>'
          : '') +
        (missingBtns
          ? '<h4>Install Missing</h4><div class="install-btns">' + missingBtns + '</div>'
          : '') +
      '</div>'
    );
  }

  function _onCardClick(e) {
    var nodeId = parseInt(this.getAttribute('data-node-id'), 10);
    if (_expandedNode === nodeId) _expandedNode = null;
    else _expandedNode = nodeId;
    _renderGrid();
  }

  function _onInstallClick(e) {
    e.stopPropagation();
    var tool = this.getAttribute('data-tool');
    if (!tool) return;
    this.disabled = true;
    this.textContent = 'Installing...';
    _json('/api/install-tool', { method: 'POST', body: { tool: tool } });
  }

  function _filterNodes(nodes) {
    var q = '';
    var input = _$('searchInput');
    if (input) q = input.value.toLowerCase().trim();

    var result = [];
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (_statusFilter && n.status !== _statusFilter) continue;
      if (q) {
        var match = false;
        if ((n.hostname || '').toLowerCase().indexOf(q) !== -1) match = true;
        if ((n.ip_primary || '').indexOf(q) !== -1) match = true;
        if (!match) {
          var tools = n.tools || [];
          for (var t = 0; t < tools.length; t++) {
            if ((tools[t].tool_id || '').toLowerCase().indexOf(q) !== -1) { match = true; break; }
          }
        }
        if (!match) continue;
      }
      result.push(n);
    }
    return result;
  }

  function _renderNetworksSidebar() {
    var el = _$('networkList');
    if (!el) return;
    var nodes = _fleetData.nodes || [];
    var fabricMap = {};
    for (var i = 0; i < nodes.length; i++) {
      var addrs = nodes[i].addresses || [];
      for (var a = 0; a < addrs.length; a++) {
        var fid = addrs[a].fabric_id || 'local';
        if (!fabricMap[fid]) fabricMap[fid] = { up: 0, down: 0 };
        if (addrs[a].lane_status === 'up') fabricMap[fid].up++;
        else fabricMap[fid].down++;
      }
    }

    var keys = Object.keys(fabricMap);
    if (!keys.length) {
      el.innerHTML = '<span class="sidebar-empty">No fabrics configured</span>';
      return;
    }
    var html = '';
    for (var k = 0; k < keys.length; k++) {
      var f = fabricMap[keys[k]];
      html +=
        '<div class="sidebar-network-item">' +
          '<span class="sidebar-network-name">' + _esc(keys[k]) + '</span>' +
          '<span class="sidebar-network-stat">' +
            '<span class="dot dot-online"></span>' + f.up +
            (f.down ? ' <span class="dot dot-offline"></span>' + f.down : '') +
          '</span>' +
        '</div>';
    }
    el.innerHTML = html;
  }

  // ── Filter chips ───────────────────────────────────────────────────

  function _initFilters() {
    var chipBox = _$('filterChips');
    if (!chipBox) return;
    var statuses = ['online', 'offline', 'scanning'];
    var html = '';
    for (var i = 0; i < statuses.length; i++) {
      html += '<button class="chip chip-filter" data-status="' + statuses[i] + '">' + statuses[i] + '</button>';
    }
    chipBox.innerHTML = html;
    var chips = chipBox.querySelectorAll('.chip-filter');
    for (var j = 0; j < chips.length; j++) {
      chips[j].addEventListener('click', function () {
        var st = this.getAttribute('data-status');
        if (_statusFilter === st) {
          _statusFilter = '';
          this.classList.remove('chip-active');
        } else {
          _statusFilter = st;
          var all = chipBox.querySelectorAll('.chip-filter');
          for (var k = 0; k < all.length; k++) all[k].classList.remove('chip-active');
          this.classList.add('chip-active');
        }
        _renderGrid();
      });
    }

    var searchInput = _$('searchInput');
    if (searchInput) {
      searchInput.addEventListener('input', function () { _renderGrid(); });
    }
  }

  // ── Settings ───────────────────────────────────────────────────────

  function _loadSettings() {
    _json('/api/settings').then(function (d) {
      var settings = d.settings || {};
      var inputs = document.querySelectorAll('.form-input[data-key]');
      for (var i = 0; i < inputs.length; i++) {
        var key = inputs[i].getAttribute('data-key');
        if (settings[key] !== undefined) inputs[i].value = settings[key];
      }
    });
  }

  function _saveSettings(sectionId) {
    var section = _$(sectionId);
    if (!section) return;
    var inputs = section.querySelectorAll('.form-input[data-key]');
    var payload = {};
    for (var i = 0; i < inputs.length; i++) {
      payload[inputs[i].getAttribute('data-key')] = inputs[i].value;
    }
    _json('/api/settings', { method: 'POST', body: { settings: payload } })
      .then(function () {
        var btn = section.closest('.card');
        if (btn) {
          var b = btn.querySelector('.btn-accent');
          if (b) { b.textContent = 'Saved!'; setTimeout(function () { b.textContent = 'Save'; }, 1500); }
        }
      });
  }

  // ── Audit ──────────────────────────────────────────────────────────

  function _loadAudit() {
    var tbody = _$('auditBody');
    if (!tbody) return;
    _json('/api/audit').then(function (d) {
      var entries = d.entries || [];
      if (!entries.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;opacity:0.5">No audit entries</td></tr>';
        return;
      }
      var html = '';
      for (var i = 0; i < entries.length; i++) {
        var e = entries[i];
        html +=
          '<tr>' +
            '<td style="white-space:nowrap;font-size:0.78rem">' + _esc(e.timestamp || '') + '</td>' +
            '<td>' + _esc(e.username || '') + '</td>' +
            '<td>' + _esc(e.action || '') + '</td>' +
            '<td style="font-size:0.78rem">' + _esc(e.detail || '') + '</td>' +
          '</tr>';
      }
      tbody.innerHTML = html;
    });
  }

  // ── Fabrics ────────────────────────────────────────────────────────

  function _loadFabrics() {
    var el = _$('fabricList');
    if (!el) return;
    _json('/api/fabrics').then(function (d) {
      var fabrics = d.fabrics || [];
      if (!fabrics.length) {
        el.innerHTML = '<div class="empty-state-inline">No fabrics configured. Add a network to begin fleet discovery.</div>';
        return;
      }
      var html = '';
      for (var i = 0; i < fabrics.length; i++) {
        var f = fabrics[i];
        var subnets = '';
        try { subnets = JSON.parse(f.subnets || '[]').join(', '); } catch (e) { subnets = f.subnets || ''; }
        html +=
          '<div class="fabric-row" data-fabric-id="' + f.id + '">' +
            '<div class="fabric-info">' +
              '<strong>' + _esc(f.name) + '</strong>' +
              '<span class="badge badge-os">' + _esc(f.type || '') + '</span>' +
              (f.enabled ? '<span class="badge badge-up">enabled</span>' : '<span class="badge badge-down">disabled</span>') +
              '<span class="fabric-subnets">' + _esc(subnets) + '</span>' +
            '</div>' +
            '<div class="fabric-actions">' +
              '<button class="btn btn-xs btn-danger btn-fabric-del" data-fid="' + f.id + '">Delete</button>' +
            '</div>' +
          '</div>';
      }
      el.innerHTML = html;

      var delBtns = el.querySelectorAll('.btn-fabric-del');
      for (var j = 0; j < delBtns.length; j++) {
        delBtns[j].addEventListener('click', function () {
          var fid = this.getAttribute('data-fid');
          _json('/api/fabrics/' + fid, { method: 'DELETE' }).then(function () { _loadFabrics(); });
        });
      }
    });
  }

  function _addFabric() {
    var el = _$('fabricList');
    if (!el) return;
    if (_$('fabricAddForm')) return;

    var form = document.createElement('div');
    form.id = 'fabricAddForm';
    form.className = 'fabric-add-form';
    form.innerHTML =
      '<div class="form-row"><label>Name</label><input type="text" class="form-input" id="fabName" placeholder="e.g. Lab VPN"></div>' +
      '<div class="form-row"><label>Type</label>' +
        '<select class="form-input" id="fabType">' +
          '<option value="vpn">VPN</option>' +
          '<option value="overlay">Overlay</option>' +
          '<option value="lan">LAN</option>' +
          '<option value="cloud">Cloud</option>' +
        '</select></div>' +
      '<div class="form-row"><label>Subnets (one per line)</label><textarea class="form-input" id="fabSubnets" rows="3" placeholder="10.0.0.0/24"></textarea></div>' +
      '<div class="form-row" style="display:flex;gap:8px">' +
        '<button class="btn btn-sm btn-accent" id="fabSave">Save</button>' +
        '<button class="btn btn-sm btn-muted" id="fabCancel">Cancel</button>' +
      '</div>';
    el.prepend(form);

    _$('fabCancel').addEventListener('click', function () { form.remove(); });
    _$('fabSave').addEventListener('click', function () {
      var name = (_$('fabName').value || '').trim();
      var type = _$('fabType').value;
      var raw = (_$('fabSubnets').value || '').trim();
      var subnets = raw.split(/[\n,]+/).map(function (s) { return s.trim(); }).filter(Boolean);
      if (!name) return;
      _json('/api/fabrics', { method: 'POST', body: { name: name, type: type, subnets: subnets } })
        .then(function () { form.remove(); _loadFabrics(); });
    });
  }

  // ── Session Timeout ────────────────────────────────────────────────

  function _initSessionTimeout() {
    _resetSessionTimer();
    var events = ['click', 'keydown', 'mousemove', 'scroll', 'touchstart'];
    for (var i = 0; i < events.length; i++) {
      document.addEventListener(events[i], _onActivity, { passive: true });
    }
  }

  function _onActivity() {
    _lastActivity = Date.now();
    var overlay = _$('sessionTimeoutOverlay');
    if (overlay && overlay.classList.contains('visible')) return;
    _resetSessionTimer();
  }

  function _resetSessionTimer() {
    clearTimeout(_sessionTimer);
    clearInterval(_countdownTimer);
    _sessionTimer = setTimeout(_showTimeoutWarning, SESSION_WARN_MS);
  }

  function _showTimeoutWarning() {
    var overlay = _$('sessionTimeoutOverlay');
    if (overlay) overlay.classList.add('visible');
    var remaining = SESSION_EXPIRE_S;
    _updateCountdown(remaining);
    _countdownTimer = setInterval(function () {
      remaining--;
      if (remaining <= 0) {
        clearInterval(_countdownTimer);
        window.location.href = '/logout';
        return;
      }
      _updateCountdown(remaining);
    }, 1000);
  }

  function _updateCountdown(seconds) {
    var el = _$('sessionTimeoutCountdown');
    if (!el) return;
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    el.textContent = m + ':' + (s < 10 ? '0' : '') + s;
  }

  function _sessionContinue() {
    clearInterval(_countdownTimer);
    var overlay = _$('sessionTimeoutOverlay');
    if (overlay) overlay.classList.remove('visible');
    _json('/api/session_ping', { method: 'POST' });
    _resetSessionTimer();
  }

  // ── Scan trigger ───────────────────────────────────────────────────

  function _triggerScan() {
    var btn = _$('scanBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Scanning...'; }
    _json('/api/scan', { method: 'POST' }).then(function () {
      setTimeout(function () {
        if (btn) { btn.disabled = false; btn.textContent = 'Scan Now'; }
      }, 3000);
    });
  }

  // ── Init ───────────────────────────────────────────────────────────

  function _init() {
    _initPalette();
    _initSocket();
    _initFilters();
    _initSessionTimeout();

    _json('/api/fleet').then(function (d) {
      _fleetData = d;
      _renderFleet();
    });
  }

  document.addEventListener('DOMContentLoaded', _init);

  // ── Public API ─────────────────────────────────────────────────────

  window.NX = {
    triggerScan:     _triggerScan,
    saveSettings:    _saveSettings,
    loadSettings:    _loadSettings,
    loadAudit:       _loadAudit,
    loadFabrics:     _loadFabrics,
    addFabric:       _addFabric,
    sessionContinue: _sessionContinue,
  };

})();
