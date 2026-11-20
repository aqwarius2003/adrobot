/**
 * Unified Safe API Client with in-memory CSRF token management and session handling.
 */
(function (root) {
  let inMemoryCsrfToken = (root.APP_CONFIG && root.APP_CONFIG.csrf_token) || null;
  let isRefreshingCsrf = null;

  async function refreshCsrfToken() {
    if (isRefreshingCsrf) return isRefreshingCsrf;
    isRefreshingCsrf = (async () => {
      try {
        const res = await fetch('/api/auth/session', {
          method: 'GET',
          headers: { 'Accept': 'application/json' },
          cache: 'no-store',
        });
        if (res.ok) {
          const data = await res.json();
          if (data && data.csrf_token) {
            inMemoryCsrfToken = data.csrf_token;
            return inMemoryCsrfToken;
          }
        }
      } catch (err) {
        console.warn('Failed to refresh CSRF token:', err);
      } finally {
        isRefreshingCsrf = null;
      }
      return inMemoryCsrfToken;
    })();
    return isRefreshingCsrf;
  }

  function isSameOrigin(url) {
    if (!url) return false;
    if (url.startsWith('/')) return true;
    try {
      const parsed = new URL(url, window.location.origin);
      return parsed.origin === window.location.origin;
    } catch (_) {
      return false;
    }
  }

  function isDemoUrl(url) {
    if (!url) return false;
    return url.startsWith('/api/demo/') || url.startsWith('/demo/') || url === '/api/demo';
  }

  async function apiFetch(url, options = {}) {
    const opts = Object.assign({}, options);
    opts.headers = Object.assign({}, opts.headers || {});

    const method = (opts.method || 'GET').toUpperCase();
    const isMutating = ['POST', 'PUT', 'DELETE', 'PATCH'].includes(method);

    const isDemo = (root.APP_CONFIG && root.APP_CONFIG.is_demo) || isDemoUrl(url);
    const requiresCsrf = isMutating && isSameOrigin(url) && !isDemo;

    // Attach CSRF token only for real-mode mutating operations
    if (requiresCsrf) {
      if (!inMemoryCsrfToken) {
        await refreshCsrfToken();
      }
      if (inMemoryCsrfToken) {
        opts.headers['X-CSRF-Token'] = inMemoryCsrfToken;
      }
    }

    let response;
    try {
      response = await fetch(url, opts);
    } catch (networkErr) {
      // Case 1: Browser could not connect to application server
      const appErr = new Error('Нет связи с приложением. Проверьте, что сервер запущен.');
      appErr.isAppUnreachable = true;
      appErr.originalError = networkErr;
      throw appErr;
    }

    // If 403 due to invalid/missing CSRF token in real mode, retry once with fresh token
    if (response.status === 403 && requiresCsrf) {
      const clone = response.clone();
      try {
        const body = await clone.json();
        if (body && typeof body.detail === 'string' && body.detail.includes('CSRF')) {
          const freshToken = await refreshCsrfToken();
          if (freshToken) {
            opts.headers['X-CSRF-Token'] = freshToken;
            try {
              response = await fetch(url, opts);
            } catch (retryErr) {
              const appErr = new Error('Нет связи с приложением. Проверьте, что сервер запущен.');
              appErr.isAppUnreachable = true;
              throw appErr;
            }
          }
        }
      } catch (_) {}
    }

    // Handle session expiration for real mode only
    if (response.status === 401 && isSameOrigin(url) && !isDemo && !isDemoUrl(url)) {
      if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
        const returnTo = encodeURIComponent(window.location.pathname + window.location.search);
        if (typeof showToast === 'function') {
          showToast('Сессия работы с Keitaro истекла. Перенаправление на страницу входа...', 'warning');
        }
        setTimeout(() => {
          window.location.href = `/login?return_to=${returnTo}`;
        }, 1500);
      }
    }

    return response;
  }

  const apiClient = {
    fetch: apiFetch,
    getCsrfToken: () => inMemoryCsrfToken,
    setCsrfToken: (token) => {
      inMemoryCsrfToken = token;
    },
    refreshCsrfToken,
    isDemoUrl,
  };

  root.apiClient = apiClient;
  root.apiFetch = apiFetch;
})(typeof window !== 'undefined' ? window : this);
