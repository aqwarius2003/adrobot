const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/web/static/js/creator.js', 'utf8');
const start = source.indexOf('  function renderCreatedRouting(');
const render = start < 0 ? '' : source.slice(start, source.indexOf('\n  }', start) + 4);

class Element {
  constructor(tag) { this.tag = tag; this.children = []; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this[key] = value; }
  set innerHTML(value) { throw new Error('Result content must be rendered as text'); }
}
const context = {
  document: { createElement: (tag) => new Element(tag) },
  resultFlow1Countries: new Element('span'),
  resultFlow1Redirect: new Element('code'),
  resultFlow2Offers: new Element('div'),
};
vm.createContext(context);
vm.runInContext(render, context);
assert.equal(typeof context.renderCreatedRouting, 'function', 'Creation result must render routing details');
context.renderCreatedRouting({
  flow1_countries: ['Австралия', 'Румыния'],
  flow1_redirect_url: 'https://google.com/',
  flow2_offers: [
    { id: 8, name: 'Offer <Alpha> & Co', share: 34 },
    { id: 9, name: 'Beta', share: 33 },
    { id: 10, name: 'Gamma', share: 33 },
  ],
});
assert.equal(context.resultFlow1Countries.textContent, 'Австралия, Румыния');
assert.equal(context.resultFlow1Redirect.textContent, 'https://google.com/');
const rows = context.resultFlow2Offers.children;
assert.equal(rows.length, 3);
assert.equal(rows[0].children[0].children[0].textContent, 'Offer <Alpha> & Co');
assert.equal(rows[0].children[0].children[1].textContent, 'ID: 8');
assert.deepEqual(rows.map((row) => row.children[1].textContent), ['34%', '33%', '33%']);
assert.ok(rows.every((row) => row.children.length === 2), 'Result offers are read-only');

// A second result replaces the first and uses server shares verbatim.
context.renderCreatedRouting({
  flow1_countries: ['Канада'],
  flow1_redirect_url: 'https://google.com/',
  flow2_offers: [{ id: 11, name: 'Delta', share: 0 }, { id: 12, name: 'Epsilon', share: 100 }],
});
assert.equal(context.resultFlow1Countries.textContent, 'Канада');
assert.deepEqual(context.resultFlow2Offers.children.map((row) => row.children[1].textContent), ['0%', '100%']);
console.log('Creator result: country names, redirect, confirmed shares, safe names and replacement passed');
