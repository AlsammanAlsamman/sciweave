/* SciWeave dashboard app. Plain ES5 + D3 v7 (vendored inline), no build step.
 * Data: window.SCIWEAVE_DATA (static export) or GET /api/graph (serve mode).
 * Views: force (group halos) · radial (tree + bundled provenance) · lineage (layered DAG).
 */
(function () {
  'use strict';

  var LIVE = !!window.SCIWEAVE_LIVE;
  var DATA = null;
  var byId = {};
  var inEdges = {}, outEdges = {};
  var vizEl = document.getElementById('viz');
  var panelEl = document.getElementById('panel');
  var tooltipEl = document.getElementById('tooltip');

  function load(key, dflt) { try { var v = localStorage.getItem('sciweave-' + key); return v === null ? dflt : JSON.parse(v); } catch (e) { return dflt; } }
  function store(key, v) { try { localStorage.setItem('sciweave-' + key, JSON.stringify(v)); } catch (e) { /* private mode */ } }

  var state = {
    page: 'network',
    view: load('view', 'force'),
    groupBy: load('groupBy', null),
    showRefs: load('showRefs', true),
    showCode: load('showCode', true),
    showParams: load('showParams', false),
    showLegend: load('showLegend', true),
    hiddenTypes: load('hiddenTypes', []),
    showHidden: load('showHidden', false),
    selected: null,
    selectedEdge: null,
    search: '',
    askHighlight: null,
    zoom: {}
  };

  // ------------------------------------------------------------- icons ----
  // 24x24 stroke icons, one per node type (drawn inside the node circle).
  var ICONS = {
    resource: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z',
    raw: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
    input: 'M14 3H6v18h12V7zM14 3v4h4M8 14h7M12 11l3 3-3 3',
    pipeline: 'M3 4h7v6H3zM14 14h7v6h-7zM6.5 10v3a4 4 0 0 0 4 4H14',
    step: 'M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM12 2v3.5M12 18.5V22M2 12h3.5M18.5 12H22M4.9 4.9l2.5 2.5M16.6 16.6l2.5 2.5M4.9 19.1l2.5-2.5M16.6 7.4l2.5-2.5',
    script: 'M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16',
    result: 'M14 3H6v18h12V7zM14 3v4h4M9 13h6M9 17h6',
    table: 'M3 5h18v14H3zM3 10h18M3 15h18M9 5v14M15 5v14',
    figure: 'M4 3v17h17M8 16v-5M12 16V7M16 16v-7M20 16v-3',
    supplement: 'M20 11l-8.5 8.5a5 5 0 0 1-7-7L13 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L14 7',
    section: 'M4 5h16M4 10h16M4 15h10M4 20h13',
    article: 'M3 4h6a3 3 0 0 1 3 3v14a2 2 0 0 0-2-2H3zM21 4h-6a3 3 0 0 0-3 3v14a2 2 0 0 1 2-2h7z',
    note: 'M4 4h16v11l-5 5H4zM15 20v-5h5'
  };

  var BRANCH_ICON = 'M6 3v12M18 3a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM6 15a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM18 9c0 5-6 4-9.5 7.5';
  function branchSvg(size, col) {
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" style="vertical-align:-3px"><path d="' + BRANCH_ICON +
      '" fill="none" stroke="' + col + '" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  }
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function catOf(n) { return (DATA.types[n.type] || {}).category || 'notes'; }
  function catColor(cat) { return cssVar('--cat-' + cat) || '#888'; }
  function iconInk(fill) {
    var c = d3.rgb(fill);
    var lum = (0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b) / 255;
    return lum > 0.62 ? '#111111' : '#ffffff';
  }
  function radius(n) {
    if (n.type === 'article') return 20;
    if (n.type === 'pipeline') return 17;
    if (n.type === 'note') return 11;
    return 14;
  }
  function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function trunc(s, n) { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function fmtParams(p) { return Object.keys(p || {}).map(function (k) { return k + '=' + (Array.isArray(p[k]) ? p[k].join(',') : p[k]); }).join(', '); }
  function hasParams(e) { return e.params && Object.keys(e.params).length > 0; }
  function when(ts) { return ts ? ts.replace('T', ' ').slice(0, 16) : ''; }
  function st(id) { return (DATA.status[id] || { state: 'ok', reasons: [] }); }

  // -------------------------------------------------------------- data ----
  function index() {
    byId = {}; inEdges = {}; outEdges = {};
    DATA.nodes.forEach(function (n) { byId[n.id] = n; inEdges[n.id] = []; outEdges[n.id] = []; });
    DATA.edges.forEach(function (e) {
      if (!byId[e.source] || !byId[e.target]) return;
      outEdges[e.source].push(e); inEdges[e.target].push(e);
    });
    document.getElementById('proj-name').textContent = '· ' + DATA.project.name;
    document.title = DATA.project.name + ' · SciWeave';
  }

  // provenance walk; documents/related links are informational and not followed
  function walk(id, dir) {
    var seen = {}, out = [], stack = [id];
    while (stack.length) {
      var cur = stack.pop();
      (dir === 'up' ? inEdges[cur] : outEdges[cur]).forEach(function (e) {
        if (e.rel === 'documents' || e.rel === 'related' || e.rel === 'variant') return;
        var nxt = dir === 'up' ? e.source : e.target;
        if (!seen[nxt]) { seen[nxt] = true; out.push(nxt); stack.push(nxt); }
      });
    }
    return out;
  }

  function articleOf(n) {
    if (n.type === 'article') return n.label;
    var down = walk(n.id, 'down');
    for (var i = 0; i < down.length; i++) if (byId[down[i]].type === 'article') return byId[down[i]].label;
    return '(no article)';
  }

  function groupKey(n) {
    switch (state.groupBy) {
      case 'type': return n.type;
      case 'group': return n.groups[0] || '(ungrouped)';
      case 'step': return n.step && (DATA.steps || {})[n.step] ? n.step : '(no step)';
      case 'article': return articleOf(n);
      case 'state': return st(n.id).state;
      case 'recency': return recencyOf(n);
      default: return catOf(n);
    }
  }

  function ageDays(ts) { return ts ? (Date.now() - new Date(ts).getTime()) / 864e5 : 1e9; }
  function recencyOf(n) {
    var d = ageDays(n.updated);
    return d < 1 ? 'updated today' : d < 7 ? 'this week' : d < 31 ? 'this month' : 'older';
  }

  function stepOrder(key) { var s = (DATA.steps || {})[key]; return s ? (s.order || 0) : 1e6; }
  function stepLabel(key) {
    var s = (DATA.steps || {})[key];
    return s ? s.order + '. ' + s.label : 'no step yet';
  }
  function groupLabel(key) { return state.groupBy === 'step' ? stepLabel(key) : key; }
  function sortGroups(keys) {
    if (state.groupBy === 'step') return keys.sort(function (a, b) { return stepOrder(a) - stepOrder(b) || d3.ascending(a, b); });
    var FLOW = ['sources', 'resources', 'process', 'outputs', 'writing', 'notes', 'updated today', 'this week', 'this month', 'older'];
    return keys.sort(function (a, b) {
      var ia = FLOW.indexOf(a), ib = FLOW.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || d3.ascending(a, b);
    });
  }

  function isHiddenRef(n) { return !state.showRefs && n.mode === 'ref' && catOf(n) === 'outputs'; }

  // focus: objects hidden with their analysis (or by hand) leave the network unless "hidden items" is on
  function isFocusHidden(n) { return !state.showHidden && !!(DATA.hidden || {})[n.id]; }

  function visible() {
    var nodes = DATA.nodes.filter(function (n) { return !isHiddenRef(n) && !isFocusHidden(n) && state.hiddenTypes.indexOf(n.type) < 0; });
    var ok = {}; nodes.forEach(function (n) { ok[n.id] = true; });
    var edges = DATA.edges.filter(function (e) {
      return ok[e.source] && ok[e.target] && (state.showCode || e.rel !== 'code');
    });
    return { nodes: nodes, edges: edges };
  }

  // ----------------------------------------------------------- tooltip ----
  function showTip(evt, html) {
    tooltipEl.innerHTML = html; tooltipEl.style.display = 'block';
    var x = evt.clientX + 14, y = evt.clientY + 14;
    var w = tooltipEl.offsetWidth, h = tooltipEl.offsetHeight;
    if (x + w > window.innerWidth - 8) x = evt.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = evt.clientY - h - 14;
    tooltipEl.style.left = x + 'px'; tooltipEl.style.top = y + 'px';
  }
  function hideTip() { tooltipEl.style.display = 'none'; }
  function nodeTip(n) {
    var s = st(n.id);
    return '<div class="t">' + esc(n.id) + ' · ' + esc(n.label) + '</div>' +
      '<div class="s">' + esc(n.type) + ' · ' + esc(n.mode) + (n.current_version ? ' · v' + n.current_version : '') +
      (n.final_version ? ' · final v' + n.final_version : '') + '</div>' +
      (s.state !== 'ok' ? '<div class="s">' + esc(s.state) + ': ' + esc((s.reasons || [])[0] || '') + '</div>' : '') +
      (n.description ? '<div class="s">' + esc(trunc(n.description, 160)) + '</div>' : '');
  }
  function edgeTip(e) {
    if (e.rel === 'variant') return '<div class="t">' + esc(e.target) + ' is a branch of ' + esc(e.source) + '</div><div class="s">' +
      esc(e.label || '') + '</div>' + (e.note ? '<div class="s why">why: ' + esc(e.note) + '</div>' : '');
    var sa = (byId[e.source] || {}).step, sb = (byId[e.target] || {}).step;
    var stepLine = sa !== sb && (sa || sb) ? '<div class="s">between steps: ' + esc(stepLabel(sa)) + ' \u2192 ' + esc(stepLabel(sb)) + '</div>' : '';
    var html = '<div class="t">' + esc(e.source) + ' \u2192 ' + esc(e.target) + '</div>' + stepLine + '<div class="s">' + esc(e.rel) +
      (e.label ? ' as “' + esc(e.label) + '”' : '') + (e.stale ? ' · stale' : '') + '</div>';
    if (hasParams(e)) html += '<table class="params-table">' + Object.keys(e.params).map(function (k) {
      return '<tr><td>' + esc(k) + '</td><td>' + esc(e.params[k]) + '</td></tr>'; }).join('') + '</table>';
    else html += '<div class="s">no result-changing parameters</div>';
    if (e.script) html += '<div class="s">script ' + esc(e.script) + '@v' + esc(e.script_version) + '</div>';
    if (e.command) html += '<div class="s mono">' + esc(trunc(e.command, 140)) + '</div>';
    return html;
  }

  // ---------------------------------------------------- shared drawing ----
  function defs(svg) {
    var d = svg.append('defs');
    [['params', '--edge-params'], ['plain', '--edge-plain']].forEach(function (p) {
      [['tip', 8], ['off', 30]].forEach(function (m) {
        d.append('marker').attr('id', 'arrow-' + p[0] + '-' + m[0]).attr('viewBox', '0 -5 10 10')
          .attr('refX', m[1]).attr('refY', 0).attr('markerWidth', 7).attr('markerHeight', 7)
          .attr('markerUnits', 'userSpaceOnUse').attr('orient', 'auto')
          .append('path').attr('d', 'M0,-4L9,0L0,4Z').attr('fill', cssVar(p[1]));
      });
    });
  }

  function edgeClass(e) {
    if (e.rel === 'variant') return 'edge variant';
    return 'edge ' + (hasParams(e) ? 'params' : 'plain') + (e.rel === 'code' ? ' code' : '') + (e.stale ? ' stale' : '');
  }
  function marker(e, kind) { if (e.rel === 'variant') return null; return 'url(#arrow-' + (hasParams(e) ? 'params' : 'plain') + '-' + kind + ')'; }

  function drawNodes(layer, nodes) {
    var g = layer.selectAll('g.node-g').data(nodes, function (d) { return d.id; }).join('g')
      .attr('class', function (d) { return 'node-g' + (d.mode === 'ref' ? ' ref' : '') + (d.id === state.selected ? ' selected' : ''); })
      .attr('data-id', function (d) { return d.id; });
    g.each(function (d) {
      var el = d3.select(this), r = radius(d), s = st(d.id), fill = catColor(catOf(d));
      if (s.stale) el.append('circle').attr('class', 'ring-stale').attr('r', r + 4.5);
      else if (s.missing) el.append('circle').attr('class', 'ring-missing').attr('r', r + 4.5);
      else if (!d.path && d.type !== 'resource') el.append('circle').attr('class', 'ring-nofile').attr('r', r + 4.5);

      // large data the user skipped in "save all": its own outer red dashed circle (shown even when stale / modified)
      if (d.organize_skip && !(DATA.organized || {})[d.id]) el.append('circle').attr('class', 'ring-skip').attr('r', r + 9);
      if (isBig(d)) el.append('text').attr('class', 'big-badge').attr('x', -r * 0.95).attr('y', r * 0.95).attr('font-size', 13).text('\ud83d\udc18');
      else if (s.modified) el.append('circle').attr('class', 'ring-modified').attr('r', r + 4.5);
      el.append('circle').attr('class', 'body').attr('r', r).attr('fill', fill).attr('stroke', fill);
      var k = (r * 1.15) / 24;
      el.append('path').attr('class', 'icon').attr('d', ICONS[d.type] || ICONS.result)
        .attr('transform', 'translate(' + (-12 * k) + ',' + (-12 * k) + ') scale(' + k + ')')
        .attr('vector-effect', 'non-scaling-stroke').style('stroke', iconInk(fill));
      var b = d.branch || null;
      if (b && b.status === 'abandoned') el.classed('abandoned', true);
      if (b && b.status !== 'main') {
        var bg = el.append('g').attr('transform', 'translate(' + (-r * 0.95) + ',' + (r * 0.55) + ')');
        bg.append('circle').attr('r', 7.5).attr('fill', cssVar('--surface')).attr('stroke', cssVar('--text-2')).attr('stroke-width', 1.2);
        bg.append('path').attr('d', BRANCH_ICON).attr('transform', 'translate(-5,-5) scale(0.42)').attr('fill', 'none')
          .attr('stroke', cssVar('--text-2')).attr('stroke-width', 2.6).attr('stroke-linecap', 'round');
      }
      if (state.newIds && state.newIds[d.id] && Date.now() - state.newIds[d.id] < 6000)
        el.append('circle').attr('class', 'ring-new').attr('r', r + 6);
      if (ageDays(d.updated) < 1) el.append('circle').attr('class', 'recent-dot').attr('r', 4.5).attr('cx', -r * 0.74).attr('cy', -r * 0.74);
      if (d.final_version) el.append('text').attr('class', 'star').attr('x', r * 0.55).attr('y', -r * 0.55)
        .attr('font-size', 13).attr('fill', cssVar('--final')).text('★');
    });
    g.on('mouseenter', function (evt, d) { showTip(evt, nodeTip(d)); })
      .on('mousemove', function (evt, d) { showTip(evt, nodeTip(d)); })
      .on('mouseleave', hideTip)
      .on('click', function (evt, d) {
        evt.stopPropagation();
        // selecting opens the side panel, which narrows the network and shifts every node, so the
        // browser's own dblclick would miss: wait briefly, and a second click on the node = copy
        if (pendingClick && pendingClick.id === d.id) {
          clearTimeout(pendingClick.timer); pendingClick = null;
          organizeNode(d.id);
          return;
        }
        if (pendingClick) clearTimeout(pendingClick.timer);
        var id = d.id;
        pendingClick = { id: id, timer: setTimeout(function () { pendingClick = null; select(id); }, 260) };
      });
    g.each(function (d) {
      var o = (DATA.organized || {})[d.id];
      if (!o) return;
      var r = radius(d), col = o.state === 'ok' ? cssVar('--edge-plain') : cssVar('--stale');
      var bg = d3.select(this).append('g').attr('class', 'org-badge').attr('transform', 'translate(' + (r * 0.78) + ',' + (r * 0.62) + ')');
      bg.append('circle').attr('r', 6.5).attr('fill', cssVar('--surface')).attr('stroke', col).attr('stroke-width', 1.4);
      bg.append('path').attr('d', 'M-3.6,-1.6h2.4l0.9,-1.1h3.9v4.9h-7.2z').attr('fill', col);
    });
    return g;
  }

  // ------------------------------------------------ organized copies ----
  // Double-click (or "Save to destination") copies the object to the project's destination,
  // into <NN_step>/<group>/ so the folders mirror the network. Copies run on the server in
  // the background; we poll api/jobs until they finish.
  var jobTimer = null, pendingClick = null;
  function organizeNode(id) {
    if (!LIVE) { toast('Copying needs sciweave serve / open'); return; }
    var n = byId[id];
    if (!n || !n.path) { toast((n ? n.id : id) + ' has no file to copy'); return; }
    var go = function () {
      if ((DATA.organized || {})[id]) return recopyAsk(n).then(function (ok) { if (ok) post('api/organize', { node: id }).then(function (r) { toast(r.message); pollJobs(); }).catch(function (e) { toast(e.message); }); });
      var run = function () {
        post('api/organize', { node: id }).then(function (r) { toast(r.message); pollJobs(); })
          .catch(function (e) { toast(e.message); });
      };
      if (!isBig(n)) return run();
      askYesNo('\ud83d\udc18 Large data', bigText(n), 'Yes, copy it', 'Not now').then(function (ok) { if (ok) run(); });
    };
    if (!DATA.destination) {
      var d = prompt('Where should the organized copies of this project live?\n(a folder, e.g. D:\\my_study)', '');
      if (!d) return;
      post('api/destination', { path: d }).then(function () { return refresh(true); }).then(go).catch(function (e) { toast(e.message); });
      return;
    }
    go();
  }
  var jobsSeen = {}, jobsPrimed = false;  // jobs that finished before this page loaded stay quiet
  var JOBS_NOW = {};  // node id -> its latest job (for the progress bars)
  var RUN = { queued: 1, copying: 1, verifying: 1 };
  function fmtDur(sec) {
    if (!isFinite(sec) || sec < 0) return '';
    if (sec < 60) return Math.max(1, Math.round(sec)) + ' s';
    if (sec < 3600) return Math.round(sec / 60) + ' min';
    return (sec / 3600).toFixed(1) + ' h';
  }
  function jobStats(j) {
    var t0 = j.started ? Date.parse(j.started) / 1000 : null, el = t0 ? (j.heartbeat - t0) : 0;
    var rate = el > 1 && j.bytes_done ? j.bytes_done / el : 0;  // bytes of work per second (copy + check)
    var left = rate ? (j.bytes_total - j.bytes_done) / rate : NaN;
    var pct = j.bytes_total ? Math.min(100, 100 * j.bytes_done / j.bytes_total) : 0;
    return { pct: pct, rate: rate, left: left };
  }
  function jobLine(j, compact) {
    if (!j) return '';
    if (j.state === 'interrupted')
      return '<div class="jl jl-bad">⚠ ' + (j.kind === 'verify' ? 'verification' : 'copy') + ' interrupted (the process stopped) — press Save to retry; leftovers are cleared first</div>';
    if (j.state === 'error') return '<div class="jl jl-bad">⚠ ' + esc(j.error || 'failed') + '</div>';
    if (!RUN[j.state]) return '';
    if (j.state === 'queued') return '<div class="jl"><div class="pbar"><span style="width:0"></span></div><span class="muted">queued — waiting for a free slot (2 copies at a time)</span></div>';
    var x = jobStats(j), half = j.bytes_total / 2;
    var phase = j.kind === 'verify' ? 'verifying (SHA-256)' : (j.phase === 'checking' ? 'checking the copy (SHA-256)' : 'copying');
    var data = j.kind === 'verify' ? bigSize(j.bytes_done) + ' of ' + bigSize(j.bytes_total)
      : bigSize(Math.min(j.bytes_done, half)) + ' of ' + bigSize(half) + (j.bytes_done > half ? ' copied, checking' : '');
    return '<div class="jl"><div class="pbar"><span style="width:' + x.pct.toFixed(1) + '%"></span></div><span>' + phase + ' ' + Math.floor(x.pct) + '%' +
      (compact ? '' : ' · ' + data) + (x.rate ? ' · ' + bigSize(x.rate) + '/s' : '') + (isFinite(x.left) ? ' · ~' + fmtDur(x.left) + ' left' : '') + '</span></div>';
  }
  function paintJobs(jobs) {
    JOBS_NOW = {};
    jobs.forEach(function (j) {
      var cur = JOBS_NOW[j.node];
      if (!cur || (j.created || '') > (cur.created || '')) JOBS_NOW[j.node] = j;
    });
    Object.keys(JOBS_NOW).forEach(function (nid) {
      var j = JOBS_NOW[nid];
      document.querySelectorAll('[data-jobslot="' + nid + '"]').forEach(function (el) { el.innerHTML = jobLine(j, el.dataset.compact === '1'); });
    });
    document.querySelectorAll('[data-jobslot]').forEach(function (el) { if (!JOBS_NOW[el.dataset.jobslot]) el.innerHTML = ''; });
    // overall bar in the project panel
    var run = jobs.filter(function (j) { return RUN[j.state]; });
    var el = document.getElementById('org-jobs');
    if (el) {
      if (!run.length) { el.innerHTML = ''; return; }
      var tot = 0, done = 0, rate = 0, active = 0;
      run.forEach(function (j) { tot += j.bytes_total || 0; done += j.bytes_done || 0; if (j.state !== 'queued') { rate += jobStats(j).rate; active++; } });
      var pct = tot ? 100 * done / tot : 0, left = rate ? (tot - done) / rate : NaN;
      el.innerHTML = '<div class="jl jl-sum"><div class="pbar"><span style="width:' + pct.toFixed(1) + '%"></span></div><span><b>' + active + ' running</b>' +
        (run.length > active ? ', ' + (run.length - active) + ' queued' : '') + ' · ' + Math.floor(pct) + '%' +
        (rate ? ' · ' + bigSize(rate) + '/s' : '') + (isFinite(left) ? ' · ~' + fmtDur(left) + ' left' : '') +
        '</span><span class="muted" style="font-size:11px">copies run in their own processes: closing or restarting the dashboard does not stop them</span></div>';
    }
  }
  function pollJobs() {
    clearTimeout(jobTimer);
    fetch('api/jobs').then(function (r) { return r.json(); }).then(function (jobs) {
      var running = jobs.filter(function (j) { return RUN[j.state]; });
      jobs.forEach(function (j) {
        if (!RUN[j.state] && !jobsSeen[j.id]) {
          jobsSeen[j.id] = true;
          if (!jobsPrimed || j.state === 'interrupted') return;
          toast(j.kind === 'verify' ? (j.state === 'done' ? (j.ok ? '✓ ' : '⚠ ') + j.node + ': ' + j.message : 'Verify of ' + j.node + ' failed: ' + j.error)
            : j.state === 'done' ? j.node + ' copied and checked (SHA-256) → ' + j.path : 'Copy of ' + j.node + ' failed: ' + j.error);
          refresh(true);
        }
      });
      paintJobs(jobs);
      jobsPrimed = true;
      if (running.length) jobTimer = setTimeout(pollJobs, 1000);
    }).catch(function () {});
  }
  // ---- the project actions panel: shown whenever no node is selected (and from the top-bar
  // button): the organized copy (destination) with Save buttons for every object, and project actions ----
  // the folder an object will be copied to (same rule as Project.organized_folder on the server)
  function folderName(t) { return (String(t).trim().replace(/[^\w\-]+/g, '_').replace(/^_+|_+$/g, '') || 'item').slice(0, 60); }
  function destFolder(n) {
    var st = (DATA.steps || {})[n.step || ''];
    var parts = [st ? ('0' + (st.order || 0)).slice(-2) + '_' + folderName(st.label) : '00_No_step'];
    if (n.groups && n.groups.length) parts.push(folderName(((DATA.groups || {})[n.groups[0]] || {}).label || n.groups[0]));
    return parts.join('/');
  }
  function renderProjectPanel() {
    state.showingDest = true; state.showingSuggestions = false; state.selected = null; state.selectedEdge = null;
    var org = DATA.organized || {}, dest = DATA.destination;
    var html = '<div class="head"><div class="title"><h2>Project actions</h2><button class="icon-btn close" id="panel-close" title="Close (the Project actions button reopens it)">\u2715</button></div>' +
      '<div class="muted" style="font-size:12.5px;margin-top:2px">Click a node for its own panel; close it to come back here.</div>' +
      '<div class="actions"><button class="btn" data-sg="round" title="Look for new or changed result files not yet in the network">Check for new results</button>' +
      '<button class="btn" data-gohist="1">Analysis history</button></div>' +
      '<h3 class="pa-h">Organized copy \u00b7 destination</h3>';
    if (!dest) {
      html += '<p class="muted" style="margin:8px 0">Choose the folder where the organized copy of this project should live. ' +
        'Inside it, one folder per step (and per group) will mirror the network.</p>' +
        '<div class="actions"><button class="btn primary" data-dchange="1">Choose destination folder…</button></div></div>';
      panelEl.innerHTML = html; panelEl.classList.add('open'); return;
    }
    var withFile = DATA.nodes.filter(function (n) { return n.path; });
    var todo = withFile.filter(function (n) { return n.type !== 'resource' && !n.organize_skip && (!org[n.id] || org[n.id].state !== 'ok'); });
    var skipped = withFile.filter(function (n) { return n.organize_skip && (!org[n.id] || org[n.id].state !== 'ok'); });
    html += '<div class="mono dest-path">' + esc(dest) + '</div>' +
      '<div class="muted" style="font-size:12.5px;margin-top:4px">' + Object.keys(org).length + ' of ' + withFile.length + ' objects copied. ' +
      'Folders follow the network: one per step, then per group. Tip: double-click a node to save it.</div>' +
      '<div class="actions"><button class="btn" data-dopen="1">Open folder</button><button class="btn" data-dchange="1">Change…</button>' +
      (todo.length ? '<button class="btn primary" data-dall="1" title="Large data (\ud83d\udc18 > 4 GB) is asked one by one">Save all not copied (' + todo.length + ')</button>' : '') +
      (skipped.length ? '<span class="muted" style="font-size:12px;align-self:center">' + skipped.length + ' large skipped</span>' : '') +
      '</div><div id="org-jobs"></div></div>';
    var steps = Object.keys(DATA.steps || {}).sort(function (a, b) { return (DATA.steps[a].order || 0) - (DATA.steps[b].order || 0); });
    steps.push(null);
    steps.forEach(function (sk) {
      var items = DATA.nodes.filter(function (n) { return (n.step || null) === sk && !(DATA.hidden || {})[n.id]; });
      if (!items.length) return;
      var title = sk ? (DATA.steps[sk].order + '. ' + DATA.steps[sk].label) : 'No step';
      html += '<div class="sec"><h3>' + esc(title) + '</h3>' + items.map(function (n) {
        var o = org[n.id], st = n.type === 'resource' ? 'online' : !n.path ? 'nofile' : (o ? o.state : (n.organize_skip ? 'skipped' : 'none'));
        var chip = { skipped: ['large \u00b7 skipped', 'state-missing'], online: ['online \u00b7 nothing to copy', ''], nofile: ['no file \u2014 needs a path', 'state-missing'], none: ['not copied', ''], ok: ['copied', 'org-ok'], source_changed: ['source changed', 'state-stale'],
          copy_missing: ['copy missing', 'state-missing'], source_missing: ['source unreachable', 'state-missing'] }[st] || [st, ''];
        var btns = '';
        if (n.path) btns += '<button class="btn mini' + (st === 'ok' ? '' : ' primary') + '" data-dsave="' + esc(n.id) + '">' + (st === 'none' || st === 'skipped' ? 'Save' : st === 'ok' ? 'Copy again' : 'Update') + '</button>';
        if (o && st !== 'copy_missing') btns += '<button class="btn mini" data-dshow="' + esc(n.id) + '">Show</button>';
        return '<div class="dest-row"><div class="dest-name">' + nidLink(n.id) + ' ' + esc(trunc(n.label, 34)) +
          (isBig(n) ? ' <span class="big" title="Large data: ' + bigSize(nodeSize(n)) + ' (asked before copying)">\ud83d\udc18 ' + bigSize(nodeSize(n)) + '</span>' : '') +
          '<div class="muted dest-sub">' + esc(o ? o.path : (n.path ? '\u2192 ' + destFolder(n) + '/' : (n.meta && n.meta.url) || 'no file to copy')) + '</div></div>' +
          '<span class="chip ' + chip[1] + '">' + chip[0] + '</span><span class="dest-btns">' + btns + '</span></div>' +
          '<div class="dest-job" data-jobslot="' + esc(n.id) + '" data-compact="1">' + jobLine(JOBS_NOW[n.id], true) + '</div>';
      }).join('') + '</div>';
    });
    panelEl.innerHTML = html;
    panelEl.classList.add('open');
    pollJobs();
  }
  document.getElementById('dest-toggle').onclick = function () {
    if (!LIVE) { toast('Project actions need sciweave serve / open'); return; }
    if (state.showingDest && panelEl.classList.contains('open')) { state.showingDest = false; state.projectPanelClosed = true; panelEl.classList.remove('open'); return; }
    state.projectPanelClosed = false;
    if (state.page !== 'network') setPage('network');
    renderProjectPanel();
  };
  function setDestinationPrompt() {
    var d = prompt('Destination folder for the organized copy of this project\n(one folder per step, then per group, will be made inside it)', DATA.destination || '');
    if (d === null) return;
    post('api/destination', { path: d.trim() }).then(function (r) { toast(r.message); return refresh(true); })
      .then(function () { renderProjectPanel(); }).catch(function (e) { toast(e.message); });
  }
  // ---- large data (> 4 GB): marked with an elephant; "save all" asks yes / no for each one ----
  var BIG = 4 * 1024 * 1024 * 1024;
  function nodeSize(n) { var v = n.versions || []; return v.length ? v[v.length - 1].size || 0 : 0; }
  function isBig(n) { return nodeSize(n) > BIG; }
  function bigSize(b) { return b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB' : b >= 1e6 ? Math.round(b / 1e6) + ' MB' : b >= 1e3 ? Math.round(b / 1e3) + ' KB' : b + ' bytes'; }
  function exactSize(b) { return Number(b || 0).toLocaleString() + ' bytes' + (b >= 1e3 ? ' (' + bigSize(b) + ')' : ''); }
  function askYesNo(title, text, yes, no) {
    return new Promise(function (resolve) {
      var bg = document.createElement('div');
      bg.className = 'yn-bg';
      bg.innerHTML = '<div class="yn" role="dialog" aria-modal="true"><div class="yn-title">' + title + '</div><div class="yn-text">' + text + '</div>' +
        '<div class="yn-row"><button class="btn" data-yn="no">' + esc(no) + '</button><button class="btn primary" data-yn="yes">' + esc(yes) + '</button></div></div>';
      document.body.appendChild(bg);
      bg.querySelector('[data-yn="yes"]').focus();
      bg.addEventListener('click', function (e) {
        var b = e.target.closest('[data-yn]'); if (!b) return;
        document.body.removeChild(bg); resolve(b.dataset.yn === 'yes');
      });
    });
  }
  function bigText(n) {
    return '<b>' + esc(n.id) + ' \u00b7 ' + esc(n.label) + '</b> is <b>' + bigSize(nodeSize(n)) + '</b>.<br>It would be copied to <code>' +
      esc(joinDest(destFolder(n)) + (DATA.destination.indexOf('\\') >= 0 ? '\\' : '/')) + '</code>.';
  }
  // already copied: compare sizes (cheap) and ask before copying again
  function recopyAsk(n) {
    return post('api/organize-check', { node: n.id }).then(function (c) {
      var sizes = '<span class="yn-sizes">source <b>' + exactSize(c.source_size) + '</b>, ' + c.source_files + ' file' + (c.source_files === 1 ? '' : 's') +
        '<br>copy&nbsp;&nbsp;&nbsp;<b>' + exactSize(c.copy_size) + '</b>, ' + c.copy_files + ' file' + (c.copy_files === 1 ? '' : 's') + '</span>';
      var sum = c.has_checksum ? 'SHA-256 checksum file next to the copy (verified ' + esc((c.verified_at || '').slice(0, 16).replace('T', ' ')) + ').'
        : 'No checksum file yet (copied before checksums existed): use \u201cVerify copy\u201d to add one.';
      if (c.up_to_date)
        return askYesNo('Already up to date', '<b>' + esc(n.id) + ' \u00b7 ' + esc(n.label) + '</b>: sizes match and the source has not changed since it was copied.<br>' +
          sizes + '<br><span class="muted">' + sum + '</span>', 'Copy again anyway', 'Keep the copy');
      var why = c.state === 'copy_missing' ? 'The copy is missing at the destination.'
        : c.state === 'source_missing' ? 'The source is not reachable right now.'
        : !c.same_size ? 'Source and copy differ in size.' : 'The source changed since it was copied.';
      if (c.state === 'source_missing') { toast(why); return false; }
      return askYesNo('Update the copy?', '<b>' + esc(n.id) + ' \u00b7 ' + esc(n.label) + '</b>: ' + why + '<br>' + sizes +
        '<br><span class="muted">The new copy is checked against the source (SHA-256) before it replaces the old one.</span>', 'Yes, update it', 'Not now');
    }).catch(function (e) { toast(e.message); return false; });
  }
  function saveAll() {
    var org = DATA.organized || {};
    var todo = DATA.nodes.filter(function (n) { return n.path && n.type !== 'resource' && !n.organize_skip && (!org[n.id] || org[n.id].state !== 'ok'); });
    var updates = todo.filter(function (n) { return org[n.id]; });
    todo = todo.filter(function (n) { return !org[n.id]; });
    var small = todo.filter(function (n) { return !isBig(n); }), big = todo.filter(isBig);
    small.forEach(function (n) { post('api/organize', { node: n.id }).catch(function (e) { toast(e.message); }); });
    if (small.length) toast('copying ' + small.length + ' object(s)\u2026');
    // large data: one explicit yes / no each; a "no" is remembered and not asked again
    var i = 0, yes = 0, no = 0, u = 0;
    (function nextUpdate() {
      if (u < updates.length) {
        var un = updates[u++];
        return recopyAsk(un).then(function (ok) {
          return ok ? post('api/organize', { node: un.id }).catch(function (e) { toast(e.message); }) : null;
        }).then(nextUpdate);
      }
      next();
    })();
    function next() {
      if (i >= big.length) {
        if (big.length) toast(yes + ' large copied, ' + no + ' skipped (red circle; Save on its own any time)');
        setTimeout(pollJobs, 500); return refresh(true);
      }
      var n = big[i++];
      askYesNo('\ud83d\udc18 Large data (' + (i) + ' of ' + big.length + ')', bigText(n) + '<br><br><span class="muted">No = skip it; it will not be asked again in \u201cSave all\u201d. It stays marked with a red circle and can be saved on its own.</span>',
        'Yes, copy it', 'No, skip it').then(function (ok) {
        (ok ? (yes++, post('api/organize', { node: n.id })) : (no++, post('api/organize-skip', { node: n.id, skip: true })))
          .catch(function (e) { toast(e.message); }).then(next);
      });
    }
  }
  var ORG_STATE = { ok: 'up to date', source_changed: 'source changed since copied', copy_missing: 'copy missing at destination', source_missing: 'source not reachable' };
  function joinDest(rel) {  // show the destination path in the platform's own style
    var d = DATA.destination || '', win = d.indexOf('\\') >= 0;
    return d + (win ? '\\' : '/') + (win ? String(rel).replace(/\//g, '\\') : rel);
  }
  function orgSection(n) {
    var o = (DATA.organized || {})[n.id];
    var h = '<div class="sec"><h3>Organized copy</h3>';
    if (!DATA.destination) h += '<div class="muted">No destination yet. Double-click the node (or the button) to choose one.</div>';
    else if (!o) h += '<div class="muted">Not copied yet. Double-click the node to copy it to <code>' + esc(DATA.destination) + '</code>.</div>';
    else h += '<div class="kv"><div class="k">copy</div><div class="v mono">' + esc(joinDest(o.path)) + '</div>' +
      '<div class="k">from</div><div class="v mono">' + esc(o.source) + '</div>' +
      '<div class="k">state</div><div class="v"><span class="chip ' + (o.state === 'ok' ? 'org-ok' : 'state-stale') + '">' + esc(ORG_STATE[o.state] || o.state) + '</span> ' +
      '<span class="muted">copied ' + esc((o.copied || '').slice(0, 16).replace('T', ' ')) + '</span></div>' +
      '<div class="k">checksum</div><div class="v">' + (o.sidecar ? '<span class="mono" title="' + esc(o.sha256 || '') + '">' + esc(o.sidecar.split('/').pop()) +
        '</span> <span class="muted">SHA-256, ' + (o.files || 1) + ' file' + ((o.files || 1) === 1 ? '' : 's') + ', verified ' + esc((o.verified || '').slice(0, 16).replace('T', ' ')) + '</span>'
        : '<span class="muted">none yet \u2014 \u201cVerify copy\u201d compares it with the source and writes one</span>') + '</div></div>';
    if (LIVE && n.path) h += '<div class="actions"><button class="btn' + (o && o.state === 'ok' ? '' : ' primary') + '" data-act="organize">' +
      (o ? (o.state === 'ok' ? 'Copy again' : 'Update copy') : 'Save to destination') + '</button>' +
      (o && o.state !== 'copy_missing' ? '<button class="btn primary" data-act="reveal" title="Open File Explorer at the copy">Show in folder</button>' : '') +
      (o && o.state !== 'copy_missing' ? '<button class="btn" data-act="verify" title="Re-read the copy and check it against its SHA-256 checksum file">Verify copy</button>' : '') +
      (DATA.destination ? '<button class="btn" data-act="reveal-dest" title="Open the destination folder">Open destination</button>' : '') + '</div>' +
      '<div data-jobslot="' + esc(n.id) + '">' + jobLine(JOBS_NOW[n.id]) + '</div>';
    return h + '</div>';
  }

  function addNodeLabels(g, placement) {
    g.each(function (d) {
      var el = d3.select(this), r = radius(d);
      if (placement === 'right') {
        el.append('text').attr('class', 'code').attr('x', r + 6).attr('y', -2).text(d.id);
        el.append('text').attr('class', 'lbl').attr('x', r + 6).attr('y', 11).text(trunc(d.label, 30));
      } else {
        el.append('text').attr('class', 'code').attr('text-anchor', 'middle').attr('y', r + 13).text(d.id);
        el.append('text').attr('class', 'lbl').attr('text-anchor', 'middle').attr('y', r + 25).text(trunc(d.label, 26));
      }
    });
  }

  function edgeEvents(sel) {
    sel.on('mouseenter', function (evt, e) { showTip(evt, edgeTip(e)); })
      .on('mousemove', function (evt, e) { showTip(evt, edgeTip(e)); })
      .on('mouseleave', hideTip)
      .on('click', function (evt, e) { evt.stopPropagation(); selectEdge(e.id); });
  }

  function makeSvg() {
    vizEl.innerHTML = '';
    var w = vizEl.clientWidth || 800, h = vizEl.clientHeight || 600;
    var svg = d3.select(vizEl).append('svg').attr('viewBox', [0, 0, w, h]);
    defs(svg);
    var g = svg.append('g');
    var zoom = d3.zoom().scaleExtent([0.15, 5]).on('zoom', function (evt) {
      g.attr('transform', evt.transform); state.zoom[state.view] = evt.transform;
    });
    svg.call(zoom).on('dblclick.zoom', null);
    svg.on('click', function () { if (state.selected || state.selectedEdge) clearSelection(); });
    return { svg: svg, g: g, zoom: zoom, w: w, h: h };
  }

  function fit(ctx, pts, animate) {
    if (!pts.length) return;
    var pad = 70;
    var x0 = d3.min(pts, function (p) { return p[0]; }) - pad, x1 = d3.max(pts, function (p) { return p[0]; }) + pad;
    var y0 = d3.min(pts, function (p) { return p[1]; }) - pad, y1 = d3.max(pts, function (p) { return p[1]; }) + pad;
    var scale = Math.min(2, 0.95 / Math.max((x1 - x0) / ctx.w, (y1 - y0) / ctx.h));
    var t = d3.zoomIdentity.translate(ctx.w / 2 - scale * (x0 + x1) / 2, ctx.h / 2 - scale * (y0 + y1) / 2).scale(scale);
    (animate ? ctx.svg.transition().duration(450) : ctx.svg).call(ctx.zoom.transform, t);
  }

  function restoreOrFit(ctx, pts) {
    var z = state.zoom[state.view];
    if (z && !state.forceFit) ctx.svg.call(ctx.zoom.transform, z); else fit(ctx, pts, false);
    state.forceFit = false;
  }

  function paramBadges(layer, edges, pos) {
    if (!state.showParams && state.view !== 'lineage') return;
    layer.selectAll('text.edge-badge').data(edges.filter(hasParams)).join('text').attr('class', 'edge-badge')
      .attr('text-anchor', 'middle').text(function (e) { return trunc(fmtParams(e.params), 44); })
      .each(function (e) { var p = pos(e); d3.select(this).attr('x', p[0]).attr('y', p[1] - 4); });
  }

  // ------------------------------------------------ view 1: force+halos ----
  function renderForce() {
    var v = visible(), ctx = makeSvg();
    var nodes = v.nodes.map(function (n) { return { id: n.id, n: n, group: groupKey(n) }; });
    var idx = {}; nodes.forEach(function (d) { idx[d.id] = d; });
    var links = v.edges.map(function (e) { return { e: e, source: e.source, target: e.target }; });
    var groups = sortGroups(Array.from(new Set(nodes.map(function (d) { return d.group; }))));
    var stepMode = state.groupBy === 'step';
    var gColor = {};
    groups.forEach(function (gname) { gColor[gname] = haloColor(gname); });

    var hullLayer = ctx.g.append('g'), labelLayer = ctx.g.append('g'), linkLayer = ctx.g.append('g'),
      badgeLayer = ctx.g.append('g'), nodeLayer = ctx.g.append('g');

    // every group gets an anchor on a ring; nodes start at (and are pulled to) their anchor,
    // so halos separate instead of piling up in the middle
    var ringR = groups.length > 1 ? Math.max(170, Math.sqrt(nodes.length) * 48) : 0;
    var anchor = {}, stepR = {};
    if (stepMode) {
      // steps in analysis order, read like text: left -> right, then the next row (serpentine),
      // each cell sized for its largest step so the ovals never overlap
      // each step gets a disc sized to its object count; discs are packed in rows in step
      // order (serpentine), and a containment force keeps objects inside their own disc
      var size = {};
      nodes.forEach(function (d) { size[d.group] = (size[d.group] || 0) + 1; });
      groups.forEach(function (g) { stepR[g] = 70 + 46 * Math.sqrt(size[g]); });
      var GAP = 90;
      var targetW = Math.max(1100, Math.sqrt(d3.sum(groups, function (g) { return 4 * stepR[g] * stepR[g]; })) * 1.75);
      var rowsL = [], cur = [], wsum = 0;
      groups.forEach(function (g) {
        var gw = 2 * stepR[g] + GAP;
        if (cur.length && wsum + gw > targetW) { rowsL.push(cur); cur = []; wsum = 0; }
        cur.push(g); wsum += gw;
      });
      if (cur.length) rowsL.push(cur);
      var y = 0, rowY = [];
      rowsL.forEach(function (row) { var h = d3.max(row, function (g) { return 2 * stepR[g]; }); rowY.push(y + h / 2); y += h + GAP; });
      var totalH = y - GAP;
      rowsL.forEach(function (row, ri) {
        var ordered = ri % 2 ? row.slice().reverse() : row;
        var rw = d3.sum(ordered, function (g) { return 2 * stepR[g]; }) + GAP * (ordered.length - 1);
        var x = ctx.w / 2 - rw / 2;
        ordered.forEach(function (g) {
          anchor[g] = [x + stepR[g], ctx.h / 2 - totalH / 2 + rowY[ri]];
          x += 2 * stepR[g] + GAP;
        });
      });
    } else {
      groups.forEach(function (gname, i) {
        var a = (i / Math.max(1, groups.length)) * 2 * Math.PI - Math.PI / 2;
        anchor[gname] = [ctx.w / 2 + Math.cos(a) * ringR, ctx.h / 2 + Math.sin(a) * ringR];
      });
    }
    // live growth: groups that were already drawn keep their place (a growing oval must not slide the
    // others around); only a brand-new group gets a new spot. A reload, resize or new grouping re-lays out.
    var ac = state.anchorCache;
    if (ac && ac.mode === state.groupBy && !state.relayout)
      Object.keys(anchor).forEach(function (g) { if (ac.a[g]) anchor[g] = ac.a[g]; });
    state.anchorCache = { mode: state.groupBy, a: anchor };
    if (state.relayout) state.posCache = {};
    state.relayout = false;
    var seen = {};
    nodes.forEach(function (d, i) {
      var an = anchor[d.group];
      if (stepMode) {  // sunflower seeding inside the step's disc
        var j = seen[d.group] = (seen[d.group] || 0) + 1, rr = Math.sqrt(j / (1 + (nodes.length))) * stepR[d.group] * 0.9;
        d.x = an[0] + Math.cos(j * 2.39996) * Math.min(rr * 3, stepR[d.group] * 0.8);
        d.y = an[1] + Math.sin(j * 2.39996) * Math.min(rr * 3, stepR[d.group] * 0.8);
      } else { d.x = an[0] + Math.cos(i * 2.4) * 30; d.y = an[1] + Math.sin(i * 2.4) * 30; }
    });
    // keep each object inside its step's disc (hard boundary, so ovals never overlap)
    function containForce() {
      if (!stepMode) return;
      nodes.forEach(function (d) {
        var a = anchor[d.group], dx = d.x - a[0], dy = d.y - a[1], dist = Math.sqrt(dx * dx + dy * dy) || 1;
        var lim = stepR[d.group] - radius(d.n) - 28;
        if (dist > lim) { var k = (dist - lim) / dist; d.x -= dx * k; d.y -= dy * k; d.vx *= 0.5; d.vy *= 0.5; }
      });
    }

    function clusterForce(strength) {
      var ns;
      function force(alpha) {
        var c = {};
        ns.forEach(function (d) { var k = c[d.group] || (c[d.group] = { x: 0, y: 0, n: 0 }); k.x += d.x; k.y += d.y; k.n++; });
        ns.forEach(function (d) { var k = c[d.group]; d.vx -= (d.x - k.x / k.n) * strength * alpha; d.vy -= (d.y - k.y / k.n) * strength * alpha; });
      }
      force.initialize = function (_) { ns = _; };
      return force;
    }

    // live growth: objects seen before go back to the same place relative to their group's centre
    // (the centre moves when a group grows) and are held still while new objects settle around them
    var pc = state.posCache || {}, known = 0;
    nodes.forEach(function (d) {
      var c = pc[d.id], an = anchor[d.group];
      if (c && c.g === d.group && an) { d.x = an[0] + c.dx; d.y = an[1] + c.dy; d.fx = d.x; d.fy = d.y; known++; }
    });
    var sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(function (d) { return d.id; }).distance(function (l) { return l.e.rel === 'code' ? 60 : 95; })
        .strength(function (l) { return l.source.group === l.target.group ? 0.4 : 0.04; }))
      .force('charge', d3.forceManyBody().strength(-300).distanceMax(420))
      .force('collide', d3.forceCollide().radius(function (d) { return radius(d.n) + 26; }).strength(0.9))
      .force('x', d3.forceX(function (d) { return anchor[d.group][0]; }).strength(stepMode ? 0.16 : 0.07))
      .force('y', d3.forceY(function (d) { return anchor[d.group][1]; }).strength(stepMode ? 0.16 : 0.07))
      .force('cluster', clusterForce(0.25))
      .force('contain', containForce);

    function crossing(l) { return l.source.group !== l.target.group; }
    var linkSel = linkLayer.selectAll('path.edge').data(links).join('path')
      .attr('class', function (l) { return edgeClass(l.e) + (stepMode && crossing(l) ? ' cross' : ''); }).attr('marker-end', function (l) { return marker(l.e, 'tip'); });
    var hitSel = linkLayer.selectAll('path.edge-hit').data(links).join('path').attr('class', 'edge-hit')
      .datum(function (l) { return l; });
    hitSel.on('mouseenter', function (evt, l) { showTip(evt, edgeTip(l.e)); })
      .on('mousemove', function (evt, l) { showTip(evt, edgeTip(l.e)); })
      .on('mouseleave', hideTip)
      .on('click', function (evt, l) { evt.stopPropagation(); selectEdge(l.e.id); });

    var nodeSel = drawNodes(nodeLayer, nodes.map(function (d) { return d.n; }));
    addNodeLabels(nodeSel, 'below');
    nodeSel.call(d3.drag()
      .on('start', function (evt, n) { var d = idx[n.id]; if (!evt.active) sim.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', function (evt, n) { var d = idx[n.id]; d.fx = evt.x; d.fy = evt.y; })
      .on('end', function (evt, n) { var d = idx[n.id]; if (!evt.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

    function linePath(l) {
      var sx = l.source.x, sy = l.source.y, tx = l.target.x, ty = l.target.y;
      var dx = tx - sx, dy = ty - sy, len = Math.sqrt(dx * dx + dy * dy) || 1;
      var rs = radius(l.source.n) + 2, rt = radius(l.target.n) + 5;
      var x1 = sx + dx / len * rs, y1 = sy + dy / len * rs, x2 = tx - dx / len * rt, y2 = ty - dy / len * rt;
      if (stepMode && crossing(l)) {
        // links between steps arc across the gap; links within a step stay straight
        var bend = Math.min(90, len * 0.2);
        var mx = (x1 + x2) / 2 - dy / len * bend, my = (y1 + y2) / 2 + dx / len * bend;
        return 'M' + x1 + ',' + y1 + 'Q' + mx + ',' + my + ' ' + x2 + ',' + y2;
      }
      return 'M' + x1 + ',' + y1 + 'L' + x2 + ',' + y2;
    }
    var lineGen = d3.line().curve(d3.curveCatmullRomClosed.alpha(0.7));
    function hull(points, pad) {
      if (points.length < 3) {
        var cx = d3.mean(points, function (p) { return p[0]; }), cy = d3.mean(points, function (p) { return p[1]; });
        return d3.range(14).map(function (i) { var a = i / 14 * 2 * Math.PI; return [cx + Math.cos(a) * (pad + 12), cy + Math.sin(a) * (pad + 12)]; });
      }
      var h = d3.polygonHull(points); var c = d3.polygonCentroid(h);
      return h.map(function (p) { var dx = p[0] - c[0], dy = p[1] - c[1], l = Math.sqrt(dx * dx + dy * dy) || 1; return [p[0] + dx / l * pad, p[1] + dy / l * pad]; });
    }

    function tick() {
      linkSel.attr('d', linePath); hitSel.attr('d', linePath);
      nodeSel.attr('transform', function (n) { var d = idx[n.id]; return 'translate(' + d.x + ',' + d.y + ')'; });
      var by = {};
      nodes.forEach(function (d) { (by[d.group] = by[d.group] || []).push([d.x, d.y]); });
      var hulls = groups.filter(function (k) { return by[k]; }).map(function (k) { return { k: k, pts: hull(by[k], 46) }; });
      if (stepMode) {
        // one oval per step, fitted around its objects
        var ovals = groups.filter(function (k) { return by[k]; }).map(function (k) {
          var pts = by[k], cx = d3.mean(pts, function (p) { return p[0]; }), cy = d3.mean(pts, function (p) { return p[1]; });
          var rx = Math.max(95, d3.max(pts, function (p) { return Math.abs(p[0] - cx); }) + 70);
          var ry = Math.max(70, d3.max(pts, function (p) { return Math.abs(p[1] - cy); }) + 62);
          return { k: k, cx: cx, cy: cy, rx: rx, ry: ry };
        });
        hullLayer.selectAll('ellipse').data(ovals, function (d) { return d.k; }).join('ellipse').attr('class', 'step-oval')
          .attr('cx', function (d) { return d.cx; }).attr('cy', function (d) { return d.cy; })
          .attr('rx', function (d) { return d.rx; }).attr('ry', function (d) { return d.ry; })
          .attr('fill', function (d) { return gColor[d.k]; }).attr('fill-opacity', cssVar('--halo-opacity'))
          .attr('stroke', function (d) { return gColor[d.k]; }).attr('stroke-opacity', 0.5).attr('stroke-width', 1.4)
          .on('click', function (evt, d) { evt.stopPropagation(); showStep(d.k); });
        labelLayer.selectAll('text').data(ovals, function (d) { return d.k; }).join('text').attr('class', 'halo-label step-label')
          .attr('text-anchor', 'middle').attr('x', function (d) { return d.cx; }).attr('y', function (d) { return d.cy - d.ry - 8; })
          .text(function (d) { return stepLabel(d.k); })
          .on('click', function (evt, d) { evt.stopPropagation(); showStep(d.k); });
        paramBadges(badgeLayer, v.edges, function (e) {
          var s = idx[e.source], t = idx[e.target]; return [(s.x + t.x) / 2, (s.y + t.y) / 2];
        });
        return;
      }
      hullLayer.selectAll('path').data(hulls, function (d) { return d.k; }).join('path')
        .attr('d', function (d) { return lineGen(d.pts); })
        .attr('fill', function (d) { return gColor[d.k]; }).attr('fill-opacity', cssVar('--halo-opacity'))
        .attr('stroke', function (d) { return gColor[d.k]; }).attr('stroke-opacity', 0.45).attr('stroke-width', 1.2);
      labelLayer.selectAll('text').data(hulls, function (d) { return d.k; }).join('text').attr('class', 'halo-label')
        .attr('text-anchor', 'middle')
        .attr('x', function (d) { return d3.mean(d.pts, function (p) { return p[0]; }); })
        .attr('y', function (d) { return d3.min(d.pts, function (p) { return p[1]; }) - 6; })
        .text(function (d) { return groupLabel(d.k); });
      paramBadges(badgeLayer, v.edges, function (e) {
        var s = idx[e.source], t = idx[e.target]; return [(s.x + t.x) / 2, (s.y + t.y) / 2];
      });
    }

    sim.stop();
    // first layout: settle fully; later redraws (live updates): existing objects keep their place
    var ticks = known && known >= nodes.length * 0.6 ? 120 : 320;
    for (var i = 0; i < ticks; i++) sim.tick();
    nodes.forEach(function (d) { d.fx = null; d.fy = null; });  // free again for dragging / gentle settling
    function savePos() {
      var c = {};
      nodes.forEach(function (d) { var an = anchor[d.group]; if (an) c[d.id] = { dx: d.x - an[0], dy: d.y - an[1], g: d.group }; });
      state.posCache = c;
    }
    savePos();
    sim.on('end', savePos);
    tick();
    restoreOrFit(ctx, nodes.map(function (d) { return [d.x, d.y]; }));
    sim.on('tick', tick);
    sim.alpha(0.03).restart();
    state.fitCurrent = function () { fit(ctx, nodes.map(function (d) { return [d.x, d.y]; }), true); };
    return function () { sim.stop(); };
  }

  var STEP_TINTS = ['--cat-sources', '--cat-process', '--cat-outputs'];
  function haloColor(key) {
    if (state.groupBy === 'category') return catColor(key);
    if (state.groupBy === 'step') {
      // three validated hues cycled by order (+ label and order number carry identity), grey for "no step"
      return (DATA.steps || {})[key] ? cssVar(STEP_TINTS[(stepOrder(key) - 1) % 3 < 0 ? 0 : (stepOrder(key) - 1) % 3]) : cssVar('--text-3');
    }
    if (state.groupBy === 'type' && DATA.types[key]) return catColor(DATA.types[key].category);
    if (state.groupBy === 'state') return { stale: cssVar('--stale'), missing: cssVar('--edge-params'), modified: cssVar('--text-3') }[key] || cssVar('--cat-outputs');
    var g = DATA.groups[key];
    if (g && g.color) return g.color;
    // custom groups / articles: neutral halos, the label carries identity
    return cssVar('--text-3');
  }

  // --------------------------------------------- view 2: radial bundles ----
  function renderRadial() {
    var v = visible(), ctx = makeSvg();
    var groups = {};
    v.nodes.forEach(function (n) { var k = groupKey(n); (groups[k] = groups[k] || []).push(n); });
    var rootData = { id: '__root', name: DATA.project.name, children: sortGroups(Object.keys(groups)).map(function (k) {
      return { id: '__g_' + k, name: k, children: groups[k].sort(function (a, b) { return d3.ascending(a.id, b.id); }).map(function (n) { return { id: n.id, node: n }; }) };
    }) };
    var root = d3.hierarchy(rootData);
    var R = Math.max(220, Math.min(ctx.w, ctx.h) / 2 - 130);
    R = Math.max(R, v.nodes.length * 7);
    d3.cluster().size([2 * Math.PI, R]).separation(function (a, b) { return a.parent === b.parent ? 1 : 2.2; })(root);
    var leaf = {};
    root.leaves().forEach(function (l) { if (l.data.node) leaf[l.data.id] = l; });
    function pt(a, r) { return [r * Math.cos(a - Math.PI / 2), r * Math.sin(a - Math.PI / 2)]; }

    var g = ctx.g.append('g');
    g.append('g').selectAll('path').data(root.links().filter(function (l) { return l.source.depth > 0; })).join('path')
      .attr('class', 'tree-link')
      .attr('d', d3.linkRadial().angle(function (d) { return d.x; }).radius(function (d) { return d.y; }));

    var line = d3.lineRadial().curve(d3.curveBundle.beta(0.85)).radius(function (d) { return d.y; }).angle(function (d) { return d.x; });
    var bundles = v.edges.filter(function (e) { return leaf[e.source] && leaf[e.target]; });
    var edgeSel = g.append('g').selectAll('path').data(bundles).join('path')
      .attr('class', function (e) { return edgeClass(e); })
      .attr('marker-end', function (e) { return marker(e, 'off'); })
      .attr('d', function (e) { return line(leaf[e.source].path(leaf[e.target])); });
    edgeSel.style('stroke-opacity', 0.8);
    var hit = g.append('g').selectAll('path').data(bundles).join('path').attr('class', 'edge-hit')
      .attr('d', function (e) { return line(leaf[e.source].path(leaf[e.target])); });
    edgeEvents(hit);

    // group labels
    g.append('g').selectAll('text').data(root.children || []).join('text').attr('class', 'halo-label')
      .attr('text-anchor', 'middle')
      .attr('transform', function (d) { var p = pt(d.x, d.y); return 'translate(' + p[0] + ',' + p[1] + ')'; })
      .text(function (d) { return d.data.name; });

    var nodes = v.nodes.filter(function (n) { return leaf[n.id]; });
    var nodeSel = drawNodes(g.append('g'), nodes);
    nodeSel.attr('transform', function (n) { var l = leaf[n.id], p = pt(l.x, l.y); return 'translate(' + p[0] + ',' + p[1] + ')'; });
    nodeSel.each(function (n) {
      var l = leaf[n.id], deg = l.x * 180 / Math.PI - 90, flip = l.x > Math.PI, r = radius(n) + 7;
      d3.select(this).append('text').attr('class', 'lbl')
        .attr('transform', 'rotate(' + deg + ')' + (flip ? ' rotate(180)' : ''))
        .attr('x', flip ? -r : r).attr('dy', '0.32em').attr('text-anchor', flip ? 'end' : 'start')
        .html('<tspan class="code" style="font-weight:700">' + esc(n.id) + '</tspan> ' + esc(trunc(n.label, 30)));
    });

    var pts = root.descendants().map(function (d) { var p = pt(d.x, d.y); return p; });
    pts.push([-R - 190, 0], [R + 190, 0], [0, -R - 150], [0, R + 150]);
    restoreOrFit(ctx, pts);
    state.fitCurrent = function () { fit(ctx, pts, true); };
    return function () {};
  }

  // ---------------------------------------------- view 3: lineage (DAG) ----
  function renderLineage() {
    var v = visible(), ctx = makeSvg();
    var keep = {};
    if (state.selected && byId[state.selected]) {
      keep[state.selected] = true;
      walk(state.selected, 'up').concat(walk(state.selected, 'down')).forEach(function (id) { keep[id] = true; });
    } else v.nodes.forEach(function (n) { keep[n.id] = true; });
    var nodes = v.nodes.filter(function (n) { return keep[n.id]; });
    var edges = v.edges.filter(function (e) { return keep[e.source] && keep[e.target]; });
    var ins = {}, outs = {};
    nodes.forEach(function (n) { ins[n.id] = []; outs[n.id] = []; });
    edges.forEach(function (e) { ins[e.target].push(e.source); outs[e.source].push(e.target); });

    var depth = {};
    function dep(id, trail) {
      if (depth[id] !== undefined) return depth[id];
      if (trail[id]) return 0;
      trail[id] = true;
      var d = 0; ins[id].forEach(function (p) { d = Math.max(d, dep(p, trail) + 1); });
      delete trail[id];
      return (depth[id] = d);
    }
    nodes.forEach(function (n) { dep(n.id, {}); });
    // pull pure sources right next to their first consumer (scripts, params files)
    nodes.forEach(function (n) {
      if (!ins[n.id].length && outs[n.id].length) {
        var m = d3.min(outs[n.id], function (c) { return depth[c]; });
        depth[n.id] = Math.max(0, m - 1);
      }
    });
    var layers = [];
    nodes.forEach(function (n) { (layers[depth[n.id]] = layers[depth[n.id]] || []).push(n.id); });
    layers = layers.map(function (l) { return (l || []).sort(function (a, b) { return d3.ascending(groupKey(byId[a]), groupKey(byId[b])) || d3.ascending(a, b); }); });
    var order = {};
    function setOrder() { layers.forEach(function (l) { l.forEach(function (id, i) { order[id] = i; }); }); }
    setOrder();
    for (var sweep = 0; sweep < 6; sweep++) {
      var down = sweep % 2 === 0;
      layers.forEach(function (l) {
        l.sort(function (a, b) {
          var na = down ? ins[a] : outs[a], nb = down ? ins[b] : outs[b];
          var ba = na.length ? d3.mean(na, function (x) { return order[x]; }) : order[a];
          var bb = nb.length ? d3.mean(nb, function (x) { return order[x]; }) : order[b];
          return ba - bb;
        });
      });
      setOrder();
    }
    var X = 190, Y = 74, pos = {};
    var maxLen = d3.max(layers, function (l) { return l.length; }) || 1;
    layers.forEach(function (l, i) { l.forEach(function (id, j) { pos[id] = [i * X, (j - (l.length - 1) / 2) * Y + maxLen * Y / 2]; }); });

    var g = ctx.g.append('g');
    var link = d3.linkHorizontal();
    function ePath(e) {
      var s = pos[e.source], t = pos[e.target];
      return link({ source: [s[0] + radius(byId[e.source]) + 2, s[1]], target: [t[0] - radius(byId[e.target]) - 5, t[1]] });
    }
    g.append('g').selectAll('path').data(edges).join('path').attr('class', edgeClass)
      .attr('marker-end', function (e) { return marker(e, 'tip'); }).attr('d', ePath);
    edgeEvents(g.append('g').selectAll('path').data(edges).join('path').attr('class', 'edge-hit').attr('d', ePath));
    paramBadges(g.append('g'), edges, function (e) {
      var s = pos[e.source], t = pos[e.target]; return [(s[0] + t[0]) / 2, (s[1] + t[1]) / 2];
    });
    var nodeSel = drawNodes(g.append('g'), nodes);
    nodeSel.attr('transform', function (n) { return 'translate(' + pos[n.id][0] + ',' + pos[n.id][1] + ')'; });
    addNodeLabels(nodeSel, 'below');

    if (state.selected) {
      g.append('text').attr('class', 'halo-label').attr('x', -40).attr('y', -30)
        .text('lineage of ' + state.selected + ' — click empty space to show all');
    }
    var pts = nodes.map(function (n) { return pos[n.id]; });
    state.forceFit = true;
    restoreOrFit(ctx, pts);
    state.fitCurrent = function () { fit(ctx, pts, true); };
    return function () {};
  }

  var VIEWS = { force: renderForce, radial: renderRadial, lineage: renderLineage };
  var cleanup = null;

  function render() {
    if (!DATA) return;
    if (cleanup) cleanup();
    hideTip();
    if (!DATA.nodes.length) {
      vizEl.innerHTML = '<div class="empty-hint">No nodes yet. Add some with <code>sciweave add</code> or ask Claude to import results with the SciWeave protocol.</div>';
      cleanup = null;
    } else {
      cleanup = (VIEWS[state.view] || renderForce)();
    }
    applyHighlight();
    renderLegend();
    var stale = DATA.nodes.filter(function (n) { return st(n.id).stale; }).length;
    var hiddenN = DATA.nodes.filter(function (n) { return state.hiddenTypes.indexOf(n.type) >= 0; }).length;
    var focusN = Object.keys(DATA.hidden || {}).length;
    document.getElementById('counts').textContent = DATA.nodes.length + ' nodes · ' + DATA.edges.length + ' links' + (stale ? ' · ' + stale + ' stale' : '') +
      (hiddenN ? ' · ' + hiddenN + ' type-hidden' : '') + (focusN ? ' · ' + focusN + ' hidden for focus' + (state.showHidden ? ' (shown)' : '') : '');
  }

  // ------------------------------------------------------- highlighting ----
  function searchMatches(q) {
    q = q.trim().toLowerCase();
    if (!q) return null;
    var terms = q.split(/\s+/);
    var out = {};
    DATA.nodes.forEach(function (n) {
      var blob = [n.id, n.label, n.type, n.description, n.path, (n.tags || []).join(' '), (n.groups || []).join(' ')].join(' ').toLowerCase();
      inEdges[n.id].forEach(function (e) { blob += ' ' + fmtParams(e.params).toLowerCase(); });
      if (terms.every(function (t) { return blob.indexOf(t) >= 0; })) out[n.id] = true;
    });
    return out;
  }

  function applyHighlight() {
    var set = null;
    var q = searchMatches(state.search);
    if (q) set = q;
    else if (state.askHighlight) set = state.askHighlight;
    else if (state.selected && byId[state.selected]) {
      set = {}; set[state.selected] = true;
      walk(state.selected, 'up').concat(walk(state.selected, 'down')).forEach(function (id) { set[id] = true; });
    } else if (state.selectedEdge) {
      var e = DATA.edges.filter(function (x) { return x.id === state.selectedEdge; })[0];
      if (e) { set = {}; set[e.source] = true; set[e.target] = true; }
    }
    var strong = q || state.askHighlight;
    d3.select(vizEl).selectAll('g.node-g')
      .classed('dim', function (d) { return !!set && !set[d.id]; })
      .classed('hl', function (d) { return !!strong && !!set[d.id]; })
      .classed('selected', function (d) { return d.id === state.selected; });
    d3.select(vizEl).selectAll('path.edge, text.edge-badge').classed('dim', function (d) {
      var e = d.e || d; return !!set && !(set[e.source.id || e.source] && set[e.target.id || e.target]);
    });
  }

  // ------------------------------------------------------------ legend ----
  function renderLegend() {
    var el = document.getElementById('legend');
    el.classList.toggle('hidden', !state.showLegend || state.page !== 'network');
    var types = Object.keys(DATA.types);
    var count = {};
    DATA.nodes.forEach(function (n) { count[n.type] = (count[n.type] || 0) + 1; });
    var hidden = state.hiddenTypes.filter(function (t) { return count[t]; });
    var html = '<h4>Node types <span class="legend-hint">· click to hide / show</span>' +
      (hidden.length ? ' <a class="nid legend-showall" data-showall="1">show all</a>' : '') + '</h4><div class="grid">';
    types.forEach(function (t) {
      var fill = catColor(DATA.types[t].category);
      var off = state.hiddenTypes.indexOf(t) >= 0;
      html += '<div class="row type-toggle' + (off ? ' off' : '') + (count[t] ? '' : ' empty') + '" data-type="' + esc(t) + '" role="button" tabindex="0" ' +
        'aria-pressed="' + (!off) + '" title="' + (off ? 'Show ' : 'Hide ') + esc(t) + ' nodes">' +
        '<svg width="18" height="18" viewBox="-9 -9 18 18"><circle r="8.5" fill="' + fill + '"/>' +
        '<path d="' + ICONS[t] + '" transform="translate(-5.5,-5.5) scale(0.46)" fill="none" stroke="' + iconInk(fill) + '" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
        '<span class="tname">' + esc(t) + '</span> <span class="muted">' + esc(DATA.types[t].prefix) + (count[t] ? ' · ' + count[t] : '') + '</span></div>';
    });
    html += '</div><h4>Links &amp; state</h4><div class="grid">' +
      '<div class="row"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" stroke="' + cssVar('--edge-params') + '" stroke-width="2"/></svg>has parameters</div>' +
      '<div class="row"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" stroke="' + cssVar('--edge-plain') + '" stroke-width="2"/></svg>no parameters</div>' +
      '<div class="row"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" stroke="' + cssVar('--text-3') + '" stroke-width="2" stroke-dasharray="5 3"/></svg>stale link</div>' +
      '<div class="row"><svg width="18" height="18"><circle cx="9" cy="9" r="7" fill="none" stroke="' + cssVar('--stale') + '" stroke-width="2.5"/></svg>stale node</div>' +
      '<div class="row"><svg width="18" height="18"><circle cx="9" cy="9" r="7" fill="none" stroke="' + cssVar('--edge-params') + '" stroke-width="2.5"/></svg>no file yet (needs a path)</div>' +
      '<div class="row"><span style="width:18px;text-align:center">\ud83d\udc18</span>large data (&gt; 4 GB)</div>' +
      '<div class="row"><svg width="18" height="18"><circle cx="9" cy="9" r="7" fill="none" stroke="' + cssVar('--edge-params') + '" stroke-width="2" stroke-dasharray="3 2"/></svg>large, skipped from Save all</div>' +
      '<div class="row"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" stroke="' + cssVar('--text-2') + '" stroke-width="2.2" stroke-dasharray="0.5 5" stroke-linecap="round"/></svg>branch (variant of)</div>' +
      '<div class="row">' + branchSvg(16, cssVar('--text-2')) + 'alternative branch</div>' +
      '<div class="row"><svg width="18" height="18"><circle cx="9" cy="9" r="4.5" fill="' + cssVar('--accent') + '"/></svg>updated in the last 24 h</div>' +
      '<div class="row"><svg width="18" height="18"><circle cx="9" cy="9" r="7" fill="none" stroke="' + cssVar('--text-2') + '" stroke-width="1.5" stroke-dasharray="3 2"/></svg>modified on disk</div>' +
      '<div class="row"><span class="star" style="width:18px;text-align:center">★</span>has final version</div>' +
      '<div class="row"><svg width="18" height="18"><circle cx="9" cy="9" r="7" fill="' + cssVar('--text-3') + '" fill-opacity="0.55" stroke="' + cssVar('--text-3') + '" stroke-dasharray="2 2"/></svg>reference (not copied)</div>' +
      '</div>';
    el.innerHTML = html;
  }

  // ------------------------------------------------------- side panel ----
  function select(id) {
    state.showingSuggestions = false; state.showingDest = false;
    state.selected = id; state.selectedEdge = null; state.askHighlight = null;
    try { history.replaceState(null, '', '#node=' + encodeURIComponent(id)); } catch (e) {}
    if (state.view === 'lineage') render(); else applyHighlight();
    renderPanel();
  }
  function selectEdge(id) {
    state.selectedEdge = id; state.selected = null;
    applyHighlight(); renderEdgePanel();
  }
  function clearSelection() {
    var wasProject = state.showingDest;
    state.selected = null; state.selectedEdge = null; state.showingDest = false;
    try { history.replaceState(null, '', '#'); } catch (e) {}
    // closing a node's panel brings back the project actions; closing those hides the panel
    if (LIVE && !wasProject && !state.projectPanelClosed && state.page === 'network') renderProjectPanel();
    else { panelEl.classList.remove('open'); if (wasProject) state.projectPanelClosed = true; }
    if (state.view === 'lineage') render(); else applyHighlight();
  }

  function nidLink(id) {
    var n = byId[id];
    return '<a class="nid" data-goto="' + esc(id) + '" title="' + esc(n ? n.label : '') + '">' + esc(id) + '</a>';
  }

  function provTree(id, depth, seen) {
    if (depth > 7) return '';
    var html = '';
    inEdges[id].filter(function (e) { return e.rel !== 'documents' && e.rel !== 'related' && e.rel !== 'variant'; }).sort(function (a, b) { return d3.ascending(a.rel === 'code', b.rel === 'code') || d3.ascending(a.source, b.source); })
      .forEach(function (e) {
        var s = byId[e.source];
        html += '<div class="prov-item ' + (hasParams(e) ? 'params' : 'plain') + '" style="margin-left:' + (depth * 12) + 'px">' +
          '<span class="muted">' + esc(e.rel) + '</span> ' + nidLink(s.id) + ' ' + esc(s.label) +
          ' <span class="muted">v' + esc(s.current_version || '-') + '</span>' + (e.stale ? ' <span class="chip state-stale">stale</span>' : '');
        if (hasParams(e)) html += '<table class="params-table">' + Object.keys(e.params).map(function (k) {
          return '<tr><td>' + esc(k) + '</td><td class="mono">' + esc(e.params[k]) + '</td></tr>'; }).join('') + '</table>';
        var meta = [];
        if (e.script && e.rel !== 'code') meta.push('script ' + nidLink(e.script) + '@v' + esc(e.script_version));
        if (e.command) meta.push('<span class="mono">' + esc(trunc(e.command, 120)) + '</span>');
        if (e.params_file) meta.push('params file <span class="mono">' + esc(e.params_file.path) + '</span>');
        if (meta.length) html += '<div class="meta">' + meta.join(' · ') + '</div>';
        html += '</div>';
        if (!seen[s.id]) { seen[s.id] = true; html += provTree(s.id, depth + 1, seen); }
      });
    return html;
  }

  function renderPanel() {
    var n = byId[state.selected];
    if (!n) { panelEl.classList.remove('open'); return; }
    var s = st(n.id), fill = catColor(catOf(n));
    var html = '<div class="head"><div class="title">' +
      '<svg width="34" height="34" viewBox="-17 -17 34 34" style="flex:none"><circle r="16" fill="' + fill + '"/><path d="' + ICONS[n.type] +
      '" transform="translate(-9.5,-9.5) scale(0.79)" fill="none" stroke="' + iconInk(fill) + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
      '<h2>' + esc(n.id) + ' · ' + esc(n.label) + '</h2><button class="icon-btn close" id="panel-close" title="Close">✕</button></div>' +
      '<div class="chips"><span class="chip">' + esc(n.type) + '</span><span class="chip">' + esc(catOf(n)) + '</span><span class="chip">' + esc(n.mode) + '</span>' +
      '<span class="chip state-' + esc(s.state) + '">' + esc(s.state) + '</span>' +
      (n.step ? '<span class="chip" data-step="' + esc(n.step) + '" style="cursor:pointer">step ' + esc(stepLabel(n.step)) + '</span>' : '') +
      (n.final_version ? '<span class="chip"><span class="star">★</span> final v' + n.final_version + '</span>' : '') +
      ((DATA.hidden || {})[n.id] ? '<span class="chip" title="' + esc(DATA.hidden[n.id]) + '">hidden · ' + esc(DATA.hidden[n.id]) + '</span>' : '') +
      (n.groups || []).map(function (g) { return '<span class="chip">#' + esc(g) + '</span>'; }).join('') + '</div>';
    html += '<div class="actions">' +
      '<button class="btn primary" data-act="save" ' + (LIVE && n.path ? '' : 'disabled') + ' title="Snapshot the file as a new version">Save version</button>' +
      '<button class="btn" data-act="final" ' + (LIVE && n.current_version ? '' : 'disabled') + '>Mark current final</button>' +
      '<button class="btn" data-act="note" ' + (LIVE ? '' : 'disabled') + '>Add note</button>' +
      '<button class="btn" data-act="hide" ' + (LIVE ? '' : 'disabled') + ' title="Hide from view for focus; nothing is deleted">' + ((DATA.hidden || {})[n.id] ? 'Unhide' : 'Hide') + '</button>' +
      '<button class="btn" data-act="lineage">Lineage view</button>' +
      (LIVE && n.path ? '<button class="btn" data-act="organize" title="Copy this object to the project destination, into the folder of its step / group (same as double-clicking the node)">' +
        ((DATA.organized || {})[n.id] ? ((DATA.organized[n.id].state === 'ok') ? 'Copy again to destination' : 'Update copy at destination') : 'Save to destination') + '</button>' : '') +
      '</div>' +
      (LIVE ? '' : '<div class="muted" style="font-size:12px;margin-top:6px">Static export: run <code>sciweave serve</code> for save / final / notes.</div>') +
      '</div>';

    html += orgSection(n);
    if (s.reasons && s.reasons.length) html += '<div class="sec"><h3>Attention</h3>' + s.reasons.map(function (r) { return '<div>' + esc(r) + '</div>'; }).join('') + '</div>';
    html += '<div class="sec"><h3>About</h3><div class="kv">' +
      (n.description ? '<div class="k">description</div><div class="v">' + esc(n.description) + '</div>' : '') +
      (n.step ? '<div class="k">step</div><div class="v"><a class="nid" data-step="' + esc(n.step) + '">' + esc(stepLabel(n.step)) + '</a> <span class="muted">of ' + Object.keys(DATA.steps || {}).length + '</span></div>' : '<div class="k">step</div><div class="v muted">not assigned</div>') +
      '<div class="k">path</div><div class="v mono">' + esc(n.path || '—') + '</div>' +
      (n.meta && n.meta.origin ? '<div class="k">copied from</div><div class="v mono">' + esc(n.meta.origin) + '</div>' : '') +
      '<div class="k">created</div><div class="v">' + esc(when(n.created)) + '</div>' +
      '<div class="k">updated</div><div class="v">' + esc(when(n.updated)) + '</div>' +
      ((n.tags || []).length ? '<div class="k">tags</div><div class="v">' + esc(n.tags.join(', ')) + '</div>' : '') +
      Object.keys(n.meta || {}).filter(function (k) { return k !== 'origin'; }).map(function (k) {
        var val = n.meta[k]; if (typeof val === 'object') val = JSON.stringify(val);
        if (k === 'url' && /^https?:\/\//.test(String(val)))
          return '<div class="k">web address</div><div class="v"><a class="nid" href="' + esc(val) + '" target="_blank" rel="noopener">' + esc(val) + '</a></div>';
        return '<div class="k">' + esc(k.replace(/_/g, ' ')) + '</div><div class="v mono">' + esc(val) + '</div>';
      }).join('') +
      '</div></div>';
    html += '<div class="sec preview" id="preview"><h3>Preview</h3><div class="muted">loading…</div></div>';

    var prov = provTree(n.id, 0, {});
    html += '<div class="sec"><h3>How it was made</h3>' + (prov || '<div class="muted">No recorded inputs — this is a source.</div>') + '</div>';
    var used = outEdges[n.id];
    html += '<div class="sec"><h3>Used by (' + used.length + ')</h3>' + (used.length ? used.map(function (e) {
      return '<div class="prov-item ' + (hasParams(e) ? 'params' : 'plain') + '"><span class="muted">' + esc(e.rel) + '</span> ' + nidLink(e.target) + ' ' +
        esc(byId[e.target].label) + (e.label ? ' <span class="muted">as “' + esc(e.label) + '”</span>' : '') + (e.stale ? ' <span class="chip state-stale">stale</span>' : '') + '</div>';
    }).join('') : '<div class="muted">Nothing depends on it yet.</div>') + '</div>';

    html += renderBranches(n);
    html += renderUpdates(n);
    html += '<div class="sec"><h3>Notes</h3>' + ((n.notes || []).length ? n.notes.slice().reverse().map(function (x) {
      return '<div class="note-item"><div class="when">' + esc(when(x.ts)) + ' · ' + esc(x.actor || 'user') + '</div>' + esc(x.text) + '</div>';
    }).join('') : '<div class="muted">No notes.</div>') + '</div>';
    var hist = DATA.history.filter(function (h) { return h.node === n.id || (h.nodes || []).indexOf(n.id) >= 0; }).slice(-25).reverse();
    html += '<div class="sec"><h3>History</h3>' + (hist.length ? hist.map(function (h) {
      return '<div class="hist-item"><span class="when">' + esc(when(h.ts)) + '</span><span class="actor">' + esc(h.actor) + '</span>' + esc(h.event.replace(/_/g, ' ')) +
        (h.detail ? ' <span class="muted">' + esc(trunc(h.detail, 90)) + '</span>' : '') + '</div>';
    }).join('') : '<div class="muted">No events.</div>') + '</div>';

    panelEl.innerHTML = html;
    panelEl.classList.add('open');
    loadPreview(n, null);
  }

  function describeChange(c) {
    switch (c.kind) {
      case 'param': return { cls: 'param', text: c.key + ': ' + fmtVal(c.from) + ' \u2192 ' + fmtVal(c.to) + (c.source ? '  (' + c.source + ')' : '') };
      case 'input': return { cls: 'input', text: 'input ' + c.node + ' v' + c.from + ' \u2192 v' + c.to };
      case 'script': return { cls: 'input', text: 'script ' + c.node + ' v' + c.from + ' \u2192 v' + c.to };
      case 'added_input': return { cls: 'input', text: '+ input ' + c.node };
      case 'added_script': return { cls: 'input', text: '+ script ' + c.node };
      case 'dropped_input': return { cls: 'input', text: '\u2212 input ' + c.node };
      case 'content': return { cls: '', text: 'size ' + fmtSize(c.from) + ' \u2192 ' + fmtSize(c.to) };
      default: return { cls: '', text: c.kind };
    }
  }
  function fmtVal(v) { return v === undefined || v === null ? '\u2205' : Array.isArray(v) ? v.join(',') : String(v); }
  function fmtSize(b) { return b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : b > 1024 ? (b / 1024).toFixed(1) + ' KB' : b + ' B'; }

  // One timeline of how this item evolved: its versions (why + what changed) and
  // parameter edits made on the links that feed it.
  function renderUpdates(n) {
    var items = [];
    (n.versions || []).forEach(function (v) { items.push({ ts: v.ts, v: v }); });
    inEdges[n.id].forEach(function (e) { (e.changes || []).forEach(function (c) { items.push({ ts: c.ts, e: e, c: c }); }); });
    if (!items.length) return '';
    items.sort(function (a, b) { return d3.descending(a.ts, b.ts); });
    var html = '<div class="sec"><h3>Updates \u00b7 how and why it changed</h3>';
    items.forEach(function (it) {
      if (it.v) {
        var v = it.v, acts = '';
        if (v.stored) acts += '<a class="nid" data-vprev="' + v.v + '">view</a>';
        if (LIVE && v.v !== n.final_version) acts += '<a class="nid" data-vfinal="' + v.v + '">final</a>';
        if (LIVE && v.stored && v.v !== n.current_version) acts += '<a class="nid" data-vrestore="' + v.v + '">restore</a>';
        html += '<div class="upd"><div class="upd-head">' + (v.v === n.final_version ? '<span class="star">\u2605</span>' : '') +
          '<b>v' + v.v + '</b><span class="muted">' + esc(when(v.ts)) + '</span><span class="actor">' + esc(v.actor || 'user') + '</span>' +
          (v.v === n.current_version ? '<span class="chip">current</span>' : '') + '<span class="acts">' + acts + '</span></div>';
        if (v.why) html += '<div class="upd-why"><b>why</b>' + esc(v.why) + '</div>';
        else if (v.v > 1 && v.actor !== 'import') html += '<div class="upd-why muted">no reason recorded</div>';
        if (v.message) html += '<div class="upd-msg">' + esc(v.message) + '</div>';
        var ch = (v.changes || []).map(describeChange);
        if (ch.length) html += '<div>' + ch.map(function (c) { return '<span class="chg ' + c.cls + '">' + esc(c.text) + '</span>'; }).join('') + '</div>';
        if (!v.stored) html += '<div class="muted" style="font-size:11.5px">fingerprint only (not restorable)</div>';
        html += '</div>';
      } else {
        var diffs = Object.keys(it.c.params || {}).map(function (k) {
          return '<span class="chg param">' + esc(k + ': ' + fmtVal(it.c.params[k][0]) + ' \u2192 ' + fmtVal(it.c.params[k][1])) + '</span>';
        }).join('');
        html += '<div class="upd param"><div class="upd-head"><b>parameters</b><span class="muted">' + esc(when(it.ts)) + '</span><span class="actor">' +
          esc(it.c.actor || 'user') + '</span><span class="muted">on ' + nidLink(it.e.source) + ' \u2192 ' + esc(n.id) + '</span></div>' +
          (it.c.why ? '<div class="upd-why"><b>why</b>' + esc(it.c.why) + '</div>' : '') + '<div>' + diffs + '</div>' +
          '<div class="muted" style="font-size:11.5px">takes effect in the next version saved after the re-run</div></div>';
      }
    });
    return html + '</div>';
  }

  function branchFamily(id) {
    var root = id, guard = 0;
    while (byId[root] && byId[root].branch && byId[root].branch.of && byId[byId[root].branch.of] && guard++ < 50) root = byId[root].branch.of;
    var fam = [root], stack = [root];
    while (stack.length) {
      var cur = stack.pop();
      DATA.nodes.forEach(function (x) { if (x.branch && x.branch.of === cur && fam.indexOf(x.id) < 0) { fam.push(x.id); stack.push(x.id); } });
    }
    return fam;
  }
  function renderBranches(n) {
    var fam = branchFamily(n.id);
    if (fam.length < 2) return '';
    var html = '<div class="sec"><h3>Branches \u00b7 alternative versions of this analysis</h3>';
    fam.forEach(function (id) {
      var x = byId[id], b = x.branch || { status: 'main' };
      var status = b.status || 'main';
      html += '<div class="br"><span class="chip st-' + esc(status) + '">' + (status === 'main' ? '\u2713 main' : esc(status)) + '</span>' +
        (id === n.id ? '<b>' + esc(id) + '</b>' : nidLink(id)) + ' <span>' + esc(trunc(x.label, 38)) + '</span>' +
        (LIVE && status !== 'main' ? '<a class="nid" style="margin-left:auto" data-brmain="' + esc(id) + '">make main</a>' : '') +
        (b.why ? '<div class="why" style="flex-basis:100%">why: ' + esc(b.why) + '</div>' : '') +
        (b.decision && b.decision.why ? '<div class="muted" style="flex-basis:100%;font-size:12px">decided ' + esc(when(b.decision.ts)) + ': ' + esc(b.decision.why) + '</div>' : '') +
        '</div>';
    });
    return html + '</div>';
  }

  // ---- a step: its objects, and its links within / from / to other steps
  function showStep(key) {
    state.showingSuggestions = false; state.selected = null; state.selectedEdge = null;
    var members = DATA.nodes.filter(function (n) { return (n.step || '(no step)') === key || (key === '(no step)' && !(DATA.steps || {})[n.step]); });
    var ids = {}; members.forEach(function (n) { ids[n.id] = true; });
    state.askHighlight = ids;
    applyHighlight();
    var within = 0, inn = {}, out = {};
    DATA.edges.forEach(function (e) {
      var a = ids[e.source], b = ids[e.target];
      if (a && b) within++;
      else if (b) { var k1 = (byId[e.source] || {}).step || '(no step)'; inn[k1] = (inn[k1] || 0) + 1; }
      else if (a) { var k2 = (byId[e.target] || {}).step || '(no step)'; out[k2] = (out[k2] || 0) + 1; }
    });
    var st = (DATA.steps || {})[key] || {};
    var byType = {};
    members.forEach(function (n) { (byType[n.type] = byType[n.type] || []).push(n); });
    function flow(obj, arrow) {
      var ks = Object.keys(obj).sort(function (a, b) { return stepOrder(a) - stepOrder(b); });
      return ks.length ? '<div class="step-flow">' + ks.map(function (k) {
        return '<span class="chip" data-step="' + esc(k) + '">' + (arrow === 'in' ? esc(stepLabel(k)) + ' →' : '→ ' + esc(stepLabel(k))) + ' · ' + obj[k] + '</span>';
      }).join('') + '</div>' : '<div class="muted">none</div>';
    }
    var html = '<div class="head"><div class="title"><h2>' + esc(stepLabel(key)) + '</h2><button class="icon-btn close" id="panel-close">✕</button></div>' +
      (st.description ? '<div class="muted" style="margin-top:6px">' + esc(st.description) + '</div>' : '') +
      '<div class="chips"><span class="chip">' + members.length + ' objects</span><span class="chip">' + within + ' links within</span></div></div>';
    html += '<div class="sec"><h3>Objects by type</h3>' + Object.keys(byType).sort().map(function (t) {
      var fill = catColor(DATA.types[t].category);
      return '<div style="margin-bottom:6px"><span class="type-count"><svg width="16" height="16" viewBox="-9 -9 18 18"><circle r="8.5" fill="' + fill + '"/><path d="' + ICONS[t] +
        '" transform="translate(-5.5,-5.5) scale(0.46)" fill="none" stroke="' + iconInk(fill) + '" stroke-width="2.4" stroke-linecap="round"/></svg><b>' + esc(t) + '</b> ' + byType[t].length + '</span> ' +
        byType[t].map(function (n) { return nidLink(n.id); }).join(' ') + '</div>';
    }).join('') + '</div>';
    html += '<div class="sec"><h3>Links in from other steps</h3>' + flow(inn, 'in') + '</div>';
    html += '<div class="sec"><h3>Links out to other steps</h3>' + flow(out, 'out') + '</div>';
    html += '<div class="sec muted" style="font-size:12.5px">The step’s objects are highlighted in the network; click empty space to clear.</div>';
    panelEl.innerHTML = html;
    panelEl.classList.add('open');
  }

  function renderEdgePanel() {
    var e = DATA.edges.filter(function (x) { return x.id === state.selectedEdge; })[0];
    if (!e) return;
    var html = '<div class="head"><div class="title"><h2>' + esc(e.id) + ': ' + esc(e.source) + ' → ' + esc(e.target) + '</h2>' +
      '<button class="icon-btn close" id="panel-close">✕</button></div><div class="chips"><span class="chip">' + esc(e.rel) + '</span>' +
      '<span class="chip">' + (hasParams(e) ? 'has parameters' : 'no parameters') + '</span>' + (e.stale ? '<span class="chip state-stale">stale</span>' : '') + '</div></div>';
    html += '<div class="sec"><div class="kv"><div class="k">from</div><div class="v">' + nidLink(e.source) + ' ' + esc(byId[e.source].label) + '</div>' +
      '<div class="k">to</div><div class="v">' + nidLink(e.target) + ' ' + esc(byId[e.target].label) + '</div>' +
      (e.label ? '<div class="k">as</div><div class="v">' + esc(e.label) + '</div>' : '') +
      (e.script ? '<div class="k">script</div><div class="v">' + nidLink(e.script) + ' @ v' + esc(e.script_version) + '</div>' : '') +
      (e.command ? '<div class="k">command</div><div class="v mono">' + esc(e.command) + '</div>' : '') +
      (e.params_file ? '<div class="k">params file</div><div class="v mono">' + esc(e.params_file.path) + '</div>' : '') +
      '<div class="k">built from</div><div class="v">' + esc(e.source) + ' v' + esc(e.source_version) + ' (now v' + esc(byId[e.source].current_version) + ')</div>' +
      (e.note ? '<div class="k">note</div><div class="v">' + esc(e.note) + '</div>' : '') +
      '<div class="k">created</div><div class="v">' + esc(when(e.created)) + '</div></div></div>';
    if ((e.changes || []).length) html += '<div class="sec"><h3>Parameter changes</h3>' + e.changes.slice().reverse().map(function (c) {
      return '<div class="upd param"><div class="upd-head"><span class="muted">' + esc(when(c.ts)) + '</span><span class="actor">' + esc(c.actor || 'user') + '</span></div>' +
        (c.why ? '<div class="upd-why"><b>why</b>' + esc(c.why) + '</div>' : '') + '<div>' + Object.keys(c.params).map(function (k) {
          return '<span class="chg param">' + esc(k + ': ' + fmtVal(c.params[k][0]) + ' \u2192 ' + fmtVal(c.params[k][1])) + '</span>'; }).join('') + '</div></div>';
    }).join('') + '</div>';
    html += '<div class="sec"><h3>Parameters</h3>' + (hasParams(e) ? '<table class="params-table">' + Object.keys(e.params).map(function (k) {
      return '<tr><td>' + esc(k) + '</td><td class="mono">' + esc(e.params[k]) + '</td></tr>'; }).join('') + '</table>' : '<div class="muted">None recorded (green link).</div>') + '</div>';
    panelEl.innerHTML = html;
    panelEl.classList.add('open');
  }

  function loadPreview(n, version) {
    var box = document.getElementById('preview');
    if (!box) return;
    var head = '<h3>Preview' + (version ? ' — v' + version : '') + '</h3>';
    if (!n.path) { box.innerHTML = head + '<div class="muted">No file.</div>'; return; }
    if (!LIVE) {
      var ext = (n.path.split('.').pop() || '').toLowerCase();
      if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].indexOf(ext) >= 0 && !/^([a-zA-Z]:|\/)/.test(n.path) && !version)
        box.innerHTML = head + '<img src="' + esc(n.path) + '" alt="' + esc(n.label) + '">';
      else box.innerHTML = head + '<div class="muted">Previews of tables and old versions need <code>sciweave serve</code>.</div>';
      return;
    }
    fetch('api/preview?node=' + encodeURIComponent(n.id) + (version ? '&version=' + version : ''))
      .then(function (r) { return r.json(); })
      .then(function (p) {
        var h = head;
        if (p.error) h += '<div class="muted">' + esc(p.error) + '</div>';
        else if (p.kind === 'image') h += '<img src="' + esc(p.url.replace(/^\//, '')) + '" alt="' + esc(n.label) + '">';
        else if (p.kind === 'pdf') h += '<a class="nid" target="_blank" href="' + esc(p.url.replace(/^\//, '')) + '">open PDF</a>';
        else if (p.kind === 'table') h += '<div class="tbl"><table><tr>' + p.header.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') + '</tr>' +
          p.rows.map(function (r) { return '<tr>' + r.map(function (c) { return '<td>' + esc(c) + '</td>'; }).join('') + '</tr>'; }).join('') + '</table></div>';
        else if (p.kind === 'text') h += '<pre>' + esc(p.text) + '</pre>';
        else if (p.kind === 'dir') h += '<pre>' + esc(p.items.join('\n')) + '</pre>';
        else h += '<div class="muted">' + esc(p.reason || 'No preview.') + '</div>';
        box.innerHTML = h;
      }).catch(function () { box.innerHTML = head + '<div class="muted">Preview unavailable.</div>'; });
  }

  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json(); })
      .then(function (res) { if (res.error) throw new Error(res.error); return res; });
  }
  function toast(msg) {
    var t = document.getElementById('toast'); t.textContent = msg; t.style.display = 'block';
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.style.display = 'none'; }, 3200);
  }
  function act(promise) {
    promise.then(function (res) { toast(res.message || 'done'); return refresh(true); })
      .catch(function (err) { toast('⚠ ' + err.message); });
  }

  panelEl.addEventListener('click', function (evt) {
    var stepEl = evt.target.closest('[data-step]');
    if (stepEl) { showStep(stepEl.dataset.step); return; }
    if (evt.target.closest('[data-gohist]')) { setPage('history'); return; }
    var dt = evt.target.closest('[data-dsave],[data-dshow],[data-dopen],[data-dchange],[data-dall]');
    if (dt) {
      if (dt.dataset.dsave) organizeNode(dt.dataset.dsave);
      else if (dt.dataset.dshow) post('api/reveal', { node: dt.dataset.dshow }).then(function (r) { toast(r.message); }).catch(function (e) { toast(e.message); });
      else if (dt.dataset.dopen) post('api/reveal', {}).then(function (r) { toast(r.message); }).catch(function (e) { toast(e.message); });
      else if (dt.dataset.dchange) setDestinationPrompt();
      else if (dt.dataset.dall) saveAll();
      return;
    }
    var t = evt.target.closest('[data-goto],[data-act],[data-vprev],[data-vfinal],[data-vrestore],[data-brmain],[data-sg],[data-sgsave],[data-sgcopy],[data-sgignore],#panel-close');
    if (!t) return;
    var n = byId[state.selected];
    if (t.id === 'panel-close') return clearSelection();
    if (t.dataset.sg === 'round') { toast('running a round\u2026'); post('api/suggest', {}).then(function () { return refresh(true); })
      .then(function () { loadSuggestions(true); }).catch(function (err) { toast('\u26a0 ' + err.message); }); return; }
    if (t.dataset.sgsave) { saveWithWhy(t.dataset.sgsave, function () { loadSuggestions(true); }); return; }
    if (t.dataset.sgcopy) {
      var cmd = t.dataset.sgcopy;
      (navigator.clipboard ? navigator.clipboard.writeText(cmd) : Promise.reject()).then(function () { toast('copied: ' + cmd); },
        function () { prompt('Copy this command:', cmd); });
      return;
    }
    if (t.dataset.sgignore) { post('api/ignore', { pattern: t.dataset.sgignore }).then(function (r) { toast(r.message); loadSuggestions(true); }); return; }
    if (t.dataset.brmain) {
      var why = prompt('Why is ' + t.dataset.brmain + ' now the main analysis?', '');
      if (why !== null) act(post('api/branch', { node: t.dataset.brmain, status: 'main', why: why }));
      return;
    }
    if (t.dataset.goto) { goTo(t.dataset.goto); return; }
    if (t.dataset.vprev && n) return loadPreview(n, +t.dataset.vprev);
    if (t.dataset.vfinal && n) return act(post('api/final', { node: n.id, version: +t.dataset.vfinal }));
    if (t.dataset.vrestore && n) {
      if (confirm('Restore ' + n.id + ' to v' + t.dataset.vrestore + '? The current file will be replaced (it is kept as a version).'))
        act(post('api/restore', { node: n.id, version: +t.dataset.vrestore }));
      return;
    }
    if (!n) return;
    switch (t.dataset.act) {
      case 'save': saveWithWhy(n.id); break;
      case 'final': act(post('api/final', { node: n.id })); break;
      case 'note': var txt = prompt('Note for ' + n.id, ''); if (txt) act(post('api/note', { node: n.id, text: txt })); break;
      case 'hide': act(post('api/hide', { node: n.id, hidden: !(DATA.hidden || {})[n.id] })); break;
      case 'lineage': setView('lineage'); break;
      case 'organize': organizeNode(n.id); break;
      case 'verify': post('api/verify', { node: n.id }).then(function (r) { toast(r.message); pollJobs(); }).catch(function (e) { toast(e.message); }); break;
      case 'reveal': post('api/reveal', { node: n.id }).then(function (r) { toast(r.message); }).catch(function (e) { toast(e.message); }); break;
      case 'reveal-dest': post('api/reveal', {}).then(function (r) { toast(r.message); }).catch(function (e) { toast(e.message); }); break;
    }
  });

  function saveWithWhy(id, after) {
    var m = prompt('Save a new version of ' + id + '\n\nWhat changed? (short)', '');
    if (m === null) return;
    var why = prompt('Why was it updated? (kept with the version and shown in its history)', '');
    if (why === null) return;
    post('api/save', { node: id, message: m, why: why }).then(function (res) { toast(res.message || 'saved'); return refresh(true); })
      .then(function () { if (after) after(); }).catch(function (err) { toast('\u26a0 ' + err.message); });
  }

  // ---- suggestions from the monitor (sciweave monitor / the Claude skill / "run a round now")
  var SUGGEST = null;
  function loadSuggestions(show) {
    if (!LIVE) return;
    fetch('api/suggest').then(function (r) { return r.json(); }).then(function (s) {
      SUGGEST = s;
      var n = (s.updated || []).length + (s.new || []).length + (s.missing || []).length;
      var badge = document.getElementById('suggest-count');
      badge.hidden = !n; badge.textContent = n > 99 ? '99+' : n;
      if (show || state.showingSuggestions) renderSuggestions();
    }).catch(function () {});
  }
  function guessLabel(path) {
    return path.split('/').pop().replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim();
  }
  function renderSuggestions() {
    var s = SUGGEST || { updated: [], new: [], missing: [], stale: [] };
    state.showingSuggestions = true; state.selected = null; state.selectedEdge = null;
    var last = s.last_round ? when(s.last_round) + ' (' + Math.round(ageDays(s.last_round) * 1440) + ' min ago)' : 'never';
    var html = '<div class="head"><div class="title"><h2>Suggestions</h2><button class="icon-btn close" id="panel-close">\u2715</button></div>' +
      '<div class="muted" style="font-size:12.5px;margin-top:6px">last monitor round: ' + esc(last) + '</div>' +
      '<div class="actions"><button class="btn primary" data-sg="round">Run a round now</button></div></div>';
    function sec(title, items, fn) {
      if (!items.length) return '';
      return '<div class="sec"><h3>' + title + ' (' + items.length + ')</h3>' + items.slice(0, 60).map(fn).join('') + '</div>';
    }
    html += sec('Changed on disk \u00b7 save a new version, with a reason', s.updated || [], function (u) {
      return '<div class="sg-item">' + nidLink(u.node) + ' ' + esc(u.label) + '<div class="sg-path muted">' + esc(u.path || '') + '</div>' +
        '<div class="row2"><button class="btn" data-sgsave="' + esc(u.node) + '">Save new version\u2026</button></div></div>';
    });
    html += sec('New results \u00b7 not in the network yet', s.new || [], function (it) {
      var hint = it.version_of ? 'looks like a new version of ' + nidLink(it.version_of) : it.near ? 'next to ' + nidLink(it.near) : '';
      var cmd = it.version_of ? 'sciweave save ' + it.version_of + ' -m "' + it.path.split('/').pop() + '" --why "..."'
        : 'sciweave add ' + it.type + ' "' + guessLabel(it.path) + '" "' + it.path + '"';
      return '<div class="sg-item"><span class="chip">' + esc(it.type) + '</span> <span class="sg-path">' + esc(it.path) + '</span>' +
        (hint ? '<div class="muted" style="font-size:12px">' + hint + '</div>' : '') +
        '<div class="row2"><button class="btn" data-sgcopy="' + esc(cmd) + '">Copy command</button>' +
        '<button class="btn" data-sgignore="' + esc(it.path) + '">Ignore</button></div></div>';
    });
    html += sec('Missing on disk', s.missing || [], function (m) {
      return '<div class="sg-item">' + nidLink(m.node) + ' <span class="sg-path">' + esc(m.path) + '</span></div>';
    });
    html += sec('Stale \u00b7 regenerate when ready', s.stale || [], function (x) {
      return '<div class="sg-item">' + nidLink(x.node) + ' ' + esc(x.label) + '<div class="muted" style="font-size:12px">' + esc((x.reasons || [])[0] || '') + '</div></div>';
    });
    if (!(s.updated || []).length && !(s.new || []).length && !(s.missing || []).length && !(s.stale || []).length)
      html += '<div class="sec muted">Nothing to suggest. The network is up to date with the files on disk.</div>';
    html += '<div class="sec muted" style="font-size:12.5px">In Claude Code say <b>\u201cadd the new results to SciWeave\u201d</b>: Claude turns these into an import plan and asks before applying. ' +
      '<b>/sciweave-monitor</b> runs a round every hour; <code>sciweave monitor</code> does it in a terminal.</div>';
    panelEl.innerHTML = html;
    panelEl.classList.add('open');
  }
  document.getElementById('suggest-toggle').onclick = function () {
    if (!LIVE) { toast('Suggestions need sciweave serve'); return; }
    if (state.showingSuggestions && panelEl.classList.contains('open')) { state.showingSuggestions = false; panelEl.classList.remove('open'); return; }
    if (state.page !== 'network') setPage('network');
    loadSuggestions(true);
  };

  function goTo(id) {
    if (!byId[id]) return;
    setPage('network');
    select(id);
  }

  // ---------------------------------------------------------- articles ----
  function renderArticles() {
    var el = document.getElementById('articles');
    var arts = DATA.nodes.filter(function (n) { return n.type === 'article'; });
    if (!arts.length) { el.innerHTML = '<div class="empty-hint">No articles yet. Create one with <code>sciweave article new "Title"</code>.</div>'; return; }
    var order = { section: 0, figure: 1, table: 2, supplement: 3 };
    el.innerHTML = '<div class="cards">' + arts.map(function (a) {
      var comps = inEdges[a.id].filter(function (e) { return e.rel === 'part_of'; });
      var rank = function (e) { var o = order[byId[e.source].type]; return o === undefined ? 9 : o; };
      // sections keep manuscript order (creation order); the rest sort by their label (Figure 1, Figure 2 ...)
      comps.sort(function (x, y) {
        return rank(x) - rank(y) || (rank(x) === 0 ? d3.ascending(byId[x.source].created, byId[y.source].created) || d3.ascending(x.source, y.source)
          : d3.ascending(x.label || '', y.label || ''));
      });
      var stale = comps.filter(function (e) { return e.stale || st(e.source).stale; }).length;
      return '<div class="card"><h2>' + nidLink(a.id) + ' ' + esc(a.label) + '</h2><div class="sub">' + esc((a.meta || {}).folder || '') +
        ' · ' + comps.length + ' components' + (stale ? ' · <b>' + stale + ' need update</b> (<code>sciweave article sync ' + esc(a.id) + '</code>)' : '') + '</div>' +
        comps.map(function (e) {
          var n = byId[e.source], s = st(n.id), fill = catColor(catOf(n));
          var ver = e.placed_version ? 'placed v' + e.placed_version : 'v' + (n.current_version || '-');
          var newer = e.placed_version && n.current_version && e.placed_version !== (n.final_version || n.current_version);
          return '<div class="comp"><svg width="22" height="22" viewBox="-11 -11 22 22"><circle r="10.5" fill="' + fill + '"/><path d="' + ICONS[n.type] +
            '" transform="translate(-6.5,-6.5) scale(0.54)" fill="none" stroke="' + iconInk(fill) + '" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
            '<span class="as">' + esc(e.label || n.type) + '</span>' + nidLink(n.id) + ' <span>' + esc(trunc(n.label, 40)) + '</span>' +
            '<span class="right"><span class="chip">' + esc(ver) + '</span>' + (n.final_version ? '<span class="chip"><span class="star">★</span> v' + n.final_version + '</span>' : '') +
            (s.state !== 'ok' ? '<span class="chip state-' + esc(s.state) + '">' + esc(s.state) + '</span>' : '') +
            (newer || e.stale ? '<span class="chip state-stale">newer version</span>' : '') + '</span></div>';
        }).join('') + '</div>';
    }).join('') + '</div>';
  }


  // ------------------------------------------------- analysis guide ----
  // The analysis history as drill-down columns: top-level analyses (raw / input
  // data first) in the first column; clicking one lists its branches in the next
  // column, and so on. The right pane shows the selected analysis: summary, dates,
  // parameters, paths, objects in the network, branches and its own history.
  var histMode = null;
  // peek: hidden analyses opened from their blue dot for this visit only (not saved, not unhidden)
  var guide = { path: null, q: '', showHidden: load('guideShowHidden', false), peek: {} };
  var EYE = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/></svg>';
  var EYE_OFF = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18M10.6 5.1A10.8 10.8 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.9 8.3 2 12 2 12s3.6 7 10 7a9.8 9.8 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';
  function aHidden(k) { var all = A(), cur = k; while (cur) { if (all[cur] && all[cur].hidden) return true; cur = all[cur] && all[cur].parent; } return false; }
  function aPeeked(k) { var all = A(), cur = k; while (cur) { if (guide.peek[cur]) return true; cur = all[cur] && all[cur].parent; } return false; }
  function aShown(k) { return guide.showHidden || !aHidden(k) || aPeeked(k); }
  function guideKey() { return 'guide-' + (DATA.project && DATA.project.name || ''); }
  function A() { return DATA.analyses || {}; }
  function aKids(parent, includeHidden) {
    var all = A();
    return Object.keys(all).filter(function (k) { return (all[k].parent || null) === parent && (includeHidden || aShown(k)); }).sort(function (x, y) {
      return d3.ascending(all[x].order || 0, all[y].order || 0) || d3.ascending(all[x].start || '', all[y].start || '') || d3.ascending(x, y);
    });
  }
  function aNumbers() {
    var num = {};
    (function walk(parent, prefix) {
      var kids = aKids(parent, true), zero = kids.length && A()[kids[0]].order === 0 ? 1 : 0;  // order 0 = numbered 0 ("before")
      kids.forEach(function (k, i) { num[k] = prefix + (i + 1 - zero); walk(k, num[k] + '.'); });
    })(null, '');
    return num;
  }
  function aDescendants(k) { var n = 0; aKids(k).forEach(function (c) { n += 1 + aDescendants(c); }); return n; }
  function aWhen(a) {
    var s = a.start || '', e = a.end || '';
    if (!s && !e) return '';
    if (!e || e === s) return s || e;
    return s + ' → ' + (e.slice(0, 4) === s.slice(0, 4) ? e.slice(5) : e);
  }
  // text may refer to other analyses as @key: shown as a link with the current number
  var AREF = /@([A-Za-z0-9_](?:[A-Za-z0-9_.\-]*[A-Za-z0-9_])?)/g;
  function aText(s, num) {
    return esc(s || '').replace(AREF, function (m, k) {
      return A()[k] ? '<a class="nid aref" data-akey="' + esc(k) + '" title="' + esc(A()[k].title) + '">' + esc(num[k] + ' ' + trunc(A()[k].title, 40)) + '</a>' : m;
    });
  }
  function aPlain(s, num) {
    return String(s || '').replace(AREF, function (m, k) { return A()[k] ? num[k] + ' ' + trunc(A()[k].title, 30) : m; });
  }
  function aMatches(k, q) {
    var a = A()[k];
    return JSON.stringify([k, a.title, a.group, a.summary, a.issue, a.params, a.tools, a.paths, a.feeds, a.history]).toLowerCase().indexOf(q) >= 0;
  }

  function renderGuide() {
    var el = document.getElementById('guide');
    var all = A(), keys = Object.keys(all);
    if (!keys.length) {
      el.innerHTML = '<div class="empty-hint">No analysis history yet. Add one with <code>sciweave analysis add raw "Raw &amp; input data"</code> ' +
        'or load a whole guide with <code>sciweave analysis import guide.json</code> (or ask Claude to build it).</div>';
      return;
    }
    if (!guide.path) guide.path = load(guideKey(), []);
    guide.path = guide.path.filter(function (k) { return all[k] && aShown(k); });
    // keep the path consistent: each entry must be a child of the previous one
    for (var i = 0; i < guide.path.length; i++) {
      if ((all[guide.path[i]].parent || null) !== (i ? guide.path[i - 1] : null)) { guide.path = guide.path.slice(0, i); break; }
    }
    var num = aNumbers();
    var hiddenKeys = keys.filter(aHidden), shownKeys = keys.filter(function (k) { return !aHidden(k); });
    var done = shownKeys.filter(function (k) { return all[k].organized; }).length;
    var head = '<div class="guide-head"><div><h3>Analysis history</h3><div class="muted" style="font-size:12px">' + shownKeys.length +
      ' analyses in focus' + (hiddenKeys.length ? ' · ' + hiddenKeys.length + ' hidden' : '') + ' · click an analysis to open its branches; the eye hides it (nothing is deleted)</div></div>' +
      (hiddenKeys.length ? '<label class="gshow"><input type="checkbox" id="guide-show-hidden"' + (guide.showHidden ? ' checked' : '') + '> show hidden (' + hiddenKeys.length + ')</label>' : '') +
      '<div class="guide-prog" title="analyses in focus whose results are recorded in the network"><div class="bar"><span style="width:' +
      (100 * done / Math.max(1, shownKeys.length)) + '%"></span></div><span class="muted">' + done + ' / ' + shownKeys.length + ' organized</span></div>' +
      '<div class="search" style="min-width:200px"><input id="guide-q" placeholder="Find an analysis…" value="' + esc(guide.q) + '"></div></div>';

    var cols = '';
    if (guide.q) {
      var hits = keys.filter(function (k) { return aShown(k) && aMatches(k, guide.q); });
      hits.sort(function (x, y) {
        var a = num[x].split('.').map(Number), b = num[y].split('.').map(Number);
        for (var i = 0; i < Math.max(a.length, b.length); i++) { if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) - (b[i] || 0); }
        return 0;
      });
      cols = '<div class="gcol wide"><div class="gcol-h">' + hits.length + ' match' + (hits.length === 1 ? '' : 'es') + '</div>' +
        (hits.map(function (k) { return aRow(k, num, guide.path.indexOf(k) >= 0, true); }).join('') || '<div class="muted" style="padding:10px">Nothing matches.</div>') + '</div>';
    } else {
      var levels = [null].concat(guide.path);
      levels.forEach(function (parent, lvl) {
        var kids = aKids(parent, true);
        if (!kids.length) return;
        var title = parent ? num[parent] + ' ' + all[parent].title : 'Start here';
        // hidden analyses stay in the chain as blue dots (consecutive ones share a strip)
        var html = '', dots = [];
        var flush = function () {
          if (!dots.length) return;
          html += '<div class="gdots" title="' + dots.length + ' hidden · click a dot to show it">' + dots.map(function (k) {
            return '<span class="gdot" role="button" tabindex="0" data-apeek="' + esc(k) + '" title="' + esc(num[k] + ' ' + all[k].title) + ' (hidden) · click to show"></span>';
          }).join('') + '<span class="gdots-n">' + dots.length + ' hidden</span></div>';
          dots = [];
        };
        var lastGroup = null;
        kids.forEach(function (k) {
          var g = all[k].group || '';
          if (g !== lastGroup) {
            flush();
            if (g) html += '<div class="ggroup" title="Group label: independent items that belong together">' + esc(g) + '</div>';
            else if (lastGroup) html += '<div class="ggroup gnone"></div>';
            lastGroup = g;
          }
          if (aShown(k)) { flush(); html += aRow(k, num, guide.path[lvl] === k, false); } else dots.push(k);
        });
        flush();
        cols += '<div class="gcol"><div class="gcol-h" title="' + esc(title) + '">' + esc(trunc(title, 34)) + '</div>' + html + '</div>';
      });
    }
    var sel = guide.path[guide.path.length - 1];
    el.innerHTML = head + '<div class="guide-body"><div class="gcols" id="gcols">' + cols + '</div>' +
      '<div class="gdetail" id="gdetail">' + (sel ? aDetail(sel, num) : aIntro(num)) + '</div></div>';
    var gc = document.getElementById('gcols'); gc.scrollLeft = gc.scrollWidth;
    var shEl = document.getElementById('guide-show-hidden');
    if (shEl) shEl.onchange = function () { guide.showHidden = shEl.checked; store('guideShowHidden', guide.showHidden); renderGuide(); };
    var qEl = document.getElementById('guide-q');
    qEl.oninput = function () { guide.q = qEl.value.trim().toLowerCase(); var pos = qEl.selectionStart; renderGuide(); var n = document.getElementById('guide-q'); n.focus(); n.setSelectionRange(pos, pos); };
  }

  function aRow(k, num, on, showPath) {
    var a = A()[k], nk = aKids(k).length, hid = aHidden(k), own = !!a.hidden;
    var trail = '';
    if (showPath && a.parent) {
      var p = A()[a.parent];
      trail = '<div class="gtrail">in ' + esc(num[a.parent] + ' ' + trunc(p.title, 30)) + '</div>';
    }
    return '<button class="grow' + (on ? ' on' : '') + (hid ? (aPeeked(k) && !guide.showHidden ? ' gpeek' : ' ghidden') : '') + '" data-akey="' + esc(k) + '">' +
      (guide.peek[k] && !guide.showHidden ? '<span class="gdot in" role="button" tabindex="0" data-apeek="' + esc(k) + '" title="Fold back into a dot"></span>' : '') +
      '<span class="gnum">' + esc(num[k]) + '</span>' +
      '<span class="gmain"><span class="gtitle">' + esc(a.title) + '</span>' + trail +
      '<span class="gmeta">' + '<i class="dot st-' + esc(a.status || 'done') + '"></i>' + esc(aWhen(a) || a.status || '') +
      (a.organized ? ' · <span class="gok">✓ organized</span>' : '') + (a.issue ? ' · <span class="gwarn" title="' + esc(aPlain(a.issue, num)) + '">⚠ issue</span>' : '') + '</span></span>' +
      (LIVE && (!hid || own) ? '<span class="geye' + (own ? ' on' : '') + '" role="button" tabindex="0" data-ahide="' + esc(k) + '" title="' + (own ? 'Unhide' : 'Hide from view (with its branches and the objects only it uses)') + '">' + (own ? EYE_OFF : EYE) + '</span>' : '') +
      (nk ? '<span class="gkids" title="' + nk + ' branch' + (nk > 1 ? 'es' : '') + '">' + nk + ' ›</span>' : '') + '</button>';
  }

  function aIntro(num) {
    var tops = aKids(null);
    return '<div class="gd-sec"><h2 style="margin:0 0 6px">The analyses, in order</h2><p class="muted" style="margin:0 0 12px">Pick one on the left to see what was done, when, with which parameters, and its own history. ' +
      'Analyses with branches (›) open another column.</p>' +
      tops.map(function (k) {
        var a = A()[k], n = aDescendants(k);
        return '<div class="gd-top" data-akey="' + esc(k) + '"><span class="gnum">' + esc(num[k]) + '</span><div><b>' + esc(a.title) + '</b>' +
          (n ? ' <span class="muted">· ' + n + ' inside</span>' : '') + '<div class="muted" style="font-size:12.5px">' + esc(trunc(aPlain(a.summary, num), 170)) + '</div></div></div>';
      }).join('') + '</div>';
  }

  function aDetail(k, num) {
    var all = A(), a = all[k];
    var crumbs = [], cur = k;
    while (cur) { crumbs.unshift(cur); cur = all[cur].parent; }
    var h = '<div class="gd-crumbs">' + crumbs.map(function (c, i) {
      return (i ? '<span class="muted"> › </span>' : '') + '<a class="nid" data-akey="' + esc(c) + '">' + esc(num[c] + ' ' + trunc(all[c].title, 28)) + '</a>';
    }).join('') + '</div>';
    h += '<div class="gd-sec"><h2 class="gd-title"><span class="gnum big">' + esc(num[k]) + '</span>' + esc(a.title) + '</h2><div class="chips">' +
      '<span class="chip"><i class="dot st-' + esc(a.status || 'done') + '"></i>' + esc(a.status || 'done') + '</span>' +
      (aWhen(a) ? '<span class="chip">' + esc(aWhen(a)) + '</span>' : '') +
      (a.group ? '<span class="chip gchip">group · ' + esc(a.group) + '</span>' : '') +
      (a.step && DATA.steps && DATA.steps[a.step] ? '<span class="chip">step · ' + esc(DATA.steps[a.step].label) + '</span>' : '') +
      '<span class="chip ' + (a.organized ? 'gok-chip' : '') + '">' + (a.organized ? '✓ organized in the network' : 'not organized yet') + '</span>' +
      '<span class="chip mono">' + esc(k) + '</span></div>' +
      (a.summary ? '<p class="gd-summary">' + aText(a.summary, num) + '</p>' : '') +
      (a.issue ? '<div class="gd-issue"><b>Open issue</b> ' + aText(a.issue, num) + '</div>' : '') +
      (aHidden(k) ? '<div class="gd-hidden">' + EYE_OFF + (a.hidden ? ' Hidden from view, with its branches and the objects only it uses. Nothing is deleted.'
        : ' Hidden because it is inside a hidden analysis.') + (aPeeked(k) && !guide.showHidden ? ' You are looking at it from its blue dot; it stays hidden.' : '') + '</div>' : '') +
      (LIVE ? '<div class="actions"><button class="btn' + (a.organized ? '' : ' primary') + '" data-aorg="' + esc(k) + '">' +
        (a.organized ? 'Mark not organized' : 'Mark organized ✓') + '</button>' +
        (!aHidden(k) || a.hidden ? '<button class="btn" data-ahide="' + esc(k) + '">' + (a.hidden ? 'Unhide' : 'Hide from view') + '</button>' : '') + '</div>' : '') + '</div>';
    var kv = [];
    if (a.params) {
      var pv = typeof a.params === 'object' ? Object.keys(a.params).map(function (p) { return '<tr><td>' + esc(p) + '</td><td>' + esc(a.params[p]) + '</td></tr>'; }).join('')
        : '<tr><td colspan="2">' + esc(a.params) + '</td></tr>';
      kv.push(['Parameters', '<table class="params-table">' + pv + '</table>']);
    }
    if (a.tools) kv.push(['Tools', esc(a.tools)]);
    if (a.feeds) kv.push(['Feeds', aText(a.feeds, num)]);
    if (kv.length) h += '<div class="gd-sec"><div class="kv">' + kv.map(function (r) { return '<div class="k">' + r[0] + '</div><div class="v">' + r[1] + '</div>'; }).join('') + '</div></div>';
    if (a.paths && a.paths.length) h += '<div class="gd-sec"><h3>Where it lives</h3>' + a.paths.map(function (p) { return '<div class="gpath mono">' + esc(p) + '</div>'; }).join('') + '</div>';
    var nodes = (a.nodes || []).filter(function (n) { return byId[n]; });
    h += '<div class="gd-sec"><h3>In the network</h3>' + (nodes.length ? nodes.map(function (n) { return '<div>' + nidLink(n) + ' ' + esc(trunc(byId[n].label, 60)) + '</div>'; }).join('')
      : '<div class="muted">Nothing recorded yet. When its results are added, link them with <code>sciweave analysis link ' + esc(k) + ' &lt;IDs&gt;</code>.</div>') + '</div>';
    var kids = aKids(k);
    if (kids.length) h += '<div class="gd-sec"><h3>Branches (' + kids.length + ')</h3>' + kids.map(function (c) {
      return '<div class="gd-top" data-akey="' + esc(c) + '"><span class="gnum">' + esc(num[c]) + '</span><div><b>' + esc(all[c].title) + '</b> <span class="muted">' + esc(aWhen(all[c])) + '</span>' +
        '<div class="muted" style="font-size:12.5px">' + esc(trunc(aPlain(all[c].summary, num), 150)) + '</div></div></div>';
    }).join('') + '</div>';
    var hist = a.history || [];
    h += '<div class="gd-sec"><h3>History</h3>' + (hist.length ? '<div class="ghist">' + hist.map(function (e) {
      return '<div class="gh"><span class="gh-d">' + esc(e.date || '') + '</span><span>' + aText(e.text, num) + '</span></div>';
    }).join('') + '</div>' : '<div class="muted">No dated events yet.</div>') +
      (LIVE ? '<form class="gh-add" data-alog="' + esc(k) + '"><input name="d" type="date" class="ctl"><input name="t" placeholder="Add what happened…" class="ctl" style="flex:1"><button class="btn" type="submit">Add</button></form>' : '') + '</div>';
    return h;
  }

  function guideOpen(k) {
    var all = A(), path = [], cur = k;
    while (cur) { path.unshift(cur); cur = all[cur].parent; }
    guide.path = path; store(guideKey(), path);
    renderGuide();
  }

  // ----------------------------------------------------------- history ----
  var histFilter = { actor: '', event: '', text: '' };
  function renderHistory() {
    var el = document.getElementById('history');
    if (!histMode) histMode = Object.keys(A()).length ? 'analyses' : 'activity';
    var seg = '<div class="seg" role="tablist">' + [['analyses', 'Analyses'], ['activity', 'Activity']].map(function (m) {
      return '<button class="' + (histMode === m[0] ? 'on' : '') + '" data-hmode="' + m[0] + '">' + m[1] + '</button>';
    }).join('') + '</div>';
    if (histMode === 'analyses') { el.innerHTML = seg + '<div id="guide"></div>'; renderGuide(); return; }
    var H = DATA.history;
    var actors = Array.from(new Set(H.map(function (h) { return h.actor; }))).sort();
    var events = Array.from(new Set(H.map(function (h) { return h.event; }))).sort();
    el.innerHTML = seg + '<div class="growth"><h3>Project growth</h3><div class="muted" style="font-size:12px">cumulative nodes and saved versions over time</div><svg id="growth-svg"></svg></div>' +
      '<div class="filters"><select class="ctl" id="hf-actor"><option value="">all actors</option>' + actors.map(function (a) { return '<option' + (a === histFilter.actor ? ' selected' : '') + '>' + esc(a) + '</option>'; }).join('') + '</select>' +
      '<select class="ctl" id="hf-event"><option value="">all events</option>' + events.map(function (a) { return '<option' + (a === histFilter.event ? ' selected' : '') + '>' + esc(a) + '</option>'; }).join('') + '</select>' +
      '<div class="search" style="min-width:220px"><input id="hf-text" placeholder="filter text / node id" value="' + esc(histFilter.text) + '"></div>' +
      '<span class="muted">' + H.length + ' events</span></div><div class="timeline" id="timeline"></div>';
    drawGrowth();
    drawTimeline();
    document.getElementById('hf-actor').onchange = function (e) { histFilter.actor = e.target.value; drawTimeline(); };
    document.getElementById('hf-event').onchange = function (e) { histFilter.event = e.target.value; drawTimeline(); };
    document.getElementById('hf-text').oninput = function (e) { histFilter.text = e.target.value.toLowerCase(); drawTimeline(); };
  }

  function drawTimeline() {
    var el = document.getElementById('timeline');
    var rows = DATA.history.filter(function (h) {
      if (histFilter.actor && h.actor !== histFilter.actor) return false;
      if (histFilter.event && h.event !== histFilter.event) return false;
      if (histFilter.text && JSON.stringify(h).toLowerCase().indexOf(histFilter.text) < 0) return false;
      return true;
    }).slice().reverse().slice(0, 600);
    var html = '', day = '';
    rows.forEach(function (h) {
      var d = h.ts.slice(0, 10);
      if (d !== day) { day = d; html += '<div class="day">' + esc(d) + '</div>'; }
      html += '<div class="ev"><span class="t">' + esc(h.ts.slice(11, 16)) + '</span><span class="actor">' + esc(h.actor) + '</span><span>' +
        esc(h.event.replace(/_/g, ' ')) + (h.node ? ' ' + nidLink(h.node) : '') + (h.detail ? ' <span class="muted">' + esc(trunc(h.detail, 140)) + '</span>' : '') +
        (h.why ? ' <span class="why">\u2014 why: ' + esc(trunc(h.why, 160)) + '</span>' : '') + '</span></div>';
    });
    el.innerHTML = html || '<div class="muted">No matching events.</div>';
  }

  function drawGrowth() {
    var svgEl = document.getElementById('growth-svg');
    var H = DATA.history;
    if (H.length < 2) { svgEl.outerHTML = '<div class="muted" style="padding:20px 0">Not enough history yet.</div>'; return; }
    // real timestamps from the graph: node creation and every saved version
    var evs = [];
    DATA.nodes.forEach(function (n) {
      evs.push({ t: n.created, k: 'nodes' });
      n.versions.forEach(function (v) { evs.push({ t: v.ts, k: 'versions' }); });
    });
    evs.sort(function (a, b) { return d3.ascending(a.t, b.t); });
    var c = { nodes: 0, versions: 0 }, ser = [];
    evs.forEach(function (e) { c[e.k]++; ser.push({ t: new Date(e.t), nodes: c.nodes, versions: c.versions }); });
    var W = svgEl.clientWidth || 800, Hh = 180, m = { l: 36, r: 80, t: 10, b: 24 };
    var svg = d3.select(svgEl).attr('viewBox', [0, 0, W, Hh]);
    var x = d3.scaleTime().domain(d3.extent(ser, function (d) { return d.t; })).range([m.l, W - m.r]);
    var y = d3.scaleLinear().domain([0, d3.max(ser, function (d) { return Math.max(d.nodes, d.versions); }) || 1]).nice().range([Hh - m.b, m.t]);
    svg.append('g').selectAll('line').data(y.ticks(4)).join('line').attr('class', 'gridline')
      .attr('x1', m.l).attr('x2', W - m.r).attr('y1', y).attr('y2', y);
    svg.append('g').attr('class', 'axis').attr('transform', 'translate(0,' + (Hh - m.b) + ')').call(d3.axisBottom(x).ticks(6).tickSizeOuter(0));
    svg.append('g').attr('class', 'axis').attr('transform', 'translate(' + m.l + ',0)').call(d3.axisLeft(y).ticks(4).tickSize(0)).call(function (g) { g.select('.domain').remove(); });
    var series = [{ key: 'nodes', color: cssVar('--cat-sources') }, { key: 'versions', color: cssVar('--cat-process') }];
    series.forEach(function (s) {
      svg.append('path').datum(ser).attr('fill', 'none').attr('stroke', s.color).attr('stroke-width', 2)
        .attr('d', d3.line().curve(d3.curveStepAfter).x(function (d) { return x(d.t); }).y(function (d) { return y(d[s.key]); }));
      var last = ser[ser.length - 1];
      svg.append('text').attr('x', x(last.t) + 6).attr('y', y(last[s.key])).attr('dy', '0.32em')
        .attr('fill', cssVar('--text-2')).attr('font-size', 12).text(last[s.key] + ' ' + s.key);
    });
    // crosshair + tooltip
    var cross = svg.append('line').attr('stroke', cssVar('--text-3')).attr('y1', m.t).attr('y2', Hh - m.b).style('display', 'none');
    var bis = d3.bisector(function (d) { return d.t; }).left;
    svg.append('rect').attr('x', m.l).attr('y', m.t).attr('width', W - m.l - m.r).attr('height', Hh - m.t - m.b).attr('fill', 'transparent')
      .on('mousemove', function (evt) {
        var t = x.invert(d3.pointer(evt)[0]), i = Math.min(ser.length - 1, Math.max(0, bis(ser, t) - 1)), d = ser[i];
        cross.style('display', null).attr('x1', x(d.t)).attr('x2', x(d.t));
        showTip(evt, '<div class="t">' + esc(d.t.toISOString().slice(0, 16).replace('T', ' ')) + '</div><div class="s">' + d.nodes + ' nodes · ' + d.versions + ' versions</div>');
      })
      .on('mouseleave', function () { cross.style('display', 'none'); hideTip(); });
  }

  document.getElementById('articles').addEventListener('click', function (evt) { var t = evt.target.closest('[data-goto]'); if (t) goTo(t.dataset.goto); });
  document.getElementById('history').addEventListener('click', function (evt) {
    var t = evt.target.closest('[data-goto]'); if (t) { goTo(t.dataset.goto); return; }
    var m = evt.target.closest('[data-hmode]'); if (m) { histMode = m.dataset.hmode; renderHistory(); return; }
    var pk = evt.target.closest('[data-apeek]');
    if (pk) {
      evt.preventDefault(); evt.stopPropagation();
      var key = pk.dataset.apeek;
      if (guide.peek[key]) {
        delete guide.peek[key];
        var at = guide.path.indexOf(key); if (at >= 0) guide.path = guide.path.slice(0, at);
      } else {
        guide.peek[key] = true;
        var par = A()[key].parent || null, lvl = par ? guide.path.indexOf(par) + 1 : 0;
        guide.path = guide.path.slice(0, lvl).concat([key]);  // open it right away
      }
      renderGuide();
      return;
    }
    var hb = evt.target.closest('[data-ahide]');
    if (hb) {
      evt.preventDefault(); evt.stopPropagation();
      var hk = hb.dataset.ahide, willHide = !A()[hk].hidden;
      if (!willHide) delete guide.peek[hk];
      post('api/analysis', { key: hk, hidden: willHide }).then(function (r) {
        toast(willHide ? 'Hidden from view (nothing deleted). "show hidden" brings it back.' : 'Back in view');
        return refresh(true);
      }).catch(function (e) { toast(e.message); });
      return;
    }
    var o = evt.target.closest('[data-aorg]');
    if (o) {
      var k = o.dataset.aorg;
      post('api/analysis', { key: k, organized: !A()[k].organized }).then(function (r) { toast(r.message); return refresh(true); })
        .catch(function (e) { toast(e.message); });
      return;
    }
    var a = evt.target.closest('[data-akey]');
    if (a) {
      var key = a.dataset.akey;
      if (a.classList.contains('grow') && !guide.q) {
        // clicking a row in column L selects it and opens its branches in column L+1
        var par = A()[key].parent || null, lvl = par ? guide.path.indexOf(par) + 1 : 0;
        guide.path = guide.path.slice(0, lvl).concat([key]); store(guideKey(), guide.path); renderGuide();
      } else { guide.q = ''; guideOpen(key); }
    }
  });
  document.getElementById('history').addEventListener('keydown', function (evt) {
    if ((evt.key === 'Enter' || evt.key === ' ') && evt.target.matches && evt.target.matches('[data-apeek],[data-ahide]')) {
      evt.preventDefault(); evt.target.click();
    }
  });
  document.getElementById('history').addEventListener('submit', function (evt) {
    var f = evt.target.closest('[data-alog]'); if (!f) return;
    evt.preventDefault();
    var text = f.elements.t.value.trim(); if (!text) return;
    post('api/analysis', { key: f.dataset.alog, log: text, date: f.elements.d.value }).then(function (r) { toast('added to the history'); return refresh(true); })
      .catch(function (e) { toast(e.message); });
  });

  // ---------------------------------------------------------- controls ----
  function setPage(p) {
    state.page = p;
    document.querySelectorAll('.tab').forEach(function (b) { b.classList.toggle('active', b.dataset.page === p); });
    document.querySelectorAll('.page').forEach(function (s) { s.classList.toggle('active', s.id === 'page-' + p); });
    if (p === 'network') render();
    if (p === 'articles') renderArticles();
    if (p === 'history') renderHistory();
  }
  function setView(v) { state.view = v; store('view', v); document.getElementById('view-select').value = v; render(); }

  document.querySelectorAll('.tab').forEach(function (b) { b.onclick = function () { setPage(b.dataset.page); }; });
  var viewSel = document.getElementById('view-select'); viewSel.value = state.view; viewSel.onchange = function () { setView(viewSel.value); };
  var grpSel = document.getElementById('group-select'); grpSel.value = state.groupBy;
  grpSel.onchange = function () { state.groupBy = grpSel.value; store('groupBy', state.groupBy); state.zoom = {}; render(); };
  [['show-refs', 'showRefs'], ['show-code', 'showCode'], ['show-params', 'showParams'], ['show-legend', 'showLegend'], ['show-hidden', 'showHidden']].forEach(function (p) {
    var el = document.getElementById(p[0]); el.checked = state[p[1]];
    el.onchange = function () { state[p[1]] = el.checked; store(p[1], el.checked); if (p[1] === 'showLegend') renderLegend(); else render(); };
  });
  document.getElementById('fit-btn').onclick = function (evt) {
    if (evt.shiftKey) { state.relayout = true; state.forceFit = true; render(); return; }  // shift+Fit: fresh layout
    if (state.fitCurrent) state.fitCurrent();
  };

  // legend: click a node type to hide it from the network, click again to show it
  function toggleType(t) {
    var i = state.hiddenTypes.indexOf(t);
    if (i >= 0) state.hiddenTypes.splice(i, 1); else state.hiddenTypes.push(t);
    store('hiddenTypes', state.hiddenTypes);
    state.forceFit = true;
    render();
  }
  var legendEl = document.getElementById('legend');
  legendEl.addEventListener('click', function (evt) {
    if (evt.target.closest('[data-showall]')) { state.hiddenTypes = []; store('hiddenTypes', []); state.forceFit = true; render(); return; }
    var row = evt.target.closest('.type-toggle');
    if (row) toggleType(row.dataset.type);
  });
  legendEl.addEventListener('keydown', function (evt) {
    var row = evt.target.closest && evt.target.closest('.type-toggle');
    if (row && (evt.key === 'Enter' || evt.key === ' ')) { evt.preventDefault(); toggleType(row.dataset.type); }
  });
  var searchEl = document.getElementById('search');
  searchEl.oninput = function () { state.search = searchEl.value; if (state.page !== 'network') setPage('network'); applyHighlight(); };

  document.getElementById('theme-toggle').onclick = function () {
    var cur = document.documentElement.getAttribute('data-theme') ||
      (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    var next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('sciweave-theme', next); } catch (e) {}
    setPage(state.page);
    if (state.selected) renderPanel();
  };

  // ask
  var askbar = document.getElementById('askbar'), askAnswer = document.getElementById('ask-answer');
  document.getElementById('ask-toggle').onclick = function () {
    if (state.page !== 'network') setPage('network');
    askbar.classList.toggle('open');
    if (askbar.classList.contains('open')) document.getElementById('ask-input').focus();
  };
  document.getElementById('ask-clear').onclick = function () {
    state.askHighlight = null; askAnswer.innerHTML = ''; document.getElementById('ask-input').value = ''; applyHighlight();
  };
  document.getElementById('ask-form').onsubmit = function (evt) {
    evt.preventDefault();
    var q = document.getElementById('ask-input').value.trim();
    if (!q) return;
    askAnswer.textContent = 'thinking…';
    var p = LIVE ? post('api/ask', { question: q }) : Promise.resolve(localAsk(q));
    p.then(function (res) {
      var ids = {}; (res.highlight || []).forEach(function (id) { ids[id] = true; });
      state.askHighlight = (res.highlight || []).length ? ids : null;
      askAnswer.innerHTML = esc(res.answer).replace(/\b([A-Z]{1,4}\d+[a-z]?)\b/g, function (m) { return byId[m] ? nidLink(m) : m; }) +
        '<div class="engine">answered by ' + esc(res.engine || 'local') + '</div>';
      applyHighlight();
    }).catch(function (err) { askAnswer.textContent = '⚠ ' + err.message; });
  };
  askAnswer.addEventListener('click', function (evt) { var t = evt.target.closest('[data-goto]'); if (t) select(t.dataset.goto); });

  // static-mode fallback for Ask: same idea as sciweave.ai.local_answer, simplified
  function localAsk(q) {
    var ql = q.toLowerCase(), ids = (q.match(/\b[A-Z]{1,4}\d+[a-z]?\b/g) || []).filter(function (x) { return byId[x]; });
    if (/stale|outdated|out of date|regenerate/.test(ql)) {
      var stale = DATA.nodes.filter(function (n) { return st(n.id).stale; }).map(function (n) { return n.id; });
      return { answer: stale.length ? stale.map(function (id) { return id + ' · ' + byId[id].label + ': ' + st(id).reasons[0]; }).join('\n') : 'Nothing is stale.', highlight: stale, engine: 'local (static)' };
    }
    if (ids.length) {
      var up = walk(ids[0], 'up');
      return { answer: ids[0] + ' · ' + byId[ids[0]].label + ' is built from: ' + (up.join(', ') || 'nothing recorded'), highlight: [ids[0]].concat(up), engine: 'local (static)' };
    }
    var m = searchMatches(q.replace(/\b(how|what|was|is|the|made|which|where)\b/gi, ' ')) || {};
    var hits = Object.keys(m);
    return { answer: hits.length ? 'Matching: ' + hits.join(', ') : 'No match. Try an id like F1 or a label word.', highlight: hits, engine: 'local (static)' };
  }

  document.addEventListener('keydown', function (evt) {
    if (evt.key === 'Escape') { if (askbar.classList.contains('open')) askbar.classList.remove('open'); else clearSelection(); }
    if (evt.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') { evt.preventDefault(); searchEl.focus(); }
  });

  // live refresh
  var liveBtn = document.getElementById('live-toggle');
  var liveOn = LIVE && load('live2', true), liveTimer = null, sig = '', lastStamp = null;
  function typing() {  // don't redraw under someone typing a note / history line
    var a = document.activeElement;
    return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA') && (panelEl.contains(a) || document.getElementById('history').contains(a));
  }
  function pollStamp() {
    if (document.hidden) return;
    fetch('api/stamp').then(function (r) { return r.json(); }).then(function (s) {
      var k = JSON.stringify([s.graph, s.history]);
      if (lastStamp !== null && k !== lastStamp && !typing()) refresh(false);
      if (!typing()) lastStamp = k;
      if (s.jobs) pollJobs();
    }).catch(function () {});
  }
  if (!LIVE) liveBtn.style.display = 'none';
  function signature(d) { return JSON.stringify([d.nodes, d.edges, d.status, d.history.length, d.analyses, d.hidden, d.organized, d.destination]); }
  function refresh(force) {
    if (!LIVE) return Promise.resolve();
    return fetch('api/graph').then(function (r) { return r.json(); }).then(function (d) {
      var s = signature(d);
      if (!force && s === sig) return;
      var before = {}; (DATA && DATA.nodes || []).forEach(function (n) { before[n.id] = true; });
      var now = Date.now(), added = d.nodes.filter(function (n) { return DATA && !before[n.id]; });
      state.newIds = state.newIds || {};
      added.forEach(function (n) { state.newIds[n.id] = now; });
      if (added.length) {
        toast(added.length === 1 ? 'new: ' + added[0].id + ' \u00b7 ' + added[0].label : added.length + ' new objects added');
        setTimeout(function () { if (state.page === 'network') render(); }, 6200);  // let the pulse fade
      }
      sig = s; DATA = d; index();
      setPage(state.page);
      if (state.selected && byId[state.selected]) renderPanel();
      else if (state.showingDest) renderProjectPanel();
      loadSuggestions(false);
    });
  }
  function setLive(on) {
    liveOn = on; store('live2', on); liveBtn.classList.toggle('on', on);
    liveBtn.title = on ? 'Live: updates appear on their own (click to pause)' : 'Paused: click to show updates live again';
    clearInterval(liveTimer); if (on) { pollStamp(); liveTimer = setInterval(pollStamp, 2000); }
  }
  liveBtn.onclick = function () { setLive(!liveOn); };

  window.addEventListener('resize', function () { clearTimeout(render._t); render._t = setTimeout(function () { if (state.page === 'network') render(); }, 200); });

  // ------------------------------------------------------------- boot ----
  function boot(d) {
    DATA = d; sig = signature(d); index();
    var m = /node=([^&]+)/.exec(location.hash);
    if (m && byId[decodeURIComponent(m[1])]) state.selected = decodeURIComponent(m[1]);
    // deep links: ?view=lineage&group=type&page=history&theme=dark
    var qs = {};
    location.search.replace(/^\?/, '').split('&').forEach(function (kv) { var p = kv.split('='); if (p[0]) qs[p[0]] = decodeURIComponent(p[1] || ''); });
    if (VIEWS[qs.view]) { state.view = qs.view; viewSel.value = qs.view; }
    if (!state.groupBy) state.groupBy = Object.keys(DATA.steps || {}).length ? 'step' : 'category';
    if (qs.group) { state.groupBy = qs.group; grpSel.value = qs.group; }
    grpSel.value = state.groupBy;
    if (qs.theme === 'dark' || qs.theme === 'light') document.documentElement.setAttribute('data-theme', qs.theme);
    if (qs.page === 'analyses') { qs.page = 'history'; histMode = 'analyses'; }
    if (qs.analysis && A()[qs.analysis]) {  // ?analysis=<key> opens that analysis in the guide
      qs.page = 'history'; histMode = 'analyses';
      var path = [], cur = qs.analysis;
      while (cur) { path.unshift(cur); cur = A()[cur].parent; }
      guide.path = path;
      path.forEach(function (k) { if (A()[k].hidden) guide.peek[k] = true; });  // a link to a hidden item peeks it
    }
    // an empty network with an analysis history opens on the guide
    var dflt = !DATA.nodes.length && Object.keys(DATA.analyses || {}).length ? 'history' : 'network';
    var startPage = ['network', 'articles', 'history'].indexOf(qs.page) >= 0 ? qs.page : dflt;
    // open the project panel BEFORE the first drawing: the network is then laid out for the width it
    // really has (otherwise it is drawn full-width, squeezed when the panel opens, and jumps on redraw)
    if (LIVE && !state.selected && qs.panel !== 'suggestions' && startPage === 'network') renderProjectPanel();
    setPage(startPage);
    if (state.selected) renderPanel();
    if (LIVE) {
      setLive(liveOn); loadSuggestions(qs.panel === 'suggestions'); pollJobs();
      document.getElementById('dest-hint').hidden = false;
    }
  }
  if (window.SCIWEAVE_DATA) boot(window.SCIWEAVE_DATA);
  else fetch('api/graph').then(function (r) { return r.json(); }).then(boot)
    .catch(function (e) { vizEl.innerHTML = '<div class="empty-hint">Could not load project data: ' + esc(e.message) + '</div>'; });
})();
