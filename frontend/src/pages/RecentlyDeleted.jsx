import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  getDeletedReviewHistoryPage,
  permanentlyDeleteReview,
  restoreDeletedReview,
} from '../services/reviewService';
import {
  createInitialTrashState,
  formatTrashDate,
  getDaysRemaining,
  getTrashOperationError,
  recentlyDeletedReducer,
} from '../utils/recentlyDeletedState';

export default function RecentlyDeleted() {
  const { currentUser } = useAuth();
  const [trash, dispatch] = useReducer(
    recentlyDeletedReducer,
    undefined,
    createInitialTrashState,
  );
  const [operatingReviewId, setOperatingReviewId] = useState(null);
  const operationInFlightRef = useRef(false);
  const requestInFlightRef = useRef(null);
  const pendingRequestRef = useRef(null);
  const fetchTrashRef = useRef(null);
  const currentUserRef = useRef(currentUser);
  const activeUserIdRef = useRef(null);
  const cursor = trash.pageCursors[trash.pageNumber - 1] ?? null;

  currentUserRef.current = currentUser;

  const fetchTrash = useCallback(async (requestedCursor) => {
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
      const page = await getDeletedReviewHistoryPage(currentUser, requestedCursor);
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
          message: requestError.message || 'Could not load Recently Deleted. Please try again.',
        });
      }
    } finally {
      requestInFlightRef.current = null;
      const pendingRequest = pendingRequestRef.current;
      pendingRequestRef.current = null;
      if (
        pendingRequest
        && currentUserRef.current?.uid === pendingRequest.userId
        && fetchTrashRef.current
      ) {
        fetchTrashRef.current(pendingRequest.cursor);
      }
    }
  }, [currentUser]);

  fetchTrashRef.current = fetchTrash;

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
      fetchTrash(null);
      return;
    }

    fetchTrash(cursor);
  }, [currentUser, cursor, trash.pageNumber, trash.refreshVersion, fetchTrash]);

  async function handleRestore(review) {
    if (operationInFlightRef.current || trash.loading || !currentUser) return;
    operationInFlightRef.current = true;
    setOperatingReviewId(review.id);
    dispatch({ type: 'clear-messages' });
    try {
      await restoreDeletedReview(review.id, currentUser);
      dispatch({ type: 'restore-success', reviewId: review.id });
    } catch (error) {
      if (error.status === 410 || error.status === 404) {
        dispatch({
          type: 'stale-review',
          reviewId: review.id,
          message: getTrashOperationError(error, 'restore'),
        });
      } else {
        dispatch({
          type: 'operation-error',
          message: getTrashOperationError(error, 'restore'),
        });
      }
    } finally {
      operationInFlightRef.current = false;
      setOperatingReviewId(null);
    }
  }

  async function handlePermanentDelete() {
    const review = trash.reviewToPermanentlyDelete;
    if (!review || operationInFlightRef.current || trash.loading || !currentUser) return;
    operationInFlightRef.current = true;
    setOperatingReviewId(review.id);
    dispatch({ type: 'clear-messages' });
    try {
      await permanentlyDeleteReview(review.id, currentUser);
      dispatch({ type: 'permanent-delete-success', reviewId: review.id });
    } catch (error) {
      if (error.status === 404) {
        dispatch({
          type: 'stale-review',
          reviewId: review.id,
          message: getTrashOperationError(error, 'delete'),
        });
      } else {
        dispatch({
          type: 'operation-error',
          message: getTrashOperationError(error, 'delete'),
        });
      }
    } finally {
      operationInFlightRef.current = false;
      setOperatingReviewId(null);
    }
  }

  return (
    <div className="p-4 p-md-5">
      <div className="d-flex align-items-start justify-content-between mb-4 flex-wrap gap-3">
        <div>
          <h4 className="fw-bold text-dark mb-1">Recently Deleted</h4>
          <p className="text-secondary small mb-0">
            Deleted reviews are available to restore for 30 days.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm"
          onClick={() => dispatch({ type: 'refresh' })}
          disabled={trash.loading || operatingReviewId !== null || !currentUser}
        >
          <i className="bi bi-arrow-clockwise me-1"></i>Refresh
        </button>
      </div>

      {trash.notice && (
        <div className="alert alert-warning small mb-4" role="status">{trash.notice}</div>
      )}
      {trash.success && (
        <div className="alert alert-success small mb-4" role="status">
          <i className="bi bi-check-circle me-2"></i>{trash.success}{' '}
          {trash.success.includes('restored') && (
            <Link to="/history" className="alert-link">View Review History</Link>
          )}
        </div>
      )}
      {trash.error && (
        <div className="alert alert-danger small mb-4" role="alert">{trash.error}</div>
      )}

      {!currentUser && !trash.loading && (
        <div className="card border-0 shadow-sm">
          <div className="card-body py-5 text-center">
            <i className="bi bi-person-circle fs-1 text-secondary mb-3 d-block opacity-50"></i>
            <h5 className="fw-semibold text-dark mb-2">Sign in to view Recently Deleted</h5>
          </div>
        </div>
      )}

      {trash.loading && (
        <div className="text-center py-5">
          <div className="spinner-border text-primary" role="status">
            <span className="visually-hidden">Loading Recently Deleted…</span>
          </div>
        </div>
      )}

      {!trash.loading && !trash.error && currentUser && trash.reviews.length === 0 && (
        <div className="card border-0 shadow-sm">
          <div className="card-body py-5 text-center">
            <span
              className="d-inline-flex align-items-center justify-content-center rounded-circle bg-secondary-subtle mb-4"
              style={{ width: 72, height: 72 }}
            >
              <i className="bi bi-trash3 fs-2 text-secondary"></i>
            </span>
            <h5 className="fw-semibold text-dark mb-2">Trash is empty</h5>
            <p className="text-secondary small mb-0">
              Reviews you delete will appear here for 30 days.
            </p>
          </div>
        </div>
      )}

      {!trash.loading && trash.reviews.length > 0 && (
        <div className="card border-0 shadow-sm">
          <div className="table-responsive">
            <table className="table table-hover mb-0 align-middle small">
              <thead className="table-light">
                <tr>
                  <th className="ps-4">Project</th>
                  <th>Language</th>
                  <th>Deleted</th>
                  <th>Expires</th>
                  <th>Days remaining</th>
                  <th className="pe-4">Actions</th>
                </tr>
              </thead>
              <tbody>
                {trash.reviews.map((review) => {
                  const daysRemaining = getDaysRemaining(review.expires_at);
                  return (
                    <tr key={review.id}>
                      <td className="ps-4 fw-medium">{review.project_name || '—'}</td>
                      <td>
                        <span className="badge bg-light text-secondary border">
                          {review.language || '—'}
                        </span>
                      </td>
                      <td className="text-secondary">{formatTrashDate(review.deleted_at)}</td>
                      <td className="text-secondary">{formatTrashDate(review.expires_at)}</td>
                      <td>
                        {daysRemaining === null ? '—' : `${daysRemaining} day${daysRemaining === 1 ? '' : 's'}`}
                      </td>
                      <td className="pe-4">
                        <div className="d-flex gap-2">
                          <button
                            type="button"
                            className="btn btn-outline-primary btn-sm"
                            onClick={() => handleRestore(review)}
                            disabled={trash.loading || operatingReviewId !== null}
                          >
                            {operatingReviewId === review.id
                              ? <span className="spinner-border spinner-border-sm" role="status" aria-label="Restoring"></span>
                              : <><i className="bi bi-arrow-counterclockwise me-1"></i>Restore</>}
                          </button>
                          <button
                            type="button"
                            className="btn btn-outline-danger btn-sm"
                            onClick={() => dispatch({ type: 'request-permanent-delete', review })}
                            disabled={trash.loading || operatingReviewId !== null}
                          >
                            <i className="bi bi-trash me-1"></i>Delete permanently
                          </button>
                        </div>
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
          aria-label="Recently Deleted pagination"
        >
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            onClick={() => dispatch({ type: 'previous-page' })}
            disabled={trash.loading || operatingReviewId !== null || trash.pageNumber <= 1}
          >
            Previous
          </button>
          <span className="text-secondary small" aria-live="polite">
            Page {trash.pageNumber} · {trash.totalCount} deleted review
            {trash.totalCount === 1 ? '' : 's'} total
          </span>
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            onClick={() => dispatch({ type: 'next-page' })}
            disabled={trash.loading || operatingReviewId !== null || !trash.nextCursor}
          >
            Next
          </button>
        </div>
      )}

      {trash.reviewToPermanentlyDelete && (
        <div
          className="modal d-block"
          tabIndex="-1"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="permanent-delete-title"
          style={{ background: 'rgba(0,0,0,0.45)' }}
        >
          <div className="modal-dialog modal-dialog-centered modal-sm" role="document">
            <div className="modal-content border-0 shadow">
              <div className="modal-body p-4 text-center">
                <i className="bi bi-exclamation-triangle fs-2 text-danger mb-3 d-block"></i>
                <h6 id="permanent-delete-title" className="fw-semibold mb-2">
                  Permanently delete this review?
                </h6>
                <p className="text-secondary small mb-3">
                  This cannot be undone. Associated findings and feedback will remain stored.
                </p>
                <div className="d-flex gap-2 justify-content-center">
                  <button
                    type="button"
                    className="btn btn-outline-secondary btn-sm"
                    onClick={() => dispatch({ type: 'cancel-permanent-delete' })}
                    disabled={operatingReviewId !== null || trash.loading}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn btn-danger btn-sm px-3"
                    onClick={handlePermanentDelete}
                    disabled={operatingReviewId !== null || trash.loading}
                  >
                    {operatingReviewId === trash.reviewToPermanentlyDelete.id
                      ? <span className="spinner-border spinner-border-sm" role="status" aria-label="Deleting permanently"></span>
                      : 'Delete permanently'}
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
