/**
 * static/js/job_watcher.js
 *
 * Global "your SAP posting finished" toast, per logged-in user.
 *
 * WHY: pollRFJob() (rf_poller.js) only polls while the page that started the
 * job is open. If the user clicks another invoice meanwhile, that page --
 * and its polling -- is gone. rf_poller.js therefore registers every tracked
 * posting job in localStorage under "mi_jobs_v1:<username>" (per user, so a
 * shared PC never mixes people up); this file is loaded on every page and
 * keeps polling /api/queue_status/<id> for any registered job the current page
 * is not already polling itself. When one finishes it shows a toast
 * (top-right) with the document number, "Open invoice" and "Copy number".
 *
 * It only READS job status from the existing queue API. It never touches the
 * queue, the bots or the worker. All browser storage use is try/catch.
 */
(function () {
  'use strict';

  var ctx  = window.MI_CTX || {};
  var user = ctx.username || '';
  if (!user) return;
  window._miActivePolls = window._miActivePolls || {};   // pages without rf_poller.js (e.g. History)

  var JOBS_KEY  = 'mi_jobs_v1:' + user;
  var SEEN_KEY  = 'mi_toast_seen_v1:' + user;    // localStorage: dedupe across browser tabs
  var CARRY_KEY = 'mi_toast_carry_v1:' + user;   // sessionStorage: toast to show after the page reloads
  var POLL_MS       = 3000;
  var MAX_AGE_MS    = 30 * 60 * 1000;            // ignore registry entries older than this
  var SUCCESS_MS    = 30000;                     // success toast auto-dismiss
  var CARRY_MAX_AGE = 20000;

  var inflight = {};
  var misses   = {};

  function lsGet(k) { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function ssGet(k) { try { return JSON.parse(sessionStorage.getItem(k) || 'null'); } catch (e) { return null; } }
  function ssSet(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function ssDel(k) { try { sessionStorage.removeItem(k); } catch (e) {} }

  function sameRecord(historyId) {
    return !!(ctx.historyId && historyId && Number(historyId) === Number(ctx.historyId));
  }

  function tabFor(step) {
    step = step || '';
    if (step.indexOf('migo_103') === 0) return 'migo103';
    if (step.indexOf('migo_105') === 0) return 'migo105';
    if (step.indexOf('migo_101') === 0) return 'migo101';
    if (step === 'miro') return 'miro';
    return '';
  }

  /* first browser tab to claim a finished job shows the toast; others stay quiet */
  function claim(jobId) {
    var seen = lsGet(SEEN_KEY) || [];
    var id = String(jobId);
    if (seen.indexOf(id) !== -1) return false;
    seen.push(id);
    if (seen.length > 60) seen = seen.slice(-60);
    lsSet(SEEN_KEY, seen);
    return true;
  }

  var DOC_KEYS = [
    ['gate_in_number',      'Gate In Number'],
    ['material_doc_number', 'Material Doc No.'],
    ['miro_doc_number',     'MIGO 105 Doc No.'],
    ['fi_doc_number',       'MIRO Doc No.'],
    ['reversal_doc_number', 'Reversal Doc No.']
  ];

  /* ---------- toast UI ---------- */
  function injectCss() {
    if (document.getElementById('miJobToastCss')) return;
    var st = document.createElement('style');
    st.id = 'miJobToastCss';
    st.textContent =
      '#miJobToasts{position:fixed;top:72px;right:16px;z-index:10050;display:flex;flex-direction:column;gap:10px;width:360px;max-width:calc(100vw - 32px);pointer-events:none;}' +
      '.mi-jt{pointer-events:auto;background:#fff;color:#2d3436;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.18);border-left:5px solid #00b894;padding:12px 14px;font-size:13px;line-height:1.4;}' +
      '.mi-jt.err{border-left-color:#e17055;}' +
      '.mi-jt-head{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;font-weight:700;font-size:14px;}' +
      '.mi-jt.ok .mi-jt-title{color:#00a383;}.mi-jt.err .mi-jt-title{color:#d63031;}' +
      '.mi-jt-x{background:none;border:0;cursor:pointer;font-size:15px;color:#888;padding:0 2px;line-height:1;}' +
      '.mi-jt-sub{color:#636e72;margin-top:2px;word-break:break-word;}' +
      '.mi-jt-line{margin-top:6px;word-break:break-word;}' +
      '.mi-jt-err{margin-top:6px;color:#d63031;word-break:break-word;}' +
      '.mi-jt-act{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;}' +
      '.mi-jt-btn{border:1px solid #d0d5da;background:#f5f6fa;border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer;color:#2d3436;text-decoration:none;display:inline-block;}' +
      '.mi-jt-btn.pri{background:#6c5ce7;border-color:#6c5ce7;color:#fff;}' +
      '#miJobBanner{background:#eef4ff;color:#1e5ca8;border:1px solid #b9d3f7;}';
    document.head.appendChild(st);
  }

  function container() {
    var c = document.getElementById('miJobToasts');
    if (!c) {
      c = document.createElement('div');
      c.id = 'miJobToasts';
      document.body.appendChild(c);
    }
    return c;
  }

  function copyText(txt, btn) {
    function done() {
      var old = btn.textContent;
      btn.textContent = 'Copied ✓';
      setTimeout(function () { btn.textContent = old; }, 1500);
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(done, function () { fallbackCopy(txt); done(); });
        return;
      }
    } catch (e) {}
    fallbackCopy(txt); done();
  }
  function fallbackCopy(txt) {
    try {
      var ta = document.createElement('textarea');
      ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    } catch (e) {}
  }

  function showEntry(entry) {
    try {
      injectCss();
      var job  = entry.job  || {};
      var meta = entry.meta || {};
      var ok   = job.status === 'done';
      var result = job.result || {};

      var card = document.createElement('div');
      card.className = 'mi-jt ' + (ok ? 'ok' : 'err');

      var head = document.createElement('div');
      head.className = 'mi-jt-head';
      var title = document.createElement('span');
      title.className = 'mi-jt-title';
      var isPrint = meta.step === 'zgrn_print';
      title.textContent = isPrint
        ? (ok ? '✓ Print ready' : '✗ Print failed')
        : (ok ? '✓ ' : '✗ ') + (meta.label || 'SAP posting') + (ok ? ' posted' : ' failed');
      var x = document.createElement('button');
      x.type = 'button'; x.className = 'mi-jt-x'; x.title = 'Dismiss'; x.textContent = '✕';
      x.onclick = function () { card.remove(); };
      head.appendChild(title); head.appendChild(x);
      card.appendChild(head);

      var subTxt = [];
      if (meta.invoice) subTxt.push('Inv ' + meta.invoice);
      if (meta.vendor)  subTxt.push(meta.vendor);
      if (!subTxt.length && meta.historyId) subTxt.push('Record #' + meta.historyId);
      if (subTxt.length) {
        var sub = document.createElement('div');
        sub.className = 'mi-jt-sub';
        sub.textContent = subTxt.join(' — ');
        card.appendChild(sub);
      }

      var copyVal = '';
      var printFiles = (isPrint && ok && Array.isArray(result.files)) ? result.files : [];
      printFiles.forEach(function (f) {
        if (!f || !f.label) return;
        var line = document.createElement('div');
        line.className = 'mi-jt-line';
        line.textContent = f.label + ' is ready';
        card.appendChild(line);
      });
      if (ok) {
        DOC_KEYS.forEach(function (k) {
          if (result[k[0]]) {
            var line = document.createElement('div');
            line.className = 'mi-jt-line';
            var b = document.createElement('b');
            b.textContent = k[1] + ': ';
            line.appendChild(b);
            line.appendChild(document.createTextNode(String(result[k[0]])));
            card.appendChild(line);
            if (!copyVal) copyVal = String(result[k[0]]);
          }
        });
      } else {
        var em = document.createElement('div');
        em.className = 'mi-jt-err';
        var msg = String(job.error_message || result.error || (isPrint ? 'Print failed. Open the invoice to try again.' : 'Posting failed. Open the invoice to see details.'));
        em.textContent = msg.length > 240 ? msg.slice(0, 240) + '…' : msg;
        card.appendChild(em);
      }

      var act = document.createElement('div');
      act.className = 'mi-jt-act';
      if (meta.historyId && !sameRecord(meta.historyId)) {
        var a = document.createElement('a');
        a.className = 'mi-jt-btn pri';
        a.href = '/view/' + encodeURIComponent(meta.historyId);
        a.textContent = 'Open invoice';
        act.appendChild(a);
      }
      printFiles.forEach(function (f) {
        if (!f || !f.view_url) return;
        var v = document.createElement('a');
        v.className = 'mi-jt-btn' + (act.childNodes.length ? '' : ' pri');
        v.href = f.view_url; v.target = '_blank'; v.rel = 'noopener';
        v.textContent = 'View ' + (f.label || 'certificate');
        act.appendChild(v);
      });
      if (copyVal) {
        var cb = document.createElement('button');
        cb.type = 'button'; cb.className = 'mi-jt-btn'; cb.textContent = 'Copy number';
        cb.onclick = function () { copyText(copyVal, cb); };
        act.appendChild(cb);
      }
      if (act.childNodes.length) card.appendChild(act);

      container().appendChild(card);
      if (ok) setTimeout(function () { if (card.parentNode) card.remove(); }, SUCCESS_MS);
    } catch (e) { console.error('job toast failed', e); }
  }

  /* ---------- carry a toast across a page reload ---------- */
  function pushCarry(entry) {
    var arr = ssGet(CARRY_KEY) || [];
    entry.ts = Date.now();
    arr.push(entry);
    ssSet(CARRY_KEY, arr);
  }
  function takeCarry() {
    var arr = ssGet(CARRY_KEY) || [];
    ssDel(CARRY_KEY);
    var now = Date.now();
    return arr.filter(function (e) { return e && now - (e.ts || 0) < CARRY_MAX_AGE; });
  }
  function showCarried() { takeCarry().forEach(showEntry); }

  /* ---------- finished-job handling ---------- */
  function slim(job) {
    return { id: job.id, status: job.status, result: job.result || {}, error_message: job.error_message || '' };
  }

  function refreshBell() {
    try { if (typeof fetchNotifications === 'function') fetchNotifications(); } catch (e) {}
  }

  window.miJobFinished = function (job, meta, pagePolled) {
    try {
      if (!job || !meta) return;
      var ok = job.status === 'done';
      var entry = { job: slim(job), meta: meta };

      if (pagePolled) {
        // Print (ZGRN): this page's own status line + "View" links already show
        // the result, so no extra toast here (only when the user has moved on).
        if (meta.step === 'zgrn_print') return;
        // This page polled the job itself: its own inline status already shows
        // a failure, and on success the tab reloads in ~0.4s -- so carry the
        // toast over the reload. Fallback: if no reload happens, show it now.
        if (!ok) return;
        if (!claim(job.id)) return;
        pushCarry(entry);
        setTimeout(showCarried, 2500);
        return;
      }

      var reload = ok && sameRecord(meta.historyId) && !!tabFor(meta.step);
      var mine = claim(job.id);
      if (mine) {
        if (reload) pushCarry(entry); else showEntry(entry);
      }
      if (reload) {
        try { sessionStorage.setItem('mi_reload_tab', tabFor(meta.step)); } catch (e) {}
        setTimeout(function () { window.location.reload(); }, 300);
      } else if (!ok && sameRecord(meta.historyId)) {
        releaseInflightUi(job.error_message);
      }
      refreshBell();
    } catch (e) { console.error('miJobFinished failed', e); }
  };

  /* ---------- "this record is being posted right now" banner ---------- */
  function liveJobsForThisRecord() {
    if (!ctx.historyId) return [];
    var jobs = lsGet(JOBS_KEY) || {};
    var now = Date.now();
    return Object.keys(jobs).map(function (k) { return jobs[k]; }).filter(function (j) {
      return j && sameRecord(j.historyId) && j.step === 'migo_103' &&
             !window._miActivePolls[j.jobId] && now - j.startedAt < MAX_AGE_MS;
    });
  }

  function applyInflightUi() {
    var btn = document.getElementById('migo103Btn');
    var st  = document.getElementById('migo103Status');
    if (!btn || !st) return;
    if (liveJobsForThisRecord().length === 0) { releaseInflightUi(); return; }
    if (!btn.disabled && !btn.dataset.miHeld) {
      btn.dataset.miHeld = '1';
      btn.dataset.miOrig = btn.textContent;
      btn.disabled = true;
      btn.textContent = '⏳ Processing in SAP...';
    }
    if (st.style.display === 'none' || st.dataset.miBanner) {
      st.dataset.miBanner = '1';
      st.style.cssText = 'display:block;background:#eef4ff;color:#1e5ca8;border:1px solid #b9d3f7;padding:12px 16px;border-radius:8px;margin:16px 0;font-weight:500;';
      st.textContent = 'MIGO 103 for this invoice is being posted in SAP. You can keep working — a pop-up will tell you when it is done.';
    }
  }

  function releaseInflightUi(errorMsg) {
    var btn = document.getElementById('migo103Btn');
    var st  = document.getElementById('migo103Status');
    if (btn && btn.dataset.miHeld) {
      btn.disabled = false;
      btn.textContent = btn.dataset.miOrig || '📮 Post MIGO 103';
      delete btn.dataset.miHeld;
    }
    if (st && st.dataset.miBanner) {
      delete st.dataset.miBanner;
      if (errorMsg) {
        st.style.cssText = 'display:block;background:#fff5f5;color:#e17055;border:1px solid #e17055;padding:12px 16px;border-radius:8px;margin:16px 0;font-weight:500;';
        st.textContent = '❌ ' + errorMsg;
      } else {
        st.style.display = 'none';
        st.textContent = '';
      }
    }
  }

  /* ---------- polling loop ---------- */
  function untrack(jobId) {
    var jobs = lsGet(JOBS_KEY) || {};
    if (jobs[String(jobId)]) {
      delete jobs[String(jobId)];
      lsSet(JOBS_KEY, jobs);
    }
  }

  function check(j) {
    fetch('/api/queue_status/' + encodeURIComponent(j.jobId), { credentials: 'same-origin' })
      .then(function (res) {
        if (res.status === 401) return { auth: true };
        if (res.status === 404) return { missing: true };
        return res.json();
      })
      .then(function (data) {
        inflight[j.jobId] = false;
        if (!data || data.auth) return;
        if (data.missing || !data.job) {
          misses[j.jobId] = (misses[j.jobId] || 0) + 1;
          if (misses[j.jobId] >= 5) untrack(j.jobId);
          return;
        }
        var job = data.job;
        if (job.status === 'done' || job.status === 'failed') {
          untrack(j.jobId);
          window.miJobFinished(job, {
            historyId: j.historyId, step: j.step, label: j.label,
            invoice: j.invoice, vendor: j.vendor
          }, false);
        }
      })
      .catch(function () { inflight[j.jobId] = false; });
  }

  function tick() {
    if (document.hidden) return;              // only the visible browser tab polls
    var jobs = lsGet(JOBS_KEY) || {};
    var now = Date.now(), changed = false;
    Object.keys(jobs).forEach(function (id) {
      var j = jobs[id];
      if (!j || !j.jobId || now - (j.startedAt || 0) > MAX_AGE_MS) { delete jobs[id]; changed = true; return; }
      if (window._miActivePolls[id] || inflight[id]) return;
      inflight[id] = true;
      check(j);
    });
    if (changed) lsSet(JOBS_KEY, jobs);
    applyInflightUi();
  }

  function start() {
    showCarried();
    applyInflightUi();
    setInterval(tick, POLL_MS);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });
    tick();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
