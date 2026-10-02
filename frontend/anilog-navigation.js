(function () {
  const API_BASE_URL = window.API_BASE_URL || 'http://127.0.0.1:8000';
  const LOGIN_PAGE = './anilog-login-register.html';
  let searchTimer;
  let searchController;

  function notify(message, type) {
    if (typeof window.showToast === 'function') {
      window.showToast(message, type || 'info');
      return;
    }
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type || 'info'}`;
    toast.textContent = message;
    container.appendChild(toast);
    window.setTimeout(() => toast.remove(), 3500);
  }

  function renderUser(user) {
    const avatar = user.avatar_seed ||
      `https://api.dicebear.com/7.x/bottts/svg?seed=${encodeURIComponent(user.username || 'Shadow')}`;
    document.querySelectorAll('#desktop-topbar-avatar, #header-user-avatar, #header-avatar-img, #topbar-avatar, #top-profile-avatar, #detail-topbar-avatar, #merch-topbar-avatar')
      .forEach(image => { image.src = avatar; });
    document.querySelectorAll('#desktop-streak-count, #streak-val, #streak-count-badge, #streak-counter-badge, #streak-counter, #user-streak-val, #topbar-streak-count, #merch-streak-count')
      .forEach(counter => { counter.textContent = user.streak_count ?? 1; });
  }

  async function searchAnime(query, input, results) {
    const cleanQuery = query.trim();
    if (searchController) searchController.abort();
    if (cleanQuery.length < 3) {
      results.classList.remove('active');
      results.replaceChildren();
      return;
    }

    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(async () => {
      const token = window.AniLogAuth && window.AniLogAuth.getToken();
      if (!token) return;
      searchController = new AbortController();
      results.classList.add('active');
      results.textContent = 'Searching...';

      try {
        const response = await fetch(`${API_BASE_URL}/api/anime/kitsu-search?q=${encodeURIComponent(cleanQuery)}`, {
          signal: searchController.signal,
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!response.ok) throw new Error(`Search request failed (${response.status})`);
        const data = await response.json();
        const animeList = data.data || data.results || [];
        results.replaceChildren();
        if (!animeList.length) {
          results.textContent = 'No anime found';
          return;
        }

        animeList.forEach(anime => {
          const attributes = anime.attributes || anime;
          const title = attributes.canonicalTitle || attributes.titles?.en ||
            attributes.titles?.en_jp || anime.title || 'Untitled';
          const posterUrl = attributes.posterImage?.tiny || attributes.posterImage?.small || anime.image || '';
          const item = document.createElement('button');
          item.type = 'button';
          item.className = 'search-result-item';
          const image = document.createElement('img');
          image.src = posterUrl;
          image.alt = `${title} poster`;
          const info = document.createElement('span');
          info.className = 'search-result-info';
          const name = document.createElement('span');
          name.className = 'search-result-title';
          name.textContent = title;
          const meta = document.createElement('span');
          meta.className = 'search-result-meta';
          const rawRating = attributes.averageRating || anime.score;
          const rating = rawRating ? (Number(rawRating) > 10 ? Number(rawRating) / 10 : Number(rawRating)).toFixed(1) : 'N/A';
          meta.textContent = `${(attributes.showType || attributes.subtype || anime.type || 'ANIME').toUpperCase()} · ${rating}`;
          info.append(name, meta);
          item.append(image, info);
          item.addEventListener('click', () => {
            const reviewData = {
              id: anime.id,
              title,
              image: attributes.posterImage?.large || attributes.posterImage?.medium || anime.image || '',
              episodes: attributes.episodeCount || anime.episodes || '',
              status: attributes.status || '',
              type: attributes.showType || attributes.subtype || anime.type || '',
              rating: attributes.averageRating || anime.score || '',
              synopsis: attributes.synopsis || '',
              startDate: attributes.startDate || ''
            };
            window.location.href = `./anilog-anime-review-responsive.html?anime=${encodeURIComponent(JSON.stringify(reviewData))}`;
          });
          results.appendChild(item);
        });
      } catch (error) {
        if (error.name === 'AbortError') return;
        results.textContent = 'Search failed. Try again.';
        notify('Anime search failed. Check your connection and try again.', 'error');
      }
    }, 400);
  }

  function initializeSearch() {
    const input = document.querySelector('[data-anilog-nav-search]');
    const results = document.getElementById('desktop-search-results');
    if (!input || !results) return;
    input.addEventListener('input', () => searchAnime(input.value, input, results));
    document.addEventListener('click', event => {
      if (!event.target.closest('.search')) results.classList.remove('active');
    });
  }

  function initializeActions() {
    const themeButton = document.getElementById('theme-toggle-btn');
    if (themeButton) {
      themeButton.addEventListener('click', () => {
        document.body.classList.toggle('light-theme');
        notify(`Switched to ${document.body.classList.contains('light-theme') ? 'Light' : 'Dark'} Mode`, 'info');
      });
    }

    const logoutButton = document.getElementById('logout-btn');
    if (logoutButton) {
      logoutButton.addEventListener('click', () => {
        window.AniLogAuth?.clearAuth();
        document.cookie = 'access_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
        window.location.href = LOGIN_PAGE;
      });
    }
  }

  window.addEventListener('anilog:authenticated', event => renderUser(event.detail.user));
  window.addEventListener('load', async () => {
    initializeSearch();
    initializeActions();
    const user = await window.AniLogAuth?.ready;
    if (user) renderUser(user);
  });
})();
