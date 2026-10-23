/**
 * static/js/migo_105_draft.js
 *
 * Per-user draft of a half-filled MIGO 105 form (same idea as
 * migo_103_draft.js, much simpler form). Lets someone leave an invoice and come
 * back to find what they typed.
 *
 * What is saved (only what DIFFERS from what the server rendered):
 *   fields    Material Doc No., Vendor Invoice Amt, Remarks
 *   sloc      the storage location code the user picked
 *   batches   batch values edited in the line-item table, keyed by line number
 *
 * Nothing here posts anything; submitMigo105() still reads the live form
 * exactly as before. Drafts are stored via /api/draft/migo_105/<id> and are
 * removed automatically when MIGO 105 posts (rf_queue_worker.py).
 */
(function () {
  'use strict';

  var STEP = 'migo_105';
  var FIELD_IDS = ['materialDocNumber105', 'vendorInvoiceDetail105', 'remarks105'];

  var idEl = document.getElementById('history_id_migo105');
  var historyId = idEl ? (parseInt(idEl.value, 10) || 0) : 0;
  if (!historyId) return;

  function $(id) { return document.getElementById(id); }

  function waitFor(cond, timeoutMs, cb) {
    var t0 = Date.now();
    (function poll() {
      var ok = false;
      try { ok = !!cond(); } catch (e) { ok = false; }
      if (ok || Date.now() - t0 >= timeoutMs) { cb(ok); return; }
      setTimeout(poll, 150);
    })();
  }

  function start() {
    var btn = $('migo105Btn');
    if (!btn || btn.disabled) return;          // already posted, or blocked (MIGO 103 reversed)
    if (typeof POST_LOCKED_105 !== 'undefined' && POST_LOCKED_105 &&
        typeof STEP_LOCKS_ENABLED_105 !== 'undefined' && STEP_LOCKS_ENABLED_105) return;
    if (!$('migo105ItemsBody') || !$('storageLocation105')) return;

    var initial = { fields: {}, sloc: '', batches: {} };
    FIELD_IDS.forEach(function (id) { var el = $(id); if (el) initial.fields[id] = el.value; });

    var ready = false, dirtyEarly = false;
    var canSave = true, discarding = false;
    var hasServerDraft = false;
    var lastSavedJson = null;
    var saveTimer = null;
    var notes = [];
    var savedText = '';

    function batchRows() { return document.querySelectorAll('#migo105ItemsBody tr[data-line]'); }

    /* ---------- collect current dirty state ---------- */
    function collect() {
      var d = { v: 1, fields: {}, sloc: null, batches: {} };
      var any = false;

      FIELD_IDS.forEach(function (id) {
        var el = $(id);
        if (el && el.value !== initial.fields[id]) { d.fields[id] = el.value; any = true; }
      });

      var sl = $('storageLocation105');
      if (sl && sl.value !== initial.sloc) { d.sloc = sl.value; any = true; }

      batchRows().forEach(function (row) {
        var line = row.getAttribute('data-line');
        var inp = row.querySelector('.batch-input');
        if (!inp || !Object.prototype.hasOwnProperty.call(initial.batches, line)) return;
        if (inp.value !== initial.batches[line]) { d.batches[line] = inp.value; any = true; }
      });
      return { data: d, any: any };
    }

    /* ---------- saving ---------- */
    function scheduleSave() {
      if (!canSave || discarding) return;
      if (!ready) { dirtyEarly = true; return; }
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(function () { doSave(false); }, 1500);
    }

    function doSave(keepalive) {
      if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
      if (!canSave || discarding || !ready) return;
      var c = collect();
      var url = '/api/draft/' + STEP + '/' + historyId;
      if (!c.any) {
        if (hasServerDraft) {
          fetch(url, { method: 'DELETE', credentials: 'same-origin', keepalive: !!keepalive })
            .then(function () { hasServerDraft = false; lastSavedJson = null; savedText = ''; renderBar(); })
            .catch(function () {});
        }
        return;
      }
      var json = JSON.stringify({ data: c.data });
      if (json === lastSavedJson) return;
      fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: json,
        credentials: 'same-origin',
        keepalive: !!keepalive && json.length < 60000
      }).then(function (r) {
        if (r.status === 403) { canSave = false; return null; }
        return r.ok ? r.json() : null;
      }).then(function (j) {
        if (j && j.success && j.saved !== false) {
          lastSavedJson = json;
          hasServerDraft = true;
          var n = new Date();
          savedText = '✓ Draft saved ' + ('0' + n.getHours()).slice(-2) + ':' + ('0' + n.getMinutes()).slice(-2);
          renderBar();
        }
      }).catch(function () { /* offline etc. -- next change retries */ });
    }

    // pending_list.js calls this when the user leaves via the waiting list
    window.miFlushDraft105 = function () { if (saveTimer) doSave(true); };

    /* ---------- restore ---------- */
    function fmtWhen(iso) {
      try {
        var m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(iso));
        if (!m) return '';
        var mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][parseInt(m[2], 10) - 1];
        return parseInt(m[3], 10) + ' ' + mon + ', ' + m[4] + ':' + m[5];
      } catch (e) { return ''; }
    }

    function restore(draft) {
      var d = draft.data || {};
      var restored = false;

      Object.keys(d.fields || {}).forEach(function (id) {
        var el = $(id);
        if (FIELD_IDS.indexOf(id) >= 0 && el && el.value === initial.fields[id]) {   // untouched since load
          el.value = d.fields[id]; restored = true;
        }
      });

      var sl = $('storageLocation105');
      if (d.sloc && sl && sl.value === initial.sloc && typeof _storageLocations105 !== 'undefined') {
        var loc = _storageLocations105.filter(function (l) { return l.code === d.sloc; })[0];
        if (loc) {
          sl.value = loc.code;
          var s = $('storageLocation105Search');
          if (s) s.value = loc.code + ' — ' + loc.description;
          restored = true;
        } else {
          notes.push('Your saved storage location is no longer available — please pick it again.');
        }
      }

      Object.keys(d.batches || {}).forEach(function (line) {
        if (!/^\d+$/.test(line)) return;
        var row = document.querySelector('#migo105ItemsBody tr[data-line="' + line + '"]');
        var inp = row && row.querySelector('.batch-input');
        if (inp && inp.value === initial.batches[line]) { inp.value = String(d.batches[line]); restored = true; }
      });

      if (restored || notes.length) {
        notes.unshift('📝 Draft restored' + (draft.updated_at ? ' (saved ' + fmtWhen(draft.updated_at) + ')' : '') + '.');
      }
    }

    /* ---------- the little bar above the Post button ---------- */
    function renderBar() {
      var bar = $('migoDraftBar105');
      if (!bar) return;
      var msg = notes.join(' ');
      $('migoDraftMsg105').textContent = msg;
      $('migoDraftSaved105').textContent = savedText;
      $('migoDraftDiscard105').style.display = hasServerDraft ? 'inline-block' : 'none';
      bar.style.display = (msg || savedText) ? 'flex' : 'none';
    }

    function discard() {
      function go() {
        discarding = true;
        if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
        fetch('/api/draft/' + STEP + '/' + historyId, { method: 'DELETE', credentials: 'same-origin' })
          .catch(function () {})
          .then(function () {
            try { sessionStorage.setItem('mi_reload_tab', 'migo105'); } catch (e) {}
            window.location.reload();
          });
      }
      if (typeof showConfirm === 'function') {
        showConfirm('Discard draft?', 'Your saved entries for this invoice will be thrown away and the form reloaded from scratch.', go, 'Yes, discard');
      } else if (window.confirm('Discard your saved draft for this invoice?')) {
        go();
      }
    }
    var disBtn = $('migoDraftDiscard105');
    if (disBtn) disBtn.addEventListener('click', discard);

    /* ---------- wiring ---------- */
    function relevant(el) {
      if (!el) return false;
      if (el.id && FIELD_IDS.indexOf(el.id) >= 0) return true;
      return !!(el.classList && el.classList.contains('batch-input'));
    }
    document.addEventListener('input',  function (e) { if (relevant(e.target)) scheduleSave(); }, true);
    document.addEventListener('change', function (e) { if (relevant(e.target)) scheduleSave(); }, true);

    // picking a storage location sets a hidden input directly (no input event) -- hook the picker
    if (typeof window._selectStorageLocation105 === 'function') {
      var origSel = window._selectStorageLocation105;
      window._selectStorageLocation105 = function (loc) { origSel(loc); scheduleSave(); };
    }

    window.addEventListener('pagehide', function () { if (saveTimer) doSave(true); });
    document.addEventListener('visibilitychange', function () { if (document.hidden && saveTimer) doSave(true); });

    /* ---------- load saved draft + wait for the async parts of the form ---------- */
    var draftResult = { done: false, json: null };
    fetch('/api/draft/' + STEP + '/' + historyId, { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) {
        if (r.status === 403) { canSave = false; return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (j) { draftResult.json = j; draftResult.done = true; })
      .catch(function () { draftResult.done = true; });

    waitFor(function () {
      return draftResult.done &&
             batchRows().length > 0 &&
             typeof _storageLocations105 !== 'undefined' && _storageLocations105.length > 0;
    }, 15000, function () {
      // baseline for the async parts = what the server rendered into them
      var sl = $('storageLocation105');
      initial.sloc = sl ? sl.value : '';
      batchRows().forEach(function (row) {
        var inp = row.querySelector('.batch-input');
        if (inp) initial.batches[row.getAttribute('data-line')] = inp.defaultValue;
      });

      var j = draftResult.json;
      if (j && j.success) {
        if (j.draft && j.draft.data) {
          hasServerDraft = true;
          try { restore(j.draft); } catch (e) { console.error('105 draft restore failed', e); }
        }
        var others = (j.others || []).map(function (o) { return o.username; }).filter(Boolean);
        if (others.length) {
          notes.push('ℹ️ ' + others.join(', ') + (others.length > 1 ? ' also have' : ' also has') + ' an unposted draft for this invoice.');
        }
      }
      ready = true;
      renderBar();
      if (dirtyEarly) { dirtyEarly = false; scheduleSave(); }
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
