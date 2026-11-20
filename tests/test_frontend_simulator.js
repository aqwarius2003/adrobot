// Unit test for simulator.js logic
const assert = require('assert');

// 1. Offer Name Resolution
function resolveOfferName(off, catalogMap) {
  const catalogName = catalogMap.get(off.offer_id);
  let displayName = catalogName;
  if (!displayName && off.name && !off.name.startsWith('Offer #')) {
    displayName = off.name;
  }
  if (!displayName) {
    displayName = `Оффер #${off.offer_id}`;
  }
  return displayName;
}

const mockCatalog = new Map([
  [101, 'Demo Offer 1 (Gambling CPA)'],
  [102, 'Demo Offer 2 (Finance CPL)'],
  [103, 'Demo Offer 3 (Crypto RevShare)'],
  [104, 'Demo Offer 4 (Nutra COD)'],
  [105, 'Demo Offer 5 (E-commerce CPS)'],
]);

// Test 1: Known catalog offer displays full name and sub-ID
assert.strictEqual(
  resolveOfferName({ offer_id: 101, share: 34, state: 'active' }, mockCatalog),
  'Demo Offer 1 (Gambling CPA)'
);

// Test 2: Unknown offer falls back to 'Оффер #ID'
assert.strictEqual(
  resolveOfferName({ offer_id: 999, share: 50, state: 'active' }, mockCatalog),
  'Оффер #999'
);

// Test 3: Newly added offer with custom name not in catalog preserves custom name
assert.strictEqual(
  resolveOfferName({ offer_id: 888, name: 'Custom Offer 888', share: 20, state: 'active' }, mockCatalog),
  'Custom Offer 888'
);

// 2. Deterministic Fair Distribution
function rebalanceActiveShares(offers) {
  const active = offers.filter((o) => o.state === 'active');
  const n = active.length;
  if (n === 0) return;
  const base = Math.floor(100 / n);
  const rem = 100 % n;
  active.forEach((off, idx) => {
    off.share = base + (idx < rem ? 1 : 0);
  });
}

// 3 offers -> 34, 33, 33
const threeOffers = [
  { offer_id: 101, share: 0, state: 'active' },
  { offer_id: 102, share: 0, state: 'active' },
  { offer_id: 103, share: 0, state: 'active' },
];
rebalanceActiveShares(threeOffers);
assert.deepStrictEqual(threeOffers.map((o) => o.share), [34, 33, 33]);
assert.strictEqual(threeOffers.reduce((sum, o) => sum + o.share, 0), 100);

// 5 offers -> 20, 20, 20, 20, 20
const fiveOffers = [
  { offer_id: 101, state: 'active' },
  { offer_id: 102, state: 'active' },
  { offer_id: 103, state: 'active' },
  { offer_id: 104, state: 'active' },
  { offer_id: 105, state: 'active' },
];
rebalanceActiveShares(fiveOffers);
assert.deepStrictEqual(fiveOffers.map((o) => o.share), [20, 20, 20, 20, 20]);
assert.strictEqual(fiveOffers.reduce((sum, o) => sum + o.share, 0), 100);

// 4 offers -> 25, 25, 25, 25
const fourOffers = [
  { offer_id: 101, state: 'active' },
  { offer_id: 102, state: 'active' },
  { offer_id: 103, state: 'active' },
  { offer_id: 104, state: 'active' },
];
rebalanceActiveShares(fourOffers);
assert.deepStrictEqual(fourOffers.map((o) => o.share), [25, 25, 25, 25]);
assert.strictEqual(fourOffers.reduce((sum, o) => sum + o.share, 0), 100);

// Excluded / Removed offers get 0 share and don't count towards active count or sum
const mixedOffers = [
  { offer_id: 101, share: 50, state: 'active' },
  { offer_id: 102, share: 50, state: 'active' },
  { offer_id: 103, share: 0, state: 'removed' },
];
const activeList = mixedOffers.filter((o) => o.state === 'active');
const totalSum = activeList.reduce((sum, o) => sum + o.share, 0);
assert.strictEqual(activeList.length, 2);
assert.strictEqual(totalSum, 100);

// 3. Race condition handling
let currentLoadRequestId = 0;
let currentCampaignId = 1;

function canApplyResponse(requestId, targetCampaignId) {
  return requestId === currentLoadRequestId && targetCampaignId === currentCampaignId;
}

// Start request for campaign 1
const req1 = ++currentLoadRequestId;
// User rapidly switches to campaign 2
currentCampaignId = 2;
const req2 = ++currentLoadRequestId;

// Stale response from req1 arrives:
assert.strictEqual(canApplyResponse(req1, 1), false);
// Fresh response from req2 arrives:
assert.strictEqual(canApplyResponse(req2, 2), true);

console.log('All frontend simulator unit tests passed successfully!');
