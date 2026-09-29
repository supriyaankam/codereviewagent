import { useState, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import '../styles/CodeReview.css';

// ── Constants ────────────────────────────────────────────────────────────────

const LANGUAGES = [
  'Python', 'JavaScript', 'TypeScript', 'Java', 'C#',
  'C++', 'Go', 'Rust', 'Ruby', 'PHP', 'Swift', 'Kotlin', 'Other',
];

const SEVERITY_META = {
  critical: { color: 'danger',    icon: 'bi-x-octagon-fill',      label: 'Critical' },
  error:    { color: 'danger',    icon: 'bi-exclamation-octagon',  label: 'Error'    },
  warning:  { color: 'warning',   icon: 'bi-exclamation-triangle', label: 'Warning'  },
  info:     { color: 'secondary', icon: 'bi-info-circle',          label: 'Info'     },
};

const RISK_META = {
  critical: { color: 'danger',  label: 'Critical Risk' },
  high:     { color: 'danger',  label: 'High Risk'     },
  medium:   { color: 'warning', label: 'Medium Risk'   },
  low:      { color: 'success', label: 'Low Risk'      },
  info:     { color: 'info',    label: 'Informational' },
};

// ── Sub-components ────────────────────────────────────────────────────────────

/**
 * Per-issue feedback panel (Accept / Reject / Modify).
 * Calls POST /api/feedback and shows confirmation inline.
 */
function IssueFeedbackPanel({ issue, issueIndex, reviewId, userId, projectId, language }) {
  const [decision, setDecision]         = useState(null);   // 'accepted'|'rejected'|'modified'
  const [reason, setReason]             = useState('');
  const [modified, setModified]         = useState('');
  const [submitting, setSubmitting]     = useState(false);
  const [submitted, setSubmitted]       = useState(false);
  const [error, setError]               = useState('');
  const [memoryStored, setMemoryStored] = useState(false);

  const needsReason = decision === 'rejected' || decision === 'modified';

  async function handleSubmit(e) {
    e.preventDefault();
    if (!decision) return;
    setSubmitting(true);
    setError('');
    try {
      const body = {
        user_id:                  userId,
        review_id:                reviewId,
        issue_id:                 String(issueIndex),
        experience_id:            issue.experience_id || '',
        decision,
        note:                     reason.trim(),
        issue_title:              issue.title || '',
        issue_category:           issue.category || '',
        issue_severity:           issue.severity || '',
        original_recommendation:  issue.recommendation || '',
        modified_recommendation:  modified.trim(),
        project_id:               projectId || 'global',
        language:                 language  || '',
      };

      const res = await fetch('/api/feedback', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => 'Request failed');
        throw new Error(text);
      }

      const data = await res.json();
      setMemoryStored(data.memory_stored ?? false);
      setSubmitted(true);
    } catch (err) {
      setError(err.message || 'Failed to submit feedback.');
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <div className="feedback-confirmed d-flex align-items-center gap-2 mt-3 px-3 py-2 rounded small">
        <i className={`bi ${decision === 'accepted' ? 'bi-check-circle-fill text-success' : decision === 'rejected' ? 'bi-x-circle-fill text-danger' : 'bi-pencil-fill text-warning'}`}></i>
        <span className="text-secondary">
          Feedback <strong>{decision}</strong> recorded.
          {memoryStored && <span className="ms-1 text-muted">(saved to team memory)</span>}
        </span>
      </div>
    );
  }

  return (
    <div className="issue-feedback-panel mt-3 pt-3 border-top">
      {error && (
        <div className="alert alert-danger py-1 px-2 small mb-2">
          <i className="bi bi-exclamation-circle me-1"></i>{error}
        </div>
      )}

      {/* Decision buttons */}
      <div className="d-flex gap-2 flex-wrap mb-2">
        <span className="small text-secondary me-1 align-self-center">Your decision:</span>
        {[
          { key: 'accepted', label: 'Accept',  icon: 'bi-check-lg',  cls: 'btn-outline-success' },
          { key: 'rejected', label: 'Reject',  icon: 'bi-x-lg',      cls: 'btn-outline-danger'  },
          { key: 'modified', label: 'Modify',  icon: 'bi-pencil',    cls: 'btn-outline-warning' },
        ].map(({ key, label, icon, cls }) => (
          <button
            key={key}
            type="button"
            className={`btn btn-sm ${cls} ${decision === key ? 'active' : ''}`}
            onClick={() => setDecision(decision === key ? null : key)}
            aria-pressed={decision === key}
          >
            <i className={`bi ${icon} me-1`}></i>{label}
          </button>
        ))}
      </div>

      {/* Reason / modification form */}
      {decision && (
        <form onSubmit={handleSubmit}>
          {decision === 'modified' && (
            <div className="mb-2">
              <label className="form-label small fw-medium mb-1">
                Your preferred approach
              </label>
              <textarea
                className="form-control form-control-sm"
                rows={2}
                placeholder="Describe how your team would handle this instead…"
                value={modified}
                onChange={e => setModified(e.target.value)}
              />
            </div>
          )}
          {needsReason && (
            <div className="mb-2">
              <label className="form-label small fw-medium mb-1">
                Reason <span className="text-muted">(optional but helps team memory)</span>
              </label>
              <textarea
                className="form-control form-control-sm"
                rows={2}
                placeholder={
                  decision === 'rejected'
                    ? 'e.g. "Our project intentionally keeps this in the service layer."'
                    : 'e.g. "We use a slightly different pattern here."'
                }
                value={reason}
                onChange={e => setReason(e.target.value)}
              />
            </div>
          )}
          <button
            type="submit"
            className="btn btn-sm btn-primary px-3"
            disabled={submitting}
          >
            {submitting
              ? <><span className="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>Saving…</>
              : 'Submit feedback'}
          </button>
        </form>
      )}
    </div>
  );
}

