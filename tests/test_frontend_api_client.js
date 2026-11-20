/**
 * Tests for api_client.js: in-memory CSRF transport, auto-attachment, retry on 403 CSRF.
 */
const assert = require('assert');

// Mock browser globals
const globalWindow = {
  location: {
    origin: 'http://localhost:8000',
    pathname: '/real/editor',
    search: ''
  },
  APP_CONFIG: {
    is_demo: false,
    csrf_token: 'initial-csrf-123'
  }
};
global.window = globalWindow;

// Load module
const fs = require('fs');
const code = fs.readFileSync('src/web/static/js/api_client.js', 'utf8');
const runInContext = new Function('root', code);
runInContext(globalWindow);

const client = globalWindow.apiClient;

async function runTests() {
  console.log('--- Running API Client Tests ---');

  // Test 1: Initial token
  assert.strictEqual(client.getCsrfToken(), 'initial-csrf-123', 'Initial token must match APP_CONFIG');

  // Test 2: Attaches CSRF to mutating request
  let lastHeaders = null;
  global.fetch = async (url, opts) => {
    lastHeaders = opts.headers;
    return {
      status: 200,
      ok: true,
      json: async () => ({ status: 'ok' })
    };
  };

  await client.fetch('/api/streams/1/offers', { method: 'POST', body: '{}' });
  assert.strictEqual(lastHeaders['X-CSRF-Token'], 'initial-csrf-123', 'POST must attach X-CSRF-Token');

  // Test 3: Does NOT attach CSRF to GET
  lastHeaders = null;
  await client.fetch('/api/campaigns', { method: 'GET' });
  assert.strictEqual(lastHeaders['X-CSRF-Token'], undefined, 'GET must not attach X-CSRF-Token');

  // Test 4: Does NOT attach CSRF to cross-origin URL
  lastHeaders = null;
  await client.fetch('https://external.example.com/api', { method: 'POST' });
  assert.strictEqual(lastHeaders['X-CSRF-Token'], undefined, 'Cross-origin must not attach CSRF token');

  // Test 5: 403 CSRF retry
  let callCount = 0;
  global.fetch = async (url, opts) => {
    callCount++;
    if (url === '/api/auth/session') {
      return {
        status: 200,
        ok: true,
        json: async () => ({ csrf_token: 'fresh-refreshed-token-456' })
      };
    }
    if (callCount === 1) {
      return {
        status: 403,
        ok: false,
        clone: () => ({
          json: async () => ({ detail: 'Invalid CSRF token' })
        })
      };
    }
    return {
      status: 200,
      ok: true,
      json: async () => ({ status: 'ok' })
    };
  };

  const retryRes = await client.fetch('/api/streams/1/offers', { method: 'POST' });
  assert.strictEqual(retryRes.status, 200, 'Must retry and succeed after CSRF refresh');
  assert.strictEqual(client.getCsrfToken(), 'fresh-refreshed-token-456', 'Token must be updated to fresh token');

  // Test 6: app_init.js parses inert JSON config and registers showToast
  const appInitCode = fs.readFileSync('src/web/static/js/app_init.js', 'utf8');
  const mockDoc = {
    getElementById: (id) => {
      if (id === 'appConfigData') {
        return { textContent: JSON.stringify({ is_demo: true, current_mode: 'demo', csrf_token: 'test-token-789' }) };
      }
      return null;
    }
  };
  const testWindow = {
    document: mockDoc,
    apiClient: {
      setCsrfToken: (t) => { testWindow._token = t; }
    }
  };
  new Function('window', 'document', appInitCode)(testWindow, mockDoc);
  assert.strictEqual(testWindow.APP_CONFIG.current_mode, 'demo', 'app_init must parse current_mode from JSON');
  assert.strictEqual(testWindow._token, 'test-token-789', 'app_init must register csrf_token with apiClient');
  assert.strictEqual(typeof testWindow.showToast, 'function', 'app_init must define showToast');

  // Test 7: confirm_switch_demo.js handles click and calls switch-mode
  const confirmCode = fs.readFileSync('src/web/static/js/confirm_switch_demo.js', 'utf8');
  let switchModeCalled = false;
  let clickedListener = null;
  const mockConfirmDoc = {
    addEventListener: (event, cb) => cb(),
    getElementById: (id) => {
      if (id === 'confirmData') {
        return { textContent: JSON.stringify({ csrf_token: 'demo-csrf-token', target_url: '/demo/simulator' }) };
      }
      if (id === 'confirmSwitchBtn') {
        return {
          addEventListener: (event, cb) => { clickedListener = cb; }
        };
      }
      return null;
    }
  };
  const mockConfirmWindow = {
    location: {},
    apiClient: {
      setCsrfToken: () => {},
      fetch: async (url, opts) => {
        if (url === '/api/auth/switch-mode') switchModeCalled = true;
      }
    }
  };
  new Function('window', 'document', confirmCode)(mockConfirmWindow, mockConfirmDoc);
  assert(clickedListener !== null, 'confirm_switch_demo must register click listener');
  await clickedListener();
  assert.strictEqual(switchModeCalled, true, 'confirmSwitchBtn click must call /api/auth/switch-mode');
  assert.strictEqual(mockConfirmWindow.location.href, '/demo/simulator', 'Must redirect to target_url');

  // Test 8: Demo endpoints (/api/demo/...) do NOT require or attach CSRF token
  lastHeaders = null;
  let sessionCheckedForDemo = false;
  global.fetch = async (url, opts) => {
    if (url === '/api/auth/session') sessionCheckedForDemo = true;
    lastHeaders = opts ? opts.headers : {};
    return { status: 200, ok: true, json: async () => ({ status: 'ok' }) };
  };
  await client.fetch('/api/demo/streams/1/push', { method: 'POST', body: '{}' });
  assert.strictEqual(sessionCheckedForDemo, false, 'Demo endpoint must not trigger /api/auth/session call');
  assert.strictEqual(lastHeaders['X-CSRF-Token'], undefined, 'Demo endpoint must not attach X-CSRF-Token');

  // Test 9: Application unreachable throws error with isAppUnreachable
  global.fetch = async () => {
    throw new TypeError('Failed to fetch (ECONNREFUSED)');
  };
  let caughtError = null;
  try {
    await client.fetch('/api/demo/campaigns');
  } catch (err) {
    caughtError = err;
  }
  assert(caughtError !== null, 'Must throw when app is unreachable');
  assert.strictEqual(caughtError.isAppUnreachable, true, 'Must set isAppUnreachable flag');
  assert.strictEqual(caughtError.message, 'Нет связи с приложением. Проверьте, что сервер запущен.');

  // Test 10: showToast deduplicates notifications
  const toastContainerMock = {
    children: [],
    querySelectorAll: () => toastContainerMock.children,
    appendChild: (t) => toastContainerMock.children.push(t),
  };
  const toastDoc = {
    getElementById: (id) => id === 'toastContainer' ? toastContainerMock : null,
    createElement: (tag) => ({
      className: '',
      textContent: '',
      classList: { contains: () => false, add: () => {} },
      remove: () => {}
    }),
  };
  const toastWindow = { document: toastDoc };
  new Function('window', 'document', appInitCode)(toastWindow, toastDoc);
  toastWindow.showToast('Нет связи с приложением. Проверьте, что сервер запущен.', 'error');
  assert.strictEqual(toastContainerMock.children.length, 1, 'First toast added');
  // Second identical call
  toastWindow.showToast('Нет связи с приложением. Проверьте, что сервер запущен.', 'error');
  assert.strictEqual(toastContainerMock.children.length, 1, 'Duplicate toast must NOT be added');

  console.log('✔ All API Client, app_init, and confirm_switch_demo tests passed successfully!');
}

runTests().catch(err => {
  console.error('API Client test failed:', err);
  process.exit(1);
});
