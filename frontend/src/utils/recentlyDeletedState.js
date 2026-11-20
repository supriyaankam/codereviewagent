export function createInitialTrashState(loading = true) {
  return {
    reviews: [],
    loading,
    error: '',
    notice: '',
    success: '',
    pageNumber: 1,
    pageCursors: [null],
    nextCursor: null,
    totalCount: 0,
    refreshVersion: 0,
    reviewToPermanentlyDelete: null,
  };
}

function removeReviewFromPage(state, reviewId) {
  const reviews = state.reviews.filter((review) => review.id !== reviewId);
  const pageNumber = reviews.length === 0 && state.pageNumber > 1
    ? state.pageNumber - 1
    : state.pageNumber;
  return {
    reviews,
    pageNumber,
    pageCursors: state.pageCursors.slice(0, pageNumber),
    totalCount: Math.max(0, state.totalCount - 1),
  };
}

export function recentlyDeletedReducer(state, action) {
  switch (action.type) {
    case 'load-start':
      return { ...state, loading: true, error: '' };
    case 'load-success':
      return {
        ...state,
        loading: false,
        error: '',
        reviews: action.reviews,
        nextCursor: action.nextCursor,
        totalCount: action.totalCount,
      };
    case 'load-error':
      return { ...state, loading: false, error: action.message };
    case 'next-page':
      if (state.loading || !state.nextCursor) return state;
      return {
        ...state,
        loading: true,
        pageNumber: state.pageNumber + 1,
        pageCursors: [
          ...state.pageCursors.slice(0, state.pageNumber),
          state.nextCursor,
        ],
        notice: '',
      };
    case 'previous-page': {
      if (state.loading || state.pageNumber <= 1) return state;
      const pageNumber = state.pageNumber - 1;
      return {
        ...state,
        loading: true,
        pageNumber,
        pageCursors: state.pageCursors.slice(0, pageNumber),
        notice: '',
      };
    }
    case 'refresh':
      return {
        ...state,
        loading: true,
        pageCursors: state.pageCursors.slice(0, state.pageNumber),
        refreshVersion: state.refreshVersion + 1,
        notice: '',
      };
    case 'invalid-cursor':
      return {
        ...state,
        reviews: [],
        loading: true,
        error: '',
        notice: 'This Trash page cursor is no longer valid. Recently Deleted was reset to page 1.',
        pageNumber: 1,
        pageCursors: [null],
        nextCursor: null,
        refreshVersion: state.refreshVersion + 1,
      };
    case 'request-permanent-delete':
      if (state.loading) return state;
      return {
        ...state,
        error: '',
        notice: '',
        success: '',
        reviewToPermanentlyDelete: action.review,
      };
    case 'cancel-permanent-delete':
      return { ...state, reviewToPermanentlyDelete: null };
    case 'permanent-delete-success': {
      const removed = removeReviewFromPage(state, action.reviewId);
      return {
        ...state,
        ...removed,
        loading: true,
        error: '',
        success: 'Review permanently deleted.',
        notice: '',
        reviewToPermanentlyDelete: null,
        refreshVersion: state.refreshVersion + 1,
      };
    }
    case 'restore-success': {
      const removed = removeReviewFromPage(state, action.reviewId);
      return {
        ...state,
        ...removed,
        loading: true,
        error: '',
        success: 'Review restored to Review History.',
        notice: '',
        refreshVersion: state.refreshVersion + 1,
      };
    }
    case 'stale-review': {
      const removed = removeReviewFromPage(state, action.reviewId);
      return {
        ...state,
        ...removed,
        loading: true,
        error: '',
        notice: action.message,
        success: '',
        reviewToPermanentlyDelete: null,
        refreshVersion: state.refreshVersion + 1,
      };
    }
    case 'operation-error':
      return { ...state, error: action.message, success: '' };
    case 'clear-messages':
      return { ...state, error: '', notice: '', success: '' };
    case 'reset-for-user':
      return {
        ...createInitialTrashState(Boolean(action.userId)),
        refreshVersion: state.refreshVersion + 1,
      };
    case 'signed-out':
      if (
        !state.loading
        && state.reviews.length === 0
        && state.pageNumber === 1
        && state.totalCount === 0
        && state.reviewToPermanentlyDelete === null
      ) {
        return state;
      }
      return {
        ...createInitialTrashState(false),
        refreshVersion: state.refreshVersion + 1,
      };
    default:
      return state;
  }
}

export function getTrashOperationError(error, operation) {
  if (error?.status === 409 && operation === 'restore') {
    return 'This review cannot be restored because an active review with the same ID already exists.';
  }
  if (error?.status === 410 && operation === 'restore') {
    return 'This review has expired and cannot be restored. The list was refreshed.';
  }
  if (error?.status === 404) {
    return 'This review is no longer in Recently Deleted. The list was refreshed.';
  }
  return error?.message || `Could not ${operation === 'restore' ? 'restore' : 'delete'} the review.`;
}

export function formatTrashDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function getDaysRemaining(expiresAt, now = Date.now()) {
  if (!expiresAt) return null;
  const expiryTime = new Date(expiresAt).getTime();
  if (!Number.isFinite(expiryTime)) return null;
  return Math.max(0, Math.ceil((expiryTime - now) / 86_400_000));
}
