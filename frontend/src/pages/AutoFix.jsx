import { useState, useRef, useCallback } from 'react';
import PropTypes from 'prop-types';
import { useAuth } from '../context/AuthContext';
import CodeComparison from '../components/CodeComparison';
import { buildAnalysisBatches } from '../utils/analysisBatches';
import { apiUrl } from '../services/api';
import '../styles/AutoFix.css';
import '../styles/CodeReview.css';

const LANGUAGES = [
  'Python', 'JavaScript', 'TypeScript', 'Java', 'C#',
  'C++', 'Go', 'Rust', 'Ruby', 'PHP', 'Swift', 'Kotlin', 'Other',
];

const ACCEPTED_EXTS = '.py,.js,.ts,.jsx,.tsx,.java,.cs,.go,.rb,.php,.cpp,.c,.rs,.kt,.swift,.zip';
const PROJECT_UPLOAD_RESUME_KEY = 'codeReviewAgent.projectUploadResume';

function formatBytes(bytes) {
  if (bytes >= 1024 ** 3) return `${(bytes / (1024 ** 3)).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / (1024 ** 2)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} bytes`;
}

async function responseError(response) {
  const body = await response.json().catch(() => null);
  return body?.detail || `Request failed (HTTP ${response.status}).`;
}

function folderManifest(files) {
  return files.map((item) => ({
    path: item.webkitRelativePath || item.name,
    size: item.size,
    lastModified: item.lastModified,
  })).sort((left, right) => left.path.localeCompare(right.path));
}

