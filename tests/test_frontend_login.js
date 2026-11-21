const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class MockElement {
  constructor(tag = 'div') {
    this.tag = tag;
    this.style = {};
    this.value = 'synthetic-key';
    this.listeners = {};
    this.children = [];
    this._text = '';
    this.className = '';
    this.disabled = false;
    this.usedInnerHTML = false;
  }
  get textContent() {
    return this._text + this.children.map((c) => c.textContent || '').join('');
  }
  set textContent(v) {
    this._text = String(v);
    this.children = [];
  }
  get innerHTML() {
    return this.textContent;
  }
  set innerHTML(v) {
    this.usedInnerHTML = true;
    this._text = String(v);
  }
  appendChild(child) {
    this.children.push(child);
  }
  addEventListener(event, handler) {
    this.listeners[event] = handler;
  }
}

const activeIntervals = [];
function customSetInterval(fn, ms) {
  const id = setInterval(fn, ms);
  activeIntervals.push(id);
  return id;
}

function load(file, fetch) {
  const fields = {};
  for (const id of ['loginForm', 'apiKeyInput', 'submitBtn', 'loginAlert', 'openDemoBtn', 'returnToInput']) {
    fields[id] = new MockElement(id);
  }
  fields.returnToInput.value = '/real/editor?camp=42';
  // Normal login has no optional button.
  if (file.endsWith('/login.js')) delete fields.openDemoBtn;
  const window = { location: { href: '/login', search: '' } };
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    document: {
      addEventListener(event, handler) { handler(); },
      getElementById(id) { return fields[id] || null; },
      createElement(tag) { return new MockElement(tag); },
      createTextNode(text) { return { textContent: text }; },
    },
    window, fetch, URL, URLSearchParams, setInterval: customSetInterval, clearInterval,
  });
  return { fields, window };
}

async function run() {
  let lastBody = null;
  const normal = load('src/web/static/js/login.js', async (url, opts) => {
    lastBody = JSON.parse(opts.body);
    return {
      ok: false, status: 503, json: async () => ({}),
    };
  });
  await normal.fields.loginForm.listeners.submit({ preventDefault() {} });
  assert.equal(normal.fields.loginAlert.usedInnerHTML, false, 'loginAlert must not use unsafe innerHTML');
  assert.match(normal.fields.loginAlert.textContent, /Не удалось проверить подключение/);
  assert.doesNotMatch(normal.fields.loginAlert.textContent, /демо|demo/i);
  assert.equal(normal.fields.submitBtn.disabled, false);
  assert.equal(normal.fields.apiKeyInput.value, '');
  assert.equal(lastBody.return_to, '/real/editor?camp=42', 'Login request must pass safe return_to from server input');

  // Test 429 countdown rendered safely
  let timerInterval = null;
  const rateLimit = load('src/web/static/js/login.js', async () => ({
    ok: false,
    status: 429,
    headers: { get: () => '45' },
    json: async () => ({ retry_after_seconds: 45 }),
  }));
  await rateLimit.fields.loginForm.listeners.submit({ preventDefault() {} });
  assert.equal(rateLimit.fields.loginAlert.usedInnerHTML, false, 'Countdown must use safe DOM elements instead of innerHTML');
  assert.equal(rateLimit.fields.submitBtn.disabled, true);
  assert.match(rateLimit.fields.loginAlert.textContent, /Слишком много попыток.*45.*сек/);

  // Test successful login redirects to server-provided safe return_to
  const successLogin = load('src/web/static/js/login.js', async () => ({
    ok: true,
    status: 200,
    json: async () => ({ authenticated: true, csrf_token: 'csrf-xyz', return_to: '/real/editor?camp=42' }),
  }));
  await successLogin.fields.loginForm.listeners.submit({ preventDefault() {} });
  assert.equal(successLogin.window.location.href, '/real/editor?camp=42');

  for (const status of [200, 403]) {
    const optional = load('src/web/static/js/login_demo.js', async () => ({ ok: status === 200, status }));
    await optional.fields.openDemoBtn.listeners.click();
    // A 403 with an existing real session must reach the safe confirmation page.
    assert.equal(optional.window.location.href, '/demo/editor');
    assert.equal(optional.fields.openDemoBtn.disabled, false);
  }

  for (const fetch of [
    async () => ({ ok: false, status: 503 }),
    async () => { throw new Error('offline'); },
  ]) {
    const optional = load('src/web/static/js/login_demo.js', fetch);
    await optional.fields.openDemoBtn.listeners.click();
    assert.equal(optional.window.location.href, '/login');
    assert.equal(optional.fields.loginAlert.style.display, 'block');
    assert.equal(optional.fields.openDemoBtn.disabled, false);
  }
  for (const id of activeIntervals) clearInterval(id);
  console.log('Login: normal page without optional button; errors; safe countdown; safe return_to passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
