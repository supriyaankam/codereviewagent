import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import {
  getReviewForPage,
  hydrateReviewSession,
} from '../src/services/reviewService.js';
import {
  attachAcceptedAutoFix,
  createReviewRequest,
  createEmptyReviewSession,
  findingIdentity,
  getReviewPageMode,
} from '../src/utils/reviewPageState.js';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('opening Code Review initializes an empty editor with no displayed results', () => {
  const session = createEmptyReviewSession();

  assert.deepEqual(session.form, { project: '', language: '', code: '' });
  assert.equal(session.result, null);
  assert.equal(session.reviewedCode, '');
  assert.equal(session.sourceChanged, false);
  assert.equal(session.error, '');
});

test('a fresh Code Review route does not fetch or restore a previous review', async () => {
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    throw new Error('Fresh review must not request saved review data.');
  };

  const mode = getReviewPageMode(new URLSearchParams());
  const savedReview = await getReviewForPage(
    mode.type === 'saved' ? mode.reviewId : null,
    { getIdToken: async () => 'test-token' },
  );

  assert.deepEqual(mode, { type: 'new' });
  assert.equal(savedReview, null);
  assert.equal(fetchCount, 0);
});

test('clicking a Review History item restores the exact selected review', async () => {
  let requestedUrl;
  globalThis.fetch = async (url) => {
    requestedUrl = url;
    return new Response(JSON.stringify({
      id: 'history-review-42',
      project_name: 'selected-project',
      language: 'Python',
      code: 'print("selected")',
      summary: 'Selected review summary.',
      ai_issues: [{ title: 'Selected finding', line: 1 }],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const mode = getReviewPageMode(
    new URLSearchParams('review_id=history-review-42'),
  );
  const saved = await getReviewForPage(
    mode.type === 'saved' ? mode.reviewId : null,
    { getIdToken: async () => 'test-token' },
  );
  const restored = hydrateReviewSession(saved);

  assert.equal(requestedUrl, '/api/reviews/history-review-42');
  assert.equal(restored.form.project, 'selected-project');
  assert.equal(restored.form.language, 'Python');
  assert.equal(restored.form.code, 'print("selected")');
  assert.equal(restored.result.summary, 'Selected review summary.');
  assert.deepEqual(restored.result.ai_issues, [{ title: 'Selected finding', line: 1 }]);
});

test('New Review clears the previous editor, result, and source state', () => {
  const previousSession = {
    form: { project: 'old-project', language: 'Python', code: 'old()' },
    reviewedCode: 'old()',
    result: { review_id: 'old-review' },
    sourceChanged: true,
    error: 'Old error',
  };
  const newSession = createEmptyReviewSession();

  assert.notDeepEqual(newSession, previousSession);
  assert.deepEqual(newSession.form, { project: '', language: '', code: '' });
  assert.equal(newSession.result, null);
  assert.equal(newSession.reviewedCode, '');
  assert.equal(newSession.sourceChanged, false);
  assert.equal(newSession.error, '');
});

test('accepted Auto-Fix submits updated source and retains only fresh findings', () => {
  const updatedCode = 'def parse(value=None):\n    return value\n';
  const request = createReviewRequest({
    userId: 'user-1',
    projectName: '  parser  ',
    language: 'Python',
    code: updatedCode,
  });
  const resolved = {
    title: 'Mutable default argument',
    category: 'quality',
    line: 1,
  };
  const stillPresent = {
    title: 'Use of eval()',
    category: 'security',
    line: 3,
    source: 'ast',
  };
  const freshReview = {
    review_id: 'new-review',
    code: updatedCode,
    ai_issues: [],
    static_issues: [stillPresent],
  };
  const acceptedFix = {
    original_code: 'def parse(value=[]):\n    return eval(value)\n',
    fixed_code: updatedCode,
    accepted: true,
  };
  const result = attachAcceptedAutoFix(
    freshReview,
    acceptedFix,
    [resolved, stillPresent],
  );

  assert.equal(request.code, updatedCode);
  assert.equal(request.project_name, 'parser');
  assert.equal(result.review_id, 'new-review');
  assert.deepEqual(result.static_issues, [stillPresent]);
  assert.equal(result.auto_fix.original_code, acceptedFix.original_code);
  assert.equal(result.auto_fix.fixed_code, updatedCode);
  assert.deepEqual(result.auto_fix.reanalysis.issues, [stillPresent]);
  assert.deepEqual(
    result.auto_fix.ineffective_finding_keys,
    [findingIdentity(stillPresent)],
  );
});
