import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { updateStandard } from '../src/services/standardsService.js';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('updateStandard sends the loaded Firestore document ID to the backend', async () => {
  let requestUrl;
  let requestOptions;
  globalThis.fetch = async (url, options) => {
    requestUrl = url;
    requestOptions = options;
    return new Response(JSON.stringify({ id: 'firestore-doc-123', enabled: false }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const updated = await updateStandard(
    'firestore-doc-123',
    {
      title: 'Never hardcode credentials',
      description: 'Keep credentials out of source code.',
      category: 'security',
      severity: 'critical',
      recommended_solution: 'Use an environment variable.',
      enabled: false,
    },
    { getIdToken: async () => 'test-token' },
  );

  assert.equal(requestUrl, '/api/standards/firestore-doc-123');
  assert.equal(requestOptions.method, 'PUT');
  assert.equal(JSON.parse(requestOptions.body).enabled, false);
  assert.equal(updated.id, 'firestore-doc-123');
  assert.equal(updated.enabled, false);
});
