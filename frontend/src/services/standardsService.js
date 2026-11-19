/**
 * Team Standards — REST calls to the FastAPI backend.
 * The backend writes to Firestore; we never write directly from here
 * so the backend is the single source of truth for standards.
 */

import { apiUrl } from './api';

const BASE = '/api/standards';

// ── helpers ───────────────────────────────────────────────────────────────────

async function request(url, options = {}, currentUser) {
  if (!currentUser) throw new Error('Sign in to access team standards.');
  const token = await currentUser.getIdToken();
  const { headers: optionHeaders = {}, ...requestOptions } = options;
  const res = await fetch(apiUrl(url), {
    ...requestOptions,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...optionHeaders,
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(text || `HTTP ${res.status}`);
  }
  // 204 No Content → nothing to parse
  if (res.status === 204) return null;
  return res.json();
}

// ── API calls ─────────────────────────────────────────────────────────────────

/**
 * Fetch all standards, optionally filtered by project_id.
 * @param {string} [projectId]
 * @returns {Promise<Array>}
 */
export async function fetchStandards(projectId = '', currentUser) {
  const url = projectId ? `${BASE}?project_id=${encodeURIComponent(projectId)}` : BASE;
  return request(url, {}, currentUser);
}

/**
 * Create a new standard.
 * @param {{ title: string, description: string, category: string, severity: string, recommended_solution: string, enabled: boolean, project_id: string, created_by: string }} data
 */
export async function createStandard(data, currentUser) {
  return request(BASE, { method: 'POST', body: JSON.stringify(data) }, currentUser);
}

/**
 * Update an existing standard.
 * @param {string} id
 * @param {{ title: string, description: string, category: string }} data
 */
export async function updateStandard(id, data, currentUser) {
  return request(`${BASE}/${id}`, { method: 'PUT', body: JSON.stringify(data) }, currentUser);
}

/**
 * Delete a standard.
 * @param {string} id
 */
export async function deleteStandard(id, currentUser) {
  return request(`${BASE}/${id}`, { method: 'DELETE' }, currentUser);
}
