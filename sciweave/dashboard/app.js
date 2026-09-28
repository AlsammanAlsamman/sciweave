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
    groupBy: load('groupBy', 'category'),
    showRefs: load('showRefs', true),
    showCode: load('showCode', true),
    showParams: load('showParams', false),
    showLegend: load('showLegend', true),
    selected: null,
    selectedEdge: null,
    search: '',
    askHighlight: null,
    zoom: {}
  };

  // ------------------------------------------------------------- icons ----
  // 24x24 stroke icons, one per node type (drawn inside the node circle).
  var ICONS = {
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
        if (e.rel === 'documents' || e.rel === 'related') return;
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
      case 'article': return articleOf(n);
      case 'state': return st(n.id).state;
      default: return catOf(n);
    }
  }

  function isHiddenRef(n) { return !state.showRefs && n.mode === 'ref' && catOf(n) === 'outputs'; }

  function visible() {
    var nodes = DATA.nodes.filter(function (n) { return !isHiddenRef(n); });
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
    var html = '<div class="t">' + esc(e.source) + ' → ' + esc(e.target) + '</div><div class="s">' + esc(e.rel) +
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
    return 'edge ' + (hasParams(e) ? 'params' : 'plain') + (e.rel === 'code' ? ' code' : '') + (e.stale ? ' stale' : '');
  }
  function marker(e, kind) { return 'url(#arrow-' + (hasParams(e) ? 'params' : 'plain') + '-' + kind + ')'; }

  function drawNodes(layer, nodes) {
    var g = layer.selectAll('g.node-g').data(nodes, function (d) { return d.id; }).join('g')
      .attr('class', function (d) { return 'node-g' + (d.mode === 'ref' ? ' ref' : '') + (d.id === state.selected ? ' selected' : ''); })
      .attr('data-id', function (d) { return d.id; });
    g.each(function (d) {
      var el = d3.select(this), r = radius(d), s = st(d.id), fill = catColor(catOf(d));
      if (s.stale) el.append('circle').attr('class', 'ring-stale').attr('r', r + 4.5);
      else if (s.missing) el.append('circle').attr('class', 'ring-missing').attr('r', r + 4.5);
      else if (s.modified) el.append('circle').attr('class', 'ring-modified').attr('r', r + 4.5);
      el.append('circle').attr('class', 'body').attr('r', r).attr('fill', fill).attr('stroke', fill);
      var k = (r * 1.15) / 24;
      el.append('path').attr('class', 'icon').attr('d', ICONS[d.type] || ICONS.result)
        .attr('transform', 'translate(' + (-12 * k) + ',' + (-12 * k) + ') scale(' + k + ')')
        .attr('vector-effect', 'non-scaling-stroke').style('stroke', iconInk(fill));
      if (d.final_version) el.append('text').attr('class', 'star').attr('x', r * 0.55).attr('y', -r * 0.55)
        .attr('font-size', 13).attr('fill', cssVar('--final')).text('★');
    });
    g.on('mouseenter', function (evt, d) { showTip(evt, nodeTip(d)); })
      .on('mousemove', function (evt, d) { showTip(evt, nodeTip(d)); })
      .on('mouseleave', hideTip)
      .on('click', function (evt, d) { evt.stopPropagation(); select(d.id); });
    return g;
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
    var FLOW = ['sources', 'process', 'outputs', 'writing', 'notes'];
    var groups = Array.from(new Set(nodes.map(function (d) { return d.group; }))).sort(function (a, b) {
      var ia = FLOW.indexOf(a), ib = FLOW.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || d3.ascending(a, b);
    });
    var gColor = {};
    groups.forEach(function (gname) { gColor[gname] = haloColor(gname); });

    var hullLayer = ctx.g.append('g'), labelLayer = ctx.g.append('g'), linkLayer = ctx.g.append('g'),
      badgeLayer = ctx.g.append('g'), nodeLayer = ctx.g.append('g');

    // every group gets an anchor on a ring; nodes start at (and are pulled to) their anchor,
    // so halos separate instead of piling up in the middle
    var ringR = groups.length > 1 ? Math.max(170, Math.sqrt(nodes.length) * 48) : 0;
    var anchor = {};
    groups.forEach(function (gname, i) {
      var a = (i / Math.max(1, groups.length)) * 2 * Math.PI - Math.PI / 2;
      anchor[gname] = [ctx.w / 2 + Math.cos(a) * ringR, ctx.h / 2 + Math.sin(a) * ringR];
    });
    nodes.forEach(function (d, i) {
      var an = anchor[d.group];
      d.x = an[0] + Math.cos(i * 2.4) * 30; d.y = an[1] + Math.sin(i * 2.4) * 30;
    });

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

    var sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(function (d) { return d.id; }).distance(function (l) { return l.e.rel === 'code' ? 60 : 95; })
        .strength(function (l) { return l.source.group === l.target.group ? 0.4 : 0.04; }))
      .force('charge', d3.forceManyBody().strength(-300).distanceMax(420))
      .force('collide', d3.forceCollide().radius(function (d) { return radius(d.n) + 26; }).strength(0.9))
      .force('x', d3.forceX(function (d) { return anchor[d.group][0]; }).strength(0.07))
      .force('y', d3.forceY(function (d) { return anchor[d.group][1]; }).strength(0.07))
      .force('cluster', clusterForce(0.25));

    var linkSel = linkLayer.selectAll('path.edge').data(links).join('path')
      .attr('class', function (l) { return edgeClass(l.e); }).attr('marker-end', function (l) { return marker(l.e, 'tip'); });
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
      return 'M' + (sx + dx / len * rs) + ',' + (sy + dy / len * rs) + 'L' + (tx - dx / len * rt) + ',' + (ty - dy / len * rt);
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
      hullLayer.selectAll('path').data(hulls, function (d) { return d.k; }).join('path')
        .attr('d', function (d) { return lineGen(d.pts); })
        .attr('fill', function (d) { return gColor[d.k]; }).attr('fill-opacity', cssVar('--halo-opacity'))
        .attr('stroke', function (d) { return gColor[d.k]; }).attr('stroke-opacity', 0.45).attr('stroke-width', 1.2);
      labelLayer.selectAll('text').data(hulls, function (d) { return d.k; }).join('text').attr('class', 'halo-label')
        .attr('text-anchor', 'middle')
        .attr('x', function (d) { return d3.mean(d.pts, function (p) { return p[0]; }); })
        .attr('y', function (d) { return d3.min(d.pts, function (p) { return p[1]; }) - 6; })
        .text(function (d) { return d.k; });
      paramBadges(badgeLayer, v.edges, function (e) {
        var s = idx[e.source], t = idx[e.target]; return [(s.x + t.x) / 2, (s.y + t.y) / 2];
      });
    }

    sim.stop();
    for (var i = 0; i < 320; i++) sim.tick();
    tick();
    restoreOrFit(ctx, nodes.map(function (d) { return [d.x, d.y]; }));
    sim.on('tick', tick);
    sim.alpha(0.03).restart();
    state.fitCurrent = function () { fit(ctx, nodes.map(function (d) { return [d.x, d.y]; }), true); };
    return function () { sim.stop(); };
  }

  function haloColor(key) {
    if (state.groupBy === 'category') return catColor(key);
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
    var rootData = { id: '__root', name: DATA.project.name, children: Object.keys(groups).sort().map(function (k) {
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
    document.getElementById('counts').textContent = DATA.nodes.length + ' nodes · ' + DATA.edges.length + ' links' + (stale ? ' · ' + stale + ' stale' : '');
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
    var html = '<h4>Node types</h4><div class="grid">';
    types.forEach(function (t) {
      var fill = catColor(DATA.types[t].category);
      html += '<div class="row"><svg width="18" height="18" viewBox="-9 -9 18 18"><circle r="8.5" fill="' + fill + '"/>' +
        '<path d="' + ICONS[t] + '" transform="translate(-5.5,-5.5) scale(0.46)" fill="none" stroke="' + iconInk(fill) + '" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
        esc(t) + ' <span class="muted">' + esc(DATA.types[t].prefix) + '</span></div>';
    });
    html += '</div><h4>Links &amp; state</h4><div class="grid">' +
      '<div class="row"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" stroke="' + cssVar('--edge-params') + '" stroke-width="2"/></svg>has parameters</div>' +
      '<div class="row"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" stroke="' + cssVar('--edge-plain') + '" stroke-width="2"/></svg>no parameters</div>' +
      '<div class="row"><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" stroke="' + cssVar('--text-3') + '" stroke-width="2" stroke-dasharray="5 3"/></svg>stale link</div>' +
      '<div class="row"><svg width="18" height="18"><circle cx="9" cy="9" r="7" fill="none" stroke="' + cssVar('--stale') + '" stroke-width="2.5"/></svg>stale node</div>' +
      '<div class="row"><svg width="18" height="18"><circle cx="9" cy="9" r="7" fill="none" stroke="' + cssVar('--text-2') + '" stroke-width="1.5" stroke-dasharray="3 2"/></svg>modified on disk</div>' +
      '<div class="row"><span class="star" style="width:18px;text-align:center">★</span>has final version</div>' +
      '<div class="row"><svg width="18" height="18"><circle cx="9" cy="9" r="7" fill="' + cssVar('--text-3') + '" fill-opacity="0.55" stroke="' + cssVar('--text-3') + '" stroke-dasharray="2 2"/></svg>reference (not copied)</div>' +
      '</div>';
    el.innerHTML = html;
  }

  // ------------------------------------------------------- side panel ----
  function select(id) {
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
    state.selected = null; state.selectedEdge = null;
    try { history.replaceState(null, '', '#'); } catch (e) {}
    panelEl.classList.remove('open');
    if (state.view === 'lineage') render(); else applyHighlight();
  }

  function nidLink(id) {
    var n = byId[id];
    return '<a class="nid" data-goto="' + esc(id) + '" title="' + esc(n ? n.label : '') + '">' + esc(id) + '</a>';
  }

  function provTree(id, depth, seen) {
    if (depth > 7) return '';
    var html = '';
    inEdges[id].filter(function (e) { return e.rel !== 'documents' && e.rel !== 'related'; }).sort(function (a, b) { return d3.ascending(a.rel === 'code', b.rel === 'code') || d3.ascending(a.source, b.source); })
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
      (n.final_version ? '<span class="chip"><span class="star">★</span> final v' + n.final_version + '</span>' : '') +
      (n.groups || []).map(function (g) { return '<span class="chip">#' + esc(g) + '</span>'; }).join('') + '</div>';
    html += '<div class="actions">' +
      '<button class="btn primary" data-act="save" ' + (LIVE && n.path ? '' : 'disabled') + ' title="Snapshot the file as a new version">Save version</button>' +
      '<button class="btn" data-act="final" ' + (LIVE && n.current_version ? '' : 'disabled') + '>Mark current final</button>' +
      '<button class="btn" data-act="note" ' + (LIVE ? '' : 'disabled') + '>Add note</button>' +
      '<button class="btn" data-act="lineage">Lineage view</button></div>' +
      (LIVE ? '' : '<div class="muted" style="font-size:12px;margin-top:6px">Static export: run <code>sciweave serve</code> for save / final / notes.</div>') +
      '</div>';

    if (s.reasons && s.reasons.length) html += '<div class="sec"><h3>Attention</h3>' + s.reasons.map(function (r) { return '<div>' + esc(r) + '</div>'; }).join('') + '</div>';
    html += '<div class="sec"><h3>About</h3><div class="kv">' +
      (n.description ? '<div class="k">description</div><div class="v">' + esc(n.description) + '</div>' : '') +
      '<div class="k">path</div><div class="v mono">' + esc(n.path || '—') + '</div>' +
      (n.meta && n.meta.origin ? '<div class="k">copied from</div><div class="v mono">' + esc(n.meta.origin) + '</div>' : '') +
      '<div class="k">created</div><div class="v">' + esc(when(n.created)) + '</div>' +
      '<div class="k">updated</div><div class="v">' + esc(when(n.updated)) + '</div>' +
      ((n.tags || []).length ? '<div class="k">tags</div><div class="v">' + esc(n.tags.join(', ')) + '</div>' : '') +
      Object.keys(n.meta || {}).filter(function (k) { return k !== 'origin'; }).map(function (k) {
        var val = n.meta[k]; if (typeof val === 'object') val = JSON.stringify(val);
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

    if (n.versions.length) {
      html += '<div class="sec"><h3>Versions</h3><table class="versions"><tr><th>v</th><th>when</th><th>message</th><th></th></tr>' +
        n.versions.slice().reverse().map(function (v) {
          var acts = '';
          if (v.stored) acts += '<a class="nid" data-vprev="' + v.v + '">view</a> ';
          if (LIVE && v.v !== n.final_version) acts += '<a class="nid" data-vfinal="' + v.v + '">final</a> ';
          if (LIVE && v.stored && v.v !== n.current_version) acts += '<a class="nid" data-vrestore="' + v.v + '">restore</a>';
          var parents = Object.keys(v.parents || {}).map(function (k) { return k + '@v' + v.parents[k]; }).join(', ');
          return '<tr><td>' + (v.v === n.final_version ? '<span class="star">★</span>' : '') + 'v' + v.v + '</td><td>' + esc(when(v.ts)) +
            '<div class="muted">' + esc(v.actor || '') + '</div></td><td>' + esc(v.message || '') +
            (parents ? '<div class="muted">from ' + esc(parents) + '</div>' : '') + (v.stored ? '' : '<div class="muted">fingerprint only</div>') +
            '</td><td>' + acts + '</td></tr>';
        }).join('') + '</table></div>';
    }
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
    var t = evt.target.closest('[data-goto],[data-act],[data-vprev],[data-vfinal],[data-vrestore],#panel-close');
    if (!t) return;
    var n = byId[state.selected];
    if (t.id === 'panel-close') return clearSelection();
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
      case 'save': var m = prompt('Version message (what changed?)', ''); if (m !== null) act(post('api/save', { node: n.id, message: m })); break;
      case 'final': act(post('api/final', { node: n.id })); break;
      case 'note': var txt = prompt('Note for ' + n.id, ''); if (txt) act(post('api/note', { node: n.id, text: txt })); break;
      case 'lineage': setView('lineage'); break;
    }
  });

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

  // ----------------------------------------------------------- history ----
  var histFilter = { actor: '', event: '', text: '' };
  function renderHistory() {
    var el = document.getElementById('history');
    var H = DATA.history;
    var actors = Array.from(new Set(H.map(function (h) { return h.actor; }))).sort();
    var events = Array.from(new Set(H.map(function (h) { return h.event; }))).sort();
    el.innerHTML = '<div class="growth"><h3>Project growth</h3><div class="muted" style="font-size:12px">cumulative nodes and saved versions over time</div><svg id="growth-svg"></svg></div>' +
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
        esc(h.event.replace(/_/g, ' ')) + (h.node ? ' ' + nidLink(h.node) : '') + (h.detail ? ' <span class="muted">' + esc(trunc(h.detail, 140)) + '</span>' : '') + '</span></div>';
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
  document.getElementById('history').addEventListener('click', function (evt) { var t = evt.target.closest('[data-goto]'); if (t) goTo(t.dataset.goto); });

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
  [['show-refs', 'showRefs'], ['show-code', 'showCode'], ['show-params', 'showParams'], ['show-legend', 'showLegend']].forEach(function (p) {
    var el = document.getElementById(p[0]); el.checked = state[p[1]];
    el.onchange = function () { state[p[1]] = el.checked; store(p[1], el.checked); if (p[1] === 'showLegend') renderLegend(); else render(); };
  });
  document.getElementById('fit-btn').onclick = function () { if (state.fitCurrent) state.fitCurrent(); };
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
  var liveOn = LIVE && load('live', false), liveTimer = null, sig = '';
  if (!LIVE) liveBtn.style.display = 'none';
  function signature(d) { return JSON.stringify([d.nodes, d.edges, d.status, d.history.length]); }
  function refresh(force) {
    if (!LIVE) return Promise.resolve();
    return fetch('api/graph').then(function (r) { return r.json(); }).then(function (d) {
      var s = signature(d);
      if (!force && s === sig) return;
      sig = s; DATA = d; index();
      setPage(state.page);
      if (state.selected && byId[state.selected]) renderPanel();
    });
  }
  function setLive(on) {
    liveOn = on; store('live', on); liveBtn.classList.toggle('on', on);
    clearInterval(liveTimer); if (on) liveTimer = setInterval(function () { refresh(false); }, 5000);
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
    if (qs.group) { state.groupBy = qs.group; grpSel.value = qs.group; }
    if (qs.theme === 'dark' || qs.theme === 'light') document.documentElement.setAttribute('data-theme', qs.theme);
    setPage(['network', 'articles', 'history'].indexOf(qs.page) >= 0 ? qs.page : 'network');
    if (state.selected) renderPanel();
    if (LIVE) setLive(liveOn);
  }
  if (window.SCIWEAVE_DATA) boot(window.SCIWEAVE_DATA);
  else fetch('api/graph').then(function (r) { return r.json(); }).then(boot)
    .catch(function (e) { vizEl.innerHTML = '<div class="empty-hint">Could not load project data: ' + esc(e.message) + '</div>'; });
})();
