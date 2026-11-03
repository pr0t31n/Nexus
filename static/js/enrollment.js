(function () {
  'use strict';

  var NX = window.NX || {};
  window.NX = NX;

  /* ── state ── */
  var _enrollments = [];
  var _brokerStatus = {};
  var _pollTimer = null;

  /* ── API helpers ── */

  function _get(url) {
    return fetch(url).then(function (r) { return r.json(); });
  }

  function _post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  /* ── Enrollment ── */

  function loadEnrollmentStatus() {
    return _get('/api/enrollment/status').then(function (data) {
      _enrollments = data.enrollments || [];
      renderEnrollmentTable();
      _updateSummaryCards();
      return data;
    }).catch(function (err) {
      console.error('[Nexus] enrollment status error:', err);
    });
  }

  function issueAgentCert(hostname) {
    if (!hostname || !hostname.trim()) return Promise.resolve();
    return _post('/api/enrollment/issue', { hostname: hostname.trim() }).then(function (data) {
      if (data.error) {
        console.error('[Nexus] issue cert error:', data.error);
      }
      loadEnrollmentStatus();
      return data;
    }).catch(function (err) {
      console.error('[Nexus] issue cert error:', err);
    });
  }

  function revokeEnrollment(nodeId) {
    if (!nodeId) return Promise.resolve();
    return _post('/api/enrollment/revoke', { node_id: nodeId }).then(function (data) {
      if (data.error) {
        console.error('[Nexus] revoke error:', data.error);
      }
      loadEnrollmentStatus();
      return data;
    }).catch(function (err) {
      console.error('[Nexus] revoke error:', err);
    });
  }

  function renderEnrollmentTable() {
    var tbody = document.querySelector('#enrollment-table tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    _enrollments.forEach(function (e) {
      var tr = document.createElement('tr');
      tr.className = 'enrollment-row';

      var enrolledBadge = e.enrolled
        ? '<span class="nx-badge nx-badge--success">Enrolled</span>'
        : '<span class="nx-badge nx-badge--warn">Pending</span>';

      var connBadge = e.agent_connected
        ? '<span class="nx-badge nx-badge--success">Connected</span>'
        : '<span class="nx-badge nx-badge--danger">Disconnected</span>';

      var actions = '';
      if (e.enrolled) {
        actions = '<button class="nx-btn nx-btn--sm nx-btn--danger" '
          + 'onclick="NX.revokeEnrollment(\'' + _esc(e.node_id) + '\')">Revoke</button>';
      } else {
        actions = '<button class="nx-btn nx-btn--sm nx-btn--primary" '
          + 'onclick="NX.issueAgentCert(\'' + _esc(e.hostname) + '\')">Issue</button>';
      }

      tr.innerHTML = ''
        + '<td>' + _esc(e.hostname || '—') + '</td>'
        + '<td>' + enrolledBadge + '</td>'
        + '<td>' + connBadge + '</td>'
        + '<td>' + _esc(e.agent_version || '—') + '</td>'
        + '<td>' + _esc(e.os || '—') + '</td>'
        + '<td>' + _esc(e.last_seen || '—') + '</td>'
        + '<td>' + actions + '</td>';

      tbody.appendChild(tr);
    });
  }

  /* ── Broker ── */

  function loadBrokerStatus() {
    return _get('/api/broker/status').then(function (data) {
      _brokerStatus = data || {};
      renderBrokerPanel();
      _updateSummaryCards();
      return data;
    }).catch(function (err) {
      console.error('[Nexus] broker status error:', err);
    });
  }

  function renderBrokerPanel() {
    var panel = document.getElementById('broker-panel');
    if (!panel) return;

    var agents = _brokerStatus.agents || [];
    var connected = _brokerStatus.connected_count || agents.length;

    var statusDot = _brokerStatus.running
      ? '<span class="broker-status-dot broker-status-dot--ok"></span> Running'
      : '<span class="broker-status-dot broker-status-dot--off"></span> Stopped';

    var html = ''
      + '<div class="broker-panel-header">'
      + '  <div class="broker-panel-status">' + statusDot + '</div>'
      + '  <div class="broker-panel-count">' + connected + ' agent(s) connected</div>'
      + '</div>'
      + '<table class="enrollment-table" id="broker-agents">'
      + '  <thead><tr>'
      + '    <th>Hostname</th><th>Status</th><th>Last Heartbeat</th>'
      + '  </tr></thead>'
      + '  <tbody>';

    agents.forEach(function (a) {
      var hbClass = a.alive ? 'heartbeat--ok' : 'heartbeat--stale';
      html += '<tr class="broker-agent-row">'
        + '<td>' + _esc(a.hostname || a.node_id || '—') + '</td>'
        + '<td><span class="heartbeat-indicator ' + hbClass + '"></span> '
        + (a.alive ? 'Alive' : 'Stale') + '</td>'
        + '<td>' + _esc(a.last_heartbeat || '—') + '</td>'
        + '</tr>';
    });

    html += '</tbody></table>';
    panel.innerHTML = html;
  }

  /* ── Summary cards ── */

  function _updateSummaryCards() {
    var enrolled = 0;
    var pending = 0;
    var connected = 0;

    _enrollments.forEach(function (e) {
      if (e.enrolled) { enrolled++; } else { pending++; }
      if (e.agent_connected) { connected++; }
    });

    _setText('enrolled-count', enrolled);
    _setText('pending-count', pending);
    _setText('connected-count', connected);
  }

  /* ── SocketIO listeners ── */

  function _bindSocket() {
    var sock = NX.socket;
    if (!sock) return;

    sock.on('broker_agent_connected', function (data) {
      loadBrokerStatus();
      loadEnrollmentStatus();
    });

    sock.on('broker_agent_disconnected', function (data) {
      loadBrokerStatus();
      loadEnrollmentStatus();
    });

    sock.on('enrollment_update', function (data) {
      loadEnrollmentStatus();
    });
  }

  /* ── Tabs ── */

  function _initTabs() {
    var btns = document.querySelectorAll('.tab-btn[data-tab]');
    btns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var target = btn.getAttribute('data-tab');
        btns.forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        document.querySelectorAll('.tab-content').forEach(function (tc) {
          tc.classList.toggle('active', tc.id === target);
        });
      });
    });
  }

  /* ── Issue form toggle ── */

  function _initIssueForm() {
    var toggleBtn = document.getElementById('show-issue-form');
    var form = document.getElementById('issue-form');
    if (!toggleBtn || !form) return;

    toggleBtn.addEventListener('click', function () {
      form.classList.toggle('hidden');
    });

    var submitBtn = document.getElementById('issue-submit');
    if (submitBtn) {
      submitBtn.addEventListener('click', function () {
        var input = document.getElementById('issue-hostname');
        if (input && input.value.trim()) {
          issueAgentCert(input.value.trim());
          input.value = '';
          form.classList.add('hidden');
        }
      });
    }
  }

  /* ── Utilities ── */

  function _esc(s) {
    if (s === null || s === undefined) return '';
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(String(s)));
    return d.innerHTML;
  }

  function _setText(id, val) {
    var el = document.getElementById(id);
    if (el) el.textContent = String(val);
  }

  /* ── Init ── */

  function initEnrollment() {
    _initTabs();
    _initIssueForm();
    _bindSocket();
    loadEnrollmentStatus();
    loadBrokerStatus();

    _pollTimer = setInterval(function () {
      loadEnrollmentStatus();
      loadBrokerStatus();
    }, 15000);
  }

  /* ── Public API ── */
  NX.loadEnrollmentStatus = loadEnrollmentStatus;
  NX.loadBrokerStatus = loadBrokerStatus;
  NX.issueAgentCert = issueAgentCert;
  NX.revokeEnrollment = revokeEnrollment;
  NX.renderEnrollmentTable = renderEnrollmentTable;
  NX.renderBrokerPanel = renderBrokerPanel;
  NX.initEnrollment = initEnrollment;

})();
