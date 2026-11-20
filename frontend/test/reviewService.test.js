import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import {
  getLatestReview,
  getReviewHistoryPage,
  getReview,
  getReviewDetailPath,
  getSavedIssueCount,
  hydrateReviewSession,
  deleteReview,
  saveReviewAutoFix,
  submitReview,
} from '../src/services/reviewService.js';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('deleteReview sends an authenticated DELETE request for the selected review', async () => {
  let requestUrl;
  let requestOptions;
  globalThis.fetch = async (url, options) => {
    requestUrl = url;
    requestOptions = options;
    return new Response(null, { status: 204 });
  };

  await deleteReview('review/id 1', {
    getIdToken: async () => 'test-token',
  });

  assert.equal(requestUrl, '/api/reviews/review%2Fid%201');
  assert.equal(requestOptions.method, 'DELETE');
  assert.equal(requestOptions.headers.Authorization, 'Bearer test-token');
});

test('deleteReview reports backend errors and requires a review ID', async () => {
  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: 'Review not found.' }),
    { status: 404, headers: { 'Content-Type': 'application/json' } },
  );

  await assert.rejects(
    deleteReview('missing-review', { getIdToken: async () => 'test-token' }),
    /Review not found/,
  );
  await assert.rejects(
    deleteReview('', { getIdToken: async () => 'test-token' }),
    /Review ID is required/,
  );
});

