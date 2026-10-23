/**
 * static/js/pending_list.js
 *
 * "Waiting for MIGO 103" panel (bottom of the MIGO 103 tab, under the Post button).
 *
 * Lists every record whose Gate In is done and MIGO 103 is not, oldest first,
 * refreshed in place every ~10s (no page reload) while the MIGO 103 tab is
 * visible. Each row is a plain link to /view/<id>, with small status chips:
 *   Posting...   a MIGO 103 job for it is queued/running right now
 *   <name> is on it   someone else has that invoice's MIGO 103 tab open
 *                     (informational only -- nothing is ever blocked)
 *   Draft        you have an unposted draft saved for it
 *   Ready        none of the above
 *
 * Also:
 *   Search box   filters the list by invoice number, vendor or record ID.
 *                Enter opens the match when exactly one row is left.
 *   Visited      every record page you open is remembered in THIS browser
 *                (localStorage, per login name, newest first, max 30). The one
 *                you were on just before this one is tagged "Last visited",
 *                the others show "opened 25m ago", and the header has a
 *                "Back to <invoice>" button to jump to the previous one.
 *   Count        a small "N waiting" chip next to the Post button scrolls
 *                down to this panel.
 *
 * Read-only UI: it only calls GET /api/pending/migo_103. Rows are built with
 * the DOM API (textContent), never innerHTML, so vendor/invoice text can't
 * inject markup.
 */
