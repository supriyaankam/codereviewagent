import { useState, useCallback, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import PropTypes from 'prop-types';
import { useAuth } from '../context/AuthContext';
import CodeComparison from '../components/CodeComparison';
import {
  getReviewForPage,
  hydrateReviewSession,
  saveReviewAutoFix,
  submitReview,
} from '../services/reviewService';
import {
  attachAcceptedAutoFix,
  createEmptyReviewSession,
  createReviewRequest,
  findingIdentity,
  getReviewPageMode,
} from '../utils/reviewPageState';
import '../styles/CodeReview.css';

// ── Constants ────────────────────────────────────────────────────────────────

const LANGUAGES = [
  'Python', 'JavaScript', 'TypeScript', 'Java', 'C#',
  'C++', 'Go', 'Rust', 'Ruby', 'PHP', 'Swift', 'Kotlin', 'Other',
];

const SEVERITY_META = {
  critical: { color: 'danger',    icon: 'bi-x-octagon-fill',      label: 'Critical' },
  error:    { color: 'danger',    icon: 'bi-exclamation-octagon',  label: 'High'     },
  warning:  { color: 'warning',   icon: 'bi-exclamation-triangle', label: 'Medium'   },
  info:     { color: 'secondary', icon: 'bi-info-circle',          label: 'Low'      },
};

const RISK_META = {
  critical: { color: 'danger',  label: 'Critical Risk' },
  high:     { color: 'danger',  label: 'High Risk'     },
  medium:   { color: 'warning', label: 'Medium Risk'   },
  low:      { color: 'success', label: 'Low Risk'      },
  info:     { color: 'info',    label: 'Informational' },
};

const REVIEW_ISSUE_PROP_TYPE = PropTypes.shape({
  severity: PropTypes.string,
  category: PropTypes.string,
  title: PropTypes.string,
  explanation: PropTypes.string,
  description: PropTypes.string,
  recommendation: PropTypes.string,
  line: PropTypes.number,
  column: PropTypes.number,
  source: PropTypes.string,
  team_standard: PropTypes.bool,
  standard_id: PropTypes.string,
  standard_name: PropTypes.string,
  confidence: PropTypes.string,
  experience_id: PropTypes.string,
  beginner_explanation: PropTypes.shape({
    problem: PropTypes.string,
    cause: PropTypes.string,
    impact: PropTypes.string,
    fix: PropTypes.string,
    example: PropTypes.string,
    concept: PropTypes.string,
  }),
});

// ── Sub-components ────────────────────────────────────────────────────────────

/**
 * Per-issue feedback panel (Accept / Reject / Modify).
 * Calls POST /api/feedback and shows confirmation inline.
 */
function IssueFeedbackPanel({
  issue, reviewId, userId, projectId, language, savedFeedback,
}) {
  const { currentUser } = useAuth();
  const [decision, setDecision]         = useState(savedFeedback?.decision ?? null);
  const [reason, setReason]             = useState(savedFeedback?.note ?? '');
  const [modified, setModified]         = useState('');
  const [submitting, setSubmitting]     = useState(false);
  const [submitted, setSubmitted]       = useState(Boolean(savedFeedback));
  const [error, setError]               = useState('');
  const [memoryStored, setMemoryStored] = useState(false);

  const needsReason = decision === 'rejected' || decision === 'modified';
  const feedbackIssueId = `finding:${issue.line || 0}:${(issue.title || '').trim().toLowerCase()}`;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!decision) return;
    setSubmitting(true);
    setError('');
    try {
      if (!currentUser) throw new Error('Sign in before submitting review feedback.');
      const body = {
        user_id:                  userId,
        review_id:                reviewId,
        issue_id:                 feedbackIssueId,
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

      const token = await currentUser.getIdToken();
      const res = await fetch('/api/feedback', {
        method:  'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
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

  IssueFeedbackPanel.propTypes = {
    issue: REVIEW_ISSUE_PROP_TYPE.isRequired,
    reviewId: PropTypes.string.isRequired,
    userId: PropTypes.string.isRequired,
    projectId: PropTypes.string.isRequired,
    language: PropTypes.string.isRequired,
    savedFeedback: PropTypes.object,
  };

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

SourceBadge.propTypes = {
  source: PropTypes.string,
};

/**
 * Single issue card — severity, source tool, line, explanation, recommendation, feedback panel.
 */
function IssueFixSuggestion({
  issue, code, language, onApply, ineffective,
}) {
  const { currentUser } = useAuth();
  const [loading, setLoading] = useState(false);
  const [proposal, setProposal] = useState(null);
  const [error, setError] = useState('');
  const requestInProgress = useRef(false);

  useEffect(() => {
    setProposal(null);
    setError('');
  }, [code]);

  async function handleSuggestFix() {
    if (requestInProgress.current) return;
    requestInProgress.current = true;
    setLoading(true);
    setError('');
    setProposal(null);
    try {
      if (!currentUser) throw new Error('Sign in before requesting an AI fix.');
      const token = await currentUser.getIdToken();
      const response = await fetch('/api/reviews/fix', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          code,
          language,
          source_language: language,
          issues: [issue],
        }),
      });
      if (!response.ok) {
        const detail = await response.text().catch(() => `HTTP ${response.status}`);
        throw new Error(detail);
      }
      const data = await response.json();
      if (!data.ai_available || !data.fixed_code || data.fixed_code === code) {
        setError(data.error || 'A confident correction was not available. The source code was not changed.');
        return;
      }
      setProposal(data);
    } catch (requestError) {
      setError(requestError.message || 'Could not generate a fix suggestion.');
    } finally {
      requestInProgress.current = false;
      setLoading(false);
    }
  }

  if (issue.severity === 'info') return null;

  return (
    <div className="issue-fix-suggestion mt-3">
      {!proposal && !ineffective && (
        <button
          type="button"
          className="btn btn-sm btn-outline-primary"
          onClick={handleSuggestFix}
          disabled={loading}
        >
          {loading
            ? <><span className="spinner-border spinner-border-sm me-2" role="status" />Generating proposal…</>
            : <><i className="bi bi-magic me-2" />Suggest a fix for this finding</>}
        </button>
      )}
      {!proposal && ineffective && (
        <div className="alert alert-warning py-2 small mt-2 mb-0" role="status">
          The updated review still reports this finding, so the accepted fix did not resolve the matching rule. Review the code manually before requesting another suggestion.
        </div>
      )}
      {error && <div className="alert alert-warning py-2 small mt-2 mb-0">{error}</div>}
      {proposal && (
        <div className="mt-3">
          <div className="alert alert-info py-2 small">
            <strong>Review this proposal before applying it.</strong>{' '}
            {proposal.summary || 'The AI proposed a targeted correction.'}
            {proposal.validation?.message && (
              <div className="mt-1">
                Validation: {proposal.validation.message}
              </div>
            )}
          </div>
          <CodeComparison
            original={proposal.original_region || code}
            proposed={proposal.proposed_region || proposal.fixed_code}
            language={language}
            startLine={proposal.line_range?.start || 1}
            onApply={() => onApply(proposal.fixed_code, proposal)}
          />
          <button type="button" className="btn btn-sm btn-link px-0 mt-2" onClick={() => setProposal(null)}>
            Reject proposal
          </button>
        </div>
      )}
    </div>
  );
}

IssueFixSuggestion.propTypes = {
  issue: PropTypes.shape({
    severity: PropTypes.string,
    title: PropTypes.string,
    category: PropTypes.string,
    line: PropTypes.number,
    source: PropTypes.string,
    recommendation: PropTypes.string,
  }).isRequired,
  code: PropTypes.string.isRequired,
  language: PropTypes.string.isRequired,
  onApply: PropTypes.func.isRequired,
  ineffective: PropTypes.bool,
};

function IssueCard({
  issue, index, reviewId, userId, projectId, language, code, beginnerMode, onApplyFix,
  savedFeedback, ineffectiveFindingKeys,
}) {
  const [open, setOpen] = useState(index < 3);
  const sev = SEVERITY_META[issue.severity] ?? SEVERITY_META.info;
  const isTeamStandard = issue.team_standard || issue.title?.toUpperCase().includes('[TEAM STANDARD]');
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
          {issue.source === 'ai' && issue.confidence !== 'high' && (
            <span className="badge bg-warning-subtle text-warning-emphasis">Potential · verify context</span>
          )}
          <span className="badge bg-light text-secondary text-capitalize border">
            {issue.category}
          </span>
          {isTeamStandard && (
            <span className="badge bg-primary-subtle text-primary">
              <i className="bi bi-shield-check me-1"></i>
              Team Standard: {issue.standard_name || issue.title?.replace(/^\[Team Standard\]\s*/i, '')}
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
          {beginnerMode ? (
            <div className="beginner-explanation mb-3">
              <p className="small fw-medium text-dark mb-2">
                <i className="bi bi-mortarboard me-1 text-primary"></i>Beginner-friendly explanation
              </p>
              {[
                ['What is the problem?', issue.beginner_explanation?.problem || issue.title],
                ['Why does it happen?', issue.beginner_explanation?.cause || issue.explanation || issue.description],
                ['Why should you care?', issue.beginner_explanation?.impact || issue.explanation || issue.description],
                ['How can it be fixed?', issue.beginner_explanation?.fix || issue.recommendation],
                ['Example', issue.beginner_explanation?.example],
                ['Programming concept', issue.beginner_explanation?.concept],
              ].filter(([, value]) => value).map(([label, value]) => (
                <p className="small text-secondary mb-2" key={label}>
                  <strong className="text-dark">{label} </strong>{value}
                </p>
              ))}
            </div>
          ) : (issue.explanation || issue.description) && (
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

          <IssueFixSuggestion
            issue={issue}
            code={code}
            language={language}
            ineffective={ineffectiveFindingKeys.includes(findingIdentity(issue))}
            onApply={(fixedCode, proposal) => onApplyFix(fixedCode, {
              ...(proposal || {}),
              original_code: code,
              changes: proposal?.changes || [{
                issue: issue.title || 'Review finding',
                change: 'Applied the accepted issue fix.',
              }],
              summary: proposal?.summary || issue.recommendation || '',
              fixed_code: fixedCode,
              source_language: language,
              target_language: language,
              is_conversion: false,
              accepted: true,
            }, [issue])}
          />

          {/* Feedback panel — skip for syntax errors (nothing to accept/reject) */}
          {!isSyntaxError && (
            <IssueFeedbackPanel
              issue={issue}
              reviewId={reviewId}
              userId={userId}
              projectId={projectId}
              language={language}
              savedFeedback={savedFeedback}
            />
          )}
        </div>
      )}
    </div>
  );
}

IssueCard.propTypes = {
  issue: REVIEW_ISSUE_PROP_TYPE.isRequired,
  index: PropTypes.number.isRequired,
  reviewId: PropTypes.string.isRequired,
  userId: PropTypes.string.isRequired,
  projectId: PropTypes.string.isRequired,
  language: PropTypes.string.isRequired,
  code: PropTypes.string.isRequired,
  beginnerMode: PropTypes.bool.isRequired,
  onApplyFix: PropTypes.func.isRequired,
  savedFeedback: PropTypes.object,
  ineffectiveFindingKeys: PropTypes.arrayOf(PropTypes.string).isRequired,
};

/**
 * Full review results panel.
 *
 * Issue list logic:
 *   - allIssues = AI findings
 *   - extraStatic = static findings not already represented in AI output
 *   - totalCount = allIssues.length + extraStatic.length  (no double-counting)
 *
 * "No issues found" is only shown when BOTH lists are empty.
 */
function QualityScore({ qualityScore }) {
  if (!qualityScore) return null;
  const color = qualityScore.score >= 80 ? 'success' : qualityScore.score >= 60 ? 'warning' : 'danger';

  return (
    <section className="card quality-score-card border-0 shadow-sm mb-4" aria-label="Code quality score">
      <div className="card-body p-4">
        <div className="d-flex align-items-center gap-3 flex-wrap">
          <div className={`quality-score-value text-${color}`}>{qualityScore.score}<span className="fs-6 text-secondary">/100</span></div>
          <div>
            <h6 className="fw-bold mb-1">Code quality score</h6>
            <p className="small text-secondary mb-0">
              Deterministic score from unique static findings and measured Python complexity; AI findings are excluded.
              This score is not a guarantee that code is bug-free.
            </p>
          </div>
        </div>
        <div className="d-flex flex-wrap gap-3 mt-4">
          {Object.entries(qualityScore.categories || {}).map(([category, score]) => (
            <div className="score-category" key={category}>
              <div className="d-flex justify-content-between small mb-1 text-capitalize">
                <span>{category}</span><strong>{score}</strong>
              </div>
              <div className="progress" role="progressbar" aria-label={`${category} score`} aria-valuenow={score} aria-valuemin="0" aria-valuemax="100">
                <div className={`progress-bar bg-${score >= 80 ? 'success' : score >= 60 ? 'warning' : 'danger'}`} style={{ width: `${score}%` }} />
              </div>
            </div>
          ))}
        </div>
        <details className="mt-3">
          <summary className="small fw-semibold" style={{ cursor: 'pointer' }}>How this score was calculated</summary>
          <ul className="small text-secondary mt-2 mb-2">
            {(qualityScore.factors || []).map((factor) => <li key={factor}>{factor}</li>)}
          </ul>
          {qualityScore.method?.notes && (
            <p className="small text-secondary mb-2">{qualityScore.method.notes}</p>
          )}
          <p className="small text-secondary mb-0">
            Measured nonblank lines: {qualityScore.metrics?.nonblank_lines ?? '—'}.
            {' '}Maximum Python function complexity: {
              qualityScore.metrics?.complexity_measurable
                ? qualityScore.metrics.max_cyclomatic_complexity
                : 'not measured'
            }.
          </p>
        </details>
      </div>
    </section>
  );
}

QualityScore.propTypes = {
  qualityScore: PropTypes.shape({
    score: PropTypes.number.isRequired,
    categories: PropTypes.object,
    factors: PropTypes.arrayOf(PropTypes.string),
    metrics: PropTypes.object,
    method: PropTypes.shape({ notes: PropTypes.string }),
  }),
};

function ReviewResults({
  result, userId, projectId, language, code, onApplyFix, sourceChanged,
}) {
  const [beginnerMode, setBeginnerMode] = useState(false);
  const riskMeta = result.overall_risk ? RISK_META[result.overall_risk] : null;

  const aiIssues = Array.isArray(result.ai_issues) ? result.ai_issues : [];
  const staticIssues = Array.isArray(result.static_issues) ? result.static_issues : [];
  const ineffectiveFindingKeys = result.auto_fix?.ineffective_finding_keys || [];
  const findingsComplete = Array.isArray(result.ai_issues) && Array.isArray(result.static_issues);
  const analysisFailed = result.analysis_status === 'failed';
  const staticCounts = result.static_summary?.counts;
  const staticCapabilities = result.static_metadata?.capabilities || [];
  const staticChecksDescription = staticCapabilities.includes('ast')
    ? 'Python AST, Pylint, Bandit, and secret checks found nothing.'
    : result.static_metadata?.message
      || (result.static_supported === true
        ? 'Available syntax and team-standard checks found nothing.'
        : 'No static analyzer was available for this language.');
  const summaryText = result.ai_available === false
    ? (typeof staticCounts?.total === 'number'
      ? `Static analysis found ${staticCounts.total} finding${staticCounts.total === 1 ? '' : 's'}.`
      : result.static_supported === true ? 'Static analysis completed.' : '')
    : result.summary;
  const allIssues = [...aiIssues];

  // Extra static issues not already represented in allIssues
  const extraStatic = staticIssues.filter(
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

  const aiOnlyIssues     = aiIssues.filter(i => i.severity !== 'info');
  const aiInfoIssues     = allIssues.filter(i => i.severity === 'info');
  const staticOnlyReal   = extraStatic.filter(i => i.severity !== 'info');
  const staticOnlyInfo   = extraStatic.filter(i => i.severity === 'info');
  const savedFeedbackFor = (index) => (result.feedback || [])
    .find((item) => item.issue_id === String(index));

  return (
    <div className="review-results mt-4">

      {/* ── Header row ── */}
      <div className="d-flex align-items-center justify-content-between flex-wrap gap-3 mb-4">
        <h5 className="fw-bold mb-0">Review Results</h5>
        <div className="d-flex align-items-center gap-2 flex-wrap">
          <button
            type="button"
            className={`btn btn-sm ${beginnerMode ? 'btn-primary' : 'btn-outline-secondary'}`}
            onClick={() => setBeginnerMode((enabled) => !enabled)}
            aria-pressed={beginnerMode}
          >
            <i className="bi bi-mortarboard me-1" />
            {beginnerMode ? 'Beginner mode on' : 'Beginner explanations'}
          </button>
          {result.overall_risk && (
            <span className={`badge bg-${riskMeta?.color || 'secondary'} fs-6 px-3 py-2`}>
              {riskMeta?.label || result.overall_risk}
            </span>
          )}
          {typeof result.ai_available === 'boolean' && !result.ai_available && (
            <span className="badge bg-warning-subtle text-warning border border-warning-subtle">
              <i className="bi bi-cpu me-1"></i>
              {result.static_supported === true
                ? 'Static analysis only'
                : result.static_supported === false
                  ? 'AI unavailable · no static checks'
                  : 'AI unavailable · analyzer status not recorded'}
            </span>
          )}
          {typeof result.ai_available !== 'boolean' && (
            <span className="badge bg-secondary-subtle text-secondary border">
              AI availability not recorded
            </span>
          )}
          {codeIsClean && (
            <span className="badge bg-success fs-6 px-3 py-2">
              <i className="bi bi-check-circle me-1"></i>Code looks good
            </span>
          )}
        </div>
      </div>

      <QualityScore qualityScore={result.quality_score} />

      {result.restorationUnavailable?.length > 0 && (
        <div className="alert alert-warning small" role="status">
          <strong>Some saved review information is unavailable:</strong>{' '}
          {result.restorationUnavailable.join(', ')}.
          Available source code, summary, scores and findings are still shown below.
        </div>
      )}

      {result.ai_available === false && (result.ai_failure_reason || result.fallback_reason) && (
        <div className="alert alert-warning small" role="status">
          <strong>AI review unavailable.</strong>{' '}
          {result.ai_failure_reason || result.fallback_reason}
          {result.ai_failure_category && (
            <span className="ms-1">({result.ai_failure_category})</span>
          )}
        </div>
      )}

      {analysisFailed && (
        <div className="alert alert-danger small" role="alert">
          Both AI and supported static analysis were unavailable. Findings, issue counts,
          risk, and quality score are not available for this review.
        </div>
      )}
      {result.static_analysis_error && (
        <div className="alert alert-warning small" role="status">
          Static analysis could not complete ({result.static_analysis_error}).
        </div>
      )}
      {result.static_metadata?.message && (
        <div className="alert alert-info small" role="status">
          {result.static_metadata.message}
        </div>
      )}

      {result.ai_code_truncated && (
        <div className="alert alert-warning small">
          The AI reviewed the first 12,000 characters. Available static checks inspect the full submitted source.
        </div>
      )}

      {sourceChanged && (
        <div className="alert alert-warning d-flex align-items-center gap-2">
          <i className="bi bi-pencil-square" />
          <span>The editor contains an applied proposal. Findings below refer to the prior review; review the updated source to refresh them.</span>
        </div>
      )}

      {/* ── Summary card ── */}
      {summaryText && (
        <div className={`card border-0 shadow-sm mb-4 border-start border-4 border-${
          codeIsClean ? 'success' : result.ai_available ? 'primary' : 'warning'}`}>
          <div className="card-body px-4 py-3">
            <p className="small fw-semibold text-dark mb-1">
              <i className={`bi ${codeIsClean ? 'bi-check-circle text-success' : result.ai_available ? 'bi-stars text-primary' : 'bi-search text-warning'} me-2`}></i>
              {result.ai_available === true
                ? 'AI Summary'
                : result.ai_available === false
                  ? result.static_supported === true
                    ? 'Static Analysis Summary'
                    : 'Analysis Summary'
                  : 'Saved Summary'}
            </p>
            <p className="small text-secondary mb-0">{summaryText}</p>
          </div>
        </div>
      )}

      {/* ── "Code looks good" banner when only style suggestions exist ── */}
      {codeIsClean && findingsComplete && !analysisFailed && (
        <div className="card border-0 shadow-sm border-start border-4 border-success mb-4">
          <div className="card-body px-4 py-3 d-flex align-items-center gap-3">
            <i className="bi bi-check-circle-fill text-success fs-3 flex-shrink-0"></i>
            <div>
              <p className="fw-semibold text-dark mb-1">No errors or warnings found</p>
              <p className="small text-secondary mb-0">
                No high-confidence issues were reported by the available checks. This review cannot guarantee the code is free of bugs.
                {infoIssues.length > 0 && ` There are ${infoIssues.length} optional style suggestion${infoIssues.length > 1 ? 's' : ''} below.`}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── Stats row ── */}
      {findingsComplete && !analysisFailed ? (
        <div className="row g-3 mb-4">
          {[
            { label: 'Real Issues',
              value: realIssues.length,
              color: realIssues.length > 0 ? 'danger' : 'success',
              icon: realIssues.length > 0 ? 'bi-exclamation-triangle-fill' : 'bi-check-circle-fill' },
            { label: 'Critical',
              value: allCombined.filter(i => i.severity === 'critical').length,
              color: 'danger', icon: 'bi-x-octagon-fill' },
            { label: 'Warnings',
              value: allCombined.filter(i => i.severity === 'warning').length,
              color: 'warning', icon: 'bi-exclamation-triangle' },
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
      ) : (
        <div className="alert alert-info small" role="status">
          Detailed finding counts are unavailable for this saved review; no zero counts are inferred.
          {result.issues_count !== undefined && ` The saved record reports ${result.issues_count} total issue(s).`}
        </div>
      )}

      {/* ── Real problems (critical / error / warning) ── */}
      {(aiOnlyIssues.length > 0 || staticOnlyReal.length > 0) && (
        <div className="mb-4">
          <h6 className="fw-semibold text-dark mb-3">
            <i className="bi bi-exclamation-triangle-fill text-warning me-2"></i>
            Issues to Fix
            <span className="badge bg-danger-subtle text-danger ms-2">{aiOnlyIssues.length + staticOnlyReal.length}</span>
          </h6>
          {aiOnlyIssues.map((issue, i) => (
            <IssueCard key={`${result.review_id}-ai-${i}`} issue={issue} index={i}
              reviewId={result.review_id} userId={userId} projectId={projectId} language={language}
              code={code} beginnerMode={beginnerMode} onApplyFix={onApplyFix}
              ineffectiveFindingKeys={ineffectiveFindingKeys}
              savedFeedback={savedFeedbackFor(i) || (result.feedback || []).find(
                item => item.issue_id === `finding:${issue.line || 0}:${(issue.title || '').trim().toLowerCase()}`
              )} />
          ))}
          {staticOnlyReal.map((issue, i) => (
            <IssueCard key={`${result.review_id}-str-${i}`} issue={{ ...issue, explanation: issue.description, confidence: 'high' }}
              index={i + aiOnlyIssues.length}
              reviewId={result.review_id} userId={userId} projectId={projectId} language={language}
              code={code} beginnerMode={beginnerMode} onApplyFix={onApplyFix}
              ineffectiveFindingKeys={ineffectiveFindingKeys}
              savedFeedback={savedFeedbackFor(i + aiOnlyIssues.length) || (result.feedback || []).find(
                item => item.issue_id === `finding:${issue.line || 0}:${(issue.title || '').trim().toLowerCase()}`
              )} />
          ))}
        </div>
      )}

      {/* ── Style suggestions (info only) — collapsed by default ── */}
      {(aiInfoIssues.length > 0 || staticOnlyInfo.length > 0) && (
        <details className="mb-4" open={Array.isArray(result.restorationUnavailable)}>
          <summary className="fw-semibold text-secondary small mb-2" style={{ cursor: 'pointer', listStyle: 'none' }}>
            <i className="bi bi-info-circle me-2 text-secondary"></i>
            Style Suggestions
            <span className="badge bg-secondary-subtle text-secondary ms-2">{aiInfoIssues.length + staticOnlyInfo.length}</span>
            <span className="text-muted ms-2" style={{ fontSize: '0.72rem' }}>(click to expand)</span>
          </summary>
          <div className="mt-2">
            {aiInfoIssues.map((issue, i) => (
              <IssueCard key={`${result.review_id}-ai-info-${i}`} issue={issue} index={i}
                reviewId={result.review_id} userId={userId} projectId={projectId} language={language}
                code={code} beginnerMode={beginnerMode} onApplyFix={onApplyFix}
                ineffectiveFindingKeys={ineffectiveFindingKeys}
                savedFeedback={savedFeedbackFor(i) || (result.feedback || []).find(
                  item => item.issue_id === `finding:${issue.line || 0}:${(issue.title || '').trim().toLowerCase()}`
                )} />
            ))}
            {staticOnlyInfo.map((issue, i) => (
              <IssueCard key={`${result.review_id}-st-info-${i}`} issue={{ ...issue, explanation: issue.description, confidence: 'high' }}
                index={i + aiInfoIssues.length}
                reviewId={result.review_id} userId={userId} projectId={projectId} language={language}
                code={code} beginnerMode={beginnerMode} onApplyFix={onApplyFix}
                ineffectiveFindingKeys={ineffectiveFindingKeys}
                savedFeedback={savedFeedbackFor(i + aiInfoIssues.length) || (result.feedback || []).find(
                  item => item.issue_id === `finding:${issue.line || 0}:${(issue.title || '').trim().toLowerCase()}`
                )} />
            ))}
          </div>
        </details>
      )}

      {/* ── Truly zero issues — no static, no AI findings at all ── */}
      {totalCount === 0 && findingsComplete && !analysisFailed && (
        <div className="card border-0 shadow-sm">
          <div className="card-body text-center py-5">
            <i className="bi bi-check-circle-fill text-success fs-1 mb-3 d-block"></i>
            <h6 className="fw-semibold text-dark mb-2">No issues found</h6>
            <p className="text-secondary small mb-0">
              {result.static_supported
                ? staticChecksDescription
                : result.static_supported === false
                  ? 'No static analyzer was available for this language.'
                  : 'Static analyzer availability was not recorded.'}
              {result.ai_available === false
                ? ' AI analysis was unavailable for this review.'
                : result.ai_available === true
                  ? ' No AI findings were reported, but automated review cannot prove code is defect-free.'
                  : ' AI availability was not recorded.'}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

ReviewResults.propTypes = {
  result: PropTypes.shape({
    overall_risk: PropTypes.string,
    ai_issues: PropTypes.arrayOf(REVIEW_ISSUE_PROP_TYPE),
    static_issues: PropTypes.arrayOf(REVIEW_ISSUE_PROP_TYPE),
    analysis_status: PropTypes.string,
    static_summary: PropTypes.shape({
      counts: PropTypes.shape({ total: PropTypes.number }),
    }),
    static_supported: PropTypes.bool,
    summary: PropTypes.string,
    ai_available: PropTypes.bool,
    ai_failure_reason: PropTypes.string,
    fallback_reason: PropTypes.string,
    ai_failure_category: PropTypes.string,
    static_analysis_error: PropTypes.string,
    static_metadata: PropTypes.shape({
      message: PropTypes.string,
      capabilities: PropTypes.arrayOf(PropTypes.string),
    }),
    review_id: PropTypes.string,
    quality_score: PropTypes.object,
    auto_fix: PropTypes.shape({
      ineffective_finding_keys: PropTypes.arrayOf(PropTypes.string),
    }),
    ai_code_truncated: PropTypes.bool,
    feedback: PropTypes.arrayOf(PropTypes.object),
    issues_count: PropTypes.number,
    restorationUnavailable: PropTypes.arrayOf(PropTypes.string),
  }).isRequired,
  userId: PropTypes.string.isRequired,
  projectId: PropTypes.string.isRequired,
  language: PropTypes.string.isRequired,
  code: PropTypes.string.isRequired,
  onApplyFix: PropTypes.func.isRequired,
  sourceChanged: PropTypes.bool.isRequired,
};

// ── Fix Code Panel ────────────────────────────────────────────────────────────

/**
 * Shows after a review. Lets the user:
 *  1. Click "Fix Code" → calls POST /api/reviews/fix → shows corrected code
 *  2. Download the corrected code as a .py (or language-appropriate) file
 *  3. See the re-analysis results (remaining issues after the fix)
 */
function FixPanel({
  originalCode, language, sourceLanguage, issues, filename, reviewId, savedAutoFix, onApplyFix,
}) {
  const { currentUser } = useAuth();
  const [fixing, setFixing]         = useState(false);
  const [fixResult, setFixResult]   = useState(savedAutoFix || null);
  const [fixError, setFixError]     = useState('');
  const [showDiff, setShowDiff]     = useState('fixed');
  const fixRequestInProgress = useRef(false);

  // Detect language mismatch from props
  const isConversion = sourceLanguage && sourceLanguage.toLowerCase() !== language.toLowerCase();
  const exceedsFixLimit = originalCode.length > 14_000;

  async function handleFix() {
    if (fixRequestInProgress.current) return;
    fixRequestInProgress.current = true;
    setFixing(true);
    setFixError('');
    setFixResult(null);
    try {
      if (!currentUser) throw new Error('Sign in before requesting an AI fix.');
      const token = await currentUser.getIdToken();
      const res = await fetch('/api/reviews/fix', {
        method:  'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
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
      if (!data.ai_available) {
        setFixError(data.error || data.summary || 'The fix could not be generated. The source code was not changed.');
        return;
      }
      const autoFix = { ...data, original_code: originalCode, accepted: false };
      await saveReviewAutoFix(reviewId, autoFix, currentUser);
      setFixResult(autoFix);
    } catch (err) {
      setFixError(err.message || 'Fix failed. Is the backend running?');
    } finally {
      fixRequestInProgress.current = false;
      setFixing(false);
    }
  }

  FixPanel.propTypes = {
    originalCode: PropTypes.string.isRequired,
    language: PropTypes.string.isRequired,
    sourceLanguage: PropTypes.string,
    issues: PropTypes.arrayOf(REVIEW_ISSUE_PROP_TYPE).isRequired,
    filename: PropTypes.string,
    reviewId: PropTypes.string.isRequired,
    savedAutoFix: PropTypes.object,
    onApplyFix: PropTypes.func.isRequired,
  };

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
  const staticValidationAvailable = Boolean(fixResult?.reanalysis?.supported);
  const isClean         = fixResult && staticValidationAvailable && remainingCount === 0;

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
                disabled={fixing || exceedsFixLimit}
              >
                {fixing
                  ? <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                      {isConversion ? 'Converting…' : 'Fixing…'}</>
                  : <><i className="bi bi-wrench-adjustable me-2"></i>
                      {isConversion ? `Convert to ${language}` : 'Fix Code'}</>}
              </button>
            )}
          </div>

          {exceedsFixLimit && (
            <div className="alert alert-warning py-2 small">
              AI fix proposals are limited to 14,000 characters. The original source remains unchanged.
            </div>
          )}

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
              {fixResult.error && fixResult.summary !== `Auto-fix unavailable: ${fixResult.error}` && (
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
                  {fixResult.original_code || originalCode}
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
                <button
                  className="btn btn-success btn-sm px-4"
                  onClick={() => onApplyFix(fixResult.fixed_code, {
                    ...fixResult,
                    original_code: fixResult.original_code || originalCode,
                    accepted: true,
                  }, issues)}
                >
                  <i className="bi bi-check2-circle me-2"></i>Apply fixed code to editor
                </button>
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
              {fixResult.accepted && (
                <div className="alert alert-success py-2 small" role="status">
                  This Auto-Fix was accepted and is saved with the review.
                </div>
              )}

              {/* Re-analysis results */}
              <div className={`p-3 rounded border ${isClean ? 'border-success bg-success-subtle' : 'border-warning bg-warning-subtle'}`}>
                <h6 className={`fw-semibold mb-2 ${isClean ? 'text-success' : 'text-warning-emphasis'}`}>
                  <i className={`bi ${isClean ? 'bi-check-circle-fill' : 'bi-exclamation-triangle'} me-2`}></i>
                  {staticValidationAvailable ? 'Static Re-Analysis' : 'Validation unavailable'}
                </h6>
                {!staticValidationAvailable ? (
                  <p className="small mb-0 text-secondary">
                    No static validator is available for this language. The proposal has not been validated; review and test it before use.
                  </p>
                ) : isClean ? (
                  <p className="small mb-0 text-success-emphasis">
                    No findings were detected by the available Python checks. This does not guarantee that the code is free of bugs.
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
  const { currentUser } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  const initialSession = createEmptyReviewSession();
  const [form, setForm]       = useState(initialSession.form);
  const [loading, setLoading] = useState(false);
  const [restoreLoading, setRestoreLoading] = useState(Boolean(searchParams.get('review_id')));
  const [error, setError]     = useState(initialSession.error);
  const [result, setResult]   = useState(initialSession.result);
  const [reviewedCode, setReviewedCode] = useState(initialSession.reviewedCode);
  const [sourceChanged, setSourceChanged] = useState(initialSession.sourceChanged);
  const skipRestoreRef = useRef(false);
  const reviewPageMode = getReviewPageMode(searchParams);
  const requestedReviewId = reviewPageMode.type === 'saved' ? reviewPageMode.reviewId : null;

  useEffect(() => {
    if (skipRestoreRef.current) {
      skipRestoreRef.current = false;
      return;
    }
    if (!requestedReviewId || !currentUser) {
      const emptySession = createEmptyReviewSession();
      setForm(emptySession.form);
      setReviewedCode(emptySession.reviewedCode);
      setResult(emptySession.result);
      setSourceChanged(emptySession.sourceChanged);
      setError(emptySession.error);
      setRestoreLoading(false);
      return;
    }

    let active = true;
    setRestoreLoading(true);
    setError('');
    const emptySession = createEmptyReviewSession();
    setForm(emptySession.form);
    setReviewedCode(emptySession.reviewedCode);
    setResult(emptySession.result);
    setSourceChanged(emptySession.sourceChanged);
    const restoreReview = async () => {
      try {
        const saved = await getReviewForPage(requestedReviewId, currentUser);
        if (!active) return;
        if (!saved) return;

        const restored = hydrateReviewSession(saved);
        setForm(restored.form);
        setReviewedCode(restored.reviewedCode);
        setResult(restored.result);
        setSourceChanged(false);
      } catch (restoreError) {
        if (active) {
          const failedSession = createEmptyReviewSession();
          setForm(failedSession.form);
          setReviewedCode(failedSession.reviewedCode);
          setResult(failedSession.result);
          setSourceChanged(failedSession.sourceChanged);
          setError(restoreError.message || 'Could not restore the saved review.');
        }
      } finally {
        if (active) setRestoreLoading(false);
      }
    };
    restoreReview();

    return () => { active = false; };
  }, [currentUser, requestedReviewId]);

  function handleChange(e) {
    setForm((current) => ({ ...current, [e.target.name]: e.target.value }));
    if (result) setResult(null);   // reset results on form change
    setSourceChanged(false);
    setError('');
  }

  const runReview = useCallback(async () => {
    if (!currentUser) {
      setError('Sign in with Firebase before submitting code for review.');
      return;
    }
    setLoading(true);
    setError('');
    setResult(null);
    setSourceChanged(false);

    try {
      if (requestedReviewId) {
        skipRestoreRef.current = true;
        setSearchParams({ new: '1' }, { replace: true });
      }
      const codeBeingReviewed = form.code;
      const body = createReviewRequest({
        userId: currentUser.uid,
        projectName: form.project,
        language: form.language,
        code: codeBeingReviewed,
      });

      const data = await submitReview(body, currentUser);
      setResult(data);
      setReviewedCode(codeBeingReviewed);
    } catch (err) {
      setError(err.message || 'Could not complete the review. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [form, currentUser, requestedReviewId, setSearchParams]);

  const handleSubmit = useCallback((event) => {
    event.preventDefault();
    return runReview();
  }, [runReview]);

  async function applyFixToEditor(fixedCode, autoFix, attemptedFindings = []) {
    try {
      if (!currentUser || !result?.review_id) {
        throw new Error('The saved review is unavailable; the Auto-Fix was not applied.');
      }
      if (fixedCode === form.code) {
        throw new Error('The proposed fix does not change the source code.');
      }
      const acceptedFix = {
        ...autoFix,
        original_code: autoFix?.original_code || form.code,
        fixed_code: fixedCode,
        accepted: true,
      };
      await saveReviewAutoFix(result.review_id, acceptedFix, currentUser);
      setForm((current) => ({ ...current, code: fixedCode }));
      setReviewedCode(fixedCode);
      setResult(null);
      setSourceChanged(false);
      setError('');
      setLoading(true);

      if (requestedReviewId) {
        skipRestoreRef.current = true;
        setSearchParams({ new: '1' }, { replace: true });
      }

      let updatedReview;
      try {
        const body = createReviewRequest({
          userId: currentUser.uid,
          projectName: form.project,
          language: form.language,
          code: fixedCode,
        });
        updatedReview = await submitReview(body, currentUser);
      } catch (reviewError) {
        throw new Error(
          `The Auto-Fix was applied, but updated code reanalysis failed: ${reviewError.message}`,
        );
      }

      const refreshedResult = attachAcceptedAutoFix(
        updatedReview,
        acceptedFix,
        attemptedFindings,
      );
      setResult(refreshedResult);
      try {
        await saveReviewAutoFix(
          refreshedResult.review_id,
          refreshedResult.auto_fix,
          currentUser,
        );
      } catch (saveError) {
        setError(
          `The updated code was reviewed, but the before/after Auto-Fix record could not be saved: ${saveError.message}`,
        );
      }
    } catch (fixSaveError) {
      setError(fixSaveError.message || 'Could not save the accepted Auto-Fix.');
    } finally {
      setLoading(false);
    }
  }

  function startNewReview() {
    setSearchParams({ new: '1' }, { replace: true });
    const emptySession = createEmptyReviewSession();
    setForm(emptySession.form);
    setResult(emptySession.result);
    setReviewedCode(emptySession.reviewedCode);
    setSourceChanged(emptySession.sourceChanged);
    setError(emptySession.error);
  }

  function restoreReviewedCode() {
    setForm((current) => ({ ...current, code: reviewedCode }));
    setSourceChanged(false);
  }

  const isReady = form.project.trim() && form.language && form.code.trim() && !loading;

  if (restoreLoading) {
    return (
      <div className="p-4 p-md-5 text-center" role="status">
        <div className="spinner-border text-primary" aria-hidden="true"></div>
        <p className="small text-secondary mt-3 mb-0">Loading your saved review…</p>
      </div>
    );
  }

  return (
    <div className="p-4 p-md-5">
      {/* Page header */}
      <div className="d-flex align-items-start justify-content-between flex-wrap gap-3 mb-5">
        <div>
          <h4 className="fw-bold text-dark mb-1">Code Review</h4>
          <p className="text-secondary small mb-0">
            Paste your code and let the AI agent review it against your team&apos;s standards.
          </p>
        </div>
        <button type="button" className="btn btn-outline-primary btn-sm" onClick={startNewReview}>
          <i className="bi bi-plus-lg me-1"></i>New Review
        </button>
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
                      <option value="" disabled>Select a language...</option>
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
                    required spellCheck={false} maxLength={100_000}
                  />
                  <div className="form-text">
                    {form.code.length > 0
                      ? `${form.code.split('\n').length} lines · ${form.code.length.toLocaleString()} / 100,000 chars`
                      : 'Supports any snippet or full file · Python has AST, Pylint, and Bandit checks'}
                  </div>
                </div>

                {sourceChanged && (
                  <div className="alert alert-warning d-flex justify-content-between align-items-center gap-2 flex-wrap">
                    <span><i className="bi bi-info-circle me-2" />Your original reviewed source is preserved. The editor now contains the accepted proposal.</span>
                    <button type="button" className="btn btn-sm btn-outline-secondary" onClick={restoreReviewedCode}>
                      Restore reviewed source
                    </button>
                  </div>
                )}

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
                    : <><i className="bi bi-stars me-2"></i>{sourceChanged ? 'Review Updated Code' : 'Review Code'}</>}
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
              code={form.code}
              onApplyFix={applyFixToEditor}
              sourceChanged={sourceChanged}
            />
          )}

          {/* Fix Code panel — only show when review has issues */}
          {result && !sourceChanged && (
            (result.ai_issues?.length > 0 || result.static_issues?.length > 0 || result.auto_fix) && (
              <FixPanel
                key={result.review_id}
                originalCode={form.code}
                language={form.language}
                sourceLanguage={result.language || form.language}
                reviewId={result.review_id}
                savedAutoFix={result.auto_fix}
                onApplyFix={applyFixToEditor}
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
                      {result.ai_available ? 'Active' : result.static_supported ? 'Static only' : 'Unavailable'}
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
                    <strong>{result.issues_count ?? (
                      Array.isArray(result.ai_issues) || Array.isArray(result.static_issues)
                        ? (result.ai_issues || []).length +
                          (result.static_issues || []).filter(
                            si => !(result.ai_issues || []).some(
                              ai => ai.title === si.title && ai.line === si.line
                            )
                          ).length
                        : '—'
                    )}</strong>
                  </li>                </ul>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