test('getReviewHistoryPage requests the authenticated user page with an optional cursor', async () => {
  let requestUrl;
  let requestOptions;
  globalThis.fetch = async (url, options) => {
    requestUrl = url;
    requestOptions = options;
    return new Response(JSON.stringify({
      reviews: [{ id: 'review-1' }],
      next_cursor: 'cursor-2',
      total_count: 11,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const page = await getReviewHistoryPage({
    uid: 'user 1',
    getIdToken: async () => 'history-token',
  }, 'cursor/2');

  assert.equal(
    requestUrl,
    '/api/reviews/page?user_id=user+1&cursor=cursor%2F2',
  );
  assert.equal(requestOptions.headers.Authorization, 'Bearer history-token');
  assert.deepEqual(page.reviews, [{ id: 'review-1' }]);
  assert.equal(page.total_count, 11);
});

test('getReviewHistoryPage omits a missing cursor and reports invalid cursor status', async () => {
  let requestUrl;
  globalThis.fetch = async (url) => {
    requestUrl = url;
    return new Response(JSON.stringify({
      reviews: [],
      next_cursor: null,
      total_count: 0,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  await getReviewHistoryPage({
    uid: 'user-1',
    getIdToken: async () => 'history-token',
  });
  assert.equal(requestUrl, '/api/reviews/page?user_id=user-1');

  globalThis.fetch = async () => new Response(
    JSON.stringify({ detail: 'Invalid review cursor.' }),
    { status: 400, headers: { 'Content-Type': 'application/json' } },
  );
  await assert.rejects(
    getReviewHistoryPage(
      { uid: 'user-1', getIdToken: async () => 'history-token' },
      'invalid',
    ),
    (error) => error.message === 'Invalid review cursor.' && error.status === 400,
  );
});

test('getReviewHistoryPage rejects missing users and malformed response envelopes', async () => {
  await assert.rejects(getReviewHistoryPage(null), /Sign in/);
  await assert.rejects(
    getReviewHistoryPage({ getIdToken: async () => 'history-token' }),
    /Sign in/,
  );
  globalThis.fetch = async () => new Response(JSON.stringify({
    reviews: [],
    next_cursor: null,
    total_count: '0',
  }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
  await assert.rejects(
    getReviewHistoryPage({
      uid: 'user-1',
      getIdToken: async () => 'history-token',
    }),
    /response was invalid/,
  );
});

test('accepts a review only after the API confirms Firestore persistence', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/review');
    assert.equal(options.method, 'POST');
    assert.equal(JSON.parse(options.body).user_id, 'user-1');
    return new Response(JSON.stringify({
      review_id: 'firestore-review-1',
      saved_to_db: true,
      quality_score: { score: 90 },
    }), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const review = await submitReview(
    { user_id: 'user-1', code: 'value = 1' },
    { getIdToken: async () => 'test-token' },
  );

  assert.equal(review.review_id, 'firestore-review-1');
  assert.equal(review.saved_to_db, true);
});

test('rejects a successful-looking response that was not saved to Firestore', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    review_id: 'local',
    saved_to_db: false,
  }), {
    status: 201,
    headers: { 'Content-Type': 'application/json' },
  });

  await assert.rejects(
    submitReview({ user_id: 'user-1' }, { getIdToken: async () => 'test-token' }),
    /not saved to Firestore/,
  );
});

test('shows persistence errors returned by the review API', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: 'The review could not be saved to Firestore.',
  }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' },
  });

  await assert.rejects(
    submitReview({ user_id: 'user-1' }, { getIdToken: async () => 'test-token' }),
    /could not be saved to Firestore/,
  );
});

test('preserves language validation responses for matched, mismatched, ambiguous, empty, and unsupported code', async () => {
  const cases = [
    {
      status: 201,
      body: {
        review_id: 'javascript-review',
        saved_to_db: true,
        detected_language: 'javascript',
      },
      request: { language: 'JavaScript', code: 'console.log("hi");' },
      expectedLanguage: 'javascript',
    },
    {
      status: 201,
      body: {
        review_id: 'c-review',
        saved_to_db: true,
        detected_language: 'c',
      },
      request: { language: 'C', code: 'int main() { return 0; }' },
      expectedLanguage: 'c',
    },
    {
      status: 422,
      body: {
        detail: {
          code: 'language_mismatch',
          message: 'Selected JavaScript, detected C.',
          selected_language: 'JavaScript',
          detected_language: 'c',
          candidates: ['c'],
        },
      },
      request: { language: 'JavaScript', code: 'int main(void) {}' },
      expectedCode: 'language_mismatch',
      expectedDetected: 'c',
    },
    {
      status: 422,
      body: {
        detail: {
          code: 'language_mismatch',
          message: 'Selected Python, detected Java.',
          selected_language: 'Python',
          detected_language: 'java',
          candidates: ['java'],
        },
      },
      request: { language: 'Python', code: 'public class Main {}' },
      expectedCode: 'language_mismatch',
      expectedDetected: 'java',
    },
    {
      status: 409,
      body: {
        detail: {
          code: 'language_ambiguous',
          message: 'The source is compatible with JavaScript and TypeScript.',
          selected_language: 'JavaScript',
          detected_language: null,
          candidates: ['javascript', 'typescript'],
        },
      },
      request: { language: 'JavaScript', code: 'const count: number = 1;' },
      expectedCode: 'language_ambiguous',
      expectedDetected: null,
    },
    {
      status: 422,
      body: {
        detail: {
          code: 'empty_code',
          message: 'Code must not be empty.',
          selected_language: 'JavaScript',
          detected_language: null,
          candidates: [],
        },
      },
      request: { language: 'JavaScript', code: '   ' },
      expectedCode: 'empty_code',
      expectedDetected: null,
    },
    {
      status: 422,
      body: {
        detail: {
          code: 'unsupported_language',
          message: 'Unsupported language selection.',
          selected_language: 'Fortran',
          detected_language: null,
          candidates: [],
        },
      },
      request: { language: 'Fortran', code: 'program hello' },
      expectedCode: 'unsupported_language',
      expectedDetected: null,
    },
  ];

  for (const testCase of cases) {
    globalThis.fetch = async (_url, options) => {
      assert.equal(JSON.parse(options.body).language, testCase.request.language);
      return new Response(JSON.stringify(testCase.body), {
        status: testCase.status,
        headers: { 'Content-Type': 'application/json' },
      });
    };

    if (testCase.expectedLanguage) {
      const response = await submitReview(
        { user_id: 'user-1', ...testCase.request },
        { getIdToken: async () => 'test-token' },
      );
      assert.equal(response.detected_language, testCase.expectedLanguage);
      continue;
    }

    await assert.rejects(
      submitReview(
        { user_id: 'user-1', ...testCase.request },
        { getIdToken: async () => 'test-token' },
      ),
      (error) => {
        assert.equal(error.languageValidation.code, testCase.expectedCode);
        assert.equal(
          error.languageValidation.selected_language,
          testCase.request.language,
        );
        assert.equal(
          error.languageValidation.detected_language,
          testCase.expectedDetected,
        );
        return true;
      },
    );
  }
});

test('loads the latest review using authenticated user credentials', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/reviews/latest');
    assert.match(options.headers.Authorization, /^Bearer /);
    return new Response(JSON.stringify({ id: 'review-2', code: 'return 2' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const review = await getLatestReview({ getIdToken: async () => 'test-token' });
  assert.equal(review.id, 'review-2');
});

test('loads a selected history review and restores the entire session shape', async () => {
  globalThis.fetch = async (url) => {
    assert.equal(url, '/api/reviews/review%2F3');
    return new Response(JSON.stringify({
      id: 'review/3',
      project_name: 'payments',
      language: 'python',
      code: 'password = "placeholder"',
      overall_risk: 'high',
      summary: 'The original AI summary.',
      issues_count: 2,
      warning_count: 1,
      ai_available: true,
      static_supported: true,
      ai_issues: [{
        title: 'Hardcoded credential',
        severity: 'critical',
        line: 7,
        recommendation: 'Use an environment variable.',
      }],
      static_issues: [{
        title: 'Weak password handling',
        severity: 'warning',
        line: 9,
        recommendation: 'Use a secret store.',
      }],
      quality_score: { score: 84 },
      feedback: [{ issue_id: 'finding:1:password', decision: 'accepted' }],
      auto_fix: { fixed_code: 'safe = True', accepted: true },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const saved = await getReview('review/3', { getIdToken: async () => 'test-token' });
  const restored = hydrateReviewSession(saved);
  assert.deepEqual(restored.form, {
    project: 'payments',
    language: 'python',
    code: 'password = "placeholder"',
  });
  assert.equal(restored.reviewedCode, saved.code);
  assert.equal(restored.result.review_id, 'review/3');
  assert.equal(restored.result.overall_risk, 'high');
  assert.equal(restored.result.summary, 'The original AI summary.');
  assert.equal(restored.result.issues_count, 2);
  assert.equal(restored.result.warning_count, 1);
  assert.equal(restored.result.ai_available, true);
  assert.equal(restored.result.static_supported, true);
  assert.deepEqual(restored.result.ai_issues, saved.ai_issues);
  assert.deepEqual(restored.result.static_issues, saved.static_issues);
  assert.deepEqual(restored.result.quality_score, saved.quality_score);
  assert.deepEqual(restored.result.feedback, saved.feedback);
  assert.deepEqual(restored.result.auto_fix, saved.auto_fix);
  assert.equal(restored.result.saved_to_db, true);
});

test('history navigation preserves full IDs and missing counts remain unavailable', () => {
  const reviewIds = ['review-alpha-2026', 'review/beta-2026', 'review-gamma-2026'];

  for (const reviewId of reviewIds) {
    assert.equal(
      getReviewDetailPath(reviewId),
      `/review?review_id=${encodeURIComponent(reviewId)}`,
    );
  }
  assert.equal(getSavedIssueCount({}), null);
  assert.equal(getSavedIssueCount({ issues_count: 0 }), 0);
  assert.equal(getSavedIssueCount({ issues_count: 4 }), 4);
});

test('restores five selected reviews independently using each complete ID', async () => {
  const records = new Map([
    ['review-alpha-2026', {
      id: 'review-alpha-2026',
      project_name: 'alpha',
      language: 'Python',
      code: 'alpha()\n',
      summary: 'Alpha summary.',
      overall_risk: 'high',
      quality_score: { score: 91 },
      issues_count: 2,
      ai_issues: [{ title: 'Alpha AI', line: 1 }],
      static_issues: [{ title: 'Alpha static', line: 2 }],
    }],
    ['review/beta-2026', {
      id: 'review/beta-2026',
      project: 'beta',
      programming_language: 'JavaScript',
      source_code: 'beta()\n',
      risk_level: 'medium',
      ai_result: {
        summary: 'Beta summary.',
        available: true,
        issues: [{ title: 'Beta AI', line_number: 3, source: 'ai' }],
      },
      static_result: {
        issues: [{ title: 'Beta static', line_number: 4, source: 'eslint' }],
      },
      issue_count: 2,
    }],
    ['review-gamma-2026', {
      id: 'review-gamma-2026',
      project_name: 'gamma',
      language: 'Go',
      original_code: 'gamma()\n',
      summary: 'Gamma summary.',
      overall_risk: 'low',
      ai_issues: [{ title: 'Gamma AI', line: 5 }],
    }],
    ['review-delta-2026', {
      id: 'review-delta-2026',
      project_name: 'delta',
      language: 'Rust',
      source_code: 'fn delta() {}\n',
      overall_risk: 'medium',
      summary: 'AI review unavailable — timeout.',
      ai_available: false,
      ai_failure_reason: 'Groq request timed out.',
      ai_failure_category: 'timeout',
      total_issue_count: 1,
      warning_count: 1,
      static_supported: true,
      static_analyzer_metadata: { language: 'rust', supported: true },
      static_summary: { counts: { total: 1, warning: 1 } },
      ai_issues: [],
      static_issues: [{
        title: 'Delta static',
        description: 'Delta finding details.',
        recommendation: 'Apply the delta fix.',
        severity: 'warning',
        line: 7,
        source: 'clippy',
      }],
    }],
    ['review-epsilon-2026', {
      id: 'review-epsilon-2026',
      project_name: 'epsilon',
      language: 'TypeScript',
      code: 'const epsilon = true;\n',
      overall_risk: 'info',
      ai_summary: 'Epsilon has no findings.',
      ai_available: true,
      quality_score: { score: 100 },
      total_issue_count: 0,
      critical_count: 0,
      warning_count: 0,
      info_count: 0,
      static_supported: false,
      static_issues: [],
      ai_issues: [],
    }],
  ]);
  const requestedUrls = [];

  globalThis.fetch = async (url) => {
    requestedUrls.push(url);
    const record = [...records.values()].find(
      ({ id }) => url === `/api/reviews/${encodeURIComponent(id)}`,
    );
    assert.ok(record, `unexpected review request: ${url}`);
    return new Response(JSON.stringify(record), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const restoredById = new Map();
  for (const reviewId of records.keys()) {
    const saved = await getReview(reviewId, { getIdToken: async () => 'test-token' });
    restoredById.set(reviewId, hydrateReviewSession(saved));
  }

  assert.deepEqual(requestedUrls, [...records.keys()].map(
    (reviewId) => `/api/reviews/${encodeURIComponent(reviewId)}`,
  ));
  assert.equal(restoredById.get('review-alpha-2026').form.code, 'alpha()\n');
  assert.equal(restoredById.get('review-alpha-2026').result.summary, 'Alpha summary.');
  assert.deepEqual(
    restoredById.get('review-alpha-2026').result.static_issues.map(({ title }) => title),
    ['Alpha static'],
  );
  assert.equal(restoredById.get('review/beta-2026').form.project, 'beta');
  assert.equal(restoredById.get('review/beta-2026').result.summary, 'Beta summary.');
  assert.deepEqual(
    restoredById.get('review/beta-2026').result.ai_issues.map(({ title }) => title),
    ['Beta AI'],
  );
  assert.deepEqual(
    restoredById.get('review/beta-2026').result.static_issues.map(({ title }) => title),
    ['Beta static'],
  );
  assert.equal(restoredById.get('review-gamma-2026').form.code, 'gamma()\n');
  assert.equal(restoredById.get('review-gamma-2026').result.quality_score, undefined);
  assert.deepEqual(
    restoredById.get('review-gamma-2026').result.ai_issues.map(({ title }) => title),
    ['Gamma AI'],
  );
  assert.equal(restoredById.get('review-gamma-2026').result.static_issues, undefined);
  assert.equal(restoredById.get('review-delta-2026').result.ai_available, false);
  assert.equal(restoredById.get('review-delta-2026').result.summary, undefined);
  assert.equal(
    restoredById.get('review-delta-2026').result.ai_failure_category,
    'timeout',
  );
  assert.deepEqual(
    restoredById.get('review-delta-2026').result.static_issues.map(({ title }) => title),
    ['Delta static'],
  );
  assert.equal(
    restoredById.get('review-delta-2026').result.total_issue_count,
    1,
  );
  assert.equal(
    restoredById.get('review-epsilon-2026').result.summary,
    'Epsilon has no findings.',
  );
  assert.equal(restoredById.get('review-epsilon-2026').result.quality_score.score, 100);
});

test('surfaces a missing saved review instead of starting another analysis', async () => {
  globalThis.fetch = async (url) => {
    assert.equal(url, '/api/reviews/missing-review');
    return new Response(JSON.stringify({ detail: 'Review not found.' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await assert.rejects(
    getReview('missing-review', { getIdToken: async () => 'test-token' }),
    /Review not found/,
  );
});

test('hydrates legacy nested review data without rerunning analysis or inventing availability', () => {
  const restored = hydrateReviewSession({
    id: 'legacy-review',
    project: 'test',
    programming_language: 'Python',
    source_code: 'api_key = "original"\n',
    risk_level: 'high',
    ai_result: {
      summary: 'The original AI summary.',
      available: true,
      issues: [{
        title: 'Hardcoded API key',
        severity: 'critical',
        line_number: 1,
        description: 'A credential is stored in source.',
        suggested_fix: 'Use an environment variable.',
        source: 'ai',
      }],
    },
    static_result: {
      supported: true,
      issues: [{
        name: 'Secret assignment',
        severity: 'warning',
        line_number: 1,
        message: 'A sensitive value is assigned directly.',
        recommended_solution: 'Use secret management.',
        source: 'bandit',
      }],
    },
    code_quality_score: { score: 61, categories: { security: 40 } },
    issue_count: 2,
    warning_issues: 1,
    auto_fix_result: { fixed_code: 'api_key = load_secret()\n', accepted: true },
  });

  assert.deepEqual(restored.form, {
    project: 'test',
    language: 'Python',
    code: 'api_key = "original"\n',
  });
  assert.equal(restored.result.overall_risk, 'high');
  assert.equal(restored.result.summary, 'The original AI summary.');
  assert.equal(restored.result.quality_score.score, 61);
  assert.equal(restored.result.issues_count, 2);
  assert.equal(restored.result.warning_count, 1);
  assert.equal(restored.result.ai_available, true);
  assert.equal(restored.result.static_supported, true);
  assert.equal(restored.result.ai_issues[0].line, 1);
  assert.equal(restored.result.ai_issues[0].recommendation, 'Use an environment variable.');
  assert.equal(restored.result.static_issues[0].line, 1);
  assert.equal(restored.result.static_issues[0].recommendation, 'Use secret management.');
  assert.equal(restored.result.auto_fix.accepted, true);
  assert.deepEqual(restored.result.restorationUnavailable, [
    'critical issue count',
    'info issue count',
  ]);
});

test('retains available source and findings when old review metadata is incomplete', () => {
  const restored = hydrateReviewSession({
    id: 'partial-review',
    project_name: 'test',
    language: 'Python',
    code: 'value = 1\n',
    ai_findings: [{
      title: 'Saved finding',
      severity: 'warning',
      line_number: 1,
      recommended_solution: 'Validate the value.',
    }],
  });

  assert.deepEqual(restored.form, {
    project: 'test',
    language: 'Python',
    code: 'value = 1\n',
  });
  assert.equal(restored.result.ai_issues[0].title, 'Saved finding');
  assert.equal(restored.result.ai_issues[0].line, 1);
  assert.ok(restored.result.restorationUnavailable.includes('quality score'));
  assert.ok(restored.result.restorationUnavailable.includes('AI availability metadata'));
  assert.equal(restored.result.ai_available, undefined);
  assert.equal(restored.result.static_supported, undefined);
  assert.equal(restored.result.static_issues, undefined);
});

test('restores findings from the original committed Firestore review shape', () => {
  const restored = hydrateReviewSession({
    id: 'legacy-firestore-review',
    author_id: 'user-1',
    project_id: 'global',
    project_name: 'test',
    language: 'Python',
    code: "password = 'original'\n",
    status: 'completed',
    overall_risk: 'high',
    summary: 'Original saved AI summary.',
    issues_count: 2,
    critical_count: 1,
    warning_count: 1,
    info_count: 0,
    ai_issues: [{
      title: 'Hardcoded password',
      severity: 'critical',
      line: 1,
      explanation: 'A literal password appears in source.',
      recommendation: 'Load it from an environment variable.',
      source: 'ai',
    }],
    static_issues: [{
      title: 'Weak credential handling',
      severity: 'warning',
      line: 1,
      description: 'A credential is assigned directly.',
      recommendation: 'Use a secrets manager.',
      source: 'bandit',
    }],
    ai_available: true,
    auto_fix: { fixed_code: 'password = read_secret()\n', accepted: true },
  });

  assert.equal(restored.form.project, 'test');
  assert.equal(restored.form.language, 'Python');
  assert.equal(restored.form.code, "password = 'original'\n");
  assert.equal(restored.result.overall_risk, 'high');
  assert.equal(restored.result.summary, 'Original saved AI summary.');
  assert.equal(restored.result.quality_score, undefined);
  assert.equal(restored.result.static_supported, undefined);
  assert.equal(restored.result.ai_available, true);
  assert.equal(restored.result.ai_issues[0].recommendation, 'Load it from an environment variable.');
  assert.equal(restored.result.static_issues[0].recommendation, 'Use a secrets manager.');
  assert.ok(restored.result.restorationUnavailable.includes('quality score'));
  assert.ok(restored.result.restorationUnavailable.includes('static analyzer metadata'));
});

test('preserves info-only historical findings and reports unsaved metadata as unavailable', () => {
  const restored = hydrateReviewSession({
    id: 'legacy-info-review',
    project_name: 'legacy',
    language: 'Python',
    code: 'print("one")\nprint("two")\n',
    overall_risk: 'info',
    summary: 'Saved historical summary.',
    ai_available: false,
    ai_issues: [
      { title: 'print() call detected', severity: 'info', line: 1, source: 'ast' },
      { title: 'print() call detected', severity: 'info', line: 2, source: 'ast' },
    ],
    static_issues: [
      { title: 'print() call detected', severity: 'info', line: 1, source: 'ast' },
      { title: 'print() call detected', severity: 'info', line: 2, source: 'ast' },
    ],
  });

  assert.equal(restored.result.ai_available, false);
  assert.equal(restored.result.quality_score, undefined);
  assert.equal(restored.result.static_supported, undefined);
  assert.equal(restored.result.ai_issues.length, 2);
  assert.equal(restored.result.static_issues.length, 2);
  assert.ok(restored.result.restorationUnavailable.includes('quality score'));
  assert.ok(restored.result.restorationUnavailable.includes('static analyzer metadata'));
});

test('restores hbSSyNCA-style historical review with five separate static findings', () => {
  const saved = {
    id: 'hbSSyNCA',
    project_name: 'test',
    language: 'Python',
    code: Array.from({ length: 48 }, (_, index) => `value_${index + 1} = ${index + 1}`).join('\n'),
    status: 'completed',
    overall_risk: 'medium',
    summary: 'Original saved summary.',
    issues_count: 5,
    critical_count: 0,
    warning_count: 5,
    info_count: 0,
    ai_available: true,
    ai_issues: [],
    static_issues: Array.from({ length: 5 }, (_, index) => ({
      id: `static-${index + 1}`,
      title: `Static finding ${index + 1}`,
      description: `Saved description ${index + 1}.`,
      recommendation: `Saved suggested fix ${index + 1}.`,
      severity: 'warning',
      line: (index + 1) * 3,
      source: 'static',
    })),
    auto_fix: { fixed_code: 'already saved', accepted: false },
  };
  const restored = hydrateReviewSession(saved);

  assert.equal(restored.result.review_id, 'hbSSyNCA');
  assert.equal(restored.form.code.split('\n').length, 48);
  assert.equal(restored.result.summary, 'Original saved summary.');
  assert.equal(restored.result.issues_count, 5);
  assert.equal(restored.result.static_issues.length, 5);
  assert.deepEqual(restored.result.static_issues.map(issue => issue.line), [3, 6, 9, 12, 15]);
  assert.deepEqual(
    restored.result.static_issues.map(issue => issue.recommendation),
    Array.from({ length: 5 }, (_, index) => `Saved suggested fix ${index + 1}.`),
  );
  assert.equal(restored.result.quality_score, undefined);
  assert.equal(restored.result.static_supported, undefined);
  assert.equal(restored.result.ai_available, true);
  assert.deepEqual(restored.result.auto_fix, saved.auto_fix);
  assert.ok(restored.result.restorationUnavailable.includes('quality score'));
  assert.ok(restored.result.restorationUnavailable.includes('static analyzer metadata'));
});

test('restores saved static analyzer metadata without warning', () => {
  const staticMetadata = {
    language: 'python',
    supported: true,
    has_syntax_error: false,
    summary: {
      counts: { total: 0, critical: 0, error: 0, warning: 0, info: 0 },
      by_category: {},
      by_source: {},
    },
  };
  const restored = hydrateReviewSession({
    id: 'review-with-static-metadata',
    project_name: 'metadata-project',
    language: 'Python',
    code: 'value = 1',
    overall_risk: 'low',
    summary: 'Review complete.',
    quality_score: { score: 100 },
    issues_count: 0,
    critical_count: 0,
    warning_count: 0,
    info_count: 0,
    ai_available: true,
    ai_issues: [],
    static_issues: [],
    static_metadata: staticMetadata,
  });

  assert.deepEqual(restored.result.static_metadata, staticMetadata);
  assert.equal(restored.result.static_supported, true);
  assert.ok(!restored.result.restorationUnavailable.includes('static analyzer metadata'));
});

test('does not invent static analyzer metadata for a legacy review', () => {
  const restored = hydrateReviewSession({
    id: 'legacy-review-without-static-metadata',
    project_name: 'legacy-project',
    language: 'Python',
    code: 'value = 1',
    overall_risk: 'low',
    summary: 'Legacy review.',
    quality_score: { score: 100 },
    issues_count: 0,
    critical_count: 0,
    warning_count: 0,
    info_count: 0,
    ai_available: true,
    ai_issues: [],
    static_issues: [],
  });

  assert.equal(restored.result.static_metadata, undefined);
  assert.equal(restored.result.static_supported, undefined);
  assert.ok(restored.result.restorationUnavailable.includes('static analyzer metadata'));
});

test('preserves analysis failure without fabricating empty counts, risk, or score', () => {
  const restored = hydrateReviewSession({
    id: 'analysis-failed-review',
    project_name: 'failed-project',
    language: 'Python',
    source_code: 'value = 1\n',
    ai_available: false,
    ai_failure_reason: 'Groq returned invalid JSON.',
    ai_failure_category: 'response_json',
    static_analysis_error: 'analyzer_error',
    analysis_status: 'failed',
    overall_risk: null,
    total_issue_count: null,
    quality_score: null,
    ai_issues: [],
    static_issues: [],
  });

  assert.equal(restored.form.code, 'value = 1\n');
  assert.equal(restored.result.analysis_status, 'failed');
  assert.equal(restored.result.ai_failure_category, 'response_json');
  assert.equal(restored.result.static_analysis_error, 'analyzer_error');
  assert.equal(restored.result.quality_score, undefined);
  assert.equal(restored.result.overall_risk, undefined);
  assert.equal(restored.result.total_issue_count, undefined);
  assert.equal(restored.result.issues_count, undefined);
});

test('persists generated and accepted Auto-Fix state against the selected review', async () => {
  const proposal = {
    original_code: 'value = 1',
    fixed_code: 'value = 2',
    changes: [{ issue: 'finding', change: 'Updated value' }],
    reanalysis: { issues: [] },
    accepted: true,
  };
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/reviews/review-1/autofix');
    assert.equal(options.method, 'PATCH');
    assert.deepEqual(JSON.parse(options.body), proposal);
    return new Response(JSON.stringify({ saved: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  const result = await saveReviewAutoFix(
    'review-1',
    proposal,
    { getIdToken: async () => 'test-token' },
  );
  assert.equal(result.saved, true);
});
