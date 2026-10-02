(function () {
  const API_BASE_URL = window.API_BASE_URL || 'http://127.0.0.1:8000';
  const LOGIN_PAGE = './anilog-login-register.html';
  let user = null;
  let ready;
  const nativeFetch = window.fetch.bind(window);

  const style = document.createElement('style');
  style.textContent = `
    #anilog-auth-overlay {
      position: fixed;
      inset: 0;
      z-index: 2147483647;
      display: grid;
      place-content: center;
      justify-items: center;
      gap: 14px;
      background: #05060c;
      color: #f5f3f8;
      font: 13px Arial, Helvetica, sans-serif;
      text-align: center;
    }
    #anilog-auth-overlay[hidden] { display: none; }
    #anilog-auth-spinner {
      width: 36px;
      height: 36px;
      border: 3px solid rgba(157, 44, 255, .2);
      border-top-color: #c34cff;
      border-radius: 50%;
      animation: anilog-auth-spin .8s linear infinite;
    }
    @keyframes anilog-auth-spin { to { transform: rotate(360deg); } }
  `;
  document.head.appendChild(style);

  const overlay = document.createElement('div');
  overlay.id = 'anilog-auth-overlay';
  overlay.setAttribute('role', 'status');
  overlay.setAttribute('aria-live', 'polite');
  const spinner = document.createElement('div');
  spinner.id = 'anilog-auth-spinner';
  const message = document.createElement('div');
  message.textContent = 'Checking your session...';
  overlay.append(spinner, message);
  document.documentElement.appendChild(overlay);

  function getCookie(name) {
    const cookie = document.cookie.split('; ').find(item => item.startsWith(`${name}=`));
    return cookie ? decodeURIComponent(cookie.slice(name.length + 1)) : null;
  }

  function getToken() {
    const storedToken = localStorage.getItem('access_token');
    const token = storedToken || getCookie('access_token');
    if (!token) return null;

    const normalizedToken = token.replace(/^Bearer\s+/i, '').trim();
    if (!storedToken) localStorage.setItem('access_token', normalizedToken);
    return normalizedToken;
  }

  function clearAuth() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user_data');
    document.cookie = 'access_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
  }

  function redirectToLogin(reason) {
    message.textContent = reason;
    spinner.hidden = true;
    window.setTimeout(() => window.location.replace(LOGIN_PAGE), 900);
  }

  function finish(userData, isCached) {
    user = userData;
    overlay.remove();
    window.dispatchEvent(new CustomEvent('anilog:authenticated', {
      detail: { user: userData, cached: isCached }
    }));
    return userData;
  }

  async function authenticate() {
    const token = getToken();
    if (!token) {
      redirectToLogin('Please log in to continue.');
      return null;
    }

    try {
      const response = await nativeFetch(`${API_BASE_URL}/api/auth/me`, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });

      if (response.status === 401) {
        clearAuth();
        redirectToLogin('Your session has expired. Redirecting to login...');
        return null;
      }
      if (!response.ok) {
        throw new Error(`Server returned status: ${response.status}`);
      }

      const userData = await response.json();
      localStorage.setItem('user_data', JSON.stringify(userData));
      return finish(userData, false);
    } catch (error) {
      console.error('AniLog authentication failed:', error);
      try {
        const cachedUser = JSON.parse(localStorage.getItem('user_data') || 'null');
        if (cachedUser && typeof cachedUser === 'object' && cachedUser.username) {
          return finish(cachedUser, true);
        }
      } catch (cacheError) {
        console.error('Cached AniLog profile is invalid:', cacheError);
        localStorage.removeItem('user_data');
      }

      redirectToLogin('Unable to connect. Redirecting to login...');
      return null;
    }
  }

  window.fetch = function (input, init) {
    const requestedUrl = typeof input === 'string' ? input : input.url || input.href;
    return ready.then(authenticatedUser => {
      if (!authenticatedUser) {
        return new Response(null, { status: 401, statusText: 'Authentication required' });
      }

      if (requestedUrl.includes('/api/auth/me')) {
        return new Response(JSON.stringify(authenticatedUser), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        });
      }

      const headers = new Headers(input instanceof Request ? input.headers : undefined);
      new Headers(init && init.headers).forEach((value, key) => headers.set(key, value));
      if (new URL(requestedUrl, window.location.href).origin === new URL(API_BASE_URL).origin &&
          !headers.has('Authorization')) {
        headers.set('Authorization', `Bearer ${getToken()}`);
      }
      return nativeFetch(input, { ...init, headers });
    });
  };

  ready = authenticate();
  window.AniLogAuth = {
    ready,
    getToken,
    getUser: () => user,
    clearAuth
  };
})();
