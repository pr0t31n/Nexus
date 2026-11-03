/* Nexus v2.2 — Terminal + Tunnels client
 * xterm.js SSH sessions, SOCKS5/port-forward tunnel management.
 */
(function () {
  'use strict';

  var MAX_TABS = 4;
  var _terms = {};          // session_id → {term, fitAddon, container, tab, hostname, ip, via, fabric, connected_at}
  var _activeSession = null;
  var _tunnels = [];
  var _drawerOpen = false;
  var _socket = null;
  var _nodes = [];           // cached online nodes
  var _credentials = {};     // node_id → [{id, label, username}]

  // ── Helpers ────────────────────────────────────────────────────────

  function _$(id) { return document.getElementById(id); }

  function _esc(s) {
    if (!s) return '';
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

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

  function _sessionCount() {
    return Object.keys(_terms).length;
  }

  function _updateSessionBadge() {
    var n = _sessionCount();
    var el = _$('termSessionCount');
    if (el) el.textContent = n + (n === 1 ? ' session' : ' sessions');
  }

  function _formatDuration(ms) {
    var s = Math.floor(ms / 1000);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    s = s % 60;
    if (h > 0) return h + ':' + (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  // ── Terminal theme (reads CSS custom properties) ──────────────────

  function _getTermTheme() {
    var cs = getComputedStyle(document.documentElement);
    return {
      background:          cs.getPropertyValue('--bg-0').trim() || '#1a1e17',
      foreground:          cs.getPropertyValue('--fg-0').trim() || '#d8ddd2',
      cursor:              cs.getPropertyValue('--accent').trim() || '#7a9a4e',
      cursorAccent:        cs.getPropertyValue('--bg-0').trim() || '#1a1e17',
      selectionBackground: 'rgba(255,255,255,0.15)',
      selectionForeground: cs.getPropertyValue('--fg-0').trim() || '#d8ddd2',
      black:   '#1a1e17',
      red:     '#c0392b',
      green:   '#5cb85c',
      yellow:  '#c8a23c',
      blue:    '#3b82f6',
      magenta: '#9b59b6',
      cyan:    '#1abc9c',
      white:   '#d8ddd2',
      brightBlack:   '#697062',
      brightRed:     '#e74c3c',
      brightGreen:   '#6abf4b',
      brightYellow:  '#e8a83a',
      brightBlue:    '#60a5fa',
      brightMagenta: '#bb77d9',
      brightCyan:    '#48d1cc',
      brightWhite:   '#f0f4ec',
    };
  }

  // ── Connect modal ─────────────────────────────────────────────────

  function _openConnectModal() {
    if (_sessionCount() >= MAX_TABS) {
      alert('Maximum ' + MAX_TABS + ' sessions allowed. Close a tab first.');
      return;
    }

    var body = _$('termConnectBody');
    body.innerHTML = '<div style="text-align:center;color:var(--fg-2);padding:16px">Loading nodes…</div>';
    _$('termConnectModal').classList.add('show');

    _json('/api/fleet/nodes?status=online').then(function (d) {
      _nodes = (d.nodes || d || []);
      _renderConnectForm();
    }).catch(function () {
      body.innerHTML = '<div style="color:var(--danger);padding:8px">Failed to load fleet nodes.</div>';
    });
  }

  function _renderConnectForm() {
    var body = _$('termConnectBody');
    if (!_nodes.length) {
      body.innerHTML = '<div style="color:var(--fg-2);padding:12px">No online nodes found. Run a fleet scan first.</div>';
      return;
    }

    var html = '<div class="form-row"><label>Node</label><select class="form-input" id="termNodeSelect">';
    for (var i = 0; i < _nodes.length; i++) {
      var n = _nodes[i];
      var label = _esc(n.hostname || n.ip_primary || n.ip || 'Node #' + n.id);
      var extra = (n.ip_primary || n.ip) ? ' (' + _esc(n.ip_primary || n.ip) + ')' : '';
      html += '<option value="' + n.id + '">' + label + extra + '</option>';
    }
    html += '</select></div>';

    html += '<div class="form-row"><label>Credentials</label>';
    html += '<select class="form-input" id="termCredSelect">';
    html += '<option value="">Loading…</option>';
    html += '</select></div>';

    html += '<div id="termInlineCred" style="display:none">';
    html += '<div class="form-row"><label>Username</label>';
    html += '<input type="text" class="form-input" id="termInlineUser" placeholder="root"></div>';
    html += '<div class="form-row"><label>Password / Key passphrase</label>';
    html += '<input type="password" class="form-input" id="termInlinePass" placeholder="(optional)"></div>';
    html += '<div class="form-row"><label>Private key (paste PEM)</label>';
    html += '<textarea class="form-input" id="termInlineKey" rows="3" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></div>';
    html += '</div>';

    body.innerHTML = html;

    var sel = _$('termNodeSelect');
    sel.addEventListener('change', function () { _loadCredentials(sel.value); });
    _loadCredentials(sel.value);
  }

  function _loadCredentials(nodeId) {
    var sel = _$('termCredSelect');
    var inline = _$('termInlineCred');
    if (!sel) return;

    sel.innerHTML = '<option value="">Loading…</option>';
    if (inline) inline.style.display = 'none';

    _json('/api/credentials/' + nodeId).then(function (d) {
      var creds = d.credentials || [];
      _credentials[nodeId] = creds;
      sel.innerHTML = '';

      for (var i = 0; i < creds.length; i++) {
        var c = creds[i];
        var lbl = _esc(c.label || c.username || 'Credential #' + c.id);
        sel.innerHTML += '<option value="' + c.id + '">' + lbl + ' (' + _esc(c.username) + ')</option>';
      }
      sel.innerHTML += '<option value="__new__">+ Enter new credentials</option>';

      sel.addEventListener('change', function () {
        if (inline) inline.style.display = sel.value === '__new__' ? 'block' : 'none';
      });
    }).catch(function () {
      sel.innerHTML = '<option value="__new__">+ Enter new credentials</option>';
      if (inline) inline.style.display = 'block';
    });
  }

  function _closeTermConnect() {
    _$('termConnectModal').classList.remove('show');
  }

  // ── Connect to node ───────────────────────────────────────────────

  function _connect() {
    var nodeSelect = _$('termNodeSelect');
    var credSelect = _$('termCredSelect');
    if (!nodeSelect) return;

    var nodeId = parseInt(nodeSelect.value, 10);
    var credId = credSelect ? credSelect.value : '';
    var payload = { node_id: nodeId, sid: _socket ? _socket.id : '' };

    if (credId === '__new__') {
      var user = (_$('termInlineUser') || {}).value || '';
      var pass = (_$('termInlinePass') || {}).value || '';
      var key  = (_$('termInlineKey')  || {}).value || '';
      if (!user) { alert('Username is required.'); return; }
      payload.inline_credential = { username: user, password: pass, private_key: key };
    } else if (credId) {
      payload.credential_id = parseInt(credId, 10);
    }

    var btn = _$('termConnectBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Connecting…'; }

    _json('/api/terminal/connect', { method: 'POST', body: payload }).then(function (d) {
      if (btn) { btn.disabled = false; btn.textContent = 'Connect'; }

      if (!d.ok) {
        alert(d.error || 'Connection failed.');
        return;
      }

      _closeTermConnect();
      _createSession(d.session_id, {
        hostname: d.hostname || '',
        ip:       d.ip || '',
        via:      d.via || 'direct',
        fabric:   d.fabric_name || '',
      });
    }).catch(function (err) {
      if (btn) { btn.disabled = false; btn.textContent = 'Connect'; }
      alert('Connection error: ' + (err.message || err));
    });
  }

  // ── Session management ────────────────────────────────────────────

  function _createSession(sessionId, meta) {
    var viewport = _$('termViewport');
    var empty = _$('termEmpty');
    if (empty) empty.style.display = 'none';

    var container = document.createElement('div');
    container.className = 'term-container';
    container.id = 'term-' + sessionId;
    container.style.display = 'none';
    viewport.appendChild(container);

    var term = new Terminal({
      theme:          _getTermTheme(),
      fontSize:       14,
      fontFamily:     "'SF Mono', 'Fira Code', 'Cascadia Code', ui-monospace, monospace",
      cursorBlink:    true,
      cursorStyle:    'bar',
      allowProposedApi: true,
      scrollback:     5000,
      convertEol:     true,
    });

    var fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(container);

    requestAnimationFrame(function () {
      fitAddon.fit();
      _socket.emit('term_resize', {
        session_id: sessionId,
        cols: term.cols,
        rows: term.rows,
      });
    });

    term.onData(function (data) {
      _socket.emit('term_input', { session_id: sessionId, data: data });
    });

    term.onResize(function (size) {
      _socket.emit('term_resize', {
        session_id: sessionId,
        cols: size.cols,
        rows: size.rows,
      });
    });

    var tab = _createTab(sessionId, meta.hostname || meta.ip || sessionId);

    _terms[sessionId] = {
      term:         term,
      fitAddon:     fitAddon,
      container:    container,
      tab:          tab,
      hostname:     meta.hostname,
      ip:           meta.ip,
      via:          meta.via,
      fabric:       meta.fabric,
      connected_at: Date.now(),
    };

    _activateTab(sessionId);
    _updateSessionBadge();
  }

  function _destroySession(sessionId) {
    var entry = _terms[sessionId];
    if (!entry) return;

    entry.term.dispose();
    if (entry.container && entry.container.parentNode) {
      entry.container.parentNode.removeChild(entry.container);
    }
    if (entry.tab && entry.tab.parentNode) {
      entry.tab.parentNode.removeChild(entry.tab);
    }

    delete _terms[sessionId];

    if (_activeSession === sessionId) {
      _activeSession = null;
      var ids = Object.keys(_terms);
      if (ids.length > 0) {
        _activateTab(ids[ids.length - 1]);
      } else {
        var empty = _$('termEmpty');
        if (empty) empty.style.display = '';
        var bar = _$('termRouteBar');
        if (bar) bar.hidden = true;
      }
    }

    _updateSessionBadge();
  }

  function _disconnect(sessionId) {
    _json('/api/terminal/disconnect', { method: 'POST', body: { session_id: sessionId } })
      .catch(function () { /* ignore, server may already be gone */ });
    _destroySession(sessionId);
  }

  // ── Tab management ────────────────────────────────────────────────

  function _createTab(sessionId, label) {
    var tabs = _$('termTabs');
    var newBtn = _$('termNewBtn');

    var tab = document.createElement('button');
    tab.className = 'term-tab';
    tab.setAttribute('data-session', sessionId);

    var span = document.createElement('span');
    span.className = 'term-tab-label';
    span.textContent = label;
    tab.appendChild(span);

    var closeBtn = document.createElement('span');
    closeBtn.className = 'term-tab-close';
    closeBtn.textContent = '×';
    closeBtn.title = 'Close session';
    closeBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      _disconnect(sessionId);
    });
    tab.appendChild(closeBtn);

    tab.addEventListener('click', function () {
      _activateTab(sessionId);
    });

    tabs.insertBefore(tab, newBtn);
    return tab;
  }

  function _activateTab(sessionId) {
    var entry = _terms[sessionId];
    if (!entry) return;

    Object.keys(_terms).forEach(function (sid) {
      var e = _terms[sid];
      e.container.style.display = 'none';
      e.tab.classList.remove('term-tab-active');
    });

    entry.container.style.display = 'block';
    entry.tab.classList.add('term-tab-active');
    _activeSession = sessionId;

    requestAnimationFrame(function () {
      entry.fitAddon.fit();
      entry.term.focus();
    });

    _updateRouteBar(sessionId);
  }

  // ── Route bar ─────────────────────────────────────────────────────

  function _updateRouteBar(sessionId) {
    var entry = _terms[sessionId];
    var bar = _$('termRouteBar');
    if (!entry || !bar) return;

    bar.hidden = false;

    var via = _$('termRouteVia');
    if (via) {
      var parts = [];
      if (entry.fabric) parts.push(entry.fabric);
      if (entry.via && entry.via !== 'direct') parts.push('via ' + entry.via);
      parts.push(entry.ip || entry.hostname || sessionId);
      via.textContent = parts.join(' → ');
    }

    _updateDuration(sessionId);
  }

  function _updateDuration(sessionId) {
    var entry = _terms[sessionId];
    var el = _$('termRouteDuration');
    if (!entry || !el) return;
    el.textContent = _formatDuration(Date.now() - entry.connected_at);
  }

  // ── Tunnel management ─────────────────────────────────────────────

  function _toggleTunnelDrawer() {
    _drawerOpen = !_drawerOpen;
    var body = _$('tunnelDrawerBody');
    var arrow = _$('tunnelArrow');
    if (body) body.hidden = !_drawerOpen;
    if (arrow) arrow.innerHTML = _drawerOpen ? '&#9660;' : '&#9650;';
    if (_drawerOpen) _loadTunnels();
  }

  function _loadTunnels() {
    _json('/api/tunnels').then(function (d) {
      _tunnels = d.tunnels || [];
      _renderTunnels();
      var badge = _$('tunnelCount');
      if (badge) badge.textContent = _tunnels.length;
    }).catch(function () {
      _tunnels = [];
      _renderTunnels();
    });
  }

  function _renderTunnels() {
    var list = _$('tunnelList');
    if (!list) return;

    if (!_tunnels.length) {
      list.innerHTML = '<div class="tunnel-empty">No active tunnels.</div>';
      return;
    }

    var html = '';
    for (var i = 0; i < _tunnels.length; i++) {
      var t = _tunnels[i];
      var ttype = t.tunnel_type || t.type || 'forward';
      var icon = ttype === 'socks5' ? '&#9673;' : '&#8644;';
      var detail = ttype === 'socks5'
        ? 'localhost:' + t.local_port
        : 'localhost:' + t.local_port + ' → ' + _esc(t.remote_host) + ':' + t.remote_port;
      var statusCls = t.status === 'active' ? 'tunnel-status-up' : 'tunnel-status-down';
      var tid = t.tunnel_id || t.id || '';

      html += '<div class="tunnel-row">';
      html += '<span class="tunnel-type">' + icon + '</span>';
      html += '<span class="tunnel-label">' + _esc(ttype === 'socks5' ? 'SOCKS5' : 'Forward') + '</span>';
      html += '<span class="tunnel-detail">' + detail + '</span>';
      if (t.fabric_name) html += '<span class="tunnel-fabric">' + _esc(t.fabric_name) + '</span>';
      html += '<span class="tunnel-status ' + statusCls + '"></span>';
      html += '<button class="btn btn-xs btn-danger tunnel-destroy" data-tid="' + tid + '" title="Destroy">&times;</button>';
      html += '</div>';
    }
    list.innerHTML = html;

    list.querySelectorAll('.tunnel-destroy').forEach(function (btn) {
      btn.addEventListener('click', function () {
        _destroyTunnel(btn.getAttribute('data-tid'));
      });
    });
  }

  function _destroyTunnel(tunnelId) {
    _json('/api/tunnels/' + tunnelId, { method: 'DELETE' }).then(function () {
      _loadTunnels();
    });
  }

  // ── SOCKS5 dialog ─────────────────────────────────────────────────

  function _openSocksDialog() {
    _json('/api/fleet/nodes?status=online').then(function (d) {
      _nodes = d.nodes || d || [];
      _renderSocksForm();
      _$('socksModal').classList.add('show');
    });
  }

  function _closeSocksDialog() {
    _$('socksModal').classList.remove('show');
  }

  function _renderSocksForm() {
    var body = _$('socksBody');
    var html = '<div class="form-row"><label>Node (SSH hop)</label><select class="form-input" id="socksNodeSelect">';
    for (var i = 0; i < _nodes.length; i++) {
      var n = _nodes[i];
      html += '<option value="' + n.id + '">' + _esc(n.hostname || n.ip_primary || n.ip) + '</option>';
    }
    html += '</select></div>';
    html += '<div class="form-row"><label>Credential</label><select class="form-input" id="socksCredSelect"><option value="">Loading…</option></select></div>';
    html += '<div class="form-row"><label>Local Port</label><input type="number" class="form-input" id="socksLocalPort" value="1080" min="1024" max="65535"></div>';
    body.innerHTML = html;

    var sel = _$('socksNodeSelect');
    sel.addEventListener('change', function () { _loadTunnelCreds(sel.value, 'socksCredSelect'); });
    _loadTunnelCreds(sel.value, 'socksCredSelect');
  }

  function _loadTunnelCreds(nodeId, selectId) {
    var sel = _$(selectId);
    if (!sel) return;
    sel.innerHTML = '<option value="">Loading…</option>';

    _json('/api/credentials/' + nodeId).then(function (d) {
      var creds = d.credentials || [];
      sel.innerHTML = '';
      for (var i = 0; i < creds.length; i++) {
        var c = creds[i];
        sel.innerHTML += '<option value="' + c.id + '">' + _esc(c.label || c.username) + '</option>';
      }
      if (!creds.length) sel.innerHTML = '<option value="">No credentials</option>';
    }).catch(function () {
      sel.innerHTML = '<option value="">Error loading</option>';
    });
  }

  function _createSocks() {
    var nodeId = parseInt((_$('socksNodeSelect') || {}).value, 10);
    var credId = parseInt((_$('socksCredSelect') || {}).value, 10);
    var port   = parseInt((_$('socksLocalPort')  || {}).value, 10) || 1080;

    var btn = _$('socksCreateBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Creating…'; }

    _json('/api/tunnels/socks', {
      method: 'POST',
      body: { node_id: nodeId, credential_id: credId, local_port: port },
    }).then(function (d) {
      if (btn) { btn.disabled = false; btn.textContent = 'Create'; }
      if (!d.ok) { alert(d.error || 'Failed to create SOCKS5 proxy.'); return; }
      _closeSocksDialog();
      _loadTunnels();
    }).catch(function (err) {
      if (btn) { btn.disabled = false; btn.textContent = 'Create'; }
      alert('Error: ' + (err.message || err));
    });
  }

  // ── Port Forward dialog ───────────────────────────────────────────

  function _openForwardDialog() {
    _json('/api/fleet/nodes?status=online').then(function (d) {
      _nodes = d.nodes || d || [];
      _renderForwardForm();
      _$('forwardModal').classList.add('show');
    });
  }

  function _closeForwardDialog() {
    _$('forwardModal').classList.remove('show');
  }

  function _renderForwardForm() {
    var body = _$('forwardBody');
    var html = '<div class="form-row"><label>Node (SSH hop)</label><select class="form-input" id="fwdNodeSelect">';
    for (var i = 0; i < _nodes.length; i++) {
      var n = _nodes[i];
      html += '<option value="' + n.id + '">' + _esc(n.hostname || n.ip_primary || n.ip) + '</option>';
    }
    html += '</select></div>';
    html += '<div class="form-row"><label>Credential</label><select class="form-input" id="fwdCredSelect"><option value="">Loading…</option></select></div>';
    html += '<div class="form-row"><label>Local Port</label><input type="number" class="form-input" id="fwdLocalPort" value="8080" min="1024" max="65535"></div>';
    html += '<div class="form-row"><label>Remote Host</label><input type="text" class="form-input" id="fwdRemoteHost" placeholder="127.0.0.1" value="127.0.0.1"></div>';
    html += '<div class="form-row"><label>Remote Port</label><input type="number" class="form-input" id="fwdRemotePort" value="80" min="1" max="65535"></div>';
    body.innerHTML = html;

    var sel = _$('fwdNodeSelect');
    sel.addEventListener('change', function () { _loadTunnelCreds(sel.value, 'fwdCredSelect'); });
    _loadTunnelCreds(sel.value, 'fwdCredSelect');
  }

  function _createForward() {
    var nodeId     = parseInt((_$('fwdNodeSelect')   || {}).value, 10);
    var credId     = parseInt((_$('fwdCredSelect')   || {}).value, 10);
    var localPort  = parseInt((_$('fwdLocalPort')    || {}).value, 10) || 8080;
    var remoteHost = (_$('fwdRemoteHost') || {}).value || '127.0.0.1';
    var remotePort = parseInt((_$('fwdRemotePort')   || {}).value, 10) || 80;

    var btn = _$('forwardCreateBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Creating…'; }

    _json('/api/tunnels/forward', {
      method: 'POST',
      body: {
        node_id: nodeId, credential_id: credId,
        local_port: localPort, remote_host: remoteHost, remote_port: remotePort,
      },
    }).then(function (d) {
      if (btn) { btn.disabled = false; btn.textContent = 'Create'; }
      if (!d.ok) { alert(d.error || 'Failed to create port forward.'); return; }
      _closeForwardDialog();
      _loadTunnels();
    }).catch(function (err) {
      if (btn) { btn.disabled = false; btn.textContent = 'Create'; }
      alert('Error: ' + (err.message || err));
    });
  }

  // ── Window resize ─────────────────────────────────────────────────

  function _onResize() {
    if (_activeSession && _terms[_activeSession]) {
      _terms[_activeSession].fitAddon.fit();
    }
  }

  // ── Load existing sessions on page load ───────────────────────────

  function _loadSessions() {
    _json('/api/terminal/sessions').then(function (d) {
      var sessions = d.sessions || [];
      for (var i = 0; i < sessions.length; i++) {
        var s = sessions[i];
        if (!_terms[s.session_id]) {
          _createSession(s.session_id, {
            hostname:     s.hostname || '',
            ip:           s.ip || '',
            via:          s.via || 'direct',
            fabric:       s.fabric_name || '',
          });
          if (s.connected_at) {
            _terms[s.session_id].connected_at = new Date(s.connected_at).getTime();
          }
        }
      }
    }).catch(function () { /* no existing sessions */ });
  }

  // ── SocketIO setup ────────────────────────────────────────────────

  function _initSocket() {
    if (window.NX && window.NX._socket) {
      _socket = window.NX._socket;
    } else if (typeof io !== 'undefined') {
      _socket = io({ transports: ['websocket', 'polling'] });
    } else {
      console.error('terminal.js: socket.io not available');
      return;
    }

    _socket.on('term_output', function (d) {
      var entry = _terms[d.session_id];
      if (entry) entry.term.write(d.data);
    });

    _socket.on('term_closed', function (d) {
      var entry = _terms[d.session_id];
      if (entry) {
        entry.term.writeln('\r\n\x1b[31m--- Session closed' + (d.reason ? ': ' + d.reason : '') + ' ---\x1b[0m');
        entry.tab.classList.add('term-tab-dead');
      }
    });

    _socket.on('tunnel_update', function () {
      _loadTunnels();
    });
  }

  // ── Duration ticker ───────────────────────────────────────────────

  function _updateDurations() {
    if (_activeSession) _updateDuration(_activeSession);
  }

  // ── Init ──────────────────────────────────────────────────────────

  function _init() {
    _initSocket();

    var newBtn = _$('termNewBtn');
    if (newBtn) newBtn.addEventListener('click', _openConnectModal);
    window.addEventListener('resize', _onResize);

    _loadSessions();
    _loadTunnels();

    setInterval(_updateDurations, 1000);
  }

  document.addEventListener('DOMContentLoaded', _init);

  // ── Public API (extends NX namespace from nexus.js) ───────────────

  window.NX = window.NX || {};
  window.NX.closeTermConnect   = _closeTermConnect;
  window.NX.termConnect        = _connect;
  window.NX.toggleTunnelDrawer = _toggleTunnelDrawer;
  window.NX.openSocksDialog    = _openSocksDialog;
  window.NX.closeSocksDialog   = _closeSocksDialog;
  window.NX.createSocks        = _createSocks;
  window.NX.openForwardDialog  = _openForwardDialog;
  window.NX.closeForwardDialog = _closeForwardDialog;
  window.NX.createForward      = _createForward;

})();
