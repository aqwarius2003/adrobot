const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function load(file, fetch) {
  const fields = {};
  for (const id of ['loginForm', 'apiKeyInput', 'submitBtn', 'loginAlert', 'openDemoBtn']) {
    fields[id] = { style: {}, value: 'synthetic-key', listeners: {},
      addEventListener(event, handler) { this.listeners[event] = handler; } };
  }
  // Normal login has no optional button.
  if (file.endsWith('/login.js')) delete fields.openDemoBtn;
  const window = { location: { href: '/login', search: '' } };
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    document: {
      addEventListener(event, handler) { handler(); },
      getElementById(id) { return fields[id] || null; },
    },
    window, fetch, URL, URLSearchParams, setInterval, clearInterval,
  });
  return { fields, window };
}

async function run() {
  const normal = load('src/web/static/js/login.js', async () => ({
    ok: false, status: 503, json: async () => ({}),
  }));
  await normal.fields.loginForm.listeners.submit({ preventDefault() {} });
  assert.match(normal.fields.loginAlert.innerHTML, /Не удалось проверить подключение/);
  assert.doesNotMatch(normal.fields.loginAlert.innerHTML, /демо|demo/i);
  assert.equal(normal.fields.submitBtn.disabled, false);
  assert.equal(normal.fields.apiKeyInput.value, '');

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
  console.log('Login: normal page without optional button; errors; safe confirmation navigation passed');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
