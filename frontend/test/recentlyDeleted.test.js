import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import {
  getDeletedReviewHistoryPage,
  permanentlyDeleteReview,
  restoreDeletedReview,
} from '../src/services/reviewService.js';
import {
  createInitialTrashState,
  getDaysRemaining,
  getTrashOperationError,
  recentlyDeletedReducer,
} from '../src/utils/recentlyDeletedState.js';

const originalFetch = globalThis.fetch;
const currentUser = {
  uid: 'user 1',
  getIdToken: async () => 'firebase-token',
};

afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('Trash page service sends the authenticated UID and optional cursor', async () => {
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

  const page = await getDeletedReviewHistoryPage(currentUser, 'cursor/2');

  assert.equal(
    requestUrl,
    '/api/reviews/trash/page?user_id=user+1&cursor=cursor%2F2',
  );
  assert.equal(requestOptions.headers.Authorization, 'Bearer firebase-token');
  assert.equal(page.total_count, 11);
});

test('Trash page service validates the response and requires an authenticated user', async () => {
  await assert.rejects(getDeletedReviewHistoryPage(null), /Sign in/);
  globalThis.fetch = async () => new Response(JSON.stringify({
    reviews: [],
    next_cursor: null,
    total_count: '0',
  }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
  await assert.rejects(
    getDeletedReviewHistoryPage(currentUser),
    /response was invalid/,
  );
});

test('restore and permanent-delete services use authenticated methods and preserve HTTP errors', async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    if (options.method === 'POST') {
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify({ detail: 'Review has expired.' }), {
      status: 410,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await restoreDeletedReview('review-1', currentUser);
  await assert.rejects(
    permanentlyDeleteReview('review-1', currentUser),
    (error) => error.status === 410 && error.message === 'Review has expired.',
  );

  assert.deepEqual(requests.map(({ url, options }) => [url, options.method]), [
    ['/api/reviews/trash/review-1/restore', 'POST'],
    ['/api/reviews/trash/review-1', 'DELETE'],
  ]);
  assert.ok(requests.every(({ options }) => (
    options.headers.Authorization === 'Bearer firebase-token'
  )));
});

test('Trash service methods reject missing review IDs', async () => {
  await assert.rejects(restoreDeletedReview('', currentUser), /Review ID is required/);
  await assert.rejects(permanentlyDeleteReview('', currentUser), /Review ID is required/);
});

test('loading and empty states are represented after the Trash request finishes', () => {
  const loading = createInitialTrashState();
  assert.equal(loading.loading, true);

  const empty = recentlyDeletedReducer(loading, {
    type: 'load-success',
    reviews: [],
    nextCursor: null,
    totalCount: 0,
  });
  assert.equal(empty.loading, false);
  assert.deepEqual(empty.reviews, []);
  assert.equal(empty.totalCount, 0);
});

test('Trash pagination advances and returns to the previous cursor without duplicates', () => {
  const firstPage = recentlyDeletedReducer(createInitialTrashState(false), {
    type: 'load-success',
    reviews: [{ id: 'review-1' }],
    nextCursor: 'page-two',
    totalCount: 11,
  });
  const secondRequest = recentlyDeletedReducer(firstPage, { type: 'next-page' });
  assert.equal(secondRequest.pageNumber, 2);
  assert.deepEqual(secondRequest.pageCursors, [null, 'page-two']);
  assert.equal(
    recentlyDeletedReducer(secondRequest, { type: 'next-page' }),
    secondRequest,
  );

  const loadedSecondPage = recentlyDeletedReducer(secondRequest, {
    type: 'load-success',
    reviews: [{ id: 'review-2' }],
    nextCursor: null,
    totalCount: 11,
  });
  const previous = recentlyDeletedReducer(loadedSecondPage, { type: 'previous-page' });
  assert.equal(previous.pageNumber, 1);
  assert.deepEqual(previous.pageCursors, [null]);
});

test('invalid Trash cursors reset to the first page with a visible notice', () => {
  const laterPage = {
    ...createInitialTrashState(false),
    reviews: [{ id: 'review-11' }],
    pageNumber: 2,
    pageCursors: [null, 'stale-cursor'],
    nextCursor: 'another-cursor',
  };
  const reset = recentlyDeletedReducer(laterPage, { type: 'invalid-cursor' });

  assert.equal(reset.pageNumber, 1);
  assert.deepEqual(reset.pageCursors, [null]);
  assert.deepEqual(reset.reviews, []);
  assert.match(reset.notice, /reset to page 1/i);
});

test('restore success removes the review, refreshes the count, and refreshes the current page', () => {
  const state = {
    ...createInitialTrashState(false),
    reviews: [{ id: 'review-1' }, { id: 'review-2' }],
    totalCount: 2,
  };

  const restored = recentlyDeletedReducer(state, {
    type: 'restore-success',
    reviewId: 'review-1',
  });

  assert.deepEqual(restored.reviews, [{ id: 'review-2' }]);
  assert.equal(restored.totalCount, 1);
  assert.equal(restored.refreshVersion, state.refreshVersion + 1);
  assert.match(restored.success, /restored to Review History/);
});

test('restore conflict keeps the review in Trash and shows an actionable error', () => {
  const review = { id: 'review-1' };
  const state = {
    ...createInitialTrashState(false),
    reviews: [review],
    totalCount: 1,
  };
  const failed = recentlyDeletedReducer(state, {
    type: 'operation-error',
    message: getTrashOperationError({ status: 409 }, 'restore'),
  });

  assert.deepEqual(failed.reviews, [review]);
  assert.equal(failed.totalCount, 1);
  assert.match(failed.error, /active review with the same ID/);
});

test('permanent deletion requires a confirmation state before removing a review', () => {
  const review = { id: 'review-1', project_name: 'Payments' };
  const state = {
    ...createInitialTrashState(false),
    reviews: [review],
    totalCount: 1,
  };
  const awaitingConfirmation = recentlyDeletedReducer(state, {
    type: 'request-permanent-delete',
    review,
  });

  assert.equal(awaitingConfirmation.reviewToPermanentlyDelete, review);
  assert.deepEqual(awaitingConfirmation.reviews, [review]);
  assert.equal(awaitingConfirmation.totalCount, 1);

  const cancelled = recentlyDeletedReducer(awaitingConfirmation, {
    type: 'cancel-permanent-delete',
  });
  assert.equal(cancelled.reviewToPermanentlyDelete, null);
  assert.deepEqual(cancelled.reviews, [review]);

  const confirmed = recentlyDeletedReducer(awaitingConfirmation, {
    type: 'permanent-delete-success',
    reviewId: review.id,
  });
  assert.deepEqual(confirmed.reviews, []);
  assert.equal(confirmed.totalCount, 0);
  assert.equal(confirmed.reviewToPermanentlyDelete, null);
});

test('expired and missing reviews are removed from the page and show a refresh notice', () => {
  const state = {
    ...createInitialTrashState(false),
    reviews: [{ id: 'review-1' }],
    totalCount: 1,
  };
  const error = getTrashOperationError({ status: 410 }, 'restore');
  const expired = recentlyDeletedReducer(state, {
    type: 'stale-review',
    reviewId: 'review-1',
    message: error,
  });

  assert.deepEqual(expired.reviews, []);
  assert.equal(expired.totalCount, 0);
  assert.match(expired.notice, /expired/);
  assert.equal(expired.refreshVersion, state.refreshVersion + 1);
});

test('Trash operation errors explain invalid cursor, conflict, and expiry cases', () => {
  assert.match(
    getTrashOperationError({ status: 409 }, 'restore'),
    /active review with the same ID/,
  );
  assert.match(
    getTrashOperationError({ status: 410 }, 'restore'),
    /expired and cannot be restored/,
  );
  assert.match(
    getTrashOperationError({ status: 404 }, 'delete'),
    /no longer in Recently Deleted/,
  );
});

test('days remaining is rounded up and safely handles expired or invalid dates', () => {
  const now = Date.parse('2026-11-20T00:00:00.000Z');
  assert.equal(getDaysRemaining('2026-11-21T00:00:00.000Z', now), 1);
  assert.equal(getDaysRemaining('2026-11-20T00:00:01.000Z', now), 1);
  assert.equal(getDaysRemaining('2026-11-19T23:59:00.000Z', now), 0);
  assert.equal(getDaysRemaining('not-a-date', now), null);
});
