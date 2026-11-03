/* Nexus v2.1 — Client-side JavaScript
 * Fleet management, fabric wizard, palette engine, socket.io, session timeout.
 */
(function () {
  'use strict';

  var _socket = null;
  var _fleetData = { nodes: [], stats: {}, last_scan: null, fabrics: [] };
  var _expandedNode = null;
  var _statusFilter = '';
  var _fabricFilter = '';
  var _sessionTimer = null;
  var _countdownTimer = null;
  var _lastActivity = Date.now();

  // Wizard state
  var _wizardStep = 0;
  var _wizardTypes = [];
  var _wizardSelectedType = '';
  var _wizardConfig = {};

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

  function _laneDot(status) {
    if (status === 'up') return '<span class="lane-dot lane-up"></span>';
    if (status === 'down') return '<span class="lane-dot lane-down"></span>';
    return '<span class="lane-dot lane-unknown"></span>';
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
    _socket.on('fabric_health', function (data) {
      if (!data) return;
      var fabrics = _fleetData.fabrics || [];
      for (var i = 0; i < fabrics.length; i++) {
        if (fabrics[i].id === data.fabric_id) {
          fabrics[i].status = data.status;
          fabrics[i].health_detail = data.detail;
          break;
        }
      }
      _renderNetworksSidebar();
    });
  }

  // ── Fleet Rendering ────────────────────────────────────────────────

  function _renderFleet() {
    _updateStats();
    _updateLastScan();
    _updateBadge();
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

  function _updateBadge() {
    var badge = _$('badgeFleet');
    if (badge) badge.textContent = (_fleetData.stats || {}).online || 0;
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
          _esc(tool.tool_name || tool.tool_id) + ver +
        '</span>';
    }

    // Fabric lane indicators
    var laneHtml = '';
    if (addrs.length) {
      for (var a = 0; a < addrs.length; a++) {
        var addr = addrs[a];
        var fabName = addr.fabric_name || ('Fabric ' + (addr.fabric_id || '?'));
        laneHtml +=
          '<span class="lane-tag">' +
            _laneDot(addr.lane_status) +
            '<span class="lane-name">' + _esc(fabName) + '</span>' +
            '<span class="lane-ip">' + _esc(addr.address) + '</span>' +
          '</span>';
      }
    }

    // Role badge
    var roleBadge = '';
    if (node.role) {
      roleBadge = '<span class="badge badge-role">' + _esc(node.role) + '</span>';
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
            '<strong class="node-hostname">' + _esc(node.display_name || node.hostname) + '</strong>' +
            '<span class="node-ip">' + _esc(node.ip_primary) + '</span>' +
            _osBadge(node.os_family) +
            roleBadge +
          '</div>' +
          '<div class="node-card-right">' +
            '<span class="node-tool-count">' + tools.length + ' tool' + (tools.length !== 1 ? 's' : '') + '</span>' +
            '<span class="node-expand-icon">' + (expanded ? '&#9650;' : '&#9660;') + '</span>' +
          '</div>' +
        '</div>' +
        (laneHtml ? '<div class="node-card-lanes">' + laneHtml + '</div>' : '') +
        '<div class="node-card-tools">' + toolChips + '</div>' +
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
          '<td>' + _statusDot(tool.status) + ' ' + _esc(tool.tool_name || tool.tool_id) + '</td>' +
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
      var fabName = addr.fabric_name || ('Fabric ' + (addr.fabric_id || '?'));
      addrRows +=
        '<tr>' +
          '<td>' + _esc(addr.address) + '</td>' +
          '<td>' + _esc(fabName) + '</td>' +
          '<td>' + _esc(addr.fabric_type || '-') + '</td>' +
          '<td>' + _laneDot(addr.lane_status) + ' ' + _esc(addr.lane_status || 'unknown') + '</td>' +
          '<td>' + _relTime(addr.last_seen) + '</td>' +
        '</tr>';
    }

    var installedIds = {};
    for (var i = 0; i < tools.length; i++) installedIds[tools[i].tool_name || tools[i].tool_id] = true;

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
              '<thead><tr><th>Address</th><th>Fabric</th><th>Type</th><th>Lane</th><th>Last Seen</th></tr></thead>' +
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

      // Fabric filter
      if (_fabricFilter) {
        var onFab = false;
        var addrs = n.addresses || [];
        for (var a = 0; a < addrs.length; a++) {
          if (String(addrs[a].fabric_id) === _fabricFilter ||
              (addrs[a].fabric_name || '').toLowerCase() === _fabricFilter.toLowerCase()) {
            onFab = true;
            break;
          }
        }
        if (!onFab) continue;
      }

      if (q) {
        var match = false;
        if ((n.hostname || '').toLowerCase().indexOf(q) !== -1) match = true;
        if ((n.display_name || '').toLowerCase().indexOf(q) !== -1) match = true;
        if ((n.ip_primary || '').indexOf(q) !== -1) match = true;
        if (!match) {
          var tools = n.tools || [];
          for (var t = 0; t < tools.length; t++) {
            if ((tools[t].tool_name || tools[t].tool_id || '').toLowerCase().indexOf(q) !== -1) { match = true; break; }
          }
        }
        if (!match) {
          var addresses = n.addresses || [];
          for (var b = 0; b < addresses.length; b++) {
            if ((addresses[b].fabric_name || '').toLowerCase().indexOf(q) !== -1) { match = true; break; }
            if ((addresses[b].address || '').indexOf(q) !== -1) { match = true; break; }
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
    var fabrics = _fleetData.fabrics || [];

    if (!fabrics.length) {
      el.innerHTML = '<span class="sidebar-empty">No fabrics configured</span>';
      return;
    }
    var html = '';
    for (var i = 0; i < fabrics.length; i++) {
      var f = fabrics[i];
      var dotCls = 'net-dot';
      if (f.status === 'healthy') dotCls += ' up';
      else if (f.status === 'degraded') dotCls += ' warn';
      else if (f.status === 'down') dotCls += ' down';
      html +=
        '<div class="net-item" data-fid="' + f.id + '">' +
          '<span class="' + dotCls + '"></span>' +
          '<span class="net-name">' + _esc(f.name) + '</span>' +
          '<span class="net-count">' + (f.node_count || 0) + '</span>' +
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
            '<td style="white-space:nowrap;font-size:0.78rem">' + _esc(e.timestamp || e.ts || '') + '</td>' +
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
        var statusCls = f.status === 'healthy' ? 'badge-up' : (f.status === 'down' ? 'badge-down' : 'badge-os');
        html +=
          '<div class="fabric-row" data-fabric-id="' + f.id + '">' +
            '<div class="fabric-info">' +
              '<strong>' + _esc(f.name) + '</strong>' +
              '<span class="badge badge-os">' + _esc(f.fabric_type || '') + '</span>' +
              '<span class="badge ' + statusCls + '">' + _esc(f.status || 'unknown') + '</span>' +
              (f.enabled ? '' : '<span class="badge badge-down">disabled</span>') +
              '<span class="fabric-subnets">' + _esc(subnets) + '</span>' +
            '</div>' +
            '<div class="fabric-actions">' +
              '<button class="btn btn-xs btn-muted btn-fabric-toggle" data-fid="' + f.id + '" data-enabled="' + (f.enabled ? '1' : '0') + '">' +
                (f.enabled ? 'Disable' : 'Enable') +
              '</button>' +
              '<button class="btn btn-xs btn-muted btn-fabric-health" data-fid="' + f.id + '">Health</button>' +
              '<button class="btn btn-xs btn-danger btn-fabric-del" data-fid="' + f.id + '">Delete</button>' +
            '</div>' +
          '</div>';
      }
      el.innerHTML = html;

      el.querySelectorAll('.btn-fabric-del').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var fid = this.getAttribute('data-fid');
          if (!confirm('Delete this fabric? Node addresses on it will be removed.')) return;
          _json('/api/fabrics/' + fid, { method: 'DELETE' }).then(function () { _loadFabrics(); });
        });
      });

      el.querySelectorAll('.btn-fabric-toggle').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var fid = this.getAttribute('data-fid');
          _json('/api/fabrics/' + fid + '/toggle', { method: 'POST' }).then(function () { _loadFabrics(); });
        });
      });

      el.querySelectorAll('.btn-fabric-health').forEach(function (btn) {
        btn.addEventListener('click', function () {
          var fid = this.getAttribute('data-fid');
          this.textContent = '...';
          var self = this;
          _json('/api/fabrics/' + fid + '/health', { method: 'POST' }).then(function (d) {
            self.textContent = d.status || 'done';
            setTimeout(function () { self.textContent = 'Health'; }, 2000);
            _loadFabrics();
          });
        });
      });
    });
  }

  // ── Fabric Wizard ──────────────────────────────────────────────────

  function _openFabricWizard() {
    _wizardStep = 1;
    _wizardSelectedType = '';
    _wizardConfig = {};
    _json('/api/fabric-types').then(function (d) {
      _wizardTypes = d.types || [];
      _renderWizardStep1();
      _$('fabricWizard').classList.add('show');
    });
  }

  function _closeFabricWizard() {
    _$('fabricWizard').classList.remove('show');
    _wizardStep = 0;
  }

  function _renderWizardStep1() {
    _wizardStep = 1;
    var title = _$('wizardTitle');
    if (title) title.textContent = 'Add Network — Choose Type';
    var next = _$('wizardNext');
    if (next) { next.textContent = 'Next'; next.disabled = true; }

    var body = _$('wizardBody');
    if (!body) return;

    var categories = {};
    for (var i = 0; i < _wizardTypes.length; i++) {
      var t = _wizardTypes[i];
      var cat = t.category || 'other';
      if (!categories[cat]) categories[cat] = [];
      categories[cat].push(t);
    }

    var catLabels = {
      'vpn': 'VPN / Tunnel',
      'overlay': 'Overlay / Mesh',
      'cloud': 'Cloud VPC',
      'direct': 'Direct / Local'
    };
    var catOrder = ['vpn', 'overlay', 'cloud', 'direct'];

    var html = '<div class="wizard-type-list">';
    for (var c = 0; c < catOrder.length; c++) {
      var key = catOrder[c];
      var types = categories[key];
      if (!types || !types.length) continue;
      html += '<div class="wizard-cat-label">' + _esc(catLabels[key] || key) + '</div>';
      for (var j = 0; j < types.length; j++) {
        var tp = types[j];
        html +=
          '<label class="wizard-type-option" data-type="' + _esc(tp.type_id) + '">' +
            '<input type="radio" name="fabric_type" value="' + _esc(tp.type_id) + '">' +
            '<span class="wizard-type-name">' + _esc(tp.display_name) + '</span>' +
          '</label>';
      }
    }
    html += '</div>';
    body.innerHTML = html;

    body.querySelectorAll('input[name="fabric_type"]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        _wizardSelectedType = this.value;
        if (next) next.disabled = false;
        body.querySelectorAll('.wizard-type-option').forEach(function (opt) {
          opt.classList.toggle('selected', opt.getAttribute('data-type') === _wizardSelectedType);
        });
      });
    });
  }

  function _renderWizardStep2() {
    _wizardStep = 2;
    var title = _$('wizardTitle');
    var typeInfo = null;
    for (var i = 0; i < _wizardTypes.length; i++) {
      if (_wizardTypes[i].type_id === _wizardSelectedType) { typeInfo = _wizardTypes[i]; break; }
    }
    if (!typeInfo) return;

    if (title) title.textContent = 'Configure: ' + (typeInfo.display_name || _wizardSelectedType);
    var next = _$('wizardNext');
    if (next) next.textContent = 'Test & Save';

    var body = _$('wizardBody');
    if (!body) return;

    var fields = typeInfo.fields || [];
    var html = '<div class="wizard-form">';

    // Name field (always)
    html +=
      '<div class="wizard-field">' +
        '<label>Name <span class="wizard-req">*</span></label>' +
        '<input type="text" class="form-input wizard-input" data-field="name" placeholder="e.g. Primary VLAN0" style="width:100%;text-align:left">' +
      '</div>';

    // Subnets field (always)
    html +=
      '<div class="wizard-field">' +
        '<label>Subnets <span class="wizard-req">*</span></label>' +
        '<textarea class="form-input wizard-input" data-field="subnets" rows="3" ' +
          'placeholder="One CIDR per line\ne.g. 10.233.160.0/22" style="width:100%;text-align:left;resize:vertical"></textarea>' +
      '</div>';

    // Priority
    html +=
      '<div class="wizard-field">' +
        '<label>Priority</label>' +
        '<input type="number" class="form-input wizard-input" data-field="priority" value="100" min="1" max="999" style="width:80px">' +
        '<span class="wizard-hint">Lower = preferred for dispatch</span>' +
      '</div>';

    // Type-specific fields
    for (var f = 0; f < fields.length; f++) {
      var fd = fields[f];
      var req = fd.required ? '<span class="wizard-req">*</span>' : '';
      var inputType = fd.secret ? 'password' : (fd.type === 'number' ? 'number' : 'text');

      if (fd.type === 'select' && fd.options) {
        html += '<div class="wizard-field"><label>' + _esc(fd.label) + ' ' + req + '</label><select class="form-input wizard-input" data-field="' + _esc(fd.name) + '" style="width:100%">';
        for (var o = 0; o < fd.options.length; o++) {
          html += '<option value="' + _esc(fd.options[o]) + '">' + _esc(fd.options[o]) + '</option>';
        }
        html += '</select></div>';
      } else if (fd.type === 'textarea') {
        html += '<div class="wizard-field"><label>' + _esc(fd.label) + ' ' + req + '</label>' +
          '<textarea class="form-input wizard-input" data-field="' + _esc(fd.name) + '" rows="3" ' +
          'placeholder="' + _esc(fd.hint || '') + '" style="width:100%;text-align:left;resize:vertical"></textarea></div>';
      } else {
        html += '<div class="wizard-field"><label>' + _esc(fd.label) + ' ' + req + '</label>' +
          '<input type="' + inputType + '" class="form-input wizard-input" data-field="' + _esc(fd.name) + '" ' +
          'placeholder="' + _esc(fd.hint || '') + '"' +
          (fd.default_val ? ' value="' + _esc(fd.default_val) + '"' : '') +
          ' style="width:100%;text-align:left"></div>';
      }
    }
    html += '</div>';
    body.innerHTML = html;
  }

  function _renderWizardStep3(result) {
    _wizardStep = 3;
    var title = _$('wizardTitle');
    if (title) title.textContent = result.ok ? 'Network Added' : 'Connection Test';
    var next = _$('wizardNext');
    if (next) {
      if (result.ok) {
        next.textContent = 'Done';
        next.onclick = function () { _closeFabricWizard(); _loadFabrics(); };
      } else {
        next.textContent = 'Back';
        next.onclick = function () { _renderWizardStep2(); next.onclick = function () { _wizardNext(); }; };
      }
    }

    var body = _$('wizardBody');
    if (!body) return;
    var checks = result.checks || [];
    var html = '<div class="wizard-results">';
    for (var i = 0; i < checks.length; i++) {
      var c = checks[i];
      var icon = c.passed ? '<span class="wiz-check-pass">&#10003;</span>' : '<span class="wiz-check-fail">&#10007;</span>';
      html += '<div class="wiz-check-row">' + icon + ' ' + _esc(c.label) +
        (c.detail ? '<span class="wiz-check-detail">' + _esc(c.detail) + '</span>' : '') +
        '</div>';
    }
    if (result.ok) {
      html += '<div class="wiz-check-ok">Network added successfully.</div>';
    } else {
      html += '<div class="wiz-check-err">' + _esc(result.error || 'Connection test failed.') + '</div>';
    }
    html += '</div>';
    body.innerHTML = html;
  }

  function _wizardNext() {
    if (_wizardStep === 1) {
      if (!_wizardSelectedType) return;
      _renderWizardStep2();
    } else if (_wizardStep === 2) {
      var body = _$('wizardBody');
      if (!body) return;
      var inputs = body.querySelectorAll('.wizard-input');
      var config = {};
      var subnets = [];
      var name = '';
      var priority = 100;

      for (var i = 0; i < inputs.length; i++) {
        var field = inputs[i].getAttribute('data-field');
        var val = (inputs[i].value || '').trim();
        if (field === 'name') { name = val; continue; }
        if (field === 'subnets') {
          subnets = val.split(/[\n,]+/).map(function (s) { return s.trim(); }).filter(Boolean);
          continue;
        }
        if (field === 'priority') { priority = parseInt(val, 10) || 100; continue; }
        if (val) config[field] = val;
      }

      if (!name) { alert('Name is required.'); return; }
      if (!subnets.length) { alert('At least one subnet is required.'); return; }

      var btn = _$('wizardNext');
      if (btn) { btn.disabled = true; btn.textContent = 'Testing...'; }

      _json('/api/fabrics', {
        method: 'POST',
        body: {
          name: name,
          fabric_type: _wizardSelectedType,
          config: config,
          subnets: subnets,
          priority: priority
        }
      }).then(function (d) {
        if (btn) { btn.disabled = false; }
        _renderWizardStep3(d);
      }).catch(function () {
        if (btn) { btn.disabled = false; btn.textContent = 'Test & Save'; }
      });
    }
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
    _json('/api/fleet/scan', { method: 'POST' }).then(function () {
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
    triggerScan:        _triggerScan,
    saveSettings:       _saveSettings,
    loadSettings:       _loadSettings,
    loadAudit:          _loadAudit,
    loadFabrics:        _loadFabrics,
    openFabricWizard:   _openFabricWizard,
    closeFabricWizard:  _closeFabricWizard,
    wizardNext:         _wizardNext,
    sessionContinue:    _sessionContinue,
  };

})();
