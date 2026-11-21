/**
 * Global Toast Notifications Helper (CSP-compliant)
 * Preserves existing style, position, DOM structure, and animation.
 */
(function () {
  'use strict';

  window.showToast = function (message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const displayMessage =
      ['error', 'warning'].includes(type) && typeof window.humanizeError === 'function'
        ? window.humanizeError(message)
        : message;

    // Do not accumulate identical notifications from repeat clicks
    const existingToasts = container.querySelectorAll('.toast');
    for (const t of existingToasts) {
      if (t.textContent === displayMessage && !t.classList.contains('fade-out')) {
        return;
      }
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = displayMessage;
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('fade-out');
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { showToast: window.showToast };
  }
})();
