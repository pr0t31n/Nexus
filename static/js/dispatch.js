(function () {
  'use strict';

  var NX = window.NX || {};
  window.NX = NX;

  /* ── state ── */
  var _appliances = [];
  var _dispatchJobs = [];

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

  /* ── Appliances ── */

  function loadAppliances() {
    return _get('/api/fleet/appliances').then(function (data) {
      _appliances = data.appliances || [];
      renderApplianceCards();
      _populateNodeSelect();
      return data;
    }).catch(function (err) {
      console.error('[Nexus] load appliances error:', err);
    });
  }

  function renderApplianceCards() {
    var grid = document.getElementById('appliance-grid');
    if (!grid) return;
    grid.innerHTML = '';

    _appliances.forEach(function (a) {
      var card = document.createElement('div');
      card.className = 'appliance-card nx-card';

      var enrolledBadge = a.enrolled
        ? '<span class="nx-badge nx-badge--success">Enrolled</span>'
        : '<span class="nx-badge nx-badge--warn">Pending</span>';

      var toolPills = '';
      if (a.tools && a.tools.length) {
        a.tools.forEach(function (t) {
          var dotClass = t.status === 'running'
            ? 'tool-dot tool-dot--running'
            : t.status === 'installed'
              ? 'tool-dot tool-dot--installed'
              : 'tool-dot tool-dot--absent';
          toolPills += '<span class="tool-pill">'
            + '<span class="' + dotClass + '"></span> '
            + _esc(t.name)
            + '</span>';
        });
      }

      var lanes = '';
      if (a.fabrics && a.fabrics.length) {
        lanes = '<div class="appliance-lanes">'
          + a.fabrics.map(function (f) {
            var label = f.fabric_name || ('Fabric ' + f.fabric_id);
            var dot = f.lane_status === 'up'
              ? '<span class="lane-dot lane-dot--up"></span>'
              : '<span class="lane-dot lane-dot--down"></span>';
            return '<span class="nx-badge nx-badge--muted">' + dot + ' ' + _esc(label) + '</span>';
          }).join(' ')
          + '</div>';
      }

      card.innerHTML = ''
        + '<div class="appliance-card-header">'
        + '  <span class="appliance-hostname">' + _esc(a.hostname || '—') + '</span>'
        + '  ' + enrolledBadge
        + '</div>'
        + '<div class="appliance-tools">' + (toolPills || '<span class="text-muted">No tools</span>') + '</div>'
        + lanes;

      grid.appendChild(card);
    });
  }

  /* ── Dispatch jobs ── */

  function loadDispatchJobs() {
    return _get('/api/fleet/dispatch/jobs').then(function (data) {
      _dispatchJobs = data.jobs || [];
      renderDispatchLog();
      return data;
    }).catch(function (err) {
      console.error('[Nexus] load dispatch jobs error:', err);
    });
  }

  function dispatchCommand(nodeId, tool, argsStr, timeout) {
    var payload = {
      node_id: parseInt(nodeId, 10),
      tool: tool,
      args: argsStr ? { raw: argsStr } : {},
    };
    if (timeout) payload.timeout = timeout;
    return _post('/api/fleet/dispatch', payload).then(function (data) {
      if (data.error) {
        console.error('[Nexus] dispatch error:', data.error);
      }
      loadDispatchJobs();
      return data;
    }).catch(function (err) {
      console.error('[Nexus] dispatch error:', err);
    });
  }

  function cancelDispatch(jobId) {
    if (!jobId) return Promise.resolve();
    return _post('/api/fleet/dispatch/' + encodeURIComponent(jobId) + '/stop', {}).then(function (data) {
      if (data.error) {
        console.error('[Nexus] cancel dispatch error:', data.error);
      }
      loadDispatchJobs();
      return data;
    }).catch(function (err) {
      console.error('[Nexus] cancel dispatch error:', err);
    });
  }

  function renderDispatchLog() {
    var tbody = document.querySelector('#dispatch-log tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    _dispatchJobs.forEach(function (j) {
      var tr = document.createElement('tr');

      var statusClass = 'nx-badge--muted';
      if (j.status === 'running') statusClass = 'nx-badge--success';
      else if (j.status === 'failed' || j.status === 'error') statusClass = 'nx-badge--danger';
      else if (j.status === 'queued' || j.status === 'pending') statusClass = 'nx-badge--warn';
      else if (j.status === 'completed' || j.status === 'complete' || j.status === 'done') statusClass = 'nx-badge--success';
      else if (j.status === 'cancelled') statusClass = 'nx-badge--danger';

      var actions = '';
      if (j.status === 'running' || j.status === 'queued' || j.status === 'pending') {
        actions = '<button class="nx-btn nx-btn--sm nx-btn--danger" '
          + 'onclick="NX.cancelDispatch(\'' + _esc(j.id) + '\')">Cancel</button>';
      }

      tr.innerHTML = ''
        + '<td>' + _esc(j.id || '—') + '</td>'
        + '<td>' + _esc(j.hostname || j.node_id || '—') + '</td>'
        + '<td>' + _esc(j.tool || '—') + '</td>'
        + '<td><span class="nx-badge ' + statusClass + '">' + _esc(j.status || '—') + '</span></td>'
        + '<td>' + _esc(j.started_at || j.started || '—') + '</td>'
        + '<td>' + _esc(j.finished_at || j.finished || '—') + '</td>'
        + '<td>' + actions + '</td>';

      tbody.appendChild(tr);
    });
  }

  /* ── Dispatch form ── */

  function _populateNodeSelect() {
    var sel = document.getElementById('dispatch-node');
    if (!sel) return;

    var current = sel.value;
    sel.innerHTML = '<option value="">Select node...</option>';
    _appliances.forEach(function (a) {
      var opt = document.createElement('option');
      opt.value = a.id;
      opt.textContent = a.hostname || ('Node ' + a.id);
      sel.appendChild(opt);
    });
    if (current) sel.value = current;
  }

  function _initDispatchForm() {
    var form = document.getElementById('dispatch-form');
    if (!form) return;

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var nodeId = document.getElementById('dispatch-node').value;
      var tool = document.getElementById('dispatch-tool').value;
      var argsStr = (document.getElementById('dispatch-args').value || '').trim();
      var timeout = parseInt(document.getElementById('dispatch-timeout').value, 10) || 0;

      if (!nodeId || !tool) {
        console.warn('[Nexus] dispatch form: node and tool required');
        return;
      }

      dispatchCommand(nodeId, tool, argsStr, timeout);
    });
  }

  /* ── SocketIO ── */

  function _bindSocket() {
    var sock = NX.socket;
    if (!sock) return;

    sock.on('dispatch_update', function (data) {
      if (!data || !data.id) { loadDispatchJobs(); return; }
      var found = false;
      for (var i = 0; i < _dispatchJobs.length; i++) {
        if (_dispatchJobs[i].id === data.id) {
          Object.assign(_dispatchJobs[i], data);
          found = true;
          break;
        }
      }
      if (!found) {
        _dispatchJobs.unshift(data);
      }
      renderDispatchLog();
    });
  }

  /* ── Utilities ── */

  function _esc(s) {
    if (s === null || s === undefined) return '';
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(String(s)));
    return d.innerHTML;
  }

  /* ── Init ── */

  function initDispatch() {
    _initDispatchForm();
    _bindSocket();
    loadAppliances();
    loadDispatchJobs();

    setInterval(function () {
      loadAppliances();
      loadDispatchJobs();
    }, 15000);
  }

  /* ── Public API ── */
  NX.loadAppliances = loadAppliances;
  NX.loadDispatchJobs = loadDispatchJobs;
  NX.dispatchCommand = dispatchCommand;
  NX.cancelDispatch = cancelDispatch;
  NX.renderApplianceCards = renderApplianceCards;
  NX.renderDispatchLog = renderDispatchLog;
  NX.initDispatch = initDispatch;

})();
