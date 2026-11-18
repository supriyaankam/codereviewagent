export async function submitReview(body, currentUser) {
  if (!currentUser) throw new Error('Sign in before submitting code for review.');

  const token = await currentUser.getIdToken();
  const response = await fetch('/api/review', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.detail || `Review request failed (HTTP ${response.status}).`);
  }
  if (data.saved_to_db !== true || !data.review_id || data.review_id === 'local') {
    throw new Error('The review was analyzed but not saved to Firestore. Please retry.');
  }

  return data;
}

async function authenticatedReviewRequest(path, currentUser, options = {}) {
  if (!currentUser) throw new Error('Sign in to access saved reviews.');

  const token = await currentUser.getIdToken();
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
  });
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.detail || `Saved review request failed (HTTP ${response.status}).`);
  }
  return data;
}

export async function getLatestReview(currentUser) {
  return authenticatedReviewRequest('/api/reviews/latest', currentUser);
}

export async function getReview(reviewId, currentUser) {
  return authenticatedReviewRequest(
    `/api/reviews/${encodeURIComponent(reviewId)}`,
    currentUser,
  );
}

export async function getReviewForPage(reviewId, currentUser) {
  if (!reviewId) return null;
  return getReview(reviewId, currentUser);
}

export function getReviewDetailPath(reviewId) {
  return `/review?review_id=${encodeURIComponent(reviewId)}`;
}

export function getSavedIssueCount(review) {
  return Number.isFinite(review?.issues_count) ? review.issues_count : null;
}

export async function saveReviewAutoFix(reviewId, autoFix, currentUser) {
  return authenticatedReviewRequest(
    `/api/reviews/${encodeURIComponent(reviewId)}/autofix`,
    currentUser,
    { method: 'PATCH', body: JSON.stringify(autoFix) },
  );
}

