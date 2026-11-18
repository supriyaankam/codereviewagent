import assert from 'node:assert/strict';
import test from 'node:test';
import { buildComparisonRows } from '../src/utils/codeComparison.js';

test('marks replaced lines and preserves unchanged line numbers', () => {
  const rows = buildComparisonRows('first\nold\nlast\n', 'first\nnew\nlast\n');

  assert.deepEqual(rows.map(({ kind }) => kind), ['unchanged', 'modified', 'unchanged']);
  assert.deepEqual(
    [rows[1].original, rows[1].proposed, rows[1].originalNumber, rows[1].proposedNumber],
    ['old', 'new', 2, 2],
  );
});

test('shows inserted and removed lines in their respective panes', () => {
  const rows = buildComparisonRows('keep\nremove\n', 'add\nkeep\n');

  assert.ok(rows.some((row) => row.kind === 'removed' && row.original === 'remove'));
  assert.ok(rows.some((row) => row.kind === 'added' && row.proposed === 'add'));
});

test('unchanged source has no added or removed rows', () => {
  const rows = buildComparisonRows('one\ntwo\n', 'one\ntwo\n');

  assert.equal(rows.length, 2);
  assert.ok(rows.every((row) => row.kind === 'unchanged'));
});

test('preserves source line numbers for an extracted fix region', () => {
  const rows = buildComparisonRows('before\n', 'after\n', 42);

  assert.equal(rows[0].originalNumber, 42);
  assert.equal(rows[0].proposedNumber, 42);
});
