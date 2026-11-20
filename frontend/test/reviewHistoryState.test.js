import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createInitialReviewHistoryState,
  reviewHistoryReducer,
} from '../src/utils/reviewHistoryState.js';

test('Review History navigation stores the next cursor and returns to the previous cursor', () => {
  const firstPage = {
    ...createInitialReviewHistoryState(false),
    reviews: [{ id: 'review-1' }],
    nextCursor: 'page-two-cursor',
    totalCount: 11,
  };
  const secondPage = reviewHistoryReducer(firstPage, { type: 'next-page' });

  assert.equal(secondPage.pageNumber, 2);
  assert.deepEqual(secondPage.pageCursors, [null, 'page-two-cursor']);
  assert.equal(secondPage.refreshVersion, firstPage.refreshVersion);
  assert.equal(secondPage.loading, true);

  const loadedSecondPage = reviewHistoryReducer(secondPage, {
    type: 'load-success',
    reviews: [{ id: 'review-2' }],
    nextCursor: null,
    totalCount: 11,
  });
  const previousPage = reviewHistoryReducer(loadedSecondPage, { type: 'previous-page' });
  assert.equal(previousPage.pageNumber, 1);
  assert.deepEqual(previousPage.pageCursors, [null]);
});

test('navigation does not advance while a page request is loading', () => {
  const loadingState = {
    ...createInitialReviewHistoryState(),
    nextCursor: 'page-two-cursor',
  };

  assert.equal(
    reviewHistoryReducer(loadingState, { type: 'next-page' }),
    loadingState,
  );
});

test('deleting the last review on a page navigates back and refreshes the count', () => {
  const pageTwo = {
    ...createInitialReviewHistoryState(false),
    reviews: [{ id: 'last-review', project_name: 'Project' }],
    pageNumber: 2,
    pageCursors: [null, 'page-two-cursor'],
    totalCount: 11,
  };

  const next = reviewHistoryReducer(pageTwo, {
    type: 'delete-success',
    reviewId: 'last-review',
    projectName: 'Project',
  });

  assert.deepEqual(next.reviews, []);
  assert.equal(next.pageNumber, 1);
  assert.deepEqual(next.pageCursors, [null]);
  assert.equal(next.totalCount, 10);
  assert.equal(next.refreshVersion, pageTwo.refreshVersion + 1);
  assert.match(next.success, /Project was moved to Recently Deleted/);
});

test('deleting a review on a non-empty page keeps that page and requests a refresh', () => {
  const page = {
    ...createInitialReviewHistoryState(false),
    reviews: [{ id: 'delete-me' }, { id: 'keep-me' }],
    totalCount: 2,
  };

  const next = reviewHistoryReducer(page, {
    type: 'delete-success',
    reviewId: 'delete-me',
    projectName: 'Project',
  });

  assert.deepEqual(next.reviews, [{ id: 'keep-me' }]);
  assert.equal(next.pageNumber, 1);
  assert.equal(next.totalCount, 1);
  assert.equal(next.refreshVersion, page.refreshVersion + 1);
});

test('invalid cursors reset pagination and preserve a user-facing notice', () => {
  const laterPage = {
    ...createInitialReviewHistoryState(false),
    pageNumber: 3,
    pageCursors: [null, 'page-two-cursor', 'page-three-cursor'],
    nextCursor: 'page-four-cursor',
    reviews: [{ id: 'stale-review' }],
  };

  const reset = reviewHistoryReducer(laterPage, { type: 'invalid-cursor' });

  assert.equal(reset.pageNumber, 1);
  assert.deepEqual(reset.pageCursors, [null]);
  assert.equal(reset.nextCursor, null);
  assert.deepEqual(reset.reviews, []);
  assert.match(reset.notice, /reset to page 1/i);
  assert.equal(reset.loading, true);
});
