export function createEmptyReviewSession() {
  return {
    form: { project: '', language: '', code: '' },
    reviewedCode: '',
    result: null,
    sourceChanged: false,
    error: '',
  };
}

export function getReviewPageMode(searchParams) {
  const reviewId = searchParams.get('review_id');
  return reviewId ? { type: 'saved', reviewId } : { type: 'new' };
}

export function createReviewRequest({
  userId,
  projectName,
  language,
  code,
  languageConfirmed = false,
}) {
  const request = {
    user_id: userId,
    project_id: 'global',
    project_name: projectName.trim(),
    language,
    code,
  };
  if (languageConfirmed) request.language_confirmed = true;
  return request;
}

export function findingIdentity(issue) {
  const title = String(issue?.title || '')
    .replace(/^\[[^\]]+\]\s*/, '')
    .trim()
    .toLowerCase();
  const category = String(issue?.category || '').trim().toLowerCase();
  return `${category}|${title}`;
}

export function attachAcceptedAutoFix(review, autoFix, attemptedFindings = []) {
  const remaining = [...(review.ai_issues || []), ...(review.static_issues || [])];
  const remainingIdentities = new Set(remaining.map(findingIdentity));
  const ineffectiveFindingKeys = [...new Set(
    attemptedFindings
      .map(findingIdentity)
      .filter((identity) => identity !== '|' && remainingIdentities.has(identity)),
  )];
  return {
    ...review,
    auto_fix: {
      ...autoFix,
      ineffective_finding_keys: ineffectiveFindingKeys,
      reanalysis: {
        issues: review.static_issues || [],
        summary: review.static_summary,
        supported: review.static_supported,
        language: review.language,
        has_syntax_error: review.static_metadata?.has_syntax_error,
      },
    },
  };
}
