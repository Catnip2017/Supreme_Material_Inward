/**
 * static/js/migo_103_draft.js
 *
 * Per-user draft of a half-filled MIGO 103 form, plus the "X is on it"
 * heartbeat. Lets someone leave an invoice (to help with another) and come
 * back to find their typed values and pairing as they left them.
 *
 * What is saved (only what DIFFERS from what the server rendered):
 *   fields     header fields the user changed (PO no, dates, delivery note, ...)
 *   inv        edits made in the Invoice line-item table
 *   rows       edits made in the Matched Pairs table (qty / delivery note qty /
 *              rate / amount), keyed by invoice line, with the PO item no as a
 *              safety check
 *   structure  which invoice line is confirmed against which PO line (and the
 *              order), ONLY if the user touched the pairing. If the user never
 *              touched it, the normal auto-pairing runs again as always.
 *
 * Restore replays the exact same page functions the user would have clicked
 * (onInvPairClick / onPoPairClick / confirmMatches), after checking that the
 * invoice and PO lines still look the way they did. If they do not (lines
 * changed meanwhile) the pairing is NOT restored and the user is told to
 * re-pair -- typed header values are still restored.
 *
 * Nothing here posts anything or changes how posting works; submitMigo103()
 * still reads the live form exactly as before. Drafts are stored via
 * /api/draft/migo_103/<id> and removed automatically when the step posts.
 */
