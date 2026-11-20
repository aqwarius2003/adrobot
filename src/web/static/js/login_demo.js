document.addEventListener('DOMContentLoaded', () => {
  const button = document.getElementById('openDemoBtn');
  if (!button) return;

  function showError() {
    const alert = document.getElementById('loginAlert');
    if (!alert) return;
    alert.className = 'login-alert alert-error';
    alert.textContent = 'Не удалось переключить подключение. Попробуйте позже.';
    alert.style.display = 'block';
  }

  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const response = await fetch('/api/auth/switch-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'demo' }),
      });
      if (response.ok || response.status === 403) {
        // An active real session needs the existing confirmation page; a
        // tokenless POST must never revoke it or bypass the CSRF check.
        window.location.href = '/demo/editor';
      } else {
        showError();
      }
    } catch (_) {
      showError();
    } finally {
      button.disabled = false;
    }
  });
});
