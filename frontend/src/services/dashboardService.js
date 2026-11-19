import { apiUrl } from './api.js';

export async function fetchDashboard(currentUser) {
  if (!currentUser) throw new Error('Sign in to load your dashboard.');

  const token = await currentUser.getIdToken();
  const response = await fetch(
    apiUrl(`/api/dashboard?user_id=${encodeURIComponent(currentUser.uid)}`),
    { headers: { Authorization: `Bearer ${token}` } },
  );
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.detail || `Could not load dashboard (HTTP ${response.status}).`);
  }
  return data;
}
