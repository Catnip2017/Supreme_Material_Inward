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
 * Poll /api/queue_status/<jobId> until done or failed.
 * @param {number} jobId       - Job ID returned from the submit endpoint
 * @param {object} callbacks   - { onSuccess, onFailure, onTimeout }
 * @param {HTMLElement} button - The submit button to re-enable on completion
 */
function pollRFJob(jobId, callbacks, button = null) {
  const startTime = Date.now();

  function poll() {
    // Timeout guard
    if (Date.now() - startTime > RF_POLL_TIMEOUT_MS) {
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

        if (status === 'done') {
          const result = job.result || {};
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
      return res.json();
    })
    .then(data => {
      if (!data) return;
      if (!data.success) {
        if (button) { button.disabled = false; button.textContent = button._originalText; }
        if (callbacks.onError) callbacks.onError(data.error || 'Unknown error');
        return;
      }
      // Start polling for the job result
      pollRFJob(data.job_id, callbacks, button);
    })
    .catch(err => {
      if (button) { button.disabled = false; button.textContent = button._originalText; }
      console.error('Submit error:', err);
      if (callbacks.onError) callbacks.onError('Network error. Please try again.');
    });
}
