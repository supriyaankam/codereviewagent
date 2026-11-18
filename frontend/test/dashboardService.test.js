import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { fetchDashboard } from '../src/services/dashboardService.js';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('fetches persisted dashboard reviews and quality scores for the signed-in user', async () => {
  const savedDashboard = {
    total_reviews: 2,
    total_issues: 3,
    average_quality_score: 86.5,
    quality_scored_reviews: 2,
    recent_reviews: [{ id: 'review-1', project_name: 'Saved project', issues_count: 2 }],
  };
  const requestedUrls = [];
  globalThis.fetch = async (url, options) => {
    requestedUrls.push(url);
    assert.equal(options.headers.Authorization, 'Bearer test-token');
    return new Response(JSON.stringify(savedDashboard), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  const user = { uid: 'user-1', getIdToken: async () => 'test-token' };

  const afterInitialLoad = await fetchDashboard(user);
  const afterRefresh = await fetchDashboard(user);

  assert.equal(requestedUrls.length, 2);
  assert.equal(requestedUrls[0], '/api/dashboard?user_id=user-1');
  assert.deepEqual(afterInitialLoad, savedDashboard);
  assert.deepEqual(afterRefresh.recent_reviews, savedDashboard.recent_reviews);
  assert.equal(afterRefresh.average_quality_score, 86.5);
});

test('surfaces Firestore configuration errors returned by the dashboard API', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: 'Configure FIREBASE_SERVICE_ACCOUNT_PATH in backend/.env.',
  }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' },
  });

  await assert.rejects(
    fetchDashboard({ uid: 'user-1', getIdToken: async () => 'test-token' }),
    /FIREBASE_SERVICE_ACCOUNT_PATH/,
  );
});
