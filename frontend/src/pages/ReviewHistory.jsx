import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { getReviewDetailPath, getSavedIssueCount } from '../services/reviewService';

const RISK_COLOR = {
  critical: 'danger',
  high:     'danger',
  medium:   'warning',
  low:      'success',
  info:     'info',
};

export default function ReviewHistory() {
  const { currentUser } = useAuth();

  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState('');

  const fetchHistory = useCallback(async () => {
    if (!currentUser) { setLoading(false); return; }
    setLoading(true);
    setError('');
    try {
      const token = await currentUser.getIdToken();
      const res = await fetch(
        `/api/reviews?user_id=${encodeURIComponent(currentUser.uid)}`,
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setReviews(Array.isArray(data) ? data : []);
    } catch {
      setError('Could not load review history. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [currentUser]);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

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
          onClick={fetchHistory}
          disabled={loading}
        >
          <i className="bi bi-arrow-clockwise me-1"></i>Refresh
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="alert alert-danger small mb-4">
          <i className="bi bi-exclamation-circle me-2"></i>{error}
        </div>
      )}

      {/* Not logged in */}
      {!currentUser && !loading && (
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
      {loading && (
        <div className="text-center py-5">
          <div className="spinner-border text-primary" role="status">
            <span className="visually-hidden">Loading…</span>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && currentUser && reviews.length === 0 && (
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
      {!loading && reviews.length > 0 && (
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
                </tr>
              </thead>
              <tbody>
                {reviews.map((rev) => {
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
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="card-footer bg-transparent text-secondary small py-2 px-4">
            {reviews.length} review{reviews.length !== 1 ? 's' : ''} total
          </div>
        </div>
      )}
    </div>
  );
}
