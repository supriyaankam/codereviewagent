import { useState, useRef, useCallback } from 'react';
import '../styles/AutoFix.css';

const LANGUAGES = [
  'Python', 'JavaScript', 'TypeScript', 'Java', 'C#',
  'C++', 'Go', 'Rust', 'Ruby', 'PHP', 'Swift', 'Kotlin', 'Other',
];

const ACCEPTED_EXTS = '.py,.js,.ts,.jsx,.tsx,.java,.cs,.go,.rb,.php,.cpp,.c,.rs,.kt,.swift,.zip';

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
};

function IssuesList({ issues }) {
  if (!issues?.length) return (
    <div className="text-center py-4">
      <i className="bi bi-check-circle-fill text-success fs-3 mb-2 d-block"></i>
      <p className="text-secondary small mb-0">No issues detected by static analysis.</p>
    </div>
  );
  return (
    <div>
      {issues.map((issue, i) => {
        const color = SEVERITY_COLORS[issue.severity] ?? 'secondary';
        return (
          <div key={i} className={`border-start border-4 border-${color} ps-3 mb-3 py-1`}>
            <div className="d-flex align-items-center gap-2 mb-1 flex-wrap">
              <span className={`badge bg-${color}-subtle text-${color} text-capitalize`}>
                {issue.severity}
              </span>
              <span className="badge bg-light text-secondary border text-capitalize">
                {issue.category}
              </span>
              {issue.line > 0 && (
                <span className="text-muted small">Line {issue.line}</span>
              )}
            </div>
            <p className="fw-medium small text-dark mb-1">{issue.title}</p>
            {issue.description && (
              <p className="small text-secondary mb-1">{issue.description}</p>
            )}
            {issue.recommendation && (
              <p className="small text-success mb-0">
                <i className="bi bi-lightbulb me-1"></i>{issue.recommendation}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
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

// ── SingleFileResult ──────────────────────────────────────────────────────────
function SingleFileResult({ result, onDownloadSingle }) {
  const [tab, setTab] = useState('diff');
  const hasChanges = (result.changes?.length > 0) ||
                     (result.original_code !== result.fixed_code);

  return (
    <div className="card border-0 shadow-sm mb-4">
      <div className="card-header bg-white border-bottom d-flex align-items-center justify-content-between flex-wrap gap-2 py-3 px-4">
        <div className="d-flex align-items-center gap-3 flex-wrap">
          <code className="text-primary fw-semibold">{result.filename || result.path}</code>
          <span className="badge bg-secondary-subtle text-secondary">{result.language}</span>
          {result.issues_found > 0 && (
            <span className="badge bg-danger-subtle text-danger">
              <i className="bi bi-exclamation-triangle me-1"></i>
              {result.issues_found} issue{result.issues_found !== 1 ? 's' : ''} found
            </span>
          )}
          {hasChanges ? (
            <span className="badge bg-success-subtle text-success">
              <i className="bi bi-pencil-square me-1"></i>Fixed
            </span>
          ) : (
            <span className="badge bg-success-subtle text-success">
              <i className="bi bi-check-circle me-1"></i>No changes needed
            </span>
          )}
          {!result.ai_available && (
            <span className="badge bg-warning-subtle text-warning">
              <i className="bi bi-cpu me-1"></i>Static only
            </span>
          )}
        </div>
        <button className="btn btn-sm btn-outline-primary" onClick={() => onDownloadSingle(result)}>
          <i className="bi bi-download me-1"></i>Download fixed file
        </button>
      </div>

      {result.summary && (
        <div className="px-4 pt-3 pb-0">
          <div className="alert alert-info py-2 small mb-0">
            <i className="bi bi-chat-text me-2"></i>{result.summary}
          </div>
        </div>
      )}

      {result.error && (
        <div className="px-4 pt-3 pb-0">
          <div className="alert alert-warning py-2 small mb-0">
            <i className="bi bi-exclamation-triangle me-2"></i>{result.error}
          </div>
        </div>
      )}

      <div className="card-body p-4">
        <ul className="nav nav-tabs mb-3">
          {[
            { key: 'diff',     label: 'Fixed Code',    icon: 'bi-file-earmark-code' },
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

// ── Main AutoFix page ─────────────────────────────────────────────────────────
export default function AutoFix() {
  const [mode, setMode]           = useState('upload');  // 'upload' | 'manual'
  const [dragOver, setDragOver]   = useState(false);
  const [file, setFile]           = useState(null);
  const [manualCode, setManualCode] = useState('');
  const [manualLang, setManualLang] = useState('Python');
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState('');
  const [results, setResults]     = useState(null);
  const fileInputRef = useRef();

  function resetState() { setError(''); setResults(null); }

  // ── file drag / select ────────────────────────────────────────────────
  function handleFileDrop(e) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) { setFile(dropped); resetState(); }
  }

  function handleFileSelect(e) {
    const selected = e.target.files[0];
    if (selected) { setFile(selected); resetState(); }
  }

  // ── upload submit ─────────────────────────────────────────────────────
  const handleUploadSubmit = useCallback(async () => {
    if (!file) return;
    setLoading(true); resetState();
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('download', 'false');
      const res = await fetch('/api/fix/upload', { method: 'POST', body: fd });
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
  }, [file]);

  // ── manual code submit ────────────────────────────────────────────────
  const handleManualSubmit = useCallback(async (e) => {
    e.preventDefault();
    if (!manualCode.trim()) return;
    setLoading(true); resetState();
    try {
      const langSlug = manualLang.toLowerCase().replace('#', 'sharp').replace('+', 'p');
      const res = await fetch('/api/fix/code', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
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
  }, [manualCode, manualLang]);

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

  async function handleDownloadZip() {
    if (!file) return;
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('download', 'true');
      const res = await fetch('/api/fix/upload', { method: 'POST', body: fd });
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

  const totalIssues  = results?.reduce((s, r) => s + (r.issues_found ?? 0), 0) ?? 0;
  const totalChanges = results?.reduce((s, r) => s + (r.changes?.length ?? 0), 0) ?? 0;
  const multiFile    = results && results.length > 1;

  return (
    <div className="p-4 p-md-5">

      {/* Page header */}
      <div className="mb-4">
        <h4 className="fw-bold text-dark mb-1">
          <i className="bi bi-magic text-primary me-2"></i>Auto-Fix
        </h4>
        <p className="text-secondary small mb-0">
          Upload a project or file, or paste code manually — the AI finds all issues and fixes them automatically.
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
            <div
              className={`upload-dropzone card border-2 border-dashed text-center p-5 ${dragOver ? 'drag-active' : ''}`}
              onDragOver={e => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleFileDrop}
              onClick={() => fileInputRef.current?.click()}
              role="button" tabIndex={0}
              onKeyDown={e => e.key === 'Enter' && fileInputRef.current?.click()}
              aria-label="Upload file"
            >
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPTED_EXTS}
                className="d-none"
                onChange={handleFileSelect}
              />
              <i className={`bi ${file ? 'bi-file-earmark-check-fill text-success' : 'bi-cloud-upload text-primary'} fs-1 mb-3 d-block`}></i>
              {file ? (
                <>
                  <p className="fw-semibold text-dark mb-1">{file.name}</p>
                  <p className="text-secondary small mb-0">
                    {file.size >= 1024 * 1024
                      ? `${(file.size / (1024 * 1024)).toFixed(1)} MB`
                      : `${(file.size / 1024).toFixed(1)} KB`
                    } — click to change
                  </p>
                </>
              ) : (
                <>
                  <p className="fw-semibold text-dark mb-1">Drop your file here</p>
                  <p className="text-secondary small mb-2">or click to browse</p>
                  <p className="text-muted" style={{ fontSize: '0.75rem' }}>
                    .py .js .ts .java .cs .go .rb .php .cpp .swift .kt<br />
                    or a <strong>.zip</strong> project archive (max 100 MB)
                  </p>
                </>
              )}
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
                {results && file.name.toLowerCase().endsWith('.zip') && (
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

            <div className="mt-3 p-3 bg-light rounded small text-secondary">
              <p className="fw-medium text-dark mb-2">Limits</p>
              <ul className="mb-0 ps-3">
                <li>Single file: max 10 MB</li>
                <li>ZIP archive: max 100 MB, 1000 source files</li>
                <li>Per file inside ZIP: max 5 MB</li>
                <li>Skips: node_modules, .git, __pycache__, dist, build</li>
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

            {results && (
              <>
                <div className="row g-3 mb-4">
                  {[
                    { label: 'Files processed', value: results.length,   color: 'primary', icon: 'bi-files' },
                    { label: 'Issues found',    value: totalIssues,      color: 'danger',  icon: 'bi-exclamation-triangle' },
                    { label: 'Lines changed',   value: totalChanges,     color: 'success', icon: 'bi-pencil-square' },
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
                      <i className="bi bi-file-zip me-2"></i>Download All Fixed Files (.zip)
                    </button>
                  </div>
                )}

                {results.map((r, i) => (
                  <SingleFileResult key={i} result={r} onDownloadSingle={handleDownloadSingle} />
                ))}
              </>
            )}

            {!results && !error && !loading && (
              <div className="text-center py-5 text-secondary">
                <i className="bi bi-arrow-left fs-2 mb-3 d-block opacity-25"></i>
                <p className="small">Upload a file or project to see results here.</p>
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
