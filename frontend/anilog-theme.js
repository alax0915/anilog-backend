(function () {
  const API_BASE_URL = window.API_BASE_URL || 'http://127.0.0.1:8000';
  const THEME_KEY = 'anilog_theme';
  const LOGIN_PAGE = './anilog-login-register.html';

  function cookieValue(name) {
    const cookie = document.cookie.split('; ').find(item => item.startsWith(`${name}=`));
    return cookie ? decodeURIComponent(cookie.slice(name.length + 1)) : null;
  }

  function normalizeTheme(theme) {
    return theme === 'light' ? 'light' : 'dark';
  }

  function applyTheme(theme) {
    const isLight = normalizeTheme(theme) === 'light';
    document.body.classList.toggle('light-theme', isLight);
    document.body.classList.toggle('light-mode', isLight);
    document.documentElement.dataset.theme = isLight ? 'light' : 'dark';
    return isLight ? 'light' : 'dark';
  }

  function setThemeCookie(theme) {
    document.cookie = `${THEME_KEY}=${theme}; path=/; max-age=31536000; SameSite=Lax`;
  }

  function getToken() {
    const token = window.AniLogAuth?.getToken?.() ||
      localStorage.getItem('access_token') || cookieValue('access_token');
    return token ? token.replace(/^Bearer\s+/i, '').trim() : null;
  }

  function extractTheme(user) {
    const settings = user?.user_settings || {};
    return settings.theme || settings.app_theme || settings.appTheme || null;
  }

  function syncSettingsFromServer(token) {
    if (!token) return Promise.resolve(null);
    return fetch(`${API_BASE_URL}/api/user/settings`, {
      method: 'GET',
      headers: { 'Authorization': `Bearer ${token}` }
    }).then(async response => {
      if (response.status === 401) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('user_data');
        document.cookie = 'access_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/;';
        window.location.replace(LOGIN_PAGE);
        return null;
      }
      if (!response.ok) throw new Error(`Settings request failed (${response.status})`);
      const settings = await response.json();
      const theme = normalizeTheme(settings.theme || settings.app_theme);
      localStorage.setItem(THEME_KEY, theme);
      setThemeCookie(theme);
      applyTheme(theme);
      return theme;
    }).catch(error => {
      console.warn('Unable to synchronize theme settings:', error);
      return null;
    });
  }

  async function toggleTheme() {
    const nextTheme = document.body.classList.contains('light-theme') ? 'dark' : 'light';
    applyTheme(nextTheme);
    localStorage.setItem(THEME_KEY, nextTheme);
    setThemeCookie(nextTheme);

    const token = getToken();
    if (!token) return;
    try {
      const response = await fetch(`${API_BASE_URL}/api/user/settings`, {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ theme: nextTheme })
      });
      if (response.status === 401) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('user_data');
        document.cookie = 'access_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/;';
        window.location.replace(LOGIN_PAGE);
        return;
      }
      if (!response.ok) throw new Error(`Theme update failed (${response.status})`);
      const savedSettings = await response.json();
      const savedTheme = normalizeTheme(savedSettings.theme || savedSettings.app_theme);
      localStorage.setItem(THEME_KEY, savedTheme);
      setThemeCookie(savedTheme);
      applyTheme(savedTheme);
    } catch (error) {
      console.error('Theme could not be saved:', error);
      if (typeof window.showToast === 'function') {
        window.showToast('Theme changed on this page but could not be saved to your account.', 'error');
      }
    }
  }

  applyTheme(localStorage.getItem(THEME_KEY) || cookieValue(THEME_KEY) || 'dark');
  window.AniLogTheme = { applyTheme, toggleTheme, ready: Promise.resolve(null) };

  document.addEventListener('click', event => {
    if (!(event.target instanceof Element)) return;
    const control = event.target.closest('#theme-toggle-btn, #btn-toggle-theme, .dropdown-item');
    if (!control) return;
    const controlText = control.textContent.toLowerCase();
    const controlIcon = control.querySelector('.material-symbols-outlined')?.textContent.trim().toLowerCase();
    const isThemeControl = control.id === 'theme-toggle-btn' ||
      control.id === 'btn-toggle-theme' ||
      /theme|light\s*\/\s*dark|dark\s*\/\s*light/.test(controlText) ||
      ['dark_mode', 'light_mode', 'contrast', 'brightness_4', 'brightness_5'].includes(controlIcon);
    if (!isThemeControl) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    toggleTheme();
  }, true);

  window.addEventListener('anilog:authenticated', event => {
    const theme = extractTheme(event.detail?.user);
    if (theme) {
      const normalized = normalizeTheme(theme);
      localStorage.setItem(THEME_KEY, normalized);
      setThemeCookie(normalized);
      applyTheme(normalized);
    }
  });

  const isLoginPage = /anilog-login-register\.html$/.test(window.location.pathname);
  if (window.AniLogAuth?.ready) {
    window.AniLogTheme.ready = window.AniLogAuth.ready.then(async user => {
      const theme = extractTheme(user);
      if (theme) {
        const normalized = normalizeTheme(theme);
        localStorage.setItem(THEME_KEY, normalized);
        setThemeCookie(normalized);
        applyTheme(normalized);
        return normalized;
      } else if (!isLoginPage) {
        return syncSettingsFromServer(getToken());
      }
      return null;
    });
  } else if (!isLoginPage) {
    window.AniLogTheme.ready = syncSettingsFromServer(getToken());
  }
})();
