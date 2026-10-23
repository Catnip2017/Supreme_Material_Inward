// ============================================================
// sap_bot_status.js — live "N SAP bot(s) active" indicator
//
// ADDED (2026-09-03): shows the real, live count of SAP automation
// bots currently running on the server, instead of leaving whoever
// manages the automation machine to guess whether it's safe to close
// SAP Logon Pad by hand from what it happens to look like on screen.
// Closing it while a bot is still genuinely mid-run interrupts that
// bot's work (confirmed real incident: po_fetch history_id=628,
// 2026-09-03).
//
// RESTRICTED (2026-09-03, per client decision): only rendered for
// SuperAdmin (see index.html's {% if is_superadmin %}) -- this number
// only matters to whoever might actually touch SAP Logon Pad on the
// automation server; everyone else never sees this element at all, so
// this script simply no-ops on every other page/role.
//
// FIX (2026-09-03): the first version showed a plain "–" placeholder
// before the first successful fetch, and silently left it there
// forever on any failure (e.g. hit before the backend endpoint had
// been deployed/restarted) -- indistinguishable from "genuinely 0" or
// "just hasn't loaded yet", which is exactly the "why isn't this
// updating" confusion this indicator was supposed to prevent, not
// cause. Now: a distinct grey "checking…" state before the first
// reply, and a distinct red "status unavailable" state if fetches keep
// failing -- so a broken/not-yet-deployed indicator LOOKS broken
// instead of looking like a frozen "0".
// ============================================================

const SAP_BOT_STATUS_POLL_INTERVAL = 5000; // 5 seconds — cheap read, keep it fresh
const SAP_BOT_STATUS_FAIL_THRESHOLD = 2;   // show the error state after this many misses in a row

let _sapBotStatusPoller = null;
let _sapBotStatusFailCount = 0;

function initSapBotStatus() {
  const el = document.getElementById('sapBotStatus');
  if (!el) return; // not rendered on this page/role — nothing to do
  fetchSapBotStatus();
  _sapBotStatusPoller = setInterval(fetchSapBotStatus, SAP_BOT_STATUS_POLL_INTERVAL);
}

function _setSapBotState(el, countEl, labelEl, cls, count, label, title) {
  el.classList.remove('sap-bot-status-idle', 'sap-bot-status-active', 'sap-bot-status-checking', 'sap-bot-status-error');
  el.classList.add(cls);
  countEl.textContent = count;
  if (labelEl) labelEl.textContent = label;
  el.title = title;
}

async function fetchSapBotStatus() {
  const el = document.getElementById('sapBotStatus');
  const countEl = document.getElementById('sapBotCount');
  const labelEl = document.getElementById('sapBotLabel');
  if (!el || !countEl) return;

  try {
    const res = await fetch('/api/sap_bot_status');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (!data || !data.success) throw new Error('unsuccessful response');

    _sapBotStatusFailCount = 0;
    const n = data.active_bots || 0;
    _setSapBotState(
      el, countEl, labelEl,
      n === 0 ? 'sap-bot-status-idle' : 'sap-bot-status-active',
      n,
      n === 1 ? 'SAP bot active' : 'SAP bots active',
      n === 0
        ? 'No SAP automation bots are currently running — safe to close SAP Logon Pad.'
        : `${n} SAP automation bot${n === 1 ? '' : 's'} currently running — closing SAP Logon Pad now will interrupt ${n === 1 ? 'it' : 'them'}.`
    );
  } catch (e) {
    _sapBotStatusFailCount += 1;
    console.warn('SAP bot status check failed:', e);
    if (_sapBotStatusFailCount >= SAP_BOT_STATUS_FAIL_THRESHOLD) {
      _setSapBotState(
        el, countEl, labelEl, 'sap-bot-status-error', '!', 'status unavailable',
        'Could not reach the SAP bot status check — this does not affect the bots themselves, just this indicator. Check that the app server is running the latest deploy.'
      );
    }
  }
}

document.addEventListener('DOMContentLoaded', initSapBotStatus);