// ── Source badge helper ───────────────────────────────────────────────────────

const SOURCE_META = {
  syntax:  { label: 'Syntax',  color: 'danger'   },
  ast:     { label: 'AST',     color: 'primary'  },
  pylint:  { label: 'Pylint',  color: 'info'     },
  bandit:  { label: 'Bandit',  color: 'warning'  },
  secrets: { label: 'Secrets', color: 'danger'   },
  ai:      { label: 'AI',      color: 'primary'  },
  static:  { label: 'Static',  color: 'secondary'},
};

function SourceBadge({ source }) {
  const meta = SOURCE_META[source] ?? SOURCE_META.static;
  return (
    <span className={`badge bg-${meta.color}-subtle text-${meta.color} border border-${meta.color}-subtle`}
          style={{ fontSize: '0.68rem' }}>
      {meta.label}
    </span>
  );
}

/**
 * Single issue card — severity, source tool, line, explanation, recommendation, feedback panel.
 */
function IssueCard({ issue, index, reviewId, userId, projectId, language }) {
  const [open, setOpen] = useState(index < 3);
  const sev = SEVERITY_META[issue.severity] ?? SEVERITY_META.info;
  const isTeamStandard = issue.title?.includes('[TEAM STANDARD]');
  const isSyntaxError  = issue.source === 'syntax' || issue.category === 'syntax';

  return (
    <div className={`issue-card card border-0 shadow-sm mb-3 border-start border-4 border-${sev.color}`}>
      <div
        className="card-header bg-transparent border-0 py-3 px-4 d-flex align-items-center justify-content-between gap-2"
        style={{ cursor: 'pointer' }}
        onClick={() => setOpen(o => !o)}
        role="button"
        aria-expanded={open}
      >
        {/* Left: badges */}
        <div className="d-flex align-items-center gap-2 flex-wrap">
          <span className={`badge bg-${sev.color}-subtle text-${sev.color} d-flex align-items-center gap-1`}>
            <i className={`bi ${sev.icon}`}></i>
            {sev.label}
          </span>
          {issue.source && <SourceBadge source={issue.source} />}
          <span className="badge bg-light text-secondary text-capitalize border">
            {issue.category}
          </span>
          {isTeamStandard && (
            <span className="badge bg-primary-subtle text-primary">
              <i className="bi bi-shield-check me-1"></i>Team Standard
            </span>
          )}
          {issue.line > 0 && (
            <span className="text-muted small fw-medium">
              Line {issue.line}{issue.column > 0 ? `, col ${issue.column}` : ''}
            </span>
          )}
        </div>
        {/* Right: title + chevron */}
        <div className="d-flex align-items-center gap-2 ms-2">
          <span className="fw-medium text-dark small issue-title">{issue.title}</span>
          <i className={`bi bi-chevron-${open ? 'up' : 'down'} text-secondary flex-shrink-0`}></i>
        </div>
      </div>

      {open && (
        <div className="card-body pt-0 px-4 pb-4">
          {/* Explanation / description */}
          {(issue.explanation || issue.description) && (
            <div className="mb-3">
              <p className="small fw-medium text-dark mb-1">
                <i className="bi bi-info-circle me-1 text-secondary"></i>
                {isSyntaxError ? 'Syntax error detail' : 'Why this matters'}
              </p>
              <p className={`small mb-0 ${isSyntaxError ? 'text-danger' : 'text-secondary'}`}>
                {issue.explanation || issue.description}
              </p>
            </div>
          )}
          {/* Recommendation / suggestion */}
          {issue.recommendation && (
            <div className="recommendation-box px-3 py-2 rounded mb-2">
              <p className="small fw-medium mb-1">
                <i className="bi bi-lightbulb text-warning me-1"></i>Suggested fix
              </p>
              <p className="small mb-0">{issue.recommendation}</p>
            </div>
          )}

          {/* Feedback panel — skip for syntax errors (nothing to accept/reject) */}
          {!isSyntaxError && (
            <IssueFeedbackPanel
              issue={issue}
              issueIndex={index}
              reviewId={reviewId}
              userId={userId}
              projectId={projectId}
              language={language}
            />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Full review results panel.
 *
 * Issue list logic:
 *   - allIssues  = ai_issues (may include promoted static issues when AI unavailable)
 *   - extraStatic = static_issues that are NOT already in allIssues (dedup by title+line)
 *   - totalCount = allIssues.length + extraStatic.length  (no double-counting)
 *
 * "No issues found" is only shown when BOTH lists are empty.
 */
function ReviewResults({ result, userId, projectId, language }) {
  const risk = RISK_META[result.overall_risk] ?? RISK_META.medium;

  const allIssues = [...(result.ai_issues || [])];

  // Extra static issues not already represented in allIssues
  const extraStatic = (result.static_issues || []).filter(
    si => !allIssues.some(
      ai => ai.title.replace(/^\[.*?\]\s*/, '') === si.title.replace(/^\[.*?\]\s*/, '')
            && ai.line === si.line
    )
  );

  const allCombined = [...allIssues, ...extraStatic];
  const totalCount  = allCombined.length;

  // Separate real problems (critical/error/warning) from style tips (info)
  const realIssues  = allCombined.filter(i => i.severity !== 'info');
  const infoIssues  = allCombined.filter(i => i.severity === 'info');
  const hasRealProblems = realIssues.length > 0;
  // "Code looks good" = no real problems (only optional style suggestions)
  const codeIsClean = !hasRealProblems && result.overall_risk === 'info';

  const aiOnlyIssues     = allIssues.filter(i => i.severity !== 'info');
  const aiInfoIssues     = allIssues.filter(i => i.severity === 'info');
  const staticOnlyReal   = extraStatic.filter(i => i.severity !== 'info');
  const staticOnlyInfo   = extraStatic.filter(i => i.severity === 'info');

  return (
    <div className="review-results mt-4">

      {/* ── Header row ── */}
      <div className="d-flex align-items-center justify-content-between flex-wrap gap-3 mb-4">
        <h5 className="fw-bold mb-0">Review Results</h5>
        <div className="d-flex align-items-center gap-2 flex-wrap">
          <span className={`badge bg-${risk.color} fs-6 px-3 py-2`}>
            {risk.label}
          </span>
          {!result.ai_available && (
            <span className="badge bg-warning-subtle text-warning border border-warning-subtle">
              <i className="bi bi-cpu me-1"></i>Static analysis only
            </span>
          )}
          {codeIsClean && (
            <span className="badge bg-success fs-6 px-3 py-2">
              <i className="bi bi-check-circle me-1"></i>Code looks good
            </span>
          )}
        </div>
      </div>

      {/* ── Summary card ── */}
      {result.summary && (
        <div className={`card border-0 shadow-sm mb-4 border-start border-4 border-${
          codeIsClean ? 'success' : result.ai_available ? 'primary' : 'warning'}`}>
          <div className="card-body px-4 py-3">
            <p className="small fw-semibold text-dark mb-1">
              <i className={`bi ${codeIsClean ? 'bi-check-circle text-success' : result.ai_available ? 'bi-stars text-primary' : 'bi-search text-warning'} me-2`}></i>
              {result.ai_available ? 'AI Summary' : 'Static Analysis Summary'}
            </p>
            <p className="small text-secondary mb-0">{result.summary}</p>
          </div>
        </div>
      )}

      {/* ── "Code looks good" banner when only style suggestions exist ── */}
      {codeIsClean && (
        <div className="card border-0 shadow-sm border-start border-4 border-success mb-4">
          <div className="card-body px-4 py-3 d-flex align-items-center gap-3">
            <i className="bi bi-check-circle-fill text-success fs-3 flex-shrink-0"></i>
            <div>
              <p className="fw-semibold text-dark mb-1">No errors or warnings found</p>
              <p className="small text-secondary mb-0">
                This code is functionally correct with no bugs, security issues, or logical errors.
                {infoIssues.length > 0 && ` There are ${infoIssues.length} optional style suggestion${infoIssues.length > 1 ? 's' : ''} below.`}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── Stats row ── */}
      <div className="row g-3 mb-4">
        {[
          { label: 'Real Issues',
            value: realIssues.length,
            color: realIssues.length > 0 ? 'danger' : 'success',
            icon:  realIssues.length > 0 ? 'bi-exclamation-triangle-fill' : 'bi-check-circle-fill' },
          { label: 'Critical',
            value: allCombined.filter(i => i.severity === 'critical').length,
            color: 'danger',    icon: 'bi-x-octagon-fill' },
          { label: 'Warnings',
            value: allCombined.filter(i => i.severity === 'warning').length,
            color: 'warning',   icon: 'bi-exclamation-triangle' },
          { label: 'Style Tips',
            value: infoIssues.length,
            color: 'secondary', icon: 'bi-info-circle' },
        ].map(({ label, value, color, icon }) => (
          <div key={label} className="col-6 col-md-3">
            <div className="card border-0 shadow-sm text-center py-3">
              <i className={`bi ${icon} text-${color} fs-4 mb-1`}></i>
              <div className={`fw-bold fs-5 text-${color}`}>{value}</div>
              <div className="small text-secondary">{label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* ── Real problems (critical / error / warning) ── */}
      {(aiOnlyIssues.length > 0 || staticOnlyReal.length > 0) && (
        <div className="mb-4">
          <h6 className="fw-semibold text-dark mb-3">
            <i className="bi bi-exclamation-triangle-fill text-warning me-2"></i>
            Issues to Fix
            <span className="badge bg-danger-subtle text-danger ms-2">{aiOnlyIssues.length + staticOnlyReal.length}</span>
          </h6>
          {aiOnlyIssues.map((issue, i) => (
            <IssueCard key={`ai-${i}`} issue={issue} index={i}
              reviewId={result.review_id} userId={userId} projectId={projectId} language={language} />
          ))}
          {staticOnlyReal.map((issue, i) => (
            <IssueCard key={`str-${i}`} issue={{ ...issue, explanation: issue.description }}
              index={i + aiOnlyIssues.length}
              reviewId={result.review_id} userId={userId} projectId={projectId} language={language} />
          ))}
        </div>
      )}

      {/* ── Style suggestions (info only) — collapsed by default ── */}
      {(aiInfoIssues.length > 0 || staticOnlyInfo.length > 0) && (
        <details className="mb-4">
          <summary className="fw-semibold text-secondary small mb-2" style={{ cursor: 'pointer', listStyle: 'none' }}>
            <i className="bi bi-info-circle me-2 text-secondary"></i>
            Style Suggestions
            <span className="badge bg-secondary-subtle text-secondary ms-2">{aiInfoIssues.length + staticOnlyInfo.length}</span>
            <span className="text-muted ms-2" style={{ fontSize: '0.72rem' }}>(click to expand)</span>
          </summary>
          <div className="mt-2">
            {aiInfoIssues.map((issue, i) => (
              <IssueCard key={`ai-info-${i}`} issue={issue} index={i}
                reviewId={result.review_id} userId={userId} projectId={projectId} language={language} />
            ))}
            {staticOnlyInfo.map((issue, i) => (
              <IssueCard key={`st-info-${i}`} issue={{ ...issue, explanation: issue.description }}
                index={i + aiInfoIssues.length}
                reviewId={result.review_id} userId={userId} projectId={projectId} language={language} />
            ))}
          </div>
        </details>
      )}

      {/* ── Truly zero issues — no static, no AI findings at all ── */}
      {totalCount === 0 && (
        <div className="card border-0 shadow-sm">
          <div className="card-body text-center py-5">
            <i className="bi bi-check-circle-fill text-success fs-1 mb-3 d-block"></i>
            <h6 className="fw-semibold text-dark mb-2">No issues found</h6>
            <p className="text-secondary small mb-0">
              Static analysis (AST, Pylint, Bandit) found nothing.
              {!result.ai_available
                ? ' Add GROQ_API_KEY for AI-powered review.'
                : ' The AI review also found the code looks clean.'}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Fix Code Panel ────────────────────────────────────────────────────────────

/**
 * Shows after a review. Lets the user:
 *  1. Click "Fix Code" → calls POST /api/reviews/fix → shows corrected code
 *  2. Download the corrected code as a .py (or language-appropriate) file
 *  3. See the re-analysis results (remaining issues after the fix)
 */
function FixPanel({ originalCode, language, sourceLanguage, issues, filename }) {
  const [fixing, setFixing]         = useState(false);
  const [fixResult, setFixResult]   = useState(null);
  const [fixError, setFixError]     = useState('');
  const [showDiff, setShowDiff]     = useState('fixed');

  // Detect language mismatch from props
  const isConversion = sourceLanguage && sourceLanguage.toLowerCase() !== language.toLowerCase();

  async function handleFix() {
    setFixing(true);
    setFixError('');
    setFixResult(null);
    try {
      const res = await fetch('/api/reviews/fix', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          code:            originalCode,
          language:        language,           // TARGET — user's selection
          source_language: sourceLanguage || language, // actual language of submitted code
          issues:          issues,
          filename:        filename || `code.${language.toLowerCase()}`,
        }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => 'Fix request failed');
        throw new Error(txt);
      }
      const data = await res.json();
      setFixResult(data);
    } catch (err) {
      setFixError(err.message || 'Fix failed. Is the backend running?');
    } finally {
      setFixing(false);
    }
  }

  function handleDownload() {
    if (!fixResult?.fixed_code) return;
    // Use TARGET language for file extension
    const tgt = (fixResult.target_language || language).toLowerCase();
    const ext = tgt === 'python' ? 'py'
              : tgt === 'javascript' ? 'js'
              : tgt === 'typescript' ? 'ts'
              : tgt === 'java' ? 'java'
              : tgt === 'c++' ? 'cpp'
              : tgt === 'c#' ? 'cs'
              : tgt === 'go' ? 'go'
              : tgt === 'rust' ? 'rs'
              : tgt === 'ruby' ? 'rb'
              : tgt === 'php' ? 'php'
              : tgt === 'kotlin' ? 'kt'
              : tgt === 'swift' ? 'swift'
              : 'txt';
    const blob = new Blob([fixResult.fixed_code], { type: 'text/plain' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `fixed_code.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // Count remaining issues from re-analysis
  const remainingIssues = fixResult?.reanalysis?.issues ?? [];
  const remainingCount  = remainingIssues.length;
  const isClean         = fixResult && remainingCount === 0;

  return (
    <div className="fix-panel mt-4">
      <div className="card border-0 shadow-sm border-start border-4 border-success">
        <div className="card-body p-4">

          {/* Header */}
          <div className="d-flex align-items-center justify-content-between flex-wrap gap-3 mb-3">
            <div>
              <h6 className="fw-bold text-dark mb-1">
                <i className="bi bi-wrench-adjustable text-success me-2"></i>
                AI Auto-Fix
                {isConversion && (
                  <span className="badge bg-info-subtle text-info ms-2 fw-normal" style={{ fontSize: '0.72rem' }}>
                    <i className="bi bi-arrow-right me-1"></i>
                    {sourceLanguage} → {language}
                  </span>
                )}
              </h6>
              <p className="small text-secondary mb-0">
                {isConversion
                  ? `Convert code from ${sourceLanguage} to ${language} and fix all detected issues.`
                  : `Let the AI correct all detected issues and generate fixed ${language} code.`}
              </p>
            </div>
            {!fixResult && (
              <button
                className="btn btn-success fw-medium px-4"
                onClick={handleFix}
                disabled={fixing}
              >
                {fixing
                  ? <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                      {isConversion ? 'Converting…' : 'Fixing…'}</>
                  : <><i className="bi bi-wrench-adjustable me-2"></i>
                      {isConversion ? `Convert to ${language}` : 'Fix Code'}</>}
              </button>
            )}
          </div>

          {/* Error */}
          {fixError && (
            <div className="alert alert-danger py-2 small mb-3">
              <i className="bi bi-exclamation-circle me-2"></i>{fixError}
            </div>
          )}

          {/* Fix result */}
          {fixResult && (
            <>
              {/* Summary */}
              {fixResult.summary && (
                <div className="alert alert-success py-2 small mb-3">
                  <i className="bi bi-check-circle me-2"></i>
                  <strong>{fixResult.is_conversion ? 'Converted & Fixed' : 'Fixed'}:</strong> {fixResult.summary}
                </div>
              )}
              {fixResult.error && (
                <div className="alert alert-warning py-2 small mb-3">
                  <i className="bi bi-exclamation-triangle me-2"></i>{fixResult.error}
                </div>
              )}

              {/* Target language badge */}
              <div className="d-flex align-items-center gap-2 mb-3">
                <span className="small text-secondary">Target Language:</span>
                <span className="badge bg-primary px-3 py-1">
                  {fixResult.target_language || language}
                </span>
                {fixResult.is_conversion && (
                  <span className="small text-muted">
                    (converted from {fixResult.source_language})
                  </span>
                )}
              </div>

              {/* Tabs */}
              <ul className="nav nav-tabs mb-3">
                {[
                  { key: 'fixed',    label: `Fixed Code (${fixResult.target_language || language})`, icon: 'bi-file-earmark-check' },
                  { key: 'changes',  label: `Changes (${fixResult.changes?.length ?? 0})`, icon: 'bi-list-ul' },
                  { key: 'original', label: `Original (${fixResult.source_language || language})`, icon: 'bi-file-earmark' },
                ].map(({ key, label, icon }) => (
                  <li key={key} className="nav-item">
                    <button
                      className={`nav-link ${showDiff === key ? 'active' : ''}`}
                      onClick={() => setShowDiff(key)}
                    >
                      <i className={`bi ${icon} me-1`}></i>{label}
                    </button>
                  </li>
                ))}
              </ul>

              {/* Fixed code viewer */}
              {showDiff === 'fixed' && (
                <pre className="fix-code-block font-monospace small mb-3">
                  {fixResult.fixed_code}
                </pre>
              )}

              {/* Original code viewer */}
              {showDiff === 'original' && (
                <pre className="fix-code-block font-monospace small mb-3">
                  {originalCode}
                </pre>
              )}

              {/* Changes list */}
              {showDiff === 'changes' && (
                <div className="mb-3">
                  {fixResult.changes?.length > 0 ? (
                    <ul className="list-unstyled mb-0">
                      {fixResult.changes.map((c, i) => (
                        <li key={i} className="border-bottom py-2">
                          <p className="small fw-medium text-dark mb-1">
                            <i className="bi bi-bug text-danger me-1"></i>
                            Issue: {c.issue}
                          </p>
                          <p className="small text-secondary mb-0">
                            <i className="bi bi-check2 text-success me-1"></i>
                            Fix: {c.change}
                          </p>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-secondary small mb-0">No detailed change log available.</p>
                  )}
                </div>
              )}

              {/* Action buttons */}
              <div className="d-flex gap-3 flex-wrap mb-4">
                <button className="btn btn-primary btn-sm px-4" onClick={handleDownload}>
                  <i className="bi bi-download me-2"></i>Download Fixed Code
                </button>
                <button
                  className="btn btn-outline-secondary btn-sm px-4"
                  onClick={() => { setFixResult(null); setFixError(''); }}
                >
                  <i className="bi bi-arrow-counterclockwise me-2"></i>Fix Again
                </button>
              </div>

              {/* Re-analysis results */}
              <div className={`p-3 rounded border ${isClean ? 'border-success bg-success-subtle' : 'border-warning bg-warning-subtle'}`}>
                <h6 className={`fw-semibold mb-2 ${isClean ? 'text-success' : 'text-warning-emphasis'}`}>
                  <i className={`bi ${isClean ? 'bi-check-circle-fill' : 'bi-exclamation-triangle'} me-2`}></i>
                  Re-Analysis Result
                </h6>
                {isClean ? (
                  <p className="small mb-0 text-success-emphasis">
                    ✅ All issues resolved. The corrected code is clean — no further problems detected.
                  </p>
                ) : (
                  <>
                    <p className="small mb-2 text-warning-emphasis">
                      {remainingCount} issue{remainingCount !== 1 ? 's' : ''} still found in the corrected code:
                    </p>
                    <ul className="mb-0 ps-3">
                      {remainingIssues.map((iss, i) => (
                        <li key={i} className="small text-dark mb-1">
                          <span className={`badge bg-${iss.severity === 'critical' || iss.severity === 'error' ? 'danger' : iss.severity === 'warning' ? 'warning' : 'secondary'}-subtle text-dark me-2`}>
                            {iss.severity}
                          </span>
                          {iss.title}
                          {iss.line > 0 && <span className="text-muted ms-1">— Line {iss.line}</span>}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function CodeReview() {
  const { currentUser, firebaseReady } = useAuth();

  const [form, setForm]       = useState({ project: '', language: '', code: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');
  const [result, setResult]   = useState(null);

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value });
    if (result) setResult(null);   // reset results on form change
    setError('');
  }

  const handleSubmit = useCallback(async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setResult(null);

    try {
      const body = {
        user_id:      currentUser?.uid ?? 'anonymous',
        project_id:   'global',
        project_name: form.project.trim(),
        language:     form.language,
        code:         form.code,
      };

      const res = await fetch('/api/review', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => 'Review request failed');
        throw new Error(text);
      }

      const data = await res.json();
      setResult(data);
    } catch (err) {
      setError(err.message || 'Could not complete the review. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [form, currentUser]);

  const isReady = form.project.trim() && form.language && form.code.trim() && !loading;

  return (
    <div className="p-4 p-md-5">
      {/* Page header */}
      <div className="mb-5">
        <h4 className="fw-bold text-dark mb-1">Code Review</h4>
        <p className="text-secondary small mb-0">
          Paste your code and let the AI agent review it against your team&apos;s standards.
        </p>
      </div>

      <div className="row g-4">
        {/* ── Form column ── */}
        <div className="col-12 col-lg-8">
          <div className="card border-0 shadow-sm">
            <div className="card-body p-4">
              <form onSubmit={handleSubmit}>
                <div className="row g-3 mb-3">
                  <div className="col-12 col-sm-6">
                    <label htmlFor="project" className="form-label fw-medium small">
                      Project name <span className="text-danger">*</span>
                    </label>
                    <input
                      id="project" name="project" type="text"
                      className="form-control"
                      placeholder="e.g. payments-service"
                      value={form.project} onChange={handleChange} required
                    />
                  </div>
                  <div className="col-12 col-sm-6">
                    <label htmlFor="language" className="form-label fw-medium small">
                      Language <span className="text-danger">*</span>
                    </label>
                    <select
                      id="language" name="language" className="form-select"
                      value={form.language} onChange={handleChange} required
                    >
                      <option value="" disabled>Select a language…</option>
                      {LANGUAGES.map(l => <option key={l} value={l}>{l}</option>)}
                    </select>
                  </div>
                </div>

                <div className="mb-4">
                  <label htmlFor="code" className="form-label fw-medium small">
                    Code <span className="text-danger">*</span>
                  </label>
                  <textarea
                    id="code" name="code"
                    className="form-control code-textarea font-monospace"
                    placeholder="Paste your code here…"
                    rows={18}
                    value={form.code} onChange={handleChange}
                    required spellCheck={false}
                  />
                  <div className="form-text">
                    {form.code.length > 0
                      ? `${form.code.split('\n').length} lines · ${form.code.length} chars`
                      : 'Supports any snippet or full file'}
                  </div>
                </div>

                {error && (
                  <div className="alert alert-danger py-2 small mb-3">
                    <i className="bi bi-exclamation-circle me-2"></i>{error}
                  </div>
                )}

                <button
                  type="submit" className="btn btn-primary px-5 fw-medium"
                  disabled={!isReady}
                >
                  {loading
                    ? <><span className="spinner-border spinner-border-sm me-2"
                              role="status" aria-hidden="true"></span>Analyzing…</>
                    : <><i className="bi bi-stars me-2"></i>Review Code</>}
                </button>
              </form>
            </div>
          </div>

          {/* Results rendered below the form */}
          {result && (
            <ReviewResults
              result={result}
              userId={currentUser?.uid ?? 'anonymous'}
              projectId="global"
              language={form.language}
            />
          )}

          {/* Fix Code panel — only show when review has issues */}
          {result && (
            (result.ai_issues?.length > 0 || result.static_issues?.length > 0) && (
              <FixPanel
                originalCode={form.code}
                language={form.language}
                sourceLanguage={result.language || form.language}
                issues={[
                  ...(result.ai_issues || []),
                  ...(result.static_issues || []).filter(
                    si => !(result.ai_issues || []).some(
                      ai => ai.title === si.title && ai.line === si.line
                    )
                  ),
                ]}
                filename={`${form.project.trim().replace(/\s+/g, '_') || 'code'}.${
                  form.language.toLowerCase() === 'python' ? 'py'
                  : form.language.toLowerCase() === 'javascript' ? 'js'
                  : form.language.toLowerCase() === 'typescript' ? 'ts'
                  : form.language.toLowerCase() === 'java' ? 'java'
                  : form.language.toLowerCase() === 'c++' ? 'cpp'
                  : form.language.toLowerCase() === 'c#' ? 'cs'
                  : form.language.toLowerCase() === 'go' ? 'go'
                  : form.language.toLowerCase() === 'rust' ? 'rs'
                  : form.language.toLowerCase() === 'ruby' ? 'rb'
                  : form.language.toLowerCase() === 'php' ? 'php'
                  : form.language.toLowerCase() === 'kotlin' ? 'kt'
                  : form.language.toLowerCase() === 'swift' ? 'swift'
                  : 'txt'
                }`}
              />
            )
          )}
        </div>

        {/* ── Tips column ── */}
        <div className="col-12 col-lg-4">
          <div className="card border-0 shadow-sm">
            <div className="card-body p-4">
              <h6 className="fw-semibold text-dark mb-3">
                <i className="bi bi-lightbulb text-warning me-2"></i>Tips
              </h6>
              <ul className="list-unstyled small text-secondary mb-0">
                {[
                  'Paste a complete function or class for best results.',
                  'Select the correct language for accurate analysis.',
                  'Include context comments where helpful.',
                  'Smaller, focused snippets get more precise feedback.',
                  'Use Accept / Reject / Modify on each issue to teach the AI your preferences.',
                ].map(tip => (
                  <li key={tip} className="d-flex gap-2 mb-3">
                    <i className="bi bi-check2 text-success mt-1 flex-shrink-0"></i>
                    <span>{tip}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          {/* Memory / context indicator */}
          {result && (
            <div className="card border-0 shadow-sm mt-3">
              <div className="card-body p-4">
                <h6 className="fw-semibold text-dark mb-3">
                  <i className="bi bi-cpu text-primary me-2"></i>Review Context
                </h6>
                <ul className="list-unstyled small mb-0">
                  <li className="d-flex justify-content-between mb-2">
                    <span className="text-secondary">AI analysis</span>
                    <span className={`badge bg-${result.ai_available ? 'success' : 'warning'}-subtle text-${result.ai_available ? 'success' : 'warning'}`}>
                      {result.ai_available ? 'Active' : 'Static only'}
                    </span>
                  </li>
                  <li className="d-flex justify-content-between mb-2">
                    <span className="text-secondary">Review ID</span>
                    <code className="small text-muted" style={{ fontSize: '0.7rem' }}>
                      {result.review_id?.slice(0, 8) ?? '—'}
                    </code>
                  </li>
                  <li className="d-flex justify-content-between">
                    <span className="text-secondary">Issues found</span>
                    <strong>{
                      (result.ai_issues?.length ?? 0) +
                      (result.static_issues || []).filter(
                        si => !(result.ai_issues || []).some(
                          ai => ai.title === si.title && ai.line === si.line
                        )
                      ).length
                    }</strong>
                  </li>                </ul>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
