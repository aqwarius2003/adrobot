/**
 * ADROBOT App Initialization (CSP-compliant)
 * Safely parses application config from #appConfigData and initializes global UI helpers.
 */
(function () {
  'use strict';

  // Parse application configuration safely from inert JSON script
  const configElem = document.getElementById('appConfigData');
  if (configElem) {
    try {
      window.APP_CONFIG = JSON.parse(configElem.textContent);
    } catch (err) {
      console.error('Failed to parse APP_CONFIG:', err);
      window.APP_CONFIG = {};
    }
  } else {
    window.APP_CONFIG = {};
  }

  // Register CSRF token with apiClient if present
  if (window.APP_CONFIG && window.APP_CONFIG.csrf_token && window.apiClient) {
    window.apiClient.setCsrfToken(window.APP_CONFIG.csrf_token);
  }

  // Responsive sidebar toggle
  const menuOpen = document.getElementById('mobileMenuOpen');
  const menuClose = document.getElementById('mobileMenuClose');
  const sidebar = document.getElementById('appSidebar');

  if (menuOpen && menuClose && sidebar) {
    const mobileLayout = window.matchMedia('(max-width: 768px)');
    const main = document.querySelector('.app-main');
    function setMenuOpen(open, moveFocus = true) {
      const isOpen = mobileLayout.matches && open;
      sidebar.classList.toggle('open', isOpen);
      sidebar.inert = mobileLayout.matches && !isOpen;
      main.inert = isOpen;
      menuOpen.setAttribute('aria-expanded', String(isOpen));
      if (moveFocus) {
        if (isOpen) menuClose.focus();
        else if (mobileLayout.matches) menuOpen.focus();
      }
    }
    menuOpen.addEventListener('click', () => setMenuOpen(true));
    menuClose.addEventListener('click', () => setMenuOpen(false));
    sidebar.addEventListener('keydown', (event) => {
      if (!mobileLayout.matches || !sidebar.classList.contains('open')) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        setMenuOpen(false);
      } else if (event.key === 'Tab') {
        const controls = sidebar.querySelectorAll('button, a[href]');
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    });
    mobileLayout.addEventListener('change', () => setMenuOpen(false, false));
    setMenuOpen(false, false);
  }
})();
