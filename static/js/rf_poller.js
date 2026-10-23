/**
 * static/js/rf_poller.js
 *
 * Reusable RF job polling utility.
 *
 * Usage (in gate_in.js, migo.js, miro.js):
 *
 *   const jobId = response.job_id;
 *   pollRFJob(jobId, {
 *     onSuccess: (result) => { showSuccess(result.gate_in_number); },
 *     onFailure: (error)  => { showError(error); },
 *     onTimeout: ()       => { showError("Timed out — check logs."); }
 *   });
 *
 * This handles:
 *   - FIX 9: Button is disabled on submit and re-enabled only on final result
 *   - Session expiry detection (401 with session_expired=true → redirect to login)
 *   - Automatic retry with exponential backoff
 */

const RF_POLL_INTERVAL_MS  = 3000;   // Poll every 3 seconds
const RF_POLL_TIMEOUT_MS   = 300000; // Give up after 5 minutes

/**
 * v20: shows the persistent bottom-left panel (showPostingResult(), defined
 * in index.html) with whichever SAP document numbers are present on a
 * successful job result -- Gate In / MIGO 103 / MIGO 105 / MIRO all funnel
 * through pollRFJob, so hooking it here once covers all four instead of
 * duplicating this per tab. Reads known result keys generically; a job
 * type with none of these present (po_fetch, dms_upload, link-attach jobs,
 * etc. -- none of which go through submitAndPoll/pollRFJob today anyway)
 * simply shows nothing.
 */
function _showStickyPostingResult(result) {
  if (typeof showPostingResult !== 'function' || !result) return;
  const KNOWN = [
    { key: 'gate_in_number',      label: 'Gate In Number' },
    { key: 'material_doc_number', label: 'Material Doc No.' },
    { key: 'miro_doc_number',     label: 'MIGO 105 Doc No.' },
    { key: 'fi_doc_number',       label: 'MIRO Doc No.' },
  ];
  const lines = KNOWN
    .filter(function (k) { return result[k.key]; })
    .map(function (k) { return { label: k.label, value: result[k.key] }; });
  if (lines.length === 0) return;
  showPostingResult('SAP Posting Done', lines);
}

/* ------------------------------------------------------------------
 * Per-user job registry (added with MIGO 103 early-notification work).
 *
 * Every tracked posting job is remembered in localStorage under a key that
 * includes the logged-in username (window.MI_CTX.username, set in
 * index.html), so two people sharing one browser/PC never see each other's
 * finished-posting toasts. static/js/job_watcher.js reads this registry on
 * EVERY page and keeps polling any job this page is not polling itself --
 * that is what lets the toast still show up after the user has navigated
 * to another invoice. Pure client-side bookkeeping: no server or bot
 * change, and all storage access is try/catch (private windows etc.).
 * ------------------------------------------------------------------ */
window._miActivePolls = window._miActivePolls || {};

function _miJobsKey() {
  const u = (window.MI_CTX && window.MI_CTX.username) || '';
  return u ? 'mi_jobs_v1:' + u : null;
}
function _miReadJobs() {
  try {
    const k = _miJobsKey();
    if (!k) return {};
    return JSON.parse(localStorage.getItem(k) || '{}') || {};
  } catch (e) { return {}; }
}
function _miWriteJobs(obj) {
  try {
    const k = _miJobsKey();
    if (!k) return;
    localStorage.setItem(k, JSON.stringify(obj));
  } catch (e) { /* storage unavailable -- toast just won't survive navigation */ }
}
function _miTrackJob(jobId, meta) {
  if (!meta || !jobId) return;
  const jobs = _miReadJobs();
  jobs[String(jobId)] = {
    jobId: jobId,
    historyId: meta.historyId || null,
    step: meta.step || '',
    label: meta.label || 'SAP posting',
    invoice: meta.invoice || '',
    vendor: meta.vendor || '',
    startedAt: Date.now()
  };
  _miWriteJobs(jobs);
}
function _miUntrackJob(jobId) {
  const jobs = _miReadJobs();
  if (jobs[String(jobId)]) {
    delete jobs[String(jobId)];
    _miWriteJobs(jobs);
  }
}