(function () {
  'use strict';

  var panel = document.getElementById('migoPendingPanel');
  if (!panel) return;

  var listEl   = document.getElementById('mplList');
  var countEl  = document.getElementById('mplCount');
  var bodyEl   = document.getElementById('mplBody');
  var toggleEl = document.getElementById('mplToggle');
  var searchEl = document.getElementById('mplSearch');
  var backEl   = document.getElementById('mplBack');
  var postCnt  = document.getElementById('mplPostCount');
  var tabEl    = document.getElementById('migo103Tab');
  var idEl     = document.getElementById('history_id_migo');
  var curId    = idEl ? (parseInt(idEl.value, 10) || 0) : 0;
  var ctx      = window.MI_CTX || {};

  var POLL_MS  = 10000;
  var CHECK_MS = 3000;
  var lastFetch = 0;
  var loading   = false;
  var HIDE_KEY  = 'mi_mpl_hidden';
  var items     = [];      // last list from the server
  var loaded    = false;   // true once the first fetch has answered
  var MAX_VISITS = 30;

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  // ── visited history (this browser only) ───────────────────────────────────
  // [{id, inv, t}] newest first. Keyed by login name so two people sharing a
  // PC don't see each other's trail.
  var VISIT_KEY = 'mi_mpl_visits:' + String(ctx.username || '');

  function readVisits() {
    var a;
    try { a = JSON.parse(lsGet(VISIT_KEY) || '[]'); } catch (e) { a = []; }
    if (!Array.isArray(a)) return [];
    return a.filter(function (v) { return v && v.id && v.t; });
  }
  function writeVisits(a) { lsSet(VISIT_KEY, JSON.stringify(a.slice(0, MAX_VISITS))); }

  function recordVisit() {
    if (!curId) return;
    var a = readVisits();
    var inv = ctx.invoiceNumber ? String(ctx.invoiceNumber) : '';
    if (a.length && a[0].id === curId) {            // a plain refresh -- keep the original time
      if (inv && a[0].inv !== inv) { a[0].inv = inv; writeVisits(a); }
      return;
    }
    var old = a.filter(function (v) { return v.id === curId; })[0];
    a = a.filter(function (v) { return v.id !== curId; });
    a.unshift({ id: curId, inv: inv || (old && old.inv) || '', t: Date.now() });
    writeVisits(a);
  }

  function visitMap() {
    var m = {};
    readVisits().forEach(function (v) { m[v.id] = v; });
    return m;
  }
  // the record opened just before this one
  function previousVisit() {
    var a = readVisits();
    for (var i = 0; i < a.length; i++) { if (a[i].id !== curId) return a[i]; }
    return null;
  }
  recordVisit();

  // Only poll while the person is actually here: tab visible, MIGO 103 tab
  // open AND some mouse/keyboard/touch activity in the last 10 minutes. An
  // abandoned open tab therefore stops calling the server (and stops
  // refreshing the login session / SAP idle clocks) after 10 idle minutes.
  var IDLE_MS = 10 * 60 * 1000;
  var lastActive = Date.now();
  ['mousemove', 'keydown', 'click', 'scroll', 'touchstart'].forEach(function (ev) {
    document.addEventListener(ev, function () { lastActive = Date.now(); }, { passive: true, capture: true });
  });

  function isActive() {
    if (document.hidden) return false;
    if (Date.now() - lastActive > IDLE_MS) return false;
    return !tabEl || tabEl.classList.contains('active');
  }

  function fmtWait(sec) {
    if (sec === null || sec === undefined || isNaN(sec)) return '';
    sec = Math.max(0, Number(sec));
    var m = Math.floor(sec / 60);
    if (m < 1) return '<1m';
    if (m < 60) return m + 'm';
    var h = Math.floor(m / 60);
    if (h < 24) return h + 'h ' + ('0' + (m % 60)).slice(-2) + 'm';
    var d = Math.floor(h / 24);
    return d + 'd ' + (h % 24) + 'h';
  }

  function fmtAgo(ts) {
    var m = Math.floor(Math.max(0, Date.now() - ts) / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return m + 'm ago';
    var h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }

  function chip(cls, text) {
    var s = document.createElement('span');
    s.className = 'mpl-chip ' + cls;
    s.textContent = text;
    return s;
  }

  // Leaving via a link: land back on the MIGO 103 tab and save any draft first.
  function onNavClick(ev) {
    if (ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
    try { sessionStorage.setItem('mi_reload_tab', 'migo103'); } catch (e) {}   // land on this tab
    try { if (typeof window.miFlushDraft === 'function') window.miFlushDraft(); } catch (e) {}
    try { if (typeof window.miFlushDraft105 === 'function') window.miFlushDraft105(); } catch (e) {}
  }

  function buildRow(it, visits, prevId) {
    var a = document.createElement('a');
    a.className = 'mpl-row' + (it.id === curId ? ' mpl-current' : '');
    a.href = '/view/' + encodeURIComponent(it.id);

    var main = document.createElement('div');
    main.className = 'mpl-main';
    var inv = document.createElement('span');
    inv.className = 'mpl-inv';
    inv.textContent = it.invoice_number ? String(it.invoice_number) : ('Record #' + it.id);
    var ven = document.createElement('span');
    ven.className = 'mpl-ven';
    ven.textContent = it.vendor_name ? String(it.vendor_name) : '';
    main.appendChild(inv);
    main.appendChild(ven);
    a.appendChild(main);

    var meta = document.createElement('div');
    meta.className = 'mpl-meta';

    if (it.id === curId) { meta.appendChild(chip('mpl-c-here', "You're here")); }
    if (prevId && it.id === prevId) { meta.appendChild(chip('mpl-c-last', '↩ Last visited')); }
    if (it.posting_status) { meta.appendChild(chip('mpl-c-post', '⏳ Posting…')); }
    var who = it.claimed_by || [];
    if (who.length) {
      var nm = String(who[0]) + (who.length > 1 ? ' +' + (who.length - 1) : '');
      meta.appendChild(chip('mpl-c-on', '👤 ' + nm + ' is on it'));
    }
    if (it.has_my_draft) { meta.appendChild(chip('mpl-c-draft', '📝 Draft')); }
    if (!it.posting_status && !who.length && !it.has_my_draft && it.id !== curId && it.id !== prevId) {
      meta.appendChild(chip('mpl-c-ready', 'Ready'));
    }

    var seen = visits[it.id];
    if (seen && it.id !== curId) {
      var sv = document.createElement('span');
      sv.className = 'mpl-wait';
      sv.textContent = 'opened ' + fmtAgo(seen.t);
      meta.appendChild(sv);
    }

    var w = document.createElement('span');
    w.className = 'mpl-wait';
    var wt = fmtWait(it.waiting_seconds);
    w.textContent = wt ? 'waiting ' + wt : '';
    meta.appendChild(w);
    a.appendChild(meta);

    a.addEventListener('click', onNavClick);
    return a;
  }

  function matches(it, q) {
    if (!q) return true;
    q = q.toLowerCase();
    var inv = String(it.invoice_number || '').toLowerCase();
    var ven = String(it.vendor_name || '').toLowerCase();
    var id  = String(it.id);
    return inv.indexOf(q) !== -1 || ven.indexOf(q) !== -1 || id === q || ('#' + id) === q;
  }

  function currentQuery() { return searchEl ? searchEl.value.replace(/^\s+|\s+$/g, '') : ''; }

  function updateBack(prev) {
    if (!backEl) return;
    if (!prev) { backEl.style.display = 'none'; return; }
    backEl.href = '/view/' + encodeURIComponent(prev.id);
    backEl.textContent = '↩ Back to ' + (prev.inv ? prev.inv : ('Record #' + prev.id));
    backEl.title = 'Opened ' + fmtAgo(prev.t);
    backEl.style.display = '';
  }

  function render() {
    var q = currentQuery();
    var visits = visitMap();
    var prev = previousVisit();
    var prevId = prev ? prev.id : 0;

    // learn invoice numbers for visited records the list happens to know
    var dirty = false, stored = readVisits();
    stored.forEach(function (v) {
      if (v.inv) return;
      for (var i = 0; i < items.length; i++) {
        if (items[i].id === v.id && items[i].invoice_number) { v.inv = String(items[i].invoice_number); dirty = true; break; }
      }
    });
    if (dirty) { writeVisits(stored); prev = previousVisit(); prevId = prev ? prev.id : 0; }

    var shown = items.filter(function (it) { return matches(it, q); });
    countEl.textContent = q ? (shown.length + ' of ' + items.length) : String(items.length);
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
    shown.forEach(function (it) { listEl.appendChild(buildRow(it, visits, prevId)); });
    if (!shown.length) {
      var e = document.createElement('div');
      e.className = 'mpl-empty';
      e.textContent = !loaded ? 'Loading…'
        : (q ? 'No waiting invoice matches “' + q + '”.' : 'Nothing is waiting for MIGO 103.');
      listEl.appendChild(e);
    }
    updateBack(prev);
    panel.style.display = (items.length || prev) ? 'block' : 'none';

    if (postCnt) {
      postCnt.textContent = '⏳ ' + items.length + ' waiting';
      postCnt.style.display = items.length ? '' : 'none';
    }
  }

  function load() {
    if (loading) return;
    loading = true;
    lastFetch = Date.now();
    fetch('/api/pending/migo_103', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        loading = false;
        if (d && d.success && Array.isArray(d.items)) { items = d.items; loaded = true; render(); }
      })
      .catch(function () { loading = false; });
  }

  function applyHidden(h) {
    bodyEl.style.display = h ? 'none' : 'block';
    toggleEl.textContent = h ? 'Show' : 'Hide';
  }
  applyHidden(lsGet(HIDE_KEY) === '1');
  toggleEl.addEventListener('click', function () {
    var h = bodyEl.style.display !== 'none';
    lsSet(HIDE_KEY, h ? '1' : '0');
    applyHidden(h);
  });

  if (backEl) backEl.addEventListener('click', onNavClick);

  if (searchEl) {
    searchEl.addEventListener('input', render);          // list only; the box itself is never rebuilt
    searchEl.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') { searchEl.value = ''; render(); return; }
      if (ev.key !== 'Enter') return;
      var rows = listEl.querySelectorAll('a.mpl-row');
      if (rows.length === 1) { onNavClick({ button: 0 }); window.location.href = rows[0].href; }
    });
  }

  if (postCnt) {
    postCnt.addEventListener('click', function () {
      applyHidden(false);
      lsSet(HIDE_KEY, '0');
      try { panel.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) { panel.scrollIntoView(); }
      if (searchEl) { try { searchEl.focus({ preventScroll: true }); } catch (e) {} }
    });
  }

  function tick() {
    if (!isActive()) return;
    if (Date.now() - lastFetch >= POLL_MS) load();
  }
  setInterval(tick, CHECK_MS);
  document.addEventListener('visibilitychange', tick);
  render();            // shows the Back button straight away, before the first fetch returns
  // first look as soon as the tab is shown (or right now if it already is)
  tick();
})();