function sameManifest(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

// ── helpers ───────────────────────────────────────────────────────────────────
function diffLines(original, fixed) {
  const origLines  = (original || '').split('\n');
  const fixedLines = (fixed   || '').split('\n');
  const maxLen     = Math.max(origLines.length, fixedLines.length);
  const result     = [];
  for (let i = 0; i < maxLen; i++) {
    const o = origLines[i] ?? '';
    const f = fixedLines[i] ?? '';
    if (o !== f) result.push({ line: i + 1, original: o, fixed: f });
  }
  return result;
}

const SEVERITY_COLORS = {
  critical: 'danger',
  error:    'danger',
  warning:  'warning',
  info:     'secondary',
}

function ProjectQualityScore({ qualityScore }) {
  const scoreColor = qualityScore.score >= 80 ? 'success' : qualityScore.score >= 60 ? 'warning' : 'danger';
  return (
    <section className="card border-0 shadow-sm mb-4" aria-label="Project quality score">
      <div className="card-body p-4">
        <div className="d-flex align-items-center gap-3 flex-wrap">
          <div className={`quality-score-value text-${scoreColor}`}>
            {qualityScore.score}<span className="fs-6 text-secondary">/100</span>
          </div>
          <div>
            <h6 className="fw-bold mb-1">Project quality score</h6>
            <p className="small text-secondary mb-0">
              Calculated from confirmed findings and measured Python complexity in the analyzed files.
              {` Coverage: ${qualityScore.coverage.analyzed}/${qualityScore.coverage.selected} selected files.`}
            </p>
          </div>
        </div>
        <div className="d-flex flex-wrap gap-3 mt-4">
          {Object.entries(qualityScore.categories || {}).map(([category, value]) => (
            <div className="score-category" key={category}>
              <div className="d-flex justify-content-between small mb-1 text-capitalize">
                <span>{category}</span><strong>{value}/100</strong>
              </div>
              <div className="progress" role="progressbar" aria-label={`${category} score`} aria-valuenow={value} aria-valuemin="0" aria-valuemax="100">
                <div className={`progress-bar bg-${value >= 80 ? 'success' : value >= 60 ? 'warning' : 'danger'}`} style={{ width: `${value}%` }} />
              </div>
            </div>
          ))}
        </div>
        <details className="mt-3">
          <summary className="small fw-semibold" style={{ cursor: 'pointer' }}>How this score was calculated</summary>
          <p className="small text-secondary mt-2 mb-1">
            {qualityScore.method.notes} Severity penalties: critical 20, error 12, warning 5,
            info 1; Python complexity above 10 adds 2 points per step, capped at 30 per file.
          </p>
          <p className="small text-secondary mb-1">
            Measured {qualityScore.metrics.nonblank_lines} nonblank lines across {qualityScore.metrics.analyzed_files} files.
            {' '}Maximum measured Python function complexity: {
              qualityScore.metrics.complexity_measurable
                ? qualityScore.metrics.max_cyclomatic_complexity
                : 'not available'
            }.
          </p>
          {qualityScore.coverage.failed > 0 && (
            <p className="small text-warning mb-1">
              {qualityScore.coverage.failed} file(s) failed analysis and are not represented in this score.
            </p>
          )}
          {qualityScore.factors.length > 0 && (
            <ul className="small text-secondary mb-0">
              {qualityScore.factors.map((factor) => <li key={factor}>{factor}</li>)}
            </ul>
          )}
        </details>
      </div>
    </section>
  );
}

function ProjectFindings({ results, fixProposals, loading, onGenerateFix, onApplyFix, onRejectFix }) {
  return (
    <section className="mb-4" aria-label="Project analysis findings">
      <div className="d-flex align-items-center gap-2 mb-3">
        <div>
          <p className="small text-secondary mb-1">Step 6 · AutoFix Issues</p>
          <h5 className="fw-bold mb-0">Select a finding to fix or review a proposal</h5>
        </div>
        <span className="badge bg-secondary">{results.reduce((total, file) => total + file.issues.length, 0)}</span>
      </div>
      {results.map((file) => (
        <article className="card border-0 shadow-sm mb-3" key={file.file_index}>
          <div className="card-header bg-white d-flex justify-content-between align-items-center flex-wrap gap-2">
            <code className="text-primary fw-semibold text-break">{file.path}</code>
            <div className="d-flex gap-2">
              <span className="badge bg-light text-secondary border text-capitalize">{file.language}</span>
              {!file.ai_available && <span className="badge bg-warning-subtle text-warning">Static analysis only</span>}
              {file.ai_code_truncated && <span className="badge bg-secondary-subtle text-secondary">AI reviewed first 12,000 characters</span>}
            </div>
          </div>
          <div className="card-body">
            {file.summary && <p className="small text-secondary">{file.summary}</p>}
            {file.issues.length === 0 ? (
              <p className="small text-success mb-0">
                <i className="bi bi-check-circle-fill me-2" />No findings for this file.
              </p>
            ) : file.issues.map((issue, issueIndex) => {
              const proposalKey = `${file.file_index}:${issueIndex}`;
              const proposal = fixProposals[proposalKey];
              const color = SEVERITY_COLORS[issue.severity] || 'secondary';
              return (
                <div className="border rounded p-3 mb-3" key={proposalKey}>
                  <div className="d-flex align-items-center gap-2 flex-wrap mb-2">
                    <span className={`badge bg-${color}-subtle text-${color} text-capitalize`}>
                      {issue.severity || 'info'}
                    </span>
                    <span className="badge bg-light text-secondary border text-capitalize">{issue.category}</span>
                    {issue.line > 0 && <span className="small text-muted">Line {issue.line}</span>}
                    {issue.source && <span className="small text-muted">Source: {issue.source}</span>}
                    {issue.team_standard && (
                      <span className="badge bg-danger-subtle text-danger">
                        <i className="bi bi-shield-exclamation me-1"></i>
                        Team Standard: {issue.standard_name || issue.title?.replace('[Team Standard] ', '')}
                      </span>
                    )}
                  </div>
                  <h6 className="small fw-semibold mb-1">{issue.title}</h6>
                  {(issue.explanation || issue.description) && (
                    <p className="small text-secondary mb-2">{issue.explanation || issue.description}</p>
                  )}
                  {issue.recommendation && (
                    <p className="small text-success mb-2">{issue.recommendation}</p>
                  )}
                  {!proposal || proposal.status === 'error' ? (
                    <>
                    {proposal?.error && <p className="small text-danger mb-2">{proposal.error}</p>}
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-primary"
                      onClick={() => onGenerateFix(file.file_index, issueIndex)}
                      disabled={loading}
                    >
                      {proposal?.status === 'error' ? 'Try again' : 'Fix this issue'}
                    </button>
                    </>
                  ) : proposal.status === 'generating' ? (
                    <span className="small text-secondary" role="status">
                      <span className="spinner-border spinner-border-sm me-2" />Generating fix…
                    </span>
                  ) : proposal.status === 'rejected' ? (
                    <span className="small text-secondary">Suggestion rejected.</span>
                  ) : proposal.status === 'applied' ? (
                    <div className="mt-3">
                      <div className="alert alert-success py-2 small mb-2" role="status">
                        <i className="bi bi-check-circle me-2"></i>
                        Fix applied to the uploaded project copy. Re-run analysis to confirm the finding is resolved.
                      </div>
                      {proposal.summary && <p className="small text-secondary mb-2">{proposal.summary}</p>}
                      <p className="small text-secondary mb-0">
                        {proposal.validation?.supported
                          ? (proposal.validation?.passed ? 'Static validation passed before this change was applied.' : 'This fix was applied after an explicit approval override.')
                          : 'This fix was applied after an explicit approval because it was not statically verified.'}
                      </p>
                    </div>
                  ) : (
                    <div className="mt-3">
                    <p className="small text-secondary mb-2">Step 7 · Review and apply fix</p>
                      <p className={`small ${proposal.validation.passed === false ? 'text-danger' : 'text-secondary'}`}>
                        {proposal.validation.supported
                          ? (proposal.validation.passed ? 'Static validation passed. ' : 'Static validation failed. ')
                          : 'Unverified proposal. '}
                        {proposal.validation.message}
                      </p>
                      {proposal.summary && <p className="small text-secondary">{proposal.summary}</p>}
                      {!proposal.changed && (
                        <p className="small text-warning">
                          No code changes were proposed; the original file remains unchanged.
                        </p>
                      )}
                      <CodeComparison
                        original={proposal.original_code}
                        proposed={proposal.proposed_code}
                        language={proposal.language}
                        startLine={proposal.line_range?.start || 1}
                      />
                      <div className="d-flex gap-2 flex-wrap mt-3">
                        <button
                          type="button"
                          className={`btn btn-sm ${proposal.validation.supported && proposal.validation.passed ? 'btn-success' : 'btn-warning'}`}
                          onClick={() => onApplyFix(
                            proposal,
                            !proposal.validation.supported || proposal.validation.passed !== true,
                            proposalKey,
                          )}
                          disabled={
                            loading
                            || !proposal.changed
                          }
                        >
                          {proposal.validation.supported && proposal.validation.passed
                            ? 'Apply fix'
                            : 'Approve and apply unverified fix'}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-secondary"
                          onClick={() => onRejectFix(proposalKey)}
                          disabled={loading}
                        >
                          Reject suggestion
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </article>
      ))}
    </section>
  );
}

ProjectFindings.propTypes = {
  results: PropTypes.arrayOf(PropTypes.object).isRequired,
  fixProposals: PropTypes.object.isRequired,
  loading: PropTypes.bool.isRequired,
  onGenerateFix: PropTypes.func.isRequired,
  onApplyFix: PropTypes.func.isRequired,
  onRejectFix: PropTypes.func.isRequired,
};

ProjectQualityScore.propTypes = {
  qualityScore: PropTypes.shape({
    score: PropTypes.number.isRequired,
    categories: PropTypes.object,
    factors: PropTypes.arrayOf(PropTypes.string),
    metrics: PropTypes.object.isRequired,
    method: PropTypes.object.isRequired,
    coverage: PropTypes.object.isRequired,
  }).isRequired,
};
function ChangeTable({ changes }) {
  if (!changes?.length) return (
    <p className="text-secondary small mb-0">
      <i className="bi bi-check-circle-fill text-success me-2"></i>
      No line-level changes reported.
    </p>
  );
  return (
    <div className="table-responsive">
      <table className="table table-sm table-bordered align-middle small mb-0">
        <thead className="table-dark">
          <tr>
            <th style={{ width: 60 }}>Line</th>
            <th>Original</th>
            <th>Fixed</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((c, i) => (
            <tr key={i}>
              <td className="text-center fw-medium">{c.line || '—'}</td>
              <td><code className="text-danger small">{c.original}</code></td>
              <td><code className="text-success small">{c.fixed}</code></td>
              <td className="text-secondary">{c.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

ChangeTable.propTypes = {
  changes: PropTypes.arrayOf(PropTypes.shape({
    line: PropTypes.number,
    original: PropTypes.string,
    fixed: PropTypes.string,
    reason: PropTypes.string,
  })).isRequired,
};

// ── CodeDiff ──────────────────────────────────────────────────────────────────
function CodeDiff({ original, fixed }) {
  const diffs    = diffLines(original, fixed);
  const diffNums = new Set(diffs.map(d => d.line));
  const lines    = (fixed || '').split('\n');
  return (
    <div className="code-diff font-monospace small">
      {lines.map((line, i) => {
        const lineNum = i + 1;
        const changed = diffNums.has(lineNum);
        return (
          <div key={lineNum} className={`diff-line${changed ? ' diff-changed' : ''}`}>
            <span className="diff-lineno">{lineNum}</span>
            <span className="diff-marker">{changed ? '~' : ' '}</span>
            <span>{line}</span>
          </div>
        );
      })}
    </div>
  );
}

CodeDiff.propTypes = {
  original: PropTypes.string,
  fixed: PropTypes.string,
};

// ── SingleFileResult ──────────────────────────────────────────────────────────
function SingleFileResult({ result, onDownloadSingle }) {
  const [tab, setTab] = useState('diff');
  const hasChanges = result.original_code !== result.fixed_code;
  const issuesFound = result.issues_found ?? result.issues?.length ?? 0;

  return (
    <div className="card border-0 shadow-sm mb-4">
      <div className="card-header bg-white border-bottom d-flex align-items-center justify-content-between flex-wrap gap-2 py-3 px-4">
        <div className="d-flex align-items-center gap-3 flex-wrap">
          <code className="text-primary fw-semibold">{result.filename || result.path}</code>
          <span className="badge bg-secondary-subtle text-secondary">{result.language}</span>
          {issuesFound > 0 && (
            <span className="badge bg-danger-subtle text-danger">
              <i className="bi bi-exclamation-triangle me-1"></i>
              {issuesFound} issue{issuesFound !== 1 ? 's' : ''} found
            </span>
          )}
          {hasChanges ? (
            <span className="badge bg-info-subtle text-info">
              <i className="bi bi-pencil-square me-1"></i>Fix proposed — verify
            </span>
          ) : issuesFound > 0 ? (
            <span className="badge bg-danger-subtle text-danger">
              <i className="bi bi-x-circle me-1"></i>Not fixed
            </span>
          ) : (
            <span className="badge bg-success-subtle text-success">
              <i className="bi bi-check-circle me-1"></i>No findings
            </span>
          )}
          {!result.ai_available && (
            <span className="badge bg-warning-subtle text-warning">
              <i className="bi bi-cpu me-1"></i>Static only
            </span>
          )}
        </div>
        <button className="btn btn-sm btn-outline-primary" onClick={() => onDownloadSingle(result)}>
          <i className="bi bi-download me-1"></i>{hasChanges ? 'Download proposed file' : 'Download original file'}
        </button>
      </div>

      {hasChanges ? (
        <div className="px-4 pt-3 pb-0">
          <div className="alert alert-info py-2 small mb-0" role="status">
            <i className="bi bi-info-circle me-2"></i>
            A change was proposed. Review it, then rerun analysis on the proposed code to check whether the findings are resolved.
          </div>
        </div>
      ) : issuesFound > 0 ? (
        <div className="px-4 pt-3 pb-0">
          <div className="alert alert-danger py-2 small mb-0" role="status">
            <i className="bi bi-exclamation-circle me-2"></i>
            These findings are not fixed. No code changes were made; the code below is unchanged from the original.
          </div>
        </div>
      ) : (
        <div className="px-4 pt-3 pb-0">
          <div className="alert alert-success py-2 small mb-0" role="status">
            <i className="bi bi-check-circle me-2"></i>No findings were reported, and no code changes were made.
          </div>
        </div>
      )}

      {result.summary && (
        <div className="px-4 pt-3 pb-0">
          <div className="alert alert-info py-2 small mb-0">
            <i className="bi bi-chat-text me-2"></i>{result.summary}
          </div>
        </div>
      )}

      {result.error && result.summary !== `Auto-fix unavailable: ${result.error}` && (
        <div className="px-4 pt-3 pb-0">
          <div className="alert alert-warning py-2 small mb-0">
            <i className="bi bi-exclamation-triangle me-2"></i>{result.error}
          </div>
        </div>
      )}

      <div className="card-body p-4">
        <ul className="nav nav-tabs mb-3">
          {[
            { key: 'diff',     label: hasChanges ? 'Proposed Code' : 'Current Code', icon: 'bi-file-earmark-code' },
            { key: 'changes',  label: `Changes (${result.changes?.length ?? 0})`, icon: 'bi-list-ul' },
            { key: 'original', label: 'Original',      icon: 'bi-file-earmark' },
          ].map(({ key, label, icon }) => (
            <li key={key} className="nav-item">
              <button
                className={`nav-link ${tab === key ? 'active' : ''}`}
                onClick={() => setTab(key)}
              >
                <i className={`bi ${icon} me-1`}></i>{label}
              </button>
            </li>
          ))}
        </ul>

        {tab === 'diff'     && <CodeDiff original={result.original_code} fixed={result.fixed_code} />}
        {tab === 'changes'  && <ChangeTable changes={result.changes} />}
        {tab === 'original' && (
          <pre className="code-block font-monospace small mb-0">{result.original_code}</pre>
        )}
      </div>
    </div>
  );
}

SingleFileResult.propTypes = {
  result: PropTypes.shape({
    filename: PropTypes.string,
    path: PropTypes.string,
    language: PropTypes.string,
    issues_found: PropTypes.number,
    issues: PropTypes.arrayOf(PropTypes.object),
    ai_available: PropTypes.bool,
    summary: PropTypes.string,
    error: PropTypes.string,
    changes: PropTypes.arrayOf(PropTypes.shape({
      line: PropTypes.number,
      original: PropTypes.string,
      fixed: PropTypes.string,
      reason: PropTypes.string,
    })),
    original_code: PropTypes.string,
    fixed_code: PropTypes.string,
  }).isRequired,
  onDownloadSingle: PropTypes.func.isRequired,
};

// ── Main AutoFix page ─────────────────────────────────────────────────────────
export default function AutoFix() {
  const { currentUser } = useAuth();
  const [mode, setMode]           = useState('upload');  // 'upload' | 'manual'
  const [dragOver, setDragOver]   = useState(false);
  const [file, setFile]           = useState(null);
  const [folderFiles, setFolderFiles] = useState([]);
  const [folderName, setFolderName] = useState('');
  const [uploadId, setUploadId] = useState('');
  const [uploadProgress, setUploadProgress] = useState({ uploaded: 0, total: 0 });
  const [uploadStatus, setUploadStatus] = useState('');
  const [projectFiles, setProjectFiles] = useState([]);
  const [selectedProjectFiles, setSelectedProjectFiles] = useState([]);
  const [analysisLimits, setAnalysisLimits] = useState({
    maximum_files: 200,
    maximum_request_files: 20,
    maximum_batch_bytes: 64 * 1024 * 1024,
    maximum_file_bytes: 20 * 1024 * 1024,
  });
  const [analysisSummary, setAnalysisSummary] = useState(null);
  const [projectWasAnalyzed, setProjectWasAnalyzed] = useState(false);
  const [qualityScore, setQualityScore] = useState(null);
  const [fixProposals, setFixProposals] = useState({});
  const [fixStatus, setFixStatus] = useState('');
  const [lastBackup, setLastBackup] = useState(null);
  const [lastAppliedFile, setLastAppliedFile] = useState(null);
  const [manualCode, setManualCode] = useState('');
  const [manualLang, setManualLang] = useState('Python');
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState('');
  const [results, setResults]     = useState(null);
  const activeFixRequestsRef = useRef(new Set());
  const fileInputRef = useRef();
  const folderInputRef = useRef();

  function resetState() {
    setError('');
    setResults(null);
    setAnalysisSummary(null);
    setQualityScore(null);
    setFixProposals({});
    setFixStatus('');
  }

  function clearProjectUpload() {
    setFolderFiles([]);
    setFolderName('');
    setUploadId('');
    setUploadProgress({ uploaded: 0, total: 0 });
    setUploadStatus('');
    setProjectFiles([]);
    setSelectedProjectFiles([]);
    setProjectWasAnalyzed(false);
    setQualityScore(null);
    setFixProposals({});
    setLastBackup(null);
    setLastAppliedFile(null);
  }

  // ── file drag / select ────────────────────────────────────────────────
  function handleFileDrop(e) {
    e.preventDefault();
    setDragOver(false);
    const droppedFiles = Array.from(e.dataTransfer.files).sort((left, right) => (
      (left.webkitRelativePath || left.name).localeCompare(right.webkitRelativePath || right.name)
    ));
    if (droppedFiles.length > 1 || droppedFiles.some(item => item.webkitRelativePath)) {
      setFile(null);
      setFolderFiles(droppedFiles);
      setFolderName(
        (droppedFiles[0]?.webkitRelativePath || droppedFiles[0]?.name || 'Dropped project').split('/')[0],
      );
      setUploadId('');
      setProjectFiles([]);
      setSelectedProjectFiles([]);
      setProjectWasAnalyzed(false);
    } else if (droppedFiles[0]) {
      clearProjectUpload();
      setFile(droppedFiles[0]);
    }
    resetState();
  }

  function handleFileSelect(e) {
    const selected = e.target.files[0];
    if (selected) {
      clearProjectUpload();
      setFile(selected);
      resetState();
    }
    e.target.value = '';
  }

  function handleFolderSelect(e) {
    const selectedFiles = Array.from(e.target.files).sort((left, right) => (
      (left.webkitRelativePath || left.name).localeCompare(right.webkitRelativePath || right.name)
    ));
    if (!selectedFiles.length) {
      setError('The selected folder is empty or contains no files the browser can upload.');
      e.target.value = '';
      return;
    }
    const relativePath = selectedFiles[0].webkitRelativePath || selectedFiles[0].name;
    const selectedFolderName = relativePath.split('/')[0] || 'Project';
    const manifest = folderManifest(selectedFiles);
    let restoredUploadId = '';
    try {
      const saved = JSON.parse(localStorage.getItem(PROJECT_UPLOAD_RESUME_KEY) || 'null');
      if (
        saved?.userId === currentUser?.uid
        && sameManifest(saved.files, manifest)
      ) {
        restoredUploadId = saved.uploadId;
      }
    } catch (storageError) {
      console.warn('Could not read the saved project-upload session.', storageError);
    }
    setFile(null);
    setFolderFiles(selectedFiles);
    setFolderName(selectedFolderName);
    setUploadId(restoredUploadId);
    setUploadProgress({
      uploaded: 0,
      total: selectedFiles.reduce((total, item) => total + item.size, 0),
    });
    setUploadStatus(restoredUploadId ? 'Ready to resume the previous upload.' : '');
    setProjectFiles([]);
    setSelectedProjectFiles([]);
    setProjectWasAnalyzed(false);
    setQualityScore(null);
    setFixProposals({});
    setLastBackup(null);
    resetState();
    e.target.value = '';
  }

  // ── upload submit ─────────────────────────────────────────────────────
  const handleUploadSubmit = useCallback(async () => {
    if (!file) return;
    setLoading(true); resetState();
    try {
      if (!currentUser) throw new Error('Sign in with Firebase before uploading a file.');
      const token = await currentUser.getIdToken();
      const fd = new FormData();
      fd.append('file', file);
      fd.append('download', 'false');
      const res = await fetch(apiUrl('/api/fix/upload'), {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => `HTTP ${res.status}`);
        throw new Error(txt);
      }
      const data = await res.json();
      setResults(Array.isArray(data) ? data : [data]);
    } catch (err) {
      setError(err.message || 'Upload failed. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [currentUser, file]);

  const handleFolderUpload = useCallback(async () => {
    if (!folderFiles.length) return;
    if (!currentUser) {
      setError('Sign in with Firebase before uploading a project folder.');
      return;
    }
    setLoading(true);
    setError('');
    setResults(null);
    setAnalysisSummary(null);
    setUploadStatus('Preparing upload…');
    let activeUploadId = uploadId;
    const totalBytes = folderFiles.reduce((total, item) => total + item.size, 0);
    const filesManifest = folderManifest(folderFiles);
    try {
      if (!globalThis.crypto?.subtle) {
        throw new Error('Folder uploads require HTTPS or a localhost secure browser context.');
      }
      const token = await currentUser.getIdToken();
      const headers = { Authorization: `Bearer ${token}` };
      let status = null;
      if (activeUploadId) {
        const statusResponse = await fetch(apiUrl(`/api/fix/project-uploads/${activeUploadId}`), { headers });
        if (statusResponse.ok) {
          status = await statusResponse.json();
          const serverManifest = status.files
            .map(({ path, size }) => ({ path, size }))
            .sort((left, right) => left.path.localeCompare(right.path));
          const selectedManifest = filesManifest.map(({ path, size }) => ({ path, size }));
          if (!sameManifest(serverManifest, selectedManifest)) status = null;
        } else if (statusResponse.status !== 404 && statusResponse.status !== 410) {
          throw new Error(await responseError(statusResponse));
        }
        if (!status) {
          activeUploadId = '';
          setUploadId('');
        }
      }
      if (!activeUploadId) {
        setUploadStatus('Starting secure upload…');
        const createResponse = await fetch(apiUrl('/api/fix/project-uploads'), {
          method: 'POST',
          headers: { ...headers, 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: folderName || 'project',
            files: filesManifest.map(({ path, size }) => ({ path, size })),
          }),
        });
        if (!createResponse.ok) throw new Error(await responseError(createResponse));
        status = await createResponse.json();
        activeUploadId = status.upload_id;
        setUploadId(activeUploadId);
        try {
          localStorage.setItem(PROJECT_UPLOAD_RESUME_KEY, JSON.stringify({
            uploadId: activeUploadId,
            userId: currentUser.uid,
            files: filesManifest,
          }));
        } catch (storageError) {
          console.warn('Could not save the project-upload resume session.', storageError);
        }
      }

      const chunkSize = status.chunk_bytes;
      const uploadedBytes = (fileStatus) => fileStatus.complete
        ? fileStatus.size
        : fileStatus.received_chunks.reduce((total, index) => (
          total + Math.min(chunkSize, fileStatus.size - index * chunkSize)
        ), 0);
      let uploaded = status.files.reduce((total, item) => total + uploadedBytes(item), 0);
      setUploadProgress({ uploaded, total: totalBytes });
      setUploadStatus('Uploading project files…');

      for (const fileStatus of status.files) {
        if (fileStatus.complete) continue;
        const selectedFile = folderFiles[fileStatus.index];
        if (!selectedFile || selectedFile.size !== fileStatus.size) {
          throw new Error(`Could not match the selected file "${fileStatus.path}" to the upload session.`);
        }
        const receivedChunks = new Set(fileStatus.received_chunks);
        const count = Math.ceil(fileStatus.size / chunkSize);
        for (let chunkIndex = 0; chunkIndex < count; chunkIndex += 1) {
          if (receivedChunks.has(chunkIndex)) continue;
          const start = chunkIndex * chunkSize;
          const chunk = selectedFile.slice(start, Math.min(start + chunkSize, selectedFile.size));
          const buffer = await chunk.arrayBuffer();
          const digest = await crypto.subtle.digest('SHA-256', buffer);
          const checksum = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
          const chunkResponse = await fetch(
            apiUrl(`/api/fix/project-uploads/${activeUploadId}/files/${fileStatus.index}/chunks/${chunkIndex}`),
            {
              method: 'PUT',
              headers: { ...headers, 'X-Chunk-SHA256': checksum },
              body: chunk,
            },
          );
          if (!chunkResponse.ok) throw new Error(await responseError(chunkResponse));
          uploaded += chunk.size;
          setUploadProgress({ uploaded, total: totalBytes });
        }
      }

      setUploadStatus('Finalizing project…');
      const completeResponse = await fetch(apiUrl(`/api/fix/project-uploads/${activeUploadId}/complete`), {
        method: 'POST',
        headers,
      });
      if (!completeResponse.ok) throw new Error(await responseError(completeResponse));
      const filesResponse = await fetch(apiUrl(`/api/fix/project-uploads/${activeUploadId}/files`), { headers });
      if (!filesResponse.ok) throw new Error(await responseError(filesResponse));
      const project = await filesResponse.json();
      setProjectFiles(project.files);
      setAnalysisLimits(project.analysis_limits);
      setSelectedProjectFiles([]);
      setUploadProgress({ uploaded: totalBytes, total: totalBytes });
      const storageStatus = project.storage_backend === 'firebase_storage'
        ? 'Files are stored in Firebase Cloud Storage.'
        : 'Cloud Storage is not configured; files are available only in temporary backend storage.';
      const metadataStatus = project.metadata_persisted
        ? 'Project metadata is saved in Firestore.'
        : 'Firestore project metadata could not be confirmed; check backend logs and Firebase configuration.';
      setUploadStatus(`Upload complete. ${storageStatus} ${metadataStatus} Select source files to analyze.`);
    } catch (err) {
      setUploadStatus(
        activeUploadId
          ? 'Upload interrupted. Received chunks are saved for retry.'
          : 'Upload could not start.',
      );
      setError(err.message || 'Project upload failed.');
    } finally {
      setLoading(false);
    }
  }, [currentUser, folderFiles, folderName, uploadId]);

  const handleArchiveProjectUpload = useCallback(async () => {
    if (!file || !file.name.toLowerCase().endsWith('.zip')) return;
    if (!currentUser) {
      setError('Sign in with Firebase before uploading a project archive.');
      return;
    }
    setLoading(true);
    setError('');
    resetState();
    setUploadStatus('Uploading and validating ZIP archive…');
    try {
      const token = await currentUser.getIdToken();
      const formData = new FormData();
      formData.append('file', file);
      const response = await fetch(apiUrl('/api/fix/project-uploads/archive'), {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });
      if (!response.ok) throw new Error(await responseError(response));
      const project = await response.json();
      setUploadId(project.upload_id);
      setFolderName(project.name);
      setProjectFiles(project.files);
      setAnalysisLimits(project.analysis_limits);
      setSelectedProjectFiles([]);
      setProjectWasAnalyzed(false);
      setUploadProgress({ uploaded: file.size, total: file.size });
      const storageStatus = project.storage_backend === 'firebase_storage'
        ? 'Files are stored in Firebase Cloud Storage.'
        : 'Cloud Storage is not configured; files are available only in temporary backend storage.';
      const metadataStatus = project.metadata_persisted
        ? 'Project metadata is saved in Firestore.'
        : 'Firestore project metadata could not be confirmed; check backend logs and Firebase configuration.';
      setUploadStatus(`ZIP project uploaded. ${storageStatus} ${metadataStatus} Select source files to analyze.`);
    } catch (uploadError) {
      setUploadStatus('ZIP project upload failed.');
      setError(uploadError.message || 'Could not upload the ZIP project.');
    } finally {
      setLoading(false);
    }
  }, [currentUser, file]);

  const handleAnalyzeProject = useCallback(async (filesOverride = null) => {
    if (!uploadId || !selectedProjectFiles.length || !currentUser) return false;
    setLoading(true);
    setError('');
    setResults([]);
    setAnalysisSummary(null);
    setQualityScore(null);
    setFixProposals({});
    setProjectWasAnalyzed(false);
    let batches;
    try {
      batches = buildAnalysisBatches(
        selectedProjectFiles,
        filesOverride || projectFiles,
        analysisLimits,
      );
    } catch (batchError) {
      setError(batchError.message || 'The selected files exceed the analysis request limits.');
      setLoading(false);
      return false;
    }
    const totalBatches = batches.length;
    const accumulatedResults = [];
    let accumulatedSkipped = [];
    let accumulatedErrors = [];
    let analysisComplete = false;
    try {
      const token = await currentUser.getIdToken();
      for (let batchIndex = 0; batchIndex < totalBatches; batchIndex += 1) {
        const batch = batches[batchIndex];
        setUploadStatus(`Analyzing request ${batchIndex + 1} of ${totalBatches} (${batch.length} files)…`);
        const response = await fetch(apiUrl(`/api/fix/project-uploads/${uploadId}/analyze`), {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            file_indices: batch,
            reset: batchIndex === 0,
            selected_file_indices: batchIndex === 0 ? selectedProjectFiles : undefined,
          }),
        });
        if (!response.ok) throw new Error(await responseError(response));
        const data = await response.json();
        accumulatedResults.push(...data.results);
        accumulatedSkipped = data.summary.skipped_files;
        accumulatedErrors = data.summary.errors;
        analysisComplete = data.summary.complete;
        setResults([...accumulatedResults]);
        setAnalysisSummary({
          selected: selectedProjectFiles.length,
          analyzed: accumulatedResults.length,
          skipped: accumulatedSkipped.length,
          not_selected: Math.max(0, projectFiles.length - selectedProjectFiles.length),
          skipped_files: [...accumulatedSkipped],
          errors: [...accumulatedErrors],
          complete: data.summary.complete,
          languages: data.summary.languages,
          historyPersisted: data.summary.history_persisted,
        });
      }
      setUploadStatus('Analysis complete.');
      setProjectWasAnalyzed(true);
      return analysisComplete && accumulatedResults.length > 0;
    } catch (err) {
      setUploadStatus(`Analysis stopped after ${accumulatedResults.length} of ${selectedProjectFiles.length} files.`);
      setError(err.message || 'Project analysis failed.');
      return false;
    } finally {
      setLoading(false);
    }
  }, [
    analysisLimits,
    currentUser,
    projectFiles,
    selectedProjectFiles,
    uploadId,
  ]);

  const handleCheckQualityScore = useCallback(async (analysisComplete = analysisSummary?.complete) => {
    if (!uploadId || !analysisComplete || !currentUser) return false;
    setLoading(true);
    setError('');
    setUploadStatus('Calculating project quality score…');
    try {
      const token = await currentUser.getIdToken();
      const response = await fetch(apiUrl(`/api/fix/project-uploads/${uploadId}/quality-score`), {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error(await responseError(response));
      setQualityScore(await response.json());
      setUploadStatus('Project quality score calculated.');
      return true;
    } catch (scoreError) {
      setError(scoreError.message || 'Could not calculate the project quality score.');
      setUploadStatus('Quality score could not be calculated.');
      return false;
    } finally {
      setLoading(false);
    }
  }, [analysisSummary, currentUser, uploadId]);

  const handleGenerateFix = useCallback(async (fileIndex, issueIndex) => {
    if (!uploadId || !currentUser) return;
    const key = `${fileIndex}:${issueIndex}`;
    if (activeFixRequestsRef.current.has(key)) return;
    activeFixRequestsRef.current.add(key);
    setLoading(true);
    setError('');
    setFixStatus('');
    setFixProposals((current) => ({ ...current, [key]: { status: 'generating' } }));
    setUploadStatus('Generating and validating a fix proposal…');
    try {
      const token = await currentUser.getIdToken();
      const response = await fetch(apiUrl(`/api/fix/project-uploads/${uploadId}/fix-suggestions`), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ file_index: fileIndex, issue_index: issueIndex }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const proposal = await response.json();
      setFixProposals((current) => ({ ...current, [key]: { status: 'proposed', ...proposal } }));
      setUploadStatus(proposal.changed
        ? 'Fix generated. Review the selected finding and approve it before applying.'
        : 'No code change was proposed; the uploaded file remains unchanged.');
    } catch (proposalError) {
      setFixProposals((current) => ({
        ...current,
        [key]: { status: 'error', error: proposalError.message || 'Could not generate a fix suggestion.' },
      }));
      setError(proposalError.message || 'Could not generate a fix suggestion.');
    } finally {
      activeFixRequestsRef.current.delete(key);
      setLoading(false);
    }
  }, [currentUser, uploadId]);

  const handleApplyFix = useCallback(async (proposal, allowValidationOverride = false, proposalKey = null) => {
    if (!uploadId || !currentUser) return;
    setLoading(true);
    setError('');
    setFixStatus('');
    setUploadStatus(allowValidationOverride
      ? 'Applying the explicitly approved unverified fix…'
      : 'Applying the validated fix…');
    try {
      const token = await currentUser.getIdToken();
      const response = await fetch(
        apiUrl(`/api/fix/project-uploads/${uploadId}/fix-suggestions/${proposal.proposal_id}/apply`),
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ allow_validation_override: allowValidationOverride }),
        },
      );
      if (!response.ok) throw new Error(await responseError(response));
      const applied = await response.json();
      setLastBackup({ backupId: applied.backup_id, path: applied.path });
      setLastAppliedFile({ path: applied.path, code: applied.updated_code });
      const updatedProjectFiles = projectFiles.map((item) => (
        item.path === applied.path ? { ...item, size: applied.size } : item
      ));
      setProjectFiles(updatedProjectFiles);
      setFixStatus(applied.message);
      setAnalysisSummary(null);
      setResults(null);
      setQualityScore(null);
      setFixProposals((current) => {
        if (!proposalKey) {
          return {};
        }
        return {
          ...current,
          [proposalKey]: {
            ...proposal,
            status: 'applied',
            validation: proposal.validation || {},
          },
        };
      });
      setProjectWasAnalyzed(true);
      setUploadStatus(`Fix applied to ${applied.path}. Reanalyzing selected project files…`);
      const analysisComplete = await handleAnalyzeProject(updatedProjectFiles);
      if (!analysisComplete) {
        setUploadStatus(
          `Fix applied to ${applied.path}, but reanalysis did not complete successfully. Retry Analyze Project.`,
        );
        return;
      }
      const scoreUpdated = await handleCheckQualityScore(true);
      setUploadStatus(scoreUpdated
        ? `Fix applied to ${applied.path}; selected files were reanalyzed and the quality score was refreshed.`
        : `Fix applied to ${applied.path} and selected files were reanalyzed, but the quality score could not be refreshed.`);
    } catch (applyError) {
      setError(applyError.message || 'Could not apply the accepted fix.');
      setUploadStatus('The fix could not be applied; the uploaded source remains unchanged.');
    } finally {
      setLoading(false);
    }
  }, [currentUser, handleAnalyzeProject, handleCheckQualityScore, projectFiles, uploadId]);

  const handleRejectFix = useCallback((key) => {
    setFixProposals((current) => ({ ...current, [key]: { status: 'rejected' } }));
  }, []);

  const handleRestoreBackup = useCallback(async () => {
    if (!uploadId || !currentUser || !lastBackup) return;
    setLoading(true);
    setError('');
    setUploadStatus('Restoring the original uploaded source…');
    try {
      const token = await currentUser.getIdToken();
      const response = await fetch(
        apiUrl(`/api/fix/project-uploads/${uploadId}/restore/${lastBackup.backupId}`),
        { method: 'POST', headers: { Authorization: `Bearer ${token}` } },
      );
      if (!response.ok) throw new Error(await responseError(response));
      const restored = await response.json();
      setProjectFiles((files) => files.map((item) => (
        item.path === restored.path ? { ...item, size: restored.size } : item
      )));
      setFixStatus(restored.message);
      setLastBackup(null);
      setLastAppliedFile(null);
      setAnalysisSummary(null);
      setResults(null);
      setQualityScore(null);
      setUploadStatus(`Restored ${restored.path}. Reanalyze the project to refresh results.`);
    } catch (restoreError) {
      setError(restoreError.message || 'Could not restore the original source.');
    } finally {
      setLoading(false);
    }
  }, [currentUser, lastBackup, uploadId]);

  // ── manual code submit ────────────────────────────────────────────────
  const handleManualSubmit = useCallback(async (e) => {
    e.preventDefault();
    if (!manualCode.trim()) return;
    setLoading(true); resetState();
    try {
      if (!currentUser) throw new Error('Sign in with Firebase before submitting code for fixes.');
      const token = await currentUser.getIdToken();
      const langSlug = manualLang.toLowerCase().replace('#', 'sharp').replace('+', 'p');
      const res = await fetch(apiUrl('/api/fix/code'), {
        method:  'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body:    JSON.stringify({
          code:     manualCode,
          language: manualLang.toLowerCase(),
          filename: `code.${langSlug}`,
        }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => `HTTP ${res.status}`);
        throw new Error(txt);
      }
      const data = await res.json();
      setResults([data]);
    } catch (err) {
      setError(err.message || 'Fix failed. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [currentUser, manualCode, manualLang]);

  // ── download helpers ──────────────────────────────────────────────────
  function handleDownloadSingle(result) {
    const content  = result.fixed_code || result.original_code || '';
    const filename = `fixed_${result.filename || result.path || 'code.txt'}`;
    const blob = new Blob([content], { type: 'text/plain' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  }

  function handleDownloadAppliedFile() {
    if (!lastAppliedFile) return;
    const blob = new Blob([lastAppliedFile.code], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = lastAppliedFile.path.split('/').pop() || 'updated-source';
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function handleDownloadZip() {
    if (!file) return;
    setLoading(true);
    try {
      if (!currentUser) throw new Error('Sign in before downloading fixed files.');
      const token = await currentUser.getIdToken();
      const fd = new FormData();
      fd.append('file', file);
      fd.append('download', 'true');
      const res = await fetch(apiUrl('/api/fix/upload'), {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      if (!res.ok) throw new Error(`ZIP download failed (HTTP ${res.status})`);
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const name = (file.name.replace(/\.zip$/i, '') || 'project') + '_fixed.zip';
      const a    = document.createElement('a');
      a.href = url; a.download = name; a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const totalIssues  = results?.reduce((s, r) => s + (r.issues_found ?? r.issues?.length ?? 0), 0) ?? 0;
  const totalChanges = results?.reduce((s, r) => s + (r.changes?.length ?? 0), 0) ?? 0;
  const multiFile    = results && results.length > 1 && !folderFiles.length && !analysisSummary;
  const folderTotalBytes = folderFiles.reduce((total, item) => total + item.size, 0);
  const selectedProjectBytes = projectFiles
    .filter(item => selectedProjectFiles.includes(item.index))
    .reduce((total, item) => total + item.size, 0);
  const folderProgressPercent = uploadProgress.total
    ? Math.min(100, Math.round((uploadProgress.uploaded / uploadProgress.total) * 100))
    : 0;

  return (
    <div className="p-4 p-md-5">

      {/* Page header */}
      <div className="mb-4">
        <h4 className="fw-bold text-dark mb-1">
          <i className="bi bi-magic text-primary me-2"></i>Auto-Fix
        </h4>
        <p className="text-secondary small mb-0">
          Upload a project to analyze findings, calculate its quality score, and review suggested fixes before applying them.
          Single-file and manual AutoFix are still available.
        </p>
      </div>

      {/* Mode tabs */}
      <ul className="nav nav-pills mb-4">
        <li className="nav-item">
          <button
            className={`nav-link ${mode === 'upload' ? 'active' : ''}`}
            onClick={() => { setMode('upload'); resetState(); }}
          >
            <i className="bi bi-cloud-upload me-2"></i>Upload File / Project
          </button>
        </li>
        <li className="nav-item">
          <button
            className={`nav-link ${mode === 'manual' ? 'active' : ''}`}
            onClick={() => { setMode('manual'); resetState(); }}
          >
            <i className="bi bi-code-slash me-2"></i>Paste Code Manually
          </button>
        </li>
      </ul>

      {/* ── Upload mode ── */}
      {mode === 'upload' && (
        <div className="row g-4">
          {/* Left: drop zone */}
          <div className="col-12 col-lg-5">
            <p className="small text-secondary mb-2">Step 1 · Upload a project folder or ZIP</p>
            <div
              className={`upload-dropzone card border-2 border-dashed text-center p-5 ${dragOver ? 'drag-active' : ''}`}
              onDragOver={e => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleFileDrop}
              onClick={() => fileInputRef.current?.click()}
              role="button" tabIndex={0}
              onKeyDown={e => e.key === 'Enter' && fileInputRef.current?.click()}
              aria-label="Upload source file or ZIP project"
            >
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPTED_EXTS}
                className="d-none"
                onChange={handleFileSelect}
              />
              <i className={`bi ${file || folderFiles.length ? 'bi-file-earmark-check-fill text-success' : 'bi-cloud-upload text-primary'} fs-1 mb-3 d-block`}></i>
              {file ? (
                <>
                  <p className="fw-semibold text-dark mb-1">{file.name}</p>
                  <p className="text-secondary small mb-0">{formatBytes(file.size)} — click to change</p>
                </>
              ) : folderFiles.length ? (
                <>
                  <p className="fw-semibold text-dark mb-1">{folderName}</p>
                  <p className="text-secondary small mb-0">
                    {folderFiles.length.toLocaleString()} files · {formatBytes(folderTotalBytes)}
                  </p>
                </>
              ) : (
                <>
                  <p className="fw-semibold text-dark mb-1">Drop your file here</p>
                  <p className="text-secondary small mb-2">or click to browse for a file or ZIP</p>
                  <p className="text-muted" style={{ fontSize: '0.75rem' }}>
                    .py .js .ts .java .cs .go .rb .php .cpp .swift .kt<br />
                    or a <strong>.zip</strong> project archive (max 500 MiB)
                  </p>
                </>
              )}
            </div>

            <input
              ref={folderInputRef}
              type="file"
              webkitdirectory=""
              multiple
              className="d-none"
              onChange={handleFolderSelect}
            />
            <div className="mt-3 d-grid">
              <button
                type="button"
                className="btn btn-outline-primary"
                onClick={() => folderInputRef.current?.click()}
                disabled={loading}
              >
                <i className="bi bi-folder2-open me-2"></i>Select Project Folder
              </button>
            </div>

            {file && (
              <div className="mt-3 d-flex gap-2">
                <button
                  className="btn btn-primary flex-grow-1 fw-medium"
                  onClick={handleUploadSubmit}
                  disabled={loading}
                >
                  {loading
                    ? <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Analyzing & Fixing…</>
                    : <><i className="bi bi-magic me-2"></i>Analyze & Fix</>}
                </button>
                {file.name.toLowerCase().endsWith('.zip') && (
                  <button
                    className="btn btn-outline-primary"
                    onClick={handleArchiveProjectUpload}
                    disabled={loading}
                    title="Upload this ZIP for project analysis and scoring"
                  >
                    {loading
                      ? <span className="spinner-border spinner-border-sm" role="status" aria-label="Uploading project archive" />
                      : <><i className="bi bi-folder2-open me-1" />Project workflow</>}
                  </button>
                )}
                {results && !analysisSummary && file.name.toLowerCase().endsWith('.zip') && (
                  <button
                    className="btn btn-outline-primary"
                    onClick={handleDownloadZip}
                    disabled={loading}
                    title="Download all fixed files as ZIP"
                  >
                    <i className="bi bi-file-zip"></i>
                  </button>
                )}
              </div>
            )}

            {folderFiles.length > 0 && (
              <>
                <div className="mt-3">
                  <div className="d-flex justify-content-between small mb-1">
                    <span className="text-secondary">{uploadStatus || 'Ready to upload'}</span>
                    <span className="text-secondary">
                      {formatBytes(uploadProgress.uploaded)} / {formatBytes(folderTotalBytes)}
                    </span>
                  </div>
                  <div
                    className="progress"
                    role="progressbar"
                    aria-label="Project upload progress"
                    aria-valuenow={folderProgressPercent}
                    aria-valuemin="0"
                    aria-valuemax="100"
                  >
                    <div className="progress-bar" style={{ width: `${folderProgressPercent}%` }}>
                      {folderProgressPercent}%
                    </div>
                  </div>
                </div>
                {!projectFiles.length && (
                  <button
                    className="btn btn-primary w-100 mt-3 fw-medium"
                    onClick={handleFolderUpload}
                    disabled={loading}
                  >
                    {loading
                      ? <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Uploading…</>
                      : <><i className="bi bi-cloud-arrow-up me-2"></i>{uploadId ? 'Resume Upload' : 'Upload Project Folder'}</>}
                  </button>
                )}
              </>
            )}

            {projectFiles.length > 0 && (
              <div className="mt-3 p-3 border rounded bg-light">
                <p className="fw-semibold text-dark mb-1">Project upload status</p>
                <p className="small text-secondary mb-0">{folderName}</p>
                <p className="small text-secondary mb-0" role="status">
                  {loading && <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />}
                  {uploadStatus}
                </p>
                <div className="progress mt-2" role="progressbar" aria-label="Project upload progress" aria-valuenow={folderProgressPercent} aria-valuemin="0" aria-valuemax="100">
                  <div className="progress-bar" style={{ width: `${folderProgressPercent}%` }}>{folderProgressPercent}%</div>
                </div>
              </div>
            )}

            <div className="mt-3 p-3 bg-light rounded small text-secondary">
              <p className="fw-medium text-dark mb-2">Limits</p>
              <ul className="mb-0 ps-3">
                <li>Folder upload: 4 GiB total by default, configurable on the backend</li>
                <li>Chunks: 16 MiB, uploaded sequentially and resumable</li>
                <li>Choose up to 200 source files; analysis requests contain up to 20 files and 64 MiB</li>
                <li>Analysis skips generated/dependency/hidden files, binary files, and source files over 20 MiB</li>
                <li>Legacy single-file/ZIP upload: 50 MiB / 500 MiB compressed; ZIP extracts up to 500 MiB total (20 MiB per source file)</li>
              </ul>
            </div>
          </div>

          {/* Right: results */}
          <div className="col-12 col-lg-7">
            {error && (
              <div className="alert alert-danger small">
                <i className="bi bi-exclamation-circle me-2"></i>{error}
              </div>
            )}

            {projectFiles.length > 0 && (
              <div className="card border-0 shadow-sm mb-4">
                <div className="card-header bg-white py-3">
                  <p className="small text-secondary mb-2">Step 2 · View uploaded project</p>
                  <div className="d-flex justify-content-between align-items-center gap-2 flex-wrap">
                    <div>
                      <h6 className="fw-semibold mb-1">{folderName} · Uploaded project files</h6>
                      <p className="small text-secondary mb-0">
                        {projectFiles.filter(item => item.selectable).length} supported source files ·
                        {' '}{projectFiles.length} total files
                      </p>
                    </div>
                    <span className="small text-secondary">
                      {selectedProjectFiles.length} / {analysisLimits.maximum_files} selected ·
                      {' '}{formatBytes(selectedProjectBytes)} total (split into bounded requests)
                    </span>
                  </div>
                </div>
                <div className="list-group list-group-flush" style={{ maxHeight: 420, overflowY: 'auto' }}>
                  {projectFiles.map(item => (
                    <label
                      key={item.index}
                      className={`list-group-item d-flex gap-2 align-items-start ${item.selectable ? '' : 'text-muted'}`}
                    >
                      <input
                        className="form-check-input mt-1"
                        type="checkbox"
                        checked={selectedProjectFiles.includes(item.index)}
                        disabled={
                          !item.selectable
                          || loading
                          || (
                            selectedProjectFiles.length >= analysisLimits.maximum_files
                            && !selectedProjectFiles.includes(item.index)
                          )
                        }
                        onChange={() => {
                          setAnalysisSummary(null);
                          setResults(null);
                          setQualityScore(null);
                          setFixProposals({});
                          setSelectedProjectFiles(current => {
                            if (current.includes(item.index)) return current.filter(index => index !== item.index);
                            if (current.length >= analysisLimits.maximum_files) return current;
                            return [...current, item.index];
                          });
                        }}
                      />
                      <span className="flex-grow-1 text-break">
                        <code>{item.path}</code>
                        <span className="d-block small">
                          {item.selectable
                            ? `${item.language} · ${formatBytes(item.size)}`
                            : item.skip_reason}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
                <div className="card-footer bg-white">
                  <p className="small text-secondary mb-2">Step 3 · Analyze Project</p>
                  <button
                    className="btn btn-primary w-100"
                    onClick={() => handleAnalyzeProject()}
                    disabled={loading || selectedProjectFiles.length === 0 || !uploadId}
                  >
                    {loading
                      ? <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Analyzing…</>
                      : <><i className="bi bi-search me-2"></i>{projectWasAnalyzed ? 'Reanalyze Project' : 'Analyze Project'}</>}
                  </button>
                  {projectWasAnalyzed && (
                    <p className="small text-secondary mt-2 mb-0">
                      Accepted fixes automatically reanalyze selected files and refresh the score. Reanalyze after reverting a change.
                    </p>
                  )}
                  <p className="small text-secondary mb-0 mt-2">
                    Findings are produced from static analysis and individual AI requests for selected source files.
                    Uploaded code is never executed; source is sent to AI one file at a time and may be truncated
                    to the existing review limit.
                  </p>
                  <div className="mt-3 border-top pt-3">
                    <p className="small text-secondary mb-2">Step 5 · Check Quality Score</p>
                    <button
                      type="button"
                      className="btn btn-outline-primary w-100"
                      onClick={() => handleCheckQualityScore()}
                      disabled={loading || !analysisSummary?.complete}
                    >
                      {loading && uploadStatus.includes('score')
                        ? <><span className="spinner-border spinner-border-sm me-2" />Calculating score…</>
                        : <><i className="bi bi-speedometer2 me-2" />Check Quality Score</>}
                    </button>
                    {!analysisSummary?.complete && (
                      <p className="small text-muted mt-2 mb-0">
                        Complete project analysis before a score can be calculated.
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}

            {fixStatus && (
              <div className="alert alert-success small d-flex justify-content-between align-items-center flex-wrap gap-2" role="status">
                <span><i className="bi bi-check-circle me-2" />{fixStatus}</span>
                <div className="d-flex gap-2">
                  {lastAppliedFile && (
                    <button type="button" className="btn btn-sm btn-outline-success" onClick={handleDownloadAppliedFile}>
                      Download updated file
                    </button>
                  )}
                  {lastBackup && (
                    <button type="button" className="btn btn-sm btn-outline-success" onClick={handleRestoreBackup} disabled={loading}>
                      Revert this change
                    </button>
                  )}
                </div>
              </div>
            )}

            {results && (
              <>
                {analysisSummary && (
                  <div className="alert alert-info small">
                    <p className="small text-secondary mb-1">Step 4 · View analysis results</p>
                    <strong>Project analysis summary:</strong> {analysisSummary.analyzed} analyzed,
                    {' '}{analysisSummary.skipped} selected file(s) skipped,
                    {' '}{analysisSummary.not_selected} not selected,
                    {' '}{analysisSummary.errors?.length || 0} error(s).
                    {analysisSummary.languages?.length > 0 && (
                      <span> Languages: {analysisSummary.languages.join(', ')}.</span>
                    )}
                    {analysisSummary.historyPersisted === false && (
                      <p className="mb-0 mt-2 text-warning">
                        Analysis history was not saved to Firestore. Check Firebase configuration and backend logs.
                      </p>
                    )}
                    {analysisSummary.skipped_files?.length > 0 && (
                      <ul className="mb-0 mt-2">
                        {analysisSummary.skipped_files.map(item => (
                          <li key={item.path}>{item.path}: {item.reason}</li>
                        ))}
                      </ul>
                    )}
                    {analysisSummary.errors?.length > 0 && (
                      <ul className="mb-0 mt-2">
                        {analysisSummary.errors.map(item => (
                          <li key={item.path}>{item.path}: {item.error}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
                {qualityScore && <ProjectQualityScore qualityScore={qualityScore} />}
                <div className="row g-3 mb-4">
                  {[
                    { label: 'Files processed', value: results.length,   color: 'primary', icon: 'bi-files' },
                    { label: 'Issues found',    value: totalIssues,      color: 'danger',  icon: 'bi-exclamation-triangle' },
                    { label: analysisSummary ? 'Languages' : 'Lines changed', value: analysisSummary ? (analysisSummary.languages?.length || 0) : totalChanges, color: 'success', icon: analysisSummary ? 'bi-code-slash' : 'bi-pencil-square' },
                  ].map(({ label, value, color, icon }) => (
                    <div key={label} className="col-4">
                      <div className="card border-0 shadow-sm text-center py-3">
                        <i className={`bi ${icon} text-${color} fs-4 mb-1`}></i>
                        <div className={`fw-bold fs-5 text-${color}`}>{value}</div>
                        <div className="small text-secondary">{label}</div>
                      </div>
                    </div>
                  ))}
                </div>

                {multiFile && (
                  <div className="d-flex justify-content-end mb-3">
                    <button className="btn btn-primary btn-sm px-4" onClick={handleDownloadZip} disabled={loading}>
                      <i className="bi bi-file-zip me-2"></i>Download Processed Files (.zip)
                    </button>
                  </div>
                )}

                {analysisSummary ? (
                  <ProjectFindings
                    results={results}
                    fixProposals={fixProposals}
                    loading={loading}
                    onGenerateFix={handleGenerateFix}
                    onApplyFix={handleApplyFix}
                    onRejectFix={handleRejectFix}
                  />
                ) : results.map((r, i) => (
                  <SingleFileResult key={i} result={r} onDownloadSingle={handleDownloadSingle} />
                ))}
              </>
            )}

            {!results && !error && !loading && (
              <div className="text-center py-5 text-secondary">
                <i className="bi bi-arrow-left fs-2 mb-3 d-block opacity-25"></i>
                <p className="small">
                  {projectFiles.length
                    ? 'Project is ready. Analyze or reanalyze it to view current results.'
                    : 'Upload a file or project to see results here.'}
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Manual code mode ── */}
      {mode === 'manual' && (
        <div className="row g-4">
          {/* Left: form */}
          <div className="col-12 col-lg-6">
            <div className="card border-0 shadow-sm">
              <div className="card-body p-4">
                <form onSubmit={handleManualSubmit} noValidate>
                  <div className="mb-3">
                    <label className="form-label fw-medium small">Language</label>
                    <select
                      className="form-select"
                      value={manualLang}
                      onChange={e => { setManualLang(e.target.value); resetState(); }}
                    >
                      {LANGUAGES.map(l => <option key={l}>{l}</option>)}
                    </select>
                  </div>

                  <div className="mb-3">
                    <label className="form-label fw-medium small">
                      Your code
                      <span className="text-secondary ms-2 fw-normal">
                        — paste code with any bugs, errors, or vulnerabilities
                      </span>
                    </label>
                    <textarea
                      className="form-control font-monospace autofix-textarea"
                      rows={22}
                      spellCheck={false}
                      placeholder={`Paste your ${manualLang} code here…\nThe AI will analyze syntax errors, logic issues, security vulnerabilities,\nnaming problems, and fix them all automatically.`}
                      value={manualCode}
                      onChange={e => { setManualCode(e.target.value); resetState(); }}
                    />
                    <div className="form-text">
                      {manualCode.length > 0
                        ? `${manualCode.split('\n').length} lines · ${manualCode.length} chars`
                        : 'All issues will be detected and fixed — syntax errors, security flaws, bad patterns'}
                    </div>
                  </div>

                  {error && (
                    <div className="alert alert-danger small py-2 mb-3">
                      <i className="bi bi-exclamation-circle me-2"></i>{error}
                    </div>
                  )}

                  <button
                    type="submit"
                    className="btn btn-primary w-100 fw-medium"
                    disabled={!manualCode.trim() || loading}
                  >
                    {loading
                      ? <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Analyzing & Fixing…</>
                      : <><i className="bi bi-magic me-2"></i>Fix My Code</>}
                  </button>
                </form>
              </div>
            </div>
          </div>

          {/* Right: results */}
          <div className="col-12 col-lg-6">
            {results?.map((r, i) => (
              <SingleFileResult key={i} result={r} onDownloadSingle={handleDownloadSingle} />
            ))}
            {!results && !error && !loading && (
              <div className="text-center py-5 text-secondary">
                <i className="bi bi-magic fs-2 mb-3 d-block opacity-25"></i>
                <p className="small">Fixed code will appear here.</p>
                <p className="text-muted" style={{ fontSize: '0.75rem' }}>
                  Works for all languages — Python, JavaScript, Java, Go, and more
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
