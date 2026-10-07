const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '..', 'template.html'), 'utf8');
const script = template.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
new vm.Script(script); // Catch errors in every renderer, not only the extracted helpers.
const helpers = script.match(/\/\* SORT_HELPERS_START[^]*?\*\/([^]*?)\/\* SORT_HELPERS_END/)[1];
const ctx = vm.createContext({});
vm.runInContext(helpers, ctx);
const { parseSortValue, stableSortEntries } = ctx;
const ordered = (values, direction = 'asc') => Array.from(stableSortEntries(
  values.map((value, order) => ({ value, order })), direction), entry => entry.order);

assert.equal(parseSortValue('1조 2,500억'), 1.25e12);
assert.equal(parseSortValue('-1.2조'), -1.2e12);
assert.equal(parseSortValue('+3.25%p'), 3.25);
assert.equal(parseSortValue('−3.5일'), -3.5);
assert.equal(parseSortValue('D-0'), 0);
assert.equal(parseSortValue('D+3'), -3);
assert.equal(parseSortValue('2026-09-11 적용'), Date.parse('2026-09-11'));
assert.equal(parseSortValue('20260911'), Date.parse('2026-09-11'));
for (const missing of [null, undefined, '', '-', '—', 'PDF 대기', NaN, Infinity]) {
  assert.equal(parseSortValue(missing), null);
}

// Mixed units, negative amounts, and missing values must order numerically.
const amounts = ['2조', '-8억', '950억', null, '-1.1조', '1조 2,500억'];
assert.deepEqual(ordered(amounts), [4, 1, 2, 5, 0, 3]);
assert.deepEqual(ordered(amounts, 'desc'), [0, 5, 2, 1, 4, 3]);
assert.deepEqual(ordered(['10위', '2위', '100위', '-']), [1, 0, 2, 3]);
assert.deepEqual(ordered(['1.2%p', '-0.4%p', '0.05%p']), [1, 2, 0]);
assert.deepEqual(ordered(['12.3일', '2.4일', '0.3일']), [2, 1, 0]);
assert.deepEqual(ordered(['2027-01-02', '2026-12-31', '2026-02-01', '']), [2, 1, 0, 3]);
assert.deepEqual(ordered(['종목10', '종목2', '종목1']), [2, 1, 0]);

// Raw unrounded values distinguish cells that both render as '1억'.
assert.deepEqual(ordered([1.49, 1.01, 1.49, null]), [1, 0, 2, 3]);
assert.deepEqual(ordered([1.49, 1.01, 1.49, null], 'desc'), [0, 2, 1, 3]);
// Original order survives switching sort direction and retains missing-value ties.
const ties = [{ value: 1, order: 7 }, { value: null, order: 9 }, { value: 1, order: 3 }, { value: '', order: 8 }];
for (const direction of ['asc', 'desc']) {
  assert.deepEqual(Array.from(stableSortEntries(ties, direction), x => x.order), [3, 7, 8, 9]);
}
console.log('PASS: template JavaScript syntax and table sorting regression cases');
