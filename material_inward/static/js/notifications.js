// ============================================================
// notifications.js — In-app notification bell
// Only loaded when ENABLE_INAPP_NOTIFICATIONS=true
// ============================================================

const NOTIF_POLL_INTERVAL = 60000; // 60 seconds
let _notifPoller = null;

function initNotifications() {
  fetchNotifications();
  _notifPoller = setInterval(fetchNotifications, NOTIF_POLL_INTERVAL);
}

async function fetchNotifications() {
  try {
    const res = await fetch('/api/notifications/unread');
    if (!res.ok) return;
    const data = await res.json();
    renderNotifications(data.notifications || []);
  } catch (e) {
    // Silently fail — don't break UI if notifications fail
  }
}

function renderNotifications(notifs) {
  const badge = document.getElementById('bellBadge');
  const list  = document.getElementById('bellList');
  if (!badge || !list) return;

  const count = notifs.length;
  badge.textContent = count > 99 ? '99+' : count;
  badge.setAttribute('data-count', count);

  if (count === 0) {
    list.innerHTML = '<div class="notif-empty">No new notifications</div>';
    return;
  }

  list.innerHTML = '';
  notifs.forEach(n => {
    const div = document.createElement('div');
    div.className = 'notif-item';
    div.innerHTML = `
      <div class="notif-title">${_escN(n.title || '')}</div>
      <div class="notif-msg">${_escN(n.message || '')}</div>
      <div class="notif-time">${_formatTime(n.created_at)}</div>
    `;
    div.onclick = () => onNotifClick(n);
    list.appendChild(div);
  });
}

function onNotifClick(notif) {
  markRead(notif.id);
  const bd = document.getElementById('bellDropdown');
  if (bd) bd.classList.remove('show');
  if (notif.history_id) {
    window.location.href = '/view/' + notif.history_id;
  }
}

async function markRead(notifId) {
  try {
    await fetch('/api/notifications/read/' + notifId, { method: 'POST' });
    fetchNotifications();
  } catch (e) {}
}

async function markAllRead() {
  try {
    await fetch('/api/notifications/read_all', { method: 'POST' });
    fetchNotifications();
    const bd = document.getElementById('bellDropdown');
    if (bd) bd.classList.remove('show');
  } catch (e) {}
}

function _formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60)   return 'Just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return d.toLocaleDateString('en-IN', { day:'2-digit', month:'short' });
  } catch (e) {
    return '';
  }
}

function _escN(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Auto-init when script loads
document.addEventListener('DOMContentLoaded', initNotifications);