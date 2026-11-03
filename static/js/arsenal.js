/* Nexus v2.3 — Arsenal client
 * Version matrix, remote operations, SocketIO streaming.
 */
(function () {
  'use strict';

  var _matrix = null;
  var _operations = [];
  var _nodes = [];
  var _credentials = {};
  var _formVisible = false;
  var _socket = null;
  var _activeOutputs = {};

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

  // ── Version Matrix ────────────────────────────────────────────────

  function _renderMatrix(data) {
    _matrix = data;
    var head = _$('matrixHead');
    var body = _$('matrixBody');
    var empty = _$('matrixEmpty');
    var refresh = _$('lastRefresh');
    var summary = _$('arsenalSummary');

    if (!data || !data.nodes || Object.keys(data.nodes).length === 0) {
      if (head) head.innerHTML = '';
      if (body) body.innerHTML = '';
      if (empty) empty.style.display = '';
      return;
    }
    if (empty) empty.style.display = 'none';

    var toolIds = data.registry || [];
    var latest = data.latest || {};

    // Header row
    var hRow = '<tr><th class="matrix-node-header">Node</th>';
    for (var i = 0; i < toolIds.length; i++) {
      var tid = toolIds[i];
      var latestVer = latest[tid] || '';
      hRow += '<th class="matrix-tool-header" title="Latest: ' + _esc(latestVer) + '">' + _esc(tid) + '</th>';
    }
    hRow += '</tr>';
    if (head) head.innerHTML = hRow;

    // Body rows
    var html = '';
    var hostnames = Object.keys(data.nodes).sort();
    for (var h = 0; h < hostnames.length; h++) {
      var hostname = hostnames[h];
      var node = data.nodes[hostname];
      var statusCls = node.status === 'online' ? 'matrix-node-online' : 'matrix-node-offline';
      html += '<tr><td class="matrix-node-cell ' + statusCls + '">' + _esc(hostname) + '</td>';
      var tools = node.tools || {};
      for (var t = 0; t < toolIds.length; t++) {
        var tId = toolIds[t];
        var cell = tools[tId];
        if (cell) {
          var cls = 'vcell vcell-' + (cell.status || 'unknown');
          var title = cell.local_version + (cell.latest_version ? ' / ' + cell.latest_version : '');
          html += '<td class="matrix-cell"><span class="' + cls + '" title="' + _esc(title) + '">' + _esc(cell.local_version || '-') + '</span></td>';
        } else {
          html += '<td class="matrix-cell"><span class="vcell vcell-none" title="Not installed">-</span></td>';
        }
      }
      html += '</tr>';
    }
    if (body) body.innerHTML = html;

    // Refresh time
    if (refresh) {
      refresh.textContent = data.last_refresh ? 'Updated ' + _relTime(data.last_refresh) : '';
    }

    // Summary
    if (summary && data.nodes) {
      var total = 0, current = 0, behind = 0;
      for (var key in data.nodes) {
        var nt = data.nodes[key].tools || {};
        for (var tk in nt) {
          total++;
          if (nt[tk].status === 'current') current++;
          else if (nt[tk].status === 'behind') behind++;
        }
      }
      summary.textContent = total + ' instances, ' + current + ' current' + (behind ? ', ' + behind + ' behind' : '');
    }
  }

  // ── Operations ────────────────────────────────────────────────────

  function _loadOperations() {
    _json('/api/arsenal/operations?limit=50').then(function (d) {
      if (d.operations) {
        _operations = d.operations;
        _renderOperations();
      }
    });
  }

  function _renderOperations() {
    var container = _$('opsContainer');
    var empty = _$('opsEmpty');
    if (!container) return;

    if (!_operations.length) {
      container.innerHTML = '<div class="arsenal-ops-empty">No operations yet</div>';
      return;
    }

    var html = '';
    for (var i = 0; i < _operations.length; i++) {
      var op = _operations[i];
      var statusCls = 'op-' + (op.status || 'pending');
      var icon = op.status === 'completed' ? 'check' : op.status === 'running' ? 'spinner' : op.status === 'failed' ? 'x' : 'clock';
      html += '<div class="op-card ' + statusCls + '" data-id="' + op.id + '">';
      html += '<div class="op-card-left">';
      html += '<span class="op-icon op-icon-' + icon + '"></span>';
      html += '<div class="op-info">';
      html += '<span class="op-title">' + _esc(op.operation) + ' <strong>' + _esc(op.tool_name) + '</strong></span>';
      html += '<span class="op-meta">Node #' + op.node_id + ' &middot; ' + _relTime(op.created_at) + '</span>';
      html += '</div></div>';
      html += '<div class="op-card-right">';
      html += '<span class="op-status-badge">' + _esc(op.status) + '</span>';
      if (op.status === 'running') {
        html += '<button class="btn btn-xs btn-danger" onclick="NXA.cancelOp(' + op.id + ')">Cancel</button>';
      } else if (op.output) {
        html += '<button class="btn btn-xs btn-muted" onclick="NXA.viewOutput(' + op.id + ')">Output</button>';
      }
      html += '</div></div>';
    }
    container.innerHTML = html;
  }

  function _addOrUpdateOp(opData) {
    var found = false;
    for (var i = 0; i < _operations.length; i++) {
      if (_operations[i].id === opData.db_id || _operations[i].id === opData.id) {
        _operations[i].status = opData.status || _operations[i].status;
        if (opData.output !== undefined) _operations[i].output = opData.output;
        if (opData.started_at) _operations[i].started_at = opData.started_at;
        if (opData.finished_at) _operations[i].finished_at = opData.finished_at;
        found = true;
        break;
      }
    }
    if (!found && opData.db_id) {
      _operations.unshift({
        id: opData.db_id,
        node_id: opData.node_id,
        operation: opData.operation,
        tool_name: opData.tool_name,
        status: opData.status || 'pending',
        output: opData.output || '',
        created_at: new Date().toISOString(),
      });
    }
    _renderOperations();
  }

  // ── Run Form ──────────────────────────────────────────────────────

  function _populateRunForm() {
    _json('/api/fleet').then(function (d) {
      _nodes = (d.nodes || []).filter(function (n) { return n.status === 'online'; });
      var sel = _$('runNode');
      if (!sel) return;
      sel.innerHTML = '';
      for (var i = 0; i < _nodes.length; i++) {
        var n = _nodes[i];
        var opt = document.createElement('option');
        opt.value = n.id;
        opt.textContent = n.display_name || n.hostname;
        sel.appendChild(opt);
      }
      if (_nodes.length) _loadCredentials(_nodes[0].id);
    });

    var toolSel = _$('runTool');
    if (toolSel && _matrix && _matrix.registry) {
      toolSel.innerHTML = '';
      for (var t = 0; t < _matrix.registry.length; t++) {
        var opt = document.createElement('option');
        opt.value = _matrix.registry[t];
        opt.textContent = _matrix.registry[t];
        toolSel.appendChild(opt);
      }
    }

    var nodeSel = _$('runNode');
    if (nodeSel) {
      nodeSel.onchange = function () { _loadCredentials(parseInt(this.value)); };
    }
  }

  function _loadCredentials(nodeId) {
    _json('/api/credentials/' + nodeId).then(function (d) {
      var creds = d.credentials || [];
      var sel = _$('runCred');
      if (!sel) return;
      sel.innerHTML = '';
      for (var i = 0; i < creds.length; i++) {
        var c = creds[i];
        var opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = c.label + ' (' + c.username + ')';
        sel.appendChild(opt);
      }
    });
  }

  // ── Socket.IO ─────────────────────────────────────────────────────

  function _initSocket() {
    if (typeof io === 'undefined') return;
    _socket = io({ transports: ['websocket', 'polling'] });

    _socket.on('version_matrix', function (data) {
      _renderMatrix(data);
    });

    _socket.on('arsenal_started', function (data) {
      _addOrUpdateOp({ db_id: data.db_id, node_id: data.node_id,
        operation: data.operation, tool_name: data.tool_name, status: 'running' });
      _activeOutputs[data.op_id] = '';
    });

    _socket.on('arsenal_output', function (data) {
      if (data.op_id && _activeOutputs[data.op_id] !== undefined) {
        _activeOutputs[data.op_id] += data.chunk;
      }
    });

    _socket.on('arsenal_complete', function (data) {
      _addOrUpdateOp({ db_id: data.db_id, status: data.status, output: data.output });
      delete _activeOutputs[data.op_id];
    });
  }

  // ── Public API ────────────────────────────────────────────────────

  function refreshVersions() {
    var btn = _$('refreshVersionsBtn');
    if (btn) { btn.disabled = true; btn.textContent = 'Refreshing...'; }
    _json('/api/arsenal/versions/refresh', { method: 'POST' }).then(function () {
      setTimeout(function () {
        _json('/api/arsenal/versions').then(function (d) {
          _renderMatrix(d);
          if (btn) { btn.disabled = false; btn.textContent = 'Refresh Versions'; }
        });
      }, 3000);
    });
  }

  function toggleRunForm() {
    _formVisible = !_formVisible;
    var form = _$('runForm');
    if (form) form.style.display = _formVisible ? '' : 'none';
    if (_formVisible) _populateRunForm();
  }

  function runOperation() {
    var nodeId = parseInt((_$('runNode') || {}).value);
    var toolName = (_$('runTool') || {}).value;
    var op = (_$('runOp') || {}).value;
    var credId = parseInt((_$('runCred') || {}).value);

    if (!nodeId || !toolName || !op || !credId) return;

    _json('/api/arsenal/run', {
      method: 'POST',
      body: { node_id: nodeId, tool_name: toolName, operation: op, credential_id: credId }
    }).then(function (r) {
      if (r.ok) {
        toggleRunForm();
        _addOrUpdateOp({ db_id: r.db_id, node_id: nodeId,
          operation: op, tool_name: toolName, status: 'pending' });
      }
    });
  }

  function cancelOp(dbId) {
    _json('/api/arsenal/cancel/' + dbId, { method: 'POST' }).then(function (r) {
      if (r.ok) {
        _addOrUpdateOp({ db_id: dbId, status: 'cancelled' });
      }
    });
  }

  function viewOutput(dbId) {
    _json('/api/arsenal/operations/' + dbId).then(function (d) {
      var op = d.operation || d;
      var modal = _$('opModal');
      var title = _$('opModalTitle');
      var output = _$('opModalOutput');
      if (modal) modal.style.display = '';
      if (title) title.textContent = (op.operation || '') + ' ' + (op.tool_name || '') + ' — Node #' + (op.node_id || '');
      if (output) output.textContent = op.output || '(no output)';
    });
  }

  function closeOpModal(e) {
    if (e && e.target !== _$('opModal')) return;
    var modal = _$('opModal');
    if (modal) modal.style.display = 'none';
  }

  // ── Init ──────────────────────────────────────────────────────────

  function _init() {
    _initSocket();
    _json('/api/arsenal/versions').then(function (d) {
      _renderMatrix(d);
    });
    _loadOperations();
  }

  document.addEventListener('DOMContentLoaded', _init);

  // ── Export ─────────────────────────────────────────────────────────

  window.NXA = {
    refreshVersions: refreshVersions,
    toggleRunForm:   toggleRunForm,
    runOperation:    runOperation,
    cancelOp:        cancelOp,
    viewOutput:      viewOutput,
    closeOpModal:    closeOpModal,
  };

})();
