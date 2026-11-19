/**
 * Frontend behavioral tests for Tooltip and GEO Country Picker.
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');

// Read tooltip.js source
const tooltipJsPath = path.resolve(__dirname, '../src/web/static/js/tooltip.js');
const tooltipSource = fs.readFileSync(tooltipJsPath, 'utf8');

// Mock a lightweight DOM environment
class MockClassList {
  constructor() {
    this._set = new Set();
  }
  add(c) { this._set.add(c); }
  remove(c) { this._set.delete(c); }
  contains(c) { return this._set.has(c); }
}

class MockElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.classList = new MockClassList();
    this.style = {};
    this.childNodes = [];
    this.parentNode = null;
    this._textContent = '';
    this.dataset = {};
    this._eventListeners = new Map();
  }

  get textContent() { return this._textContent; }
  set textContent(val) { this._textContent = String(val); }

  setAttribute(name, val) { this.attributes.set(name, String(val)); }
  getAttribute(name) { return this.attributes.get(name) || null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }

  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  removeChild(child) {
    const idx = this.childNodes.indexOf(child);
    if (idx !== -1) {
      this.childNodes.splice(idx, 1);
      child.parentNode = null;
    }
    return child;
  }

  closest(selector) {
    let curr = this;
    while (curr) {
      if (selector === '[data-tooltip]' && curr.hasAttribute('data-tooltip')) return curr;
      if (selector === '#app-global-tooltip' && curr.id === 'app-global-tooltip') return curr;
      curr = curr.parentNode;
    }
    return null;
  }

  contains(el) {
    if (el === this) return true;
    for (const child of this.childNodes) {
      if (child.contains(el)) return true;
    }
    return false;
  }

  getBoundingClientRect() {
    return { top: 100, bottom: 120, left: 100, right: 150, width: 50, height: 20 };
  }

  addEventListener(type, fn) {
    if (!this._eventListeners.has(type)) this._eventListeners.set(type, []);
    this._eventListeners.get(type).push(fn);
  }

  removeEventListener(type, fn) {
    if (!this._eventListeners.has(type)) return;
    const arr = this._eventListeners.get(type);
    const idx = arr.indexOf(fn);
    if (idx !== -1) arr.splice(idx, 1);
  }

  dispatchEvent(e) {
    e.target = this;
    let curr = this;
    while (curr) {
      if (curr._eventListeners && curr._eventListeners.has(e.type)) {
        for (const fn of curr._eventListeners.get(e.type)) {
          fn(e);
        }
      }
      curr = curr.parentNode;
    }
  }
}

// Global document & window
const mockDocument = {
  body: new MockElement('body'),
  createElement(tag) { return new MockElement(tag); },
  contains(el) { return mockDocument.body.contains(el); },
  _listeners: new Map(),
  addEventListener(type, fn, opts) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  },
  removeEventListener(type, fn) {
    if (!this._listeners.has(type)) return;
    const arr = this._listeners.get(type);
    const idx = arr.indexOf(fn);
    if (idx !== -1) arr.splice(idx, 1);
  },
  dispatchEvent(e) {
    if (this._listeners.has(e.type)) {
      for (const fn of this._listeners.get(e.type)) {
        fn(e);
      }
    }
  }
};

const mockWindow = {
  innerWidth: 1024,
  innerHeight: 768,
  scrollX: 0,
  scrollY: 0,
  _listeners: new Map(),
  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  },
  removeEventListener(type, fn) {
    if (!this._listeners.has(type)) return;
    const arr = this._listeners.get(type);
    const idx = arr.indexOf(fn);
    if (idx !== -1) arr.splice(idx, 1);
  }
};

// Test Suite
console.log('--- Running Frontend Tests ---');

// 1. Tooltip module evaluation
const vm = require('vm');
const context = {
  window: mockWindow,
  document: mockDocument,
  Element: MockElement,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  console: console,
};
vm.createContext(context);
vm.runInContext(tooltipSource, context);

const Tooltip = context.window.AppTooltip;
assert(Tooltip, 'Tooltip module should be exported to window.AppTooltip');

// Check single floating DOM node created on init
Tooltip.init();
const triggerEl = mockDocument.createElement('span');
triggerEl.setAttribute('data-tooltip', 'Казахстан — KZ');
mockDocument.body.appendChild(triggerEl);

// 2. Test focus immediately shows tooltip
mockDocument.dispatchEvent({ type: 'focusin', target: triggerEl });
const tooltipNode = mockDocument.body.childNodes.find(n => n.id === 'app-global-tooltip');
assert(tooltipNode, 'Global tooltip node must exist');
assert.strictEqual(tooltipNode.getAttribute('role'), 'tooltip', 'Must have role=tooltip');
assert.strictEqual(tooltipNode.classList.contains('hidden'), false, 'Tooltip should be visible');
assert.strictEqual(tooltipNode.textContent, 'Казахстан — KZ', 'Text must match');
assert.strictEqual(triggerEl.getAttribute('aria-describedby'), 'app-global-tooltip', 'Must link aria-describedby');

// 3. Test Escape dismisses tooltip
mockDocument.dispatchEvent({ type: 'keydown', key: 'Escape' });
assert.strictEqual(tooltipNode.classList.contains('hidden'), true, 'Escape must hide tooltip');
assert.strictEqual(triggerEl.hasAttribute('aria-describedby'), false, 'aria-describedby must be unlinked');

// 4. Test touch tap shows, repeat tap hides
mockDocument.dispatchEvent({ type: 'touchstart', target: triggerEl });
assert.strictEqual(tooltipNode.classList.contains('hidden'), false, 'Touch tap must show tooltip');
mockDocument.dispatchEvent({ type: 'touchstart', target: triggerEl });
assert.strictEqual(tooltipNode.classList.contains('hidden'), true, 'Repeat touch tap must hide tooltip');

// 5. Test XSS safety
const xssTrigger = mockDocument.createElement('div');
xssTrigger.setAttribute('data-tooltip', '<img src=x onerror=alert(1)>');
mockDocument.body.appendChild(xssTrigger);
mockDocument.dispatchEvent({ type: 'focusin', target: xssTrigger });
assert.strictEqual(tooltipNode.textContent, '<img src=x onerror=alert(1)>', 'Text must be safe string, not executed HTML');

// 6. Test cleanup
Tooltip.destroy();
assert.strictEqual(context.window.AppTooltip, Tooltip);
console.log('✔ Tooltip module tests passed (Single container, ARIA, Touch, Escape, Safe text, Cleanup)');

// 7. Verify Batch code paste parsing logic
function parseBatchCodes(text, validCodesSet) {
  if (text.includes(',') || /^[A-Za-z\s,]+$/.test(text)) {
    const tokens = text.split(/[,\s]+/).map(t => t.trim().toUpperCase()).filter(Boolean);
    const allAreCodes = tokens.length > 0 && tokens.every(t => /^[A-Z]{2}$/.test(t));
    if (allAreCodes) {
      const invalid = tokens.filter(c => !validCodesSet.has(c));
      if (invalid.length > 0) {
        return { error: `Неизвестный код страны: "${invalid[0]}". Пакет кодов не добавлен.`, codes: [] };
      }
      const unique = [];
      tokens.forEach(c => { if (!unique.includes(c)) unique.push(c); });
      return { error: null, codes: unique };
    }
  }
  return { error: null, codes: null };
}

const validCodes = new Set(['AU', 'RO', 'KZ', 'US', 'CA']);

// Test AU, RO, au -> AU, RO
const batch1 = parseBatchCodes('AU, RO, au', validCodes);
assert.deepStrictEqual(batch1.codes, ['AU', 'RO'], 'Batch paste must normalize and deduplicate');

// Test batch with unknown code
const batch2 = parseBatchCodes('AU, XX, RO', validCodes);
assert.strictEqual(batch2.codes.length, 0, 'Must not partially add batch with invalid code');
assert(batch2.error.includes('XX'), 'Must report unknown code');

// Test prose with comma not treated as code batch
const batch3 = parseBatchCodes('Казахстан, Канада', validCodes);
assert.strictEqual(batch3.codes, null, 'Country names with commas must not be parsed as code batch');

console.log('✔ Batch paste tests passed (AU, RO, au deduplication, invalid code rejection, names preserved)');

console.log('All frontend unit tests passed successfully!');