(function () {
  'use strict';

  var STEP = 'migo_103';
  var FIELD_IDS = ['migoPoNumber', 'migoDocDate', 'migoPostDate', 'migoDeliveryNote',
                   'migoBillOfLading', 'migoGRSlipNo', 'migoHeaderText', 'migoRemarks'];
  var INV_FIELDS = ['material_code', 'qty', 'unit', 'rate', 'amount', 'hsn_sac', 'short_text'];
  var ROW_FIELDS = ['qty_expected', 'qty_actual', 'rate', 'amount'];   // input order in a matched row

  var idEl = document.getElementById('history_id_migo');
  var historyId = idEl ? (parseInt(idEl.value, 10) || 0) : 0;
  if (!historyId) return;
  var tabEl = document.getElementById('migo103Tab');

  function $(id) { return document.getElementById(id); }

  function start() {
    // Posted (and not reversed) or Gate-In-locked records: nothing to draft.
    if (typeof MIGO_103_HAS_ITEMS_IN_DB !== 'undefined' && MIGO_103_HAS_ITEMS_IN_DB &&
        !(typeof MIGO_103_IS_REVERSED !== 'undefined' && MIGO_103_IS_REVERSED)) return;
    if (typeof POST_LOCKED_103 !== 'undefined' && POST_LOCKED_103 &&
        typeof STEP_LOCKS_ENABLED_103 !== 'undefined' && STEP_LOCKS_ENABLED_103) return;
    if (!$('invoiceItemsBody') || !$('matchedBody') || !$('migo103Btn')) return;

    /* ---------- baseline: what the page looked like when it loaded ---------- */
    var initial = { fields: {}, inv: {} };
    FIELD_IDS.forEach(function (id) { var el = $(id); if (el) initial.fields[id] = el.value; });
    document.querySelectorAll('#invoiceItemsBody tr[data-idx]').forEach(function (row) {
      var idx = row.getAttribute('data-idx');
      var o = {};
      row.querySelectorAll('input[data-field]').forEach(function (inp) {
        o[inp.getAttribute('data-field')] = inp.value;
      });
      var st = descInput(row);
      if (st) o.short_text = st.value;
      initial.inv[idx] = o;
    });

    var touchedStructure = false;
    var ready = false, dirtyEarly = false;
    var canSave = true, discarding = false;
    var hasServerDraft = false;
    var lastSavedJson = null;
    var saveTimer = null;
    var notes = [];
    var savedText = '';

    function descInput(row) {
      var d = row.nextElementSibling;
      return d ? d.querySelector('input[data-field="short_text"]') : null;
    }

    /* ---------- collect current dirty state ---------- */
    function collect() {
      var d = { v: 1, fields: {}, inv: {}, rows: {}, structure: null };
      var any = false;

      FIELD_IDS.forEach(function (id) {
        var el = $(id);
        if (el && el.value !== initial.fields[id]) { d.fields[id] = el.value; any = true; }
      });

      document.querySelectorAll('#invoiceItemsBody tr[data-idx]').forEach(function (row) {
        var idx = row.getAttribute('data-idx');
        var base = initial.inv[idx];
        if (!base) return;                       // a row the user added by hand: not drafted
        var o = {}, diff = false;
        row.querySelectorAll('input[data-field]').forEach(function (inp) {
          var f = inp.getAttribute('data-field');
          if (inp.value !== base[f]) { o[f] = inp.value; diff = true; }
        });
        var st = descInput(row);
        if (st && st.value !== base.short_text) { o.short_text = st.value; diff = true; }
        if (diff) { d.inv[idx] = o; any = true; }
      });

      var structure = [];
      document.querySelectorAll('#matchedBody tr').forEach(function (row) {
        var pn = parseInt(row.getAttribute('data-pair'), 10);
        var pair = (typeof _pairs !== 'undefined') ? _pairs.find(function (p) { return p.pairNum === pn; }) : null;
        if (!pair) return;
        var itemNo = row.getAttribute('data-item-no') || '';
        structure.push({ inv_idx: pair.invIdx, po_idx: pair.poIdx, item_no: itemNo });
        var inputs = row.querySelectorAll('input[type="text"]');
        var ro = {}, rdiff = false;
        ROW_FIELDS.forEach(function (n, i) {
          if (inputs[i] && inputs[i].value !== inputs[i].defaultValue) { ro[n] = inputs[i].value; rdiff = true; }
        });
        if (rdiff) { ro.item_no = itemNo; d.rows[pair.invIdx] = ro; any = true; }
      });
      if (touchedStructure) { d.structure = structure; any = true; }

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

    window.miFlushDraft = function () { if (saveTimer) doSave(true); };

    /* ---------- restore ---------- */
    function invBtn(idx)  { return document.querySelector('#invoiceItemsBody tr[data-idx="' + idx + '"] .pair-btn-inv'); }
    function poRowOf(idx) { return document.querySelector('#poItemsBody tr[data-idx="' + idx + '"]'); }

    function validStructure(arr) {
      var seenInv = {}, seenPo = {};
      for (var i = 0; i < arr.length; i++) {
        var s = arr[i];
        if (!s || typeof s.inv_idx !== 'number') return false;
        if (!invBtn(s.inv_idx) || seenInv[s.inv_idx]) return false;
        seenInv[s.inv_idx] = true;
        if (s.po_idx === null || s.po_idx === undefined) {
          if (s.item_no) return false;
          continue;
        }
        if (typeof s.po_idx !== 'number' || seenPo[s.po_idx]) return false;
        var pr = poRowOf(s.po_idx);
        if (!pr || pr.getAttribute('data-deleted') === 'true') return false;
        if ((pr.getAttribute('data-item-no') || '') !== String(s.item_no || '')) return false;
        if (!pr.querySelector('.pair-btn-po')) return false;
        seenPo[s.po_idx] = true;
      }
      return true;
    }

    function applyStructure(arr) {
      var orig = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView = function () {};   // confirmMatches() scrolls; don't yank the page on load
      try {
        resetAllMatches();
        arr.forEach(function (s) {
          onInvPairClick(invBtn(s.inv_idx));
          if (s.po_idx === null || s.po_idx === undefined) {
            confirmSingleInvoiceLine();
          } else {
            onPoPairClick(poRowOf(s.po_idx).querySelector('.pair-btn-po'));
          }
        });
        if (arr.length) confirmMatches();
      } finally {
        Element.prototype.scrollIntoView = orig;
      }
    }

    function applyRowEdits(rows) {
      Object.keys(rows || {}).forEach(function (invIdx) {
        var pair = (typeof _pairs !== 'undefined') ? _pairs.find(function (p) { return String(p.invIdx) === String(invIdx); }) : null;
        if (!pair) return;
        var tr = document.querySelector('#matchedBody tr[data-pair="' + pair.pairNum + '"]');
        if (!tr) return;
        var saved = rows[invIdx] || {};
        if ((tr.getAttribute('data-item-no') || '') !== String(saved.item_no || '')) return;   // different line now: don't guess
        var inputs = tr.querySelectorAll('input[type="text"]');
        ROW_FIELDS.forEach(function (n, i) {
          if (saved[n] !== undefined && inputs[i]) inputs[i].value = saved[n];
        });
      });
    }

    function restore(draft) {
      var d = draft.data || {};
      var restoredSomething = false;

      Object.keys(d.fields || {}).forEach(function (id) {
        var el = $(id);
        if (FIELD_IDS.indexOf(id) >= 0 && el && el.value === initial.fields[id]) {   // untouched since load
          el.value = d.fields[id]; restoredSomething = true;
        }
      });

      Object.keys(d.inv || {}).forEach(function (idx) {
        if (!/^\d+$/.test(idx)) return;
        var row = document.querySelector('#invoiceItemsBody tr[data-idx="' + idx + '"]');
        if (!row || !initial.inv[idx]) return;
        var o = d.inv[idx] || {};
        Object.keys(o).forEach(function (f) {
          if (INV_FIELDS.indexOf(f) < 0) return;
          var inp = (f === 'short_text') ? descInput(row) : row.querySelector('input[data-field="' + f + '"]');
          if (inp && inp.value === initial.inv[idx][f]) { inp.value = o[f]; restoredSomething = true; }
        });
        try { if (typeof _refreshMismatchForInvRow === 'function') _refreshMismatchForInvRow(parseInt(idx, 10)); } catch (e) {}
      });

      var rowsOk = true;
      if (Array.isArray(d.structure)) {
        if (touchedStructure) {
          rowsOk = false;
        } else if (validStructure(d.structure)) {
          applyStructure(d.structure);
          touchedStructure = true;     // pairing is now the user's own: every later save must keep it
          restoredSomething = true;
        } else {
          rowsOk = false;
          notes.push('Your saved pairing could not be restored because the invoice or PO lines have changed — please pair them again. Everything else you typed was restored.');
        }
      }
      if (rowsOk && d.rows && Object.keys(d.rows).length) { applyRowEdits(d.rows); restoredSomething = true; }

      if (restoredSomething || notes.length) {
        notes.unshift('📝 Draft restored' + (draft.updated_at ? ' (saved ' + fmtWhen(draft.updated_at) + ')' : '') + '.');
      }
    }

    function fmtWhen(iso) {
      try {
        var m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(iso));
        if (!m) return '';
        var mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][parseInt(m[2], 10) - 1];
        return parseInt(m[3], 10) + ' ' + mon + ', ' + m[4] + ':' + m[5];
      } catch (e) { return ''; }
    }

    /* ---------- the little bar above the Post button ---------- */
    function renderBar() {
      var bar = $('migoDraftBar');
      if (!bar) return;
      var msgEl = $('migoDraftMsg'), disEl = $('migoDraftDiscard'), savedEl = $('migoDraftSaved');
      var msg = notes.join(' ');
      msgEl.textContent = msg;
      savedEl.textContent = savedText;
      disEl.style.display = hasServerDraft ? 'inline-block' : 'none';
      bar.style.display = (msg || savedText) ? 'flex' : 'none';
    }

    function discard() {
      function go() {
        discarding = true;
        if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
        fetch('/api/draft/' + STEP + '/' + historyId, { method: 'DELETE', credentials: 'same-origin' })
          .catch(function () {})
          .then(function () {
            try { sessionStorage.setItem('mi_reload_tab', 'migo103'); } catch (e) {}
            window.location.reload();
          });
      }
      if (typeof showConfirm === 'function') {
        showConfirm('Discard draft?', 'Your saved entries for this invoice will be thrown away and the form reloaded from scratch.', go, 'Yes, discard');
      } else if (window.confirm('Discard your saved draft for this invoice?')) {
        go();
      }
    }
    var disBtn = $('migoDraftDiscard');
    if (disBtn) disBtn.addEventListener('click', discard);

    /* ---------- wiring ---------- */
    function relevant(el) {
      if (!el) return false;
      if (el.id && FIELD_IDS.indexOf(el.id) >= 0) return true;
      return !!(el.closest && el.closest('#invoiceItemsBody, #matchedBody'));
    }
    document.addEventListener('input',  function (e) { if (relevant(e.target)) scheduleSave(); }, true);
    document.addEventListener('change', function (e) { if (relevant(e.target)) scheduleSave(); }, true);

    var observer = new MutationObserver(function () { touchedStructure = true; scheduleSave(); });
    function observeMatched() { observer.observe($('matchedBody'), { childList: true }); }

    window.addEventListener('pagehide', function () { if (saveTimer) doSave(true); });
    document.addEventListener('visibilitychange', function () { if (document.hidden && saveTimer) doSave(true); });

    /* ---------- "X is on it" heartbeat ---------- */
    var lastBeat = 0, wasActive = false;
    // Same presence rule as the waiting list: no heartbeat after 10 idle minutes,
    // so an abandoned open tab stops showing "X is on it" and stops polling.
    var IDLE_MS = 10 * 60 * 1000, lastActive = Date.now();
    ['mousemove', 'keydown', 'click', 'scroll', 'touchstart'].forEach(function (ev) {
      document.addEventListener(ev, function () { lastActive = Date.now(); }, { passive: true, capture: true });
    });
    function tabActive() {
      return !document.hidden && (Date.now() - lastActive <= IDLE_MS) &&
             (!tabEl || tabEl.classList.contains('active'));
    }
    function beat() {
      var act = tabActive();
      if (act && (!wasActive || Date.now() - lastBeat >= 20000)) {
        lastBeat = Date.now();
        fetch('/api/claim/' + STEP + '/' + historyId, { method: 'POST', credentials: 'same-origin' }).catch(function () {});
      }
      wasActive = act;
    }
    setInterval(beat, 5000);
    window.addEventListener('pagehide', function () {
      try { if (navigator.sendBeacon) navigator.sendBeacon('/api/claim/' + STEP + '/' + historyId + '/release'); } catch (e) {}
    });
    beat();

    /* ---------- load any saved draft, then go live ---------- */
    function finish() {
      ready = true;
      observeMatched();              // attached AFTER restore, so restoring never counts as "user touched pairing"
      renderBar();
      if (dirtyEarly) { dirtyEarly = false; scheduleSave(); }
    }

    fetch('/api/draft/' + STEP + '/' + historyId, { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) {
        if (r.status === 403) { canSave = false; return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (j) {
        if (j && j.success) {
          if (j.draft && j.draft.data) {
            hasServerDraft = true;
            try { restore(j.draft); } catch (e) { console.error('draft restore failed', e); }
          }
          var others = (j.others || []).map(function (o) { return o.username; }).filter(Boolean);
          if (others.length) {
            notes.push('ℹ️ ' + others.join(', ') + (others.length > 1 ? ' also have' : ' also has') + ' an unposted draft for this invoice.');
          }
        }
        finish();
      })
      .catch(function () { finish(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
