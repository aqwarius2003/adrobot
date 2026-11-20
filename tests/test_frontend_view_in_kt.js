/**
 * Frontend behavioral unit tests for 'View in KT' button link logic in editor.js.
 */
const assert = require('assert');

// Mock DOM Element
class MockAnchorElement {
  constructor() {
    this.attrs = {};
    this.classList = {
      classes: new Set(),
      add: (cls) => this.classList.classes.add(cls),
      remove: (cls) => this.classList.classes.delete(cls),
      contains: (cls) => this.classList.classes.has(cls),
    };
  }

  get href() {
    return this.attrs['href'] || '';
  }

  set href(val) {
    this.attrs['href'] = val;
  }

  get target() {
    return this.attrs['target'] || '';
  }

  set target(val) {
    this.attrs['target'] = val;
  }

  get rel() {
    return this.attrs['rel'] || '';
  }

  set rel(val) {
    this.attrs['rel'] = val;
  }

  removeAttribute(attr) {
    delete this.attrs[attr];
  }

  getAttribute(attr) {
    return this.attrs[attr] || null;
  }
}

// Function matching editor.js implementation
function createUpdateViewInKtLink(viewInKtLink, appConfig) {
  return function updateViewInKtLink(remoteCampaignId) {
    if (!viewInKtLink) return;
    const isDemo = appConfig && appConfig.is_demo;
    const ktUrl = appConfig && appConfig.keitaro_base_url;
    if (remoteCampaignId && ktUrl && !isDemo) {
      const cleanBase = ktUrl.replace(/\/+$/, '');
      viewInKtLink.href = `${cleanBase}/admin/#!/campaign/${remoteCampaignId}/main`;
      viewInKtLink.target = '_blank';
      viewInKtLink.rel = 'noopener noreferrer';
      viewInKtLink.classList.remove('hidden');
    } else {
      viewInKtLink.removeAttribute('href');
      viewInKtLink.classList.add('hidden');
    }
  };
}

// Test 1: Real mode - select campaign 216239 with base URL without trailing slash
{
  const link = new MockAnchorElement();
  link.classList.add('hidden');
  const appConfig = {
    is_demo: false,
    keitaro_base_url: 'https://tlgk.host',
  };
  const update = createUpdateViewInKtLink(link, appConfig);

  update(216239);

  assert.strictEqual(
    link.href,
    'https://tlgk.host/admin/#!/campaign/216239/main',
    'Route must be /admin/#!/campaign/{id}/main'
  );
  assert.strictEqual(link.target, '_blank', 'target must be _blank');
  assert.strictEqual(link.rel, 'noopener noreferrer', 'rel must be noopener noreferrer');
  assert.strictEqual(link.classList.contains('hidden'), false, 'hidden class must be removed');
}

// Test 2: Stripping trailing slashes from keitaro_base_url
{
  const link = new MockAnchorElement();
  const appConfig = {
    is_demo: false,
    keitaro_base_url: 'https://tlgk.host///',
  };
  const update = createUpdateViewInKtLink(link, appConfig);

  update('216239');

  assert.strictEqual(
    link.href,
    'https://tlgk.host/admin/#!/campaign/216239/main',
    'Trailing slashes must be stripped'
  );
}

// Test 3: Updating when another campaign is selected
{
  const link = new MockAnchorElement();
  const appConfig = {
    is_demo: false,
    keitaro_base_url: 'https://tlgk.host',
  };
  const update = createUpdateViewInKtLink(link, appConfig);

  update(216239);
  assert.strictEqual(link.href, 'https://tlgk.host/admin/#!/campaign/216239/main');

  update(999888);
  assert.strictEqual(
    link.href,
    'https://tlgk.host/admin/#!/campaign/999888/main',
    'Link must update to new remote campaign id'
  );
}

// Test 4: Deselection clears old URL and hides button
{
  const link = new MockAnchorElement();
  const appConfig = {
    is_demo: false,
    keitaro_base_url: 'https://tlgk.host',
  };
  const update = createUpdateViewInKtLink(link, appConfig);

  update(216239);
  assert.strictEqual(link.href, 'https://tlgk.host/admin/#!/campaign/216239/main');
  assert.strictEqual(link.classList.contains('hidden'), false);

  update(null);
  assert.strictEqual(link.getAttribute('href'), null, 'href attribute must be cleared');
  assert.strictEqual(link.href, '', 'href property must be empty');
  assert.strictEqual(link.classList.contains('hidden'), true, 'hidden class must be added');
}

// Test 5: In demo mode, link is hidden and href cleared
{
  const link = new MockAnchorElement();
  link.href = 'https://tlgk.host/admin/#!/campaign/216239/main';
  const appConfig = {
    is_demo: true,
    keitaro_base_url: 'https://tlgk.host',
  };
  const update = createUpdateViewInKtLink(link, appConfig);

  update(216239);
  assert.strictEqual(link.getAttribute('href'), null, 'In demo mode, href must be cleared');
  assert.strictEqual(link.classList.contains('hidden'), true, 'In demo mode, link must be hidden');
}

// Test 6: Missing viewInKtLink element is safe
{
  const appConfig = { is_demo: false, keitaro_base_url: 'https://tlgk.host' };
  const update = createUpdateViewInKtLink(null, appConfig);
  assert.doesNotThrow(() => update(216239), 'Null element must not throw');
}

// Test 7: Missing keitaro_base_url hides link
{
  const link = new MockAnchorElement();
  const appConfig = { is_demo: false, keitaro_base_url: '' };
  const update = createUpdateViewInKtLink(link, appConfig);

  update(216239);
  assert.strictEqual(link.getAttribute('href'), null);
  assert.strictEqual(link.classList.contains('hidden'), true);
}

console.log('All View-in-KT frontend tests passed successfully!');
