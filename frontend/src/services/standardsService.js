/**
 * Team Standards — REST calls to the FastAPI backend.
 * The backend writes to Firestore; we never write directly from here
 * so the backend is the single source of truth for standards.
 */

const BASE = '/api/standards';

// ── helpers ───────────────────────────────────────────────────────────────────

async function request(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
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
export async function fetchStandards(projectId = '') {
  const url = projectId ? `${BASE}?project_id=${encodeURIComponent(projectId)}` : BASE;
  return request(url);
}

/**
 * Create a new standard.
 * @param {{ title: string, description: string, category: string, project_id: string, created_by: string }} data
 */
export async function createStandard(data) {
  return request(BASE, { method: 'POST', body: JSON.stringify(data) });
}

/**
 * Update an existing standard.
 * @param {string} id
 * @param {{ title: string, description: string, category: string }} data
 */
export async function updateStandard(id, data) {
  return request(`${BASE}/${id}`, { method: 'PUT', body: JSON.stringify(data) });
}

/**
 * Delete a standard.
 * @param {string} id
 */
export async function deleteStandard(id) {
  return request(`${BASE}/${id}`, { method: 'DELETE' });
}
