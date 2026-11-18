import assert from 'node:assert/strict';
import test from 'node:test';
import { buildAnalysisBatches } from '../src/utils/analysisBatches.js';

const limits = { maximum_request_files: 3, maximum_batch_bytes: 10 };

test('splits analysis selections by both request file count and source bytes', () => {
  const files = [
    { index: 0, size: 4 },
    { index: 1, size: 4 },
    { index: 2, size: 4 },
    { index: 3, size: 6 },
  ];

  assert.deepEqual(buildAnalysisBatches([0, 1, 2, 3], files, limits), [[0, 1], [2, 3]]);
});

test('allows an individual file at the request byte limit', () => {
  assert.deepEqual(
    buildAnalysisBatches([0], [{ index: 0, size: 10 }], limits),
    [[0]],
  );
});

test('rejects missing files and files larger than the request limit', () => {
  assert.throws(
    () => buildAnalysisBatches([1], [{ index: 0, size: 1 }], limits),
    /no longer available/,
  );
  assert.throws(
    () => buildAnalysisBatches([0], [{ index: 0, size: 11 }], limits),
    /exceeds the configured/,
  );
});