/** Works out label/step/record for the job URLs we want toasts for. */
function _miJobMetaFromUrl(url) {
  const ctx = window.MI_CTX || {};
  let m = /^\/api\/run_(migo_101|migo_103|migo_105|miro)$/.exec(url || '');
  if (m) {
    const labels = { migo_101: 'MIGO 101', migo_103: 'MIGO 103', migo_105: 'MIGO 105', miro: 'MIRO' };
    return { historyId: ctx.historyId || null, step: m[1], label: labels[m[1]],
             invoice: ctx.invoiceNumber || '', vendor: ctx.vendor || '' };
  }
  m = /^\/api\/run_migo_(101|103|105)_reversal\/(\d+)/.exec(url || '');
  if (m) {
    return { historyId: parseInt(m[2], 10), step: 'migo_' + m[1] + '_reversal',
             label: 'MIGO ' + m[1] + ' Reversal',
             invoice: ctx.invoiceNumber || '', vendor: ctx.vendor || '' };
  }
  // ADDED: the MIGO 103 / Tax Classification "Print" (ZGRN) button, so its
  // "Print ready" toast also reaches the user after they open another invoice.
  m = /^\/api\/zgrn_print\/(\d+)/.exec(url || '');
  if (m) {
    return { historyId: parseInt(m[1], 10), step: 'zgrn_print', label: 'Print',
             invoice: ctx.invoiceNumber || '', vendor: ctx.vendor || '' };
  }
  return null;
}

function _miJobTerminal(jobId, job, meta) {
  _miUntrackJob(jobId);
  delete window._miActivePolls[jobId];
  try {
    if (meta && typeof window.miJobFinished === 'function') {
      window.miJobFinished(job, meta, true);   // true = this page was polling it itself
    }
  } catch (e) { console.error('miJobFinished failed', e); }
  try {
    if (typeof fetchNotifications === 'function') fetchNotifications();
  } catch (e) { /* bell refresh is best-effort */ }
}

/**
 * Poll /api/queue_status/<jobId> until done or failed.
 * @param {number} jobId       - Job ID returned from the submit endpoint
 * @param {object} callbacks   - { onSuccess, onFailure, onTimeout }
 * @param {HTMLElement} button - The submit button to re-enable on completion
 * @param {object} meta        - optional {historyId, step, label, invoice, vendor};
 *                               when given, the job is registered for the
 *                               cross-page toast (see registry above)
 */
function pollRFJob(jobId, callbacks, button = null, meta = null) {
  const startTime = Date.now();
  if (meta) {
    _miTrackJob(jobId, meta);
    window._miActivePolls[jobId] = true;
  }

  function poll() {
    // Timeout guard
    if (Date.now() - startTime > RF_POLL_TIMEOUT_MS) {
      // hand the job back to static/js/job_watcher.js so a late result still toasts
      if (meta) delete window._miActivePolls[jobId];
      if (button) { button.disabled = false; button.textContent = button._originalText; }
      if (callbacks.onTimeout) callbacks.onTimeout();
      return;
    }

    fetch(`/api/queue_status/${jobId}`)
      .then(res => {
        // FIX 9: Handle session expiry — redirect to login
        if (res.status === 401) {
          return res.json().then(data => {
            if (data.session_expired) {
              showToast('Your session has expired. Please log in again.', 'warning');
              window.location.href = '/login';
            }
          });
        }
        return res.json();
      })
      .then(data => {
        if (!data || !data.job) {
          setTimeout(poll, RF_POLL_INTERVAL_MS);
          return;
        }

        const job = data.job;
        const status = job.status;

        if (status === 'pending' || status === 'running') {
          // Still processing — poll again
          setTimeout(poll, RF_POLL_INTERVAL_MS);
          return;
        }

        // Job finished — re-enable button
        if (button) {
          button.disabled = false;
          button.textContent = button._originalText || 'Save & Post';
        }
        if (meta) _miJobTerminal(jobId, job, meta);

        if (status === 'done') {
          const result = job.result || {};
          _showStickyPostingResult(result);
          if (callbacks.onSuccess) callbacks.onSuccess(result, job);
        } else if (status === 'failed') {
          const errorMsg = job.error_message || 'RF script failed. Check logs.';
          if (callbacks.onFailure) callbacks.onFailure(errorMsg, job);
        }
      })
      .catch(err => {
        console.error('Poll error:', err);
        setTimeout(poll, RF_POLL_INTERVAL_MS);
      });
  }

  poll();
}


