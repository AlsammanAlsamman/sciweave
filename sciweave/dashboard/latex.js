/* SciWeave LaTeX editor. Plain ES5, no build step, no CDN.
   The user edits and saves; Claude's edits arrive as proposals (sciweave tex propose) that
   appear here live and are accepted or rejected one change at a time. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var ed = $('ed'), hl = $('hl');
  var S = {
    article: window.SW_ARTICLE || '', summary: null, file: null, base: null, dirty: false,
    mode: 'edit', reviewing: null, stamp: null, compiling: false, saving: false,
    showPdf: true, errors: [], propSig: '', pdfStamp: null, compileTs: null, ev: null, docShown: null
  };

  // ------------------------------------------------------------ helpers ----
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/[<]/g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function qs(o) { var a = []; for (var k in o) if (o[k] != null) a.push(encodeURIComponent(k) + '=' + encodeURIComponent(o[k])); return a.join('&'); }
  function get(path, params) {
    params = params || {}; params.article = S.article;
    return fetch('api/tex/' + path + '?' + qs(params), { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (j) { if (j && j.error) throw new Error(j.error); return j; });
  }
  function post(path, body) {
    body = body || {}; body.article = S.article;
    return fetch('api/tex/' + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (j && j.error) throw new Error(j.error); return j; });
  }
  function toast(msg, ms) {
    var t = $('toast'); t.textContent = msg; t.hidden = false; t.style.display = 'block';
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.style.display = 'none'; }, ms || 3200);
  }
  function status(msg) { $('status').textContent = msg || ''; }
  function who(a) { return a === 'claude' ? 'Claude' : a === 'user' ? 'you' : (a || '?'); }
  function ago(ts) {
    if (!ts) return '';
    var s = (Date.now() - new Date(ts).getTime()) / 1000;
    if (s < 60) return 'just now'; if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return Math.round(s / 3600) + ' h ago'; return ts.slice(0, 10);
  }
  function setDirty(d) { S.dirty = d; $('dirty-dot').hidden = !d; }

  // -------------------------------------------------------- highlighter ----
  var TOK = /(\\(?:ev|figref|tabref|supref)(?:\[[^\]]*\])?\{[^}]*\})|(\\(?:begin|end)\{[^}]*\})|(\\(?:part|chapter|section|subsection|subsubsection|paragraph)\*?)|(\\[A-Za-z@]+\*?|\\.)|(\$[^$]*\$)|(%.*$)|([{}\[\]])/g;
  function hlLine(line) {
    var out = '', last = 0, m;
    TOK.lastIndex = 0;
    while ((m = TOK.exec(line)) !== null) {
      out += esc(line.slice(last, m.index));
      var cls = m[1] ? (m[1].indexOf('\\ev{') === 0 ? 'ev' : 'rf') : m[2] ? 'e' : m[3] ? 's' : m[4] ? 'c' : m[5] ? 'm' : m[6] ? 'k' : 'b';
      out += '<span class="' + cls + '">' + esc(m[0]) + '</span>';
      last = TOK.lastIndex;
      if (m[0].length === 0) TOK.lastIndex++;
    }
    return out + esc(line.slice(last));
  }
  function marks() {
    // line numbers (1-based) to flag: compile errors in this file, and where open proposals apply
    var err = {}, prop = {};
    S.errors.forEach(function (e) { if (e.line && (!e.file || e.file === S.file || e.file.replace(/^\.\//, '') === S.file)) err[e.line] = 1; });
    if (S.summary) {
      var lines = ed.value.split('\n');
      S.summary.proposals.forEach(function (pr) {
        if (pr.file !== S.file) return;
        pr.hunks.forEach(function (h) {
          if (h.state !== 'open') return;
          var probe = (h.old[0] || (h.before.length ? h.before[h.before.length - 1] : '') || '').replace(/\r?\n$/, '');
          var at = probe ? lines.indexOf(probe) : h.start;
          if (at < 0) at = Math.min(h.start, lines.length - 1);
          var n = h.old.length ? h.old.length : 1;
          for (var i = 0; i < n; i++) prop[at + 1 + i + (h.old.length ? 0 : 1)] = 1;
        });
      });
    }
    return { err: err, prop: prop };
  }
  var hlQueued = false;
  function renderHL() {
    hlQueued = false;
    var lines = ed.value.split('\n'), mk = marks(), html = [];
    for (var i = 0; i < lines.length; i++) {
      var n = i + 1, cls = 'ln' + (mk.err[n] ? ' err' : '') + (mk.prop[n] ? ' prop' : '');
      html.push('<div class="' + cls + '" data-n="' + n + '">' + (hlLine(lines[i]) || '') + '</div>');
    }
    hl.innerHTML = html.join('');
    hl.scrollTop = ed.scrollTop;
  }
  function queueHL() { if (!hlQueued) { hlQueued = true; (window.requestAnimationFrame || setTimeout)(renderHL); } }
  ed.addEventListener('input', function () { setDirty(true); queueHL(); });
  ed.addEventListener('scroll', function () { hl.scrollTop = ed.scrollTop; });
  ed.addEventListener('keydown', function (e) {
    if (e.key === 'Tab' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      var s = ed.selectionStart, t = ed.selectionEnd;
      ed.value = ed.value.slice(0, s) + '  ' + ed.value.slice(t);
      ed.selectionStart = ed.selectionEnd = s + 2;
      setDirty(true); queueHL();
    }
  });
  function gotoLine(n) {
    var lines = ed.value.split('\n'), off = 0;
    for (var i = 0; i < Math.min(n - 1, lines.length); i++) off += lines[i].length + 1;
    ed.focus(); ed.setSelectionRange(off, off + (lines[n - 1] || '').length);
    var el = hl.querySelector('.ln[data-n="' + n + '"]');
    if (el) { ed.scrollTop = Math.max(0, el.offsetTop - 120); hl.scrollTop = ed.scrollTop; }
  }

  // ------------------------------------------------------------- files ----
  function renderFiles() {
    var files = S.summary ? S.summary.files : [];
    $('files').innerHTML = files.length ? files.map(function (f) {
      var parts = f.path.split('/'), name = parts.pop();
      return '<div class="file-item' + (f.path === S.file ? ' active' : '') + '" data-file="' + esc(f.path) + '" title="' + esc(f.path) + '">' +
        (parts.length ? '<span class="dir">' + esc(parts.join('/')) + '/</span>' : '') + esc(name) +
        (f.pending ? '<span class="pend" title="changes to review">' + f.pending + '</span>' : f.generated ? '<span class="node" title="written by a script: edits here are overwritten">generated</span>' : (f.node ? '<span class="node">' + esc(f.node) + '</span>' : '')) + '</div>';
    }).join('') : '<div class="empty-small">No .tex files yet.</div>';
  }
  $('files').addEventListener('click', function (e) {
    var t = e.target.closest('[data-file]'); if (t) openFile(t.getAttribute('data-file'));
  });

  function openFile(path, keepView) {
    if (S.tab !== 'tex') return showTab('tex').then(function () { return openFile(path, keepView); });
    var go = function () {
      return get('file', { path: path }).then(function (f) {
        var same = path === S.file, top = ed.scrollTop, sel = [ed.selectionStart, ed.selectionEnd];
        S.file = f.path; S.base = f.hash; ed.value = f.text; setDirty(false);
        try { localStorage.setItem('sw-tex-file-' + S.article, f.path); } catch (e) {}
        $('cur-file').textContent = f.path;
        if (same && keepView) { ed.scrollTop = top; try { ed.setSelectionRange(sel[0], sel[1]); } catch (e) {} } else ed.scrollTop = 0;
        renderHL(); renderFiles(); renderBanner();
        if (S.mode === 'review' && !keepView) showEditor();
      });
    };
    if (S.dirty && path !== S.file) return save().then(go, function () {});
    return go().catch(function (e) { toast(e.message); });
  }

  function save(opts) {
    opts = opts || {};
    if (!S.file) return Promise.resolve();
    S.saving = true; status('Saving…');
    return post('save', { path: S.file, text: ed.value, base_hash: opts.force ? null : S.base }).then(function (r) {
      S.base = r.hash; setDirty(false); S.saving = false; status('Saved ' + new Date().toLocaleTimeString());
      hideBanner('conflict'); return r;
    }, function (e) {
      S.saving = false; status('');
      if (/changed on disk/.test(e.message)) showConflict(); else toast(e.message);
      throw e;
    });
  }

  // ---------------------------------------------------------- banners ----
  var banners = {};
  function hideBanner(k) { delete banners[k]; renderBanner(); }
  function renderBanner() {
    var b = $('banner'), keys = Object.keys(banners);
    if (S.tab && S.tab !== 'tex') {  // a note tab: only its own conflict banner
      if (!banners.conflict) { b.hidden = true; return; }
    }
    var mine = S.summary ? S.summary.proposals.filter(function (p) { return p.file === S.file; }) : [];
    if (mine.length && S.mode === 'edit' && S.tab === 'tex') {
      banners.props = { cls: 'info', html: '<b>' + mine.length + ' proposed change' + (mine.length > 1 ? 's' : '') + '</b> for this file (blue bars in the margin). ' +
        mine.map(function (p) { return '<button class="btn small" data-review="' + esc(p.id) + '">Review ' + esc(p.id) + '</button>'; }).join(' ') };
    } else delete banners.props;
    keys = Object.keys(banners);
    if (!keys.length) { b.hidden = true; b.innerHTML = ''; return; }
    var k = banners.conflict ? 'conflict' : keys[0];
    b.className = 'tex-banner ' + banners[k].cls; b.innerHTML = banners[k].html; b.hidden = false;
  }
  function showConflict() {
    banners.conflict = { cls: 'warn', html: '<b>This file changed on disk</b> while you were editing (another window, or an accepted change). ' +
      '<button class="btn small" data-act="reload">Load the disk version (discard my edits)</button> ' +
      '<button class="btn small" data-act="overwrite">Keep my version (overwrite)</button>' };
    renderBanner();
  }
  $('banner').addEventListener('click', function (e) {
    var t = e.target.closest('button'); if (!t) return;
    if (t.getAttribute('data-review')) return openReview(t.getAttribute('data-review'));
    var a = t.getAttribute('data-act');
    if (a === 'reload') { hideBanner('conflict'); openFile(S.file, true); }
    if (a === 'overwrite') save({ force: true }).then(function () { toast('Saved your version'); });
  });

  // ------------------------------------------------------- proposals ----
  function renderProps() {
    var prs = S.summary ? S.summary.proposals : [];
    $('prop-count').textContent = prs.length || '';
    $('props').innerHTML = prs.length ? prs.map(function (p) {
      var open = p.hunks.filter(function (h) { return h.state === 'open'; }).length;
      return '<div class="prop-item' + (S.reviewing === p.id && S.mode === 'review' ? ' active' : '') + '" data-review="' + esc(p.id) + '">' +
        '<div class="pi-top"><span class="pi-id">' + esc(p.id) + '</span><span class="pi-file">' + esc(p.file) + '</span></div>' +
        (p.message ? '<div class="pi-msg">' + esc(p.message) + '</div>' : '') +
        '<div class="pi-meta">' + open + ' of ' + p.hunks.length + ' change' + (p.hunks.length > 1 ? 's' : '') + ' open · ' + esc(who(p.actor)) + ' · ' + esc(ago(p.created)) + '</div></div>';
    }).join('') : '<div class="empty-small">Nothing to review. Claude\'s edits appear here as proposals.</div>';
  }
  $('props').addEventListener('click', function (e) {
    var t = e.target.closest('[data-review]'); if (t) openReview(t.getAttribute('data-review'));
  });

  function findProp(id) {
    var prs = S.summary ? S.summary.proposals : [];
    for (var i = 0; i < prs.length; i++) if (prs[i].id === id) return prs[i];
    return null;
  }
  function openReview(id) {
    var pr = findProp(id);
    if (!pr) { toast(id + ' is no longer open'); return; }
    if (S.tab !== 'tex') { showTab('tex').then(function () { openReview(id); }); return; }
    var go = function () {
      S.mode = 'review'; S.reviewing = id;
      $('editor-wrap').hidden = true; $('review').hidden = false;
      renderReview(pr); renderProps(); renderBanner();
    };
    if (S.dirty) save().then(go, function () {}); else go();
  }
  function showEditor() {
    S.mode = 'edit'; S.reviewing = null;
    $('review').hidden = true; $('editor-wrap').hidden = false;
    renderProps(); renderBanner(); queueHL();
  }
  function clip(lines, head) {
    var s = lines.join('');
    if (s.length <= 220) return s;
    return head ? '… ' + s.slice(-220) : s.slice(0, 220) + ' …';
  }
  function renderReview(pr) {
    var mono = !/\.tex$/.test(pr.file), open = pr.hunks.filter(function (h) { return h.state === 'open'; }).length;
    var h = '<div class="rv-head"><h2>' + esc(pr.id) + ' · ' + esc(pr.file) + '</h2><span class="spacer"></span>' +
      (open ? '<button class="btn ok" data-all="accept">Accept all ' + open + '</button><button class="btn no" data-all="reject">Reject all</button>' : '') +
      '<button class="btn" data-act="close">Back to editor</button></div>' +
      '<div class="rv-sub">' + (pr.message ? esc(pr.message) + ' · ' : '') + 'proposed by ' + esc(who(pr.actor)) + ' · ' + esc(ago(pr.created)) +
      ' · <ins style="background:var(--ins-bg);color:var(--ins-ink);text-decoration:none">added</ins> / <del style="background:var(--del-bg);color:var(--del-ink)">removed</del></div>';
    h += pr.hunks.map(function (k) {
      var body = '<span class="ctx">' + esc(clip(k.before, true)) + '</span>' +
        k.words.map(function (w) {
          return w[0] === '=' ? esc(w[1]) : w[0] === '-' ? '<del>' + esc(w[1]) + '</del>' : '<ins>' + esc(w[1]) + '</ins>';
        }).join('') + '<span class="ctx">' + esc(clip(k.after, false)) + '</span>';
      return '<div class="hunk ' + esc(k.state) + '"><div class="hunk-h"><b>Change ' + k.n + '</b><span>line ' + (k.start + 1) + '</span>' +
        '<span class="spacer"></span>' + (k.state === 'open'
          ? '<button class="btn small ok" data-hunk="' + k.n + '" data-do="accept">Accept</button><button class="btn small no" data-hunk="' + k.n + '" data-do="reject">Reject</button>'
          : '<span class="state-chip ' + esc(k.state) + '">' + esc(k.state === 'conflict' ? 'conflict: text changed, edit by hand' : k.state) + '</span>') +
        '</div><div class="hunk-body' + (mono ? ' mono' : '') + '">' + body + '</div></div>';
    }).join('');
    $('review').innerHTML = h;
  }
  $('review').addEventListener('click', function (e) {
    var t = e.target.closest('button'); if (!t) return;
    if (t.getAttribute('data-act') === 'close') return showEditor();
    var id = S.reviewing, body = { id: id, accept: [], reject: [] };
    if (t.getAttribute('data-all')) body[t.getAttribute('data-all')] = 'all';
    else body[t.getAttribute('data-do')] = [parseInt(t.getAttribute('data-hunk'), 10)];
    t.disabled = true;
    post('resolve', body).then(function (r) {
      if (r.conflicts && r.conflicts.length) toast('Change ' + r.conflicts.join(', ') + ': the text changed since it was proposed; edit it by hand', 6000);
      return refresh().then(function () {
        var pr = r.proposal;
        if (pr.file === S.file && !S.dirty) openFile(S.file, true);
        if (pr.status === 'open') renderReview(pr);
        else { toast(pr.id + ' ' + pr.status); showEditor(); if (pr.file !== S.file) openFile(pr.file); }
      });
    }).catch(function (e) { t.disabled = false; toast(e.message); });
  });

  // ---------------------------------------------------------- compile ----
  function renderCompile(c) {
    var info = $('compile-info'), errs = $('errors');
    if (!c) { info.textContent = S.summary && !S.summary.engine ? 'No LaTeX engine found on this machine' : 'Not compiled yet'; info.className = 'compile-info'; errs.hidden = true; return; }
    S.errors = c.errors || [];
    info.className = 'compile-info ' + (c.ok ? 'good' : 'bad');
    info.textContent = (c.ok ? 'Compiled' : 'Failed: ' + S.errors.length + ' error' + (S.errors.length === 1 ? '' : 's')) +
      ' · ' + c.seconds + ' s · ' + ago(c.ts) + (c.warnings && c.warnings.length ? ' · ' + c.warnings.length + ' warning' + (c.warnings.length > 1 ? 's' : '') : '') +
      (c.ok ? '' : ' · PDF below is the last good one');
    errs.innerHTML = S.errors.map(function (e) {
      return '<div class="err-item" data-ef="' + esc(e.file || '') + '" data-el="' + (e.line || '') + '"><b>' + esc((e.file || '?') + (e.line ? ':' + e.line : '')) + '</b> ' + esc(e.message) + '</div>';
    }).join('');
    errs.hidden = !S.errors.length;
    $('log').textContent = c.tail || '';
    queueHL();
  }
  $('errors').addEventListener('click', function (e) {
    var t = e.target.closest('.err-item'); if (!t) return;
    var f = t.getAttribute('data-ef'), n = parseInt(t.getAttribute('data-el'), 10);
    var jump = function () { if (n) gotoLine(n); };
    if (f && f !== S.file) openFile(f).then(jump); else { if (S.mode !== 'edit') showEditor(); jump(); }
  });
  $('btn-log').addEventListener('click', function () { $('log').hidden = !$('log').hidden; });
  $('btn-pdfwin').addEventListener('click', function () {
    // a named window: clicking again brings the same window back instead of opening another
    window.open('latex-pdf.html?article=' + encodeURIComponent(S.article), 'sciweave-pdf-' + S.article,
      'popup,width=980,height=1100');
  });

  function compile() {
    if (S.compiling) return;
    if (S.tab !== 'tex' && S.note.dirty) { saveNote().then(function () { S.compiling = false; compile(); }, function () {}); S.compiling = true; return; }
    S.compiling = true; $('btn-compile').disabled = true; status('Compiling…');
    var body = S.dirty ? { save: { path: S.file, text: ed.value, base_hash: S.base } } : {};
    post('compile', body).then(function (c) {
      if (S.dirty) { setDirty(false); get('file', { path: S.file }).then(function (f) { S.base = f.hash; }); }
      renderCompile(c); reloadPdf(); loadEvidence(); status(c.ok ? 'Compiled' : 'Compile failed');
    }).catch(function (e) {
      if (/changed on disk/.test(e.message)) showConflict(); else toast(e.message, 6000);
      status('');
    }).then(function () { S.compiling = false; $('btn-compile').disabled = false; });
  }
  function reloadPdf() {
    var f = $('pdf');
    if (S.docShown) return;  // an evidence document is open in the pane; "Back" reloads
    get('stamp').then(function (st) {
      S.pdfStamp = st.pdf;
      if (!st.pdf) { f.hidden = true; $('pdf-empty').hidden = false; return; }
      $('pdf-empty').hidden = true; f.hidden = false;
      f.src = 'api/tex/pdf?' + qs({ article: S.article, t: st.pdf }) + '#view=FitH';
    });
  }

  // --------------------------------------------------------- evidence ----
  // \ev{E1}, \figref{6}, \tabref{8}, \supref{ST10} in the text; evidence.tsv / assets.tsv in the
  // article folder. Clicking an evidence item (or Ctrl+click on a marker) opens what supports it.
  function assetUrl(path, page) {
    return 'tex-asset/' + encodeURIComponent(S.article) + '/' + path.split('/').map(encodeURIComponent).join('/') +
      (/\.pdf$/i.test(path) ? '#page=' + (page || 1) : '');
  }
  function viewUrl(kind, id) {
    return 'tex-view/' + encodeURIComponent(S.article) + '/' + kind + '/' + encodeURIComponent(id);
  }
  function loadEvidence() {
    return get('evidence').then(function (r) {
      var reg = (r.picked_up || []).filter(function (x) { return /^registered/.test(x.status); });
      if (reg.length && r.ts !== S.pickTs) toast('New paper PDF' + (reg.length > 1 ? 's' : '') + ' added: ' + reg.map(function (x) { return x.status.replace(/^registered as /, ''); }).join('; '), 8000);
      S.pickTs = r.ts; S.ev = r; renderEvidence();
    }).catch(function () {});
  }
  var MISS_KIND = {
    gap: { unsourced: ['No source', 'No local source found for this claim'], unsupported: ['Overreach', 'The claim goes beyond what the evidence shows'] },
    mismatch: ['Mismatch', 'The source says something else (see the note)'],
    nofile: ['File missing', 'Evidence registered, but its file (e.g. the paper PDF) is not in the folder yet'],
    unmarked: ['Not linked', 'A sentence with a number and no evidence marker']
  };
  function missLabel(m) {
    if (m.explained) return ['Missing · cause known', 'No evidence, for a known and accepted reason: ' + m.cause];
    var k = MISS_KIND[m.kind];
    return m.kind === 'gap' ? (k[m.status] || ['Gap', '']) : k;
  }
  function openCount(r) { return (r.missing || []).filter(function (m) { return !m.explained; }).length; }
  function renderMissing(r) {
    var ms = r.missing || [], open = openCount(r), shownHead = false;
    $('evidence').innerHTML = ms.length ? ms.map(function (m, i) {
      var lab = missLabel(m), where = m.file ? m.file.replace(/^sections\//, '') + (m.line ? ':' + m.line : '') : '';
      var head = '';
      if (m.explained && !shownHead) {  // explained gaps come last, under their own heading
        shownHead = true;
        head = '<div class="miss-sep">' + (open ? '' : 'No open problems. ') + 'Missing with a known cause</div>';
      }
      return head + '<div class="ev-item miss' + (m.explained ? ' explained' : '') + '" data-miss="' + i + '" title="' + esc(lab[1] + (m.note ? '\n\n' + m.note : '')) + '">' +
        '<span class="miss-kind ' + esc(m.explained ? 'explained' : m.kind + ' ' + (m.status || '')) + '">' + esc(lab[0]) + '</span>' +
        '<span class="ev-claim">' + esc(m.claim) + '</span>' +
        (m.explained ? '<span class="miss-cause">' + esc(m.cause) + '</span>' : '') +
        '<span class="miss-where">' + (m.id ? '<a href="#" data-ev="' + esc(m.id) + '">' + esc(m.id) + '</a> ' : '') + esc(where) + (m.stale ? ' (text changed)' : '') + '</span></div>';
    }).join('') : '<div class="empty-small">Nothing missing: every claim found has evidence.</div>';
  }
  function renderEvidence() {
    var r = S.ev, on = r && r.enabled && r.evidence;
    $('ev-h').hidden = $('evidence').hidden = !on;
    if (!on) return;
    var ids = Object.keys(r.evidence), probs = r.problems || [];
    $('ev-count').textContent = ids.length;
    var nOpen = openCount(r), nExpl = (r.missing || []).length - nOpen;
    $('ev-miss').textContent = nOpen + (nExpl ? ' +' + nExpl : '');
    $('ev-miss').title = nOpen + ' open, ' + nExpl + ' missing with a known cause';
    var tabs = document.querySelectorAll('.ev-tab');
    for (var t = 0; t < tabs.length; t++) tabs[t].classList.toggle('on', tabs[t].getAttribute('data-evtab') === (S.evTab || 'linked'));
    if (S.evTab === 'missing') { renderMissing(r); return; }
    $('evidence').innerHTML = ids.map(function (id) {
      var e = r.evidence[id], st = !e.path ? 'missing' : e.kind === 'cite' ? (e.highlighted ? 'hl' : 'nohl') : 'ok';
      var tip = st === 'missing' ? 'file missing' : st === 'nohl' ? 'linked, quote not highlighted' : st === 'hl' ? 'highlighted in the paper' : 'linked';
      return '<div class="ev-item" data-ev="' + esc(id) + '" title="' + esc(tip) + '"><span class="ev-id ' + st + '">' + esc(id) + '</span>' +
        '<span class="ev-claim">' + esc(e.claim || e.target) + '</span>' + (e.used && e.used.length ? '' : '<span class="ev-unused" title="not cited in the text yet">unused</span>') + '</div>';
    }).join('') + (probs.length ? '<div class="ev-probs">' + probs.map(function (p) { return '<div>' + esc(p) + '</div>'; }).join('') + '</div>' : '');
  }
  $('evidence').addEventListener('click', function (e) {
    var t = e.target.closest('[data-ev]');
    if (t) { e.preventDefault(); showEvidence(t.getAttribute('data-ev')); return; }
    var m = e.target.closest('[data-miss]'); if (!m || !S.ev) return;
    var it = S.ev.missing[+m.getAttribute('data-miss')];
    if (!it || !it.file) return;
    if (S.mode !== 'edit') showEditor();
    var go = function () {
      if (it.line) gotoLine(it.line);
      var at = it.anchor ? ed.value.indexOf(it.anchor) : -1;  // select just the claim, not the whole paragraph
      if (at >= 0) { ed.focus(); ed.setSelectionRange(at, at + it.anchor.length); }
    };
    if (it.file !== S.file) openFile(it.file).then(go); else go();
    var why = it.explained ? it.cause : it.note;
    toast(missLabel(it)[0] + (why ? ': ' + (why.length > 260 ? why.slice(0, 260) + '…' : why) : ''), 9000);
  });
  $('ev-h').addEventListener('click', function (e) {
    var t = e.target.closest('[data-evtab]'); if (!t) return;
    S.evTab = t.getAttribute('data-evtab');
    try { localStorage.setItem('sw-tex-evtab', S.evTab); } catch (x) {}
    renderEvidence();
  });
  try { S.evTab = localStorage.getItem('sw-tex-evtab') || 'linked'; } catch (x) { S.evTab = 'linked'; }
  function showDoc(url, title, extra, inPane) {
    if (!inPane && !/\.(pdf|png|jpe?g|svg|txt|tsv|csv|md|tex)(#|$)/i.test(url.split('?')[0])) { window.open(url, '_blank'); return; }
    if (!S.showPdf || S.panes.pdfFold) { S.panes.pdfFold = false; setPdfShown(true); }
    S.docShown = url;
    $('doc-bar').innerHTML = '<b>' + esc(title) + '</b>' + (extra || '') + '<span class="spacer"></span>' +
      '<a class="btn small" href="' + esc(url) + '" target="_blank" rel="noopener">New tab</a> <button class="btn small" data-back="1">Back to manuscript</button>';
    $('doc-bar').hidden = false; $('pdf-empty').hidden = true;
    $('pdf').hidden = false; $('pdf').src = url;
  }
  $('doc-bar').addEventListener('click', function (e) {
    if (!e.target.getAttribute('data-back')) return;
    S.docShown = null; $('doc-bar').hidden = true; reloadPdf();
  });
  function showEvidence(id) {
    var e = S.ev && S.ev.evidence && S.ev.evidence[id];
    if (!e) { toast(id + ' is not in evidence.tsv'); return; }
    if (!e.path) { toast(id + ': ' + (e.problem || 'nothing to open'), 6000); return; }
    var extra = ' · ' + esc(e.target) + (e.kind === 'cite' ? ', p. ' + esc(e.page) : '') +
      (e.claim ? '<div class="doc-claim">' + esc(e.claim) + '</div>' : '') +
      (e.quote ? '<div class="doc-quote">“' + esc(e.quote) + '”</div>' : '') +
      (e.used && e.used.length ? '<div class="doc-used">Used at ' + e.used.map(function (u) {
        return '<a href="#" data-goto="' + esc(u) + '">' + esc(u) + '</a>'; }).join(', ') + '</div>' : '');
    showDoc(/\.pdf$/i.test(e.path) ? assetUrl(e.path, e.page) : viewUrl('ev', id), id, extra, true);
  }
  $('doc-bar').addEventListener('click', function (e) {
    var g = e.target.getAttribute && e.target.getAttribute('data-goto'); if (!g) return;
    e.preventDefault();
    var i = g.lastIndexOf(':'), f = g.slice(0, i), n = parseInt(g.slice(i + 1), 10);
    if (S.mode !== 'edit') showEditor();
    if (f !== S.file) openFile(f).then(function () { gotoLine(n); }); else gotoLine(n);
  });
  function showAsset(key, panel) {
    var as = (S.ev && S.ev.assets) || [], hit = null;
    for (var i = 0; i < as.length; i++) if (as[i].key === key + panel) hit = as[i];
    for (i = 0; !hit && i < as.length; i++) if (as[i].key === key) hit = as[i];
    if (!hit) { toast(key + panel + ' is not in assets.tsv'); return; }
    showDoc(/\.pdf$/i.test(hit.file) ? assetUrl(hit.file, 1) : viewUrl('asset', hit.key), hit.label + (hit.key === key ? panel : ''), ' · ' + esc(hit.file), true);
  }
  ed.addEventListener('click', function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    var pos = ed.selectionStart, v = ed.value, a = v.lastIndexOf('\n', pos - 1) + 1, b = v.indexOf('\n', pos);
    var line = v.slice(a, b < 0 ? v.length : b), col = pos - a, re = /\\(ev|figref|tabref|supref)(?:\[([^\]]*)\])?\{([^}]*)\}/g, m;
    while ((m = re.exec(line)) !== null) {
      if (col < m.index || col > re.lastIndex) continue;
      if (m[1] === 'ev') {
        var ids = m[3].split(','), off = m.index + m[0].indexOf('{') + 1, pick = ids[0];
        for (var k = 0; k < ids.length; k++) { if (col <= off + ids[k].length) { pick = ids[k]; break; } off += ids[k].length + 1; }
        showEvidence(pick.trim());
      } else showAsset((m[1] === 'figref' ? 'F' : m[1] === 'tabref' ? 'T' : '') + m[3].trim(), m[2] || '');
      return;
    }
  });
  $('btn-submission').addEventListener('click', function () {
    if (S.compiling) return;
    S.compiling = true; status('Building the submission PDF…');
    var go = S.dirty ? save() : Promise.resolve();
    go.then(function () { return post('compile', { submission: true }); }).then(function (c) {
      status(c.ok ? 'Submission PDF built' : 'Submission build failed');
      if (c.ok) showDoc('api/tex/submission-pdf?' + qs({ article: S.article, t: Date.now() }) + '#view=FitH', 'Submission PDF', ' · no evidence markers · build/main_submission.pdf', true);
      else toast('Submission build failed: ' + ((c.errors[0] || {}).message || 'see log'), 8000);
    }).catch(function (e) { toast(e.message, 6000); status(''); })
      .then(function () { S.compiling = false; });
  });

  // ---------------------------------------------------------- history ----
  $('btn-history').addEventListener('click', function () {
    if (!S.file) return;
    get('snapshots', { path: S.file }).then(function (snaps) {
      var bg = document.createElement('div'); bg.className = 'modal-bg';
      bg.innerHTML = '<div class="modal"><h3>Earlier versions of ' + esc(S.file) + '</h3>' +
        '<div style="font-size:12.5px;color:var(--text-2);margin-bottom:8px">Saved automatically before every save and every accepted change. "Load" puts it in the editor; nothing is written until you save.</div>' +
        (snaps.length ? snaps.map(function (s) {
          var t = s.id.replace(/^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d).*/, '$1-$2-$3 $4:$5:$6');
          return '<div class="snap"><span>' + esc(t) + '</span><span class="spacer"></span><span style="color:var(--text-3)">' + Math.round(s.size / 1024 * 10) / 10 + ' KB</span>' +
            '<button class="btn small" data-snap="' + esc(s.id) + '">Load</button></div>';
        }).join('') : '<div class="empty-small">No earlier versions yet.</div>') +
        '<div style="text-align:right;margin-top:10px"><button class="btn" data-close="1">Close</button></div></div>';
      document.body.appendChild(bg);
      bg.addEventListener('click', function (e) {
        if (e.target === bg || e.target.getAttribute('data-close')) { bg.remove(); return; }
        var id = e.target.getAttribute('data-snap'); if (!id) return;
        get('snapshot', { path: S.file, id: id }).then(function (r) {
          if (S.mode !== 'edit') showEditor();
          ed.value = r.text; setDirty(true); renderHL(); bg.remove();
          toast('Loaded the version of ' + id.slice(0, 15) + '. Save to keep it.');
        });
      });
    }).catch(function (e) { toast(e.message); });
  });

  // ------------------------------------------------------------ notes ----
  // Claims, structure, suggestions and ideas: Markdown files in <article>/notes/, each in its own tab.
  // Not part of the manuscript or the PDF; saved directly (no proposals), with snapshots on every save.
  function mdInline(s) {
    var parts = String(s).split(/(`[^`]*`)/), out = '';
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (/^`[^`]*`$/.test(p)) { out += '<code>' + esc(p.slice(1, -1)) + '</code>'; continue; }
      p = esc(p)
        .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, t, u) {
          return /^(https?:|#|\.{0,2}\/|[\w.-]+\.\w+)/.test(u) && !/^javascript:/i.test(u)
            ? '<a href="' + u + '" target="_blank" rel="noopener">' + t + '</a>' : m;
        })
        .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
        .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, '$1<i>$2</i>')
        .replace(/~~([^~]+)~~/g, '<s>$1</s>');
      out += p;
    }
    return out;
  }
  var LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+/;
  function indentOf(l) { return l.match(/^\s*/)[0].replace(/\t/g, '  ').length; }
  function isBlockStart(l) { return /^\s*(#{1,6}\s|>|```|\|)/.test(l) || LIST_RE.test(l) || /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(l); }
  function md(text, tight) {
    var L = String(text).replace(/\r/g, '').split('\n'), n = L.length, i = 0, out = [], m;
    while (i < n) {
      var l = L[i];
      if (!l.trim()) { i++; continue; }
      if (/^\s*```/.test(l)) {
        var code = []; i++;
        while (i < n && !/^\s*```/.test(L[i])) code.push(L[i++]);
        i++; out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>'); continue;
      }
      if ((m = /^\s*(#{1,6})\s+(.*?)\s*#*\s*$/.exec(l))) { out.push('<h' + m[1].length + '>' + mdInline(m[2]) + '</h' + m[1].length + '>'); i++; continue; }
      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(l)) { out.push('<hr>'); i++; continue; }
      if (/^\s*\|/.test(l) && i + 1 < n && /^\s*\|?\s*:?-{2,}/.test(L[i + 1])) {
        var cells = function (r) { return r.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(function (c) { return c.trim(); }); };
        var head = cells(l), rows = []; i += 2;
        while (i < n && /^\s*\|/.test(L[i])) rows.push(cells(L[i++]));
        out.push('<table><thead><tr>' + head.map(function (c) { return '<th>' + mdInline(c) + '</th>'; }).join('') + '</tr></thead><tbody>' +
          rows.map(function (r) { return '<tr>' + r.map(function (c) { return '<td>' + mdInline(c) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table>');
        continue;
      }
      if (/^\s*>/.test(l)) {
        var q = [];
        while (i < n && /^\s*>/.test(L[i])) q.push(L[i++].replace(/^\s*>\s?/, ''));
        out.push('<blockquote>' + md(q.join('\n')) + '</blockquote>'); continue;
      }
      if ((m = LIST_RE.exec(l))) {
        var base = indentOf(l), ordered = /\d/.test(m[2]), items = [];
        while (i < n) {
          var mm = LIST_RE.exec(L[i]);
          if (!mm || indentOf(L[i]) !== base || /\d/.test(mm[2]) !== ordered) break;
          var cind = mm[0].length, body = [L[i].slice(mm[0].length)]; i++;
          while (i < n) {
            var x = L[i];
            if (!x.trim()) {
              var j = i; while (j < n && !L[j].trim()) j++;
              if (j < n && indentOf(L[j]) > base) { body.push(''); i++; continue; }
              break;
            }
            if (indentOf(x) > base) { body.push(x.slice(Math.min(indentOf(x), cind))); i++; continue; }
            if (!isBlockStart(x) && body[body.length - 1].trim()) { body.push(x.trim()); i++; continue; }
            break;
          }
          items.push('<li>' + md(body.join('\n'), true) + '</li>');
          var k = i; while (k < n && !L[k].trim()) k++;
          var nx = k < n ? LIST_RE.exec(L[k]) : null;
          if (k > i && nx && indentOf(L[k]) === base && /\d/.test(nx[2]) === ordered) i = k;
        }
        out.push(ordered ? '<ol start="' + (parseInt(m[2], 10) || 1) + '">' + items.join('') + '</ol>' : '<ul>' + items.join('') + '</ul>');
        continue;
      }
      var para = [];
      while (i < n && L[i].trim() && (!para.length || !isBlockStart(L[i]))) para.push(L[i++].trim());
      out.push('<p>' + mdInline(para.join(' ')) + '</p>');
    }
    var html = out.join('\n');
    if (tight && out.length === 1 && /^<p>/.test(html)) html = html.slice(3, -4);
    else if (tight) html = html.replace(/^<p>([\s\S]*?)<\/p>/, '$1');
    return html;
  }

  // ------------------------------------------------------------ revisions ----
  // The revision register (revisions/revisions.tsv, written by the revision tools): who revised the article, when,
  // what each change did, what was decided, and how much of each part of the article it touched. Read-only here.
  S.rev = { data: null, filter: 'all', who: 'all', open: {}, jump: null };
  try { S.rev.jump = new URLSearchParams(location.search).get('r'); if (S.rev.jump) S.rev.open[S.rev.jump] = true; } catch (e) {}
  function revFile(path, label) { return path ? '<a class="rv-file" href="tex-asset/' + encodeURIComponent(S.article) + '/' + path.split('/').map(encodeURIComponent).join('/') + '?raw=1" title="' + esc(path) + '" download>' + esc(label) + '</a>' : ''; }
  var REV_PART = { 'main.tex': 'Title', 'sections/abstract.tex': 'Summary', 'sections/introduction.tex': 'Introduction',
    'sections/methods.tex': 'Methods', 'sections/results.tex': 'Results', 'sections/discussion.tex': 'Discussion',
    'sections/figures_tables.tex': 'Figures & tables', 'sections/supplemental.tex': 'Supplement' };
  var REV_ORDER = ['Title', 'Summary', 'Introduction', 'Methods', 'Results', 'Discussion', 'Figures & tables', 'Supplement', 'Other'];
  var REV_DEC = [['accepted', 'Accepted'], ['accepted with edits', 'Accepted with edits'], ['partly applied', 'Partly applied'],
    ['needs author', 'Needs author'], ['not applied', 'Not applied']];
  function revPart(r) { return REV_PART[r.file] || 'Other'; }
  function revDecClass(d) { return 'rv-' + String(d || 'pending').replace(/[^a-z]+/g, '-'); }
  function loadRevisions() {
    $('rev-view').innerHTML = '<div class="rv"><p class="muted">Loading the revision register…</p></div>';
    return get('revisions', {}).then(function (r) { S.rev.data = r; renderRevisions(); },
      function (e) { $('rev-view').innerHTML = '<div class="rv"><p class="muted">Could not read the register: ' + esc(e.message) + '</p></div>'; });
  }
  function revDiff(diff) {
    return (diff || []).map(function (d) {
      return d[0] === '=' ? esc(d[1]) : d[0] === '-' ? '<del>' + esc(d[1]) + '</del>' : '<ins>' + esc(d[1]) + '</ins>';
    }).join(' ');
  }
  function renderRevisions() {
    var D = S.rev.data;
    if (!D || !D.exists || !D.rows.length) { $('rev-view').innerHTML = '<div class="rv"><p class="muted">No revisions recorded yet (revisions/revisions.tsv).</p></div>'; return; }
    var rows = D.rows, who = {}, rounds = {};
    rows.forEach(function (r) {
      var w = who[r.reviewer] = who[r.reviewer] || { n: 0, words: 0, rounds: {}, dec: {} };
      w.n++; w.words += r.words_changed || 0; w.rounds[r.round] = r.received; w.dec[r.decision || 'pending'] = (w.dec[r.decision || 'pending'] || 0) + 1;
      rounds[r.round] = rounds[r.round] || { reviewer: r.reviewer, received: r.received, n: 0, src: r.source_file, sent: r.sent_file }; rounds[r.round].n++;
      w.files = w.files || {}; if (r.source_file) w.files[r.round] = r.source_file;
    });
    var sel = rows.filter(function (r) { return S.rev.who === 'all' || r.reviewer === S.rev.who; });
    // reviewer cards
    var cards = Object.keys(who).map(function (name) {
      var w = who[name];
      return '<button class="rv-card' + (S.rev.who === name ? ' on' : '') + '" data-rvwho="' + esc(name) + '"><div class="rv-name">' + esc(name) + '</div>' +
        '<div class="rv-sub">' + Object.keys(w.rounds).sort().map(function (k) { return esc(k) + ' · ' + esc(w.rounds[k]) + (w.files && w.files[k] ? ' · ' + revFile(w.files[k], 'edited file') : ''); }).join('<br>') + '</div>' +
        '<div class="rv-big">' + w.n + ' <span>changes</span> · ' + w.words.toLocaleString() + ' <span>words</span></div>' +
        '<div class="rv-chips">' + REV_DEC.filter(function (d) { return w.dec[d[0]]; }).map(function (d) {
          return '<span class="rv-chip ' + revDecClass(d[0]) + '">' + w.dec[d[0]] + ' ' + d[1].toLowerCase() + '</span>'; }).join('') + '</div></button>';
    }).join('');
    // impact by part
    var part = {}, maxw = 1;
    sel.forEach(function (r) {
      var p = part[revPart(r)] = part[revPart(r)] || { words: 0, n: 0, dec: {} };
      p.words += r.words_changed || 0; p.n++; p.dec[r.decision || 'pending'] = (p.dec[r.decision || 'pending'] || 0) + (r.words_changed || 0);
    });
    REV_ORDER.forEach(function (k) { if (part[k]) maxw = Math.max(maxw, part[k].words); });
    var bars = REV_ORDER.filter(function (k) { return part[k]; }).map(function (k) {
      var p = part[k];
      var segs = REV_DEC.concat([['pending', 'Pending']]).filter(function (d) { return p.dec[d[0]]; }).map(function (d) {
        return '<span class="rv-seg ' + revDecClass(d[0]) + '" style="width:' + (100 * p.dec[d[0]] / maxw).toFixed(2) + '%" title="' + esc(d[1] + ': ' + p.dec[d[0]] + ' words') + '"></span>'; }).join('');
      return '<div class="rv-bar"><span class="rv-blab">' + esc(k) + '</span><span class="rv-track">' + segs + '</span><span class="rv-bnum">' + p.n + ' · ' + p.words.toLocaleString() + ' w</span></div>';
    }).join('');
    var legend = REV_DEC.map(function (d) { return '<span><i class="rv-sw ' + revDecClass(d[0]) + '"></i>' + d[1] + '</span>'; }).join('');
    // timeline of rounds
    var tl = Object.keys(rounds).sort(function (a, b) { return String(rounds[a].received).localeCompare(rounds[b].received); }).map(function (k) {
      var x = rounds[k]; return '<span class="rv-tl"><b>' + esc(k) + '</b> ' + esc(x.received) + ' · ' + esc(x.reviewer) + ' · ' + x.n + ' changes' + (x.src ? ' · ' + revFile(x.src, 'returned file') : '') + (x.sent ? ' · ' + revFile(x.sent, 'copy we sent') : '') + '</span>'; }).join('');
    // list
    var F = { all: function () { return true; }, open: function (r) { return r.status === 'open question' || r.decision === 'partly applied' || r.decision === 'needs author'; } };
    REV_DEC.forEach(function (d) { F[d[0]] = function (r) { return r.decision === d[0]; }; });
    var segs = [['all', 'All']].concat(REV_DEC).concat([['open', 'Open questions']]).map(function (m) {
      var c = sel.filter(F[m[0]]).length; if (!c && m[0] !== 'all') return '';
      return '<button class="' + (S.rev.filter === m[0] ? 'on' : '') + '" data-rvf="' + esc(m[0]) + '">' + esc(m[1]) + ' <span class="muted">' + c + '</span></button>'; }).join('');
    var list = sel.filter(F[S.rev.filter] || F.all).map(function (r) {
      var o = S.rev.open[r.r_id];
      return '<div class="rv-item' + (o ? ' open' : '') + '" id="rv-' + esc(r.r_id) + '"><div class="rv-head" data-rvopen="' + esc(r.r_id) + '">' +
        '<b>' + esc(r.r_id) + '</b><span class="rv-by" title="Suggested by">' + esc(r.reviewer) + '</span><span class="rv-sec">' + esc(revPart(r) === 'Other' ? r.section : (revPart(r) === 'Title' || revPart(r) === 'Summary' || r.section === revPart(r)) ? revPart(r) : revPart(r) + ' · ' + r.section) + '</span>' +
        '<span class="rv-chip ' + revDecClass(r.decision) + '">' + esc(r.decision || 'pending') + '</span>' +
        '<span class="rv-st">' + esc(r.status || '') + (r.proposal ? ' · ' + esc(r.proposal) : '') + '</span>' +
        '<span class="rv-w">' + (r.words_changed || 0) + ' w</span></div>' +
        (o ? '<div class="rv-body">' +
          '<div class="rv-meta"><b>Suggested by ' + esc(r.reviewer) + '</b> (' + esc(r.round) + ', received ' + esc(r.received) + ') · ' + esc(r.kind) + ' · ' + revFile(r.source_file, 'edited file they returned') + (r.sent_file ? ' · ' + revFile(r.sent_file, 'copy we sent') : '') + ' · decided by ' + esc(r.decided_by || '') + ', ' + esc(r.decided_on || '') + '</div>' +
          (r.reason ? '<div class="rv-why"><b>Why:</b> ' + esc(r.reason) + '</div>' : '') +
          '<div class="rv-lab">What the reviewer changed (removed, added)</div><div class="rv-diff">' + (revDiff(r.diff) || '<span class="muted">(no text)</span>') + '</div>' +
          (r.final_text ? '<div class="rv-lab">In the manuscript now (as proposed)</div><div class="rv-final">' + esc(r.final_text) + '</div>' : '') +
          '</div>' : '') + '</div>';
    }).join('');
    $('rev-view').innerHTML = '<div class="rv"><h2>Revisions</h2><p class="muted">Every change sent by co-authors and reviewers, with its decision. ' +
      'Register: <code>' + esc(D.path) + '</code>. Click a reviewer to focus on their changes; click a change to see it.</p>' +
      '<div class="rv-cards">' + cards + (S.rev.who !== 'all' ? '<button class="rv-card rv-allc" data-rvwho="all">Show all reviewers</button>' : '') + '</div>' +
      '<div class="rv-timeline">' + tl + '</div>' +
      '<h3>Impact on the article' + (S.rev.who !== 'all' ? ' · ' + esc(S.rev.who) : '') + '</h3><div class="rv-bars">' + bars + '</div><div class="rv-legend">' + legend + '</div>' +
      '<h3>Changes</h3><div class="seg rv-seg-f">' + segs + '</div><div class="rv-list">' + list + '</div></div>';
    if (S.rev.jump) { var j = document.getElementById('rv-' + S.rev.jump); S.rev.jump = null; if (j) j.scrollIntoView({ block: 'center' }); }
  }
  $('rev-view').addEventListener('click', function (e) {
    var t;
    if ((t = e.target.closest('[data-rvwho]'))) { S.rev.who = t.getAttribute('data-rvwho') === S.rev.who ? 'all' : t.getAttribute('data-rvwho'); renderRevisions(); return; }
    if ((t = e.target.closest('[data-rvf]'))) { S.rev.filter = t.getAttribute('data-rvf'); renderRevisions(); return; }
    if ((t = e.target.closest('[data-rvopen]'))) { var k = t.getAttribute('data-rvopen'); S.rev.open[k] = !S.rev.open[k]; renderRevisions(); }
  });

  S.tab = 'tex';
  S.note = { key: null, base: null, dirty: false };
  try { S.nmode = localStorage.getItem('sw-tex-nmode') || 'view'; } catch (e) { S.nmode = 'view'; }
  var noteEd = $('note-ed'), noteMd = $('note-md');
  function renderTabs() {
    var notes = (S.summary && S.summary.notes) || [];
    $('doc-tabs').innerHTML = '<button class="doc-tab' + (S.tab === 'tex' ? ' on' : '') + '" data-tab="tex" title="The manuscript (LaTeX)">Manuscript</button>' +
      notes.map(function (nt) {
        return '<button class="doc-tab' + (S.tab === nt.key ? ' on' : '') + (nt.exists ? '' : ' missing') + '" data-tab="' + esc(nt.key) + '" title="' +
          esc(nt.path + (nt.exists ? '' : ' (not created yet: type and save to create it)')) + '">' + esc(nt.label) +
          (S.tab === nt.key && S.note.dirty ? '<span class="nt-dot"></span>' : '') + '</button>';
      }).join('') +
      (S.summary && S.summary.revisions ? '<button class="doc-tab' + (S.tab === 'revisions' ? ' on' : '') +
        '" data-tab="revisions" title="Revision history: every change sent by co-authors and reviewers (revisions/revisions.tsv)">Revisions</button>' : '');
  }
  function setNoteMode(mode) {
    S.nmode = mode; $('note-body').className = 'note-body ' + mode;
    var b = document.querySelectorAll('[data-nmode]');
    for (var i = 0; i < b.length; i++) b[i].classList.toggle('on', b[i].getAttribute('data-nmode') === mode);
    try { localStorage.setItem('sw-tex-nmode', mode); } catch (e) {}
    if (mode !== 'view') noteEd.focus();
  }
  function renderNoteMd() {
    noteMd.innerHTML = noteEd.value.trim() ? md(noteEd.value) : '<p class="md-empty">Empty. Switch to Edit to write here.</p>';
  }
  function setNoteDirty(d) { S.note.dirty = d; $('note-dirty').hidden = !d; renderTabs(); }
  function loadNote(key, keepView) {
    return get('note', { key: key }).then(function (r) {
      var top = noteEd.scrollTop, mtop = noteMd.scrollTop;
      S.note.key = key; S.note.base = r.hash; noteEd.value = r.text; setNoteDirty(false);
      $('note-path').textContent = r.path + (r.exists ? '' : ' (new)');
      renderNoteMd();
      if (keepView) { noteEd.scrollTop = top; noteMd.scrollTop = mtop; } else { noteEd.scrollTop = 0; noteMd.scrollTop = 0; }
    });
  }
  function saveNote(opts) {
    opts = opts || {};
    if (!S.note.key || !S.note.dirty) return Promise.resolve();
    S.saving = true; status('Saving…');
    return post('note-save', { key: S.note.key, text: noteEd.value, base_hash: opts.force ? null : S.note.base }).then(function (r) {
      S.note.base = r.hash; S.saving = false; setNoteDirty(false); status('Saved ' + new Date().toLocaleTimeString());
      hideBanner('conflict'); return refresh();
    }, function (e) {
      S.saving = false; status('');
      if (/changed on disk/.test(e.message)) showNoteConflict(); else toast(e.message);
      throw e;
    });
  }
  function showNoteConflict() {
    banners.conflict = { cls: 'warn', html: '<b>This note changed on disk</b> while you were editing (Claude or another window). ' +
      '<button class="btn small" data-act="note-reload">Load the disk version (discard my edits)</button> ' +
      '<button class="btn small" data-act="note-overwrite">Keep my version (overwrite)</button>' };
    renderBanner(); $('banner').hidden = false;
  }
  $('banner').addEventListener('click', function (e) {
    var a = e.target.getAttribute && e.target.getAttribute('data-act');
    if (a === 'note-reload') { hideBanner('conflict'); loadNote(S.note.key, true); }
    if (a === 'note-overwrite') saveNote({ force: true }).then(function () { toast('Saved your version'); });
  });
  function showTab(tab) {
    var go = function () {
      S.tab = tab;
      try { localStorage.setItem('sw-tex-tab-' + S.article, tab); } catch (e) {}
      var isTex = tab === 'tex', isRev = tab === 'revisions';
      $('note-view').hidden = isTex || isRev;
      $('rev-view').hidden = !isRev;
      $('editor-wrap').hidden = !isTex || S.mode === 'review';
      $('review').hidden = !isTex || S.mode !== 'review';
      $('cur-file').textContent = isTex ? (S.file || '') : '';
      renderTabs();
      if (isTex) { renderBanner(); queueHL(); return; }
      $('banner').hidden = true;
      if (isRev) return loadRevisions();
      setNoteMode(S.nmode);
      return loadNote(tab);
    };
    var pending = (S.tab === 'tex' ? (S.dirty ? save() : null) : (S.tab !== 'revisions' && S.note.dirty ? saveNote() : null));
    return pending ? pending.then(go, function () {}) : Promise.resolve(go());
  }
  $('doc-tabs').addEventListener('click', function (e) {
    var t = e.target.closest('[data-tab]'); if (t && t.getAttribute('data-tab') !== S.tab) showTab(t.getAttribute('data-tab'));
  });
  $('note-view').addEventListener('click', function (e) {
    var t = e.target.closest('[data-nmode]'); if (t) setNoteMode(t.getAttribute('data-nmode'));
  });
  noteMd.addEventListener('dblclick', function () { if (S.nmode === 'view') setNoteMode('split'); });
  var mdQueued = null;
  noteEd.addEventListener('input', function () {
    if (!S.note.dirty) setNoteDirty(true);
    clearTimeout(mdQueued); mdQueued = setTimeout(renderNoteMd, 150);
  });
  noteEd.addEventListener('keydown', function (e) {
    if (e.key === 'Tab' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      var s = noteEd.selectionStart, t = noteEd.selectionEnd;
      noteEd.value = noteEd.value.slice(0, s) + '  ' + noteEd.value.slice(t);
      noteEd.selectionStart = noteEd.selectionEnd = s + 2; setNoteDirty(true);
    }
  });
  function saveCurrent() { return S.tab === 'tex' ? save() : saveNote(); }

  // ------------------------------------------------------ live refresh ----
  function refresh() {
    return get('summary').then(function (s) {
      S.summary = s;
      S.propSig = s.proposals.map(function (p) { return p.id + ':' + p.hunks.filter(function (h) { return h.state === 'open'; }).length; }).join(',');
      $('side-foot').innerHTML = esc(s.root) + '<br>engine: ' + esc(s.engine || 'none found');
      renderFiles(); renderProps(); renderBanner(); renderTabs();
      if (s.compile && s.compile.ts !== S.compileTs) { S.compileTs = s.compile.ts; renderCompile(s.compile); }
      else if (!s.compile) renderCompile(null);
      if (S.mode === 'review' && S.reviewing) { var pr = findProp(S.reviewing); if (pr) renderReview(pr); else showEditor(); }
      queueHL();
    });
  }
  function poll() {
    if (document.hidden || !S.article) return;
    get('stamp').then(function (st) {
      var sig = st.proposals.join(',');
      if (sig !== S.propSig) {
        var before = S.propSig;
        refresh().then(function () { if (before !== null && sig.length > before.length) toast('New changes to review'); });
      }
      if (S.tab !== 'tex' && S.note.key && st.notes && st.notes[S.note.key] !== S.note.base && !S.saving) {
        if (!S.note.dirty) loadNote(S.note.key, true).then(function () { status('Reloaded (changed on disk)'); refresh(); });
        else showNoteConflict();
      }
      if (S.tab === 'tex' && S.file && st.files[S.file] && st.files[S.file] !== S.base && !S.saving) {
        if (!S.dirty) openFile(S.file, true).then(function () { status('Reloaded (changed on disk)'); });
        else showConflict();
      }
      if (st.pdf !== S.pdfStamp && !S.compiling) reloadPdf();
      $('stale-pdf').hidden = !st.stale || S.compiling;
      if (st.compile && st.compile !== S.compileTs) get('log').then(function (c) { S.compileTs = c.ts; renderCompile(c); loadEvidence(); });
    }).catch(function () {});
  }

  // ----------------------------------------------------------- panels ----
  // Changes to review and Evidence fold (body hidden, header kept) or hide; the PDF folds to a strip on
  // the right or hides (the PDF button). Files and the editor always stay. Remembered per browser.
  var PANE_LABEL = { props: 'Changes to review', ev: 'Evidence', pdf: 'PDF' };
  S.panes = { props: { fold: false, bin: false }, ev: { fold: false, bin: false }, pdfFold: false };
  try { var sp = JSON.parse(localStorage.getItem('sw-tex-panes') || 'null'); if (sp && sp.props && sp.ev) S.panes = sp; } catch (e) {}
  function applyPanes() {
    var side = document.querySelector('.tex-side'), P = S.panes;
    ['props', 'ev'].forEach(function (k) {
      side.classList.toggle('fold-' + k, !!P[k].fold);
      side.classList.toggle('bin-' + k, !!P[k].bin);
    });
    $('tex-main').classList.toggle('pdf-folded', S.showPdf && !!P.pdfFold);
    var hidden = ['props', 'ev'].filter(function (k) { return P[k].bin; });
    if (!S.showPdf) hidden.push('pdf');
    $('btn-panes').hidden = !hidden.length;
    $('btn-panes').textContent = 'Panels (' + hidden.length + ' hidden)';
    $('panes-menu').innerHTML = hidden.map(function (k) { return '<button data-unbin="' + k + '">Show ' + PANE_LABEL[k] + '</button>'; }).join('');
    if (!hidden.length) $('panes-menu').hidden = true;
    try { localStorage.setItem('sw-tex-panes', JSON.stringify(P)); } catch (e) {}
    queueHL();
  }
  function setPdfShown(on) {
    S.showPdf = on; $('tex-main').classList.toggle('no-pdf', !on); $('btn-pdf').classList.toggle('on', on);
    try { localStorage.setItem('sw-tex-pdf', on ? '1' : '0'); } catch (e) {}
    applyPanes();
  }
  document.addEventListener('click', function (e) {
    var t = e.target.closest && e.target.closest('[data-fold],[data-bin],[data-unbin]');
    if (!t) { if (!e.target.closest || !e.target.closest('.panes-menu-wrap')) $('panes-menu').hidden = true; return; }
    var k = t.getAttribute('data-fold'), b = t.getAttribute('data-bin'), u = t.getAttribute('data-unbin');
    if (k === 'pdf') S.panes.pdfFold = !S.panes.pdfFold;
    else if (k) S.panes[k].fold = !S.panes[k].fold;
    if (b === 'pdf') return setPdfShown(false);
    if (b) S.panes[b].bin = true;
    if (u === 'pdf') { S.panes.pdfFold = false; return setPdfShown(true); }
    if (u) { S.panes[u].bin = false; S.panes[u].fold = false; }
    applyPanes();
  });
  $('btn-panes').addEventListener('click', function () { $('panes-menu').hidden = !$('panes-menu').hidden; });

  // ------------------------------------------------------------ chrome ----
  $('btn-save').addEventListener('click', function () { saveCurrent().catch(function () {}); });
  $('btn-compile').addEventListener('click', compile);
  $('stale-pdf').addEventListener('click', function () { $('stale-pdf').hidden = true; compile(); });
  $('btn-pdf').addEventListener('click', function () {
    if (S.showPdf && S.panes.pdfFold) { S.panes.pdfFold = false; applyPanes(); return; }  // folded: unfold
    setPdfShown(!S.showPdf);
  });
  $('theme-toggle').addEventListener('click', function () {
    var cur = document.documentElement.getAttribute('data-theme') ||
      (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    var next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('sciweave-theme', next); } catch (e) {}
  });
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) { e.preventDefault(); saveCurrent().catch(function () {}); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); compile(); }
  });
  window.addEventListener('beforeunload', function (e) { if (S.dirty || S.note.dirty) { e.preventDefault(); e.returnValue = ''; } });
  window.addEventListener('resize', queueHL);
  $('article-select').addEventListener('change', function () {
    var v = this.value; if (v && v !== S.article) location.search = '?article=' + encodeURIComponent(v);
  });

  function start() {
    try { if (localStorage.getItem('sw-tex-pdf') === '0') { S.showPdf = false; $('tex-main').classList.add('no-pdf'); } } catch (e) {}
    $('btn-pdf').classList.toggle('on', S.showPdf);
    applyPanes();
    fetch('api/tex/articles', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (arts) {
      if (!arts.length) { $('files').innerHTML = '<div class="empty-small">No article in this project. Attach one: <code>sciweave tex attach &lt;folder&gt; "Title"</code></div>'; return; }
      if (!S.article) S.article = arts[0].id;
      $('article-select').innerHTML = arts.map(function (a) {
        return '<option value="' + esc(a.id) + '"' + (a.id === S.article ? ' selected' : '') + '>' + esc(a.id + ' · ' + a.label) + '</option>';
      }).join('');
      return refresh().then(function () {
        var want = null;
        try { want = localStorage.getItem('sw-tex-file-' + S.article); } catch (e) {}
        var files = S.summary.files.map(function (f) { return f.path; });
        var first = files.indexOf(want) >= 0 ? want : (files.indexOf(S.summary.main) >= 0 ? S.summary.main : files[0]);
        if (first) openFile(first);
        var tab = null;
        try { tab = new URLSearchParams(location.search).get('tab') || localStorage.getItem('sw-tex-tab-' + S.article); } catch (e) {}
        if (tab && tab !== 'tex' && ((S.summary.notes || []).some(function (x) { return x.key === tab; }) || (tab === 'revisions' && S.summary.revisions))) showTab(tab);
        else renderTabs();
        reloadPdf(); loadEvidence();
        setInterval(poll, 2500);
      });
    }).catch(function (e) { toast(e.message, 8000); });
  }
  start();
})();
