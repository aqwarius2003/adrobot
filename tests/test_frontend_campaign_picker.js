const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element extends EventTarget {
  constructor() {
    super();
    this.value = '';
    this.children = [];
    this.attrs = {};
    this.hidden = true;
    this.classList = { toggle() {} };
  }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  removeAttribute(key) { delete this.attrs[key]; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  contains(target) { return target === this || this.children.includes(target); }
  select() {}
  scrollIntoView() {}
}

const document = new Element();
document.createElement = () => new Element();
const context = { window: {}, document, Event };
vm.createContext(context);
const sourcePath = 'src/web/static/js/campaign_picker.js';
assert.ok(fs.existsSync(sourcePath), 'Campaign search component must be available');
vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context);

const select = new Element();
select.options = [
  { value: '', textContent: '-- Выберите кампанию --' },
  { value: '11', textContent: '[ID: 11] Летняя акция Google' },
  { value: '12', textContent: '[ID: 12] Google зимняя акция' },
  { value: '13', textContent: '[ID: 13] Другая кампания' },
  { value: '14', textContent: '[ID: 14] <img src=x onerror=alert(1)>' },
];
select.value = '12';
const input = new Element();
const dropdown = new Element();
dropdown.id = 'campaignDropdown';
const picker = new context.window.CampaignPicker({ select, input, dropdown });
let changes = 0;
select.addEventListener('change', () => changes++);
picker.sync();
assert.equal(input.value, '[ID: 12] Google зимняя акция');

input.dispatchEvent(new Event('focus'));
assert.equal(dropdown.hidden, false);
assert.equal(dropdown.children.length, 5, 'Opening must allow browsing all campaigns');
input.value = '  АКЦИЯ   google ';
input.dispatchEvent(new Event('input'));
assert.deepEqual(dropdown.children.map(row => row.textContent), [
  '[ID: 11] Летняя акция Google', '[ID: 12] Google зимняя акция',
]);
assert.equal(select.value, '12', 'Typing must not switch the current campaign');
assert.equal(changes, 0);

function key(key) {
  const event = new Event('keydown', { cancelable: true });
  event.key = key;
  input.dispatchEvent(event);
}
key('ArrowDown');
key('Enter');
assert.equal(select.value, '11', 'Keyboard selection must commit the highlighted result');
assert.equal(changes, 1);
assert.equal(dropdown.hidden, true);

input.value = '13';
input.dispatchEvent(new Event('input'));
assert.equal(dropdown.children.length, 1, 'Campaign ID must be searchable');
key('Escape');
assert.equal(input.value, '[ID: 11] Летняя акция Google');
assert.equal(select.value, '11');
assert.equal(changes, 1, 'Escape must restore the label without changing campaigns');

input.value = 'несуществующая';
input.dispatchEvent(new Event('input'));
assert.equal(dropdown.children[0].attrs.role, 'status');
key('Enter');
assert.equal(select.value, '11', 'Enter with no results must not select stale results');
key('Tab');
assert.equal(dropdown.hidden, true);
assert.equal(input.value, '[ID: 11] Летняя акция Google');

input.value = '14';
input.dispatchEvent(new Event('input'));
assert.equal(dropdown.children[0].textContent, '[ID: 14] <img src=x onerror=alert(1)>');
dropdown.children[0].dispatchEvent(new Event('click'));
assert.equal(select.value, '14', 'Mouse selection must commit the campaign ID');
assert.equal(changes, 2);
input.dispatchEvent(new Event('click'));
assert.equal(dropdown.hidden, false, 'Click must reopen an already focused input');
document.dispatchEvent(new Event('pointerdown'));
assert.equal(dropdown.hidden, true, 'Clicking outside must close the search');
input.value = '';
input.dispatchEvent(new Event('input'));
assert.equal(select.value, '14', 'Clearing the query must keep the committed campaign');
assert.equal(dropdown.children.length, 5);
input.dispatchEvent(new Event('blur'));
assert.equal(input.value, '[ID: 14] <img src=x onerror=alert(1)>');
key('ArrowUp');
key('Enter');
assert.equal(changes, 2, 'Re-selecting the same campaign must not reload it');
console.log('Campaign picker behavioral tests passed');
