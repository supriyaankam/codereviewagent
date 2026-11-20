export function createInitialReviewHistoryState(loading = true) {
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
  };
}

export function reviewHistoryReducer(state, action) {
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
        notice: 'This page cursor is no longer valid. Review History was reset to page 1.',
        pageNumber: 1,
        pageCursors: [null],
        nextCursor: null,
        refreshVersion: state.refreshVersion + 1,
      };
    case 'delete-success': {
      const reviews = state.reviews.filter((review) => review.id !== action.reviewId);
      const pageNumber = reviews.length === 0 && state.pageNumber > 1
        ? state.pageNumber - 1
        : state.pageNumber;
      return {
        ...state,
        loading: true,
        reviews,
        totalCount: Math.max(0, state.totalCount - 1),
        pageNumber,
        pageCursors: state.pageCursors.slice(0, pageNumber),
        success: `Review for ${action.projectName || 'this project'} was moved to Recently Deleted.`,
        notice: '',
        refreshVersion: state.refreshVersion + 1,
      };
    }
    case 'clear-messages':
      return { ...state, error: '', notice: '', success: '' };
    case 'reset-for-user':
      return {
        ...createInitialReviewHistoryState(Boolean(action.userId)),
        refreshVersion: state.refreshVersion + 1,
      };
    case 'signed-out':
      if (
        !state.loading
        && state.reviews.length === 0
        && state.pageNumber === 1
        && state.totalCount === 0
      ) {
        return state;
      }
      return {
        ...createInitialReviewHistoryState(false),
        refreshVersion: state.refreshVersion + 1,
      };
    default:
      return state;
  }
}