export function hydrateReviewSession(saved) {
  if (!saved || typeof saved !== 'object') {
    throw new Error('The saved review could not be retrieved.');
  }
  const firstIn = (records, ...keys) => {
    for (const record of records) {
      if (!record || typeof record !== 'object') continue;
      for (const key of keys) {
        if (Object.hasOwn(record, key) && record[key] !== null && record[key] !== undefined) {
          return { found: true, value: record[key] };
        }
      }
    }
    return { found: false, value: undefined };
  };
  const metadata = saved.analysis_metadata || saved.analysis || {};
  const aiResult = saved.ai_result || saved.ai_analysis || {};
  const staticResult = saved.static_result || saved.static_analysis || {};
  const savedStaticMetadata = saved.static_metadata
    || saved.static_analyzer_metadata
    || saved.analyzer_metadata
    || staticResult.static_metadata
    || staticResult.analyzer_metadata
    || metadata.static_metadata
    || metadata.analyzer_metadata
    || {};
  const project = firstIn([saved], 'project_name', 'projectName', 'project');
  const language = firstIn([saved], 'language', 'programming_language', 'programmingLanguage');
  const codeField = firstIn([saved], 'code', 'source_code', 'submitted_code', 'original_code');
  const risk = firstIn([saved], 'overall_risk', 'risk_level', 'risk');
  const aiAvailable = firstIn([saved, aiResult, metadata], 'ai_available', 'ai_analysis_available', 'available');
  const aiSummary = firstIn([saved, aiResult, metadata], 'ai_summary', 'review_summary');
  const legacySummary = aiAvailable.found && aiAvailable.value === false
    ? { found: false, value: undefined }
    : firstIn([saved, aiResult, metadata], 'summary');
  const summary = aiSummary.found ? aiSummary : legacySummary;
  const score = firstIn([saved, metadata], 'quality_score', 'code_quality_score', 'qualityScore');
  const staticSupported = firstIn(
    [saved, staticResult, savedStaticMetadata, metadata],
    'static_supported', 'static_analysis_supported', 'analyzer_supported', 'supported',
  );
  const aiIssues = firstIn([saved], 'ai_issues', 'ai_findings', 'aiIssues');
  const aiNestedIssues = firstIn([aiResult], 'issues', 'findings');
  const staticIssues = firstIn([saved], 'static_issues', 'static_findings', 'staticIssues');
  const staticNestedIssues = firstIn([staticResult], 'issues', 'findings');
  const combinedIssues = firstIn([saved], 'issues', 'findings', 'review_findings');
  const issuesCount = firstIn(
    [saved],
    'total_issue_count', 'issues_count', 'issue_count', 'total_issues',
  );
  const criticalCount = firstIn([saved], 'critical_count', 'critical_issues');
  const warningCount = firstIn([saved], 'warning_count', 'warning_issues');
  const infoCount = firstIn([saved], 'info_count', 'info_issues');
  const errorCount = firstIn([saved], 'error_count', 'error_issues');
  const staticSummary = firstIn([saved], 'static_summary').found
    ? firstIn([saved], 'static_summary')
    : firstIn([staticResult, savedStaticMetadata], 'summary');
  const staticMetadata = firstIn(
    [saved, staticResult, savedStaticMetadata, metadata],
    'static_analyzer_metadata', 'static_metadata', 'analyzer_metadata',
  );
  const aiFailureReason = firstIn(
    [saved, aiResult, metadata],
    'ai_failure_reason', 'fallback_reason',
  );
  const aiFailureCategory = firstIn(
    [saved, aiResult, metadata],
    'ai_failure_category', 'failure_category',
  );
  const staticAnalysisError = firstIn([saved, staticResult, metadata], 'static_analysis_error');
  const analysisStatus = firstIn([saved, metadata], 'analysis_status', 'status');
  const autoFix = firstIn([saved], 'auto_fix', 'auto_fix_result', 'autofix', 'autofix_result');

  const normalizeIssue = (issue) => {
    const normalized = { ...issue };
    const aliases = {
      title: ['title', 'name', 'rule_name'],
      description: ['description', 'explanation', 'message'],
      explanation: ['explanation', 'description', 'message'],
      recommendation: ['recommendation', 'suggested_fix', 'recommended_solution', 'fix'],
      line: ['line', 'line_number', 'lineNumber'],
      severity: ['severity', 'risk_level'],
      source: ['source', 'analyzer', 'tool'],
    };
    for (const [target, keys] of Object.entries(aliases)) {
      const field = firstIn([issue], ...keys);
      if (field.found) normalized[target] = field.value;
    }
    return normalized;
  };
  const classify = (issues, kind) => (Array.isArray(issues) ? issues : [])
    .filter((issue) => {
      if (kind === 'ai') return ['ai', 'llm', 'groq', 'openai'].includes(
        String(issue.source || issue.analyzer || issue.tool || '').toLowerCase(),
      ) || String(issue.analysis_type || issue.finding_type || '').toLowerCase() === 'ai';
      return !['ai', 'llm', 'groq', 'openai'].includes(
        String(issue.source || issue.analyzer || issue.tool || '').toLowerCase(),
      ) && String(issue.analysis_type || issue.finding_type || '').toLowerCase() !== 'ai';
    });

  const result = { ...saved, review_id: saved.review_id || saved.id, saved_to_db: true };
  const mapped = {
    project_name: project,
    language,
    code: codeField,
    overall_risk: risk,
    summary,
    ai_summary: aiSummary,
    ai_issues: aiIssues.found ? aiIssues : aiNestedIssues,
    static_issues: staticIssues.found ? staticIssues : staticNestedIssues,
    quality_score: score,
    ai_available: aiAvailable,
    static_supported: staticSupported,
    issues_count: issuesCount,
    total_issue_count: issuesCount,
    critical_count: criticalCount,
    warning_count: warningCount,
    info_count: infoCount,
    error_count: errorCount,
    static_summary: staticSummary,
    static_metadata: staticMetadata,
    ai_failure_reason: aiFailureReason,
    ai_failure_category: aiFailureCategory,
    static_analysis_error: staticAnalysisError,
    analysis_status: analysisStatus,
    auto_fix: autoFix,
  };
  for (const [key, field] of Object.entries(mapped)) {
    if (field.found) {
      result[key] = ['ai_issues', 'static_issues'].includes(key) && Array.isArray(field.value)
        ? field.value.map(normalizeIssue)
        : field.value;
    }
    else delete result[key];
  }
  if (!mapped.ai_issues.found && combinedIssues.found) {
    result.ai_issues = classify(combinedIssues.value, 'ai').map(normalizeIssue);
  }
  if (!mapped.static_issues.found && combinedIssues.found) {
    result.static_issues = classify(combinedIssues.value, 'static').map(normalizeIssue);
  }
  if (score.found && typeof score.value === 'number') {
    result.quality_score = { score: score.value };
  }
  if (!aiAvailable.found) delete result.ai_available;
  if (!staticSupported.found) delete result.static_supported;
  if (!staticMetadata.found) delete result.static_metadata;

  const code = codeField.found ? codeField.value : '';
  const missing = [];
  if (!project.found) missing.push('project name');
  if (!language.found) missing.push('programming language');
  if (!codeField.found) missing.push('submitted source code');
  if (!risk.found) missing.push('risk level');
  if (!score.found) missing.push('quality score');
  if (!issuesCount.found) missing.push('issue counts');
  if (!criticalCount.found) missing.push('critical issue count');
  if (!warningCount.found) missing.push('warning issue count');
  if (!infoCount.found) missing.push('info issue count');
  if (!mapped.ai_issues.found && !combinedIssues.found && aiAvailable.value !== false) {
    missing.push('AI findings');
  }
  if (!mapped.static_issues.found && !combinedIssues.found && staticSupported.value !== false) {
    missing.push('static findings');
  }
  if (!aiAvailable.found) missing.push('AI availability metadata');
  if (!staticSupported.found) missing.push('static analyzer metadata');
  if (aiAvailable.value !== false && !summary.found) missing.push('AI summary');
  result.restorationUnavailable = missing;

  return {
    form: {
      project: project.found ? project.value : '',
      language: language.found ? language.value : '',
      code,
    },
    reviewedCode: code,
    result,
  };
}
