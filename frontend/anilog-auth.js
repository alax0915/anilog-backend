(function () {
  const API_BASE_URL = window.API_BASE_URL || 'http://127.0.0.1:8000';
  const LOGIN_PAGE = './anilog-login-register.html';
  let user = null;
  let ready;
  let refreshPromise = null;
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
    nativeFetch(`${API_BASE_URL}/api/auth/logout`, {
      method: 'POST',
      credentials: 'include'
    }).catch(() => {});
  }

  function refreshAccessToken() {
    if (!refreshPromise) {
      refreshPromise = nativeFetch(`${API_BASE_URL}/api/auth/refresh`, {
        method: 'POST',
        credentials: 'include'
      }).then(async response => {
        if (!response.ok) return null;
        const tokens = await response.json();
        if (!tokens.access_token) return null;
        localStorage.setItem('access_token', tokens.access_token);
        return tokens.access_token;
      }).catch(() => null).finally(() => { refreshPromise = null; });
    }
    return refreshPromise;
  }

  async function fetchWithSession(input, init, requestedUrl) {
    const requestHeaders = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init && init.headers).forEach((value, key) => requestHeaders.set(key, value));
    const isBackendRequest = new URL(requestedUrl, window.location.href).origin === new URL(API_BASE_URL).origin;
    const isAuthEndpoint = /\/api\/auth\/(login|refresh|logout)$/.test(requestedUrl);
    if (isBackendRequest && !requestHeaders.has('Authorization') && getToken()) {
      requestHeaders.set('Authorization', `Bearer ${getToken()}`);
    }

    const options = {
      ...init,
      headers: requestHeaders,
      credentials: init?.credentials || (isBackendRequest ? 'include' : undefined)
    };
    const firstRequest = input instanceof Request ? input.clone() : input;
    let response = await nativeFetch(firstRequest, options);
    if (!isBackendRequest || isAuthEndpoint || response.status !== 401) return response;

    const newAccessToken = await refreshAccessToken();
    if (!newAccessToken) return response;
    requestHeaders.set('Authorization', `Bearer ${newAccessToken}`);
    const retryRequest = input instanceof Request ? input.clone() : input;
    response = await nativeFetch(retryRequest, { ...options, headers: requestHeaders });
    return response;
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

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers.Authorization = `Bearer ${token}`;
      let response = await nativeFetch(`${API_BASE_URL}/api/auth/me`, {
        method: 'GET',
        credentials: 'include',
        headers
      });

      if (response.status === 401) {
        const refreshedToken = await refreshAccessToken();
        if (refreshedToken) {
          response = await nativeFetch(`${API_BASE_URL}/api/auth/me`, {
            method: 'GET',
            credentials: 'include',
            headers: { ...headers, Authorization: `Bearer ${refreshedToken}` }
          });
        }
      }

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
      return fetchWithSession(input, init, requestedUrl);
    });
  };

  document.addEventListener('click', event => {
    if (!(event.target instanceof Element)) return;
    const control = event.target.closest('button, [role="button"]');
    if (!control || !/log\s*out|logout/i.test(`${control.textContent} ${control.getAttribute('onclick') || ''}`)) return;
    nativeFetch(`${API_BASE_URL}/api/auth/logout`, {
      method: 'POST',
      credentials: 'include',
      keepalive: true
    }).catch(() => {});
  }, true);

  ready = authenticate();
  window.AniLogAuth = {
    ready,
    getToken,
    getUser: () => user,
    clearAuth,
    refreshAccessToken
  };
})();