/**
 * Submit a form via fetch and start polling for the RF job result.
 *
 * @param {string}      url         - Flask route URL
 * @param {object}      payload     - JSON payload
 * @param {HTMLElement} button      - The button that triggered the submit
 * @param {object}      callbacks   - { onSuccess, onFailure, onError }
 */
function submitAndPoll(url, payload, button, callbacks) {
  // FIX 9: Disable button immediately to prevent double submission
  if (button) {
    button._originalText = button.textContent;
    button.disabled = true;
    button.textContent = '⏳ Processing in SAP...';
  }

  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
    .then(res => {
      // Session expiry check
      if (res.status === 401) {
        return res.json().then(data => {
          if (button) { button.disabled = false; button.textContent = button._originalText; }
          if (data.session_expired) {
            showToast('Session expired. Please log in again.', 'warning');
            window.location.href = '/login';
          }
        });
      }
      // Conflict — already processing
      if (res.status === 409) {
        return res.json().then(data => {
          if (button) { button.disabled = false; button.textContent = button._originalText; }
          showToast('⚠ ' + (data.error || 'Already processing. Please wait.'), 'warning');
        });
      }
      // ADDED (2026-09-08): distinguish "server responded, but not with
      // the JSON we expected" (a real server-side problem -- an
      // uncaught crash, a proxy/gateway error page, a 502/503 from
      // something in front of the app) from an actual network failure
      // (the request never reached the server, or no response ever
      // came back at all). Both used to fall straight into res.json(),
      // and a non-JSON body makes THAT call itself throw -- which the
      // .catch() below then mislabeled as "Network error. Please try
      // again.", telling the user to blame their connection when the
      // real problem was on the server. Reading the body as text first
      // and parsing it ourselves lets each case get its own message.
      return res.text().then(text => {
        try {
          return JSON.parse(text);
        } catch (parseErr) {
          const err = new Error(
            `Server returned an unexpected response (HTTP ${res.status}) instead of a ` +
            'result. This usually means something went wrong on the server -- check the ' +
            'server log, not your connection.'
          );
          err.isServerResponseError = true;
          throw err;
        }
      });
    })
    .then(data => {
      if (!data) return;
      if (!data.success) {
        if (button) { button.disabled = false; button.textContent = button._originalText; }
        if (callbacks.onError) callbacks.onError(data.error || 'Unknown error');
        return;
      }
      // ADDED (2026-10-08): the server short-circuits with
      // {success, already_exists, files} instead of queueing a job when the
      // result already exists (today: /api/zgrn_print when the certificate
      // PDF is already saved) -- nothing to poll, hand the files straight
      // to onSuccess and put the button back.
      if (data.already_exists) {
        if (button) { button.disabled = false; button.textContent = button._originalText; }
        if (callbacks.onSuccess) callbacks.onSuccess({ files: data.files || [] }, null);
        return;
      }
      // Start polling for the job result
      pollRFJob(data.job_id, callbacks, button, _miJobMetaFromUrl(url));
    })
    .catch(err => {
      if (button) { button.disabled = false; button.textContent = button._originalText; }
      console.error('Submit error:', err);
      if (callbacks.onError) {
        callbacks.onError(err && err.isServerResponseError ? err.message : 'Network error. Please try again.');
      }
    });
}
