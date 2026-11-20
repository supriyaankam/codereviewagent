import { useState, useEffect, useCallback, useReducer, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  deleteReview,
  getReviewHistoryPage,
  getReviewDetailPath,
  getSavedIssueCount,
} from '../services/reviewService';
import {
  createInitialReviewHistoryState,
  reviewHistoryReducer,
} from '../utils/reviewHistoryState';

const RISK_COLOR = {
  critical: 'danger',
  high:     'danger',
  medium:   'warning',
  low:      'success',
  info:     'info',
};

export default function ReviewHistory() {
  const { currentUser } = useAuth();

  const [history, dispatch] = useReducer(
    reviewHistoryReducer,
    undefined,
    createInitialReviewHistoryState,
  );
  const [reviewToDelete, setReviewToDelete] = useState(null);
  const [deletingReviewId, setDeletingReviewId] = useState(null);
  const [deleteError, setDeleteError] = useState('');
  const deleteInFlightRef = useRef(false);
  const requestInFlightRef = useRef(null);
  const pendingRequestRef = useRef(null);
  const fetchHistoryRef = useRef(null);
  const currentUserRef = useRef(currentUser);
  const activeUserIdRef = useRef(null);
  const cursor = history.pageCursors[history.pageNumber - 1] ?? null;

  currentUserRef.current = currentUser;

  const fetchHistory = useCallback(async (requestedCursor) => {
    if (!currentUser) {
      dispatch({ type: 'signed-out' });
      return;
    }

    const request = { userId: currentUser.uid, cursor: requestedCursor };
    if (requestInFlightRef.current) {
      const activeRequest = requestInFlightRef.current;
      if (activeRequest.userId === request.userId && activeRequest.cursor === request.cursor) {
        return;
      }
      pendingRequestRef.current = request;
      return;
    }

    requestInFlightRef.current = request;
    dispatch({ type: 'load-start' });
    try {
      const page = await getReviewHistoryPage(currentUser, requestedCursor);
      if (currentUserRef.current?.uid !== request.userId) return;
      dispatch({
        type: 'load-success',
        reviews: page.reviews,
        nextCursor: page.next_cursor,
        totalCount: page.total_count,
      });
    } catch (requestError) {
      if (currentUserRef.current?.uid !== request.userId) return;
      if (requestedCursor && [400, 422].includes(requestError.status)) {
        dispatch({ type: 'invalid-cursor' });
      } else {
        dispatch({
          type: 'load-error',
          message: requestError.message || 'Could not load review history. Please try again.',
        });
      }
    } finally {
      requestInFlightRef.current = null;
      const pendingRequest = pendingRequestRef.current;
      pendingRequestRef.current = null;
      if (
        pendingRequest
        && currentUserRef.current?.uid === pendingRequest.userId
        && fetchHistoryRef.current
      ) {
        fetchHistoryRef.current(pendingRequest.cursor);
      }
    }
  }, [currentUser]);

  fetchHistoryRef.current = fetchHistory;

  useEffect(() => {
    if (!currentUser) {
      activeUserIdRef.current = null;
      pendingRequestRef.current = null;
      dispatch({ type: 'signed-out' });
      return;
    }

    if (activeUserIdRef.current !== currentUser.uid) {
      const previousUserId = activeUserIdRef.current;
      activeUserIdRef.current = currentUser.uid;
      if (previousUserId !== null) {
        dispatch({ type: 'reset-for-user', userId: currentUser.uid });
        return;
      }
      fetchHistory(null);
      return;
    }

    fetchHistory(cursor);
  }, [currentUser, cursor, history.pageNumber, history.refreshVersion, fetchHistory]);

  async function handleDeleteReview() {
    if (
      !reviewToDelete
      || deletingReviewId
      || deleteInFlightRef.current
      || history.loading
    ) return;
    const review = reviewToDelete;
    deleteInFlightRef.current = true;
    setDeletingReviewId(review.id);
    setDeleteError('');
    try {
      await deleteReview(review.id, currentUser);
      dispatch({
        type: 'delete-success',
        reviewId: review.id,
        projectName: review.project_name,
      });
      setReviewToDelete(null);
    } catch (deleteRequestError) {
      setDeleteError(deleteRequestError.message || 'Could not delete the review.');
    } finally {
      deleteInFlightRef.current = false;
      setDeletingReviewId(null);
    }
  }

  return (
    <div className="p-4 p-md-5">
      {/* Page header */}
      <div className="d-flex align-items-start justify-content-between mb-5 flex-wrap gap-3">
        <div>
          <h4 className="fw-bold text-dark mb-1">Review History</h4>
          <p className="text-secondary small mb-0">
            All past code reviews for your team, in one place.
          </p>
        </div>
        <button
          className="btn btn-outline-secondary btn-sm"
          onClick={() => dispatch({ type: 'refresh' })}
          disabled={history.loading || deletingReviewId !== null || !currentUser}
        >
          <i className="bi bi-arrow-clockwise me-1"></i>Refresh
        </button>
      </div>

      {history.notice && (
        <div className="alert alert-warning small mb-4" role="status">
          <i className="bi bi-info-circle me-2"></i>{history.notice}
        </div>
      )}

      {history.success && (
        <div className="alert alert-success small mb-4" role="status">
          <i className="bi bi-check-circle me-2"></i>{history.success}{' '}
          <Link to="/recently-deleted" className="alert-link">View Recently Deleted</Link>
        </div>
      )}

      {/* Error */}
      {history.error && (
        <div className="alert alert-danger small mb-4" role="alert">
          <i className="bi bi-exclamation-circle me-2"></i>{history.error}
        </div>
      )}

      {/* Not logged in */}
      {!currentUser && !history.loading && (
        <div className="card border-0 shadow-sm">
          <div className="card-body py-5 text-center">
            <i className="bi bi-person-circle fs-1 text-secondary mb-3 d-block opacity-50"></i>
            <h5 className="fw-semibold text-dark mb-2">Sign in to see your history</h5>
            <p className="text-secondary small mb-0">
              Your review history is linked to your account.
            </p>
          </div>
        </div>
      )}

      {/* Loading */}
      {history.loading && (
        <div className="text-center py-5">
          <div className="spinner-border text-primary" role="status">
            <span className="visually-hidden">Loading…</span>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!history.loading && !history.error && currentUser && history.reviews.length === 0 && (
        <div className="card border-0 shadow-sm">
          <div className="card-body py-5 text-center">
            <span className="d-inline-flex align-items-center justify-content-center rounded-circle bg-primary-subtle mb-4"
                  style={{ width: 72, height: 72 }}>
              <i className="bi bi-clock-history fs-2 text-primary"></i>
            </span>
            <h5 className="fw-semibold text-dark mb-2">No reviews yet</h5>
            <p className="text-secondary small mb-4" style={{ maxWidth: 380, margin: '0 auto' }}>
              Once you submit a code review, it will appear here. Your full review
              history is stored in Firebase and persists across sessions.
            </p>
            <Link to="/review" className="btn btn-primary btn-sm px-4">
              <i className="bi bi-code-slash me-2"></i>Start your first review
            </Link>
          </div>
        </div>
      )}

      {/* Reviews list */}
      {!history.loading && history.reviews.length > 0 && (
        <div className="card border-0 shadow-sm">
          <div className="table-responsive">
            <table className="table table-hover mb-0 align-middle small">
              <thead className="table-light">
                <tr>
                  <th className="ps-4">Project</th>
                  <th>Language</th>
                  <th>Risk</th>
                  <th>Issues</th>
                  <th>Status</th>
                  <th>Date</th>
                  <th>Summary</th>
                  <th className="pe-4">Actions</th>
                </tr>
              </thead>
              <tbody>
                {history.reviews.map((rev) => {
                  const riskColor = RISK_COLOR[rev.overall_risk] ?? 'secondary';
                  const issueCount = getSavedIssueCount(rev);
                  const date = rev.created_at
                    ? new Date(rev.created_at).toLocaleDateString(undefined, {
                        month: 'short', day: 'numeric', year: 'numeric',
                      })
                    : '—';
                  return (
                    <tr key={rev.id}>
                      <td className="ps-4 fw-medium">
                        <Link to={getReviewDetailPath(rev.id)}>
                          {rev.project_name || '—'}
                        </Link>
                      </td>
                      <td>
                        <span className="badge bg-light text-secondary border">
                          {rev.language || '—'}
                        </span>
                      </td>
                      <td>
                        {rev.overall_risk ? (
                          <span className={`badge bg-${riskColor}-subtle text-${riskColor} text-capitalize`}>
                            {rev.overall_risk}
                          </span>
                        ) : <span className="text-muted">—</span>}
                      </td>
                      <td>
                        <span className={`fw-medium ${issueCount === null ? 'text-muted' : issueCount > 0 ? 'text-danger' : 'text-success'}`}>
                          {issueCount ?? '—'}
                        </span>
                      </td>
                      <td>
                        <span className={`badge ${rev.status === 'completed' ? 'bg-success-subtle text-success' : 'bg-warning-subtle text-warning'}`}>
                          {rev.status || 'pending'}
                        </span>
                      </td>
                      <td className="text-secondary">{date}</td>
                      <td className="text-secondary pe-4" style={{ maxWidth: 220 }}>
                        <span className="text-truncate d-block" style={{ maxWidth: 220 }}>
                          {rev.summary || '—'}
                        </span>
                      </td>
                      <td className="pe-4">
                        <button
                          type="button"
                          className="btn btn-outline-danger btn-sm"
                          onClick={() => {
                            setDeleteError('');
                            dispatch({ type: 'clear-messages' });
                            setReviewToDelete(rev);
                          }}
                          disabled={history.loading || deletingReviewId !== null}
                          aria-label={`Delete review for ${rev.project_name || 'this project'}`}
                        >
                          <i className="bi bi-trash me-1"></i>Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {currentUser && (
        <div
          className="d-flex align-items-center justify-content-center gap-3 mt-4"
          aria-label="Review History pagination"
        >
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            onClick={() => dispatch({ type: 'previous-page' })}
            disabled={
              history.loading
              || deletingReviewId !== null
              || history.pageNumber <= 1
            }
          >
            Previous
          </button>
          <span className="text-secondary small" aria-live="polite">
            Page {history.pageNumber} · {history.totalCount} review
            {history.totalCount === 1 ? '' : 's'} total
          </span>
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            onClick={() => dispatch({ type: 'next-page' })}
            disabled={
              history.loading
              || deletingReviewId !== null
              || !history.nextCursor
            }
          >
            Next
          </button>
        </div>
      )}

      {reviewToDelete && (
        <div
          className="modal d-block"
          tabIndex="-1"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="delete-review-title"
          style={{ background: 'rgba(0,0,0,0.45)' }}
        >
          <div className="modal-dialog modal-dialog-centered modal-sm" role="document">
            <div className="modal-content border-0 shadow">
              <div className="modal-body p-4 text-center">
                <i className="bi bi-trash fs-2 text-danger mb-3 d-block"></i>
                <h6 id="delete-review-title" className="fw-semibold mb-2">
                  Delete this review?
                </h6>
                <p className="text-secondary small mb-3">
                  This moves the review to Recently Deleted, where it can be restored for 30 days.
                  Associated findings and feedback will remain stored.
                </p>
                {deleteError && (
                  <div className="alert alert-danger small text-start" role="alert">
                    {deleteError}
                  </div>
                )}
                <div className="d-flex gap-2 justify-content-center">
                  <button
                    type="button"
                    className="btn btn-outline-secondary btn-sm"
                    onClick={() => setReviewToDelete(null)}
                    disabled={deletingReviewId !== null || history.loading}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn btn-danger btn-sm px-4"
                    onClick={handleDeleteReview}
                    disabled={deletingReviewId !== null || history.loading}
                  >
                    {deletingReviewId === reviewToDelete.id
                      ? <span className="spinner-border spinner-border-sm" role="status" aria-label="Deleting"></span>
                      : 'Delete review'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
