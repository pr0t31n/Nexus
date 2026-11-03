/* Nexus v2.5 — Audit & Monitoring JS */
(function () {
  'use strict';

  /* ── State ── */
  var state = {
    sessions: { page: 0, limit: 50, total: 0 },
    ports:    { page: 0, limit: 100, total: 0 },
    log:      { page: 0, limit: 100, total: 0 },
    nh:       { page: 0, limit: 100, total: 0 },
  };

  /* ── Helpers ── */
  function $(id) { return document.getElementById(id); }
  function esc(s) { if (!s) return ''; var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  function fmtTs(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d)) return esc(iso);
    return d.toLocaleString('en-US', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  }

  function fmtDuration(start, end) {
    if (!start) return '—';
    if (!end) return 'active';
    var ms = new Date(end) - new Date(start);
    if (ms < 0) return '—';
    var s = Math.floor(ms / 1000);
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    var h = Math.floor(s / 3600);
    return h + 'h ' + Math.floor((s % 3600) / 60) + 'm';
  }

  function badgeFor(val, map) {
    var cls = map[val] || 'badge-accent';
    return '<span class="badge ' + cls + '">' + esc(val) + '</span>';
  }

  var authBadges = { success: 'badge-online', failed: 'badge-danger', timeout: 'badge-warning' };
  var sevBadges  = { info: 'badge-accent', warning: 'badge-warning', error: 'badge-danger', critical: 'badge-danger' };

  function collectFilters(prefix, keys) {
    var f = {};
    keys.forEach(function (k) {
      var el = $(prefix + '-' + k);
      if (el && el.value) f[k] = el.value;
    });
    var from = $(prefix + '-from');
    var to   = $(prefix + '-to');
    if (from && from.value) f['from'] = from.value;
    if (to && to.value)     f['to']   = to.value;
    return f;
  }

  function qs(obj) {
    var parts = [];
    for (var k in obj) { if (obj[k] !== '' && obj[k] != null) parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(obj[k])); }
    return parts.length ? '?' + parts.join('&') : '';
  }

  function updatePager(sec, prefix) {
    var s = state[sec];
    var pages = Math.max(1, Math.ceil(s.total / s.limit));
    var cur = s.page + 1;
    $(prefix + '-page-info').textContent = 'Page ' + cur + ' of ' + pages + ' (' + s.total + ')';
    $(prefix + '-prev').disabled = s.page <= 0;
    $(prefix + '-next').disabled = cur >= pages;
  }

  /* ── Fetch helpers ── */
  function api(path, params, cb) {
    params = params || {};
    fetch('/api/audit/' + path + qs(params), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(cb)
      .catch(function (e) { console.error('audit api', path, e); });
  }

  /* ── Stats ── */
  function loadStats() {
    api('stats', {}, function (d) {
      $('stat-active-sessions').textContent = d.active_sessions;
      $('stat-open-ports').textContent = d.open_ports;
      $('stat-unexpected').textContent = d.unexpected_ports;
      $('stat-failed-auth').textContent = d.failed_auth_24h;
      $('stat-audit-24h').textContent = d.audit_entries_24h;

      var el = $('stat-unexpected');
      if (d.unexpected_ports > 0) el.closest('.stat-card').classList.add('stat-card--warn');
      else el.closest('.stat-card').classList.remove('stat-card--warn');
    });
  }

  /* ── Sessions ── */
  function loadSessions() {
    var s = state.sessions;
    var f = collectFilters('sess', ['search', 'method', 'result']);
    if (f.result) { f.auth_result = f.result; delete f.result; }
    if (f.search) { f.search = f.search; }
    f.limit = s.limit;
    f.offset = s.page * s.limit;
    api('sessions', f, function (d) {
      s.total = d.total;
      var tb = $('sessions-table').querySelector('tbody');
      tb.innerHTML = '';
      if (!d.sessions.length) {
        tb.innerHTML = '<tr><td colspan="8" class="text-muted" style="text-align:center">No sessions found</td></tr>';
      } else {
        d.sessions.forEach(function (r) {
          var tr = document.createElement('tr');
          tr.className = 'audit-row';
          tr.innerHTML =
            '<td>' + fmtTs(r.timestamp_start) + '</td>' +
            '<td>' + esc(r.method) + '</td>' +
            '<td><code>' + esc(r.source_ip) + '</code></td>' +
            '<td>' + esc(r.username || '—') + '</td>' +
            '<td>' + esc(r.node_hostname || r.node_id || '—') + '</td>' +
            '<td>' + badgeFor(r.auth_result, authBadges) + '</td>' +
            '<td>' + fmtDuration(r.timestamp_start, r.timestamp_end) + '</td>' +
            '<td><button class="nx-btn nx-btn--sm nx-btn--ghost" data-sid="' + r.id + '">View</button></td>';
          tb.appendChild(tr);
        });
      }
      updatePager('sessions', 'sess');
    });
  }

  /* ── Ports ── */
  function loadPorts() {
    var s = state.ports;
    var f = collectFilters('port', ['search', 'proto']);
    var stateEl = $('port-state');
    if (stateEl && stateEl.value) f.is_open = stateEl.value;
    var wlEl = $('port-wl');
    if (wlEl && wlEl.value) f.whitelisted = wlEl.value;
    f.limit = s.limit;
    f.offset = s.page * s.limit;
    api('ports', f, function (d) {
      s.total = d.total;
      var tb = $('ports-table').querySelector('tbody');
      tb.innerHTML = '';
      if (!d.ports.length) {
        tb.innerHTML = '<tr><td colspan="10" class="text-muted" style="text-align:center">No ports found</td></tr>';
      } else {
        d.ports.forEach(function (r) {
          var openBadge = r.is_open ? '<span class="badge badge-online">OPEN</span>' : '<span class="badge badge-offline">CLOSED</span>';
          var wlBadge = r.whitelisted ? '<span class="badge badge-accent">Yes</span>' : '<span class="badge badge-offline">No</span>';
          var arsMatch = r.arsenal_tool_match ? esc(r.arsenal_tool_match) : '<span class="text-muted">—</span>';
          var tr = document.createElement('tr');
          tr.className = 'audit-row';
          tr.innerHTML =
            '<td><strong>' + r.port + '</strong></td>' +
            '<td>' + esc(r.protocol || 'tcp') + '</td>' +
            '<td>' + esc(r.service_name || '—') + '</td>' +
            '<td><code>' + esc(r.bind_address || '—') + '</code></td>' +
            '<td>' + esc(r.process_owner || '—') + '</td>' +
            '<td class="audit-cell-trunc" title="' + esc(r.binary_path || '') + '">' + esc(r.binary_path || '—') + '</td>' +
            '<td>' + openBadge + '</td>' +
            '<td>' + wlBadge + '</td>' +
            '<td>' + arsMatch + '</td>' +
            '<td><button class="nx-btn nx-btn--sm nx-btn--ghost" data-pid="' + r.id + '">Manage</button></td>';
          tb.appendChild(tr);
        });
      }
      updatePager('ports', 'port');
    });
  }

  /* ── Audit Log ── */
  function loadAuditLog() {
    var s = state.log;
    var f = collectFilters('log', ['search', 'severity']);
    f.limit = s.limit;
    f.offset = s.page * s.limit;
    api('log', f, function (d) {
      s.total = d.total;
      var tb = $('auditlog-table').querySelector('tbody');
      tb.innerHTML = '';
      if (!d.entries.length) {
        tb.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center">No audit entries</td></tr>';
      } else {
        d.entries.forEach(function (r) {
          var sev = (r.severity || r.event_type || 'info').toLowerCase();
          var tr = document.createElement('tr');
          tr.className = 'audit-row';
          tr.innerHTML =
            '<td>' + fmtTs(r.ts) + '</td>' +
            '<td>' + badgeFor(sev, sevBadges) + '</td>' +
            '<td>' + esc(r.action) + '</td>' +
            '<td>' + esc(r.username || '—') + '</td>' +
            '<td class="audit-cell-trunc" title="' + esc(r.detail || '') + '">' + esc(r.detail || '—') + '</td>' +
            '<td><code>' + esc(r.ip || '—') + '</code></td>';
          tb.appendChild(tr);
        });
      }
      updatePager('log', 'log');
    });
  }

  /* ── Node History ── */
  function loadNodeHistory() {
    var s = state.nh;
    var f = collectFilters('nh', ['search']);
    var evtEl = $('nh-event-type');
    if (evtEl && evtEl.value) f.event_type = evtEl.value;
    f.limit = s.limit;
    f.offset = s.page * s.limit;
    api('node-history', f, function (d) {
      s.total = d.total;
      var tb = $('nodehistory-table').querySelector('tbody');
      tb.innerHTML = '';
      if (!d.events.length) {
        tb.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center">No node history</td></tr>';
      } else {
        d.events.forEach(function (r) {
          var tr = document.createElement('tr');
          tr.className = 'audit-row';
          tr.innerHTML =
            '<td>' + fmtTs(r.ts) + '</td>' +
            '<td>' + esc(r.node_hostname || r.node_id) + '</td>' +
            '<td>' + badgeFor(r.event_type, {
              connect: 'badge-online', disconnect: 'badge-danger',
              ip_change: 'badge-warning', version_change: 'badge-accent'
            }) + '</td>' +
            '<td class="audit-cell-trunc" title="' + esc(r.prev_value || '') + '">' + esc(r.prev_value || '—') + '</td>' +
            '<td class="audit-cell-trunc" title="' + esc(r.new_value || '') + '">' + esc(r.new_value || '—') + '</td>' +
            '<td class="audit-cell-trunc" title="' + esc(r.detail || '') + '">' + esc(r.detail || '—') + '</td>';
          tb.appendChild(tr);
        });
      }
      updatePager('nh', 'nh');
    });
  }

  /* ── Session drawer ── */
  function openSessionDrawer(sid) {
    var drawer = $('session-drawer');
    var body   = $('session-drawer-body');
    drawer.classList.add('open');
    body.innerHTML = '<p class="text-muted">Loading…</p>';
    api('sessions/' + sid, {}, function (d) {
      var rows = [
        ['Method',        d.method],
        ['Interface',     d.interface_name || '—'],
        ['Source IP',     d.source_ip],
        ['Source MAC',    d.source_mac || '—'],
        ['Hostname',      d.node_hostname || d.node_id || '—'],
        ['Username',      d.username || '—'],
        ['Auth Result',   d.auth_result],
        ['Session Type',  d.session_type || '—'],
        ['Key Fingerprint', d.key_fingerprint || '—'],
        ['Cert Fingerprint', d.cert_fingerprint || '—'],
        ['Geo IP',        d.geo_ip || '—'],
        ['ASN',           d.asn || '—'],
        ['Process Lineage', d.process_lineage || '—'],
        ['Start',         fmtTs(d.timestamp_start)],
        ['End',           d.timestamp_end ? fmtTs(d.timestamp_end) : 'Active'],
        ['Duration',      fmtDuration(d.timestamp_start, d.timestamp_end)],
      ];
      var html = '<table class="audit-detail-table">';
      rows.forEach(function (r) {
        html += '<tr><th>' + esc(r[0]) + '</th><td>' + esc(String(r[1])) + '</td></tr>';
      });
      html += '</table>';
      body.innerHTML = html;
    });
  }

  function closeSessionDrawer() {
    $('session-drawer').classList.remove('open');
  }

  /* ── Port modal ── */
  function openPortModal(pid) {
    var overlay = $('port-modal-overlay');
    var body    = $('port-modal-body');
    var footer  = $('port-modal-footer');
    overlay.classList.add('open');
    body.innerHTML = '<p class="text-muted">Loading…</p>';
    footer.innerHTML = '';
    api('ports/' + pid, {}, function (d) {
      var rows = [
        ['Port',           d.port],
        ['Protocol',       d.protocol || 'tcp'],
        ['Service',        d.service_name || '—'],
        ['Bind Address',   d.bind_address || '—'],
        ['Process Owner',  d.process_owner || '—'],
        ['Binary Path',    d.binary_path || '—'],
        ['Binary Hash',    d.binary_hash || '—'],
        ['State',          d.is_open ? 'OPEN' : 'CLOSED'],
        ['First Seen',     fmtTs(d.first_seen)],
        ['Last Seen',      fmtTs(d.last_seen)],
        ['Whitelisted',    d.whitelisted ? 'Yes' : 'No'],
        ['WL Approved By', d.wl_approved_by || '—'],
        ['WL Reason',      d.wl_reason || '—'],
        ['WL Date',        d.wl_date ? fmtTs(d.wl_date) : '—'],
        ['Arsenal Match',  d.arsenal_tool_match || '—'],
        ['Firewall Rule',  d.firewall_rule || '—'],
        ['Notes',          d.notes || '—'],
      ];
      var html = '<table class="audit-detail-table">';
      rows.forEach(function (r) {
        html += '<tr><th>' + esc(r[0]) + '</th><td>' + esc(String(r[1])) + '</td></tr>';
      });
      html += '</table>';
      body.innerHTML = html;

      var btns = '';
      if (d.whitelisted) {
        btns += '<button class="nx-btn nx-btn--danger" onclick="NX._portUnwhitelist(' + d.id + ')">Remove Whitelist</button>';
      } else {
        btns += '<input type="text" class="nx-input" id="wl-reason" placeholder="Whitelist reason…" style="flex:1">';
        btns += '<button class="nx-btn nx-btn--primary" onclick="NX._portWhitelist(' + d.id + ')">Whitelist</button>';
      }
      footer.innerHTML = '<div class="audit-modal-actions">' + btns + '</div>';
    });
  }

  function closePortModal() {
    $('port-modal-overlay').classList.remove('open');
  }

  function portWhitelist(pid) {
    var reason = ($('wl-reason') || {}).value || '';
    fetch('/api/audit/ports/' + pid + '/whitelist', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason })
    }).then(function () { closePortModal(); loadPorts(); loadStats(); });
  }

  function portUnwhitelist(pid) {
    fetch('/api/audit/ports/' + pid + '/unwhitelist', {
      method: 'POST', credentials: 'same-origin'
    }).then(function () { closePortModal(); loadPorts(); loadStats(); });
  }

  /* ── Tab switching ── */
  function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.remove('active'); });
        document.querySelectorAll('.tab-content').forEach(function (c) { c.classList.remove('active'); });
        btn.classList.add('active');
        var target = $(btn.getAttribute('data-tab'));
        if (target) target.classList.add('active');
      });
    });
  }

  /* ── Paging ── */
  function bindPager(sec, prefix, loader) {
    $(prefix + '-prev').addEventListener('click', function () {
      if (state[sec].page > 0) { state[sec].page--; loader(); }
    });
    $(prefix + '-next').addEventListener('click', function () {
      var pages = Math.ceil(state[sec].total / state[sec].limit);
      if (state[sec].page + 1 < pages) { state[sec].page++; loader(); }
    });
  }

  /* ── Filter bindings ── */
  function bindFilters(prefix, ids, loader) {
    var timer = null;
    ids.forEach(function (id) {
      var el = $(prefix + '-' + id);
      if (!el) return;
      var ev = el.tagName === 'SELECT' ? 'change' : 'input';
      el.addEventListener(ev, function () {
        state[prefix === 'sess' ? 'sessions' : prefix === 'port' ? 'ports' : prefix === 'log' ? 'log' : 'nh'].page = 0;
        clearTimeout(timer);
        timer = setTimeout(loader, el.tagName === 'SELECT' ? 0 : 300);
      });
    });
    var clearBtn = $(prefix + '-clear-filters');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        ids.forEach(function (id) {
          var el = $(prefix + '-' + id);
          if (el) el.value = '';
        });
        state[prefix === 'sess' ? 'sessions' : prefix === 'port' ? 'ports' : prefix === 'log' ? 'log' : 'nh'].page = 0;
        loader();
      });
    }
  }

  /* ── Delegated clicks ── */
  function bindDelegated() {
    $('sessions-table').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-sid]');
      if (btn) openSessionDrawer(parseInt(btn.dataset.sid));
    });
    $('ports-table').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-pid]');
      if (btn) openPortModal(parseInt(btn.dataset.pid));
    });
    $('session-drawer-close').addEventListener('click', closeSessionDrawer);
    $('session-drawer-backdrop').addEventListener('click', closeSessionDrawer);
    $('port-modal-close').addEventListener('click', closePortModal);
    $('port-modal-overlay').addEventListener('click', function (e) {
      if (e.target === $('port-modal-overlay')) closePortModal();
    });
  }

  /* ── Init ── */
  function initAudit() {
    initTabs();
    bindPager('sessions', 'sess', loadSessions);
    bindPager('ports', 'port', loadPorts);
    bindPager('log', 'log', loadAuditLog);
    bindPager('nh', 'nh', loadNodeHistory);

    bindFilters('sess', ['search', 'method', 'result', 'from', 'to'], loadSessions);
    bindFilters('port', ['search', 'state', 'wl', 'proto'], loadPorts);
    bindFilters('log',  ['search', 'severity', 'from', 'to'], loadAuditLog);
    bindFilters('nh',   ['search', 'event-type', 'from', 'to'], loadNodeHistory);

    bindDelegated();
    loadStats();
    loadSessions();
    loadPorts();
    loadAuditLog();
    loadNodeHistory();

    setInterval(loadStats, 30000);
  }

  /* ── Expose ── */
  window.NX = window.NX || {};
  NX.initAudit = initAudit;
  NX._portWhitelist = portWhitelist;
  NX._portUnwhitelist = portUnwhitelist;
})();
