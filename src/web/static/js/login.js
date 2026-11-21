document.addEventListener('DOMContentLoaded', () => {
  const loginForm = document.getElementById('loginForm');
  const apiKeyInput = document.getElementById('apiKeyInput');
  const submitBtn = document.getElementById('submitBtn');
  const loginAlert = document.getElementById('loginAlert');
  const returnToInput = document.getElementById('returnToInput');

  let countdownInterval = null;

  function showAlert(message, type = 'error') {
    loginAlert.className = `login-alert alert-${type}`;
    loginAlert.textContent = message;
    loginAlert.style.display = 'block';
  }

  function hideAlert() {
    loginAlert.style.display = 'none';
    loginAlert.textContent = '';
  }

  function startCountdown(seconds) {
    if (countdownInterval) {
      clearInterval(countdownInterval);
    }
    submitBtn.disabled = true;
    let remaining = seconds;

    const render = () => {
      loginAlert.className = 'login-alert alert-warning';
      loginAlert.textContent = 'Слишком много попыток. Повторите через ';
      const timerSpan = document.createElement('span');
      timerSpan.className = 'countdown-timer';
      timerSpan.textContent = String(remaining);
      loginAlert.appendChild(timerSpan);
      loginAlert.appendChild(document.createTextNode(' сек.'));
      loginAlert.style.display = 'block';
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

    const safeReturnTo = returnToInput && returnToInput.value ? returnToInput.value : '';

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify({ api_key: key, return_to: safeReturnTo }),
      });

      const data = await res.json().catch(() => ({}));

      if (res.ok && data.authenticated) {
        if (window.apiClient && data.csrf_token) {
          window.apiClient.setCsrfToken(data.csrf_token);
        }
        window.location.href = data.return_to || safeReturnTo || '/real/editor';
        return;
      }

      if (res.status === 429) {
        const retrySec = data.retry_after_seconds || parseInt(res.headers.get('Retry-After') || '120', 10);
        startCountdown(retrySec);
      } else if (res.status === 503) {
        showAlert(data.detail || 'Не удалось проверить подключение. Попробуйте позже.', 'warning');
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

});
