/**
 * Shared Client Utilities (CSP-compliant)
 */
(function () {
  'use strict';

  /**
   * Safely escapes HTML special characters for insertion into text or attributes.
   * Replaces &, <, >, ", and ' with corresponding HTML entities.
   *
   * @param {*} str - Input value to escape
   * @returns {string} - Escaped string safe for HTML body and attribute contexts
   */
  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  window.escapeHtml = escapeHtml;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { escapeHtml };
  }
})();
