/** Resolve an API path for dev proxy or production API host. */
export function apiUrl(path: string): string {
  const base = import.meta.env.VITE_API_URL?.replace(/\/$/, "");
  if (!base) {
    return path.startsWith("/") ? path : `/${path}`;
  }
  const normalized = path.startsWith("/api/") ? path.slice(4) : path;
  return `${base}${normalized.startsWith("/") ? normalized : `/${normalized}`}`;
}
