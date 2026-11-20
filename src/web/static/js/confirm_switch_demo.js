/**
 * ADROBOT Demo Confirmation Modal Handler (CSP-compliant)
 */
document.addEventListener('DOMContentLoaded', () => {
  'use strict';

  const dataElem = document.getElementById('confirmData');
  let config = {};
  if (dataElem) {
    try {
      config = JSON.parse(dataElem.textContent);
    } catch (err) {
      console.error('Failed to parse confirmData:', err);
    }
  }

  if (config.csrf_token && window.apiClient) {
    window.apiClient.setCsrfToken(config.csrf_token);
  }

  const btn = document.getElementById('confirmSwitchBtn');
  if (btn) {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        if (window.apiClient) {
          await window.apiClient.fetch('/api/auth/switch-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'demo' })
          });
        }
      } catch (err) {
        console.warn('Switch mode request error:', err);
      }
      window.location.href = config.target_url || '/demo/editor';
    });
  }
});
