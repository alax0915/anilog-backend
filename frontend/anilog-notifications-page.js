(function () {
  const API_BASE_URL = window.API_BASE_URL || 'http://127.0.0.1:8000';
  let notificationsData = [];
  let activeFilter = 'all';

  function displayError(message) {
    const container = document.getElementById('notif-container');
    if (!container) return;
    container.innerHTML = '';
    const error = document.createElement('div');
    error.style.cssText = 'padding:20px;color:var(--danger);text-align:center;';
    error.textContent = message;
    container.appendChild(error);
  }

  function relativeTime(value) {
    if (!value) return 'Recently';
    const timestamp = new Date(value).getTime();
    if (!Number.isFinite(timestamp)) return 'Recently';
    const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000));
    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  }

  function updateHeaderStats() {
    const unreadCount = notificationsData.filter(item => item.unread).length;
    const badge = document.getElementById('notif-badge-count');
    const markReadButton = document.getElementById('btn-mark-read');
    if (badge) {
      badge.textContent = `${unreadCount} New`;
      badge.style.background = unreadCount > 0 ? 'var(--purple)' : 'var(--panel3)';
      badge.style.color = unreadCount > 0 ? '#fff' : 'var(--muted)';
    }
    if (markReadButton) markReadButton.disabled = unreadCount === 0;
    window.AniLogTheme?.updateNotificationBadge?.(unreadCount);
    try {
      const cachedUser = JSON.parse(localStorage.getItem('user_data') || 'null');
      if (cachedUser) {
        cachedUser.notifications = unreadCount;
        localStorage.setItem('user_data', JSON.stringify(cachedUser));
      }
    } catch (error) {
      console.warn('Unable to cache unread notification count:', error);
    }
  }

  function renderNotifications(filter) {
    activeFilter = filter;
    const container = document.getElementById('notif-container');
    if (!container) return;
    container.replaceChildren();
    const filtered = notificationsData.filter(item => filter === 'all' || item.type === filter);

    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.style.cssText = 'padding:20px;color:var(--muted);text-align:center;';
      empty.textContent = 'No notifications found.';
      container.appendChild(empty);
      updateHeaderStats();
      return;
    }

    filtered.forEach(item => {
      const card = document.createElement('div');
      card.className = `notif-card ${item.unread ? 'unread' : ''}`;
      if (item.animeId) card.dataset.animeId = item.animeId;

      const poster = document.createElement('img');
      poster.className = 'notif-poster';
      poster.src = item.poster || './logo.png';
      poster.alt = item.animeTitle;
      poster.onerror = () => { poster.src = './logo.png'; };

      const content = document.createElement('div');
      content.className = 'notif-content';
      const header = document.createElement('div');
      header.className = 'notif-header';
      const badge = document.createElement('div');
      badge.className = 'notif-badge';
      const icon = document.createElement('span');
      icon.className = 'material-symbols-outlined';
      icon.style.fontSize = '12px';
      icon.textContent = 'circle';
      badge.append(icon, document.createTextNode(` ${item.title}`));
      const time = document.createElement('span');
      time.className = 'notif-time';
      time.textContent = item.time;
      header.append(badge, time);

      const animeTitle = document.createElement('div');
      animeTitle.className = 'notif-title';
      animeTitle.textContent = item.animeTitle;
      const message = document.createElement('div');
      message.className = 'notif-body';
      message.textContent = item.message;
      content.append(header, animeTitle, message);
      card.append(poster, content);
      card.addEventListener('click', () => openNotificationDetail(item));
      container.appendChild(card);
    });
    updateHeaderStats();
  }

  async function fetchNotifications() {
    const response = await fetch(`${API_BASE_URL}/api/user/notifications`, {
      headers: { 'Authorization': `Bearer ${window.AniLogAuth.getToken()}` }
    });
    if (!response.ok) throw new Error(`Unable to load notifications (${response.status})`);
    const payload = await response.json();
    notificationsData = (payload.notifications || []).map(item => ({
      id: item.id,
      animeId: item.anime_id,
      animeTitle: item.anime_title || 'AniLog',
      poster: item.poster,
      type: item.type === 'SCHEDULED_ANIME' ? 'episodes' : 'system',
      title: item.title,
      message: item.message,
      time: relativeTime(item.scheduled_for || item.created_at),
      unread: !item.is_read
    }));
    renderNotifications(activeFilter);
  }

  async function openNotificationDetail(notification) {
    if (notification.unread) {
      try {
        const response = await fetch(`${API_BASE_URL}/api/user/notifications/${encodeURIComponent(notification.id)}/read`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${window.AniLogAuth.getToken()}` }
        });
        if (!response.ok) throw new Error(`Unable to mark notification read (${response.status})`);
        notification.unread = false;
        updateHeaderStats();
      } catch (error) {
        displayError(error.message || 'Could not update notification.');
        return;
      }
    }

    if (!notification.animeId) return;
    const animePayload = {
      id: String(notification.animeId),
      attributes: {
        canonicalTitle: notification.animeTitle,
        posterImage: { large: notification.poster, medium: notification.poster, original: notification.poster }
      }
    };
    sessionStorage.setItem('selectedAnime', JSON.stringify(animePayload));
    window.location.href = `./anilog-anime-detail.html?id=${encodeURIComponent(notification.animeId)}&title=${encodeURIComponent(notification.animeTitle)}`;
  }

  document.querySelectorAll('.filter-btn').forEach(button => {
    button.addEventListener('click', event => {
      document.querySelectorAll('.filter-btn').forEach(item => item.classList.remove('active'));
      event.currentTarget.classList.add('active');
      renderNotifications(event.currentTarget.dataset.filter);
    });
  });

  const markReadButton = document.getElementById('btn-mark-read');
  markReadButton?.addEventListener('click', async () => {
    const originalHTML = markReadButton.innerHTML;
    markReadButton.disabled = true;
    try {
      const response = await fetch(`${API_BASE_URL}/api/user/notifications/read-all`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${window.AniLogAuth.getToken()}` }
      });
      if (!response.ok) throw new Error(`Unable to mark notifications read (${response.status})`);
      markReadButton.innerHTML = '<span class="material-symbols-outlined" style="color:var(--success)">check_circle</span> All Marked as Read';
      await fetchNotifications();
      window.setTimeout(() => { markReadButton.innerHTML = originalHTML; updateHeaderStats(); }, 1500);
    } catch (error) {
      markReadButton.innerHTML = originalHTML;
      displayError(error.message || 'Could not update notifications.');
      updateHeaderStats();
    }
  });

  window.addEventListener('load', async () => {
    const preloader = document.getElementById('preloader');
    try {
      const user = await window.AniLogAuth?.ready;
      if (!user) return;
      await fetchNotifications();
    } catch (error) {
      displayError(error.message || 'Unable to load notifications.');
    } finally {
      if (preloader) {
        preloader.style.opacity = '0';
        preloader.style.visibility = 'hidden';
      }
      document.body.classList.add('loaded');
    }
  });
})();
