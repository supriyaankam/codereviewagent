import { useState, useEffect, useCallback } from 'react';
import { Link, useLocation } from 'react-router-dom';
import PropTypes from 'prop-types';
import { useAuth } from '../context/AuthContext';
import { fetchDashboard as requestDashboard } from '../services/dashboardService';

const RISK_COLOR = {
  critical: 'danger',
  high:     'danger',
  medium:   'warning',
  low:      'success',
  info:     'info',
  '':       'secondary',
};

function StatCard({ label, value, icon, color, description, loading }) {
  return (
    <div className="col-12 col-sm-6 col-xl-4">
      <div className="card border-0 shadow-sm h-100">
        <div className="card-body p-4">
          <div className="d-flex align-items-center justify-content-between mb-3">
            <span className="text-secondary small fw-medium">{label}</span>
            <span className={`badge bg-${color}-subtle text-${color} p-2 rounded`}>
              <i className={`bi ${icon} fs-5`}></i>
            </span>
          </div>
          {loading ? (
            <div className="placeholder-glow">
              <span className="placeholder col-4 display-6"></span>
            </div>
          ) : (
            <div className={`display-6 fw-bold text-${color} mb-1`}>{value}</div>
          )}
          <p className="text-secondary small mb-0">{description}</p>
        </div>
      </div>
    </div>
  );
}

StatCard.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  icon: PropTypes.string.isRequired,
  color: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  loading: PropTypes.bool.isRequired,
};

