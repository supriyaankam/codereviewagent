const viteEnv = import.meta.env || {};
const configuredBaseUrl = viteEnv.VITE_API_BASE_URL?.trim().replace(/\/+$/, '');

export function apiUrl(path) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;

  if (!viteEnv.PROD || !configuredBaseUrl) {
    return normalizedPath;
  }

  return `${configuredBaseUrl}${normalizedPath}`;
}
