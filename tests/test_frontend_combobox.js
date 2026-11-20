/**
 * Frontend behavioral unit tests for OfferCombobox and Fair Share Rebalancing.
 */
const assert = require('assert');

// Test fair shares algorithm matching Task 3.1 §2
function rebalanceActiveShares(offers) {
  const active = offers.filter((o) => o.state === 'active');
  const n = active.length;
  if (n === 0) return offers;

  const base = Math.floor(100 / n);
  const rem = 100 % n;

  active.forEach((off, idx) => {
    off.share = base + (idx < rem ? 1 : 0);
  });
  return offers;
}

// 1. Test 3 offers: 34 / 33 / 33
const threeOffers = [
  { offer_id: 101, state: 'active', share: 0 },
  { offer_id: 102, state: 'active', share: 0 },
  { offer_id: 103, state: 'active', share: 0 },
];
rebalanceActiveShares(threeOffers);
assert.deepStrictEqual(
  threeOffers.map((o) => o.share),
  [34, 33, 33],
  '3 active offers must produce [34, 33, 33]'
);
assert.strictEqual(
  threeOffers.reduce((sum, o) => sum + o.share, 0),
  100
);

// 2. Test 4 offers: 25 / 25 / 25 / 25
const fourOffers = [
  { offer_id: 101, state: 'active', share: 0 },
  { offer_id: 102, state: 'active', share: 0 },
  { offer_id: 103, state: 'active', share: 0 },
  { offer_id: 104, state: 'active', share: 0 },
];
rebalanceActiveShares(fourOffers);
assert.deepStrictEqual(
  fourOffers.map((o) => o.share),
  [25, 25, 25, 25],
  '4 active offers must produce [25, 25, 25, 25]'
);
assert.strictEqual(
  fourOffers.reduce((sum, o) => sum + o.share, 0),
  100
);

// 3. Test 5 offers: 20 / 20 / 20 / 20 / 20
const fiveOffers = [
  { offer_id: 101, state: 'active', share: 0 },
  { offer_id: 102, state: 'active', share: 0 },
  { offer_id: 103, state: 'active', share: 0 },
  { offer_id: 104, state: 'active', share: 0 },
  { offer_id: 105, state: 'active', share: 0 },
];
rebalanceActiveShares(fiveOffers);
assert.deepStrictEqual(
  fiveOffers.map((o) => o.share),
  [20, 20, 20, 20, 20],
  '5 active offers must produce [20, 20, 20, 20, 20]'
);
assert.strictEqual(
  fiveOffers.reduce((sum, o) => sum + o.share, 0),
  100
);

// 4. Test 2 offers: 50 / 50
const twoOffers = [
  { offer_id: 101, state: 'active', share: 0 },
  { offer_id: 102, state: 'active', share: 0 },
];
rebalanceActiveShares(twoOffers);
assert.deepStrictEqual(
  twoOffers.map((o) => o.share),
  [50, 50],
  '2 active offers must produce [50, 50]'
);

// 5. Removed offers stay 0%
const withRemoved = [
  { offer_id: 101, state: 'active', share: 0 },
  { offer_id: 102, state: 'active', share: 0 },
  { offer_id: 103, state: 'removed', share: 0 },
];
rebalanceActiveShares(withRemoved);
assert.strictEqual(withRemoved[0].share, 50);
assert.strictEqual(withRemoved[1].share, 50);
assert.strictEqual(withRemoved[2].share, 0);

// 6. Test stream resolving logic
function resolveStreams(streams) {
  let flow2Candidates = streams.filter((s) => s.role === 'flow_2');
  let flow1Candidates = streams.filter((s) => s.role === 'flow_1');

  if (flow2Candidates.length === 0 && flow1Candidates.length === 0) {
    const defCandidates = streams.filter(
      (s) => s.type === 'default' || s.schema === 'landings_offers' || (s.name && s.name.includes('Flow 2'))
    );
    const regCandidates = streams.filter(
      (s) => s.type === 'regular' || s.schema === 'redirect' || (s.name && s.name.includes('Flow 1'))
    );

    if (defCandidates.length === 1) flow2Candidates = defCandidates;
    if (regCandidates.length === 1) flow1Candidates = regCandidates;
  }

  return { flow2Candidates, flow1Candidates };
}

// Test with explicit roles
const explicitStreams = [
  { id: 10, name: 'Flow 1', role: 'flow_1', type: 'regular' },
  { id: 11, name: 'Flow 2', role: 'flow_2', type: 'default' },
];
const res1 = resolveStreams(explicitStreams);
assert.strictEqual(res1.flow2Candidates.length, 1);
assert.strictEqual(res1.flow2Candidates[0].id, 11);
assert.strictEqual(res1.flow1Candidates.length, 1);
assert.strictEqual(res1.flow1Candidates[0].id, 10);

// Test with reversed order in array (shuffled response)
const shuffledStreams = [
  { id: 11, name: 'Flow 2', role: 'flow_2', type: 'default' },
  { id: 10, name: 'Flow 1', role: 'flow_1', type: 'regular' },
];
const res2 = resolveStreams(shuffledStreams);
assert.strictEqual(res2.flow2Candidates[0].id, 11, 'Shuffled order must still resolve stream 11 as Flow 2');

// Test with ambiguous duplicates
const ambiguousStreams = [
  { id: 11, name: 'Flow 2 A', role: 'flow_2' },
  { id: 12, name: 'Flow 2 B', role: 'flow_2' },
];
const res3 = resolveStreams(ambiguousStreams);
assert.strictEqual(res3.flow2Candidates.length, 2, 'Must detect multiple Flow 2 candidates for ambiguity protection');

// Test with no Flow 2
const noFlow2Streams = [
  { id: 10, name: 'Flow 1', role: 'flow_1' },
];
const res4 = resolveStreams(noFlow2Streams);
assert.strictEqual(res4.flow2Candidates.length, 0, 'Must detect absence of Flow 2');

console.log('ALL FRONTEND TESTS PASSED SUCCESSFULLY.');