export default function Dashboard() {
  const { currentUser } = useAuth();
  const location = useLocation();

  const [stats, setStats]     = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState('');

  const fetchDashboard = useCallback(async () => {
    if (!currentUser) { setLoading(false); return; }
    setLoading(true);
    setError('');
    try {
      setStats(await requestDashboard(currentUser));
    } catch (err) {
      setError(err.message || 'Could not load dashboard. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [currentUser]);

  useEffect(() => { fetchDashboard(); }, [fetchDashboard]);

  const statCards = [
    {
      label:       'Total Reviews',
      value:       stats?.total_reviews   ?? 0,
      icon:        'bi-file-earmark-code',
      color:       'primary',
      description: 'All-time reviews submitted',
    },
    {
      label: 'Average Quality Score',
      value: stats?.average_quality_score == null
        ? '—'
        : `${Number(stats.average_quality_score).toFixed(1)}%`,
      icon: 'bi-graph-up',
      color: 'success',
      description: `${stats?.quality_scored_reviews ?? 0} scored reviews`,
    },
    {
      label:       'Issues Found',
      value:       stats?.total_issues == null ? '—' : stats.total_issues,
      icon:        'bi-exclamation-triangle',
      color:       'warning',
      description: stats?.issue_counts_complete === false
        ? 'Unavailable for some older reviews'
        : 'Total issues detected',
    },
    {
      label:       'Critical Issues',
      value:       stats?.critical_issues == null ? '—' : stats.critical_issues,
      icon:        'bi-x-octagon',
      color:       'danger',
      description: stats?.critical_counts_complete === false
        ? 'Unavailable for some older reviews'
        : 'High-severity findings',
    },
    {
      label: 'Team Standards',
      value: stats?.total_standards ?? 0,
      icon: 'bi-shield-check',
      color: 'primary',
      description: `${stats?.enabled_standards ?? 0} enabled · ${stats?.disabled_standards ?? 0} disabled`,
    },
    {
      label: 'Team Violations',
      value: stats?.team_standard_violations ?? 0,
      icon: 'bi-patch-exclamation',
      color: 'danger',
      description: 'Findings tied to enabled team standards',
    },
    {
      label:       'Accepted Suggestions',
      value:       stats?.accepted_count  ?? 0,
      icon:        'bi-check-circle',
      color:       'success',
      description: 'Suggestions acted on',
    },
    {
      label:       'Rejected Suggestions',
      value:       stats?.rejected_count  ?? 0,
      icon:        'bi-slash-circle',
      color:       'secondary',
      description: 'Suggestions dismissed',
    },
  ];

  return (
    <div className="p-4 p-md-5">
      {/* Page header */}
      <div className="d-flex align-items-start justify-content-between mb-5 flex-wrap gap-3">
        <div>
          <h4 className="fw-bold text-dark mb-1">Dashboard</h4>
          <p className="text-secondary small mb-0">Overview of your code review activity.</p>
        </div>
        <button
          className="btn btn-outline-secondary btn-sm"
          onClick={fetchDashboard}
          disabled={loading}
          title="Refresh dashboard"
        >
          <i className={`bi bi-arrow-clockwise me-1 ${loading ? 'spin' : ''}`}></i>
          Refresh
        </button>
      </div>

      {location.state?.accountWarning && (
        <div className="alert alert-warning small mb-4" role="status">
          <i className="bi bi-exclamation-triangle me-2"></i>
          Your account was created, but some profile details could not be saved.{' '}
          {location.state.accountWarning}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="alert alert-danger small mb-4">
          <i className="bi bi-exclamation-circle me-2"></i>{error}
        </div>
      )}

      {/* Not logged in */}
      {!currentUser && !loading && (
        <div className="alert alert-info small mb-4">
          <i className="bi bi-person-circle me-2"></i>
          Sign in to see your personal review statistics.
        </div>
      )}

      {/* Stat cards */}
      <div className="row g-4 mb-5">
        {statCards.map(({ label, value, icon, color, description }) => (
          <StatCard
            key={label}
            label={label}
            value={value}
            icon={icon}
            color={color}
            description={description}
            loading={loading}
          />
        ))}
      </div>

      {/* Recent reviews */}
      <div className="mb-5">
        <h6 className="fw-semibold text-dark mb-3">
          <i className="bi bi-clock-history me-2 text-primary"></i>
          Recent Reviews
        </h6>

        {loading && (
          <div className="text-center py-4">
            <div className="spinner-border spinner-border-sm text-primary" role="status">
              <span className="visually-hidden">Loading…</span>
            </div>
          </div>
        )}

        {!loading && stats?.recent_reviews?.length === 0 && (
          <div className="card border-0 shadow-sm">
            <div className="card-body text-center py-4">
              <i className="bi bi-inbox text-secondary fs-3 mb-2 d-block opacity-50"></i>
              <p className="text-secondary small mb-3">No reviews yet.</p>
              <Link to="/review" className="btn btn-primary btn-sm px-4">
                <i className="bi bi-code-slash me-2"></i>Start your first review
              </Link>
            </div>
          </div>
        )}

        {!loading && stats?.recent_reviews?.length > 0 && (
          <div className="card border-0 shadow-sm">
            <div className="table-responsive">
              <table className="table table-hover mb-0 align-middle small">
                <thead className="table-light">
                  <tr>
                    <th className="ps-4">Project</th>
                    <th>Language</th>
                    <th>Risk</th>
                    <th>Issues</th>
                    <th>Team Compliance</th>
                    <th>Date</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {stats.recent_reviews.map((rev) => {
                    const riskColor = RISK_COLOR[rev.overall_risk] ?? 'secondary';
                    const date = rev.created_at
                      ? new Date(rev.created_at).toLocaleDateString(undefined, {
                          month: 'short', day: 'numeric', year: 'numeric',
                        })
                      : '—';
                    return (
                      <tr key={rev.id}>
                        <td className="ps-4 fw-medium text-dark">
                          {rev.project_name || '—'}
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
                          {rev.issues_count == null ? (
                            <span className="text-muted">—</span>
                          ) : (
                            <span className={`fw-medium ${rev.issues_count > 0 ? 'text-danger' : 'text-success'}`}>
                              {rev.issues_count}
                            </span>
                          )}
                        </td>
                        <td>
                          <span className={`badge ${rev.compliance_status === 'Compliant' ? 'bg-success-subtle text-success' : 'bg-danger-subtle text-danger'}`}>
                            {rev.compliance_status || 'Compliant'}
                          </span>
                        </td>
                        <td className="text-secondary">{date}</td>
                        <td className="pe-4">
                          <Link
                            to="/review"
                            className="btn btn-outline-primary btn-sm py-0 px-2"
                            style={{ fontSize: '0.72rem' }}
                          >
                            New Review
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Quick actions */}
      <div>
        <h6 className="fw-semibold text-dark mb-3">Quick actions</h6>
        <div className="d-flex gap-3 flex-wrap">
          <Link to="/review" className="btn btn-primary btn-sm px-4">
            <i className="bi bi-code-slash me-2"></i>New Review
          </Link>
          <Link to="/history" className="btn btn-outline-secondary btn-sm px-4">
            <i className="bi bi-clock-history me-2"></i>View History
          </Link>
        </div>
      </div>
    </div>
  );
}
