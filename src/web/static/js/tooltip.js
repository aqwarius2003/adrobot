/**
 * Standalone, lightweight, accessible Tooltip module.
 *
 * Requirements:
 * - Independent of countries, Creator, Keitaro, or catalogs.
 * - Single floating DOM node with role="tooltip".
 * - Event delegation for data-tooltip attribute.
 * - Zero network requests.
 * - Safe textContent insertion (no HTML interpretation).
 * - Hover delay (180ms), immediate on focus and touch.
 * - Escape key to dismiss without losing focus.
 * - Touch support (tap shows, repeat tap / tap outside hides).
 * - Viewport collision detection and edge clamping.
 * - ARIA aria-describedby linkage.
 * - Clean cleanup on destroy.
 */
(function (global) {
  'use strict';

  let tooltipEl = null;
  let activeTrigger = null;
  let hoverTimer = null;
  let dismissedTrigger = null;
  let isInitialized = false;

  const HOVER_DELAY = 180; // ms

  function getTooltipElement() {
    if (!tooltipEl) {
      tooltipEl = document.createElement('div');
      tooltipEl.id = 'app-global-tooltip';
      tooltipEl.setAttribute('role', 'tooltip');
      tooltipEl.className = 'app-tooltip hidden';
      document.body.appendChild(tooltipEl);
    }
    return tooltipEl;
  }

  function findTrigger(target) {
    if (!target || !(target instanceof Element)) return null;
    return target.closest('[data-tooltip]');
  }

  function clearTimer() {
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
  }

  function positionTooltip(trigger, el) {
    const rect = trigger.getBoundingClientRect();
    const tooltipRect = el.getBoundingClientRect();

    const margin = 8;
    // Prefer positioning above the trigger
    let top = rect.top - tooltipRect.height - margin;
    let placement = 'top';

    // If overflows above viewport top, flip to bottom
    if (top < margin) {
      top = rect.bottom + margin;
      placement = 'bottom';
    }

    // Center horizontally relative to trigger
    let left = rect.left + (rect.width - tooltipRect.width) / 2;

    // Viewport boundaries clamp
    const minLeft = margin;
    const maxLeft = window.innerWidth - tooltipRect.width - margin;
    if (left < minLeft) left = minLeft;
    if (left > maxLeft) left = maxLeft;

    el.style.top = `${Math.round(top + window.scrollY)}px`;
    el.style.left = `${Math.round(left + window.scrollX)}px`;
    el.setAttribute('data-placement', placement);
  }

  function show(trigger, immediate = false) {
    if (!trigger || trigger === dismissedTrigger) return;
    const text = trigger.getAttribute('data-tooltip');
    if (!text || !text.trim()) return;

    clearTimer();

    const render = () => {
      // If trigger was removed from DOM while waiting, abort
      if (!document.contains(trigger)) {
        hide();
        return;
      }
      activeTrigger = trigger;
      const el = getTooltipElement();
      el.textContent = text;
      el.classList.remove('hidden');

      // Link ARIA aria-describedby
      if (!trigger.hasAttribute('aria-describedby')) {
        trigger.setAttribute('aria-describedby', el.id);
        trigger.dataset.tooltipHadAria = 'false';
      }

      positionTooltip(trigger, el);
    };

    if (immediate) {
      render();
    } else {
      hoverTimer = setTimeout(render, HOVER_DELAY);
    }
  }

  function hide() {
    clearTimer();
    if (activeTrigger) {
      if (activeTrigger.dataset.tooltipHadAria === 'false') {
        activeTrigger.removeAttribute('aria-describedby');
        delete activeTrigger.dataset.tooltipHadAria;
      }
      activeTrigger = null;
    }
    if (tooltipEl) {
      tooltipEl.classList.add('hidden');
    }
  }

  // Event handlers
  function onMouseOver(e) {
    const trigger = findTrigger(e.target);
    if (!trigger) {
      if (activeTrigger && !e.target.closest('#app-global-tooltip')) {
        hide();
      }
      return;
    }
    if (trigger === activeTrigger) return;
    if (trigger !== dismissedTrigger) {
      show(trigger, false);
    }
  }

  function onMouseOut(e) {
    const trigger = findTrigger(e.target);
    if (trigger) {
      // If cursor is still inside the trigger, don't hide
      if (e.relatedTarget && trigger.contains(e.relatedTarget)) return;
      dismissedTrigger = null;
      hide();
    }
  }

  function onFocusIn(e) {
    const trigger = findTrigger(e.target);
    if (trigger) {
      dismissedTrigger = null;
      show(trigger, true); // Immediate on focus
    }
  }

  function onFocusOut(e) {
    const trigger = findTrigger(e.target);
    if (trigger) {
      dismissedTrigger = null;
      hide();
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Escape' && activeTrigger) {
      dismissedTrigger = activeTrigger;
      hide();
    }
  }

  function onTouchStart(e) {
    const trigger = findTrigger(e.target);
    if (!trigger) {
      if (activeTrigger && !e.target.closest('#app-global-tooltip')) {
        hide();
      }
      return;
    }
    // Tap on active trigger closes it
    if (trigger === activeTrigger) {
      hide();
      return;
    }
    // Tap on new trigger shows immediately
    dismissedTrigger = null;
    show(trigger, true);
  }

  function onScrollOrResize() {
    if (activeTrigger) {
      // If trigger was removed, hide immediately
      if (!document.contains(activeTrigger)) {
        hide();
      } else {
        positionTooltip(activeTrigger, getTooltipElement());
      }
    }
  }

  const Tooltip = {
    init: function () {
      if (isInitialized) return;
      isInitialized = true;

      document.addEventListener('mouseover', onMouseOver, true);
      document.addEventListener('mouseout', onMouseOut, true);
      document.addEventListener('focusin', onFocusIn, true);
      document.addEventListener('focusout', onFocusOut, true);
      document.addEventListener('keydown', onKeyDown, true);
      document.addEventListener('touchstart', onTouchStart, { passive: true });
      window.addEventListener('scroll', onScrollOrResize, { passive: true });
      window.addEventListener('resize', onScrollOrResize, { passive: true });
    },

    destroy: function () {
      hide();
      if (!isInitialized) return;
      isInitialized = false;

      document.removeEventListener('mouseover', onMouseOver, true);
      document.removeEventListener('mouseout', onMouseOut, true);
      document.removeEventListener('focusin', onFocusIn, true);
      document.removeEventListener('focusout', onFocusOut, true);
      document.removeEventListener('keydown', onKeyDown, true);
      document.removeEventListener('touchstart', onTouchStart);
      window.removeEventListener('scroll', onScrollOrResize);
      window.removeEventListener('resize', onScrollOrResize);

      if (tooltipEl && tooltipEl.parentNode) {
        tooltipEl.parentNode.removeChild(tooltipEl);
        tooltipEl = null;
      }
    },

    show: show,
    hide: hide,
  };

  // Auto-init on script load
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => Tooltip.init());
    } else {
      Tooltip.init();
    }
  }

  global.AppTooltip = Tooltip;
})(typeof window !== 'undefined' ? window : this);
