/**
 * URL safety for externally-sourced links.
 *
 * The data pipeline ingests `homepage_url` and ORCID values from faculty
 * directories and OpenAlex; anything that fails validation here is simply
 * not rendered as a link.  This blocks stored-XSS vectors like
 * `javascript:alert(1)` slipping in through the data path.
 */

/** Only http(s) absolute URLs are safe for `<a href>` — `javascript:`,
 * `data:`, `vbscript:` and anything non-parseable are rejected. */
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!/^https?:\/\//i.test(trimmed)) return null;
  try {
    return new URL(trimmed).toString();
  } catch {
    return null;
  }
}

/** ORCID iD format: 4-4-4-3 groups, last character may be a check digit X. */
export function isOrcid(value: string | null | undefined): boolean {
  return typeof value === "string" && /^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/.test(value);
}
