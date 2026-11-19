import { apiUrl } from './api.js';

async function request(path, currentUser) {
  if (!currentUser) throw new Error('Sign in to access Project History.');

  const token = await currentUser.getIdToken();
  const response = await fetch(apiUrl(path), {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Project History request failed (HTTP ${response.status}).`);
  }
  return data;
}

function pageQuery({ pageSize = 20, cursor } = {}) {
  const params = new URLSearchParams({ page_size: String(pageSize) });
  if (cursor) params.set('cursor', cursor);
  return `?${params}`;
}

export function getProjectHistory(currentUser, pagination) {
  return request(`/api/projects/history${pageQuery(pagination)}`, currentUser);
}

export function getProjectAnalysisHistory(projectId, currentUser, pagination) {
  return request(
    `/api/projects/history/${encodeURIComponent(projectId)}/analyses${pageQuery(pagination)}`,
    currentUser,
  );
}

export function getProjectAnalysis(projectId, analysisId, currentUser) {
  return request(
    `/api/projects/history/${encodeURIComponent(projectId)}/analyses/${encodeURIComponent(analysisId)}`,
    currentUser,
  );
}
