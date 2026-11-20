document.addEventListener('DOMContentLoaded', () => {
  const loginForm = document.getElementById('loginForm');
  const apiKeyInput = document.getElementById('apiKeyInput');
  const submitBtn = document.getElementById('submitBtn');
  const openDemoBtn = document.getElementById('openDemoBtn');
  const loginAlert = document.getElementById('loginAlert');

  let countdownInterval = null;

  function showAlert(message, type = 'error') {
    loginAlert.className = `login-alert alert-${type}`;
    loginAlert.innerHTML = message;
    loginAlert.style.display = 'block';
  }

  function hideAlert() {
    loginAlert.style.display = 'none';
    loginAlert.innerHTML = '';
  }

  function startCountdown(seconds) {
    if (countdownInterval) {
      clearInterval(countdownInterval);
    }
    submitBtn.disabled = true;
    let remaining = seconds;

    const render = () => {
      showAlert(
        `Слишком много попыток. Повторите через <span class="countdown-timer">${remaining}</span> сек.`,
        'warning'
      );
    };

    render();
    countdownInterval = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearInterval(countdownInterval);
        countdownInterval = null;
        submitBtn.disabled = false;
        hideAlert();
      } else {
        render();
      }
    }, 1000);
  }

  function getSafeReturnTo(raw) {
    if (!raw || typeof raw !== 'string') return '/real/editor';
    const trimmed = raw.trim();
    if (!trimmed) return '/real/editor';

    // Reject schemes (colon before query), backslashes, protocol-relative, encoded variants
    if (
      trimmed.includes('\\') ||
      trimmed.toLowerCase().includes('%5c') ||
      trimmed.startsWith('//') ||
      trimmed.toLowerCase().includes('%2f%2f') ||
      trimmed.split('?')[0].includes(':') ||
      trimmed.split('?')[0].toLowerCase().includes('%3a')
    ) {
      return '/real/editor';
    }

    try {
      const dummyOrigin = 'http://localhost';
      const parsed = new URL(trimmed, dummyOrigin);
      if (parsed.origin !== dummyOrigin) return '/real/editor';

      const p = parsed.pathname;
      const allowed = ['/real/editor', '/editor', '/real/'];
      const isAllowed = allowed.some((prefix) => p === prefix || p.startsWith(prefix));
      if (!isAllowed) return '/real/editor';

      return parsed.pathname + (parsed.search || '');
    } catch (_) {
      return '/real/editor';
    }
  }

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideAlert();

    const key = apiKeyInput.value.trim();
    if (!key) {
      showAlert('Пожалуйста, введите API-ключ');
      return;
    }

    // Immediately clear input field for security
    apiKeyInput.value = '';
    submitBtn.disabled = true;
    submitBtn.textContent = 'Проверка ключа...';

    const urlParams = new URLSearchParams(window.location.search);
    const returnTo = getSafeReturnTo(urlParams.get('return_to'));

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify({ api_key: key }),
      });

      const data = await res.json().catch(() => ({}));

      if (res.ok && data.authenticated) {
        if (window.apiClient && data.csrf_token) {
          window.apiClient.setCsrfToken(data.csrf_token);
        }
        window.location.href = returnTo;
        return;
      }

      if (res.status === 429) {
        const retrySec = data.retry_after_seconds || parseInt(res.headers.get('Retry-After') || '120', 10);
        startCountdown(retrySec);
      } else if (res.status === 503) {
        showAlert(data.detail || 'Не удалось проверить подключение. Попробуйте позже или откройте демо', 'warning');
      } else {
        showAlert(data.detail || 'API-ключ не подходит или не даёт доступа к кампаниям', 'error');
      }
    } catch (err) {
      showAlert('Ошибка сети при отправке запроса. Попробуйте позже.', 'error');
    } finally {
      if (!countdownInterval) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Войти в Keitaro';
      }
    }
  });

  openDemoBtn.addEventListener('click', async () => {
    try {
      await fetch('/api/auth/switch-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'demo' }),
      });
    } catch (_) {}
    window.location.href = '/demo/editor';
  });
});
