const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const file = 'src/web/static/js/errors.js';
const context = { window: {} };
vm.createContext(context);
if (fs.existsSync(file)) vm.runInContext(fs.readFileSync(file, 'utf8'), context);
assert.equal(typeof context.window.humanizeError, 'function', 'Shared error formatter must exist');
const humanize = context.window.humanizeError;

assert.equal(
  humanize('Conflict: stream revision changed (expected 2, current 3). Reload required.'),
  'Данные потока уже изменились. Обновите страницу и повторите действие.'
);
assert.match(humanize('Conflict: stream fingerprint mismatch'), /изменился.*черновик/i);
assert.match(humanize('Conflict: Remote stream was modified in Keitaro since last fetch. Resolve via Cancel -> Fetch Streams.'), /изменился.*черновик/i);
assert.match(humanize('Cannot remove the last active offer in the stream'), /последний активный оффер/i);
assert.match(humanize('Sum of pinned weights (120%) exceeds 100%'), /закреплённых.*100%/i);
assert.match(humanize('All offers are pinned but their sum is 97%, not 100%'), /100%.*закрепление/i);
assert.match(humanize('Active offers share must equal 100% (currently 97%)'), /100%/);
assert.match(humanize('Authentication failed: Invalid API key'), /доступ.*администратору/i);
assert.match(humanize('Оффер #105 недоступен в каталоге Keitaro: Resource not found at /offers/105. Привязка сохранена в архиве.'), /#105.*архиве/);
assert.match(humanize('Read-back verification GET failed: timeout'), /не подтверждён.*повторно/i);
assert.match(humanize('Failed to fetch'), /связь.*публиковали/i);
assert.equal(humanize('Не удалось загрузить потоки кампании'), 'Не удалось загрузить потоки кампании');
assert.equal(humanize({ detail: [{ loc: ['body', 'share'], msg: 'Input should be a valid integer', input: 'secret' }] }), 'Проверьте введённые значения. Доля должна быть целым числом от 0 до 100.');
const unknown = humanize('GET https://internal.example/admin?api_key=secret failed: traceback');
assert.ok(!unknown.includes('secret') && !unknown.includes('http') && !unknown.includes('traceback'));
assert.equal(humanize(new Error('Unknown failure')), 'Не удалось выполнить действие. Обновите страницу. Если ошибка повторяется, обратитесь к администратору.');

// Exercise the actual notification boundary, not a duplicate of its implementation.
const template = fs.readFileSync('src/web/templates/base.html', 'utf8');
assert.ok(template.indexOf('/static/js/errors.js') < template.indexOf('/static/js/app_init.js'));
const appInitSource = fs.readFileSync('src/web/static/js/app_init.js', 'utf8');
const start = appInitSource.indexOf('  window.showToast = function');
const end = appInitSource.indexOf('\n  };', start) + 4;
const messages = [];
const winObj = { humanizeError: humanize };
const toastContext = {
  window: winObj,
  humanizeError: humanize,
  document: {
    getElementById: () => ({
      querySelectorAll: () => [],
      appendChild: (toast) => messages.push(toast.textContent)
    }),
    createElement: () => ({ className: '', classList: { contains: () => false, add: () => {} } })
  },
  setTimeout: () => {}
};
vm.createContext(toastContext);
vm.runInContext(appInitSource.slice(start, end), toastContext);
toastContext.window.showToast('Conflict: stream revision changed (expected 2, current 3). Reload required.', 'error');
toastContext.window.showToast('Изменения сохранены', 'success');
assert.equal(messages[0], humanize('stream revision changed'));
assert.equal(messages[1], 'Изменения сохранены');

for (const [file, target] of [['editor.js', 'editorNotice'], ['simulator.js', 'simNotice']]) {
  const source = fs.readFileSync(`src/web/static/js/${file}`, 'utf8');
  const start = source.indexOf('  function showNotice(');
  const end = source.indexOf('\n  }', start) + 4;
  const element = { classList: { remove() {} } };
  const noticeContext = { window: { humanizeError: humanize }, [target]: element };
  vm.createContext(noticeContext);
  vm.runInContext(source.slice(start, end), noticeContext);
  noticeContext.showNotice('Conflict: stream revision changed (expected 2, current 3). Reload required.', 'conflict');
  assert.equal(element.textContent, humanize('stream revision changed'));
  noticeContext.showNotice('Изменения сохранены', 'success');
  assert.equal(element.textContent, 'Изменения сохранены');
}
console.log('Error messages and notification integration: passed');
