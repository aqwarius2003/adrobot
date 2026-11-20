const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/web/static/js/editor.js', 'utf8');
function extract(name) {
  const start = source.indexOf(`  function ${name}(`);
  return start < 0 ? '' : source.slice(start, source.indexOf('\n  }', start) + 4);
}
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.style = {}; }
  appendChild(child) { this.children.push(child); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  querySelector() { const tbody = new Element('tbody'); this.appendChild(tbody); return tbody; }
}
let requests = 0;
const context = {
  window: { APP_CONFIG: {}, OfferCombobox: class {} },
  document: { createElement: (tag) => new Element(tag), createTextNode: (text) => ({ text }) },
  getStatusText: () => 'Синхронизирован', escapeHtml: (text) => text,
  updateWeight: async () => { requests++; }, showToast: () => {}
};
vm.createContext(context);
vm.runInContext(extract('getWeightEditLimits') + '\n' + extract('createStreamCard'), context);
assert.equal(typeof context.getWeightEditLimits, 'function', 'Weight constraints must be applied before rendering');
function controls(offers, status = 'clean') {
  const tree = context.createStreamCard({ id: 1, name: 'Flow 2', is_managed: true, sync_status: status, offers });
  const inputs = [];
  const walk = (el) => { if (el.type === 'number') inputs.push(el); (el.children || []).forEach(walk); };
  walk(tree);
  return inputs;
}
const offer = (id, share, pinned = false, state = 'active') => ({ offer_id: id, name: `Offer ${id}`, share, is_pinned: pinned, state });
(async () => {
  const single = controls([offer(1, 100), offer(2, 0, false, 'removed')])[0];
  assert.equal(single.disabled, true);
  assert.equal(single.min, '100');
  assert.equal(single.max, '100');
  assert.match(single.title, /единственный/i);
  single.value = '87';
  await single.listeners.change();
  assert.equal(requests, 0);
  assert.equal(Number(single.value), 100);

  const pinned = controls([offer(1, 40, true), offer(2, 60, true)]);
  assert.ok(pinned.every((input) => input.disabled));
  const remaining = controls([offer(1, 40, true), offer(2, 60)]);
  assert.equal(remaining[0].disabled, false);
  assert.equal(remaining[1].disabled, true);
  const flexible = controls([offer(1, 30, true), offer(2, 35), offer(3, 35)]);
  assert.equal(flexible[1].min, '0');
  assert.equal(flexible[1].max, '70');
  flexible[1].value = '71';
  await flexible[1].listeners.change();
  assert.equal(requests, 0);
  assert.equal(Number(flexible[1].value), 35);
  flexible[1].value = '34.5';
  await flexible[1].listeners.change();
  assert.equal(requests, 0);
  flexible[1].value = '34';
  const pending = flexible[1].listeners.change();
  assert.equal(flexible[1].disabled, true);
  await pending;
  assert.equal(requests, 1);
  assert.equal(flexible[1].disabled, false);
  assert.ok(controls([offer(1, 50), offer(2, 50)], 'publishing').every((input) => input.disabled));
  console.log('Weight controls: single offer, pins, bounds, invalid input, pending request passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
