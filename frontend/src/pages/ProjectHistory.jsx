import { useCallback, useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import { useAuth } from '../context/AuthContext';
import {
  getProjectAnalysis,
  getProjectAnalysisHistory,
  getProjectHistory,
} from '../services/projectHistoryService';

const PAGE_SIZE = 10;

function formatDate(value) {
  if (!value) return '—';
  const date = typeof value === 'number'
    ? new Date(value < 1_000_000_000_000 ? value * 1000 : value)
    : new Date(value);
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function statusLabel(status) {
  if (status === 'complete' || status === 'analyzed') return 'Complete';
  if (status === 'in_progress') return 'In progress';
  if (status === 'failed') return 'Failed';
  if (status === 'uploading') return 'Uploading';
  return 'Not analyzed';
}

function Findings({ analysis }) {
  const filesByIndex = new Map(
    (analysis.files || []).map((file) => [String(file.file_index), { ...file, findings: [] }]),
  );
  const findingsByPath = new Map();

  for (const finding of analysis.findings || []) {
    const file = filesByIndex.get(String(finding.file_index));
    if (file) {
      file.findings.push(finding);
    } else {
      const path = finding.path || 'Unknown file';
      if (!findingsByPath.has(path)) findingsByPath.set(path, []);
      findingsByPath.get(path).push(finding);
    }
  }

  const files = [
    ...filesByIndex.values(),
    ...[...findingsByPath].map(([path, findings]) => ({ path, findings })),
  ];

  if (!files.length) {
    return <p className="text-secondary mb-0">No per-file results were saved for this run.</p>;
  }

  return files.map((file) => (
    <section className="border rounded p-3 mb-3 bg-white" key={`${file.file_index ?? file.path}`}>
      <div className="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-2">
        <h6 className="fw-semibold mb-0 text-break">{file.path || 'Unknown file'}</h6>
        <span className="badge bg-light text-secondary border">{file.language || 'Language unavailable'}</span>
      </div>
      {file.ai_available === false && (
        <p className="small text-warning-emphasis mb-2">
          AI review was unavailable for this file.
        </p>
      )}
      {!file.findings.length ? (
        <p className="small text-secondary mb-0">No findings were saved for this file.</p>
      ) : file.findings.map((finding) => (
        <article className="border-top pt-3 mt-3" key={finding.id || `${finding.title}-${finding.line}`}>
          <div className="d-flex flex-wrap gap-2 align-items-center mb-1">
            <span className="badge bg-secondary-subtle text-secondary text-capitalize">
              {finding.severity || 'unknown'}
            </span>
            <strong>{finding.title || 'Finding'}</strong>
            <span className="small text-secondary">
              Line {Number.isInteger(finding.line) ? finding.line : '—'}
            </span>
          </div>
        </article>
      ))}
    </section>
  ));
}

Findings.propTypes = {
  analysis: PropTypes.shape({
    files: PropTypes.arrayOf(PropTypes.object),
    findings: PropTypes.arrayOf(PropTypes.object),
  }).isRequired,
};

export default function ProjectHistory() {
  const { currentUser } = useAuth();
  const [projects, setProjects] = useState([]);
  const [projectCursor, setProjectCursor] = useState(null);
  const [selectedProject, setSelectedProject] = useState(null);
  const [analyses, setAnalyses] = useState([]);
  const [analysisCursor, setAnalysisCursor] = useState(null);
  const [selectedAnalysis, setSelectedAnalysis] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');

  const loadProjects = useCallback(async (cursor = null, append = false) => {
    if (!currentUser) {
      setProjects([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const page = await getProjectHistory(currentUser, { pageSize: PAGE_SIZE, cursor });
      setProjects((current) => append ? [...current, ...page.items] : page.items);
      setProjectCursor(page.next_cursor);
    } catch (loadError) {
      setError(loadError.message || 'Could not load Project History.');
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [currentUser]);

  useEffect(() => {
    setSelectedProject(null);
    setSelectedAnalysis(null);
    loadProjects();
  }, [loadProjects]);

  const openProject = async (project) => {
    setSelectedProject(project);
    setSelectedAnalysis(null);
    setAnalyses([]);
    setAnalysisCursor(null);
    setLoading(true);
    setError('');
    try {
      const page = await getProjectAnalysisHistory(
        project.id,
        currentUser,
        { pageSize: PAGE_SIZE },
      );
      setAnalyses(page.items);
      setAnalysisCursor(page.next_cursor);
    } catch (loadError) {
      setError(loadError.message || 'Could not load this project’s analysis history.');
    } finally {
      setLoading(false);
    }
  };

  const openAnalysis = async (analysis) => {
    setLoading(true);
    setError('');
    try {
      const detail = await getProjectAnalysis(selectedProject.id, analysis.id, currentUser);
      setSelectedAnalysis(detail);
    } catch (loadError) {
      setError(loadError.message || 'Could not reopen this analysis.');
    } finally {
      setLoading(false);
    }
  };

  const loadMore = async () => {
    setLoadingMore(true);
    if (!selectedProject) {
      const cursor = projectCursor;
      if (!cursor) return setLoadingMore(false);
      await loadProjects(cursor, true);
      return;
    }
    const cursor = analysisCursor;
    if (!cursor) return setLoadingMore(false);
    try {
      const page = await getProjectAnalysisHistory(
        selectedProject.id,
        currentUser,
        { pageSize: PAGE_SIZE, cursor },
      );
      setAnalyses((current) => [...current, ...page.items]);
      setAnalysisCursor(page.next_cursor);
    } catch (loadError) {
      setError(loadError.message || 'Could not load more analysis history.');
    } finally {
      setLoadingMore(false);
    }
  };

  const goToProjects = () => {
    setSelectedProject(null);
    setSelectedAnalysis(null);
    setAnalyses([]);
    setError('');
  };

  const goToAnalyses = () => {
    setSelectedAnalysis(null);
    setError('');
  };

  const hasMore = selectedProject
    ? !selectedAnalysis && Boolean(analysisCursor)
    : Boolean(projectCursor);

  return (
    <div className="p-4 p-md-5">
      <div className="d-flex align-items-start justify-content-between flex-wrap gap-3 mb-4">
        <div>
          <h4 className="fw-bold text-dark mb-1">Project History</h4>
          <p className="text-secondary small mb-0">
            Reopen saved project analyses and findings.
          </p>
        </div>
        {!selectedProject && (
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            onClick={() => loadProjects()}
            disabled={loading}
          >
            <i className="bi bi-arrow-clockwise me-1" />Refresh
          </button>
        )}
      </div>

      {selectedAnalysis && (
        <button type="button" className="btn btn-link px-0 mb-3" onClick={goToAnalyses}>
          <i className="bi bi-arrow-left me-1" />Back to {selectedProject?.name} analyses
        </button>
      )}
      {selectedProject && !selectedAnalysis && (
        <button type="button" className="btn btn-link px-0 mb-3" onClick={goToProjects}>
          <i className="bi bi-arrow-left me-1" />Back to projects
        </button>
      )}

      {error && <div className="alert alert-danger small" role="alert">{error}</div>}
      {loading && (
        <div className="text-center py-4">
          <div className="spinner-border text-primary" role="status">
            <span className="visually-hidden">Loading…</span>
          </div>
        </div>
      )}

      {!loading && !error && !selectedProject && (
        projects.length ? (
          <div className="card border-0 shadow-sm">
            <div className="table-responsive">
              <table className="table table-hover align-middle mb-0">
                <thead className="table-light">
                  <tr>
                    <th>Project</th><th>Uploaded</th><th>Files</th>
                    <th>Analysis status</th><th>Issues</th><th />
                  </tr>
                </thead>
                <tbody>
                  {projects.map((project) => (
                    <tr key={project.id}>
                      <td className="fw-medium">{project.name || 'Unnamed project'}</td>
                      <td>{formatDate(project.created_at)}</td>
                      <td>{project.file_count ?? '—'}</td>
                      <td>{statusLabel(project.analysis_status || project.status)}</td>
                      <td>{project.issue_count ?? 0}</td>
                      <td>
                        <button className="btn btn-sm btn-outline-primary" onClick={() => openProject(project)}>
                          View analyses
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="alert alert-info">No uploaded projects have been saved to Project History.</div>
        )
      )}

      {!loading && !error && selectedProject && !selectedAnalysis && (
        <div className="card border-0 shadow-sm">
          <div className="card-header bg-white">
            <h5 className="mb-1">{selectedProject.name}</h5>
            <span className="small text-secondary">{selectedProject.file_count ?? 0} files · analysis runs</span>
          </div>
          {analyses.length ? (
            <div className="list-group list-group-flush">
              {analyses.map((analysis) => (
                <button
                  type="button"
                  className="list-group-item list-group-item-action d-flex justify-content-between align-items-center flex-wrap gap-2"
                  key={analysis.id}
                  onClick={() => openAnalysis(analysis)}
                >
                  <span>
                    <strong>{formatDate(analysis.created_at)}</strong>
                    <span className="small text-secondary ms-3">{analysis.analyzed_files ?? 0} analyzed files</span>
                  </span>
                  <span className="d-flex align-items-center gap-3">
                    <span className="badge bg-light text-secondary border">{statusLabel(analysis.status)}</span>
                    <span>{analysis.issue_count ?? 0} issues</span>
                    <i className="bi bi-chevron-right" />
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <div className="card-body text-secondary">No saved analyses for this project.</div>
          )}
        </div>
      )}

      {!loading && !error && selectedAnalysis && (
        <div>
          <div className="card border-0 shadow-sm mb-4">
            <div className="card-body">
              <div className="d-flex justify-content-between flex-wrap gap-2">
                <div>
                  <h5 className="fw-bold mb-1">{selectedProject.name}</h5>
                  <div className="small text-secondary">
                    Run {formatDate(selectedAnalysis.created_at)} · {statusLabel(selectedAnalysis.status)}
                  </div>
                </div>
                <span className="badge bg-light text-secondary border align-self-start">
                  {selectedAnalysis.issue_count ?? 0} issues
                </span>
              </div>
              {!selectedAnalysis.source_available && (
                <div className="alert alert-warning small mt-3 mb-0" role="status">
                  Source files are no longer available. Saved findings and quality scores remain accessible;
                  upload the project again to run a new analysis.
                </div>
              )}
              {selectedAnalysis.source_available && selectedAnalysis.source_storage === 'local' && (
                <div className="alert alert-info small mt-3 mb-0" role="status">
                  Source files are available only in temporary backend storage and may expire.
                </div>
              )}
            </div>
          </div>

          {selectedAnalysis.quality_scores?.length > 0 && (
            <div className="card border-0 shadow-sm mb-4">
              <div className="card-body">
                <h6 className="fw-bold">Quality scores</h6>
                <div className="d-flex flex-wrap gap-3">
                  {selectedAnalysis.quality_scores.map((score) => (
                    <div className="border rounded p-3" key={score.id}>
                      <strong>{score.score ?? '—'}</strong>
                      <span className="small text-secondary ms-1">/ 100</span>
                      <div className="small text-secondary">{formatDate(score.created_at)}</div>
                      {!score.analysis_id && <div className="small text-muted">Older score, run not linked</div>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          <h5 className="fw-bold mb-3">Saved findings</h5>
          <p className="small text-secondary">
            Detailed AI text and code examples are not retained in Project History to protect source code and credentials.
          </p>
          <Findings analysis={selectedAnalysis} />
        </div>
      )}

      {!loading && !error && hasMore && (
        <div className="text-center mt-4">
          <button
            type="button"
            className="btn btn-outline-primary"
            onClick={loadMore}
            disabled={loadingMore}
          >
            {loadingMore ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}
    </div>
  );
}
