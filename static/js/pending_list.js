/**
 * static/js/pending_list.js
 *
 * "Waiting for MIGO 103" panel (top of the MIGO 103 tab).
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
  var tabEl    = document.getElementById('migo103Tab');
  var idEl     = document.getElementById('history_id_migo');
  var curId    = idEl ? (parseInt(idEl.value, 10) || 0) : 0;

  var POLL_MS  = 10000;
  var CHECK_MS = 3000;
  var lastFetch = 0;
  var loading   = false;
  var HIDE_KEY  = 'mi_mpl_hidden';

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

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

  function chip(cls, text) {
    var s = document.createElement('span');
    s.className = 'mpl-chip ' + cls;
    s.textContent = text;
    return s;
  }

  function buildRow(it) {
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

    var chips = 0;
    if (it.id === curId) { meta.appendChild(chip('mpl-c-here', "You're here")); chips++; }
    if (it.posting_status) { meta.appendChild(chip('mpl-c-post', '⏳ Posting…')); chips++; }
    var who = it.claimed_by || [];
    if (who.length) {
      var nm = String(who[0]) + (who.length > 1 ? ' +' + (who.length - 1) : '');
      meta.appendChild(chip('mpl-c-on', '👤 ' + nm + ' is on it'));
      chips++;
    }
    if (it.has_my_draft) { meta.appendChild(chip('mpl-c-draft', '📝 Draft')); chips++; }
    if (!it.posting_status && !who.length && !it.has_my_draft && it.id !== curId) {
      meta.appendChild(chip('mpl-c-ready', 'Ready'));
    }

    var w = document.createElement('span');
    w.className = 'mpl-wait';
    var wt = fmtWait(it.waiting_seconds);
    w.textContent = wt ? 'waiting ' + wt : '';
    meta.appendChild(w);
    a.appendChild(meta);

    a.addEventListener('click', function (ev) {
      if (ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
      try { sessionStorage.setItem('mi_reload_tab', 'migo103'); } catch (e) {}   // land on this tab
      try { if (typeof window.miFlushDraft === 'function') window.miFlushDraft(); } catch (e) {}
    });
    return a;
  }

  function render(items) {
    countEl.textContent = String(items.length);
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
    items.forEach(function (it) { listEl.appendChild(buildRow(it)); });
    panel.style.display = items.length ? 'block' : 'none';
  }

  function load() {
    if (loading) return;
    loading = true;
    lastFetch = Date.now();
    fetch('/api/pending/migo_103', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        loading = false;
        if (d && d.success && Array.isArray(d.items)) render(d.items);
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

  function tick() {
    if (!isActive()) return;
    if (Date.now() - lastFetch >= POLL_MS) load();
  }
  setInterval(tick, CHECK_MS);
  document.addEventListener('visibilitychange', tick);
  // first look as soon as the tab is shown (or right now if it already is)
  tick();
})();
